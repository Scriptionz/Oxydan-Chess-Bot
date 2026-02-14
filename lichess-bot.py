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

# ==========================================================
# ⚙️ MODÜLER AYARLAR PANELİ (Burayı Değiştirmeniz Yeterli)
# ==========================================================
SETTINGS = {
    "TOKEN": os.environ.get('LICHESS_TOKEN'),
    "ENGINE_PATH": "./src/Ethereal",
    "BOOK_PATH": "./M11.2.bin",
    
    # --- OYUN LİMİTLERİ ---
    "MAX_PARALLEL_GAMES": 2,      # Aynı anda oynanacak maç sayısı
    "MAX_TOTAL_RUNTIME": 21300,   # Toplam çalışma süresi (5 saat 55 dk)
    "STOP_ACCEPTING_MINS": 15,    # Kapanışa kaç dk kala yeni maç almasın?
    
    # --- MOTOR VE ZAMAN YÖNETİMİ ---
    "LATENCY_BUFFER": 0.15,       # Saniye cinsinden ağ gecikme payı (150ms)
    "TABLEBASE_PIECE_LIMIT": 6,   # Kaç taş kalınca tablebase'e sorsun? (6 güvenlidir)
    "MIN_THINK_TIME": 0.05,       # En az düşünme süresi
    
    # --- MESAJLAR ---
    "GREETING": "Oxydan Aegis v7 InDev Active. System stabilized.",
}
# ==========================================================

class OxydanAegisV4:
    def __init__(self, exe_path, uci_options=None):
        self.exe_path = exe_path
        self.book_path = SETTINGS["BOOK_PATH"]
        self.uci_options = uci_options
        self.engine_pool = queue.Queue()
        
        # Havuz Boyutu: Paralel maç sayısı + 1 (Yedek ünite)
        pool_size = SETTINGS["MAX_PARALLEL_GAMES"] + 1
        
        try:
            for i in range(pool_size):
                eng = chess.engine.SimpleEngine.popen_uci(self.exe_path, timeout=30)
                if uci_options:
                    for opt, val in uci_options.items():
                        try: eng.configure({opt: val})
                        except: pass
                self.engine_pool.put(eng)
            print(f"🚀 Oxydan v4.2: {pool_size} Motor Ünitesi Havuza Alındı.", flush=True)
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

    def calculate_smart_time(self, t, inc, board):
        move_num = board.fullmove_number if board else 1
        
        # 1. ACİL DURUM (3 saniye altı panik modu)
        if t < 3.0:
            return 0.05 if t > 1.0 else 0.02

        # 2. TEMPO ANALİZİ (MTG - Moves To Go)
        if t > 600: mtg = 45   # Classical
        elif t > 180: mtg = 35 # Rapid
        else: mtg = 25         # Blitz
        
        if move_num > 60: mtg = max(15, mtg - 10)

        # 3. BÜTÇE VE KARMAŞIKLIK
        base_budget = (t / mtg) + (inc * 0.85)
        legal_moves = board.legal_moves.count()
        complexity = 1.3 if legal_moves > 40 else (0.7 if legal_moves < 15 else 1.0)
        target_time = base_budget * complexity

        # 4. GÜVENLİK SINIRLARI
        if t < 10.0:
            target_time = min(target_time, t / 45)
            min_think = SETTINGS["MIN_THINK_TIME"]
        else:
            min_think = 0.3 if t > 30 else 0.1

        max_limit = t * 0.15 # Tek hamlede bütçenin %15'inden fazlasını harcama
        final_time = max(min_think, min(target_time, max_limit))
        
        return max(0.01, final_time - SETTINGS["LATENCY_BUFFER"])

    def get_best_move(self, board, wtime, btime, winc, binc):
        # 1. Kitap Kontrolü
        if os.path.exists(self.book_path):
            try:
                with chess.polyglot.open_reader(self.book_path) as reader:
                    entry = reader.get(board)
                    if entry: return entry.move
            except: pass

        # 2. Tablebase (Gereksiz API yükünü önlemek için limitli)
        if len(board.piece_map()) <= SETTINGS["TABLEBASE_PIECE_LIMIT"]:
            try:
                fen = board.fen().replace(" ", "_")
                r = requests.get(f"https://tablebase.lichess.ovh/standard?fen={fen}", timeout=0.5)
                if r.status_code == 200:
                    data = r.json()
                    if data.get("moves"): return chess.Move.from_uci(data["moves"][0]["uci"])
            except: pass

        # 3. Motor Hesaplama
        engine = self.engine_pool.get()
        try:
            my_time = wtime if board.turn == chess.WHITE else btime
            my_inc = winc if board.turn == chess.WHITE else binc
            t_sec = self.to_seconds(my_time)
            i_sec = self.to_seconds(my_inc)
            
            think_time = self.calculate_smart_time(t_sec, i_sec, board)
            result = engine.play(board, chess.engine.Limit(time=think_time))
            return result.move
        except Exception as e:
            print(f"Motor Hatası: {e}")
            return next(iter(board.legal_moves)) if board.legal_moves else None
        finally:
            self.engine_pool.put(engine)

