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
EXE_PATH = "./src/Ethereal" # Veya "./src/Ethereal"

class OxydanAegisV8:
    def __init__(self, exe_path, uci_options=None):
        self.exe_path = exe_path
        self.book_path = "./M11.2.bin"
        self.lock = threading.Lock()
        try:
            # Motorun yanıt vermesi için popen aşamasında timeout kullanıyoruz
            self.engine = chess.engine.SimpleEngine.popen_uci(self.exe_path, timeout=30)
            if uci_options:
                for option_name, option_value in uci_options.items():
                    try:
                        self.engine.configure({option_name: option_value})
                    except: pass
            print("C++ Motor Bağlantısı Stabil.", flush=True)
        except Exception as e:
            print(f"KRİTİK: Motor Başlatılamadı: {e}", flush=True)
            sys.exit(1)

    def parse_time(self, t):
        """Zaman verisini (timedelta, ms, s) güvenli bir saniyeye çevirir."""
        if t is None: return 10.0
        
        # Loglardaki timedelta hatasını çözen kısım
        if isinstance(t, timedelta):
            return t.total_seconds()
        
        try:
            val = float(t)
            # Milisaniye ise saniyeye çevir (Lichess 180000 gönderirse 180 yapar)
            return val / 1000.0 if val > 1000 else val
        except:
            return 10.0

    def get_best_move(self, board, wtime, btime, winc, binc):
        # 1. Kitap Kontrolü
        if os.path.exists(self.book_path):
            try:
                with chess.polyglot.open_reader(self.book_path) as reader:
                    entry = reader.get(board)
                    if entry: return entry.move
            except: pass

        # 2. Motor Hesaplama
        with self.lock:
            try:
                limit = chess.engine.Limit(
                    white_clock=self.parse_time(wtime),
                    black_clock=self.parse_time(btime),
                    white_inc=self.parse_time(winc),
                    black_inc=self.parse_time(binc)
                )
                
                # DÜZELTME: 'timeout' parametresini kaldırdık, versiyon hatasını önler.
                result = self.engine.play(board, limit)
                return result.move
            except Exception as e:
                print(f"Motor Hatası Detayı: {e}", flush=True)
                # Rastgele değil, yasal ilk hamleyi yap (Bağlantı kopmasın diye)
                return list(board.legal_moves)[0] if board.legal_moves else None

    def quit(self):
        with self.lock:
            try: self.engine.quit()
            except: pass

def handle_game(client, game_id, bot):
    try:
        # Oyunun gerçekten var olduğunu kontrol et (404 koruması)
        time.sleep(1.5) # Lichess'in oyunu oluşturması için kısa bir bekleme
        
        my_id = client.account.get()['id']
        stream = client.bots.stream_game_state(game_id)
        
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

            if curr_state.get('status') in ['mate', 'resign', 'draw', 'outoftime', 'aborted']:
                print(f"Oyun Bitti. Durum: {curr_state.get('status')}", flush=True)
                break

            if board.turn == my_color and not board.is_game_over():
                move = bot.get_best_move(
                    board, 
                    curr_state.get('wtime'), curr_state.get('btime'),
                    curr_state.get('winc'), curr_state.get('binc')
                )
                
                if move:
                    try:
                        client.bots.make_move(game_id, move.uci())
                    except Exception as move_err:
                        print(f"Hamle Hatası: {move_err}", flush=True)

    except Exception as e:
        if "404" not in str(e):
            print(f"Thread Hatası (GameID: {game_id}): {e}", flush=True)

def main():
    try:
        with open("config.yml", "r") as f:
            config = yaml.safe_load(f)
    except:
        print("HATA: config.yml bulunamadı.", flush=True)
        return

    bot = OxydanAegisV8(EXE_PATH, uci_options=config.get('engine', {}).get('uci_options', {}))
    client = berserk.Client(session=berserk.TokenSession(TOKEN))
    
    print("Oxydan v8.0 Stabil Başlatıldı.", flush=True)

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
                    # 404 Hatasını önlemek için thread başlatmadan önce minik bir bekleme
                    game_id = event['game']['id']
                    threading.Thread(target=handle_game, args=(client, game_id, bot)).start()
                    
        except Exception as e:
            if "404" not in str(e):
                print(f"Bağlantı Hatası: {e}", flush=True)
            time.sleep(5)

if __name__ == "__main__":
    main()
