from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .domain import AmigoServiceError, CalendarEntryRecord, CalendarItemKind, CalendarItemStatus
from .services import ActivityService
from .storage import SqliteDatabase, _now


def _database(database: SqliteDatabase | Path | None) -> SqliteDatabase:
    if isinstance(database, SqliteDatabase):
        return database
    return SqliteDatabase(database) if database is not None else SqliteDatabase()


def _required(value: str, label: str, maximum: int = 200) -> str:
    result = str(value or "").strip()
    if not result:
        raise AmigoServiceError(f"{label} is required.")
    if len(result) > maximum:
        raise AmigoServiceError(f"{label} must be {maximum} characters or fewer.")
    return result


def _description(value: str) -> str:
    result = str(value or "").strip()
    if len(result) > 4000:
        raise AmigoServiceError("Description must be 4,000 characters or fewer.")
    return result


def _instant(value: datetime | str, label: str) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise AmigoServiceError(f"{label} must be a valid local date and time.") from exc
    # Treat timezone-free values as local wall time. Persist every time in UTC
    # so range searches and ordering remain consistent across DST boundaries.
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    else:
        parsed = parsed.astimezone(timezone.utc)
    parsed = parsed.replace(microsecond=0)
    return parsed.isoformat(timespec="seconds")


def _times(start: datetime | str, end: datetime | str, *, maximum_hours: int, label: str) -> tuple[str, str]:
    starts_at = _instant(start, f"{label} start")
    ends_at = _instant(end, f"{label} end")
    start_dt = datetime.fromisoformat(starts_at)
    end_dt = datetime.fromisoformat(ends_at)
    if end_dt <= start_dt:
        raise AmigoServiceError(f"{label} end must be after its start.")
    if end_dt - start_dt > timedelta(hours=maximum_hours):
        raise AmigoServiceError(f"{label} cannot be longer than {maximum_hours} hours.")
    return starts_at, ends_at


