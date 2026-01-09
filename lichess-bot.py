import berserk
import chess
import chess.engine
import os
import time
import chess.polyglot

# --- AYARLAR ---
TOKEN = os.environ.get('LICHESS_TOKEN')
print(f"Token sistemden okundu mu?: {'EVET' if TOKEN else 'HAYIR'}")
if TOKEN:
    print(f"Token uzunluğu: {len(TOKEN)} karakter")
# Derlediğiniz ve "ready" cevabını aldığımız EXE'nin tam yolu
EXE_PATH = "./src/Ethereal"

class OxydanAegisV8:
    def __init__(self, exe_path):
        self.exe_path = exe_path
        # C++ motorunu SimpleEngine ile başlatıyoruz (Senkron yapı)
        try:
            # Buradaki değişikliğe dikkat: transport değişkenini sildik
            self.engine = chess.engine.SimpleEngine.popen_uci(self.exe_path)
            print("C++ Oxydan Core Bağlandı: Bağlantı Sağlıklı.")
        except Exception as e:
            print(f"KRİTİK HATA: C++ motoru başlatılamadı! Yol: {exe_path}\nHata: {e}")
            
    def get_best_move(self, board):
        # 1. ÖNCE KİTABA BAK (Eski robotun efsane açılışları için)
        book_path = "./M11.2.bin" # Kitap yolun
        if os.path.exists(book_path):
            try:
                with chess.polyglot.open_reader(book_path) as reader:
                    entry = reader.get(board)
                    if entry:
                        print(f"Kitap Hamlesi: {entry.move}")
                        return entry.move
            except Exception as e:
                print(f"Kitap okunurken hata: {e}")

        # 2. KİTAPTA YOKSA C++ MOTORUNA SOR
        try:
            # Süreyi 0.500 yapalım ki C++ daha derin düşünebilsin
            result = self.engine.play(board, chess.engine.Limit(time=0.500))
            return result.move
        except Exception as e:
            print(f"Motor hatası: {e}. Yasal ilk hamle yapılıyor.")
            return list(board.legal_moves)[0]

    def quit(self):
        self.engine.quit()

def handle_game(client, game_id, bot):
    try:
        my_id = client.account.get()['id']
        client.bots.post_message(game_id, "Oxydan v8.0 C++ Core devrede. Hibrit zeka aktif.")
        
        for state in client.bots.stream_game_state(game_id):
            if state['type'] == 'gameFull':
                my_color = chess.WHITE if state['white'].get('id') == my_id else chess.BLACK
                curr_state = state['state']
            elif state['type'] == 'gameState':
                curr_state = state
            else:
                continue

            # Mevcut tahtayı güncelle
            board = chess.Board()
            moves = curr_state.get('moves', "")
            if moves:
                for m in moves.split():
                    board.push_uci(m)

            # Hamle sırası bizdeyse
            if board.turn == my_color and not board.is_game_over():
                print(f"Oxydan (C++) Düşünüyor... Derinlik aranıyor.")
                move = bot.get_best_move(board)
                if move:
                    client.bots.make_move(game_id, move.uci())
                    print(f"Hamle yapıldı: {move.uci()}")

            if curr_state.get('status') in ['mate', 'resign', 'draw', 'outoftime']:
                print("Oyun bitti.")
                break
    except Exception as e:
        print(f"Oyun sırasında hata: {e}")

def main():
    bot = OxydanAegisV8(EXE_PATH)
    session = berserk.TokenSession(TOKEN)
    client = berserk.Client(session=session)
    
    print("Oxydan v8.0 Hybrid (Python Wrapper + C++ Core) Başlatıldı.")
    print("Lichess üzerinde maç bekleniyor...")

    try:
        for event in client.bots.stream_incoming_events():
            if event['type'] == 'challenge':
                # Gelen her türlü meydan okumayı kabul et
                try:
                    client.challenges.accept(event['challenge']['id'])
                    print("Meydan okuma kabul edildi!")
                except Exception as e:
                    print(f"Hata: Meydan okuma artık geçerli değil (rakip iptal etmiş olabilir): {e}")
                    continue  # Sadece hata durumunda döngünün başına dön

            elif event['type'] == 'gameStart':
                handle_game(client, event['game']['id'], bot)

    except Exception as ana_hata:
        print(f"Ana döngüde beklenmedik hata: {ana_hata}")
    except KeyboardInterrupt:
        print("Bot kapatılıyor...")
        bot.quit()

if __name__ == "__main__":
    main()
