import os
import sys
import berserk
import chess
import chess.engine
import time
import chess.polyglot
import threading
import yaml
from datetime import timedelta
from matchmaking import Matchmaker

# --- AYARLAR ---
TOKEN = os.environ.get('LICHESS_TOKEN')
EXE_PATH = "./src/Ethereal" 

class OxydanAegisV3:
    def __init__(self, exe_path, uci_options=None):
        self.exe_path = exe_path
        self.book_path = "./M11.2.bin"
        self.lock = threading.Lock()
        try:
            # Motoru başlatırken popen seviyesinde timeout veriyoruz
            self.engine = chess.engine.SimpleEngine.popen_uci(self.exe_path, timeout=30)
            if uci_options:
                for opt, val in uci_options.items():
                    try: self.engine.configure({opt: val})
                    except: pass
            print(f"Oxydan v3 Core Aktif. Engine: {exe_path}", flush=True)
        except Exception as e:
            print(f"KRİTİK HATA: Motor başlatılamadı: {e}", flush=True)
            sys.exit(1)

    def to_seconds(self, t):
        """Her türlü Lichess zaman verisini (ms, s, timedelta) güvenli saniyeye çevirir."""
        if t is None: return 0.0
        # Eğer veri timedelta nesnesiyse direkt saniyeye çevir
        if isinstance(t, timedelta):
            return t.total_seconds()
        try:
            # Sayı veya string ise float yap ve 1000'den büyükse ms kabul et
            val = float(t)
            return val / 1000.0 if val > 1000 else val
        except:
            return 0.0

    def calculate_smart_time(self, t_ms, inc_ms):
        """
        Gecikme paylarını (lag) ve Python işlem süresini hesaba katar.
        """
        t = self.to_seconds(t_ms)
        
        # --- GECİKME TELAFİSİ ---
        # 150ms ağ ve işlemci yükü payı bırakıyoruz
        overhead = 0.150 
        
        # Bullet (1dk altı) için %75, diğerleri için %85 güvenli marj
        is_bullet = t < 60
        margin = 0.75 if is_bullet else 0.85 
        
        usable_time = (t - overhead) * margin
        
        # Kritik durumda (2sn altı) motora çok hızlı oynamasını söyle
        if t < 2.0:
            return max(0.05, t - 0.200)
            
        return max(0.05, usable_time)

    def get_best_move(self, board, wtime, btime, winc, binc):
        # 1. Kitap Kontrolü
        if os.path.exists(self.book_path):
            try:
                with chess.polyglot.open_reader(self.book_path) as reader:
                    entry = reader.get(board)
                    if entry: return entry.move
            except: pass

        # 2. Motor Hesaplama (Dinamik Limit)
        with self.lock:
            try:
                # v3.1: Tüm zaman parametreleri to_seconds süzgecinden geçer
                limit = chess.engine.Limit(
                    white_clock=self.calculate_smart_time(wtime, winc),
                    black_clock=self.calculate_smart_time(btime, binc),
                    white_inc=self.to_seconds(winc),
                    black_inc=self.to_seconds(binc)
                )
                result = self.engine.play(board, limit)
                return result.move
            except Exception as e:
                print(f"Motor Hatası Detayı: {e}", flush=True)
                # Acil durum hamlesi (zaman bitmesin diye)
                return list(board.legal_moves)[0] if board.legal_moves else None

def handle_game(client, game_id, bot, my_id):
    """v3: Her hamlede account.get() çağırmaz, my_id dışarıdan gelir."""
    try:
        # 1.5 saniye beklemek yerine stream'i hemen başlatıp kontrol ediyoruz
        stream = client.bots.stream_game_state(game_id)
        is_white = True
        my_color = chess.WHITE

        for state in stream:
            if state['type'] == 'gameFull':
                is_white = state['white'].get('id') == my_id
                my_color = chess.WHITE if is_white else chess.BLACK
                curr_state = state['state']
            elif state['type'] == 'gameState':
                curr_state = state
            else: continue

            board = chess.Board()
            moves = curr_state.get('moves', "")
            if moves:
                for m in moves.split(): board.push_uci(m)

            # Oyun bitti mi?
            if curr_state.get('status') in ['mate', 'resign', 'draw', 'outoftime', 'aborted']:
                break

            # Hamle sırası bizde mi?
            if board.turn == my_color and not board.is_game_over():
                # v3: Hamle yapmadan önce son bir kez sırayı kontrol et (Bad Request önleyici)
                move = bot.get_best_move(
                    board, 
                    curr_state.get('wtime'), curr_state.get('btime'),
                    curr_state.get('winc'), curr_state.get('binc')
                )
                
                if move:
                    try:
                        client.bots.make_move(game_id, move.uci())
                    except: pass 

    except Exception as e:
        if "404" not in str(e):
            print(f"Oyun Hatası ({game_id}): {e}", flush=True)

def main():
    try:
        with open("config.yml", "r") as f:
            config = yaml.safe_load(f)
    except:
        print("HATA: config.yml bulunamadı.")
        return

    # Bot ID'sini bir kere al ve sakla (Hız kazandırır)
    session = berserk.TokenSession(TOKEN)
    client = berserk.Client(session=session)
    try:
        my_id = client.account.get()['id']
    except Exception as e:
        print(f"Lichess bağlantısı kurulamadı: {e}")
        return

    bot = OxydanAegisV3(EXE_PATH, uci_options=config.get('engine', {}).get('uci_options', {}))
    print(f"Oxydan v3 Stabil Başlatıldı. ID: {my_id}", flush=True)

    if config.get("matchmaking"):
        mm = Matchmaker(client, config)
        threading.Thread(target=mm.start, daemon=True).start()

    recent_opponents = []
    
    while True:
        try:
            for event in client.bots.stream_incoming_events():
                if event['type'] == 'challenge':
                    challenger = event['challenge']['challenger']['id']
                    if recent_opponents.count(challenger) < 3:
                        client.challenges.accept(event['challenge']['id'])
                        recent_opponents.append(challenger)
                        if len(recent_opponents) > 10: recent_opponents.pop(0)
                
                elif event['type'] == 'gameStart':
                    game_id = event['game']['id']
                    # Thread'e my_id'yi de gönderiyoruz
                    threading.Thread(target=handle_game, args=(client, game_id, bot, my_id)).start()
                    
        except Exception as e:
            time.sleep(5)

if __name__ == "__main__":
    main()