def handle_game(client, game_id, bot, my_id):
    try:
        client.bots.post_message(game_id, SETTINGS["GREETING"])
        stream = client.bots.stream_game_state(game_id)
        my_color = None

        for state in stream:
            if 'error' in state: break

            if state['type'] == 'gameFull':
                my_color = chess.WHITE if state['white'].get('id') == my_id else chess.BLACK
                curr_state = state['state']
            elif state['type'] == 'gameState':
                curr_state = state
            else: continue

            moves = curr_state.get('moves', "").split()
            board = chess.Board()
            for m in moves: board.push_uci(m)

            if curr_state.get('status') in ['mate', 'resign', 'draw', 'outoftime', 'aborted', 'stalemate']:
                break

            if board.turn == my_color and not board.is_game_over():
                wtime, btime = curr_state.get('wtime'), curr_state.get('btime')
                winc, binc = curr_state.get('winc'), curr_state.get('binc')
                move = bot.get_best_move(board, wtime, btime, winc, binc)
                
                if move:
                    for attempt in range(3):
                        try:
                            client.bots.make_move(game_id, move.uci())
                            break 
                        except:
                            time.sleep((attempt + 1) * 1)
    except Exception as e:
        print(f"Oyun Hatası ({game_id}): {e}", flush=True)

def handle_game_wrapper(client, game_id, bot, my_id, active_games):
    try:
        handle_game(client, game_id, bot, my_id)
    finally:
        active_games.discard(game_id)
        print(f"✅ [{game_id}] Bitti. Kalan Slot: {len(active_games)}/{SETTINGS['MAX_PARALLEL_GAMES']}", flush=True)

def main():
    start_time = time.time()
    
    try:
        with open("config.yml", "r") as f:
            config = yaml.safe_load(f)
    except:
        print("HATA: config.yml okunamadı.")
        return

    session = berserk.TokenSession(SETTINGS["TOKEN"])
    client = berserk.Client(session=session)
    try:
        my_id = client.account.get()['id']
    except:
        print("Lichess bağlantısı kurulamadı.")
        return

    bot = OxydanAegisV4(SETTINGS["ENGINE_PATH"], uci_options=config.get('engine', {}).get('uci_options', {}))
    active_games = set() 

    if config.get("matchmaking"):
        mm = Matchmaker(client, config, active_games) 
        threading.Thread(target=mm.start, daemon=True).start()

    print(f"🔥 Oxydan Aegis Hazır. ID: {my_id} | Max Slot: {SETTINGS['MAX_PARALLEL_GAMES']}", flush=True)

    while True:
        try:
            stop_signal = os.path.exists("STOP.txt")
            elapsed = time.time() - start_time

            # Kritik zaman kontrolü
            if elapsed > SETTINGS["MAX_TOTAL_RUNTIME"]:
                print("🛑 Toplam süre doldu. Kapanıyor.")
                sys.exit(0)

            for event in client.bots.stream_incoming_events():
                # Stream içindeyken periyodik kontroller
                cur_elapsed = time.time() - start_time
                should_stop = os.path.exists("STOP.txt") or cur_elapsed > SETTINGS["MAX_TOTAL_RUNTIME"]
                
                # Yeni maç kabul etmeme sınırı (son 15 dk)
                close_to_end = cur_elapsed > (SETTINGS["MAX_TOTAL_RUNTIME"] - (SETTINGS["STOP_ACCEPTING_MINS"] * 60))

                if event['type'] == 'challenge':
                    ch_id = event['challenge']['id']
                    
                    if should_stop or close_to_end or len(active_games) >= SETTINGS["MAX_PARALLEL_GAMES"]:
                        client.challenges.decline(ch_id, reason='later')
                        if should_stop and len(active_games) == 0: sys.exit(0)
                    else:
                        client.challenges.accept(ch_id)

                elif event['type'] == 'gameStart':
                    game_id = event['game']['id']
                    if game_id not in active_games and len(active_games) < SETTINGS["MAX_PARALLEL_GAMES"]:
                        active_games.add(game_id)
                        threading.Thread(
                            target=handle_game_wrapper,
                            args=(client, game_id, bot, my_id, active_games),
                            daemon=True
                        ).start()

        except Exception as e:
            if "429" in str(e):
                print("🚨 Hız sınırı (429). Bekleniyor...")
                time.sleep(60)
            else:
                time.sleep(5)

if __name__ == "__main__":
    main()
