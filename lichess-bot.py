import os
import sys
import berserk
import chess
import chess.engine
import time
import chess.polyglot
import threading
import yaml
import requests
import queue
import random
from datetime import timedelta
from matchmaking import Matchmaker, SETTINGS as MM_SETTINGS

# ==========================================================
# âš™ï¸ AYARLAR
# ==========================================================
SETTINGS = {
    "TOKEN":                      os.environ.get('LICHESS_TOKEN'),
    "ENGINE_PATH":                "./src/Ethereal",
    "BOOK_PATH":                  "./book.bin",

    "MAX_PARALLEL_GAMES":         2,
    "MAX_TOTAL_RUNTIME":          21600,   # 6 saat
    "MAX_GAME_TIME_LIMIT":        1800,    # 30+0'a kadar
    "MIN_GAME_SECONDS_REMAINING": 300,     # 5 dk gÃ¼venlik payÄ±
    "MIN_TIME_TO_DECLINE":        600,     # 10 dk buffer

    "LATENCY_BUFFER":             0.07,
    "TABLEBASE_PIECE_LIMIT":      7,
    "ONLINE_TABLEBASE_ENABLED":    True,
    "MIN_TIME_FOR_TABLEBASE":      12.0,
    "ABORT_WAIT_SECONDS":         60,
    "LOSING_SCORE_THRESHOLD":     -300,
    "CHAT_ENABLED":               True,
    "CHAT_IN_RATED":              True,
    "SCORE_CHAT_ENABLED":         False,
}

# ==========================================================
# ğŸ’¬ MESAJ HAVUZLARI
# ==========================================================
MESSAGES = {
    "greeting_bot": [
        "Hi! Oxydan 11 ready. Developed by Emir KaradaÄŸ. Good luck! â™Ÿï¸",
        "Let's play! May the best engine win. Powered by Oxydan 11 ğŸ¤–",
        "Oxydan 11 on the board! Good luck! âš¡",
        "Hello! Bringing Oxydan 11's A-game today ğŸ˜¤â™Ÿï¸",
    ],
    "greeting_human": [
        "Hi! I'm Oxydan 11, a chess bot developed by Emir KaradaÄŸ. Good luck and have fun! ğŸ“â™Ÿï¸",
        "Welcome! I'm Oxydan 11. Let's play! After the game, I can analyze moves with you ğŸ¤–",
        "Hello! Oxydan 11 here, created by Emir KaradaÄŸ. Good luck! ğŸ“",
        "Hi there! Let's play a great game. Proudly developed by Emir KaradaÄŸ! â™Ÿï¸",
    ],
    "win": [
        "Good game! Well played ğŸ¤",
        "Thanks for the game! You put up a great fight â™Ÿï¸",
        "GG! That was an interesting game ğŸ¯",
        "Well played! Hope to play again soon ğŸ¤–",
    ],
    "loss": [
        "Good game! You played well, congratulations ğŸ‰",
        "Well deserved win! GG ğŸ¤",
        "Excellent play! I'll have to do better next time ğŸ˜…",
        "GG! You outplayed Oxydan 11 today ğŸ‘",
    ],
    "draw": [
        "Good game! A well-earned draw ğŸ¤",
        "Balanced game! GG â™Ÿï¸",
        "A draw! Both sides fought well ğŸ¯",
    ],
    "losing_realization": [
        "You're playing really well, I'm in trouble here! ğŸ˜…",
        "Nice moves! I can see this is going to be tough ğŸ˜¬",
        "Impressive! You've got a strong position ğŸ‘",
        "I have to admit, you're outplaying me right now! ğŸ“",
    ],
    "human_postgame": [
        "GG! If you'd like to review any moves or have chess questions, feel free to ask! ğŸ“",
        "Well played! I'm happy to discuss the game or give tips if you're interested ğŸ¤–â™Ÿï¸",
        "Good game! Any questions about the moves? I'm here to help! ğŸ“",
    ],
}

def pick_message(category):
    return random.choice(MESSAGES.get(category, ["Good game!"]))


