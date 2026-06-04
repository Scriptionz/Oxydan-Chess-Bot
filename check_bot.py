import chess
import os
import sys
import importlib.util
import time

def run_diagnostic():
    print("🛠️ Oxydan V11 Pre-Flight Diagnostics...")
    
    # 1. Dosya Yollarını Tanımla
    main_script = "lichess-bot.py"
    exe_path = "./src/Ethereal"
    
    # 2. Dosya Kontrolleri
    if not os.path.exists(main_script):
        print(f"❌ ERROR: {main_script} bulunamadı!")
        sys.exit(1)
        
    if not os.path.exists(exe_path):
        print(f"❌ ERROR: Motor dosyası (binary) {exe_path} konumunda yok!")
        sys.exit(1)

    try:
        # 3. Dinamik Olarak Modülü Yükle
        spec = importlib.util.spec_from_file_location("lichess_bot_module", main_script)
        lichess_bot_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lichess_bot_module)
        
        try:
            OxydanV11 = getattr(lichess_bot_module, "OxydanV11")
        except AttributeError:
            print("❌ ERROR: 'OxydanV11' sınıfı lichess-bot.py içinde bulunamadı!")
            sys.exit(1)
            
        print("✅ Module loaded successfully.")

        # 4. Motor Havuzu Başlatma Testi
        print("🤖 Initializing engine instance for testing...")
        bot = OxydanV11(exe_path, uci_options={"Hash": 16, "Threads": 1})
        board = chess.Board()
        
        # 5. Hamle Üretme Testi
        print("♟️ Testing pool-based engine move generation...")
        
        # 🛑 KRİTİK DÜZELTME: Fallback mekanizmasını test için devre dışı bırakıyoruz!
        # Eğer motor hata verir veya kilitlenirse, python yedek hamle üreticisi devreye giremeyecek, 
        # test doğrudan None alacak ve başarısız (fail) sayılacaktır.
        bot.fallback_move = lambda b: None
        
        move = bot.get_best_move(board, 10000, 10000, 1000, 1000)
        
        if move and move in board.legal_moves:
            print(f"✅ SUCCESS: Engine produced legal move: {move.uci()}")
            
            # --- HAVUZU GÜVENLİ BOŞALTMA ---
            print("🧹 Cleaning up engine pool processes...")
            closed_engines = 0
            if hasattr(bot, 'engine_pool') and bot.engine_pool is not None:
                while not bot.engine_pool.empty():
                    try:
                        engine = bot.engine_pool.get_nowait()
                        engine.quit() 
                        closed_engines += 1
                    except Exception as e:
                        print(f"⚠️ Bir motor kapatılırken hata oluştu: {e}")
                    finally:
                        if hasattr(bot.engine_pool, 'task_done'):
                            bot.engine_pool.task_done()
            else:
                try:
                    bot.quit()
                    closed_engines += 1
                except:
                    pass

            time.sleep(1) 
            print(f"✅ {closed_engines} motor başarıyla kapatıldı ve süreçler temizlendi.")
            print("✅ Diagnostics passed. Ready for deployment.")
            os._exit(0) 
        else:
            # 🚨 Motor çöktüğünde veya fallback devreye girmek zorunda kaldığında artık buraya düşecek:
            print("❌ ERROR: Engine FAILED to produce a valid move! Fallback mechanism was bypassed.")
            sys.exit(1)

    except Exception as e:
        print(f"❌ CRITICAL ERROR during diagnostics: {e}")
        sys.exit(1)

if __name__ == "__main__":
    run_diagnostic()
