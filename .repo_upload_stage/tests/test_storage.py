from __future__ import annotations

import sqlite3
import tempfile
from contextlib import closing
import unittest
from pathlib import Path
from unittest.mock import patch

from app.storage import ConversationStore


class ConversationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "amigo.sqlite3"
        self.store = ConversationStore(self.database)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_each_start_begins_a_fresh_conversation(self) -> None:
        self.store.add_message("user", "hello in first chat")
        first_id = self.store.current_conversation_id

        # Reopening the database models the next launch of Amigo.
        reopened = ConversationStore(self.database)
        self.assertNotEqual(first_id, reopened.current_conversation_id)
        self.assertEqual(reopened.recent_messages(), [])
        self.assertTrue(any(c["id"] == first_id for c in reopened.list_conversations()))

    def test_history_can_be_loaded_and_continued(self) -> None:
        first_id = self.store.current_conversation_id
        self.store.add_message("user", "open notepad")
        self.store.add_message("assistant", "Notepad is open")

        second_id = self.store.start_conversation()
        self.store.add_message("user", "another question")
        self.assertTrue(self.store.load_conversation(first_id))
        self.assertEqual(len(self.store.recent_messages()), 2)
        self.assertNotEqual(second_id, self.store.current_conversation_id)

    def test_migrates_legacy_single_history_without_losing_messages(self) -> None:
        legacy_database = Path(self.temp.name) / "legacy.sqlite3"
        with closing(sqlite3.connect(legacy_database)) as connection:
            with connection:
                connection.execute(
                    """CREATE TABLE messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )"""
                )
                connection.execute(
                    "INSERT INTO messages(role, content, created_at) VALUES (?, ?, ?)",
                    ("user", "my earlier prompt", "2026-01-01T00:00:00+00:00"),
                )
                connection.execute(
                    "INSERT INTO messages(role, content, created_at) VALUES (?, ?, ?)",
                    ("assistant", "earlier response", "2026-01-01T00:00:01+00:00"),
                )
        migrated = ConversationStore(legacy_database)
        self.assertEqual(migrated.recent_messages(), [])  # launch is a fresh chat
        legacy = next(c for c in migrated.list_conversations() if c["id"] == "legacy-history")
        self.assertEqual(legacy["title"], "Earlier chats")
        self.assertTrue(migrated.load_conversation("legacy-history"))
        self.assertEqual(migrated.recent_messages()[0]["content"], "my earlier prompt")

    def test_default_database_migration_copies_chat_history_and_keeps_old_database(self) -> None:
        legacy_database = Path(self.temp.name) / "old" / "amigo.sqlite3"
        new_database = Path(self.temp.name) / "new" / "amigo.sqlite3"
        legacy_database.parent.mkdir()
        with closing(sqlite3.connect(legacy_database)) as connection:
            with connection:
                connection.execute(
                    """CREATE TABLE conversations (
                        id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    )"""
                )
                connection.execute(
                    """CREATE TABLE messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL,
                        role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL
                    )"""
                )
                connection.execute(
                    "INSERT INTO conversations VALUES (?, ?, ?, ?)",
                    ("earlier", "Earlier", "2026-01-01", "2026-01-01"),
                )
                connection.execute(
                    "INSERT INTO messages(conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                    ("earlier", "user", "preserve this chat", "2026-01-01"),
                )
        with patch("app.storage.DATABASE_PATH", new_database), patch(
            "app.storage.LEGACY_DATABASE_PATH", legacy_database
        ):
            migrated = ConversationStore(new_database)
        self.assertTrue(new_database.exists())
        self.assertTrue(legacy_database.exists())
        self.assertTrue(migrated.load_conversation("earlier"))
        self.assertEqual(migrated.recent_messages()[0]["content"], "preserve this chat")


if __name__ == "__main__":
    unittest.main()