def make_client():
    return berserk.Client(session=berserk.TokenSession(SETTINGS["TOKEN"]))


def active_count(active_games, active_games_lock, pending_starts=None):
    with active_games_lock:
        pending = pending_starts["count"] if pending_starts else 0
        return len(active_games) + pending


def reserve_game_slot(active_games, active_games_lock, pending_starts):
    with active_games_lock:
        if len(active_games) + pending_starts["count"] >= SETTINGS["MAX_PARALLEL_GAMES"]:
            return False
        pending_starts["count"] += 1
        return True


def release_reserved_slot(active_games_lock, pending_starts):
    with active_games_lock:
        if pending_starts["count"] > 0:
            pending_starts["count"] -= 1


def active_add_if_room(active_games, active_games_lock, game_id):
    with active_games_lock:
        if game_id in active_games:
            return False
        if len(active_games) >= SETTINGS["MAX_PARALLEL_GAMES"]:
            return False
        active_games.add(game_id)
        return True


def active_discard(active_games, active_games_lock, game_id):
    with active_games_lock:
        active_games.discard(game_id)

# ==========================================================
# âœ… DÃœZELTME 1: runtime_watchdog tanÄ±mlandÄ±
# ==========================================================
def runtime_watchdog(start_time, active_games, active_games_lock):
    """
    Arka planda Ã§alÄ±ÅŸÄ±r. MAX_TOTAL_RUNTIME aÅŸÄ±lÄ±nca
    aktif oyun yoksa sistemi kapatÄ±r, varsa bekler.
    """
    while True:
        time.sleep(30)
        elapsed = time.time() - start_time
        if elapsed > SETTINGS["MAX_TOTAL_RUNTIME"]:
            count = active_count(active_games, active_games_lock)
            if count == 0:
                print("â° [Watchdog] Ã‡alÄ±ÅŸma sÃ¼resi doldu, sistem kapatÄ±lÄ±yor.", flush=True)
                os._exit(0)
            else:
                print(f"â° [Watchdog] SÃ¼re doldu ama {count} aktif oyun var, bekleniyor...", flush=True)


# ==========================================================
# ğŸ§  AÃ‡ILIÅ TAKÄ°BÄ° (THREAD-SAFE)
# ==========================================================
class OpeningTracker:
    def __init__(self, memory_size=10):
        self.memory_size = memory_size
        self.recent = []
        self.lock = threading.Lock()

    def record(self, opening_key):
        with self.lock:
            if opening_key in self.recent:
                self.recent.remove(opening_key)
            self.recent.append(opening_key)
            if len(self.recent) > self.memory_size:
                self.recent.pop(0)

    def was_recent(self, opening_key):
        with self.lock:
            return opening_key in self.recent

    def get_opening_key(self, board):
        moves = list(board.move_stack)[:5]
        return "_".join(m.uci() for m in moves)


