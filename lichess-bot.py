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
        
        # 1. TAHTA ANALİZİ: Karmaşıklığı ölçüyoruz
        # Tahtada ne kadar az taş varsa (özellikle piyon oyun sonları), o kadar hızlı oynar.
        piece_count = len(board.piece_map()) if board else 32
        is_simple_endgame = piece_count <= 8 # 8 taş ve altı çok basit konumdur
        
        # 2. DİNAMİK OVERHEAD (Gecikme Kalkanı)
        # 5 saniye altında risk alıp 80ms'ye düşüyoruz ki süre yetmeme hatası olmasın.
        overhead = 0.080 if t < 5.0 else 0.150 
        
        # 3. ZAMAN DİLİMİ VE MARJ AYARLARI
        if t < 5.0:
            # "PANİK MODU": Sadece hayatta kalmaya odaklan.
            margin = 0.95
            # Artış (increment) varsa sadece onun %70'ini kullan, ana süren artsın.
            base_alloc = (inc * 0.7) if inc > 0 else (t * 0.12)
        elif t < 10.0:
            # "TURBO MODU": Hızlı hamlelerle baskı kur.
            margin = 0.90
            base_alloc = (t * 0.08) + (inc * 0.6)
        elif is_simple_endgame:
            # "HIZLI OYUN SONU": Konum kolaysa Lynx gibi anında oyna.
            margin = 0.85
            base_alloc = (t * 0.03) + (inc * 0.4)
        else:
            # NORMAL OYUN: Stratejik derinliği koru.
            margin = 0.85
            base_alloc = (t / 25) + (inc * 0.5)

        usable_time = (t - overhead) * margin
        
        # Karmaşıklığa ve zamana göre en mantıklı süreyi seç
        final_time = min(usable_time, base_alloc)

        # 4. SON SANİYE SİGORTASI: 2 saniye altı "pre-move" hızı
        if t < 2.0: 
            return max(0.05, t - 0.25)
            
        return max(0.1, final_time)
        
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
                # Limitleri saniye cinsinden hesapla
                # board nesnesini buraya ekledik ki karmaşıklığı analiz edebilsin
                wc = self.calculate_smart_time(wtime, winc, board)
                bc = self.calculate_smart_time(btime, binc, board)
                
                wi = self.to_seconds(winc)
                bi = self.to_seconds(binc)

                limit = chess.engine.Limit(
                    white_clock=wc,
                    black_clock=bc,
                    white_inc=wi,
                    black_inc=bi
                )

                # Motor hesaplamayı başlatır
                result = self.engine.play(board, limit)
                return result.move
            except Exception as e:
                print(f"!!! MOTOR HATASI: {e} !!!", flush=True)
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
                    threading.Thread(target=handle_game, args=(client, game_id, bot, my_id)).start()
        except Exception as e:
            time.sleep(5)

if __name__ == "__main__":
    main()
