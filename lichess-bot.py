import os
import sys

# 1. ADIM: Python'a "önce kendi klasörüne bak" emrini veriyoruz (BU SATIR KRİTİK)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 2. ADIM: Şimdi diğer kütüphaneleri ve kendi modülümüzü çağırabiliriz
import berserk
import chess
import chess.engine
import time
import chess.polyglot
import threading
import yaml
from matchmaking import Matchmaker # Artık hata vermeyecek

# --- AYARLAR ---
TOKEN = os.environ.get('LICHESS_TOKEN')
print(f"Token sistemden okundu mu?: {'EVET' if TOKEN else 'HAYIR'}")
if TOKEN:
    print(f"Token uzunluğu: {len(TOKEN)} karakter")
# Derlediğiniz ve "ready" cevabını aldığımız EXE'nin tam yolu
EXE_PATH = "./src/Ethereal"

class OxydanAegisV8:
    def __init__(self, exe_path, uci_options=None):
        self.exe_path = exe_path
        # Kitabı BURADA bir kez açıyoruz, her hamlede değil!
        self.book_path = "./M11.2.bin"
        
        try:
            # Motoru 15 saniye sınırıyla başlat
            self.engine = chess.engine.SimpleEngine.popen_uci(self.exe_path, timeout=15)
            
            if uci_options:
                for name, value in uci_options.items():
                    self.engine.configure({name: value})
                    print(f"Ayar: {name} -> {value}", flush=True) # flush önemli!
            
            print("C++ Oxydan Core Bağlandı.", flush=True)
        except Exception as e:
            print(f"KRİTİK: Motor Başlatılamadı: {e}", flush=True)
            sys.exit(1)

    def get_best_move(self, board, opponent_rating=2500, my_time=180000, increment=0):
        # 1. KİTAP KONTROLÜ (Hızlı ve Güvenli)
        if os.path.exists(self.book_path):
            try:
                with chess.polyglot.open_reader(self.book_path) as reader:
                    entry = reader.get(board)
                    if entry:
                        print(f"Kitap Hamlesi: {entry.move}", flush=True)
                        return entry.move
            except: pass

        # 2. MOTOR HESAPLAMA
        try:
            # Süre None gelirse 60 saniye varsay
            t = my_time if my_time else 60000 
            
            # Dinamik limit belirleme
            if opponent_rating >= 2750:
                limit = chess.engine.Limit(time=max(2.0, (t/1000)*0.05), depth=24)
            else:
                limit = chess.engine.Limit(time=0.5, depth=18)

            # Motorun takılmasını önlemek için play fonksiyonuna da timeout ekle
            result = self.engine.play(board, limit, timeout=limit.time + 5.0)
            return result.move
        except Exception as e:
            print(f"Motor Hatası: {e}", flush=True)
            return list(board.legal_moves)[0]
            
    def get_best_move(self, board, opponent_rating=2500, my_time=180000, increment=0):
        # 1. ÖNCE KİTABA BAK
        book_path = "./M11.2.bin"
        if os.path.exists(book_path):
            try:
                with chess.polyglot.open_reader(book_path) as reader:
                    entry = reader.get(board)
                    if entry:
                        print(f"Kitap Hamlesi: {entry.move}")
                        return entry.move
            except Exception as e:
                pass # Hata mesajını temizledik ki loglar şişmesin

        # 2. DİNAMİK ZAMAN VE DERİNLİK HESABI (2800+ STRATEJİSİ)
        try:
            # Temel süre: Eğer rakip 2750+ ise daha çok düşün
            if opponent_rating >= 2750:
                # Kalan sürenin %5'ini kullan veya en az 2 saniye düşün
                # my_time milisaniye cinsinden gelir, saniyeye çeviriyoruz
                calc_time = max(2.0, ((my_time / 1000) * 0.05) + (increment / 1000))
                limit = chess.engine.Limit(time=calc_time, depth=24)
                print(f"!!! KRİTİK RAKİP ({opponent_rating}): {calc_time:.2f}sn / 24 Derinlik")
            elif opponent_rating >= 2500:
                limit = chess.engine.Limit(time=1.0, depth=20)
            else:
                limit = chess.engine.Limit(time=0.5, depth=16)

            result = self.engine.play(board, limit)
            return result.move
        except Exception as e:
            print(f"Motor hatası: {e}")
            return list(board.legal_moves)[0]

    def quit(self):
        self.engine.quit()

