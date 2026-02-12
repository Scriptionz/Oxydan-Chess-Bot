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

class OxydanAegisV3:
    def __init__(self, exe_path, uci_options=None):
        self.exe_path = exe_path
        self.book_path = "./M11.2.bin"
        self.lock = threading.Lock()
        try:
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
        if t is None: return 0.0
        if isinstance(t, timedelta): return t.total_seconds()
        try:
            val = float(t)
            return val / 1000.0 if val > 1000 else val
        except: return 0.0

    def calculate_smart_time(self, t_ms, inc_ms, board=None):
        t = self.to_seconds(t_ms)
        inc = self.to_seconds(inc_ms)
        
        # 1. KRİTİK EŞİK: 1 DAKİKA ALTI HIZLANMA
        # 1 dakikadan fazla süremiz varsa (Rapid/Blitz başları)
        if t > 60:
            # Çok daha derin düşünme: t / 15 (5 dakikada ~20 saniye hamle başına)
            base_alloc = (t / 15) + (inc * 0.7)
            min_think = 1.0 # En az 1 saniye düşünerek kaliteyi koru
        
        # 20 saniye ile 60 saniye arası (Vites yükseltme)
        elif t > 20:
            # t / 25 (1 dakikada ~2.4 saniye hamle başına)
            base_alloc = (t / 25) + (inc * 0.8)
            min_think = 0.5
            
        # 20 saniyenin altı (Panik modu / Pre-move hazırlığı)
        else:
            # t / 40 (Çok hızlı ama hala mantıklı)
            base_alloc = (t / 40) + inc
            min_think = 0.2

        # 2. ÜST SINIR (Tek hamlede batmamak için)
        # Hiçbir hamlede toplam sürenin %25'ini geçme
        max_think = t * 0.25 

        final_time = max(min_think, min(base_alloc, max_think))

        # 3. GÜVENLİK SİGORTASI (Lag ve bağlantı için)
        usable_total = max(0.05, t - 0.150)
        
        return min(final_time, usable_total)

    def get_best_move(self, board, wtime, btime, winc, binc):
        # --- 1. KİTAP KONTROLÜ (Hemen Oynasın) ---
        if os.path.exists(self.book_path):
            try:
                with chess.polyglot.open_reader(self.book_path) as reader:
                    entry = reader.get(board)
                    if entry: 
                        print(f"📖 Kitap Hamlesi: {entry.move}", flush=True)
                        return entry.move
            except: pass

        # --- 2. TABLEBASE KONTROLÜ (Hemen Oynasın) ---
        if len(board.piece_map()) <= 7:
            try:
                fen = board.fen().replace(" ", "_")
                # Timeout'u 0.3 saniyeye düşürdük ki hızlıca geçsin
                r = requests.get(f"https://tablebase.lichess.ovh/standard?fen={fen}", timeout=0.3)
                if r.status_code == 200:
                    data = r.json()
                    if "moves" in data and len(data["moves"]) > 0:
                        best_move_uci = data["moves"][0]["uci"]
                        print(f"☁️ Cloud Tablebase: {best_move_uci}", flush=True)
                        return chess.Move.from_uci(best_move_uci)
            except: pass

        # --- 3. MOTOR HESAPLAMA (Gelişmiş Zamanlama) ---
        with self.lock:
            try:
                my_time = wtime if board.turn == chess.WHITE else btime
                my_inc = winc if board.turn == chess.WHITE else binc
                
                think_time = self.calculate_smart_time(my_time, my_inc, board)
                
                # Motoru sadece süre ile kısıtlıyoruz ki o sürede en derine insun
                limit = chess.engine.Limit(time=think_time)
                
                result = self.engine.play(board, limit)
                return result.move
            except Exception as e:
                return next(iter(board.legal_moves)) if board.legal_moves else None
                
