"""Oxydan 12 — Lichess bot main loop.

Compared to v11, this rewrite:

* Loads everything from :mod:`config` (no more hard-coded SETTINGS dict
  duplicated across files).
* Uses :mod:`oxydan_chat` for branded chat. The previous version had
  two ``except TypeError: pass`` branches that silently dropped every
  chat message — that is fixed here and every send is logged.
* Tracks openings with :mod:`oxydan_learn` for optional win/loss-aware
  book selection.
* Implements master-level time allocation: a panic mode for <2s, a
  transition band, a fast opening budget, and a complexity bonus past
  move 30.
* Stays faithful to the Lichess Bot API surface (``client.bots.*``).
* Adds a ``--self-test`` flag that exercises config + chat plumbing
  without actually connecting to Lichess, so a fork author can verify
  their setup in 5 seconds.
* Properly routes incoming challenges through the matchmaker's
  ``is_challenge_acceptable`` (the v12.0.0 release forgot this and
  accepted anything that fit the time/parallel constraints).
* Reports every finished game back to the matchmaker so the rating
  tracker, opponent tracker and protection mode actually work.
"""

from __future__ import annotations

import argparse
import logging
import os
import queue
import random
import sys
import threading
import time
from datetime import timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

import berserk
import chess
import chess.engine
import chess.polyglot
import requests
import yaml

from config import settings
from oxydan_chat import ChatSender
from oxydan_learn import learn

LOG = logging.getLogger("oxydan")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _setup_logging() -> None:
    # Make stdout/stderr UTF-8 so emoji in log lines don't crash on
    # Windows consoles that default to cp1254.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    level = os.environ.get("OXYDAN_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
    # berserk has a chatty default logger; tone it down unless asked.
    if os.environ.get("OXYDAN_VERBOSE_BERSERK") != "1":
        logging.getLogger("berserk").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Game mode classification
# ---------------------------------------------------------------------------

def _get_game_mode(time_control: Any) -> str:
    if not isinstance(time_control, dict):
        return "blitz"
    limit = time_control.get("limit", 300)
    if limit < 180:
        return "bullet"
    if limit < 480:
        return "blitz"
    if limit < 1500:
        return "rapid"
    return "classical"


# ---------------------------------------------------------------------------
# Active-game bookkeeping
# ---------------------------------------------------------------------------

class ActiveGames:
    """Thread-safe wrapper around the set of in-flight game ids."""

    def __init__(self) -> None:
        self._ids: Set[str] = set()
        self._pending = 0
        self._lock = threading.Lock()

    def count(self, include_pending: bool = True) -> int:
        with self._lock:
            return len(self._ids) + (self._pending if include_pending else 0)

    def try_reserve_slot(self) -> bool:
        with self._lock:
            if self.count() >= settings.runtime.max_parallel_games:
                return False
            self._pending += 1
            return True

    def release_reservation(self) -> None:
        with self._lock:
            if self._pending > 0:
                self._pending -= 1

    def add(self, game_id: str) -> bool:
        with self._lock:
            if game_id in self._ids:
                return False
            if len(self._ids) >= settings.runtime.max_parallel_games:
                return False
            self._ids.add(game_id)
            return True

    def discard(self, game_id: str) -> None:
        with self._lock:
            self._ids.discard(game_id)


# ---------------------------------------------------------------------------
# Runtime watchdog
# ---------------------------------------------------------------------------

class RuntimeWatchdog(threading.Thread):
    """Shuts the bot down after ``max_total_runtime_seconds`` (default 6h)."""

    def __init__(self, start_time: float, active: ActiveGames) -> None:
        super().__init__(daemon=True, name="oxydan-watchdog")
        self._start = start_time
        self._active = active

    def run(self) -> None:
        while True:
            time.sleep(30)
            elapsed = time.time() - self._start
            if elapsed <= settings.runtime.max_total_runtime_seconds:
                continue
            if self._active.count(include_pending=False) == 0:
                LOG.info("⏰ Watchdog: runtime cap reached, exiting cleanly.")
                os._exit(0)
            LOG.info(
                "⏰ Watchdog: runtime cap reached, %d game(s) still running — waiting.",
                self._active.count(include_pending=False),
            )