# ==========================================================
# ğŸ§  MOTOR YÃ–NETÄ°MÄ°
# ==========================================================
class OxydanV11:
    def __init__(self, exe_path, uci_options=None):
        self.exe_path        = exe_path
        self.book_path       = SETTINGS["BOOK_PATH"]
        self.engine_pool     = queue.Queue()
        self.opening_tracker = OpeningTracker(memory_size=10)

        pool_size = SETTINGS["MAX_PARALLEL_GAMES"] + 1
        # config_overhead'i dÃ¶ngÃ¼ dÄ±ÅŸÄ±nda tanÄ±mla (son deÄŸer deÄŸil, ilk deÄŸer)
        config_overhead = 100
        if uci_options:
            config_overhead = uci_options.get("Move Overhead",
                              uci_options.get("MoveOverhead", 100))

        try:
            for _ in range(pool_size):
                eng = chess.engine.SimpleEngine.popen_uci(self.exe_path, timeout=30)
                # "Move Overhead" boÅŸluklu yazÄ±m zorunlu â€” MoveOverhead Ã§alÄ±ÅŸmaz
                try:
                    eng.configure({"Move Overhead": config_overhead})
                except Exception:
                    try:
                        eng.configure({"MoveOverhead": config_overhead})
                    except Exception:
                        pass
                if uci_options:
                    for opt, val in uci_options.items():
                        if opt in ("MoveOverhead", "Move Overhead"):
                            continue
                        try:
                            eng.configure({opt: val})
                        except Exception:
                            pass
                self.engine_pool.put(eng)
            print(f"ğŸš€ {pool_size} Motor HazÄ±r. Move Overhead: {config_overhead}ms", flush=True)
        except Exception as e:
            print(f"KRÄ°TÄ°K HATA: {e}", flush=True)
            sys.exit(1)

    def get_score(self, board):
        engine = None
        try:
            engine = self.engine_pool.get(timeout=1)
            info   = engine.analyse(board, chess.engine.Limit(depth=6, time=0.05))
            score  = info.get("score")
            if score:
                return score.white().score(mate_score=10000)
        except Exception as e:
            print(f"âš ï¸ Skor analizi hatasÄ±: {e}")
        finally:
            if engine:
                self.engine_pool.put(engine)
        return None

    def to_seconds(self, t):
        """
        Lichess gameState clock fields (wtime/btime/winc/binc) are milliseconds.
        The old val > 1000 heuristic was dangerous: at 1000 ms or below it
        treated milliseconds as seconds, so the bot could think it had plenty
        of time while actually flagging.
        """
        if t is None:
            return 0.0
        if isinstance(t, timedelta):
            return max(0.0, t.total_seconds())
        try:
            return max(0.0, float(t) / 1000.0)
        except (TypeError, ValueError):
            return 0.0

    def calculate_smart_time(self, t, inc, board):
        buffer     = SETTINGS.get("LATENCY_BUFFER", 0.07)
        move_count = len(board.move_stack)
        legal_moves = len(list(board.legal_moves))

        # â”€â”€ âœ… DÃœZELTME 4: GranÃ¼ler panik basamaklarÄ± geri getirildi â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # Eskiden sadece t<2 ve t<5 vardÄ± â€” bullet'ta flag yeniyordu.

        if t < 0.3:
            return 0.001                                            # Premove hÄ±zÄ±

        if t < 0.8:
            return t * 0.05                                         # ~%5, max ~40ms

        if t < 2.0:
            return max(0.01, (t * 0.06) + (inc * 0.98) - buffer)   # 2sn altÄ±

        if t < 3.0:
            return max(0.02, (t * 0.08) + (inc * 1.00) - buffer)   # 3sn altÄ±

        if t < 5.0:
            return max(0.03, (t * 0.10) + (inc * 0.90) - buffer)   # 5sn altÄ±

        if t < 10.0:
            return max(0.05, (t * 0.12) + (inc * 0.80) - buffer)   # 10sn altÄ±

        # â”€â”€ NORMAL HESAPLAMA (10sn+) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if t < 30.0:
            complexity = max(0.5, min(legal_moves / 25.0, 2.5))
            think      = ((t * 0.18) + (inc * 0.80)) * complexity
            return max(0.05, min(think, t * 0.5) - buffer)

        # Rapid/Classical (30sn+)
        if move_count < 15:     divisor = 40
        elif move_count < 40:   divisor = 25
        elif move_count < 60:   divisor = 15
        else:                   divisor = 8    # Oyun sonu: daha Ã§ok dÃ¼ÅŸÃ¼n

        base_time  = t / divisor
        final_time = base_time + (inc * 0.6)
        complexity = min(legal_moves / 20.0, 2.0)
        final_time *= complexity

        if move_count >= 60:    max_frac = 0.20
        elif move_count >= 40:  max_frac = 0.15
        else:                   max_frac = 0.12

        return max(0.15, min(final_time, t * max_frac, 20.0) - buffer)

    def fallback_move(self, board):
        legal = list(board.legal_moves)
        if not legal:
            return None

        best_move = legal[0]
        best_score = -10**9
        piece_values = {
            chess.PAWN: 100,
            chess.KNIGHT: 320,
            chess.BISHOP: 330,
            chess.ROOK: 500,
            chess.QUEEN: 900,
            chess.KING: 0,
        }

        for move in legal:
            score = 0
            if board.is_capture(move):
                captured = board.piece_at(move.to_square)
                mover = board.piece_at(move.from_square)
                if captured:
                    score += 10 * piece_values.get(captured.piece_type, 0)
                if mover:
                    score -= piece_values.get(mover.piece_type, 0)
            if move.promotion:
                score += piece_values.get(move.promotion, 0)
            if board.gives_check(move):
                score += 80

            board.push(move)
            if board.is_checkmate():
                score += 100000
            if board.is_repetition(2):
                score -= 50
            board.pop()

            if score > best_score:
                best_score = score
                best_move = move

        return best_move

    def get_best_move(self, board, wtime, btime, winc, binc):
        my_time = self.to_seconds(wtime if board.turn == chess.WHITE else btime)
        my_inc  = self.to_seconds(winc  if board.turn == chess.WHITE else binc)

        # 1. KÄ°TAP
        if not board.chess960 and os.path.exists(self.book_path):
            try:
                with chess.polyglot.open_reader(self.book_path) as reader:
                    entries = list(reader.find_all(board))
                    if entries:
                        shuffled = list(entries)
                        random.shuffle(shuffled)
                        for entry in shuffled:
                            if entry.move not in board.legal_moves: continue
                            board.push(entry.move)
                            key = self.opening_tracker.get_opening_key(board)
                            board.pop()
                            if not self.opening_tracker.was_recent(key):
                                return entry.move
                        for entry in shuffled:
                            if entry.move in board.legal_moves:
                                return entry.move
            except Exception as e:
                print(f"ğŸ“– Kitap HatasÄ±: {e}")

        # 2. TABLEBASE
        if (SETTINGS.get("ONLINE_TABLEBASE_ENABLED", True)
                and my_time >= SETTINGS.get("MIN_TIME_FOR_TABLEBASE", 12.0)
                and not board.chess960
                and len(board.piece_map()) <= SETTINGS["TABLEBASE_PIECE_LIMIT"]):
            try:
                r = requests.get(
                    "https://tablebase.lichess.ovh/standard",
                    params={"fen": board.fen()},
                    timeout=min(0.4, max(0.05, my_time * 0.02))
                )
                if r.status_code == 200:
                    data = r.json()
                    if data.get("moves"):
                        best = chess.Move.from_uci(data["moves"][0]["uci"])
                        if best in board.legal_moves:
                            return best
            except:
                pass

        # 3. MOTOR
        engine = None
        try:
            engine = self.engine_pool.get(timeout=1)
            think   = self.calculate_smart_time(my_time, my_inc, board)

            white_clock = self.to_seconds(wtime)
            black_clock = self.to_seconds(btime)
            white_inc   = self.to_seconds(winc)
            black_inc   = self.to_seconds(binc)

            limit = chess.engine.Limit(
                time=think,
                white_clock=white_clock,
                black_clock=black_clock,
                white_inc=white_inc,
                black_inc=black_inc,
            )
            result = engine.play(board, limit, timeout=max(0.05, think + 0.25))
            if result.move and result.move in board.legal_moves:
                if len(board.move_stack) <= 10:
                    board.push(result.move)
                    self.opening_tracker.record(
                        self.opening_tracker.get_opening_key(board)
                    )
                    board.pop()
                return result.move
            print(f"âš ï¸ Motor yasal olmayan hamle: {result.move}, fallback.")
        except Exception as e:
            print(f"ğŸš¨ Motor HatasÄ±: {e}")
        finally:
            if engine:
                self.engine_pool.put(engine)

        return self.fallback_move(board)


