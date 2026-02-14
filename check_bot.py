import chess
import os
import sys
import importlib.util
import time

def run_diagnostic():
    print("🛠️ Oxydan V7 Tactical Diagnostics...")
    
    # 1. Dosya Yollarını Tanımla
    main_script = "lichess-bot.py"
    exe_path = "./src/Ethereal"
    
    # --- v7: BAĞLANTI VE SİSTEM TEMİZLİĞİ ---
    try:
        import requests
        start_ping = time.time()
        # Lichess API durumunu kontrol et
        r = requests.get("https://lichess.org/api/status", timeout=5)
        latency = (time.time() - start_ping) * 1000
        print(f"🌐 Lichess Latency: {latency:.1f}ms")
    except Exception as e:
        print(f"⚠️ Network Warning: Lichess connection failed or slow: {e}")

    if os.name != 'nt': # Linux/Mac ise zombi süreçleri temizle
        os.system("pkill -f Ethereal > /dev/null 2>&1")
        print("🧹 Zombie processes cleared.")

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
        
        # Sınıfı modülün içinden al
        OxydanAegisClass = getattr(lichess_bot_module, "OxydanAegisV4")
        
        print("✅ Module loaded successfully.")

        # 4. Motor Havuzu Başlatma Testi
        bot = OxydanAegisClass(exe_path, uci_options={"Hash": 16, "Threads": 1})
        
        # v7 Testi: Rastgele bir konum yerine taktiksel bir konum test et
        test_fen = "r1bqk2r/pppp1ppp/2n2n2/4p3/1bB1P3/2N2N2/PPPP1PPP/R1BQK2R w KQkq - 0 1"
        board = chess.Board(test_fen)
        
        # 5. Hamle Üretme Testi
        print("♟️ Testing engine intelligence & pool management...")
        # game_id, board, wtime, btime, winc, binc
        move = bot.get_best_move("diag_test", board, 10000, 10000, 1000, 1000)
        
        if move and move in board.legal_moves:
            print(f"✅ SUCCESS: Engine produced legal move: {move.uci()}")
            
            # 6. HAVUZU GÜVENLİ BOŞALTMA
            print("🧹 Cleaning up engine pool processes...")
            closed_engines = 0
            while not bot.engine_pool.empty():
                try:
                    engine = bot.engine_pool.get_nowait()
                    engine.quit() 
                    closed_engines += 1
                except Exception as e:
                    print(f"⚠️ Motor kapatma hatası: {e}")
                finally:
                    bot.engine_pool.task_done()

            time.sleep(1) 
            print(f"✅ {closed_engines} motor başarıyla temizlendi.")
            print("✅ Diagnostics passed. Ready for Oxydan V7 deployment.")
            
            os._exit(0) 
        else:
            print("❌ ERROR: Engine failed to produce a valid move!")
            sys.exit(1)

    except Exception as e:
        print(f"❌ CRITICAL DIAGNOSTIC ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    run_diagnostic()
