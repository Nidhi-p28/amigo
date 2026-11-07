from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from app.calendar import CalendarQuery, CalendarService
from app.domain import AmigoServiceError, CalendarItemKind, CalendarItemStatus
from app.services import ActivityService, GoalService, ProgressQuery
from app.storage import SqliteDatabase


class CalendarServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = SqliteDatabase(Path(self.temporary.name) / "amigo.sqlite3", copy_legacy=False)
        self.calendar = CalendarService(self.database)
        self.query = CalendarQuery(self.database)
        self.goals = GoalService(self.database)
        self.progress = ProgressQuery(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_sessions_and_events_remain_distinct_from_activities_and_milestones(self) -> None:
        goal = self.goals.create_goal("Learn biology")
        session = self.calendar.create_session(
            "Review cells",
            "2026-05-10T09:00:00+05:30",
            "2026-05-10T10:30:00+05:30",
            description="Read the cell chapter",
            goal_ids=[goal["id"]],
            topic_names=["Cell biology"],
        )
        event = self.calendar.create_event(
            "Library appointment",
            "2026-05-10T11:00:00+05:30",
            "2026-05-10T11:30:00+05:30",
        )

        entries = self.query.entries_between(
            "2026-05-10T00:00:00+05:30", "2026-05-11T00:00:00+05:30"
        )
        progress = self.progress.goal_progress(goal["id"])

        self.assertEqual([CalendarItemKind.SESSION, CalendarItemKind.EVENT], [entry.kind for entry in entries])
        self.assertEqual([session["id"], event["id"]], [entry.id for entry in entries])
        self.assertEqual("Learn biology", entries[0].goal_names)
        self.assertEqual("Cell biology", entries[0].topic_names)
        self.assertEqual(0, progress.activity_count)
        self.assertEqual(0, progress.milestone_total)
        self.assertEqual("2026-05-10T03:30:00+00:00", session["starts_at"])

    def test_session_can_be_rescheduled_without_losing_links(self) -> None:
        goal = self.goals.create_goal("Exam preparation")
        session = self.calendar.create_session(
            "Practice questions", "2026-06-01T09:00:00Z", "2026-06-01T10:00:00Z",
            goal_ids=[goal["id"]], topic_names=["Algebra"],
        )

        updated = self.calendar.update_session(
            session["id"], "Timed practice", "2026-06-01T14:00:00Z", "2026-06-01T15:30:00Z",
            description="Use the mock paper", goal_ids=[goal["id"]], topic_names=["Algebra", "Geometry"],
        )
        detail = self.calendar.get_session(session["id"])

        self.assertEqual("Timed practice", updated["title"])
        self.assertEqual("2026-06-01T14:00:00+00:00", detail["starts_at"])
        self.assertEqual([goal["id"]], detail["goal_ids"])
        self.assertEqual(["Algebra", "Geometry"], detail["topic_names"])

    def test_cancellation_is_hidden_by_default_and_preserved_in_calendar_history(self) -> None:
        session = self.calendar.create_session(
            "Reading block", "2026-07-01T09:00:00Z", "2026-07-01T10:00:00Z"
        )
        event = self.calendar.create_event(
            "Club meeting", "2026-07-01T11:00:00Z", "2026-07-01T12:00:00Z"
        )

        cancelled = self.calendar.cancel_session(session["id"])
        active = self.query.entries_between("2026-07-01T00:00:00Z", "2026-07-02T00:00:00Z")
        history = self.query.entries_between(
            "2026-07-01T00:00:00Z", "2026-07-02T00:00:00Z", include_cancelled=True
        )

        self.assertEqual(CalendarItemStatus.CANCELLED.value, cancelled["status"])
        self.assertEqual([event["id"]], [entry.id for entry in active])
        self.assertEqual(2, len(history))
        self.assertEqual(CalendarItemStatus.CANCELLED, history[0].status)

    def test_general_event_can_be_edited_rescheduled_and_cancelled(self) -> None:
        event = self.calendar.create_event(
            "Advising", "2026-07-04T09:00:00Z", "2026-07-04T09:30:00Z", description="Initial details"
        )
        updated = self.calendar.update_event(
            event["id"], "Advisor meeting", "2026-07-04T15:00:00Z", "2026-07-04T16:00:00Z",
            description="Bring questions",
        )

        self.assertEqual("Advisor meeting", updated["title"])
        self.assertEqual("Bring questions", self.calendar.get_event(event["id"])["description"])
        cancelled = self.calendar.cancel_event(event["id"])
        self.assertEqual(CalendarItemStatus.CANCELLED.value, cancelled["status"])

    def test_multiday_calendar_event_appears_on_each_overlapping_day(self) -> None:
        self.calendar.create_event(
            "Conference", "2026-08-01T18:00:00Z", "2026-08-03T09:00:00Z"
        )

        first_day = self.query.entries_for_day(date(2026, 8, 2))
        last_day = self.query.entries_for_day(date(2026, 8, 3))

        self.assertEqual(1, len(first_day))
        self.assertEqual(1, len(last_day))
        self.assertEqual(CalendarItemKind.EVENT, first_day[0].kind)

    def test_invalid_time_ranges_and_missing_goal_references_are_rejected(self) -> None:
        goal = self.goals.create_goal("Statistics")
        with self.assertRaises(AmigoServiceError):
            self.calendar.create_session("Bad time", "2026-09-01T10:00:00Z", "2026-09-01T09:00:00Z")
        with self.assertRaises(AmigoServiceError):
            self.calendar.create_session("Too long", "2026-09-01T00:00:00Z", "2026-09-02T01:00:00Z")
        with self.assertRaises(AmigoServiceError):
            self.calendar.create_session(
                "Missing goal", "2026-09-01T09:00:00Z", "2026-09-01T10:00:00Z", goal_ids=["unknown"]
            )
        self.assertEqual(0, self.progress.goal_progress(goal["id"]).activity_count)


if __name__ == "__main__":
    unittest.main()
