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

    def calculate_smart_time(self, t_ms, inc_ms, moves_made):
        """
        v3 Dinamik Zaman Yönetimi:
        Lichess'ten gelen süreyi (ms) ağ gecikmesini hesaba katarak kırpar.
        """
        if t_ms is None: return 1.0
        
        # Saniyeye çevir
        t = float(t_ms) / 1000.0 if isinstance(t_ms, (int, float, str)) else t_ms.total_seconds()
        inc = float(inc_ms) / 1000.0 if inc_ms else 0.0

        # --- GECİKME TELAFİSİ (Lag Compensation) ---
        # Her hamle için 100ms ağ gecikmesi + 50ms Python işlem yükü düşüyoruz.
        overhead = 0.150 
        
        # Eğer Bullet oynuyorsak (Süre < 120sn) daha agresif bir marj kullan
        is_bullet = t < 120
        margin = 0.85 if not is_bullet else 0.75 # Sürenin %75-85'ini kullan
        
        # Kalan süreden overhead'i düş ve marjla çarp
        usable_time = (t - overhead) * margin
        
        # Eğer çok az süremiz kaldıysa (panik modu) direkt 100ms içinde hamle yap
        if t < 2.0:
            return max(0.1, t - 0.2)
            
        return max(0.1, usable_time)

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
                moves_made = len(board.move_stack)
                limit = chess.engine.Limit(
                    white_clock=self.calculate_smart_time(wtime, winc, moves_made),
                    black_clock=self.calculate_smart_time(btime, binc, moves_made),
                    white_inc=float(winc)/1000.0 if winc else 0,
                    black_inc=float(binc)/1000.0 if binc else 0
                )
                result = self.engine.play(board, limit)
                return result.move
            except Exception as e:
                print(f"Motor Hatası: {e}", flush=True)
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
