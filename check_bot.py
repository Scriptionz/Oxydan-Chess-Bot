"""Oxydan pre-flight diagnostics.

Runs a single engine move to confirm:
  * ``config.yml`` is valid (via :mod:`config`)
  * The compiled engine binary exists
  * The engine produces a legal move on the starting position
  * The engine pool can be drained cleanly

Also exposes a back-compat alias so the workflow (which historically
imported ``OxydanV11``) still works.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time

import chess

import config as oxydan_config


def _load_bot_module():
    main_script = "lichess-bot.py"
    if not os.path.exists(main_script):
        print(f"❌ ERROR: {main_script} not found in {os.getcwd()}")
        sys.exit(1)
    spec = importlib.util.spec_from_file_location("lichess_bot_module", main_script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_diagnostic() -> int:
    print("🛠️ Oxydan v12 pre-flight diagnostics...")

    settings = oxydan_config.settings
    exe_path = settings.engine.binary_path

    if not os.path.exists(exe_path):
        print(f"❌ ERROR: Engine binary not found at {exe_path}")
        return 1

    try:
        lichess_bot_module = _load_bot_module()
    except Exception as exc:  # noqa: BLE001
        print(f"❌ ERROR: Could not import lichess-bot.py: {exc}")
        return 1

    # Accept either OxydanV12 (current) or OxydanV11 (legacy) class.
    BotClass = getattr(lichess_bot_module, "OxydanV12", None) or \
               getattr(lichess_bot_module, "OxydanV11", None)
    if BotClass is None:
        print("❌ ERROR: Neither OxydanV12 nor OxydanV11 class found in lichess-bot.py")
        return 1

    print("✅ Module loaded successfully.")

    try:
        bot = BotClass(exe_path, uci_options={"Hash": 16, "Threads": 1})
    except Exception as exc:  # noqa: BLE001
        print(f"❌ ERROR: Engine failed to start: {exc}")
        return 1

    board = chess.Board()

    # Disable fallback so a real engine failure surfaces clearly.
    bot.fallback_move = lambda b: None  # type: ignore[assignment]

    try:
        move = bot.get_best_move(board, 10000, 10000, 1000, 1000)
    except Exception as exc:  # noqa: BLE001
        print(f"❌ ERROR: get_best_move raised: {exc}")
        return 1

    if not move or move not in board.legal_moves:
        print("❌ ERROR: Engine did not produce a legal move (fallback was disabled).")
        return 1
    print(f"✅ Engine produced legal move: {move.uci()}")

    # Drain pool.
    closed = 0
    if hasattr(bot, "engine_pool") and bot.engine_pool is not None:
        while not bot.engine_pool.empty():
            try:
                eng = bot.engine_pool.get_nowait()
                eng.quit()
                closed += 1
            except Exception:  # noqa: BLE001
                break
    print(f"🧹 Closed {closed} engine process(es).")

    time.sleep(1)
    print("✅ Diagnostics passed. Ready for deployment.")
    return 0


if __name__ == "__main__":
    sys.exit(run_diagnostic())
