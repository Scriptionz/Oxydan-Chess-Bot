import argparse
import dataclasses
import json
import logging
import signal
import sys
import threading
import time
from typing import Dict, Optional, Set

import chess
import chess.engine
import chess.polyglot
import requests
import os

# ---------------------------------------------------------------------------
# Logging & Configuration
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(threadName)s: %(message)s",
)
logger = logging.getLogger("Oxydan12")


@dataclasses.dataclass
class TimeManagerConfig:
    opening_time_fraction: float = 0.04
    midgame_time_fraction: float = 0.06
    endgame_time_fraction: float = 0.05
    panic_threshold_sec: float = 2.0
    panic_move_time: float = 0.15
    network_safety_sec: float = 0.25
    minimum_engine_time: float = 0.05


@dataclasses.dataclass
class Settings:
    # Prefer an environment variable in production:
    #   LICHESS_TOKEN=your_token
    token: str = os.getenv("LICHESS_TOKEN", "")
    lichess_base_url: str = "https://lichess.org"
    engine_path: str = os.getenv("ENGINE_PATH", "./src/Ethereal")
    threads_per_engine: int = 2
    hash_mb: int = 256
    max_concurrent_games: int = 2
    polyglot_book_path: Optional[str] = "books/Cerebellum3Merge.bin"
    tablebase_enabled: bool = True
    tablebase_timeout_sec: float = 0.75
    request_timeout_sec: float = 15.0
    time_config: TimeManagerConfig = dataclasses.field(
        default_factory=TimeManagerConfig
    )


class Config:
    settings = Settings()


# ---------------------------------------------------------------------------
# Thread-safe game tracking
# ---------------------------------------------------------------------------
class ActiveGames:
    """Tracks active games and pending challenge reservations."""

    def __init__(self, max_games: int, reservation_ttl: float = 30.0):
        self.max_games = max_games
        self.reservation_ttl = reservation_ttl
        self.lock = threading.Lock()
        self._active_game_ids: Set[str] = set()
        self._pending_reservations: Dict[str, float] = {}

    def _cleanup_stale_reservations(self) -> None:
        now = time.time()
        expired = [
            cid
            for cid, ts in self._pending_reservations.items()
            if now - ts > self.reservation_ttl
        ]
        for cid in expired:
            logger.warning(
                "Pending reservation for challenge %s timed out. Cleaning up.",
                cid,
            )
            del self._pending_reservations[cid]

    def can_accept_challenge(self) -> bool:
        with self.lock:
            self._cleanup_stale_reservations()
            return (
                len(self._active_game_ids) + len(self._pending_reservations)
                < self.max_games
            )

    def reserve_slot(self, challenge_id: str) -> bool:
        with self.lock:
            self._cleanup_stale_reservations()
            if challenge_id in self._pending_reservations:
                return True

            used = len(self._active_game_ids) + len(self._pending_reservations)
            if used < self.max_games:
                self._pending_reservations[challenge_id] = time.time()
                return True
            return False

    def release_reservation(self, challenge_id: str) -> None:
        with self.lock:
            self._pending_reservations.pop(challenge_id, None)

    def confirm_game_start(
        self, game_id: str, challenge_id: Optional[str] = None
    ) -> None:
        with self.lock:
            if challenge_id:
                self._pending_reservations.pop(challenge_id, None)
            elif self._pending_reservations:
                # Lichess gameStart does not reliably carry the originating
                # challenge id, so consume the oldest accepted reservation.
                oldest = min(
                    self._pending_reservations,
                    key=self._pending_reservations.get,
                )
                del self._pending_reservations[oldest]

            self._active_game_ids.add(game_id)
            logger.info(
                "Game %s started. Active slots: %d/%d",
                game_id,
                len(self._active_game_ids),
                self.max_games,
            )

    def game_finished(self, game_id: str) -> None:
        with self.lock:
            self._active_game_ids.discard(game_id)
            logger.info(
                "Game %s finished. Active slots: %d/%d",
                game_id,
                len(self._active_game_ids),
                self.max_games,
            )