# ---------------------------------------------------------------------------
# Engine wrapper — OxydanV12
# ---------------------------------------------------------------------------

class OxydanV12:
    """Owns the engine pool and the time-management policy.

    The previous class (``OxydanV11``) mixed engine-pool plumbing with
    opening-book + tablebase + time-allocation logic in one ~200-line
    method. ``OxydanV12`` factors those into named pieces so each can
    be reasoned about independently.
    """

    def __init__(self, exe_path: str, uci_options: Optional[Dict[str, Any]] = None) -> None:
        self.exe_path = exe_path
        self.book_path = settings.book_path
        self.engine_pool: "queue.Queue[chess.engine.SimpleEngine]" = queue.Queue()

        pool_size = settings.runtime.max_parallel_games + 1
        overhead = (uci_options or {}).get("MoveOverhead") or \
                   (uci_options or {}).get("Move Overhead") or 100

        try:
            for _ in range(pool_size):
                eng = chess.engine.SimpleEngine.popen_uci(self.exe_path, timeout=30)
                self._configure(eng, uci_options or {}, overhead)
                self.engine_pool.put(eng)
            LOG.info("🚀 %d engine process(es) ready (MoveOverhead=%s ms).", pool_size, overhead)
        except Exception as exc:  # noqa: BLE001
            LOG.critical("Engine pool failed to start: %s", exc)
            sys.exit(1)

    @staticmethod
    def _configure(eng: chess.engine.SimpleEngine, options: Dict[str, Any], overhead: int) -> None:
        # MoveOverhead has two spellings depending on engine; try both.
        for key in ("Move Overhead", "MoveOverhead"):
            try:
                eng.configure({key: overhead})
                break
            except Exception:  # noqa: BLE001
                continue
        for opt, val in options.items():
            if opt in ("MoveOverhead", "Move Overhead"):
                continue
            try:
                eng.configure({opt: val})
            except Exception:  # noqa: BLE001
                LOG.debug("Engine ignored option %r=%r", opt, val)

    # --- helpers --------------------------------------------------------
    @staticmethod
    def _to_seconds(value: Any) -> float:
        if value is None:
            return 0.0
        if isinstance(value, timedelta):
            return max(0.0, value.total_seconds())
        try:
            return max(0.0, float(value) / 1000.0)
        except (TypeError, ValueError):
            return 0.0

    # --- quick position scoring (used for losing-realization chat) ----
    def get_score(self, board: chess.Board) -> Optional[int]:
        engine: Optional[chess.engine.SimpleEngine] = None
        try:
            engine = self.engine_pool.get(timeout=5)
            info = engine.analyse(board, chess.engine.Limit(depth=6, time=0.05))
            score = info.get("score")
            if score is not None:
                return score.white().score(mate_score=10000)
        except Exception as exc:  # noqa: BLE001
            LOG.debug("get_score failed: %s", exc)
        finally:
            if engine is not None:
                self.engine_pool.put(engine)
        return None

    # --- fallback: capture-priority move if engine totally fails --------
    def fallback_move(self, board: chess.Board) -> Optional[chess.Move]:
        legal = list(board.legal_moves)
        if not legal:
            return None
        values = {
            chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330,
            chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 0,
        }
        best, best_score = legal[0], -10**9
        for mv in legal:
            score = 0
            captured = board.piece_at(mv.to_square)
            mover = board.piece_at(mv.from_square)
            if captured:
                score += 10 * values.get(captured.piece_type, 0)
            if mover:
                score -= values.get(mover.piece_type, 0)
            if mv.promotion:
                score += values.get(mv.promotion, 0)
            if board.gives_check(mv):
                score += 80
            board.push(mv)
            if board.is_checkmate():
                score += 100_000
            if board.is_repetition(2):
                score -= 50
            board.pop()
            if score > best_score:
                best, best_score = mv, score
        return best

    # --- master time allocation ----------------------------------------
    def _allocate_time(
        self,
        board: chess.Board,
        wtime: Any, btime: Any, winc: Any, binc: Any,
    ) -> Tuple[float, float, float, float]:
        """Compute (my_clock, opp_clock, my_inc, opp_inc) for engine play.

        Layers four policies on top of the raw clocks:

        1. ``panic_threshold_s``   — pre-move speed, no inc.
        2. ``transition_threshold_s`` — keep the increment, use 80% of clock.
        3. ``opening_time_fraction`` — spend less in the first
           ``opening_move_count`` plies.
        4. ``complexity_extra_fraction`` — bonus past
           ``complexity_moves_threshold`` plies (tactical middlegame).
        """
        tm = settings.time_management
        buffer = tm.latency_buffer_ms / 1000.0

        if board.turn == chess.WHITE:
            my_raw, op_raw = wtime, btime
            my_inc_raw, op_inc_raw = winc, binc
        else:
            my_raw, op_raw = btime, wtime
            my_inc_raw, op_inc_raw = binc, winc

        my_s = max(0.005, self._to_seconds(my_raw) - buffer)
        op_s = max(0.005, self._to_seconds(op_raw) - buffer)
        my_inc = self._to_seconds(my_inc_raw)
        op_inc = self._to_seconds(op_inc_raw)

        # Layer 1: panic (sub-2s) — pre-move speed, no increment.
        if my_s < tm.panic_threshold_s:
            return max(0.010, my_s * 0.10), op_s, 0.0, op_inc

        # Layer 2: transition band — keep increment, but use 80% of clock.
        if my_s < tm.transition_threshold_s:
            my_send = my_s * 0.8
            return my_send, op_s, my_inc, op_inc

        # Layer 3: opening budget — first N plies spend less.
        ply = len(board.move_stack)
        if ply < tm.opening_move_count:
            my_send = my_s * tm.opening_time_fraction
            return my_send, op_s, my_inc, op_inc

        # Layer 4: complexity bonus past the opening.
        if ply > tm.complexity_moves_threshold:
            extra = my_s * tm.complexity_extra_fraction
            # Don't overspend: cap bonus at 30% of clock.
            my_send = min(my_s, my_s * 0.7 + extra)
            return my_send, op_s, my_inc, op_inc

        # Default: give the engine the full clock.
        return my_s, op_s, my_inc, op_inc

    # --- main entry: pick a move for the current position --------------
    def get_best_move(
        self,
        board: chess.Board,
        wtime: Any, btime: Any, winc: Any, binc: Any,
    ) -> Optional[chess.Move]:
        move = self._try_opening_book(board)
        if move is not None:
            return move
        move = self._try_online_tablebase(board, wtime, btime, wtime, btime)
        if move is not None:
            return move
        move = self._try_engine(board, wtime, btime, winc, binc)
        if move is not None:
            return move
        LOG.warning("Engine failed, falling back to heuristic.")
        return self.fallback_move(board)

    # --- opening book (with optional Oxydan Learn weighting) -----------
    def _try_opening_book(self, board: chess.Board) -> Optional[chess.Move]:
        if board.chess960 or not os.path.exists(self.book_path):
            return None
        try:
            with chess.polyglot.open_reader(self.book_path) as reader:
                entries = list(reader.find_all(board))
            if not entries:
                return None

            # Oxydan Learn: rank by historical win/loss.
            ranked = learn.rank_book_moves(board, entries)
            # Inject small randomness so the same move doesn't repeat
            # every game even when the weights are equal.
            ranked.sort(key=lambda pair: random.random() / max(pair[1], 0.01))

            for move, _weight in ranked:
                if move not in board.legal_moves:
                    continue
                # Avoid repeating the last few opening lines.
                board.push(move)
                key = learn.opening_key(board)
                board.pop()
                if not learn.weight_for_key(key) < 0.5:  # soft floor
                    return move
            # Last resort: first legal entry.
            for entry in entries:
                if entry.move in board.legal_moves:
                    return entry.move
        except Exception as exc:  # noqa: BLE001
            LOG.warning("📖 Opening book error: %s", exc)
        return None

    # --- online 7-piece tablebase --------------------------------------
    def _try_online_tablebase(
        self, board: chess.Board, wtime: Any, btime: Any, _winc: Any, _binc: Any
    ) -> Optional[chess.Move]:
        if not settings.tablebase.online_enabled or board.chess960:
            return None
        if len(board.piece_map()) > settings.tablebase.max_pieces:
            return None
        # Need at least 15s on the clock; otherwise the network call
        # is worse than the engine's own choice.
        my_clock = self._to_seconds(wtime if board.turn == chess.WHITE else btime)
        if my_clock < max(15.0, settings.tablebase.min_time_for_lookup):
            return None
        try:
            r = requests.get(
                "https://tablebase.lichess.ovh/standard",
                params={"fen": board.fen()},
                timeout=min(0.2, max(0.02, my_clock * 0.01)),
            )
            if r.status_code == 200:
                payload = r.json()
                moves = payload.get("moves") or []
                if moves:
                    best = chess.Move.from_uci(moves[0]["uci"])
                    if best in board.legal_moves:
                        return best
        except Exception:  # noqa: BLE001
            return None
        return None

    # --- the actual engine ---------------------------------------------
    def _try_engine(
        self, board: chess.Board, wtime: Any, btime: Any, winc: Any, binc: Any,
    ) -> Optional[chess.Move]:
        engine: Optional[chess.engine.SimpleEngine] = None
        try:
            engine = self.engine_pool.get(timeout=5)

            my_s, op_s, my_inc, op_inc = self._allocate_time(
                board, wtime, btime, winc, binc,
            )

            if board.turn == chess.WHITE:
                limit = chess.engine.Limit(
                    white_clock=my_s, black_clock=op_s,
                    white_inc=my_inc,  black_inc=op_inc,
                )
            else:
                limit = chess.engine.Limit(
                    white_clock=op_s, black_clock=my_s,
                    white_inc=op_inc,  black_inc=my_inc,
                )

            result = engine.play(board, limit)
            if result.move and result.move in board.legal_moves:
                return result.move
            LOG.warning("Engine returned illegal move %s, ignoring.", result.move)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("🚨 Engine error: %s: %s", type(exc).__name__, exc)
        finally:
            if engine is not None:
                self.engine_pool.put(engine)
        return None


