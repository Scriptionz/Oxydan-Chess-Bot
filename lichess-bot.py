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
from matchmaking import Matchmaker

# ==========================================================
# ⚙️ AYARLAR (Varsayılanlar - Config.yml ile ezilebilir)
# ==========================================================
SETTINGS = {
    "TOKEN":                 os.environ.get('LICHESS_TOKEN'),
    "ENGINE_PATH":           "./src/Ethereal",
    "BOOK_PATH":             "./book.bin",

    "MAX_PARALLEL_GAMES":     2,
    "MAX_TOTAL_RUNTIME":      21300,
    "STOP_ACCEPTING_MINS":    15,

    "LATENCY_BUFFER":         0.07,  # Python yükü için optimize edildi
    "TABLEBASE_PIECE_LIMIT":  7,
    "ABORT_WAIT_SECONDS":      60,
    "LOSING_SCORE_THRESHOLD": -300,
}

# ==========================================================
# 💬 MESAJ HAVUZLARI (Sürüm 11 & Emir Karadağ Entegrasyonu)
# ==========================================================
MESSAGES = {
    "greeting_bot": [
        "Hi! Oxydan 11 ready. Developed by Emir Karadağ. Good luck! ♟️",
        "Let's play! May the best engine win. Powered by Oxydan 11 🤖",
        "Oxydan 11 on the board! Good luck! ⚡",
        "Hello! Bringing Oxydan 11's A-game today 😤♟️",
    ],
    "greeting_human": [
        "Hi! I'm Oxydan 11, a chess bot developed by Emir Karadağ. Good luck and have fun! 🎓♟️",
        "Welcome! I'm Oxydan 11. Let's play! After the game, I can analyze moves with you 🤖",
        "Hello! Oxydan 11 here, created by Emir Karadağ. Good luck! 🎓",
        "Hi there! Let's play a great game. Proudly developed by Emir Karadağ! ♟️",
    ],
    "win": [
        "Good game! Well played 🤝",
        "Thanks for the game! You put up a great fight ♟️",
        "GG! That was an interesting game 🎯",
        "Well played! Hope to play again soon 🤖",
    ],
    "loss": [
        "Good game! You played well, congratulations 🎉",
        "Well deserved win! GG 🤝",
        "Excellent play! I'll have to do better next time 😅",
        "GG! You outplayed Oxydan 11 today 👏",
    ],
    "draw": [
        "Good game! A well-earned draw 🤝",
        "Balanced game! GG ♟️",
        "A draw! Both sides fought well 🎯",
    ],
    "losing_realization": [
        "You're playing really well, I'm in trouble here! 😅",
        "Nice moves! I can see this is going to be tough 😬",
        "Impressive! You've got a strong position 👏",
        "I have to admit, you're outplaying me right now! 🎓",
    ],
    "human_postgame": [
        "GG! If you'd like to review any moves or have chess questions, feel free to ask! 🎓",
        "Well played! I'm happy to discuss the game or give tips if you're interested 🤖♟️",
        "Good game! Any questions about the moves? I'm here to help! 🎓",
    ],
}

def pick_message(category):
    return random.choice(MESSAGES.get(category, ["Good game!"]))


# ==========================================================
# 🧠 AÇILIŞ TAKİBİ
# ==========================================================
class OpeningTracker:
    def __init__(self, memory_size=10):
        self.memory_size = memory_size
        self.recent = []

    def record(self, opening_key):
        if opening_key in self.recent:
            self.recent.remove(opening_key)
        self.recent.append(opening_key)
        if len(self.recent) > self.memory_size:
            self.recent.pop(0)

    def was_recent(self, opening_key):
        return opening_key in self.recent

    def get_opening_key(self, board):
        moves = list(board.move_stack)[:5]
        return "_".join(m.uci() for m in moves)