# ---------------------------------------------------------------------------
# Stockfish engine management
# ---------------------------------------------------------------------------
class EnginePool:
    """
    Simple process pool.

    One Stockfish process is created per configured slot, so simultaneous games
    do not serialize behind a single global engine lock.
    """

    def __init__(self, path: str, threads: int, hash_mb: int, pool_size: int):
        self.path = path
        self.threads = threads
        self.hash_mb = hash_mb
        self.pool_size = max(1, pool_size)
        self.lock = threading.Lock()
        self._engines: list[chess.engine.SimpleEngine] = []
        self._available: list[chess.engine.SimpleEngine] = []
        self._condition = threading.Condition(self.lock)

    def start(self) -> None:
        with self.lock:
            if self._engines:
                return

            logger.info("Initializing %d Stockfish engine(s) from: %s",
                        self.pool_size, self.path)

            try:
                for _ in range(self.pool_size):
                    engine = chess.engine.SimpleEngine.popen_uci(self.path)
                    engine.configure(
                        {
                            "Threads": self.threads,
                            "Hash": self.hash_mb,
                        }
                    )
                    self._engines.append(engine)
                    self._available.append(engine)
            except Exception:
                for engine in self._engines:
                    try:
                        engine.quit()
                    except Exception:
                        pass
                self._engines.clear()
                self._available.clear()
                raise

    def get_best_move(
        self, board: chess.Board, time_limit: float
    ) -> chess.Move:
        with self._condition:
            while not self._available:
                self._condition.wait()
            engine = self._available.pop()

        try:
            limit = chess.engine.Limit(
                time=max(0.05, float(time_limit))
            )
            result = engine.play(board, limit)
            if result.move is None:
                raise RuntimeError("Stockfish returned no move.")
            return result.move
        finally:
            with self._condition:
                self._available.append(engine)
                self._condition.notify()

    def close(self) -> None:
        with self.lock:
            engines = list(self._engines)
            self._engines.clear()
            self._available.clear()

        for engine in engines:
            try:
                engine.quit()
            except Exception as exc:
                logger.error("Error during engine shutdown: %s", exc)


