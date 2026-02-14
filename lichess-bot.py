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
from datetime import timedelta
from matchmaking import Matchmaker

# --- AYARLAR ---
TOKEN = os.environ.get('LICHESS_TOKEN')
EXE_PATH = "./src/Ethereal"

class OxydanAegisV4:
    def __init__(self, exe_path, uci_options=None):
        self.exe_path = exe_path
        self.book_path = "./M11.2.bin"
        self.engine_pool = queue.Queue()
        self.game_histories = {} 

        try:
            cores = os.cpu_count() or 2
            for i in range(2):
                eng = chess.engine.SimpleEngine.popen_uci(self.exe_path, timeout=30)
                base_configs = {"Hash": 256, "Threads": max(1, cores // 2)}
                for opt, val in base_configs.items():
                    try: eng.configure({opt: val})
                    except: pass
                if uci_options:
                    for opt, val in uci_options.items():
                        if opt.lower() == "ponder": continue
                        try: eng.configure({opt: val})
                        except: pass
                self.engine_pool.put(eng)
            print(f"🚀 Oxydan v7: Motorlar başarıyla hazırlandı.", flush=True)
        except Exception as e:
            print(f"KRİTİK HATA: Motorlar başlatılamadı: {e}", flush=True)
            sys.exit(1)

    def to_seconds(self, t):
        if t is None: return 0.0
        if isinstance(t, timedelta): return t.total_seconds()
        try:
            val = float(t)
            return val / 1000.0 if val > 1000 else val
        except: return 0.0

    def calculate_smart_time(self, t, inc, board, last_eval=None):
        move_num = board.fullmove_number
        pieces = len(board.piece_map())
        if t < 2.0: return max(0.05, inc - 0.1 if inc > 0.1 else 0.05)
        mtg = 40 if move_num < 30 else (30 if pieces > 12 else 20)
        base_time = (t / mtg) + (inc * 0.7)
        multiplier = 1.0
        if last_eval:
            if last_eval < -1.5: multiplier = 1.4
            elif last_eval > 3.0: multiplier = 0.8
        legal_count = board.legal_moves.count()
        if legal_count == 1: return 0.1
        if legal_count > 35: multiplier *= 1.2
        target = base_time * multiplier
        return max(0.1, min(target, t * 0.10))

    def check_game_end_conditions(self, game_id, board):
        hist_data = self.game_histories.get(game_id, {})
        hist = hist_data.get("eval_history", [])
        if len(hist) < 6: return None
        last_evals = hist[-6:]
        if all(e < -10.0 for e in last_evals): return "resign"
        if len(board.piece_map()) <= 10 and all(abs(e) < 0.15 for e in last_evals): return "draw"
        return None

    def get_best_move(self, game_id, board, wtime, btime, winc, binc):
        if game_id not in self.game_histories:
            self.game_histories[game_id] = {"eval_history": [], "last_score": 0}

        if os.path.exists(self.book_path):
            try:
                with chess.polyglot.open_reader(self.book_path) as reader:
                    entry = reader.get(board)
                    if entry: return entry.move
            except: pass

        if len(board.piece_map()) <= 7:
            try:
                fen = board.fen().replace(" ", "_")
                r = requests.get(f"https://tablebase.lichess.ovh/standard?fen={fen}", timeout=0.3)
                if r.status_code == 200:
                    data = r.json()
                    if data.get("moves"): return chess.Move.from_uci(data["moves"][0]["uci"])
            except: pass

        engine = self.engine_pool.get()
        try:
            my_time = wtime if board.turn == chess.WHITE else btime
            my_inc = winc if board.turn == chess.WHITE else binc
            t_sec = self.to_seconds(my_time)
            i_sec = self.to_seconds(my_inc)
            last_score = self.game_histories[game_id]["last_score"]
            think_time = self.calculate_smart_time(t_sec, i_sec, board, last_score)
            result = engine.play(board, chess.engine.Limit(time=think_time))
            if result.info and "score" in result.info:
                score_val = result.info["score"].relative.score(mate_score=10000)
                if score_val is not None:
                    actual_score = score_val / 100.0
                    self.game_histories[game_id]["eval_history"].append(actual_score)
                    self.game_histories[game_id]["last_score"] = actual_score
            return result.move
        except Exception as e:
            print(f"Motor Hatası: {e}")
            return next(iter(board.legal_moves)) if board.legal_moves else None
        finally:
            self.engine_pool.put(engine)

def handle_game(client, game_id, bot, my_id):
    try:
        client.bots.post_message(game_id, "Oxydan Aegis v4: Intelligent Logic Engaged.")
        # OYUN STREAMI İÇİN EN GÜVENLİ YOL
        stream = client.bots.stream_game_state(game_id)
        my_color = None
        
        for state in stream:
            if 'error' in state: break
            
            # Her state geldiğinde board'u tazeleyelim (Abort engellemek için)
            board = chess.Board()
            if state['type'] == 'gameFull':
                my_color = chess.WHITE if state['white'].get('id') == my_id else chess.BLACK
                curr_state = state['state']
            elif state['type'] == 'gameState':
                curr_state = state
            else: continue

            moves_list = curr_state.get('moves', "").split()
            for m in moves_list: board.push_uci(m)

            if curr_state.get('status') in ['mate', 'resign', 'draw', 'outoftime', 'aborted']:
                if game_id in bot.game_histories: del bot.game_histories[game_id]
                break

            if board.turn == my_color and not board.is_game_over():
                decision = bot.check_game_end_conditions(game_id, board)
                if decision == "resign":
                    client.bots.resign_game(game_id)
                    break
                
                move = bot.get_best_move(game_id, board, curr_state.get('wtime'), curr_state.get('btime'), curr_state.get('winc'), curr_state.get('binc'))
                if move:
                    try: client.bots.make_move(game_id, move.uci())
                    except: pass
    except Exception as e:
        print(f"Oyun Hatası ({game_id}): {e}")

def handle_game_wrapper(client, game_id, bot, my_id, active_games):
    try: handle_game(client, game_id, bot, my_id)
    finally:
        active_games.discard(game_id)
        print(f"✅ [{game_id}] Bitti. Kalan: {len(active_games)}")

def main():
    start_time = time.time()
    try:
        with open("config.yml", "r") as f: config = yaml.safe_load(f)
    except: return

    # 404 FIX: Base URL'i açıkça belirtiyoruz
    session = berserk.TokenSession(TOKEN)
    client = berserk.Client(session=session, base_url="https://lichess.org")

    try:
        acc = client.account.get()
        my_id = acc['id']
        print(f"🚀 Oxydan v7 Aktif. Kullanıcı: {my_id} (Title: {acc.get('title', 'None')})")
    except Exception as e:
        print(f"❌ Kimlik Hatası: {e}")
        return

    bot = OxydanAegisV4(EXE_PATH, uci_options=config.get('engine', {}).get('uci_options', {}))
    active_games = set() 
    
    if config.get("matchmaking"):
        mm = Matchmaker(client, config, active_games) 
        threading.Thread(target=mm.start, daemon=True).start()

    # --- EN SAĞLAM EVENT DÖNGÜSÜ ---
    while True:
        try:
            # STOP kontrolü
            if os.path.exists("STOP") or (time.time() - start_time) > 20700:
                if not active_games: os._exit(0)

            # Stream'i her döngüde baştan başlatmak yerine bir kez açıyoruz
            # client.bots.stream_incoming_events() en garantisidir.
            for event in client.bots.stream_incoming_events():
                if event['type'] == 'challenge':
                    ch_id = event['challenge']['id']
                    if len(active_games) >= 2:
                        client.challenges.decline(ch_id, reason='later')
                    else:
                        client.challenges.accept(ch_id)

                elif event['type'] == 'gameStart':
                    g_id = event['game']['id']
                    if g_id not in active_games:
                        active_games.add(g_id)
                        threading.Thread(target=handle_game_wrapper, args=(client, g_id, bot, my_id, active_games), daemon=True).start()
        
        except Exception as e:
            # 404 veya Bağlantı hatası olursa 10 saniye bekle ve baştan dene
            wait = 10 if "404" in str(e) else 3
            print(f"⚠️ Bağlantı hatası ({wait}s sonra denenecek): {e}")
            time.sleep(wait)

if __name__ == "__main__":
    main()