def handle_game(client, game_id, bot):
    try:
        my_id = client.account.get()['id']
        opponent_rating = 2500 # Varsayılan
        is_white = True
        my_color = chess.WHITE

        # Tek bir stream üzerinden her şeyi halledelim
        for state in client.bots.stream_game_state(game_id):
            if state['type'] == 'gameFull':
                # Bilgileri sadece oyun başında bir kez ayarla
                is_white = state['white'].get('id') == my_id
                my_color = chess.WHITE if is_white else chess.BLACK
                opp_info = state['black'] if is_white else state['white']
                opponent_rating = opp_info.get('rating', 2500)
                curr_state = state['state']
            elif state['type'] == 'gameState':
                curr_state = state
            else:
                continue

            board = chess.Board()
            moves = curr_state.get('moves', "")
            if moves:
                for m in moves.split():
                    board.push_uci(m)

            if board.turn == my_color and not board.is_game_over():
                # --- SÜRE BİLGİSİNİ ÇEK ---
                # Lichess süreyi milisaniye (ms) olarak gönderir
                my_time = curr_state['wtime'] if is_white else curr_state['btime']
                my_inc = curr_state['winc'] if is_white else curr_state['binc']

                # Güncellenmiş fonksiyonu çağır
                move = bot.get_best_move(board, opponent_rating, my_time, my_inc)
                
                if move:
                    client.bots.make_move(game_id, move.uci())
                    print(f"Hamle: {move.uci()} (Kalan Süre: {my_time/1000:.1f}s)")

            if curr_state.get('status') in ['mate', 'resign', 'draw', 'outoftime']:
                break
    except Exception as e:
        print(f"Oyun hatası: {e}")

def main():
    # 1. AYARLARI YÜKLE
    try:
        with open("config.yml", "r") as f:
            config = yaml.safe_load(f)
    except Exception as e:
        print(f"HATA: config.yml okunamadı: {e}")
        return

    uci_settings = config.get('engine', {}).get('uci_options', {})
    bot = OxydanAegisV8(EXE_PATH, uci_options=uci_settings)
    session = berserk.TokenSession(TOKEN)
    client = berserk.Client(session=session)
    
    print("Oxydan v8.0 Hybrid (Python Wrapper + C++ Core) Başlatıldı.")

    # 2. MATCHMAKING (AVCI MODU) BAŞLATMA
    if config.get("matchmaking"):
        try:
            # Matchmaker'ı oluşturuyoruz
            # Not: matchmaking.py dosyanın içeriğine göre parametreler değişebilir
            # Standart yapıda (client, config) yeterlidir.
            mm = Matchmaker(client, config) 
            
            # Ayrı bir thread'de başlatıyoruz ki ana döngüyü (stream) kilitlemesin
            threading.Thread(target=mm.start, daemon=True).start()
            print("Matchmaking (Avcı Modu) Arka Planda Başlatıldı. Rakip aranıyor...")
        except Exception as e:
            print(f"Matchmaking başlatılamadı: {e}")

    print("Lichess üzerinde gelen maçlar da dinleniyor...")

    # 3. ANA DÖNGÜ (Gelen Meydan Okumalar)
    try:
        for event in client.bots.stream_incoming_events():
            if event['type'] == 'challenge':
                try:
                    client.challenges.accept(event['challenge']['id'])
                    print("Gelen meydan okuma kabul edildi!")
                except Exception as e:
                    print(f"Hata: Meydan okuma artık geçerli değil: {e}")
                    continue

            elif event['type'] == 'gameStart':
                handle_game(client, event['game']['id'], bot)

    except Exception as ana_hata:
        print(f"Ana döngüde beklenmedik hata: {ana_hata}")
    except KeyboardInterrupt:
        print("Bot kapatılıyor...")
        bot.quit()

if __name__ == "__main__":
    main()
