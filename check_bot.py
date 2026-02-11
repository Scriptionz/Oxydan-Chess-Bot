import chess
import os
import sys
from main import OxydanAegisV3  # main.py dosyanın adıysa

def run_diagnostic():
    print("🛠️ Oxydan Pre-Flight Diagnostics...")
    
    exe_path = "./src/Ethereal"
    if not os.path.exists(exe_path):
        print("❌ ERROR: Engine binary not found!")
        sys.exit(1)

    try:
        # Motoru minimal ayarlarla başlat
        bot = OxydanAegisV3(exe_path, uci_options={"Hash": 16})
        board = chess.Board()
        
        # Test hamlesi üret (10 saniye süre varmış gibi)
        move = bot.get_best_move(board, 10000, 10000, 1000, 1000)
        
        if move and move in board.legal_moves:
            print(f"✅ SUCCESS: Engine produced legal move: {move.uci()}")
            sys.exit(0) # Başarılı çıkış
        else:
            print("❌ ERROR: Engine failed to produce a move!")
            sys.exit(1)
            
    except Exception as e:
        print(f"❌ CRITICAL FAILURE: {e}")
        sys.exit(1)

if __name__ == "__main__":
    run_diagnostic()
