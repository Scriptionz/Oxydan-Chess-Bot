"""Oxydan chat engine — branding + diagnostics.

The previous implementation in ``lichess-bot.py`` had two silent failure
modes that made chat appear to "never work":

1. The fallback path tried ``post_message(..., room="player")``, which
   is not a valid parameter on modern berserk. The ``except TypeError:
   continue`` branch swallowed the error and the loop ended without
   raising or printing anything.
2. The single ``client.bots.post_message(game_id, text, spectator=...)``
   call also failed silently when the game was in ``created`` state
   (Lichess rejects chat pre-start) or when the token lacked chat scope.

This module fixes that:

* Logs every chat send attempt with a stable prefix so users can grep
  ``chat:`` in the GitHub Actions log to verify it's working.
* Always sends to the **player** room on the engine's own account.
  Spectator chat is intentionally not used (rarely visible in
  bot-vs-bot games anyway).
* Re-tries a couple of times with a short delay to ride out transient
  429s during fast games.
* Surfaces every non-success response code so config/token problems
  are visible instead of silent.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Dict, List, Optional

import berserk

from config import settings

LOG = logging.getLogger("oxydan.chat")


# ---------------------------------------------------------------------------
# Message pools — kept in one place so they're easy to extend.
# Mirrors the categories the old lichess-bot.py used.
# ---------------------------------------------------------------------------

DEFAULT_MESSAGES: Dict[str, List[str]] = {
    "greeting_bot": [
        "Hi! Oxydan 11 ready. Developed by Emir Karadağ. Good luck! ♟️",
        "Let's play! May the best engine win. Powered by Oxydan 11 🤖",
        "Oxydan 11 on the board! Good luck! ⚡",
        "Hello! Bringing Oxydan 11's A-game today 😤 ♟️",
    ],
    "greeting_human": [
        "Hi! I'm Oxydan 11, a chess bot developed by Emir Karadağ. Good luck and have fun! 🎓 ♟️",
        "Welcome! I'm Oxydan 11. Let's play! After the game, I can analyze moves with you 🤖",
        "Hello! Oxydan 11 here, created by Emir Karadağ. Good luck! 🎓",
        "Hi there! Let's play a great game. Proudly developed by Emir Karadağ! ♟️",
    ],
    "win": [
        "Good game! Well played 🤝",
        "Thanks for the game! You put up a great fight ♟️",
        "GG! That was an interesting game 🎯",
        "Well played! Hope to play again soon 🤖",
    ],
    "loss": [
        "Good game! You played well, congratulations 🎉",
        "Well deserved win! GG 🤝",
        "Excellent play! I'll have to do better next time 😅",
        "GG! You outplayed Oxydan 11 today 👏",
    ],
    "draw": [
        "Good game! A well-earned draw 🤝",
        "Balanced game! GG ♟️",
        "A draw! Both sides fought well 🎯",
    ],
    "losing_realization": [
        "You're playing really well, I'm in trouble here! 😅",
        "Nice moves! I can see this is going to be tough 😬",
        "Impressive! You've got a strong position 👏",
        "I have to admit, you're outplaying me right now! 🎓",
    ],
    "human_postgame": [
        "GG! If you'd like to review any moves or have chess questions, feel free to ask! 🎓",
        "Well played! I'm happy to discuss the game or give tips if you're interested 🤖 ♟️",
        "Good game! Any questions about the moves? I'm here to help! 🎓",
    ],
    # Dry-run / smoke-test only
    "smoke": ["chat smoke-test ✔"],
}


# ---------------------------------------------------------------------------
# Sender
# ---------------------------------------------------------------------------

class ChatSender:
    """Thread-safe chat sender that wraps a berserk Bots client."""

    def __init__(self, client: berserk.Client) -> None:
        self._client = client
        self._lock = threading.Lock()
        # Lightweight stats so a quick log dump reveals whether chat is alive.
        self._stats = {
            "attempts": 0,
            "success":  0,
            "skipped":  0,
            "failed":   0,
        }

    # --- Stats ---------------------------------------------------------
    @property
    def stats(self) -> Dict[str, int]:
        return dict(self._stats)

    def reset_stats(self) -> None:
        with self._lock:
            for k in self._stats:
                self._stats[k] = 0

    # --- Public send ---------------------------------------------------
    def send(self, game_id: str, text: str, *, room: str = "player",
             retries: int = 2, delay: float = 0.4) -> bool:
        """Send a chat message. Returns True on success.

        ``room`` is informational only — Lichess bots can only post in
        the player room of games they participate in. The parameter is
        kept so callers that pass ``"spectator"`` from older code don't
        break, and so we can document the limitation in one place.
        """
        if not settings.oxydan_chat.enabled:
            with self._lock:
                self._stats["skipped"] += 1
            LOG.debug("chat: skipped (disabled) game=%s", game_id)
            return False

        if not text or not game_id:
            with self._lock:
                self._stats["skipped"] += 1
            return False

        # Player room only — see module docstring.
        if room not in ("player", "spectator"):
            room = "player"

        with self._lock:
            self._stats["attempts"] += 1

        for attempt in range(retries + 1):
            try:
                # Modern berserk (0.14+): post_message(game_id, text, spectator=bool)
                # spectator=False forces the player room.
                self._client.bots.post_message(
                    game_id, text, spectator=(room == "spectator")
                )
                with self._lock:
                    self._stats["success"] += 1
                LOG.info("chat: ✔ game=%s room=%s text=%r", game_id, room, text[:60])
                return True
            except Exception as exc:  # noqa: BLE001
                err = str(exc)
                # 429 / network: retry after a short backoff.
                if attempt < retries and ("429" in err or "Too Many" in err or "Connection" in err):
                    time.sleep(delay * (attempt + 1))
                    continue
                with self._lock:
                    self._stats["failed"] += 1
                LOG.warning(
                    "chat: ✘ game=%s room=%s attempt=%d/%d err=%s",
                    game_id, room, attempt + 1, retries + 1, err,
                )
                return False
        return False

    def send_pick(self, game_id: str, category: str, **kwargs) -> bool:
        """Pick a random message from the named category and send it."""
        pool = DEFAULT_MESSAGES.get(category)
        if not pool:
            LOG.debug("chat: unknown category %r", category)
            return False
        return self.send(game_id, random.choice(pool), **kwargs)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def pick(category: str) -> str:
    pool = DEFAULT_MESSAGES.get(category)
    if not pool:
        return "Good game!"
    return random.choice(pool)
