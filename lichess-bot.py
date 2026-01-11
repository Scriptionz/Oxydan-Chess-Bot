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

# 1. ADIM: Python yolu ayarı
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# --- AYARLAR ---
TOKEN = os.environ.get('LICHESS_TOKEN')
EXE_PATH = "./src/Ethereal"

# Anti-Spam listesi
recent_opponents = []

class OxydanAegisV8:
    def __init__(self, exe_path, uci_options=None):
        self.exe_path = exe_path
        self.book_path = "./M11.2.bin"
        self.lock = threading.Lock()
        try:
            self.engine = chess.engine.SimpleEngine.popen_uci(self.exe_path, timeout=15)
            if uci_options:
                for option_name, option_value in uci_options.items():
                    try:
                        self.engine.configure({option_name: option_value})
                        print(f"Ayar Başarılı: {option_name} -> {option_value}", flush=True)
                    except Exception as e:
                        print(f"UYARI: Ayar uygulanamadı: {e}", flush=True)
            print("C++ Oxydan Core Bağlandı.", flush=True)
        except Exception as e:
            print(f"KRİTİK: Motor Başlatılamadı: {e}", flush=True)
            sys.exit(1)

    def to_sec(self, t):
        """Her türlü zaman verisini (timedelta, ms, s) güvenli bir float saniyeye çevirir."""
        if isinstance(t, timedelta):
            return t.total_seconds()
        try:
            return float(t) / 1000.0 if t is not None else 0.0
        except:
            return 0.0

    def get_best_move(self, board, wtime=180000, btime=180000, winc=0, binc=0):
        # 1. KİTAP KONTROLÜ
        if os.path.exists(self.book_path):
            try:
                with chess.polyglot.open_reader(self.book_path) as reader:
                    entry = reader.get(board)
                    if entry:
                        print(f"Kitap: {entry.move}", flush=True)
                        return entry.move
            except: pass

        # 2. MOTOR HESAPLAMA
        with self.lock:
            try:
                # Veri tipi hatasını önlemek için to_sec kullanımı zorunlu
                limit = chess.engine.Limit(
                    white_clock=self.to_sec(wtime),
                    black_clock=self.to_sec(btime),
                    white_inc=self.to_sec(winc),
                    black_inc=self.to_sec(binc)
                )
                result = self.engine.play(board, limit)
                return result.move
            except Exception as e:
                print(f"Motor Hatası: {e}", flush=True)
                return list(board.legal_moves)[0]

    def quit(self):
        with self.lock:
            self.engine.quit()

def handle_game(client, game_id, bot):
    try:
        my_id = client.account.get()['id']
        for state in client.bots.stream_game_state(game_id):
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

            if board.turn == my_color and not board.is_game_over():
                wtime = curr_state.get('wtime')
                btime = curr_state.get('btime')
                winc = curr_state.get('winc')
                binc = curr_state.get('binc')
                
                move = bot.get_best_move(board, wtime, btime, winc, binc)
                
                if move:
                    client.bots.make_move(game_id, move.uci())
                    try:
                        my_t = wtime if is_white else btime
                        t_disp = bot.to_sec(my_t)
                        print(f"Hamle: {move.uci()} (Kalan: {t_disp:.1f}s)", flush=True)
                    except: pass

            if curr_state.get('status') in ['mate', 'resign', 'draw', 'outoftime']:
                break
    except Exception as e:
        print(f"Oyun hatası: {e}", flush=True)

def main():
    try:
        with open("config.yml", "r") as f:
            config = yaml.safe_load(f)
    except Exception as e:
        print(f"HATA: config.yml: {e}", flush=True)
        return

    bot = OxydanAegisV8(EXE_PATH, uci_options=config.get('engine', {}).get('uci_options', {}))
    client = berserk.Client(session=berserk.TokenSession(TOKEN))
    
    print("Oxydan v8.0 Stabil Başlatıldı.", flush=True)

    if config.get("matchmaking"):
        mm = Matchmaker(client, config) 
        threading.Thread(target=mm.start, daemon=True).start()

    while True:
        try:
            for event in client.bots.stream_incoming_events():
                if event['type'] == 'challenge':
                    challenger_id = event['challenge']['challenger']['id']
                    # Anti-Spam: Son rakipleri kontrol et
                    if recent_opponents.count(challenger_id) >= 2:
                        client.challenges.decline(event['challenge']['id'])
                        continue
                    
                    try:
                        client.challenges.accept(event['challenge']['id'])
                        recent_opponents.append(challenger_id)
                        if len(recent_opponents) > 5: recent_opponents.pop(0)
                    except: continue

                elif event['type'] == 'gameStart':
                    threading.Thread(target=handle_game, args=(client, event['game']['id'], bot)).start()
        except Exception as e:
            print(f"Bağlantı Hatası: {e}. 60s bekleniyor...", flush=True)
            time.sleep(60)
        except KeyboardInterrupt:
            bot.quit()
            break

if __name__ == "__main__":
    main()
