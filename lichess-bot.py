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
from datetime import timedelta
from matchmaking import Matchmaker

# --- AYARLAR ---
TOKEN = os.environ.get('LICHESS_TOKEN')
EXE_PATH = "./src/Ethereal"

import queue

class OxydanAegisV4:
    def __init__(self, exe_path, uci_options=None):
        self.exe_path = exe_path
        self.book_path = "./M11.2.bin"
        self.uci_options = uci_options
        # Motor Havuzu: Aynı anda 3 maçı yönetecek 3 ayrı motor örneği
        self.engine_pool = queue.Queue()
        
        try:
            for i in range(2):
                eng = chess.engine.SimpleEngine.popen_uci(self.exe_path, timeout=30)
                if uci_options:
                    for opt, val in uci_options.items():
                        try: eng.configure({opt: val})
                        except: pass
                self.engine_pool.put(eng)
            print(f"🚀 Oxydan v4 Aktif: 2 Bağımsız Motor Ünitesi Hazır.", flush=True)
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
        
        # --- 1. LYNX SAVUNMASI (Acil Durum Refleksi) ---
        # 3 saniyenin altında kalite yerine hıza odaklan (instamove etkisi)
        if t < 3.0:
            return 0.05 if t > 1.0 else 0.02

        # --- 2. TEMPOYA GÖRE MTG (Moves To Go) ---
        if t > 600: mtg = 45   # Classical (10 dk+)
        elif t > 180: mtg = 35 # Rapid/Blitz
        else: mtg = 25         # Blitz/Bullet
        
        # Oyunun sonuna doğru (60+ hamle) daha da hızlan
        if move_num > 60: mtg = max(15, mtg - 10)

        # --- 3. BÜTÇE VE KARMAŞIKLIK ---
        base_budget = (t / mtg) + (inc * 0.85)
        
        legal_moves = board.legal_moves.count()
        complexity = 1.3 if legal_moves > 40 else (0.7 if legal_moves < 15 else 1.0)
        
        target_time = base_budget * complexity

        # --- 4. GÜVENLİK SINIRLARI ---
        if t < 10.0:
            target_time = min(target_time, t / 45)
            min_think = 0.05
        else:
            min_think = 0.3 if t > 30 else 0.1

        max_limit = t * 0.15 # Tek hamlede bütçenin %15'inden fazlasını harcama
        
        final_time = max(min_think, min(target_time, max_limit))
        return max(0.01, final_time - 0.1) # 100ms ağ gecikme payı

    def get_best_move(self, board, wtime, btime, winc, binc):
        # 1. Kitap Kontrolü
        if os.path.exists(self.book_path):
            try:
                with chess.polyglot.open_reader(self.book_path) as reader:
                    entry = reader.get(board)
                    if entry: return entry.move
            except: pass

        # 2. Tablebase (7 taş ve altı)
        if len(board.piece_map()) <= 7:
            try:
                fen = board.fen().replace(" ", "_")
                r = requests.get(f"https://tablebase.lichess.ovh/standard?fen={fen}", timeout=0.3)
                if r.status_code == 200:
                    data = r.json()
                    if data.get("moves"): return chess.Move.from_uci(data["moves"][0]["uci"])
            except: pass

        # 3. Motor Hesaplama (Havuzdan motor çağırarak)
        engine = self.engine_pool.get() # Boştaki motoru al
        try:
            my_time = wtime if board.turn == chess.WHITE else btime
            my_inc = winc if board.turn == chess.WHITE else binc
            
            t_sec = self.to_seconds(my_time)
            i_sec = self.to_seconds(my_inc)
            
            think_time = self.calculate_smart_time(t_sec, i_sec, board)
            
            limit = chess.engine.Limit(time=think_time)
            result = engine.play(board, limit)
            return result.move
        except Exception as e:
            print(f"Motor Hatası: {e}")
            return next(iter(board.legal_moves)) if board.legal_moves else None
        finally:
            self.engine_pool.put(engine) # İş bitince motoru havuza iade et
                
def handle_game(client, game_id, bot, my_id):
    try:
        client.bots.post_message(game_id, "Hi! Oxydan v6 says Goodluck!")
        stream = client.bots.stream_game_state(game_id)
        my_color = None
        board = chess.Board() # Tahtayı döngü DIŞINDA oluşturuyoruz

        for state in stream:
            if state['type'] == 'gameFull':
                my_color = chess.WHITE if state['white'].get('id') == my_id else chess.BLACK
                curr_state = state['state']
                # Oyunun başındaki hamleleri bir kez yükle
                moves = curr_state.get('moves', "").split()
                board = chess.Board() # Sıfırla ve doldur
                for m in moves: board.push_uci(m)
                
            elif state['type'] == 'gameState':
                curr_state = state
                moves_list = curr_state.get('moves', "").split()
                if moves_list:
                    last_move = moves_list[-1]
                    # Eğer tahtadaki son hamle Lichess'ten gelenle aynı değilse ekle
                    if not board.move_stack or board.peek().uci() != last_move:
                        board.push_uci(last_move)
            else: 
                continue

            if curr_state.get('status') in ['mate', 'resign', 'draw', 'outoftime', 'aborted']:
                client.bots.post_message(game_id, "GG! See you later.")
                print(f"[{game_id}] Oyun bitti.", flush=True)
                break

            if board.turn == my_color and not board.is_game_over():
                # Hamle verilerini topla
                wtime = curr_state.get('wtime')
                btime = curr_state.get('btime')
                winc = curr_state.get('winc')
                binc = curr_state.get('binc')

                move = bot.get_best_move(board, wtime, btime, winc, binc)
                
                if move:
                    for attempt in range(3):
                        try:
                            client.bots.make_move(game_id, move.uci())
                            break 
                        except Exception as e:
                            print(f"[{game_id}] Deneme {attempt+1} hatası: {e}")
                            time.sleep(0.2)

    except Exception as e:
        if "404" not in str(e):
            print(f"Oyun Hatası ({game_id}): {e}", flush=True)

