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

# Anti-Spam için son rakipleri tutan liste
recent_opponents = []

class OxydanAegisV8:
    def __init__(self, exe_path, uci_options=None):
        self.exe_path = exe_path
        self.book_path = "./M11.2.bin"
        self.lock = threading.Lock()
        try:
            # Motoru 15 saniye sınırıyla başlat
            self.engine = chess.engine.SimpleEngine.popen_uci(self.exe_path, timeout=15)
            
            if uci_options:
                for option_name, option_value in uci_options.items():
                    try:
                        self.engine.configure({option_name: option_value})
                        print(f"Ayar Başarılı: {option_name} -> {option_value}", flush=True)
                    except Exception as e:
                        print(f"UYARI: '{option_name}' ayarı uygulanamadı: {e}", flush=True)

            print("C++ Oxydan Core Bağlandı ve Özelleştirildi.", flush=True)
        except Exception as e:
            print(f"KRİTİK: Motor Başlatılamadı: {e}", flush=True)
            sys.exit(1)

    # ÖZELLİK SİLİNMEDİ: Parametreler motorun süreyi anlaması için genişletildi
    def get_best_move(self, board, wtime=180000, btime=180000, winc=0, binc=0):
        # 1. KİTAP KONTROLÜ (Aynen korundu)
        if os.path.exists(self.book_path):
            try:
                with chess.polyglot.open_reader(self.book_path) as reader:
                    entry = reader.get(board)
                    if entry:
                        print(f"Kitap Hamlesi: {entry.move}", flush=True)
                        return entry.move
            except: pass

        # 2. DİNAMİK SÜRE YÖNETİMİ
        # Burada 'opponent_rating'e göre sabit süre verme hatasını düzelttik.
        # Motor artık tahtadaki gerçek süreye (wtime/btime) bakarak Lynx gibi düşünür.
        with self.lock:
            try:
                # Lichess milisaniye gönderir, motor saniye bekler.
                limit = chess.engine.Limit(
                    wtime=wtime / 1000.0 if wtime else None,
                    btime=btime / 1000.0 if btime else None,
                    winc=winc / 1000.0 if winc else 0,
                    binc=binc / 1000.0 if binc else 0
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
                # Lichess'ten gelen güncel süreleri alıyoruz
                wtime = curr_state.get('wtime')
                btime = curr_state.get('btime')
                winc = curr_state.get('winc')
                binc = curr_state.get('binc')
                
                # Motoru çağırırken tüm süreleri gönderiyoruz
                move = bot.get_best_move(
                    board, 
                    wtime=wtime, 
                    btime=btime, 
                    winc=winc, 
                    binc=binc
                )
                
                if move:
                    client.bots.make_move(game_id, move.uci())
                    try:
                        my_time = wtime if is_white else btime
                        t_sec = my_time / 1000.0
                        print(f"Hamle: {move.uci()} (Kalan: {t_sec:.1f}s)", flush=True)
                    except:
                        print(f"Hamle: {move.uci()}", flush=True)

            if curr_state.get('status') in ['mate', 'resign', 'draw', 'outoftime']:
                break
    except Exception as e:
        print(f"Oyun hatası: {e}", flush=True)

def main():
    try:
        with open("config.yml", "r") as f:
            config = yaml.safe_load(f)
    except Exception as e:
        print(f"HATA: config.yml okunamadı: {e}", flush=True)
        return

    uci_settings = config.get('engine', {}).get('uci_options', {})
    bot = OxydanAegisV8(EXE_PATH, uci_options=uci_settings)
    session = berserk.TokenSession(TOKEN)
    client = berserk.Client(session=session)
    
    print("Oxydan v8.0 Hybrid Başlatıldı. 429 Koruması ve Anti-Spam Aktif.", flush=True)

    if config.get("matchmaking"):
        mm = Matchmaker(client, config) 
        threading.Thread(target=mm.start, daemon=True).start()
        print("Matchmaking Arka Planda Aktif.", flush=True)

    # ANA DÖNGÜ
    while True:
        try:
            for event in client.bots.stream_incoming_events():
                if event['type'] == 'challenge':
                    challenger_id = event['challenge']['challenger']['id']
                    
                    if recent_opponents.count(challenger_id) >= 2:
                        print(f"Reddedildi: {challenger_id} ile çok fazla oynandı.", flush=True)
                        try:
                            client.challenges.decline(event['challenge']['id'])
                        except: pass
                        continue

                    try:
                        client.challenges.accept(event['challenge']['id'])
                        print(f"Meydan okuma kabul edildi: {challenger_id}", flush=True)
                        
                        recent_opponents.append(challenger_id)
                        if len(recent_opponents) > 5:
                            recent_opponents.pop(0)
                    except: 
                        continue

                elif event['type'] == 'gameStart':
                    threading.Thread(target=handle_game, args=(client, event['game']['id'], bot)).start()

        except Exception as e:
            print(f"Bağlantı hatası (429 olabilir): {e}. 60 saniye sonra tekrar denenecek...", flush=True)
            time.sleep(60)
        except KeyboardInterrupt:
            bot.quit()
            break

if __name__ == "__main__":
    main()