# ==========================================================
# ğŸ® OYUN YÃ–NETÄ°MÄ°
# ==========================================================

def _get_game_mode(time_control):
    if not isinstance(time_control, dict): return 'blitz'
    limit = time_control.get('limit', 300)
    if limit < 180:    return 'bullet'
    elif limit < 480:  return 'blitz'
    elif limit < 1500: return 'rapid'
    else:              return 'classical'


def _send_message(client, game_id, text, spectator=False):
    """
    âœ… DÃœZELTME 2: post_chat_message â†’ post_message
    Berserk kÃ¼tÃ¼phanesinde post_chat_message mevcut deÄŸil.
    """
    if not SETTINGS.get("CHAT_ENABLED", True):
        return
    try:
        client.bots.post_message(game_id, text, spectator=spectator)
        return
    except TypeError:
        pass
    except Exception as e:
        print(f"âš ï¸ Mesaj gÃ¶nderilemedi ({game_id}, spectator={spectator}): {e}")
        return

    for kwargs in ({"room": "spectator" if spectator else "player"}, {}):
        try:
            client.bots.post_message(game_id, text, **kwargs)
            return
        except TypeError:
            continue
        except Exception as e:
            print(f"âš ï¸ Mesaj gÃ¶nderilemedi ({game_id}, {kwargs}): {e}")
            return

    print(f"âš ï¸ Mesaj gÃ¶nderilemedi ({game_id}): post_message imzasÄ± uyumsuz.")