def main():
    # Botun tam başlangıç zamanını kaydet
    start_time = time.time()
    
    try:
        with open("config.yml", "r") as f:
            config = yaml.safe_load(f)
    except Exception as e:
        print(f"HATA: config.yml okunamadı: {e}")
        return

    session = berserk.TokenSession(TOKEN)
    client = berserk.Client(session=session)
    try:
        my_id = client.account.get()['id']
    except Exception as e:
        print(f"Lichess bağlantısı kurulamadı: {e}")
        return

    # Sınıf adını V4 olarak güncellediğini varsayıyorum (3 motorlu havuz yapısı)
    bot = OxydanAegisV4(EXE_PATH, uci_options=config.get('engine', {}).get('uci_options', {}))
    print(f"🚀 Oxydan v4 Stabil Başlatıldı. ID: {my_id}", flush=True)

    if config.get("matchmaking"):
        mm = Matchmaker(client, config)
        threading.Thread(target=mm.start, daemon=True).start()

    # --- YENİ: ÇOKLU OYUN TAKİBİ ---
    active_games = set()
    recent_opponents = []
    
    # --- ANA DÖNGÜ ---
    while True:
        try:
            elapsed = time.time() - start_time
            
            # 5 saat 55 dakika dolduysa tamamen kapat (Güvenli çıkış)
            if elapsed > 21300:
                print("🛑 KRİTİK ZAMAN: 6 saat sınırına ulaşıldı. Kapatılıyor.", flush=True)
                sys.exit(0)

            # Lichess event akışını dinle
            for event in client.bots.stream_incoming_events():
                current_elapsed = time.time() - start_time
                
                # Kapanışa yakın yeni maç almayı durdur (5s 45dk)
                is_time_safe = current_elapsed < 20700

                # 1. MEYDAN OKUMA KONTROLÜ (Challenge)
                if event['type'] == 'challenge':
                    challenge = event['challenge']
                    challenge_id = challenge['id']
                    
                    tc = challenge.get('timeControl', {})
                    limit = tc.get('limit', 0)
                    
                    current_elapsed = time.time() - start_time
                    is_long_request = limit >= 600  # 10 dk ve üzeri (Rapid/Klasik)
                    
                    # 1. KURAL: 5. saatten sonra (18000 sn) asla uzun maç kabul etme
                    if is_long_request and current_elapsed > 18000:
                        client.challenges.decline(challenge_id, reason='later')
                        print(f"🚫 5. saat doldu, uzun maç reddedildi: {challenge_id}")
                        continue

                    # 2. KURAL: Kapanışa 15 dk kala (20700 sn) hiçbir maçı kabul etme
                    if current_elapsed > 20700:
                        client.challenges.decline(challenge_id, reason='later')
                        continue

                    # 3. KURAL: Uzun maç slot kontrolü (Max 1 adet)
                    ongoing_games = client.games.get_ongoing()
                    long_game_count = sum(1 for g in ongoing_games if g['speed'] in ['rapid', 'classical'])

                    if is_long_request and long_game_count >= 1:
                        client.challenges.decline(challenge_id, reason='later')
                    elif len(active_games) < 2:
                        client.challenges.accept(challenge_id)
                    else:
                        client.challenges.decline(challenge_id, reason='later')

                # 2. MAÇ BAŞLAMA KONTROLÜ (Game Start)
                elif event['type'] == 'gameStart':
                    game_id = event['game']['id']
                    if game_id not in active_games:
                        active_games.add(game_id)
                        # Yeni maç için thread başlat
                        threading.Thread(
                            target=handle_game_wrapper, 
                            args=(client, game_id, bot, my_id, active_games),
                            daemon=True
                        ).start()
                
                # Zaman kontrolü (İç döngüden çıkış)
                if current_elapsed > 21300:
                    break

        except Exception as e:
            # Bağlantı koparsa veya Lichess timeout verirse 5 saniye bekle ve devam et
            print(f"⚠️ Ana döngüde hata oluştu, yeniden bağlanılıyor: {e}")
            time.sleep(5)

def handle_game_wrapper(client, game_id, bot, my_id, active_games):
    """Oyun bittiğinde active_games listesinden game_id'yi silen yardımcı fonksiyon."""
    try:
        handle_game(client, game_id, bot, my_id)
    except Exception as e:
        print(f"[{game_id}] handle_game hatası: {e}", flush=True)
    finally:
        active_games.discard(game_id)
        print(f"✅ [{game_id}] Slot boşaltıldı. Kalan aktif maç: {len(active_games)}", flush=True)

if __name__ == "__main__":
    main()
