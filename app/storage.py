from __future__ import annotations

import os
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import DATABASE_PATH, LEGACY_DATABASE_PATH, _is_reparse_point


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


DOMAIN_MIGRATIONS: dict[int, str] = {
    1: """
    CREATE TABLE goals (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL CHECK(length(trim(title)) > 0),
        description TEXT NOT NULL DEFAULT '',
        start_date TEXT NOT NULL,
        target_date TEXT,
        priority INTEGER NOT NULL DEFAULT 2 CHECK(priority BETWEEN 1 AND 3),
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'paused', 'completed')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX idx_goals_status_updated ON goals(status, updated_at DESC);
    CREATE UNIQUE INDEX idx_goals_title_nocase ON goals(title COLLATE NOCASE);

    CREATE TABLE goal_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        goal_id TEXT NOT NULL REFERENCES goals(id) ON DELETE RESTRICT,
        event_type TEXT NOT NULL,
        details TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_goal_events_goal_created ON goal_events(goal_id, created_at DESC);

    CREATE TABLE milestones (
        id TEXT PRIMARY KEY,
        goal_id TEXT NOT NULL REFERENCES goals(id) ON DELETE RESTRICT,
        title TEXT NOT NULL CHECK(length(trim(title)) > 0),
        target_date TEXT,
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'completed')),
        completed_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX idx_milestones_goal_status ON milestones(goal_id, status, created_at);

    CREATE TABLE activities (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL CHECK(length(trim(title)) > 0),
        occurred_at TEXT NOT NULL,
        duration_minutes INTEGER CHECK(duration_minutes IS NULL OR duration_minutes BETWEEN 1 AND 1440),
        status TEXT NOT NULL DEFAULT 'completed' CHECK(status IN ('completed', 'partial')),
        reflection TEXT NOT NULL DEFAULT '',
        origin TEXT NOT NULL CHECK(origin IN ('ui', 'assistant', 'voice')),
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_activities_occurred ON activities(occurred_at DESC, id);

    CREATE TABLE topics (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK(length(trim(name)) > 0),
        description TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );

    CREATE TABLE materials (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL CHECK(length(trim(title)) > 0),
        relative_path TEXT NOT NULL UNIQUE,
        media_type TEXT NOT NULL DEFAULT 'application/octet-stream',
        size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
        sha256 TEXT NOT NULL,
        imported_at TEXT NOT NULL
    );

    CREATE TABLE goal_topics (
        goal_id TEXT NOT NULL REFERENCES goals(id) ON DELETE RESTRICT,
        topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE RESTRICT,
        created_at TEXT NOT NULL,
        PRIMARY KEY(goal_id, topic_id)
    );
    CREATE INDEX idx_goal_topics_topic ON goal_topics(topic_id, goal_id);

    CREATE TABLE activity_goals (
        activity_id TEXT NOT NULL REFERENCES activities(id) ON DELETE RESTRICT,
        goal_id TEXT NOT NULL REFERENCES goals(id) ON DELETE RESTRICT,
        PRIMARY KEY(activity_id, goal_id)
    );
    CREATE INDEX idx_activity_goals_goal ON activity_goals(goal_id, activity_id);

    CREATE TABLE activity_topics (
        activity_id TEXT NOT NULL REFERENCES activities(id) ON DELETE RESTRICT,
        topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE RESTRICT,
        PRIMARY KEY(activity_id, topic_id)
    );
    CREATE INDEX idx_activity_topics_topic ON activity_topics(topic_id, activity_id);

    CREATE TABLE material_topics (
        material_id TEXT NOT NULL REFERENCES materials(id) ON DELETE RESTRICT,
        topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE RESTRICT,
        PRIMARY KEY(material_id, topic_id)
    );
    CREATE INDEX idx_material_topics_topic ON material_topics(topic_id, material_id);

    CREATE TABLE assistant_actions (
        id TEXT PRIMARY KEY,
        action_type TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        entity_id TEXT,
        arguments_json TEXT NOT NULL,
        outcome TEXT NOT NULL CHECK(outcome IN ('succeeded', 'failed', 'clarification')),
        result_text TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_assistant_actions_created ON assistant_actions(created_at DESC);
    """,
    2: """
    CREATE TABLE knowledge_notes (
        id TEXT PRIMARY KEY,
        topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE RESTRICT,
        title TEXT NOT NULL CHECK(length(trim(title)) > 0),
        relative_path TEXT NOT NULL UNIQUE,
        content_sha256 TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX idx_knowledge_notes_topic_updated ON knowledge_notes(topic_id, updated_at DESC);

    CREATE TABLE material_index_state (
        material_id TEXT PRIMARY KEY REFERENCES materials(id) ON DELETE CASCADE,
        status TEXT NOT NULL CHECK(status IN ('indexing', 'indexed', 'unsupported', 'failed')),
        source_sha256 TEXT NOT NULL DEFAULT '',
        extractor TEXT NOT NULL DEFAULT '',
        character_count INTEGER NOT NULL DEFAULT 0 CHECK(character_count >= 0),
        indexed_at TEXT,
        error_message TEXT NOT NULL DEFAULT ''
    );

    CREATE TABLE knowledge_chunks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        material_id TEXT REFERENCES materials(id) ON DELETE CASCADE,
        note_id TEXT REFERENCES knowledge_notes(id) ON DELETE CASCADE,
        topic_id TEXT REFERENCES topics(id) ON DELETE RESTRICT,
        part_no INTEGER NOT NULL CHECK(part_no >= 0),
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        source_path TEXT NOT NULL,
        source_locator TEXT NOT NULL DEFAULT '',
        CHECK ((material_id IS NULL) <> (note_id IS NULL)),
        UNIQUE(material_id, part_no),
        UNIQUE(note_id, part_no)
    );
    CREATE INDEX idx_knowledge_chunks_material ON knowledge_chunks(material_id, part_no);
    CREATE INDEX idx_knowledge_chunks_note ON knowledge_chunks(note_id, part_no);

    CREATE VIRTUAL TABLE knowledge_fts USING fts5(
        title, body,
        content='knowledge_chunks', content_rowid='id',
        tokenize='unicode61 remove_diacritics 2'
    );
    CREATE TRIGGER knowledge_chunks_ai AFTER INSERT ON knowledge_chunks BEGIN
        INSERT INTO knowledge_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
    END;
    CREATE TRIGGER knowledge_chunks_ad AFTER DELETE ON knowledge_chunks BEGIN
        INSERT INTO knowledge_fts(knowledge_fts, rowid, title, body)
        VALUES ('delete', old.id, old.title, old.body);
    END;
    CREATE TRIGGER knowledge_chunks_au AFTER UPDATE ON knowledge_chunks BEGIN
        INSERT INTO knowledge_fts(knowledge_fts, rowid, title, body)
        VALUES ('delete', old.id, old.title, old.body);
        INSERT INTO knowledge_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
    END;
    """,
    3: """
    CREATE TABLE planned_sessions (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL CHECK(length(trim(title)) > 0),
        description TEXT NOT NULL DEFAULT '',
        starts_at TEXT NOT NULL,
        ends_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'scheduled' CHECK(status IN ('scheduled', 'cancelled')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK(ends_at > starts_at)
    );
    CREATE INDEX idx_planned_sessions_status_start ON planned_sessions(status, starts_at, ends_at);

    CREATE TABLE planned_session_goals (
        session_id TEXT NOT NULL REFERENCES planned_sessions(id) ON DELETE RESTRICT,
        goal_id TEXT NOT NULL REFERENCES goals(id) ON DELETE RESTRICT,
        PRIMARY KEY(session_id, goal_id)
    );
    CREATE INDEX idx_planned_session_goals_goal ON planned_session_goals(goal_id, session_id);

    CREATE TABLE planned_session_topics (
        session_id TEXT NOT NULL REFERENCES planned_sessions(id) ON DELETE RESTRICT,
        topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE RESTRICT,
        PRIMARY KEY(session_id, topic_id)
    );
    CREATE INDEX idx_planned_session_topics_topic ON planned_session_topics(topic_id, session_id);

    CREATE TABLE calendar_events (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL CHECK(length(trim(title)) > 0),
        description TEXT NOT NULL DEFAULT '',
        starts_at TEXT NOT NULL,
        ends_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'scheduled' CHECK(status IN ('scheduled', 'cancelled')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK(ends_at > starts_at)
    );
    CREATE INDEX idx_calendar_events_status_start ON calendar_events(status, starts_at, ends_at);
    """,
    4: """
    CREATE TABLE daily_activity_items (
        id TEXT PRIMARY KEY,
        activity_date TEXT NOT NULL,
        title TEXT NOT NULL CHECK(length(trim(title)) > 0),
        estimated_minutes INTEGER CHECK(estimated_minutes IS NULL OR estimated_minutes BETWEEN 1 AND 1440),
        goal_id TEXT REFERENCES goals(id) ON DELETE RESTRICT,
        topic_id TEXT REFERENCES topics(id) ON DELETE RESTRICT,
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'completed')),
        completed_activity_id TEXT UNIQUE REFERENCES activities(id) ON DELETE RESTRICT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK((status = 'pending' AND completed_activity_id IS NULL) OR
              (status = 'completed' AND completed_activity_id IS NOT NULL))
    );
    CREATE INDEX idx_daily_activity_items_date_status ON daily_activity_items(activity_date, status, created_at);
    CREATE INDEX idx_daily_activity_items_goal_date ON daily_activity_items(goal_id, activity_date);
    """,
    5: """
    CREATE TABLE projects (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL CHECK(length(trim(title)) > 0),
        description TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL DEFAULT 'project' CHECK(kind IN ('project', 'study')),
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'paused', 'completed')),
        target_date TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX idx_projects_status_updated ON projects(status, updated_at DESC);
    CREATE TABLE project_goals (
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
        goal_id TEXT NOT NULL REFERENCES goals(id) ON DELETE RESTRICT,
        created_at TEXT NOT NULL,
        PRIMARY KEY(project_id, goal_id)
    );
    CREATE TABLE project_topics (
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
        topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE RESTRICT,
        created_at TEXT NOT NULL,
        PRIMARY KEY(project_id, topic_id)
    );
    CREATE TABLE project_materials (
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
        material_id TEXT NOT NULL REFERENCES materials(id) ON DELETE RESTRICT,
        created_at TEXT NOT NULL,
        PRIMARY KEY(project_id, material_id)
    );
    CREATE TABLE project_notes (
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
        note_id TEXT NOT NULL REFERENCES knowledge_notes(id) ON DELETE RESTRICT,
        created_at TEXT NOT NULL,
        PRIMARY KEY(project_id, note_id)
    );
    CREATE TABLE project_activities (
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
        activity_id TEXT NOT NULL REFERENCES activities(id) ON DELETE RESTRICT,
        created_at TEXT NOT NULL,
        PRIMARY KEY(project_id, activity_id)
    );
    ALTER TABLE daily_activity_items ADD COLUMN project_id TEXT REFERENCES projects(id) ON DELETE RESTRICT;
    CREATE INDEX idx_daily_activity_items_project_date ON daily_activity_items(project_id, activity_date);
    """,
    6: """
    CREATE TABLE topic_learning_state (
        topic_id TEXT PRIMARY KEY REFERENCES topics(id) ON DELETE RESTRICT,
        state TEXT NOT NULL DEFAULT 'introduced'
          CHECK(state IN ('not_encountered', 'introduced', 'learning', 'understood', 'applied', 'needs_revision')),
        rationale TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL
    );
    CREATE TABLE learning_evidence (
        id TEXT PRIMARY KEY,
        topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE RESTRICT,
        evidence_type TEXT NOT NULL CHECK(evidence_type IN ('state_change', 'quiz', 'activity', 'reflection', 'application')),
        summary TEXT NOT NULL,
        score REAL CHECK(score IS NULL OR (score >= 0 AND score <= 1)),
        source_id TEXT,
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_learning_evidence_topic_created ON learning_evidence(topic_id, created_at DESC);
    CREATE TABLE learning_quizzes (
        id TEXT PRIMARY KEY,
        topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE RESTRICT,
        title TEXT NOT NULL,
        questions_json TEXT NOT NULL,
        sources_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_learning_quizzes_topic_created ON learning_quizzes(topic_id, created_at DESC);
    CREATE TABLE learning_quiz_attempts (
        id TEXT PRIMARY KEY,
        quiz_id TEXT NOT NULL REFERENCES learning_quizzes(id) ON DELETE RESTRICT,
        score INTEGER NOT NULL CHECK(score >= 0),
        total INTEGER NOT NULL CHECK(total > 0),
        answers_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        CHECK(score <= total)
    );
    CREATE TABLE learning_recommendations (
        id TEXT PRIMARY KEY,
        topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE RESTRICT,
        recommendation_type TEXT NOT NULL,
        reason TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'accepted', 'postponed', 'dismissed')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX idx_learning_recommendations_status ON learning_recommendations(status, created_at DESC);
    """,
    7: """
    CREATE TABLE user_profile (
        id INTEGER PRIMARY KEY CHECK(id = 1),
        name TEXT NOT NULL DEFAULT '',
        bio TEXT NOT NULL DEFAULT '',
        current_context TEXT NOT NULL DEFAULT '',
        aspirations TEXT NOT NULL DEFAULT '',
        interests TEXT NOT NULL DEFAULT '',
        response_style TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL
    );
    CREATE TABLE personal_memories (
        id TEXT PRIMARY KEY,
        content TEXT NOT NULL CHECK(length(trim(content)) > 0),
        source TEXT NOT NULL DEFAULT 'user',
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'rejected')),
        created_at TEXT NOT NULL,
        approved_at TEXT
    );
    CREATE INDEX idx_personal_memories_status_created ON personal_memories(status, created_at DESC);
    CREATE TABLE saved_discoveries (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        topic_id TEXT REFERENCES topics(id) ON DELETE SET NULL,
        source_note_id TEXT REFERENCES knowledge_notes(id) ON DELETE SET NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_saved_discoveries_created ON saved_discoveries(created_at DESC);
    """,
    8: """
    CREATE TABLE focus_sessions (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        started_at TEXT NOT NULL,
        planned_minutes INTEGER NOT NULL CHECK(planned_minutes BETWEEN 1 AND 240),
        ended_at TEXT,
        status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running', 'completed', 'cancelled')),
        goal_id TEXT REFERENCES goals(id) ON DELETE RESTRICT,
        topic_id TEXT REFERENCES topics(id) ON DELETE RESTRICT,
        project_id TEXT REFERENCES projects(id) ON DELETE RESTRICT,
        activity_id TEXT REFERENCES activities(id) ON DELETE RESTRICT,
        created_at TEXT NOT NULL,
        CHECK((status = 'completed' AND activity_id IS NOT NULL AND ended_at IS NOT NULL) OR
              (status != 'completed' AND activity_id IS NULL))
    );
    CREATE INDEX idx_focus_sessions_started ON focus_sessions(started_at DESC);
    """
}