def handle_game(client, game_id, bot, my_id, mm):
    # âœ… DÃœZELTME 5: start_time parametresi kaldÄ±rÄ±ldÄ± â€” iÃ§inde kullanÄ±lmÄ±yordu
    try:
        stream = client.bots.stream_game_state(game_id)

        board            = None
        my_color         = None
        last_move_count  = 0
        is_vs_human      = False
        game_started     = False
        game_start_time  = None
        losing_msg_sent  = False
        game_mode        = 'blitz'
        rated            = False
        opp_id           = ''

        for state in stream:
            if 'error' in state: break

            if state['type'] == 'gameFull':
                white = state.get('white', {})
                black = state.get('black', {})
                rated = bool(state.get('rated', False))
                my_color    = chess.WHITE if white.get('id') == my_id else chess.BLACK

                opp         = black if my_color == chess.WHITE else white
                opp_id      = opp.get('id', '')
                opp_title   = (opp.get('title') or '').upper()
                is_vs_human = opp_title != 'BOT'

                # âœ… DÃœZELTME 3: Blacklist resign kontrolÃ¼
                if opp_id.lower() in MM_SETTINGS.get("PERMANENT_BLACKLIST", set()):
                    print(f"ğŸš« Blacklisted rakip: {opp_id} â€” resign yapÄ±lÄ±yor.")
                    try:
                        client.bots.resign_game(game_id)
                    except Exception as e:
                        print(f"âš ï¸ Resign hatasÄ±: {e}")
                    return

                # Chess960 + initialFen ile doÄŸru board baÅŸlatma
                variant     = state.get('variant', {}).get('key', 'standard')
                is_960      = variant == 'chess960'
                initial_fen = state.get('initialFen', 'startpos')

                if initial_fen and initial_fen != 'startpos':
                    board = chess.Board(initial_fen, chess960=is_960)
                else:
                    board = chess.Board(chess960=is_960)

                clock     = state.get('clock', {})
                game_mode = 'chess960' if is_960 else _get_game_mode(clock)

                last_move_count = 0
                game_start_time = time.time()
                losing_msg_sent = False

                greeting_cat = "greeting_human" if is_vs_human else "greeting_bot"
                if not rated or SETTINGS.get("CHAT_IN_RATED", True):
                    _send_message(client, game_id, pick_message(greeting_cat))

                curr_state = state['state']

            elif state['type'] == 'gameState':
                curr_state = state
            else:
                continue

            if board is None: continue

            # Hamleleri gÃ¼ncelle (parse_uci + push: Chess960 rok iÃ§in doÄŸru yol)
            moves_str = curr_state.get('moves', '').strip()
            moves     = moves_str.split() if moves_str else []

            if len(moves) > last_move_count:
                game_started = True
                for m in moves[last_move_count:]:
                    try:
                        board.push(board.parse_uci(m))
                    except Exception as e:
                        print(f"âš ï¸ Hamle parse hatasÄ± ({m}): {e}")
                        break
                last_move_count = len(board.move_stack)

            # Abort kontrolÃ¼
            if (not game_started
                    and game_start_time
                    and (time.time() - game_start_time) > SETTINGS["ABORT_WAIT_SECONDS"]):
                try:
                    client.bots.abort_game(game_id)
                    print(f"â±ï¸ Abort: {game_id} (rakip hamle yapmadÄ±)")
                except Exception as e:
                    print(f"âš ï¸ Abort hatasÄ±: {e}")
                break

            # Oyun sonu
            status = curr_state.get('status')
            if status in ['mate', 'resign', 'draw', 'outoftime', 'aborted', 'stalemate']:
                winner       = curr_state.get('winner')
                my_color_str = 'white' if my_color == chess.WHITE else 'black'

                if status in ['draw', 'stalemate']:
                    result, msg_cat = 'draw', 'draw'
                elif winner:
                    result  = 'win' if winner == my_color_str else 'loss'
                    msg_cat = result
                else:
                    result, msg_cat = 'draw', 'draw'

                if not rated or SETTINGS.get("CHAT_IN_RATED", True):
                    _send_message(client, game_id, pick_message(msg_cat))
                if is_vs_human and (not rated or SETTINGS.get("CHAT_IN_RATED", True)):
                    time.sleep(1)
                    _send_message(client, game_id, pick_message("human_postgame"))

                if mm and status != 'aborted':
                    mm.record_game_result(result, game_mode, opponent_id=opp_id)
                break

            # Kaybetme farkÄ±ndalÄ±k mesajÄ± (sadece insanlara, orta oyun+)
            if (SETTINGS.get("SCORE_CHAT_ENABLED", False)
                    and is_vs_human and not losing_msg_sent
                    and len(board.move_stack) >= 20):
                try:
                    score = bot.get_score(board)
                    if score is not None:
                        my_score = score if my_color == chess.WHITE else -score
                        if my_score < SETTINGS["LOSING_SCORE_THRESHOLD"]:
                            _send_message(client, game_id, pick_message("losing_realization"))
                            losing_msg_sent = True
                except Exception as e:
                    print(f"âš ï¸ Skor hatasÄ±: {e}")

            # Hamle sÄ±rasÄ±
            if board.turn == my_color and not board.is_game_over():
                move = bot.get_best_move(
                    board,
                    curr_state.get('wtime'),
                    curr_state.get('btime'),
                    curr_state.get('winc'),
                    curr_state.get('binc')
                )
                if move:
                    for _ in range(3):
                        try:
                            client.bots.make_move(game_id, move.uci())
                            break
                        except Exception:
                            time.sleep(0.05)

    except Exception as e:
        print(f"ğŸš¨ Oyun HatasÄ± ({game_id}): {e}", flush=True)


