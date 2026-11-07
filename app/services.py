from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import WORKSPACE_DIR, _is_reparse_point
from .domain import (
    ActivityOrigin,
    ActivityStatus,
    AmigoServiceError,
    AmbiguousReferenceError,
    GoalProgress,
    GoalStatus,
    MaterialRecord,
    MilestoneStatus,
)
from .storage import SqliteDatabase, _now


def _text(value: str, label: str, *, maximum: int = 500) -> str:
    result = str(value or "").strip()
    if not result:
        raise AmigoServiceError(f"{label} is required.")
    if len(result) > maximum:
        raise AmigoServiceError(f"{label} must be {maximum} characters or fewer.")
    return result


def _optional_date(value: str | date | None, label: str) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise AmigoServiceError(f"{label} must use YYYY-MM-DD format.") from exc


def _database(database: SqliteDatabase | Path | None) -> SqliteDatabase:
    if isinstance(database, SqliteDatabase):
        return database
    return SqliteDatabase(database) if database is not None else SqliteDatabase()


class GoalService:
    def __init__(self, database: SqliteDatabase | Path | None = None) -> None:
        self.db = _database(database)

    def create_goal(
        self,
        title: str,
        description: str = "",
        *,
        start_date: str | date | None = None,
        target_date: str | date | None = None,
        priority: int = 2,
    ) -> dict[str, Any]:
        title = _text(title, "Goal name")
        description = str(description or "").strip()
        if len(description) > 4000:
            raise AmigoServiceError("Goal description must be 4,000 characters or fewer.")
        start = _optional_date(start_date, "Start date") or date.today().isoformat()
        target = _optional_date(target_date, "Target date")
        if target and target < start:
            raise AmigoServiceError("Target date cannot be earlier than the start date.")
        try:
            priority = int(priority)
        except (TypeError, ValueError) as exc:
            raise AmigoServiceError("Priority must be 1 (high), 2 (normal), or 3 (low).") from exc
        if priority not in {1, 2, 3}:
            raise AmigoServiceError("Priority must be 1 (high), 2 (normal), or 3 (low).")
        goal_id, timestamp = str(uuid.uuid4()), _now()
        with self.db.connection() as connection:
            duplicate = connection.execute(
                "SELECT 1 FROM goals WHERE title = ? COLLATE NOCASE", (title,)
            ).fetchone()
            if duplicate:
                raise AmigoServiceError("A goal with that name already exists. Choose a distinct goal name.")
            connection.execute(
                """INSERT INTO goals(id, title, description, start_date, target_date, priority, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
                (goal_id, title, description, start, target, priority, timestamp, timestamp),
            )
            connection.execute(
                "INSERT INTO goal_events(goal_id, event_type, details, created_at) VALUES (?, 'created', ?, ?)",
                (goal_id, title, timestamp),
            )
            return dict(connection.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone())

    def list_goals(self, status: str | None = None) -> list[dict[str, Any]]:
        if status is not None and status not in {item.value for item in GoalStatus}:
            raise AmigoServiceError("Goal status must be active, paused, or completed.")
        with self.db.connection() as connection:
            if status:
                rows = connection.execute(
                    "SELECT * FROM goals WHERE status = ? ORDER BY priority, target_date IS NULL, target_date, updated_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM goals ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 ELSE 2 END, priority, target_date IS NULL, target_date, updated_at DESC"
                ).fetchall()
        return [dict(row) for row in rows]

    def get_goal(self, goal_id: str) -> dict[str, Any] | None:
        with self.db.connection() as connection:
            row = connection.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
        return dict(row) if row else None

    def change_status(self, goal_id: str, status: str, details: str = "") -> dict[str, Any]:
        if status not in {item.value for item in GoalStatus}:
            raise AmigoServiceError("Goal status must be active, paused, or completed.")
        timestamp = _now()
        with self.db.connection() as connection:
            current = connection.execute("SELECT status FROM goals WHERE id = ?", (goal_id,)).fetchone()
            if not current:
                raise AmigoServiceError("That goal no longer exists.")
            old_status = current["status"]
            if old_status == GoalStatus.COMPLETED.value and status != old_status:
                raise AmigoServiceError("A completed goal cannot be reopened; create a new goal to continue the work.")
            if old_status != status:
                connection.execute("UPDATE goals SET status = ?, updated_at = ? WHERE id = ?", (status, timestamp, goal_id))
                connection.execute(
                    "INSERT INTO goal_events(goal_id, event_type, details, created_at) VALUES (?, 'status_changed', ?, ?)",
                    (goal_id, f"{old_status} → {status}" + (f" · {details.strip()}" if details.strip() else ""), timestamp),
                )
            row = connection.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
        return dict(row)

    def update_goal(self, goal_id: str, *, title: str, description: str, target_date: str | date | None, priority: int) -> dict[str, Any]:
        title = _text(title, "Goal name")
        description = str(description or "").strip()
        if len(description) > 4000:
            raise AmigoServiceError("Goal description must be 4,000 characters or fewer.")
        target = _optional_date(target_date, "Target date")
        try:
            priority = int(priority)
        except (TypeError, ValueError) as exc:
            raise AmigoServiceError("Priority must be 1 (high), 2 (normal), or 3 (low).") from exc
        if priority not in {1, 2, 3}:
            raise AmigoServiceError("Priority must be 1 (high), 2 (normal), or 3 (low).")
        with self.db.connection() as connection:
            existing = connection.execute("SELECT start_date FROM goals WHERE id = ?", (goal_id,)).fetchone()
            if not existing:
                raise AmigoServiceError("That goal no longer exists.")
            duplicate = connection.execute(
                "SELECT 1 FROM goals WHERE title = ? COLLATE NOCASE AND id <> ?", (title, goal_id)
            ).fetchone()
            if duplicate:
                raise AmigoServiceError("A goal with that name already exists. Choose a distinct goal name.")
            if target and target < existing["start_date"]:
                raise AmigoServiceError("Target date cannot be earlier than the start date.")
            timestamp = _now()
            connection.execute(
                "UPDATE goals SET title = ?, description = ?, target_date = ?, priority = ?, updated_at = ? WHERE id = ?",
                (title, description, target, priority, timestamp, goal_id),
            )
            connection.execute(
                "INSERT INTO goal_events(goal_id, event_type, details, created_at) VALUES (?, 'updated', ?, ?)",
                (goal_id, "Goal details updated", timestamp),
            )
            return dict(connection.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone())

    def list_milestones(self, goal_id: str) -> list[dict[str, Any]]:
        with self.db.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM milestones WHERE goal_id = ? ORDER BY status, target_date IS NULL, target_date, created_at",
                (goal_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_events(self, goal_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self.db.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM goal_events WHERE goal_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
                (goal_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_milestone(self, goal_id: str, title: str, target_date: str | date | None = None) -> dict[str, Any]:
        title = _text(title, "Milestone name")
        target = _optional_date(target_date, "Milestone target date")
        milestone_id, timestamp = str(uuid.uuid4()), _now()
        with self.db.connection() as connection:
            goal = connection.execute("SELECT start_date FROM goals WHERE id = ?", (goal_id,)).fetchone()
            if not goal:
                raise AmigoServiceError("That goal no longer exists.")
            if target and target < goal["start_date"]:
                raise AmigoServiceError("Milestone target date cannot be earlier than the goal start date.")
            connection.execute(
                "INSERT INTO milestones(id, goal_id, title, target_date, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                (milestone_id, goal_id, title, target, timestamp, timestamp),
            )
            connection.execute(
                "UPDATE goals SET updated_at = ? WHERE id = ?", (timestamp, goal_id)
            )
            connection.execute(
                "INSERT INTO goal_events(goal_id, event_type, details, created_at) VALUES (?, 'milestone_added', ?, ?)",
                (goal_id, title, timestamp),
            )
            return dict(connection.execute("SELECT * FROM milestones WHERE id = ?", (milestone_id,)).fetchone())

    def reschedule_milestone(self, milestone_id: str, target_date: str | date, *, reason: str = "User-approved replan") -> dict[str, Any]:
        target = _optional_date(target_date, "Milestone target date")
        if not target:
            raise AmigoServiceError("Choose a new target date.")
        timestamp = _now()
        with self.db.connection() as connection:
            row = connection.execute(
                """SELECT m.*,g.start_date,g.title AS goal_title FROM milestones m
                   JOIN goals g ON g.id=m.goal_id WHERE m.id=?""", (milestone_id,)
            ).fetchone()
            if not row:
                raise AmigoServiceError("That milestone no longer exists.")
            if row["status"] != MilestoneStatus.PENDING.value:
                raise AmigoServiceError("A completed milestone cannot be rescheduled.")
            if target < str(row["start_date"]):
                raise AmigoServiceError("Milestone target date cannot be earlier than the goal start date.")
            previous = str(row["target_date"] or "unscheduled")
            connection.execute("UPDATE milestones SET target_date=?,updated_at=? WHERE id=?", (target, timestamp, milestone_id))
            connection.execute("UPDATE goals SET updated_at=? WHERE id=?", (timestamp, row["goal_id"]))
            connection.execute(
                """INSERT INTO goal_events(goal_id,event_type,details,created_at)
                   VALUES(?,'milestone_rescheduled',?,?)""",
                (row["goal_id"], f"{row['title']}: {previous} → {target}. {str(reason).strip()}", timestamp),
            )
            return dict(connection.execute("SELECT * FROM milestones WHERE id=?", (milestone_id,)).fetchone())

    def complete_milestone(self, milestone_id: str) -> dict[str, Any]:
        timestamp = _now()
        with self.db.connection() as connection:
            row = connection.execute("SELECT * FROM milestones WHERE id = ?", (milestone_id,)).fetchone()
            if not row:
                raise AmigoServiceError("That milestone no longer exists.")
            if row["status"] != MilestoneStatus.COMPLETED.value:
                connection.execute(
                    "UPDATE milestones SET status = 'completed', completed_at = ?, updated_at = ? WHERE id = ?",
                    (timestamp, timestamp, milestone_id),
                )
                connection.execute("UPDATE goals SET updated_at = ? WHERE id = ?", (timestamp, row["goal_id"]))
                connection.execute(
                    "INSERT INTO goal_events(goal_id, event_type, details, created_at) VALUES (?, 'milestone_completed', ?, ?)",
                    (row["goal_id"], row["title"], timestamp),
                )
            return dict(connection.execute("SELECT * FROM milestones WHERE id = ?", (milestone_id,)).fetchone())

    def reopen_milestone(self, milestone_id: str) -> dict[str, Any]:
        """Return a completed milestone to pending while preserving its history."""
        timestamp = _now()
        with self.db.connection() as connection:
            row = connection.execute(
                """SELECT m.*, g.status AS goal_status FROM milestones m
                   JOIN goals g ON g.id = m.goal_id WHERE m.id = ?""",
                (milestone_id,),
            ).fetchone()
            if not row:
                raise AmigoServiceError("That milestone no longer exists.")
            if row["goal_status"] == GoalStatus.COMPLETED.value:
                raise AmigoServiceError("Resume the goal before reopening one of its milestones.")
            if row["status"] == MilestoneStatus.COMPLETED.value:
                connection.execute(
                    "UPDATE milestones SET status = 'pending', completed_at = NULL, updated_at = ? WHERE id = ?",
                    (timestamp, milestone_id),
                )
                connection.execute("UPDATE goals SET updated_at = ? WHERE id = ?", (timestamp, row["goal_id"]))
                connection.execute(
                    "INSERT INTO goal_events(goal_id, event_type, details, created_at) VALUES (?, 'milestone_reopened', ?, ?)",
                    (row["goal_id"], row["title"], timestamp),
                )
            return dict(connection.execute("SELECT * FROM milestones WHERE id = ?", (milestone_id,)).fetchone())


class ActivityService:
    def __init__(self, database: SqliteDatabase | Path | None = None) -> None:
        self.db = _database(database)

    def log_activity(
        self,
        title: str,
        *,
        duration_minutes: int | None = None,
        occurred_at: datetime | str | None = None,
        status: str = ActivityStatus.COMPLETED.value,
        reflection: str = "",
        goal_ids: Iterable[str] = (),
        goal_names: Iterable[str] = (),
        topic_names: Iterable[str] = (),
        project_ids: Iterable[str] = (),
        origin: str = ActivityOrigin.UI.value,
    ) -> dict[str, Any]:
        title = _text(title, "Activity name")
        if len(title) > 500:
            raise AmigoServiceError("Activity name must be 500 characters or fewer.")
        reflection = str(reflection or "").strip()
        if len(reflection) > 4000:
            raise AmigoServiceError("Takeaway must be 4,000 characters or fewer.")
        if status not in {item.value for item in ActivityStatus}:
            raise AmigoServiceError("Activity status must be completed or partial.")
        if origin not in {item.value for item in ActivityOrigin}:
            raise AmigoServiceError("Activity origin is invalid.")
        if duration_minutes is not None:
            try:
                duration_minutes = int(duration_minutes)
            except (TypeError, ValueError) as exc:
                raise AmigoServiceError("Duration must be a whole number of minutes.") from exc
            if not 1 <= duration_minutes <= 1440:
                raise AmigoServiceError("Duration must be between 1 and 1,440 minutes.")
        if occurred_at is None:
            occurred = _now()
        elif isinstance(occurred_at, datetime):
            occurred = occurred_at.astimezone(timezone.utc).isoformat() if occurred_at.tzinfo else occurred_at.replace(tzinfo=timezone.utc).isoformat()
        else:
            try:
                parsed = datetime.fromisoformat(str(occurred_at))
                occurred = (parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).isoformat()
            except ValueError as exc:
                raise AmigoServiceError("Activity date/time must be an ISO-formatted date or timestamp.") from exc

        clean_goal_names = [_text(value, "Goal name") for value in goal_names]
        clean_topic_names = [_text(value, "Topic name") for value in topic_names]
        clean_project_ids = list(dict.fromkeys(str(value) for value in project_ids))
        arguments = {
            "title": title,
            "duration_minutes": duration_minutes,
            "occurred_at": occurred,
            "status": status,
            "reflection": reflection,
            "goal_names": clean_goal_names,
            "topic_names": clean_topic_names,
            "project_ids": clean_project_ids,
        }
        activity_id, timestamp = str(uuid.uuid4()), _now()
        try:
            with self.db.connection() as connection:
                resolved_goal_ids = list(dict.fromkeys(str(value) for value in goal_ids))
                for goal_name in clean_goal_names:
                    matches = connection.execute(
                        "SELECT id, title, status, target_date FROM goals WHERE title = ? COLLATE NOCASE ORDER BY title",
                        (goal_name,),
                    ).fetchall()
                    if not matches:
                        candidates = connection.execute(
                            "SELECT id, title, status, target_date FROM goals ORDER BY title COLLATE NOCASE"
                        ).fetchall()
                        needle = goal_name.casefold()
                        matches = [row for row in candidates if needle in str(row["title"]).casefold()]
                    if not matches:
                        raise AmigoServiceError(f"I couldn't find a goal matching '{goal_name}'. Nothing was recorded.")
                    if len(matches) > 1:
                        labels = [
                            f"{row['title']} ({row['status']}{', target ' + str(row['target_date']) if row['target_date'] else ''})"
                            for row in matches
                        ]
                        raise AmbiguousReferenceError("goal", goal_name, labels)
                    resolved_goal_ids.append(str(matches[0]["id"]))
                for goal_id in resolved_goal_ids:
                    if not connection.execute("SELECT 1 FROM goals WHERE id = ?", (goal_id,)).fetchone():
                        raise AmigoServiceError("A selected goal no longer exists. Nothing was recorded.")

                resolved_topic_ids: list[str] = []
                for topic_name in clean_topic_names:
                    topic_id = self._get_or_create_topic(connection, topic_name)
                    resolved_topic_ids.append(topic_id)
                resolved_project_ids = clean_project_ids
                for project_id in resolved_project_ids:
                    if not connection.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone():
                        raise AmigoServiceError("A selected project no longer exists. Nothing was recorded.")

                connection.execute(
                    """INSERT INTO activities(id, title, occurred_at, duration_minutes, status, reflection, origin, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (activity_id, title, occurred, duration_minutes, status, reflection, origin, timestamp),
                )
                for goal_id in dict.fromkeys(resolved_goal_ids):
                    connection.execute("INSERT INTO activity_goals(activity_id, goal_id) VALUES (?, ?)", (activity_id, goal_id))
                    connection.execute("UPDATE goals SET updated_at = ? WHERE id = ?", (timestamp, goal_id))
                for topic_id in dict.fromkeys(resolved_topic_ids):
                    connection.execute("INSERT INTO activity_topics(activity_id, topic_id) VALUES (?, ?)", (activity_id, topic_id))
                    for goal_id in dict.fromkeys(resolved_goal_ids):
                        connection.execute(
                            "INSERT OR IGNORE INTO goal_topics(goal_id, topic_id, created_at) VALUES (?, ?, ?)",
                            (goal_id, topic_id, timestamp),
                        )
                    connection.execute(
                        """INSERT INTO learning_evidence(id,topic_id,evidence_type,summary,source_id,created_at)
                           VALUES (?,?,'activity',?,?,?)""",
                        (str(uuid.uuid4()), topic_id, f"Recorded activity: {title} ({status}).", activity_id, timestamp),
                    )
                    connection.execute(
                        """INSERT INTO topic_learning_state(topic_id,state,rationale,updated_at)
                           VALUES (?,'introduced','An actual activity was recorded for this topic.',?)
                           ON CONFLICT(topic_id) DO NOTHING""",
                        (topic_id, timestamp),
                    )
                for project_id in resolved_project_ids:
                    connection.execute(
                        "INSERT INTO project_activities(project_id,activity_id,created_at) VALUES (?,?,?)",
                        (project_id, activity_id, timestamp),
                    )
                if origin in {ActivityOrigin.ASSISTANT.value, ActivityOrigin.VOICE.value}:
                    connection.execute(
                        """INSERT INTO assistant_actions(id, action_type, entity_type, entity_id, arguments_json, outcome, result_text, created_at)
                           VALUES (?, 'log_activity', 'activity', ?, ?, 'succeeded', ?, ?)""",
                        (str(uuid.uuid4()), activity_id, json.dumps(arguments, ensure_ascii=False), f"Recorded activity: {title}", timestamp),
                    )
                result = connection.execute(
                    """SELECT a.*, GROUP_CONCAT(DISTINCT g.title) AS goal_names, GROUP_CONCAT(DISTINCT t.name) AS topic_names
                       FROM activities a
                       LEFT JOIN activity_goals ag ON ag.activity_id = a.id
                       LEFT JOIN goals g ON g.id = ag.goal_id
                       LEFT JOIN activity_topics at ON at.activity_id = a.id
                       LEFT JOIN topics t ON t.id = at.topic_id
                       WHERE a.id = ? GROUP BY a.id""",
                    (activity_id,),
                ).fetchone()
                return dict(result)
        except AmigoServiceError as exc:
            if origin in {ActivityOrigin.ASSISTANT.value, ActivityOrigin.VOICE.value}:
                self._record_failed_assistant_action(arguments, str(exc), "clarification" if isinstance(exc, AmbiguousReferenceError) else "failed")
            raise

    def _record_failed_assistant_action(self, arguments: dict[str, Any], message: str, outcome: str) -> None:
        timestamp = _now()
        with self.db.connection() as connection:
            connection.execute(
                """INSERT INTO assistant_actions(id, action_type, entity_type, entity_id, arguments_json, outcome, result_text, created_at)
                   VALUES (?, 'log_activity', 'activity', NULL, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), json.dumps(arguments, ensure_ascii=False), outcome, message, timestamp),
            )

    @staticmethod
    def _get_or_create_topic(connection, name: str) -> str:
        name = _text(name, "Topic name")
        if len(name) > 200:
            raise AmigoServiceError("Topic name must be 200 characters or fewer.")
        row = connection.execute("SELECT id FROM topics WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
        if row:
            return str(row["id"])
        topic_id = str(uuid.uuid4())
        connection.execute(
            "INSERT OR IGNORE INTO topics(id, name, created_at) VALUES (?, ?, ?)", (topic_id, name, _now())
        )
        row = connection.execute("SELECT id FROM topics WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
        return str(row["id"])

    def list_activities(self, *, goal_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self.db.connection() as connection:
            if goal_id:
                rows = connection.execute(
                    """SELECT a.*, GROUP_CONCAT(DISTINCT g.title) AS goal_names, GROUP_CONCAT(DISTINCT t.name) AS topic_names
                       FROM activities a JOIN activity_goals ag ON ag.activity_id = a.id
                       LEFT JOIN activity_goals all_ag ON all_ag.activity_id = a.id
                       LEFT JOIN goals g ON g.id = all_ag.goal_id
                       LEFT JOIN activity_topics at ON at.activity_id = a.id
                       LEFT JOIN topics t ON t.id = at.topic_id
                       WHERE ag.goal_id = ? GROUP BY a.id ORDER BY a.occurred_at DESC LIMIT ?""",
                    (goal_id, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT a.*, GROUP_CONCAT(DISTINCT g.title) AS goal_names, GROUP_CONCAT(DISTINCT t.name) AS topic_names
                       FROM activities a
                       LEFT JOIN activity_goals ag ON ag.activity_id = a.id
                       LEFT JOIN goals g ON g.id = ag.goal_id
                       LEFT JOIN activity_topics at ON at.activity_id = a.id
                       LEFT JOIN topics t ON t.id = at.topic_id
                       GROUP BY a.id ORDER BY a.occurred_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
        return [dict(row) for row in rows]

    def list_assistant_actions(self, *, goal_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self.db.connection() as connection:
            if goal_id:
                rows = connection.execute(
                    """SELECT DISTINCT aa.*, a.title AS activity_title
                       FROM assistant_actions aa
                       LEFT JOIN activities a ON a.id = aa.entity_id
                       LEFT JOIN activity_goals ag ON ag.activity_id = a.id
                       WHERE aa.action_type = 'log_activity' AND (ag.goal_id = ? OR aa.entity_id IS NULL)
                       ORDER BY aa.created_at DESC LIMIT ?""",
                    (goal_id, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT aa.*, a.title AS activity_title
                       FROM assistant_actions aa LEFT JOIN activities a ON a.id = aa.entity_id
                       WHERE aa.action_type = 'log_activity'
                       ORDER BY aa.created_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
        return [dict(row) for row in rows]


class KnowledgeLinkService:
    MAX_IMPORT_BYTES = 250 * 1024 * 1024

    def __init__(self, database: SqliteDatabase | Path | None = None) -> None:
        self.db = _database(database)

    def list_topics(self) -> list[dict[str, Any]]:
        with self.db.connection() as connection:
            rows = connection.execute("SELECT * FROM topics ORDER BY name COLLATE NOCASE").fetchall()
        return [dict(row) for row in rows]

    def link_topic_to_goal(self, goal_id: str, topic_name: str) -> dict[str, Any]:
        topic_name = _text(topic_name, "Topic name", maximum=200)
        with self.db.connection() as connection:
            if not connection.execute("SELECT 1 FROM goals WHERE id = ?", (goal_id,)).fetchone():
                raise AmigoServiceError("That goal no longer exists.")
            topic_id = ActivityService._get_or_create_topic(connection, topic_name)
            connection.execute(
                "INSERT OR IGNORE INTO goal_topics(goal_id, topic_id, created_at) VALUES (?, ?, ?)",
                (goal_id, topic_id, _now()),
            )
        return {"goal_id": goal_id, "topic_id": topic_id, "topic_name": topic_name}

    def topics_for_goal(self, goal_id: str) -> list[dict[str, Any]]:
        with self.db.connection() as connection:
            rows = connection.execute(
                "SELECT t.* FROM topics t JOIN goal_topics gt ON gt.topic_id = t.id WHERE gt.goal_id = ? ORDER BY t.name COLLATE NOCASE",
                (goal_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def materials_for_topic(self, topic_id: str) -> list[dict[str, Any]]:
        with self.db.connection() as connection:
            rows = connection.execute(
                "SELECT m.* FROM materials m JOIN material_topics mt ON mt.material_id = m.id WHERE mt.topic_id = ? ORDER BY m.title COLLATE NOCASE",
                (topic_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def import_material(self, source_path: str | Path, topic_names: Iterable[str]) -> MaterialRecord:
        topics = [_text(name, "At least one topic is required", maximum=200) for name in topic_names]
        if not topics:
            raise AmigoServiceError("Choose a topic to link this material to.")
        source = Path(source_path)
        if _is_reparse_point(source) or not source.exists() or not source.is_file():
            raise AmigoServiceError("Select an existing regular file. Linked or redirected files are blocked.")
        try:
            source = source.resolve(strict=True)
            source_stat = source.stat()
        except OSError as exc:
            raise AmigoServiceError(f"Could not inspect the selected file: {exc}") from exc
        if source_stat.st_size > self.MAX_IMPORT_BYTES:
            raise AmigoServiceError("This first-release importer supports files up to 250 MB.")

        root = Path(WORKSPACE_DIR)
        if _is_reparse_point(root) or not root.exists() or not root.is_dir():
            raise AmigoServiceError("Amigo's managed materials folder is unavailable or redirected.")
        root = root.resolve(strict=True)
        name = self._safe_filename(source.name)
        target = self._unique_target(root, name)
        digest = hashlib.sha256()
        try:
            with source.open("rb") as reader, target.open("xb") as writer:
                while True:
                    chunk = reader.read(1024 * 1024)
                    if not chunk:
                        break
                    writer.write(chunk)
                    digest.update(chunk)
            media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            relative = target.relative_to(root).as_posix()
            material_id, timestamp = str(uuid.uuid4()), _now()
            with self.db.connection() as connection:
                duplicate = connection.execute("SELECT * FROM materials WHERE sha256 = ?", (digest.hexdigest(),)).fetchone()
                if duplicate:
                    target.unlink(missing_ok=True)
                    for topic_name in topics:
                        topic_id = ActivityService._get_or_create_topic(connection, topic_name)
                        connection.execute(
                            "INSERT OR IGNORE INTO material_topics(material_id, topic_id) VALUES (?, ?)",
                            (duplicate["id"], topic_id),
                        )
                    return MaterialRecord(**dict(duplicate))
                connection.execute(
                    """INSERT INTO materials(id, title, relative_path, media_type, size_bytes, sha256, imported_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (material_id, source.stem, relative, media_type, source_stat.st_size, digest.hexdigest(), timestamp),
                )
                for topic_name in topics:
                    topic_id = ActivityService._get_or_create_topic(connection, topic_name)
                    connection.execute(
                        "INSERT INTO material_topics(material_id, topic_id) VALUES (?, ?)", (material_id, topic_id)
                    )
                row = connection.execute("SELECT * FROM materials WHERE id = ?", (material_id,)).fetchone()
            return MaterialRecord(**dict(row))
        except Exception:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    @staticmethod
    def _safe_filename(filename: str) -> str:
        path = Path(filename)
        suffix = re.sub(r"[^A-Za-z0-9.]+", "", path.suffix)[:20]
        stem = re.sub(r"[^A-Za-z0-9 _().-]+", "_", path.stem).strip(" .")[:160] or "material"
        if stem.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
            stem = f"_{stem}"
        return f"{stem}{suffix}"

    @staticmethod
    def _unique_target(root: Path, filename: str) -> Path:
        candidate = root / filename
        stem, suffix = candidate.stem, candidate.suffix
        for index in range(1, 10000):
            target = candidate if index == 1 else root / f"{stem} ({index}){suffix}"
            if not target.exists():
                return target
        raise AmigoServiceError("Too many files with the same name in Amigo's materials folder.")


class ProgressQuery:
    def __init__(self, database: SqliteDatabase | Path | None = None) -> None:
        self.db = _database(database)

    def goal_progress(self, goal_id: str) -> GoalProgress:
        with self.db.connection() as connection:
            goal = connection.execute("SELECT 1 FROM goals WHERE id = ?", (goal_id,)).fetchone()
            if not goal:
                raise AmigoServiceError("That goal no longer exists.")
            row = connection.execute(
                """SELECT
                       (SELECT COUNT(*) FROM milestones WHERE goal_id = ?) AS milestone_total,
                       (SELECT COUNT(*) FROM milestones WHERE goal_id = ? AND status = 'completed') AS milestone_completed,
                       COUNT(DISTINCT a.id) AS activity_count,
                       COALESCE(SUM(a.duration_minutes), 0) AS activity_minutes,
                       MAX(a.occurred_at) AS last_activity_at
                   FROM activity_goals ag LEFT JOIN activities a ON a.id = ag.activity_id
                   WHERE ag.goal_id = ?""",
                (goal_id, goal_id, goal_id),
            ).fetchone()
        return GoalProgress(
            goal_id=goal_id,
            milestone_total=int(row["milestone_total"]),
            milestone_completed=int(row["milestone_completed"]),
            activity_count=int(row["activity_count"]),
            activity_minutes=int(row["activity_minutes"]),
            last_activity_at=row["last_activity_at"],
        )

    def goal_summaries(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        where = "WHERE g.status = 'active'" if active_only else ""
        with self.db.connection() as connection:
            rows = connection.execute(
                f"""SELECT g.*,
                       (SELECT COUNT(*) FROM milestones m WHERE m.goal_id = g.id) AS milestone_total,
                       (SELECT COUNT(*) FROM milestones m WHERE m.goal_id = g.id AND m.status = 'completed') AS milestone_completed,
                       (SELECT COUNT(*) FROM activity_goals ag WHERE ag.goal_id = g.id) AS activity_count,
                       (SELECT COALESCE(SUM(a.duration_minutes), 0) FROM activity_goals ag JOIN activities a ON a.id = ag.activity_id WHERE ag.goal_id = g.id) AS activity_minutes,
                       (SELECT MAX(a.occurred_at) FROM activity_goals ag JOIN activities a ON a.id = ag.activity_id WHERE ag.goal_id = g.id) AS last_activity_at
                   FROM goals g {where}
                   ORDER BY CASE g.status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 ELSE 2 END,
                            g.priority, g.target_date IS NULL, g.target_date, g.updated_at DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def dashboard_summary(self) -> dict[str, Any]:
        goals = self.goal_summaries(active_only=True)
        activities = ActivityService(self.db).list_activities(limit=8)
        return {"active_goals": goals, "recent_activities": activities}