# ---------------------------------------------------------------------------
# Core evaluator
# ---------------------------------------------------------------------------
class OxydanBot:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.session = requests.Session()
        self._tb_local = threading.local()
        self.engine_pool = EnginePool(
            settings.engine_path,
            settings.threads_per_engine,
            settings.hash_mb,
            settings.max_concurrent_games,
        )
        self.engine_pool.start()
        self._book_reader = None

        if settings.polyglot_book_path:
            try:
                self._book_reader = chess.polyglot.open_reader(
                    settings.polyglot_book_path
                )
                logger.info("Opening book loaded: %s",
                            settings.polyglot_book_path)
            except Exception as exc:
                logger.warning("Could not load opening book: %s", exc)

    def _allocate_time(
        self, board: chess.Board, my_time: float, my_inc: float
    ) -> float:
        tm = self.settings.time_config
        safe_time = max(0.0, my_time - tm.network_safety_sec)

        if safe_time <= tm.minimum_engine_time:
            return tm.minimum_engine_time

        # Absolute panic mode: spend very little but keep a safety margin for
        # move submission/network overhead.
        if my_time < tm.panic_threshold_sec:
            return min(
                tm.panic_move_time,
                max(tm.minimum_engine_time, safe_time * 0.35),
            )

        fullmove = board.fullmove_number

        if fullmove <= 12:
            allocated = (
                my_time * tm.opening_time_fraction
                + my_inc * 0.75
            )
        elif fullmove > 30:
            allocated = (
                my_time * tm.endgame_time_fraction
                + my_inc * 0.8
            )
        else:
            allocated = (
                my_time * tm.midgame_time_fraction
                + my_inc * 0.8
            )

        return max(
            tm.minimum_engine_time,
            min(allocated, safe_time),
        )

    def _probe_polyglot(self, board: chess.Board) -> Optional[chess.Move]:
        if self._book_reader is None:
            return None

        try:
            entry = self._book_reader.find(board)
            if entry:
                logger.info("Polyglot book hit: %s", entry.move)
                return entry.move
        except Exception as exc:
            logger.debug("Polyglot probe failed: %s", exc)

        return None

    def _tablebase_session(self) -> requests.Session:
        session = getattr(self._tb_local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(
                {"User-Agent": "Oxydan12/1.0"}
            )
            self._tb_local.session = session
        return session

    def _probe_online_tablebase(
        self, board: chess.Board
    ) -> Optional[chess.Move]:
        if not self.settings.tablebase_enabled:
            return None
        if len(board.piece_map()) > 7:
            return None

        try:
            response = self._tablebase_session().get(
                "https://tablebase.lichess.ovh/standard",
                params={"fen": board.fen()},
                timeout=self.settings.tablebase_timeout_sec,
            )
            response.raise_for_status()

            data = response.json()
            moves = data.get("moves", [])
            if not moves:
                return None

            # Lichess tablebase returns moves sorted by DTZ/winning priority.
            # Use the first legal move instead of trusting malformed data.
            for item in moves:
                uci = item.get("uci")
                if not uci:
                    continue
                move = chess.Move.from_uci(uci)
                if move in board.legal_moves:
                    logger.info("Online tablebase hit: %s", uci)
                    return move
        except Exception as exc:
            logger.debug("Tablebase probe failed: %s", exc)

        return None

    @staticmethod
    def _fallback_move(board: chess.Board) -> chess.Move:
        legal_moves = list(board.legal_moves)
        if not legal_moves:
            raise RuntimeError("No legal moves available.")

        # Prefer checkmate, then captures, then checks. This is only a
        # last-resort fallback and deliberately avoids expensive searching.
        for move in legal_moves:
            board.push(move)
            is_mate = board.is_checkmate()
            board.pop()
            if is_mate:
                return move

        captures = [m for m in legal_moves if board.is_capture(m)]
        if captures:
            # Prefer captures with the highest captured piece value.
            values = {
                chess.PAWN: 100,
                chess.KNIGHT: 320,
                chess.BISHOP: 330,
                chess.ROOK: 500,
                chess.QUEEN: 900,
                chess.KING: 20000,
            }
            captures.sort(
                key=lambda m: values.get(
                    board.piece_at(m.to_square).piece_type
                    if board.piece_at(m.to_square)
                    else chess.PAWN,
                    0,
                ),
                reverse=True,
            )
            return captures[0]

        for move in legal_moves:
            if board.gives_check(move):
                return move

        return legal_moves[0]

    def get_move(
        self, board: chess.Board, my_time: float, my_inc: float
    ) -> chess.Move:
        if board.is_game_over():
            raise RuntimeError("Cannot select a move: game is over.")

        # Opening book is local and has no network latency.
        book_move = self._probe_polyglot(board)
        if book_move and book_move in board.legal_moves:
            return book_move

        # Never make a network tablebase request when the clock is dangerously
        # low. This prevents a tablebase timeout from causing a flag.
        if (
            self.settings.tablebase_enabled
            and my_time > self.settings.time_config.panic_threshold_sec
        ):
            tb_move = self._probe_online_tablebase(board)
            if tb_move and tb_move in board.legal_moves:
                return tb_move

        allocated_time = self._allocate_time(board, my_time, my_inc)

        try:
            move = self.engine_pool.get_best_move(
                board, time_limit=allocated_time
            )
            if move in board.legal_moves:
                return move
            raise RuntimeError(f"Engine returned illegal move: {move}")
        except Exception as exc:
            logger.error(
                "Engine evaluation failed: %s. Using legal fallback.", exc
            )
            return self._fallback_move(board)

    def close(self) -> None:
        if self._book_reader is not None:
            try:
                self._book_reader.close()
            except Exception:
                pass
            self._book_reader = None

        self.engine_pool.close()
        self.session.close()


# ---------------------------------------------------------------------------
# Lichess API client
# ---------------------------------------------------------------------------
class LichessClient:
    API_PREFIX = "/api"

    def __init__(self, settings: Settings):
        token = settings.token.strip()
        if not token or token == "YOUR_LICHESS_API_TOKEN":
            raise ValueError(
                "Set Settings.token to a valid Lichess BOT API token."
            )

        self.base_url = settings.lichess_base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/x-ndjson",
                "User-Agent": "Oxydan12/1.0",
            }
        )
        self.request_timeout = settings.request_timeout_sec

    def _url(self, path: str) -> str:
        return f"{self.base_url}{self.API_PREFIX}{path}"

    def get_account(self) -> dict:
        response = self.session.get(
            self._url("/account"),
            timeout=self.request_timeout,
        )
        response.raise_for_status()
        return response.json()

    def accept_challenge(self, challenge_id: str) -> None:
        response = self.session.post(
            self._url(f"/challenge/{challenge_id}/accept"),
            timeout=self.request_timeout,
        )
        response.raise_for_status()

    def decline_challenge(
        self, challenge_id: str, reason: str = "generic"
    ) -> None:
        response = self.session.post(
            self._url(f"/challenge/{challenge_id}/decline"),
            data={"reason": reason},
            timeout=self.request_timeout,
        )
        response.raise_for_status()

    def stream_events(self):
        """
        Streams global bot events. Lichess uses newline-delimited JSON.
        """
        with self.session.get(
            self._url("/stream/event"),
            stream=True,
            timeout=(self.request_timeout, None),
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                yield json.loads(line)

    def _thread_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(self.session.headers)
        return session

    def stream_game(self, game_id: str):
        session = self._thread_session()
        try:
            with session.get(
                self._url(f"/bot/game/stream/{game_id}"),
                stream=True,
                timeout=(self.request_timeout, None),
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    yield json.loads(line)
        finally:
            session.close()

    def make_move(self, game_id: str, move: chess.Move) -> None:
        uci = move.uci()
        session = self._thread_session()
        try:
            response = session.post(
                self._url(f"/bot/game/{game_id}/move/{uci}"),
                timeout=self.request_timeout,
            )
            response.raise_for_status()
        finally:
            session.close()

    def close(self) -> None:
        self.session.close()


# ---------------------------------------------------------------------------
# Lichess game worker
# ---------------------------------------------------------------------------
class GameWorker(threading.Thread):
    def __init__(
        self,
        client: LichessClient,
        bot: OxydanBot,
        active_games: ActiveGames,
        game_id: str,
        my_username: str,
        challenge_id: Optional[str] = None,
    ):
        super().__init__(name=f"Game-{game_id}", daemon=True)
        self.client = client
        self.bot = bot
        self.active_games = active_games
        self.game_id = game_id
        self.my_username = my_username.lower()
        self.challenge_id = challenge_id

    def _initial_board(self, game_full: dict) -> chess.Board:
        initial_fen = game_full.get("initialFen") or game_full.get("fen")

        # Lichess may identify the normal initial position as "startpos".
        if not initial_fen or initial_fen in {
            "startpos",
            "standard",
            chess.STARTING_FEN,
        }:
            return chess.Board()

        return chess.Board(initial_fen)

    @staticmethod
    def _clock_for_side(state: dict, color: chess.Color) -> tuple[float, float]:
        """
        Lichess bot game states contain wtime/btime in milliseconds and
        winc/binc in milliseconds.
        """
        if color == chess.WHITE:
            return (
                max(0.0, state.get("wtime", 0) / 1000.0),
                max(0.0, state.get("winc", 0) / 1000.0),
            )
        return (
            max(0.0, state.get("btime", 0) / 1000.0),
            max(0.0, state.get("binc", 0) / 1000.0),
        )

    def run(self) -> None:
        self.active_games.confirm_game_start(
            self.game_id, self.challenge_id
        )

        try:
            game_full = None
            board = None
            my_color: Optional[chess.Color] = None

            for event in self.client.stream_game(self.game_id):
                event_type = event.get("type")

                if event_type == "gameFull":
                    game_full = event
                    white = event.get("white", {})
                    black = event.get("black", {})

                    white_id = str(white.get("id", "")).lower()
                    white_name = str(white.get("name", "")).lower()
                    black_id = str(black.get("id", "")).lower()
                    black_name = str(black.get("name", "")).lower()

                    if self.my_username in {white_id, white_name}:
                        my_color = chess.WHITE
                    elif self.my_username in {black_id, black_name}:
                        my_color = chess.BLACK
                    else:
                        raise RuntimeError(
                            f"Could not determine bot color in game {self.game_id}"
                        )

                    board = self._initial_board(game_full)

                    state = event.get("state", {})
                    moves_str = state.get("moves", "")
                    for uci in moves_str.split():
                        board.push_uci(uci)

                elif event_type == "gameState":
                    if game_full is None or board is None or my_color is None:
                        logger.warning(
                            "Received gameState before gameFull for %s; "
                            "ignoring until full state arrives.",
                            self.game_id,
                        )
                        continue

                    moves_str = event.get("moves", "")
                    board = self._initial_board(game_full)

                    for uci in moves_str.split():
                        board.push_uci(uci)

                    status = event.get("status")
                    if status and status != "started":
                        logger.info(
                            "Game %s finished: %s",
                            self.game_id,
                            status,
                        )
                        break

                elif event_type == "chatLine":
                    continue

                elif event_type == "gameFinish":
                    logger.info("Game %s finished.", self.game_id)
                    break

                if event_type not in {"gameFull", "gameState"}:
                    continue

                if board is None or my_color is None:
                    continue

                if board.is_game_over():
                    break

                # Only calculate when it is actually our turn.
                if board.turn != my_color:
                    continue

                # gameFull has a nested state object; gameState is already
                # the current state.
                current_state = (
                    event.get("state", {})
                    if event_type == "gameFull"
                    else event
                )

                my_time, my_inc = self._clock_for_side(
                    current_state, my_color
                )

                logger.info(
                    "Game %s to move as %s. Clock %.3fs + %.3fs",
                    self.game_id,
                    "white" if my_color == chess.WHITE else "black",
                    my_time,
                    my_inc,
                )

                move = self.bot.get_move(board, my_time, my_inc)

                if move not in board.legal_moves:
                    raise RuntimeError(
                        f"Safety check failed: illegal move {move}"
                    )

                self.client.make_move(self.game_id, move)

                # Optimistically update our local board. The next gameState
                # remains authoritative and will rebuild from the move list.
                board.push(move)

        except Exception:
            logger.exception("Game worker crashed for %s", self.game_id)
        finally:
            self.active_games.game_finished(self.game_id)


# ---------------------------------------------------------------------------
# Main Lichess bot manager
# ---------------------------------------------------------------------------
class LichessBotManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = LichessClient(settings)
        self.active_games = ActiveGames(settings.max_concurrent_games)
        self.bot = OxydanBot(settings)
        self.stop_event = threading.Event()
        self.workers: Dict[str, GameWorker] = {}
        self.lock = threading.Lock()
        self.username = ""

    def _handle_challenge(self, event: dict) -> None:
        challenge = event.get("challenge", {})
        challenge_id = challenge.get("id")
        if not challenge_id:
            return

        challenger = challenge.get("challenger", {})
        challenger_name = challenger.get("name", "?")
        variant = challenge.get("variant", {}).get("key", "standard")

        if not self.active_games.reserve_slot(challenge_id):
            logger.info(
                "Rejecting challenge %s from %s: no free game slot.",
                challenge_id,
                challenger_name,
            )
            try:
                self.client.decline_challenge(
                    challenge_id, reason="later"
                )
            except Exception:
                logger.exception("Failed to decline challenge %s",
                                 challenge_id)
            return

        # Keep the bot conservative: standard chess only.
        if variant != "standard":
            self.active_games.release_reservation(challenge_id)
            try:
                self.client.decline_challenge(
                    challenge_id, reason="variant"
                )
            except Exception:
                logger.exception("Failed to decline non-standard challenge")
            return

        try:
            logger.info(
                "Accepting challenge %s from %s",
                challenge_id,
                challenger_name,
            )
            self.client.accept_challenge(challenge_id)
        except Exception:
            self.active_games.release_reservation(challenge_id)
            logger.exception(
                "Failed to accept challenge %s", challenge_id
            )

    def _cleanup_workers(self) -> None:
        with self.lock:
            finished = [
                game_id
                for game_id, worker in self.workers.items()
                if not worker.is_alive()
            ]
            for game_id in finished:
                self.workers.pop(game_id, None)

    def _start_game(self, event: dict) -> None:
        game = event.get("game", {})
        game_id = game.get("id")
        if not game_id:
            return

        with self.lock:
            if game_id in self.workers:
                return

        worker = GameWorker(
            client=self.client,
            bot=self.bot,
            active_games=self.active_games,
            game_id=game_id,
            my_username=self.username,
            challenge_id=None,
        )

        with self.lock:
            self.workers[game_id] = worker

        worker.start()

    def run(self) -> None:
        account = self.client.get_account()
        self.username = account.get("username", "").lower()
        if not self.username:
            raise RuntimeError("Could not determine bot username.")

        logger.info("Authenticated as Lichess account: %s", self.username)

        while not self.stop_event.is_set():
            try:
                self._cleanup_workers()

                for event in self.client.stream_events():
                    if self.stop_event.is_set():
                        break

                    self._cleanup_workers()
                    event_type = event.get("type")

                    if event_type == "challenge":
                        self._handle_challenge(event)

                    elif event_type == "gameStart":
                        self._start_game(event)

            except requests.RequestException as exc:
                if self.stop_event.is_set():
                    break
                logger.error("Lichess event stream error: %s", exc)
                time.sleep(2.0)

            except Exception:
                if self.stop_event.is_set():
                    break
                logger.exception("Unexpected event loop error")
                time.sleep(2.0)

    def close(self) -> None:
        self.stop_event.set()

        self.client.close()
        self.bot.close()


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
def run_self_test(settings: Settings) -> bool:
    logger.info("=== Running Oxydan 12 Engine Self-Test ===")
    bot: Optional[OxydanBot] = None

    try:
        bot = OxydanBot(settings)
        board = chess.Board()

        move = bot.get_move(
            board,
            my_time=180.0,
            my_inc=2.0,
        )

        logger.info("Self-Test move: %s", move)

        if move in board.legal_moves:
            logger.info(
                "Self-Test PASSED: legal move returned."
            )
            return True

        logger.error("Self-Test FAILED: illegal move returned.")
        return False

    except Exception as exc:
        logger.exception("Self-Test FAILED: %s", exc)
        return False

    finally:
        if bot:
            bot.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Oxydan 12 Lichess Bot"
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run engine diagnostic self-test and exit.",
    )
    args = parser.parse_args()

    settings = Config.settings

    # Token is loaded from the LICHESS_TOKEN environment variable.
    if args.self_test:
        success = run_self_test(settings)
        sys.exit(0 if success else 1)

    manager = LichessBotManager(settings)

    def shutdown_handler(sig, frame):
        logger.info("Shutdown signal received.")
        manager.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    logger.info("Oxydan 12 bot is running.")
    try:
        manager.run()
    finally:
        manager.close()


if __name__ == "__main__":
    main()
