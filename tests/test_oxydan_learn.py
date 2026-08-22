"""Unit tests for ``oxydan_learn`` (the experimental opening learner)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chess  # noqa: E402

# Force a temporary data file so we don't pollute the real one.
_tmpfile = tempfile.NamedTemporaryFile(
    "w", suffix=".json", delete=False, encoding="utf-8"
)
_tmpfile.close()
os.environ.setdefault("OXYDAN_LEARN_DATA_FILE", _tmpfile.name)

import config  # noqa: E402
config.settings.oxydan_learn.data_file = _tmpfile.name
config.settings.oxydan_learn.enabled = True
config.settings.oxydan_learn.min_games_for_weighting = 2

import oxydan_learn  # noqa: E402


class OpeningKeyTest(unittest.TestCase):
    def test_start_position_key(self):
        b = chess.Board()
        self.assertEqual(oxydan_learn.learn.opening_key(b), "start")

    def test_after_moves(self):
        b = chess.Board()
        b.push(chess.Move.from_uci("e2e4"))
        b.push(chess.Move.from_uci("e7e5"))
        self.assertEqual(oxydan_learn.learn.opening_key(b), "e2e4_e7e5")


class WeightTest(unittest.TestCase):
    def setUp(self):
        oxydan_learn.learn._data.clear()

    def test_no_data_returns_one(self):
        self.assertEqual(oxydan_learn.learn.weight_for_key("nonexistent"), 1.0)

    def test_winning_opening_above_one(self):
        b = chess.Board()
        b.push(chess.Move.from_uci("e2e4"))
        b.push(chess.Move.from_uci("e7e5"))
        for _ in range(3):
            oxydan_learn.learn.record_result(b, "win")
        # 3 wins, 0 losses → weight > 1 (towards win_weight_multiplier=1.5)
        w = oxydan_learn.learn.weight_for_board(b)
        self.assertGreater(w, 1.0)
        self.assertLessEqual(w, 1.5)

    def test_losing_opening_below_one(self):
        b = chess.Board()
        b.push(chess.Move.from_uci("d2d4"))
        b.push(chess.Move.from_uci("d7d5"))
        for _ in range(3):
            oxydan_learn.learn.record_result(b, "loss")
        w = oxydan_learn.learn.weight_for_board(b)
        self.assertLess(w, 1.0)
        self.assertGreaterEqual(w, 0.3)

    def test_below_min_games_returns_one(self):
        b = chess.Board()
        b.push(chess.Move.from_uci("c2c4"))
        # Only 1 record — under min_games_for_weighting=2
        oxydan_learn.learn.record_result(b, "win")
        self.assertEqual(oxydan_learn.learn.weight_for_board(b), 1.0)


class PersistenceTest(unittest.TestCase):
    def test_save_and_reload(self):
        b = chess.Board()
        b.push(chess.Move.from_uci("g1f3"))
        for _ in range(2):
            oxydan_learn.learn.record_result(b, "draw")

        # Verify the file was written
        self.assertTrue(os.path.exists(_tmpfile.name))
        with open(_tmpfile.name, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertGreater(len(data), 0)


if __name__ == "__main__":
    unittest.main()