# ==========================================================
# 🧠 MOTOR YÖNETİMİ
# ==========================================================
class OxydanV11:
    def __init__(self, exe_path, uci_options=None):
        self.exe_path        = exe_path
        self.book_path       = SETTINGS["BOOK_PATH"]
        self.engine_pool     = queue.Queue()
        self.opening_tracker = OpeningTracker(memory_size=10)

        pool_size = SETTINGS["MAX_PARALLEL_GAMES"] + 1
        try:
            for _ in range(pool_size):
                eng = chess.engine.SimpleEngine.popen_uci(self.exe_path, timeout=30)
                
                config_overhead = uci_options.get("MoveOverhead", uci_options.get("Move Overhead", 100))
                
                try: eng.configure({"Move Overhead": config_overhead})
                except:
                    try: eng.configure({"MoveOverhead": config_overhead})
                    except: pass
                
                if uci_options:
                    for opt, val in uci_options.items():
                        if opt in ["MoveOverhead", "Move Overhead"]: continue
                        try: eng.configure({opt: val})
                        except: pass
                        
                self.engine_pool.put(eng)
            print(f"🚀 {pool_size} Motor Hazır. MoveOverhead: {config_overhead}ms olarak ayarlandı.", flush=True)
        except Exception as e:
            print(f"KRİTİK HATA: {e}", flush=True)
            sys.exit(1)

    def get_score(self, board):
        engine = None
        try:
            engine = self.engine_pool.get()
            info = engine.analyse(board, chess.engine.Limit(depth=6, time=0.05))
            score = info.get("score")
            if score:
                return score.white().score(mate_score=10000)
        except Exception as e:
            print(f"⚠️ Skor analizi hatası: {e}")
        finally:
            if engine: self.engine_pool.put(engine)
        return None

    def to_seconds(self, t):
        if t is None: return 0.0
        if isinstance(t, timedelta): return t.total_seconds()
        try:
            val = float(t)
            return val / 1000.0 if val > 1000 else val
        except: return 0.0

    def calculate_smart_time(self, t, inc, board):
        buffer = SETTINGS.get("LATENCY_BUFFER", 0.07)
        move_count = len(board.move_stack)
        
        if t < 2.0:
            return max(0.01, (t * 0.02) + (inc * 0.98) - buffer)
        elif t < 5.0:
            think = (t * 0.03) + (inc * 0.95)
            return max(0.02, think - buffer)
        elif t < 10.0:
            think = (t * 0.05) + (inc * 0.90)
            return max(0.04, think - buffer)
        elif t < 30.0:
            think = (t / 60) + (inc * 0.85)
            return max(0.05, min(think, 1.2) - buffer)
        else:
            if move_count < 15: divisor = 50
            elif move_count < 40: divisor = 35
            else: divisor = 25
                
            base_time = (t / divisor)
            final_time = base_time + (inc * 0.7)
            tension = 0.8 + (board.legal_moves.count() / 60.0)
            final_time *= tension
            return max(0.1, min(final_time, t * 0.08, 12.0) - buffer)

    def get_best_move(self, board, wtime, btime, winc, binc):
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
                print(f"📖 Kitap Hatası: {e}")

        if not board.chess960 and len(board.piece_map()) <= SETTINGS["TABLEBASE_PIECE_LIMIT"]:
            try:
                r = requests.get(f"https://tablebase.lichess.ovh/standard?fen={board.fen()}", timeout=0.5)
                if r.status_code == 200:
                    data = r.json()
                    if data.get("moves"):
                        best = chess.Move.from_uci(data["moves"][0]["uci"])
                        if best in board.legal_moves:
                            return best
            except: pass

        engine = None
        try:
            engine = self.engine_pool.get()
            my_time = self.to_seconds(wtime if board.turn == chess.WHITE else btime)
            my_inc  = self.to_seconds(winc  if board.turn == chess.WHITE else binc)
            think   = self.calculate_smart_time(my_time, my_inc, board)

            result = engine.play(board, chess.engine.Limit(time=think))
            if result.move and result.move in board.legal_moves:
                if len(board.move_stack) <= 10:
                    board.push(result.move)
                    self.opening_tracker.record(self.opening_tracker.get_opening_key(board))
                    board.pop()
                return result.move
        except Exception as e:
            print(f"🚨 Motor Hatası: {e}")
        finally:
            if engine: self.engine_pool.put(engine)

        legal = list(board.legal_moves)
        return legal[0] if legal else None

# ==========================================================
# 🎮 GAME MANAGEMENT
# ==========================================================
def _get_game_mode(time_control):
    if not isinstance(time_control, dict): return 'blitz'
    limit = time_control.get('limit', 300)
    if limit < 180:    return 'bullet'
    elif limit < 480:  return 'blitz'
    elif limit < 1500: return 'rapid'
    else:              return 'classical'


