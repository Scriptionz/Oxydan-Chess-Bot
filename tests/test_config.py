"""Unit tests for the ``config`` module.

These tests don't need Lichess credentials. They verify that:
  * defaults are sane
  * config.yml is loaded when present
  * token placeholder resolution works
  * the dataclass back-compat properties return the right values
  * validation catches obvious mistakes
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402


class DefaultsTest(unittest.TestCase):
    def test_engine_binary_path(self):
        self.assertTrue(config.settings.engine.binary_path.endswith("Ethereal"))

    def test_max_parallel_games(self):
        self.assertGreaterEqual(config.settings.runtime.max_parallel_games, 1)

    def test_chess960_chance_in_range(self):
        self.assertTrue(0.0 <= config.settings.matchmaking.chess960_chance <= 1.0)

    def test_time_management_thresholds(self):
        tm = config.settings.time_management
        self.assertLess(tm.panic_threshold_s, tm.transition_threshold_s)
        self.assertGreater(tm.opening_time_fraction, 0.0)
        self.assertLessEqual(tm.opening_time_fraction, 1.0)


class TokenTest(unittest.TestCase):
    def test_no_token_returns_false(self):
        # Default config has the $LICHESS_TOKEN placeholder and no env var.
        if "LICHESS_TOKEN" not in os.environ:
            self.assertFalse(config.settings.has_token())

    def test_placeholder_resolution(self):
        # If the env var is set, has_token() should be True.
        os.environ["LICHESS_TOKEN"] = "lip_test"
        try:
            s = config.load_settings()
            self.assertTrue(s.has_token())
            self.assertEqual(s.token, "lip_test")
        finally:
            del os.environ["LICHESS_TOKEN"]


class BackCompatTest(unittest.TestCase):
    def test_legacy_properties(self):
        s = config.settings
        # These properties were hard-coded SETTINGS keys in the old code.
        self.assertEqual(s.latency_buffer, s.time_management.latency_buffer_ms / 1000.0)
        self.assertEqual(s.max_parallel_games, s.runtime.max_parallel_games)
        self.assertEqual(s.tablebase_piece_limit, s.tablebase.max_pieces)


class DeepMergeTest(unittest.TestCase):
    def test_partial_override(self):
        out = config._deep_merge(
            {"a": {"b": 1, "c": 2}, "d": 3},
            {"a": {"b": 99}},
        )
        self.assertEqual(out["a"]["b"], 99)
        self.assertEqual(out["a"]["c"], 2)
        self.assertEqual(out["d"], 3)

    def test_empty_overlay(self):
        out = config._deep_merge({"a": 1}, {})
        self.assertEqual(out["a"], 1)


class ValidationTest(unittest.TestCase):
    def test_invalid_chess960_chance(self):
        s = config.settings
        original = s.matchmaking.chess960_chance
        s.matchmaking.chess960_chance = 1.5
        try:
            with self.assertRaises(ValueError):
                config._validate(s)
        finally:
            s.matchmaking.chess960_chance = original


class LoadFromTempFileTest(unittest.TestCase):
    def test_loads_minimal_yaml(self):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".yml", delete=False, encoding="utf-8"
        ) as fh:
            fh.write("matchmaking:\n  max_games: 5\n")
            path = fh.name
        try:
            s = config.load_settings(path)
            self.assertEqual(s.matchmaking.max_games, 5)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