def _ids(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _topic_names(values: Iterable[str]) -> list[str]:
    names: list[str] = []
    for value in values:
        name = str(value or "").strip()
        if not name:
            continue
        if len(name) > 200:
            raise AmigoServiceError("Topic names must be 200 characters or fewer.")
        if name.casefold() not in {item.casefold() for item in names}:
            names.append(name)
    return names


def _validate_goals(connection, goal_ids: list[str]) -> None:
    for goal_id in goal_ids:
        row = connection.execute("SELECT 1 FROM goals WHERE id = ?", (goal_id,)).fetchone()
        if not row:
            raise AmigoServiceError("A linked goal no longer exists. Refresh the calendar and try again.")


class CalendarService:
    """Validated writes for planned work sessions and general calendar events."""

    def __init__(self, database: SqliteDatabase | Path | None = None) -> None:
        self.db = _database(database)

    def create_session(
        self,
        title: str,
        starts_at: datetime | str,
        ends_at: datetime | str,
        *,
        description: str = "",
        goal_ids: Iterable[str] = (),
        topic_names: Iterable[str] = (),
    ) -> dict[str, Any]:
        title = _required(title, "Session title")
        description = _description(description)
        starts, ends = _times(starts_at, ends_at, maximum_hours=24, label="Planned session")
        goals = _ids(goal_ids)
        topics = _topic_names(topic_names)
        session_id, timestamp = str(uuid.uuid4()), _now()
        with self.db.connection() as connection:
            _validate_goals(connection, goals)
            topic_ids = [ActivityService._get_or_create_topic(connection, name) for name in topics]
            connection.execute(
                """INSERT INTO planned_sessions(id, title, description, starts_at, ends_at, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'scheduled', ?, ?)""",
                (session_id, title, description, starts, ends, timestamp, timestamp),
            )
            connection.executemany(
                "INSERT INTO planned_session_goals(session_id, goal_id) VALUES (?, ?)",
                [(session_id, goal_id) for goal_id in goals],
            )
            connection.executemany(
                "INSERT INTO planned_session_topics(session_id, topic_id) VALUES (?, ?)",
                [(session_id, topic_id) for topic_id in topic_ids],
            )
            return dict(connection.execute("SELECT * FROM planned_sessions WHERE id = ?", (session_id,)).fetchone())

    def update_session(
        self,
        session_id: str,
        title: str,
        starts_at: datetime | str,
        ends_at: datetime | str,
        *,
        description: str = "",
        goal_ids: Iterable[str] = (),
        topic_names: Iterable[str] = (),
    ) -> dict[str, Any]:
        title = _required(title, "Session title")
        description = _description(description)
        starts, ends = _times(starts_at, ends_at, maximum_hours=24, label="Planned session")
        goals = _ids(goal_ids)
        topics = _topic_names(topic_names)
        timestamp = _now()
        with self.db.connection() as connection:
            row = connection.execute("SELECT status FROM planned_sessions WHERE id = ?", (session_id,)).fetchone()
            if not row:
                raise AmigoServiceError("That planned session no longer exists.")
            if row["status"] != CalendarItemStatus.SCHEDULED.value:
                raise AmigoServiceError("A cancelled session cannot be edited.")
            _validate_goals(connection, goals)
            topic_ids = [ActivityService._get_or_create_topic(connection, name) for name in topics]
            connection.execute(
                "UPDATE planned_sessions SET title=?, description=?, starts_at=?, ends_at=?, updated_at=? WHERE id=?",
                (title, description, starts, ends, timestamp, session_id),
            )
            connection.execute("DELETE FROM planned_session_goals WHERE session_id = ?", (session_id,))
            connection.executemany(
                "INSERT INTO planned_session_goals(session_id, goal_id) VALUES (?, ?)",
                [(session_id, goal_id) for goal_id in goals],
            )
            connection.execute("DELETE FROM planned_session_topics WHERE session_id = ?", (session_id,))
            connection.executemany(
                "INSERT INTO planned_session_topics(session_id, topic_id) VALUES (?, ?)",
                [(session_id, topic_id) for topic_id in topic_ids],
            )
            return dict(connection.execute("SELECT * FROM planned_sessions WHERE id = ?", (session_id,)).fetchone())

    def cancel_session(self, session_id: str) -> dict[str, Any]:
        return self._cancel("planned_sessions", session_id, "planned session")

    def get_session(self, session_id: str) -> dict[str, Any]:
        with self.db.connection() as connection:
            row = connection.execute("SELECT * FROM planned_sessions WHERE id = ?", (session_id,)).fetchone()
            if not row:
                raise AmigoServiceError("That planned session no longer exists.")
            goal_ids = connection.execute(
                "SELECT goal_id FROM planned_session_goals WHERE session_id = ? ORDER BY goal_id", (session_id,)
            ).fetchall()
            topic_names = connection.execute(
                """SELECT t.name FROM topics t JOIN planned_session_topics st ON st.topic_id=t.id
                   WHERE st.session_id = ? ORDER BY t.name COLLATE NOCASE""", (session_id,)
            ).fetchall()
        return {**dict(row), "goal_ids": [str(item["goal_id"]) for item in goal_ids],
                "topic_names": [str(item["name"]) for item in topic_names]}

    def create_event(
        self,
        title: str,
        starts_at: datetime | str,
        ends_at: datetime | str,
        *,
        description: str = "",
    ) -> dict[str, Any]:
        title = _required(title, "Event title")
        description = _description(description)
        starts, ends = _times(starts_at, ends_at, maximum_hours=24 * 7, label="Calendar event")
        event_id, timestamp = str(uuid.uuid4()), _now()
        with self.db.connection() as connection:
            connection.execute(
                """INSERT INTO calendar_events(id, title, description, starts_at, ends_at, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'scheduled', ?, ?)""",
                (event_id, title, description, starts, ends, timestamp, timestamp),
            )
            return dict(connection.execute("SELECT * FROM calendar_events WHERE id = ?", (event_id,)).fetchone())

    def update_event(
        self,
        event_id: str,
        title: str,
        starts_at: datetime | str,
        ends_at: datetime | str,
        *,
        description: str = "",
    ) -> dict[str, Any]:
        title = _required(title, "Event title")
        description = _description(description)
        starts, ends = _times(starts_at, ends_at, maximum_hours=24 * 7, label="Calendar event")
        with self.db.connection() as connection:
            row = connection.execute("SELECT status FROM calendar_events WHERE id = ?", (event_id,)).fetchone()
            if not row:
                raise AmigoServiceError("That calendar event no longer exists.")
            if row["status"] != CalendarItemStatus.SCHEDULED.value:
                raise AmigoServiceError("A cancelled event cannot be edited.")
            connection.execute(
                "UPDATE calendar_events SET title=?, description=?, starts_at=?, ends_at=?, updated_at=? WHERE id=?",
                (title, description, starts, ends, _now(), event_id),
            )
            return dict(connection.execute("SELECT * FROM calendar_events WHERE id = ?", (event_id,)).fetchone())

    def cancel_event(self, event_id: str) -> dict[str, Any]:
        return self._cancel("calendar_events", event_id, "calendar event")

    def get_event(self, event_id: str) -> dict[str, Any]:
        with self.db.connection() as connection:
            row = connection.execute("SELECT * FROM calendar_events WHERE id = ?", (event_id,)).fetchone()
        if not row:
            raise AmigoServiceError("That calendar event no longer exists.")
        return dict(row)

    def _cancel(self, table: str, item_id: str, label: str) -> dict[str, Any]:
        # Table names come only from the fixed service methods above.
        with self.db.connection() as connection:
            row = connection.execute(f"SELECT status FROM {table} WHERE id = ?", (item_id,)).fetchone()
            if not row:
                raise AmigoServiceError(f"That {label} no longer exists.")
            if row["status"] != CalendarItemStatus.CANCELLED.value:
                connection.execute(
                    f"UPDATE {table} SET status='cancelled', updated_at=? WHERE id=?", (_now(), item_id)
                )
            return dict(connection.execute(f"SELECT * FROM {table} WHERE id = ?", (item_id,)).fetchone())


class CalendarQuery:
    """Read-only combined agenda query; does not include activities or milestones."""

    def __init__(self, database: SqliteDatabase | Path | None = None) -> None:
        self.db = _database(database)

    def entries_between(
        self,
        starts_at: datetime | str,
        ends_at: datetime | str,
        *,
        include_cancelled: bool = False,
        limit: int | None = None,
    ) -> list[CalendarEntryRecord]:
        start = _instant(starts_at, "Range start")
        end = _instant(ends_at, "Range end")
        if end <= start:
            raise AmigoServiceError("Calendar range end must be after its start.")
        status_filter = "" if include_cancelled else "AND s.status = 'scheduled'"
        event_status_filter = "" if include_cancelled else "AND e.status = 'scheduled'"
        limit_clause = " LIMIT ?" if limit is not None else ""
        limit = max(1, min(int(limit), 500)) if limit is not None else None
        with self.db.connection() as connection:
            sessions = connection.execute(
                f"""SELECT s.id, s.title, s.description, s.starts_at, s.ends_at, s.status,
                           GROUP_CONCAT(DISTINCT g.title) AS goal_names,
                           GROUP_CONCAT(DISTINCT t.name) AS topic_names
                    FROM planned_sessions s
                    LEFT JOIN planned_session_goals sg ON sg.session_id=s.id
                    LEFT JOIN goals g ON g.id=sg.goal_id
                    LEFT JOIN planned_session_topics st ON st.session_id=s.id
                    LEFT JOIN topics t ON t.id=st.topic_id
                    WHERE s.starts_at < ? AND s.ends_at > ? {status_filter}
                    GROUP BY s.id ORDER BY s.starts_at{limit_clause}""",
                (end, start, limit) if limit is not None else (end, start),
            ).fetchall()
            events = connection.execute(
                f"""SELECT e.id, e.title, e.description, e.starts_at, e.ends_at, e.status
                    FROM calendar_events e
                    WHERE e.starts_at < ? AND e.ends_at > ? {event_status_filter}
                    ORDER BY e.starts_at{limit_clause}""",
                (end, start, limit) if limit is not None else (end, start),
            ).fetchall()
        result = [
            CalendarEntryRecord(
                id=str(row["id"]), kind=CalendarItemKind.SESSION, title=str(row["title"]),
                description=str(row["description"]), starts_at=str(row["starts_at"]), ends_at=str(row["ends_at"]),
                status=CalendarItemStatus(row["status"]), goal_names=str(row["goal_names"] or ""),
                topic_names=str(row["topic_names"] or ""),
            )
            for row in sessions
        ]
        result.extend(
            CalendarEntryRecord(
                id=str(row["id"]), kind=CalendarItemKind.EVENT, title=str(row["title"]),
                description=str(row["description"]), starts_at=str(row["starts_at"]), ends_at=str(row["ends_at"]),
                status=CalendarItemStatus(row["status"]),
            )
            for row in events
        )
        return sorted(result, key=lambda item: (item.starts_at, item.kind.value, item.title.casefold()))

    def entries_for_day(self, selected_day: date | str, *, include_cancelled: bool = False) -> list[CalendarEntryRecord]:
        if isinstance(selected_day, str):
            try:
                selected_day = date.fromisoformat(selected_day)
            except ValueError as exc:
                raise AmigoServiceError("Choose a valid calendar date.") from exc
        start = datetime.combine(selected_day, time.min).astimezone()
        end = datetime.combine(selected_day + timedelta(days=1), time.min).astimezone()
        return self.entries_between(start, end, include_cancelled=include_cancelled)

    def upcoming(self, *, limit: int = 20, days: int = 30) -> list[CalendarEntryRecord]:
        limit = max(1, min(int(limit), 100))
        days = max(1, min(int(days), 365))
        start = datetime.now(timezone.utc)
        end = start + timedelta(days=days)
        return self.entries_between(start, end, limit=limit)[:limit]