def handle_game_wrapper(game_id, bot, my_id, active_games, active_games_lock, mm):
    # âœ… DÃœZELTME 5: start_time parametresi kaldÄ±rÄ±ldÄ±
    client = make_client()
    try:
        handle_game(client, game_id, bot, my_id, mm)
    finally:
        active_discard(active_games, active_games_lock, game_id)


# ==========================================================
# ğŸš€ ANA DÃ–NGÃœ
# ==========================================================

def main():
    start_time = time.time()
    client     = make_client()

    try:
        with open("config.yml", "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        my_id = client.account.get()['id']
    except Exception as e:
        print(f"BaÄŸlantÄ±/Config HatasÄ±: {e}")
        return

    if config and "matchmaking" in config:
        if "max_games" in config["matchmaking"]:
            SETTINGS["MAX_PARALLEL_GAMES"] = config["matchmaking"]["max_games"]

    bot = OxydanV11(
        SETTINGS["ENGINE_PATH"],
        uci_options=config.get('engine', {}).get('uci_options', {}) if config else {}
    )
    active_games = set()
    active_games_lock = threading.Lock()
    pending_starts = {"count": 0}

    mm = None
    if config and config.get("matchmaking"):
        mm = Matchmaker(
            client, config, active_games,
            token=SETTINGS["TOKEN"],
            active_games_lock=active_games_lock
        )
        threading.Thread(target=mm.start, daemon=True).start()

    # âœ… DÃœZELTME 1: runtime_watchdog artÄ±k tanÄ±mlÄ±, sorunsuz baÅŸlar
    threading.Thread(
        target=runtime_watchdog,
        args=(start_time, active_games, active_games_lock),
        daemon=True
    ).start()

    print(f"ğŸ”¥ Oxydan 11 HazÄ±r. ID: {my_id} | Watchdog Devrede.", flush=True)

    while True:
        try:
            for event in client.bots.stream_incoming_events():
                cur_elapsed    = time.time() - start_time
                time_remaining = SETTINGS["MAX_TOTAL_RUNTIME"] - cur_elapsed

                if event['type'] == 'challenge':
                    ch    = event['challenge']
                    ch_id = ch['id']

                    tc         = ch.get('timeControl', {})
                    time_limit = tc.get('limit', 0)
                    increment  = tc.get('increment', 0)

                    estimated_game_duration = (time_limit * 2) + (increment * 120)
                    is_time_safe = time_remaining > (
                        estimated_game_duration + SETTINGS["MIN_GAME_SECONDS_REMAINING"]
                    )

                    accept, reason = True, 'policy'
                    if mm:
                        accept, reason = mm.is_challenge_acceptable(ch)

                    can_accept = (
                        is_time_safe and
                        time_limit <= SETTINGS["MAX_GAME_TIME_LIMIT"] and
                        active_count(active_games, active_games_lock, pending_starts) < SETTINGS["MAX_PARALLEL_GAMES"] and
                        accept
                    )

                    try:
                        if can_accept:
                            if not reserve_game_slot(active_games, active_games_lock, pending_starts):
                                can_accept = False

                        if can_accept:
                            client.challenges.accept(ch_id)
                            print(
                                f"âœ… Kabul: {ch_id} | {reason} | "
                                f"Kalan: {int(time_remaining)}s | "
                                f"Tahmini maÃ§: {int(estimated_game_duration)}s",
                                flush=True
                            )
                        else:
                            if not is_time_safe:
                                detail = f"Oturum sÃ¼resi yetersiz ({int(time_remaining)}s < {int(estimated_game_duration)}s)"
                            elif time_limit > SETTINGS["MAX_GAME_TIME_LIMIT"]:
                                detail = f"Oyun Ã§ok uzun ({time_limit}s)"
                            elif active_count(active_games, active_games_lock, pending_starts) >= SETTINGS["MAX_PARALLEL_GAMES"]:
                                detail = "Paralel maÃ§ limiti dolu"
                            else:
                                detail = reason

                            client.challenges.decline(ch_id, reason='later')
                            print(f"âŒ Reddedildi: {ch_id} | {detail}", flush=True)

                    except Exception as ce:
                        if can_accept:
                            release_reserved_slot(active_games_lock, pending_starts)
                        print(f"âš ï¸ Challenge iÅŸleme hatasÄ±: {ce}", flush=True)

                elif event['type'] == 'gameStart':
                    game_id = event['game']['id']
                    release_reserved_slot(active_games_lock, pending_starts)
                    if active_add_if_room(active_games, active_games_lock, game_id):
                        threading.Thread(
                            target=handle_game_wrapper,
                            # âœ… DÃœZELTME 5: start_time args'dan kaldÄ±rÄ±ldÄ±
                            args=(game_id, bot, my_id, active_games, active_games_lock, mm),
                            daemon=True
                        ).start()

        except Exception as e:
            print(f"âš ï¸ Lichess akÄ±ÅŸÄ± koptu, yeniden baÄŸlanÄ±lÄ±yor: {e}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
