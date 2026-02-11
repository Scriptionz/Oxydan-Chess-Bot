import chess
import os
import sys
import importlib.util

def run_diagnostic():
    print("🛠️ Oxydan Pre-Flight Diagnostics...")
    
    # 1. Dosya Yollarını Tanımla
    main_script = "lichess-bot.py"
    exe_path = "./src/Ethereal"
    
    # 2. Dosya Kontrolleri
    if not os.path.exists(main_script):
        print(f"❌ ERROR: {main_script} not found!")
        sys.exit(1)
        
    if not os.path.exists(exe_path):
        print(f"❌ ERROR: Engine binary not found at {exe_path}!")
        sys.exit(1)

    try:
        # 3. Dinamik Olarak 'lichess-bot.py' dosyasını içe aktar
        # (Dosya ismindeki '-' işareti yüzünden bu yöntem en güvenlisidir)
        spec = importlib.util.spec_from_file_location("lichess_bot_module", main_script)
        lichess_bot_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lichess_bot_module)
        
        # Sınıfı modülün içinden al
        OxydanAegisV3 = lichess_bot_module.OxydanAegisV3
        
        print("✅ Module loaded successfully.")

        # 4. Motoru Başlatma Testi
        bot = OxydanAegisV3(exe_path, uci_options={"Hash": 16})
        board = chess.Board()
        
        # 5. Hamle Üretme Testi (Zaman limitleriyle)
        print("♟️ Testing engine move generation...")
        move = bot.get_best_move(board, 10000, 10000, 1000, 1000)
        
        if move and move in board.legal_moves:
            print(f"✅ SUCCESS: Engine produced legal move: {move.uci()}")
            sys.exit(0) # Her şey yolunda, GitHub Actions devam edebilir.
        else:
            print("❌ ERROR: Engine failed to produce a valid move!")
            sys.exit(1)
            
    except Exception as e:
        print(f"❌ CRITICAL FAILURE during diagnostic: {e}")
        # Hata detayını göster ki debug yapabilelim
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    run_diagnostic()