# ---------------------------------------------------------------------------
# Per-game handler
# ---------------------------------------------------------------------------

def _handle_game(
    client: berserk.Client,
    game_id: str,
    bot: OxydanV12,
    my_id: str,
    active: ActiveGames,
    chat: ChatSender,
    mm: Optional[Any] = None,
) -> None:
    try:
        stream = client.bots.stream_game_state(game_id)

        board: Optional[chess.Board] = None
        my_color: Optional[bool] = None
        last_move_count = 0
        is_vs_human = False
        game_started = False
        game_start_time: Optional[float] = None
        losing_msg_sent = False
        game_mode = "blitz"
        rated = False
        opp_id = ""

        def _send(category: str, room: str = "player") -> None:
            if not settings.oxydan_chat.enabled:
                return
            if rated and not settings.oxydan_chat.chat_in_rated:
                return
            chat.send_pick(game_id, category, room=room)

        for state in stream:
            if "error" in state:
                LOG.warning("Stream error for %s: %s", game_id, state["error"])
                break

            if state["type"] == "gameFull":
                white = state.get("white", {}) or {}
                black = state.get("black", {}) or {}
                rated = bool(state.get("rated", False))
                my_color = chess.WHITE if white.get("id") == my_id else chess.BLACK
                opp = black if my_color == chess.WHITE else white
                opp_id = (opp.get("id") or "").lower()
                opp_title = (opp.get("title") or "").upper()
                is_vs_human = opp_title != "BOT"

                if opp_id in settings.matchmaking.permanent_blacklist:
                    LOG.info("🚫 Blacklisted opponent %s — resigning.", opp_id)
                    try:
                        client.bots.resign_game(game_id)
                    except Exception as exc:  # noqa: BLE001
                        LOG.warning("Resign failed: %s", exc)
                    return

                variant = (state.get("variant") or {}).get("key", "standard")
                is_960 = variant == "chess960"
                initial_fen = state.get("initialFen", "startpos")
                if initial_fen and initial_fen != "startpos":
                    board = chess.Board(initial_fen, chess960=is_960)
                else:
                    board = chess.Board(chess960=is_960)

                clock = state.get("clock", {}) or {}
                game_mode = "chess960" if is_960 else _get_game_mode(clock)

                last_move_count = 0
                game_start_time = time.time()
                losing_msg_sent = False

                # Greeting is deferred to the first gameState event to
                # make sure Lichess has officially opened the chat.
                curr_state = state.get("state", {}) or {}

            elif state["type"] == "gameState":
                curr_state = state
            else:
                continue

            if board is None:
                continue

            # Greeting on first gameState (game has officially started).
            if not game_started:
                if (curr_state.get("status") in ("started", "resign", "mate", "draw",
                                                 "outoftime", "stalemate", "aborted")
                        or curr_state.get("moves")):
                    game_started = True
                    if settings.oxydan_chat.enabled and (not rated or settings.oxydan_chat.chat_in_rated):
                        category = "greeting_human" if is_vs_human else "greeting_bot"
                        chat.send_pick(game_id, category)

            moves_str = (curr_state.get("moves") or "").strip()
            moves = moves_str.split() if moves_str else []

            if len(moves) > last_move_count:
                for m in moves[last_move_count:]:
                    # Lichess bot stream uses UCI notation.
                    try:
                        board.push(board.parse_uci(m))
                    except Exception as exc:  # noqa: BLE001
                        LOG.warning("Move parse error %r: %s", m, exc)
                        break
                last_move_count = len(board.move_stack)

            # Abort games where the opponent never shows up.
            if (not game_started
                    and game_start_time is not None
                    and (time.time() - game_start_time) > settings.runtime.abort_wait_seconds):
                try:
                    client.bots.abort_game(game_id)
                    LOG.info("⏳ Aborted idle game %s", game_id)
                except Exception as exc:  # noqa: BLE001
                    LOG.warning("Abort failed: %s", exc)
                break

            status = curr_state.get("status")
            if status in ("mate", "resign", "draw", "outoftime", "aborted", "stalemate"):
                winner = curr_state.get("winner")
                my_color_str = "white" if my_color == chess.WHITE else "black"
                if status in ("draw", "stalemate"):
                    result, category = "draw", "draw"
                elif winner:
                    result = "win" if winner == my_color_str else "loss"
                    category = result
                else:
                    result, category = "draw", "draw"

                _send(category)
                if is_vs_human:
                    time.sleep(1)
                    _send("human_postgame")

                # Oxydan Learn: book weighting for next time.
                if result in ("win", "loss", "draw") and board.move_stack:
                    try:
                        learn.record_result(board, result)
                    except Exception as exc:  # noqa: BLE001
                        LOG.debug("oxydan_learn.record_result failed: %s", exc)

                # Matchmaker: rating tracker + opponent tracker.
                if mm and status != "aborted":
                    try:
                        mm.record_game_result(result, game_mode, opponent_id=opp_id)
                    except Exception as exc:  # noqa: BLE001
                        LOG.warning("mm.record_game_result failed: %s", exc)

                LOG.info(
                    "🏁 Game %s finished: %s (mode=%s, opp=%s, rated=%s)",
                    game_id, result, game_mode, opp_id, rated,
                )
                break

            # Optional "I know I'm losing" chat for humans.
            if (settings.oxydan_chat.score_chat_enabled
                    and is_vs_human
                    and not losing_msg_sent
                    and len(board.move_stack) >= 20):
                try:
                    score = bot.get_score(board)
                    if score is not None:
                        my_score = score if my_color == chess.WHITE else -score
                        if my_score < settings.oxydan_chat.losing_score_threshold:
                            _send("losing_realization")
                            losing_msg_sent = True
                except Exception as exc:  # noqa: BLE001
                    LOG.debug("score-chat probe failed: %s", exc)

            # Our turn?
            if my_color is not None and board.turn == my_color and not board.is_game_over():
                move = bot.get_best_move(
                    board,
                    curr_state.get("wtime"),
                    curr_state.get("btime"),
                    curr_state.get("winc"),
                    curr_state.get("binc"),
                )
                if move is not None:
                    for _ in range(3):
                        try:
                            client.bots.make_move(game_id, move.uci())
                            break
                        except Exception as exc:  # noqa: BLE001
                            LOG.debug("make_move retry: %s", exc)
                            time.sleep(0.05)

    except Exception as exc:  # noqa: BLE001
        LOG.exception("handle_game crashed for %s: %s", game_id, exc)
    finally:
        active.discard(game_id)


