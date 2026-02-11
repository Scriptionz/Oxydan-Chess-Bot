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
EXE_PATH = "./src/Ethereal"  # OxydanServer.exe kullanıyorsan yolu güncelle!

class OxydanAegisV8:
    def __init__(self, exe_path, uci_options=None):
        self.exe_path = exe_path
        self.book_path = "./M11.2.bin"
        self.lock = threading.Lock()
        try:
            # Motorun yanıt vermesi için timeout süresini artırdık
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
                # Lichess milisaniye gönderir, SimpleEngine saniye bekler.
                # Değerleri 1000'e bölerek saniyeye çeviriyoruz.
                limit = chess.engine.Limit(
                    white_clock=float(wtime) / 1000.0 if wtime is not None else 10.0,
                    black_clock=float(btime) / 1000.0 if btime is not None else 10.0,
                    white_inc=float(winc) / 1000.0 if winc is not None else 0.0,
                    black_inc=float(binc) / 1000.0 if binc is not None else 0.0
                )
                
                # Timeout: Motor 30 saniye içinde yanıt vermezse Python çökmesin
                result = self.engine.play(board, limit, timeout=30)
                return result.move
            except Exception as e:
                print(f"Motor Hesaplama Hatası: {e}", flush=True)
                # Acil durum hamlesi: Rastgele yasal hamle yap (Aborted olmasın)
                return list(board.legal_moves)[0] if board.legal_moves else None

    def quit(self):
        with self.lock:
            try: self.engine.quit()
            except: pass

def handle_game(client, game_id, bot):
    try:
        my_id = client.account.get()['id']
        # Stream'i timeout ile başlatıyoruz
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

            # Oyun bitti mi kontrolü
            if curr_state.get('status') in ['mate', 'resign', 'draw', 'outoftime', 'aborted']:
                print(f"Oyun Bitti. Durum: {curr_state.get('status')}", flush=True)
                break

            # Hamle sırası bizde mi?
            if board.turn == my_color and not board.is_game_over():
                wtime = curr_state.get('wtime')
                btime = curr_state.get('btime')
                winc = curr_state.get('winc')
                binc = curr_state.get('binc')
                
                move = bot.get_best_move(board, wtime, btime, winc, binc)
                
                if move:
                    try:
                        client.bots.make_move(game_id, move.uci())
                    except Exception as move_err:
                        print(f"Hamle Gönderilemedi: {move_err}", flush=True)

    except Exception as e:
        print(f"Thread Hatası (GameID: {game_id}): {e}", flush=True)

def main():
    try:
        with open("config.yml", "r") as f:
            config = yaml.safe_load(f)
    except:
        print("HATA: config.yml bulunamadı.", flush=True)
        return

    bot = OxydanAegisV8(EXE_PATH, uci_options=config.get('engine', {}).get('uci_options', {}))
    
    # Oturumu daha dayanıklı hale getiriyoruz
    session = berserk.TokenSession(TOKEN)
    client = berserk.Client(session=session)
    
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
                    else:
                        client.challenges.decline(event['challenge']['id'])

                elif event['type'] == 'gameStart':
                    # Her oyun için ayrı thread
                    game_thread = threading.Thread(
                        target=handle_game, 
                        args=(client, event['game']['id'], bot)
                    )
                    game_thread.start()
                    
        except Exception as e:
            print(f"Bağlantı koptu, yeniden deneniyor: {e}", flush=True)
            time.sleep(10)

if __name__ == "__main__":
    main()
