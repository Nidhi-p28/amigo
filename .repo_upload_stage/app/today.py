from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from .calendar import CalendarQuery
from .domain import AmigoServiceError
from .services import GoalService, ProgressQuery, _database
from .storage import SqliteDatabase, _now


def _utc_bounds(day: date) -> tuple[str, str]:
    start = datetime.combine(day, time.min).astimezone().astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time.min).astimezone().astimezone(timezone.utc)
    return start.isoformat(), end.isoformat()


def _clean_title(value: str) -> str:
    title = str(value or "").strip()
    if not title:
        raise AmigoServiceError("Activity name is required.")
    if len(title) > 500:
        raise AmigoServiceError("Activity name must be 500 characters or fewer.")
    return title


class TodayService:
    """Purpose-built dashboard queries and daily-plan actions."""

    def __init__(self, database: SqliteDatabase | Path | None = None) -> None:
        self.db = _database(database)
        self.progress = ProgressQuery(self.db)
        self.calendar = CalendarQuery(self.db)

    def add_activity_item(
        self,
        title: str,
        *,
        activity_date: date | str | None = None,
        estimated_minutes: int | None = None,
        goal_id: str | None = None,
        topic_id: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        title = _clean_title(title)
        try:
            selected_day = date.fromisoformat(activity_date) if isinstance(activity_date, str) else activity_date
        except ValueError as exc:
            raise AmigoServiceError("Choose a valid activity date.") from exc
        selected_day = selected_day or date.today()
        if estimated_minutes is not None:
            try:
                estimated_minutes = int(estimated_minutes)
            except (TypeError, ValueError) as exc:
                raise AmigoServiceError("Estimated time must be a whole number of minutes.") from exc
            if not 1 <= estimated_minutes <= 1440:
                raise AmigoServiceError("Estimated time must be between 1 and 1,440 minutes.")
        item_id, timestamp = str(uuid.uuid4()), _now()
        with self.db.connection() as connection:
            if goal_id and not connection.execute("SELECT 1 FROM goals WHERE id = ?", (goal_id,)).fetchone():
                raise AmigoServiceError("That goal no longer exists.")
            if topic_id and not connection.execute("SELECT 1 FROM topics WHERE id = ?", (topic_id,)).fetchone():
                raise AmigoServiceError("That topic no longer exists.")
            if project_id and not connection.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone():
                raise AmigoServiceError("That project no longer exists.")
            connection.execute(
                """INSERT INTO daily_activity_items
                   (id, activity_date, title, estimated_minutes, goal_id, topic_id, project_id, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
                (item_id, selected_day.isoformat(), title, estimated_minutes, goal_id, topic_id, project_id, timestamp, timestamp),
            )
        return next(row for row in self.activity_items(selected_day) if row["id"] == item_id)

    def complete_activity_item(self, item_id: str) -> dict[str, Any]:
        """Complete a planned dashboard item by creating a distinct actual activity record."""
        timestamp = _now()
        activity_id = str(uuid.uuid4())
        with self.db.connection() as connection:
            item = connection.execute(
                "SELECT * FROM daily_activity_items WHERE id = ?", (item_id,)
            ).fetchone()
            if not item:
                raise AmigoServiceError("That planned activity no longer exists.")
            if item["status"] == "completed":
                row = connection.execute(
                    """SELECT a.*, g.title AS goal_name, t.name AS topic_name
                       FROM activities a
                       LEFT JOIN activity_goals ag ON ag.activity_id = a.id
                       LEFT JOIN goals g ON g.id = ag.goal_id
                       LEFT JOIN activity_topics at ON at.activity_id = a.id
                       LEFT JOIN topics t ON t.id = at.topic_id
                       WHERE a.id = ? GROUP BY a.id""",
                    (item["completed_activity_id"],),
                ).fetchone()
                return dict(row) if row else {"id": item["completed_activity_id"], "title": item["title"]}

            # Planned estimates remain estimates. Actual duration is left empty unless the
            # user records it separately, so the dashboard cannot imply time that was not logged.
            connection.execute(
                """INSERT INTO activities
                   (id, title, occurred_at, duration_minutes, status, reflection, origin, created_at)
                   VALUES (?, ?, ?, NULL, 'completed', '', 'ui', ?)""",
                (activity_id, item["title"], timestamp, timestamp),
            )
            if item["goal_id"]:
                connection.execute(
                    "INSERT INTO activity_goals(activity_id, goal_id) VALUES (?, ?)",
                    (activity_id, item["goal_id"]),
                )
                connection.execute(
                    "UPDATE goals SET updated_at = ? WHERE id = ?", (timestamp, item["goal_id"])
                )
            if item["topic_id"]:
                connection.execute(
                    "INSERT INTO activity_topics(activity_id, topic_id) VALUES (?, ?)",
                    (activity_id, item["topic_id"]),
                )
                if item["goal_id"]:
                    connection.execute(
                        """INSERT OR IGNORE INTO goal_topics(goal_id, topic_id, created_at)
                           VALUES (?, ?, ?)""",
                        (item["goal_id"], item["topic_id"], timestamp),
                    )
                connection.execute(
                    """INSERT INTO learning_evidence(id,topic_id,evidence_type,summary,source_id,created_at)
                       VALUES(?,?,'activity',?,?,?)""",
                    (str(uuid.uuid4()), item["topic_id"], f"Completed activity: {item['title']}", activity_id, timestamp),
                )
            if item["project_id"]:
                connection.execute(
                    "INSERT INTO project_activities(project_id,activity_id,created_at) VALUES(?,?,?)",
                    (item["project_id"], activity_id, timestamp),
                )
            connection.execute(
                """UPDATE daily_activity_items
                   SET status = 'completed', completed_activity_id = ?, updated_at = ? WHERE id = ?""",
                (activity_id, timestamp, item_id),
            )
            row = connection.execute(
                """SELECT a.*, g.title AS goal_name, t.name AS topic_name
                   FROM activities a
                   LEFT JOIN activity_goals ag ON ag.activity_id = a.id
                   LEFT JOIN goals g ON g.id = ag.goal_id
                   LEFT JOIN activity_topics at ON at.activity_id = a.id
                   LEFT JOIN topics t ON t.id = at.topic_id
                   WHERE a.id = ? GROUP BY a.id""",
                (activity_id,),
            ).fetchone()
            return dict(row)

    def activity_items(self, selected_day: date | str) -> list[dict[str, Any]]:
        day_text = selected_day.isoformat() if isinstance(selected_day, date) else date.fromisoformat(str(selected_day)).isoformat()
        with self.db.connection() as connection:
            rows = connection.execute(
                """SELECT d.*, g.title AS goal_name, t.name AS topic_name
                   ,p.title AS project_name
                   FROM daily_activity_items d
                   LEFT JOIN goals g ON g.id = d.goal_id
                   LEFT JOIN topics t ON t.id = d.topic_id
                   LEFT JOIN projects p ON p.id = d.project_id
                   WHERE d.activity_date = ?
                   ORDER BY CASE d.status WHEN 'pending' THEN 0 ELSE 1 END, d.created_at, d.id""",
                (day_text,),
            ).fetchall()
        return [dict(row) for row in rows]

    def activities_on(self, selected_day: date | str) -> list[dict[str, Any]]:
        day = date.fromisoformat(selected_day) if isinstance(selected_day, str) else selected_day
        start, end = _utc_bounds(day)
        with self.db.connection() as connection:
            rows = connection.execute(
                """SELECT a.*, GROUP_CONCAT(DISTINCT g.title) AS goal_names,
                          GROUP_CONCAT(DISTINCT t.name) AS topic_names
                   FROM activities a
                   LEFT JOIN activity_goals ag ON ag.activity_id = a.id
                   LEFT JOIN goals g ON g.id = ag.goal_id
                   LEFT JOIN activity_topics at ON at.activity_id = a.id
                   LEFT JOIN topics t ON t.id = at.topic_id
                   WHERE a.occurred_at >= ? AND a.occurred_at < ?
                   GROUP BY a.id ORDER BY a.occurred_at DESC""",
                (start, end),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            local_time = datetime.fromisoformat(str(item["occurred_at"])).astimezone()
            item["local_time"] = local_time.strftime("%I:%M %p").lstrip("0")
            result.append(item)
        return result

    def heatmap(self, year: int, month: int) -> dict[str, dict[str, int]]:
        first = date(year, month, 1)
        next_month = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
        start, _ = _utc_bounds(first)
        _, end = _utc_bounds(next_month - timedelta(days=1))
        with self.db.connection() as connection:
            rows = connection.execute(
                """SELECT occurred_at, duration_minutes FROM activities
                   WHERE occurred_at >= ? AND occurred_at < ? ORDER BY occurred_at""",
                (start, end),
            ).fetchall()
        totals: dict[str, dict[str, int]] = defaultdict(lambda: {"count": 0, "minutes": 0})
        for row in rows:
            local_day = datetime.fromisoformat(str(row["occurred_at"])).astimezone().date().isoformat()
            totals[local_day]["count"] += 1
            totals[local_day]["minutes"] += int(row["duration_minutes"] or 0)
        return dict(totals)

    def focus_priorities(self, day: date, *, limit: int = 4) -> list[dict[str, Any]]:
        priorities: list[dict[str, Any]] = []
        for session in self.calendar.entries_for_day(day):
            if session.kind.value == "session":
                start = datetime.fromisoformat(session.starts_at).astimezone()
                priorities.append({
                    "kind": "session", "title": session.title,
                    "reason": f"Planned session · {start.strftime('%I:%M %p').lstrip('0')}",
                    "goal_names": session.goal_names,
                })
        horizon = (day + timedelta(days=7)).isoformat()
        with self.db.connection() as connection:
            due = connection.execute(
                """SELECT m.id, m.title, m.target_date, g.id AS goal_id, g.title AS goal_title, g.priority
                   FROM milestones m JOIN goals g ON g.id = m.goal_id
                   WHERE g.status = 'active' AND m.status = 'pending'
                     AND m.target_date IS NOT NULL AND m.target_date <= ?
                   ORDER BY CASE WHEN m.target_date < ? THEN 0 ELSE 1 END,
                            m.target_date, g.priority, m.created_at LIMIT ?""",
                (horizon, day.isoformat(), limit),
            ).fetchall()
            priorities.extend({
                "kind": "milestone", "id": str(row["id"]), "title": str(row["title"]),
                "goal_id": str(row["goal_id"]), "goal_title": str(row["goal_title"]),
                "target_date": str(row["target_date"]),
                "reason": "Overdue milestone" if str(row["target_date"]) < day.isoformat()
                else f"Due {str(row['target_date'])}",
            } for row in due)
        if not priorities:
            goals = self.progress.goal_summaries(active_only=True)
            for goal in goals[: min(limit, 2)]:
                priorities.append({
                    "kind": "goal", "id": str(goal["id"]), "title": str(goal["title"]),
                    "reason": "Active goal · choose a next step",
                })
        return priorities[:limit]

    def active_goal_preview(self, limit: int = 4) -> list[dict[str, Any]]:
        with self.db.connection() as connection:
            rows = connection.execute(
                """SELECT g.id, g.title, g.description, g.target_date, g.priority,
                          (SELECT COUNT(*) FROM milestones m WHERE m.goal_id = g.id) AS milestone_total,
                          (SELECT COUNT(*) FROM milestones m WHERE m.goal_id = g.id AND m.status = 'completed') AS milestone_completed,
                          (SELECT COUNT(*) FROM activity_goals ag WHERE ag.goal_id=g.id) AS activity_count,
                          (SELECT m.title FROM milestones m WHERE m.goal_id = g.id AND m.status = 'pending'
                           ORDER BY m.target_date IS NULL, m.target_date, m.created_at LIMIT 1) AS next_milestone
                   FROM goals g WHERE g.status = 'active'
                   ORDER BY g.priority, g.target_date IS NULL, g.target_date, g.updated_at DESC LIMIT ?""",
                (max(1, min(int(limit), 12)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def garden_preview(self, limit: int = 4) -> list[dict[str, Any]]:
        summaries = self.progress.goal_summaries(active_only=False)
        return summaries[:max(1, min(int(limit), 8))]

    def continue_item(self) -> dict[str, Any] | None:
        with self.db.connection() as connection:
            activity = connection.execute(
                """SELECT a.id, a.title, a.occurred_at, g.id AS goal_id, g.title AS goal_title,
                          p.id AS project_id, p.title AS project_title,
                          GROUP_CONCAT(DISTINCT t.name) AS topic_names
                   FROM activities a
                   LEFT JOIN activity_goals ag ON ag.activity_id = a.id
                   LEFT JOIN goals g ON g.id = ag.goal_id
                   LEFT JOIN project_activities pa ON pa.activity_id=a.id
                   LEFT JOIN projects p ON p.id=pa.project_id
                   LEFT JOIN activity_topics at ON at.activity_id = a.id
                   LEFT JOIN topics t ON t.id = at.topic_id
                   GROUP BY a.id ORDER BY a.occurred_at DESC LIMIT 1"""
            ).fetchone()
            if activity:
                item = dict(activity)
                item["kind"] = "activity"
                return item
            note = connection.execute(
                """SELECT n.id, n.title, n.updated_at, t.name AS topic_name
                   FROM knowledge_notes n JOIN topics t ON t.id = n.topic_id
                   ORDER BY n.updated_at DESC LIMIT 1"""
            ).fetchone()
        if note:
            return {**dict(note), "kind": "note"}
        return None

    def snapshot(self, day: date | None = None) -> dict[str, Any]:
        selected_day = day or date.today()
        priorities = self.focus_priorities(selected_day)
        return {
            "day": selected_day,
            "activity_items": self.activity_items(selected_day),
            "activities": self.activities_on(selected_day),
            "priorities": priorities,
            "sessions": [
                item for item in self.calendar.entries_for_day(selected_day) if item.kind.value == "session"
            ],
            "active_goals": self.active_goal_preview(),
            "heatmap": self.heatmap(selected_day.year, selected_day.month),
            "continue_item": self.continue_item(),
        }