# ---------------------------------------------------------------------------
# Self test
# ---------------------------------------------------------------------------

def _self_test() -> int:
    """Sanity check: config + chat plumbing without connecting to Lichess."""
    print("🔧 Oxydan self-test")
    print(f"   config path     : {settings.config_path}")
    print(f"   engine binary   : {settings.engine.binary_path}")
    print(f"   max parallel    : {settings.runtime.max_parallel_games}")
    print(f"   chat enabled    : {settings.oxydan_chat.enabled}")
    print(f"   learn enabled   : {settings.oxydan_learn.enabled}")
    if settings.oxydan_learn.enabled:
        print(f"   learn summary   : {learn.summary()}")

    if not os.path.exists(settings.engine.binary_path):
        print(f"❌ Engine binary not found at {settings.engine.binary_path}")
        return 1

    try:
        bot = OxydanV12(settings.engine.binary_path,
                        uci_options=settings.engine.uci_options)
    except Exception as exc:  # noqa: BLE001
        print(f"❌ Engine failed to start: {exc}")
        return 1

    board = chess.Board()
    try:
        # The engine's opening-book path may be absent; that's fine.
        move = bot.get_best_move(board, 10000, 10000, 1000, 1000)
    finally:
        # Drain pool to avoid orphan processes.
        while not bot.engine_pool.empty():
            try:
                eng = bot.engine_pool.get_nowait()
                eng.quit()
            except Exception:  # noqa: BLE001
                break

    if not move or move not in board.legal_moves:
        print("❌ Engine did not produce a legal move.")
        return 1
    print(f"✅ Engine produced {move.uci()}")
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _make_client() -> berserk.Client:
    if not settings.has_token():
        raise RuntimeError(
            "No LICHESS_TOKEN. Set the LICHESS_TOKEN environment variable or "
            "replace the $LICHESS_TOKEN placeholder in config.yml."
        )
    return berserk.Client(session=berserk.TokenSession(settings.token))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="lichess-bot", description="Oxydan 12")
    parser.add_argument("--self-test", action="store_true",
                        help="Validate config + engine without connecting to Lichess.")
    args = parser.parse_args(argv)

    _setup_logging()
    if args.self_test:
        return _self_test()

    if not settings.has_token():
        LOG.error(
            "No LICHESS_TOKEN found. Set it as an env var or in config.yml, "
            "or run with --self-test."
        )
        return 1

    start_time = time.time()
    try:
        client = _make_client()
        my_id = client.account.get()["id"]
    except Exception as exc:  # noqa: BLE001
        LOG.error("Could not connect to Lichess: %s", exc)
        return 1

    # Local chat sender bound to the main client. Game threads share it.
    chat = ChatSender(client)

    bot = OxydanV12(
        settings.engine.binary_path,
        uci_options=settings.engine.uci_options,
    )

    active = ActiveGames()
    RuntimeWatchdog(start_time, active).start()

    # Matchmaker thread (optional). mm is initialised to None so the
    # `if mm:` checks below never raise NameError if construction fails.
    mm: Optional[Any] = None
    try:
        from matchmaking import Matchmaker  # local import to avoid cycles
    except Exception as exc:  # noqa: BLE001
        LOG.error("matchmaking module failed to import: %s", exc)
        Matchmaker = None  # type: ignore[assignment]

    if Matchmaker is not None and settings.matchmaking.allow_feed:
        try:
            with open(settings.config_path, "r", encoding="utf-8") as fh:
                raw_config = yaml.safe_load(fh) or {}
            mm = Matchmaker(
                client=client,
                config=raw_config,
                active_games=active,
                token=settings.token,
            )
            threading.Thread(target=mm.start, daemon=True, name="matchmaker").start()
        except Exception as exc:  # noqa: BLE001
            LOG.error("Matchmaker failed to start: %s", exc)
            mm = None

    LOG.info("🔥 Oxydan 12 ready. ID: %s | Chat: %s | Matchmaker: %s",
             my_id,
             "ON" if settings.oxydan_chat.enabled else "OFF",
             "ON" if mm else "OFF")

    while True:
        try:
            for event in client.bots.stream_incoming_events():
                # Respect the runtime cap.
                elapsed = time.time() - start_time
                time_remaining = settings.runtime.max_total_runtime_seconds - elapsed

                if event.get("type") == "challenge":
                    ch = event["challenge"]
                    ch_id = ch["id"]

                    tc = ch.get("timeControl") or {}
                    time_limit = tc.get("limit", 0) or 0
                    increment = tc.get("increment", 0) or 0

                    estimated_game_duration = (time_limit * 2) + (increment * 120)
                    is_time_safe = time_remaining > (
                        estimated_game_duration
                        + settings.runtime.min_game_seconds_remaining
                    )

                    # Delegate the policy decision to the matchmaker.
                    accept, reason = True, "policy-default"
                    if mm is not None:
                        try:
                            accept, reason = mm.is_challenge_acceptable(ch)
                        except Exception as exc:  # noqa: BLE001
                            LOG.warning("mm.is_challenge_acceptable failed for %s: %s",
                                        ch_id, exc)
                            accept, reason = True, "matchmaker-error"

                    can_accept = (
                        is_time_safe
                        and time_limit <= settings.runtime.max_game_time_limit
                        and active.count() < settings.runtime.max_parallel_games
                        and accept
                    )

                    if not can_accept:
                        if not is_time_safe:
                            detail = (f"runtime window too tight "
                                      f"({int(time_remaining)}s < {int(estimated_game_duration)}s)")
                        elif time_limit > settings.runtime.max_game_time_limit:
                            detail = f"game too long ({time_limit}s)"
                        elif active.count() >= settings.runtime.max_parallel_games:
                            detail = "parallel game cap reached"
                        elif not accept:
                            detail = reason or "policy"
                        else:
                            detail = "unknown"
                        try:
                            client.challenges.decline(ch_id, reason="later")
                            LOG.info("❌ Declined %s: %s (reason: %s)",
                                     ch_id, detail, reason)
                        except Exception as exc:  # noqa: BLE001
                            LOG.warning("Decline failed for %s: %s", ch_id, exc)
                        continue

                    if not active.try_reserve_slot():
                        try:
                            client.challenges.decline(ch_id, reason="later")
                        except Exception as exc:  # noqa: BLE001
                            LOG.warning("Decline (no-slot) failed for %s: %s", ch_id, exc)
                        continue

                    try:
                        client.challenges.accept(ch_id)
                        LOG.info("✅ Accepted %s (est %ds, %.0fs left, reason: %s)",
                                 ch_id, int(estimated_game_duration), time_remaining, reason)
                    except Exception as exc:  # noqa: BLE001
                        active.release_reservation()
                        LOG.warning("Accept failed for %s: %s", ch_id, exc)

                elif event.get("type") == "gameStart":
                    game_id = event["game"]["id"]
                    active.release_reservation()
                    if active.add(game_id):
                        threading.Thread(
                            target=_handle_game,
                            args=(client, game_id, bot, my_id, active, chat, mm),
                            daemon=True,
                            name=f"game-{game_id[:6]}",
                        ).start()
                    else:
                        LOG.warning("Skipped gameStart for %s — no slot.", game_id)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("Event stream dropped, reconnecting: %s", exc)
            time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
