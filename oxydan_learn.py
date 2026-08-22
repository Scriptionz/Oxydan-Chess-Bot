"""Oxydan Learn — experimental opening performance tracking.

The idea is simple but powerful:

* Every game the bot plays, we capture the first N moves (an "opening
  key") and the final result.
* The module persists a small JSON file (``oxydan_learn.json`` by
  default) so knowledge survives restarts.
* When the engine wrapper wants to pick a book move, it asks this
  module for a per-move weight: openings the bot wins with are boosted,
  openings it loses with are penalised. The book still drives the
  choice, but the bot's own history tilts the dice.

This is intentionally lightweight. It does NOT replace the opening
book, train a network, or modify Ethereal. It just makes Oxydan slightly
less predictable to opponents who prepare against it.

Disclaimer
----------
This is an experimental feature. Disable it via ``oxydan_learn.enabled:
false`` in ``config.yml`` if you want stable, book-driven behaviour.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import chess

from config import settings

LOG = logging.getLogger("oxydan.learn")


class OxydanLearn:
    """Tracks per-opening results and produces move weights."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: Dict[str, Dict[str, int]] = {
            # opening_key (e.g. "e2e4_e7e5_g1f3") -> {
            #   "wins": int, "losses": int, "draws": int, "samples": int
            # }
        }
        self._enabled: bool = bool(settings.oxydan_learn.enabled)
        self._data_file: str = settings.oxydan_learn.data_file
        self._min_games: int = max(1, int(settings.oxydan_learn.min_games_for_weighting))
        self._win_boost: float = float(settings.oxydan_learn.win_weight_multiplier)
        self._loss_penalty: float = float(settings.oxydan_learn.loss_weight_multiplier)
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _load(self) -> None:
        if not self._enabled:
            return
        if not os.path.exists(self._data_file):
            LOG.info("📚 Oxydan Learn: no data file yet, starting fresh.")
            return
        try:
            with open(self._data_file, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict):
                self._data = {
                    str(k): {
                        "wins":   int(v.get("wins", 0)),
                        "losses": int(v.get("losses", 0)),
                        "draws":  int(v.get("draws", 0)),
                        "samples": int(v.get("samples", 0)),
                    }
                    for k, v in raw.items()
                    if isinstance(v, dict)
                }
            LOG.info("📚 Oxydan Learn: loaded %d opening records.", len(self._data))
        except Exception as exc:
            LOG.warning("📚 Oxydan Learn: could not read data file (%s) — starting fresh.", exc)

    def _save_locked(self) -> None:
        if not self._enabled:
            return
        try:
            tmp = f"{self._data_file}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2, sort_keys=True)
            os.replace(tmp, self._data_file)
        except Exception as exc:
            LOG.warning("📚 Oxydan Learn: failed to persist data: %s", exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def opening_key(self, board: chess.Board, depth: int = 6) -> str:
        """Stable, low-cardinality key from the first ``depth`` half-moves."""
        moves: List[str] = []
        # Walk the move stack from the start; cheap on small depths.
        for mv in list(board.move_stack)[:depth]:
            moves.append(mv.uci())
        return "_".join(moves) if moves else "start"

    def record_result(self, board: chess.Board, result: str, depth: int = 6) -> None:
        """Record the outcome of a finished game for every prefix opening."""
        if not self._enabled:
            return
        if result not in ("win", "loss", "draw"):
            return
        keys = set()
        for k in range(1, min(depth, len(board.move_stack)) + 1):
            prefix = "_".join(mv.uci() for mv in board.move_stack[:k])
            keys.add(prefix)
        # Always record the canonical deeper key too.
        keys.add(self.opening_key(board, depth=depth))
        with self._lock:
            for k in keys:
                bucket = self._data.setdefault(
                    k, {"wins": 0, "losses": 0, "draws": 0, "samples": 0}
                )
                bucket["samples"] += 1
                bucket[result + ("es" if result == "loss" else "s")] = (
                    bucket.get(result + ("es" if result == "loss" else "s"), 0) + 1
                )
            self._save_locked()

    def weight_for_key(self, key: str) -> float:
        """Return a multiplicative weight to bias book selection.

        Returns 1.0 when there is not enough data, between
        ``loss_weight_multiplier`` and ``win_weight_multiplier`` otherwise.
        """
        if not self._enabled:
            return 1.0
        with self._lock:
            entry = self._data.get(key)
        if not entry or entry.get("samples", 0) < self._min_games:
            return 1.0
        wins   = entry.get("wins", 0)
        losses = entry.get("losses", 0)
        draws  = entry.get("draws", 0)
        n = max(1, wins + losses + draws)
        # Score between -1 (all losses) and +1 (all wins).
        score = (wins - losses) / n
        # Linear blend between the configured floor and ceiling.
        # score=+1 -> win_weight_multiplier; score=-1 -> loss_weight_multiplier.
        if score >= 0:
            return 1.0 + (self._win_boost - 1.0) * score
        return 1.0 + (1.0 - self._loss_penalty) * score  # negative factor

    def weight_for_board(self, board: chess.Board, depth: int = 6) -> float:
        """Convenience wrapper that builds the key from a live board."""
        return self.weight_for_key(self.opening_key(board, depth=depth))

    def rank_book_moves(
        self,
        board: chess.Board,
        entries,
    ) -> List[Tuple[chess.Move, float]]:
        """Score candidate book moves by their per-position history.

        ``entries`` is whatever ``chess.polyglot`` returns for
        ``reader.find_all(board)``. Each entry has a ``.move`` attribute.
        Returns a shuffled list of (move, weight) pairs, where higher
        weight means more likely to be played.
        """
        if not self._enabled or not entries:
            return [(e.move, 1.0) for e in entries]
        scored: List[Tuple[chess.Move, float]] = []
        for entry in entries:
            board.push(entry.move)
            try:
                key = self.opening_key(board, depth=6)
            finally:
                board.pop()
            scored.append((entry.move, self.weight_for_key(key)))
        return scored

    def summary(self) -> Dict[str, int]:
        with self._lock:
            return {
                "tracked_openings": len(self._data),
                "total_samples": sum(v.get("samples", 0) for v in self._data.values()),
            }


# Singleton — bot code uses this directly.
learn = OxydanLearn()