def handle_game(client, game_id, bot, my_id, mm):
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

        for state in stream:
            if 'error' in state: break

            if state['type'] == 'gameFull':
                white = state.get('white', {})
                black = state.get('black', {})
                my_color = chess.WHITE if white.get('id') == my_id else chess.BLACK

                opp         = black if my_color == chess.WHITE else white
                opp_id      = opp.get('id', '')
                opp_title   = (opp.get('title') or '').upper()
                is_vs_human = opp_title != 'BOT'

                if mm:
                    mm.opponent_tracker[opp_id] = mm.opponent_tracker.get(opp_id, 0) + 1

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

                # 🔥 DÜZELTME: client.bots.post_message yerine post_chat_message kullanıldı
                greeting_cat = "greeting_human" if is_vs_human else "greeting_bot"
                try: 
                    client.bots.post_chat_message(game_id, room="player", text=pick_message(greeting_cat))
                except Exception as e: 
                    print(f"⚠️ Karşılama mesajı gönderilemedi: {e}")

                curr_state = state['state']

            elif state['type'] == 'gameState':
                curr_state = state
            else:
                continue

            if board is None: continue

            moves_str = curr_state.get('moves', '').strip()
            moves     = moves_str.split() if moves_str else []

            if len(moves) > last_move_count:
                game_started = True
                for m in moves[last_move_count:]:
                    try: board.push(board.parse_uci(m))
                    except: break
                last_move_count = len(board.move_stack)

            if (not game_started and game_start_time and (time.time() - game_start_time) > SETTINGS["ABORT_WAIT_SECONDS"]):
                try: client.bots.abort_game(game_id)
                except: pass
                break

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

                # 🔥 DÜZELTME: post_chat_message entegrasyonu
                try:
                    client.bots.post_chat_message(game_id, room="player", text=pick_message(msg_cat))
                    if is_vs_human:
                        time.sleep(1)
                        client.bots.post_chat_message(game_id, room="player", text=pick_message("human_postgame"))
                except Exception as e: 
                    print(f"⚠️ Oyun sonu mesajı gönderilemedi: {e}")

                if mm and status != 'aborted':
                    mm.record_game_result(result, game_mode)
                break

            # 🔥 DÜZELTME: post_chat_message entegrasyonu
            if is_vs_human and not losing_msg_sent and len(board.move_stack) >= 20:
                try:
                    score = bot.get_score(board)
                    if score is not None:
                        my_score = score if my_color == chess.WHITE else -score
                        if my_score < SETTINGS["LOSING_SCORE_THRESHOLD"]:
                            client.bots.post_chat_message(game_id, room="player", text=pick_message("losing_realization"))
                            losing_msg_sent = True
                except Exception as e: 
                    print(f"⚠️ Kaybetme farkındalık mesajı hatası: {e}")

            if board.turn == my_color and not board.is_game_over():
                move = bot.get_best_move(
                    board,
                    curr_state.get('wtime'), curr_state.get('btime'),
                    curr_state.get('winc'), curr_state.get('binc')
                )
                if move:
                    for _ in range(3):
                        try:
                            client.bots.make_move(game_id, move.uci())
                            break
                        except: time.sleep(0.05)

    except Exception as e:
        print(f"🚨 Oyun Hatası ({game_id}): {e}", flush=True)


def handle_game_wrapper(client, game_id, bot, my_id, active_games, mm):
    try: handle_game(client, game_id, bot, my_id, mm)
    finally: active_games.discard(game_id)


# ==========================================================
# 🚀 ANA DÖNGÜ
# ==========================================================
def main():
    start_time = time.time()
    session    = berserk.TokenSession(SETTINGS["TOKEN"])
    client     = berserk.Client(session=session)

    try:
        with open("config.yml", "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        my_id = client.account.get()['id']
    except Exception as e:
        print(f"Bağlantı/Config Hatası: {e}")
        return

    if config and "matchmaking" in config:
        if "max_games" in config["matchmaking"]:
            SETTINGS["MAX_PARALLEL_GAMES"] = config["matchmaking"]["max_games"]

    bot = OxydanV11(
        SETTINGS["ENGINE_PATH"],
        uci_options=config.get('engine', {}).get('uci_options', {})
    )
    active_games = set()

    mm = None
    if config.get("matchmaking"):
        mm = Matchmaker(client, config, active_games, token=SETTINGS["TOKEN"])
        threading.Thread(target=mm.start, daemon=True).start()

    print(f"🔥 Oxydan 11 Hazır. ID: {my_id} | Geliştirici: Emir Karadağ", flush=True)

    while True:
        try:
            for event in client.bots.stream_incoming_events():
                cur_elapsed  = time.time() - start_time
                should_stop  = (os.path.exists("STOP.txt") or cur_elapsed > SETTINGS["MAX_TOTAL_RUNTIME"])
                close_to_end = cur_elapsed > (SETTINGS["MAX_TOTAL_RUNTIME"] - (SETTINGS["STOP_ACCEPTING_MINS"] * 60))

                if event['type'] == 'challenge':
                    ch    = event['challenge']
                    ch_id = ch['id']

                    accept, reason = True, 'policy'
                    if mm:
                        accept, reason = mm.is_challenge_acceptable(ch)

                    can_accept = (
                        not should_stop and not close_to_end and
                        len(active_games) < SETTINGS["MAX_PARALLEL_GAMES"] and accept
                    )

                    # 🔥 DÜZELTME: Meydan okuma kabul/ret adımları yerel try-except bloğuna alındı
                    try:
                        if can_accept:
                            client.challenges.accept(ch_id)
                            print(f"✅ Kabul: {ch_id} — {reason}", flush=True)
                        else:
                            decline_reason = 'later' if (should_stop or close_to_end) else 'generic'
                            client.challenges.decline(ch_id, reason=decline_reason)
                            print(f"❌ Reddedildi: {ch_id} — {reason}", flush=True)
                            if should_stop and len(active_games) == 0: os._exit(0)
                    except Exception as ce:
                        print(f"⚠️ Meydan okuma işlenirken hata (Muhtemelen karşı taraf iptal etti): {ce}", flush=True)

                elif event['type'] == 'gameStart':
                    game_id = event['game']['id']
                    if game_id not in active_games:
                        active_games.add(game_id)
                        threading.Thread(
                            target=handle_game_wrapper,
                            args=(client, game_id, bot, my_id, active_games, mm),
                            daemon=True
                        ).start()

        except Exception as e:
            print(f"⚠️ Akış koptu: {e}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
