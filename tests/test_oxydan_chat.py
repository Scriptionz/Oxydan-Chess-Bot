"""Unit tests for ``oxydan_chat`` (the chat engine)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import oxydan_chat  # noqa: E402


class ChatSenderTest(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.sender = oxydan_chat.ChatSender(self.client)

    def test_send_success(self):
        ok = self.sender.send("abc123", "hello world")
        self.assertTrue(ok)
        self.client.bots.post_message.assert_called_once()
        # Default is player room
        _, kwargs = self.client.bots.post_message.call_args
        self.assertEqual(kwargs.get("spectator"), False)

    def test_send_spectator_room(self):
        ok = self.sender.send("abc123", "hello", room="spectator")
        self.assertTrue(ok)
        _, kwargs = self.client.bots.post_message.call_args
        self.assertEqual(kwargs.get("spectator"), True)

    def test_send_failure_increments_failed(self):
        self.client.bots.post_message.side_effect = RuntimeError("boom")
        ok = self.sender.send("abc123", "hi")
        self.assertFalse(ok)
        self.assertEqual(self.sender.stats["failed"], 1)
        self.assertEqual(self.sender.stats["success"], 0)

    def test_send_empty_text_skips(self):
        ok = self.sender.send("abc123", "")
        self.assertFalse(ok)
        self.client.bots.post_message.assert_not_called()

    def test_disabled_in_settings_skips(self):
        original = config.settings.oxydan_chat.enabled
        config.settings.oxydan_chat.enabled = False
        try:
            ok = self.sender.send("abc123", "hi")
            self.assertFalse(ok)
            self.client.bots.post_message.assert_not_called()
            self.assertEqual(self.sender.stats["skipped"], 1)
        finally:
            config.settings.oxydan_chat.enabled = original

    def test_pick_unknown_category(self):
        ok = self.sender.send_pick("abc123", "no_such_category")
        self.assertFalse(ok)
        self.client.bots.post_message.assert_not_called()


class PickTest(unittest.TestCase):
    def test_pick_known(self):
        msg = oxydan_chat.pick("greeting_human")
        self.assertIsInstance(msg, str)
        self.assertGreater(len(msg), 0)

    def test_pick_unknown_fallback(self):
        msg = oxydan_chat.pick("nope")
        self.assertEqual(msg, "Good game!")


if __name__ == "__main__":
    unittest.main()