class SqliteDatabase:
    """Small connection-per-operation SQLite boundary with additive schema migrations."""

    def __init__(self, path: Path = DATABASE_PATH, *, copy_legacy: bool = True) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if copy_legacy and self._is_default_path() and not self.path.exists():
            self._copy_legacy_database()
        if self.path.exists() and (_is_reparse_point(self.path) or self.path.stat().st_nlink > 1):
            raise RuntimeError("Amigo's database cannot be a link, junction, or hard-linked file.")
        self.initialize()

    def _is_default_path(self) -> bool:
        return os.path.normcase(os.path.abspath(self.path)) == os.path.normcase(os.path.abspath(DATABASE_PATH))

    def _copy_legacy_database(self) -> None:
        legacy = Path(LEGACY_DATABASE_PATH)
        if not legacy.exists():
            return
        if _is_reparse_point(legacy) or legacy.stat().st_nlink > 1:
            raise RuntimeError("The existing Amigo database is linked; migration stopped without changing it.")
        source: sqlite3.Connection | None = None
        target: sqlite3.Connection | None = None
        try:
            source = sqlite3.connect(legacy, timeout=10)
            target = sqlite3.connect(self.path, timeout=10)
            source.backup(target)
        except sqlite3.Error as exc:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
            raise RuntimeError(f"Could not preserve the existing Amigo database: {exc}") from exc
        finally:
            if target is not None:
                target.close()
            if source is not None:
                source.close()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with closing(self.connect()) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(messages)")}
            if "conversation_id" not in columns:
                connection.execute("ALTER TABLE messages ADD COLUMN conversation_id TEXT")
                count = connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
                if count:
                    timestamp = _now()
                    connection.execute(
                        "INSERT OR IGNORE INTO conversations(id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                        ("legacy-history", "Earlier chats", timestamp, timestamp),
                    )
                    connection.execute(
                        "UPDATE messages SET conversation_id = ? WHERE conversation_id IS NULL",
                        ("legacy-history",),
                    )
                else:
                    connection.execute("UPDATE messages SET conversation_id = '' WHERE conversation_id IS NULL")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_conversation_id_id ON messages(conversation_id, id)"
            )
            legacy_rows = connection.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id IS NULL OR conversation_id = ''"
            ).fetchone()[0]
            if legacy_rows:
                timestamp = _now()
                connection.execute(
                    "INSERT OR IGNORE INTO conversations(id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    ("legacy-history", "Earlier chats", timestamp, timestamp),
                )
                connection.execute(
                    "UPDATE messages SET conversation_id = ? WHERE conversation_id IS NULL OR conversation_id = ''",
                    ("legacy-history",),
                )

            connection.commit()
            current_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            for version in sorted(DOMAIN_MIGRATIONS):
                if version <= current_version:
                    continue
                try:
                    connection.executescript(
                        f"BEGIN IMMEDIATE;\n{DOMAIN_MIGRATIONS[version]}\nPRAGMA user_version = {version};\nCOMMIT;"
                    )
                except sqlite3.Error:
                    connection.rollback()
                    raise