def handle_game(client, game_id, bot, my_id):
    try:
        stream = client.bots.stream_game_state(game_id)
        my_color = None

        for state in stream:
            if state['type'] == 'gameFull':
                my_color = chess.WHITE if state['white'].get('id') == my_id else chess.BLACK
                curr_state = state['state']
            elif state['type'] == 'gameState':
                curr_state = state
            else: continue

            board = chess.Board()
            moves = curr_state.get('moves', "")
            if moves:
                for m in moves.split(): board.push_uci(m)

            if curr_state.get('status') in ['mate', 'resign', 'draw', 'outoftime', 'aborted']:
                print(f"[{game_id}] Oyun bitti.", flush=True)
                break

            if board.turn == my_color and not board.is_game_over():
                # Hareket baslamadan önce log yazıyoruz ki nerede takıldığını görelim
                print(f"[{game_id}] Oxydan dusunuyor...", flush=True)
                
                move = bot.get_best_move(
                    board, 
                    curr_state.get('wtime'), curr_state.get('btime'),
                    curr_state.get('winc'), curr_state.get('binc')
                )
                
                if move:
                    # Hamleyi göndermek için 3 deneme hakkı veriyoruz
                    for attempt in range(3):
                        try:
                            client.bots.make_move(game_id, move.uci())
                            print(f"[{game_id}] Hamle yapildi: {move.uci()}", flush=True)
                            break # Başarılıysa döngüden çık
                        except Exception as e:
                            print(f"[{game_id}] Hamle deneme {attempt+1} hatasi: {e}", flush=True)
                            if attempt < 2:
                                time.sleep(0.5) # Yarım saniye bekle ve tekrar dene
                            else:
                                print(f"[{game_id}] Hamle gönderimi TAMAMEN BAŞARISIZ.")

    except Exception as e:
        if "404" not in str(e):
            print(f"Oyun Hatası ({game_id}): {e}", flush=True)

def main():
    # Botun tam başlangıç zamanını saniye olarak kaydet
    start_time = time.time()
    
    try:
        with open("config.yml", "r") as f:
            config = yaml.safe_load(f)
    except:
        print("HATA: config.yml bulunamadı.")
        return

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
    
    # --- ANA DÖNGÜ (ZAMAN KONTROLLÜ) ---
    while True:
        try:
            # Geçen süreyi hesapla (saniye cinsinden)
            elapsed = time.time() - start_time
            
            # 5 saat 55 dakika (21300 saniye) dolduysa botu tamamen kapat
            if elapsed > 21300:
                print("🛑 KRİTİK ZAMAN: 6 saat sınırına ulaşıldı. Güvenli kapatma yapılıyor.", flush=True)
                sys.exit(0)

            # Lichess'ten gelen event'leri dinle
            for event in client.bots.stream_incoming_events():
                # Her event geldiğinde süreyi tekrar kontrol et
                current_elapsed = time.time() - start_time
                
                # 5 saat 45 dakika (20700 saniye) dolduysa yeni maç ALMAYI DURDUR
                is_safe_to_start = current_elapsed < 20700

                if event['type'] == 'challenge':
                    challenger = event['challenge']['challenger']['id']
                    
                    if is_safe_to_start:
                        if recent_opponents.count(challenger) < 3:
                            client.challenges.accept(event['challenge']['id'])
                            recent_opponents.append(challenger)
                            if len(recent_opponents) > 10: recent_opponents.pop(0)
                    else:
                        print(f"⚠️ Yeni maç reddedildi: Kapanışa az kaldı (Elapsed: {int(current_elapsed)}s)")
                
                elif event['type'] == 'gameStart':
                    game_id = event['game']['id']
                    threading.Thread(target=handle_game, args=(client, game_id, bot, my_id)).start()
                
                # Eğer süre kritik sınırı geçtiyse stream'den çık (yeni event bekleme)
                if current_elapsed > 21300:
                    break

        except Exception as e:
            # Bağlantı koparsa veya hata olursa 5 saniye bekle ve devam et
            if "current_elapsed" in locals() and current_elapsed > 21300:
                sys.exit(0)
            time.sleep(5)

if __name__ == "__main__":
    main()