class ConversationStore:
    """Local SQLite chat history, separated into individual conversations."""

    def __init__(self, path: Path = DATABASE_PATH) -> None:
        self.database = SqliteDatabase(path)
        self.path = self.database.path
        self.current_conversation_id: str | None = None
        # A new session every time the app starts; old chats remain accessible.
        self.start_conversation()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with self.database.connection() as connection:
            yield connection

    def start_conversation(self, title: str = "New chat") -> str:
        conversation_id = str(uuid.uuid4())
        timestamp = _now()
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO conversations(id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (conversation_id, title, timestamp, timestamp),
            )
        self.current_conversation_id = conversation_id
        return conversation_id

    def load_conversation(self, conversation_id: str) -> bool:
        with self._connection() as connection:
            exists = connection.execute("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if not exists:
            return False
        self.current_conversation_id = conversation_id
        return True

    def add_message(self, role: str, content: str, conversation_id: str | None = None) -> None:
        if role not in {"user", "assistant"}:
            raise ValueError("Only user and assistant messages may be persisted.")
        selected_id = conversation_id or self.current_conversation_id
        if not selected_id:
            selected_id = self.start_conversation()
        timestamp = _now()
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO messages(conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (selected_id, role, content, timestamp),
            )
            if role == "user":
                normalized = " ".join(content.split())
                title = normalized[:52]
                if len(normalized) > 52:
                    title = title.rstrip() + "…"
                connection.execute(
                    "UPDATE conversations SET title = CASE WHEN title = 'New chat' THEN ? ELSE title END, updated_at = ? WHERE id = ?",
                    (title or "New chat", timestamp, selected_id),
                )
            else:
                connection.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (timestamp, selected_id))

    def recent_messages(self, limit: int = 30, conversation_id: str | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        selected_id = conversation_id or self.current_conversation_id
        if not selected_id:
            return []
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
                (selected_id, limit),
            ).fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]

    def list_conversations(self, limit: int = 30) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT c.id, c.title, c.created_at, c.updated_at, COUNT(m.id) AS message_count
                   FROM conversations c JOIN messages m ON m.conversation_id = c.id
                   GROUP BY c.id ORDER BY c.updated_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def clear(self) -> None:
        """Clear only the active conversation's messages, not older sessions."""
        if not self.current_conversation_id:
            return
        with self._connection() as connection:
            connection.execute("DELETE FROM messages WHERE conversation_id = ?", (self.current_conversation_id,))
            connection.execute(
                "UPDATE conversations SET title = 'New chat', updated_at = ? WHERE id = ?",
                (_now(), self.current_conversation_id),
            )
