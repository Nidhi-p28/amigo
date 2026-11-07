from __future__ import annotations

import hashlib
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agent import DesktopAgent, parse_activity_log_request
from app.domain import ActivityOrigin, AmigoServiceError, AmbiguousReferenceError
from app.services import ActivityService, GoalService, KnowledgeLinkService, ProgressQuery
from app.storage import SqliteDatabase


class DomainServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp.name) / "amigo.sqlite3"
        self.database = SqliteDatabase(self.database_path, copy_legacy=False)
        self.goals = GoalService(self.database)
        self.activities = ActivityService(self.database)
        self.knowledge = KnowledgeLinkService(self.database)
        self.progress = ProgressQuery(self.database)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_goal_pause_resume_milestones_and_derived_progress(self) -> None:
        goal = self.goals.create_goal("Learn machine learning", start_date="2026-01-01")
        first = self.goals.add_milestone(goal["id"], "Probability")
        self.goals.add_milestone(goal["id"], "Regression")
        self.goals.complete_milestone(first["id"])
        self.goals.change_status(goal["id"], "paused")
        self.goals.change_status(goal["id"], "active")
        self.activities.log_activity(
            "Studied probability", duration_minutes=30, goal_ids=[goal["id"]], topic_names=["Probability"]
        )
        self.activities.log_activity(
            "Solved practice questions", duration_minutes=30, goal_ids=[goal["id"]]
        )

        summary = self.progress.goal_progress(goal["id"])
        self.assertEqual(summary.milestone_total, 2)
        self.assertEqual(summary.milestone_completed, 1)
        self.assertEqual(summary.activity_count, 2)
        self.assertEqual(summary.activity_minutes, 60)
        events = self.goals.list_events(goal["id"])
        self.assertIn("status_changed", [row["event_type"] for row in events])
        self.assertIn("milestone_completed", [row["event_type"] for row in events])

    def test_reopening_milestone_updates_progress_and_keeps_history(self) -> None:
        goal = self.goals.create_goal("Prepare for exam")
        milestone = self.goals.add_milestone(goal["id"], "Finish practice set")
        self.goals.complete_milestone(milestone["id"])
        self.assertEqual(1, self.progress.goal_progress(goal["id"]).milestone_completed)

        reopened = self.goals.reopen_milestone(milestone["id"])

        self.assertEqual("pending", reopened["status"])
        self.assertIsNone(reopened["completed_at"])
        self.assertEqual(0, self.progress.goal_progress(goal["id"]).milestone_completed)
        self.assertEqual(
            ["milestone_completed", "milestone_reopened"],
            [
                row["event_type"]
                for row in reversed(self.goals.list_events(goal["id"]))
                if row["event_type"] in {"milestone_completed", "milestone_reopened"}
            ],
        )

    def test_goal_names_are_unique_case_insensitively(self) -> None:
        self.goals.create_goal("Learn Python")
        with self.assertRaises(AmigoServiceError):
            self.goals.create_goal("learn python")

    def test_assistant_activity_write_is_audited_and_links_exact_goal_and_topic(self) -> None:
        goal = self.goals.create_goal("ML foundations")
        result = self.activities.log_activity(
            "Read about LoRA",
            duration_minutes=45,
            goal_names=["ml foundations"],
            topic_names=["LoRA"],
            origin=ActivityOrigin.ASSISTANT.value,
        )
        self.assertEqual(result["goal_names"], "ML foundations")
        self.assertEqual(result["topic_names"], "LoRA")
        topics = self.knowledge.topics_for_goal(goal["id"])
        self.assertEqual([item["name"] for item in topics], ["LoRA"])
        with self.database.connection() as connection:
            action = connection.execute("SELECT * FROM assistant_actions WHERE entity_id = ?", (result["id"],)).fetchone()
        self.assertIsNotNone(action)
        self.assertEqual(action["outcome"], "succeeded")
        history = self.activities.list_assistant_actions(goal_id=goal["id"])
        self.assertEqual(history[0]["activity_title"], "Read about LoRA")

    def test_ambiguous_assistant_goal_reference_creates_no_activity(self) -> None:
        self.goals.create_goal("Study systems theory")
        self.goals.create_goal("Study systems practice")
        with self.assertRaises(AmbiguousReferenceError):
            self.activities.log_activity(
                "Read chapter", goal_names=["Study systems"], origin=ActivityOrigin.ASSISTANT.value
            )
        self.assertEqual(self.activities.list_activities(), [])
        with self.database.connection() as connection:
            action = connection.execute("SELECT outcome FROM assistant_actions ORDER BY created_at DESC LIMIT 1").fetchone()
        self.assertEqual(action["outcome"], "clarification")

    def test_missing_named_goal_fails_without_creating_an_activity(self) -> None:
        with self.assertRaises(AmigoServiceError):
            self.activities.log_activity(
                "Read chapter", goal_names=["missing"], origin=ActivityOrigin.ASSISTANT.value
            )
        self.assertEqual(self.activities.list_activities(), [])

    def test_material_import_copies_original_and_requires_topic_link(self) -> None:
        original_root = Path(self.temp.name) / "source"
        managed_root = Path(self.temp.name) / "materials"
        original_root.mkdir()
        managed_root.mkdir()
        original = original_root / "paper.txt"
        original.write_text("source stays unchanged", encoding="utf-8")
        with patch("app.services.WORKSPACE_DIR", managed_root):
            record = self.knowledge.import_material(original, ["Transformers"])
            imported = managed_root / record.relative_path
            self.assertTrue(imported.is_file())
            self.assertEqual(imported.read_text(encoding="utf-8"), original.read_text(encoding="utf-8"))
            self.assertEqual(record.sha256, hashlib.sha256(original.read_bytes()).hexdigest())
            self.assertEqual(original.read_text(encoding="utf-8"), "source stays unchanged")
            with self.assertRaises(AmigoServiceError):
                self.knowledge.import_material(original, [])

    def test_sqlite_uses_wal_foreign_keys_and_schema_version(self) -> None:
        with self.database.connection() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
            journal = connection.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(version, 8)
        self.assertEqual(foreign_keys, 1)
        self.assertEqual(journal.casefold(), "wal")


class ActivityCaptureParsingTests(unittest.TestCase):
    def test_explicit_activity_capture_extracts_duration_goal_and_topic(self) -> None:
        parsed = parse_activity_log_request(
            "Log activity: Read about LoRA for 45 minutes for goal ML foundations topic: LoRA"
        )
        self.assertEqual(parsed["title"], "Read about LoRA")
        self.assertEqual(parsed["duration_minutes"], 45)
        self.assertEqual(parsed["goal_names"], ["ML foundations"])
        self.assertEqual(parsed["topic_names"], ["LoRA"])

    def test_natural_capture_requires_explicit_past_work_and_duration(self) -> None:
        parsed = parse_activity_log_request("I studied probability for 30 minutes today")
        self.assertEqual(parsed["title"], "Studied probability")
        self.assertEqual(parsed["duration_minutes"], 30)
        self.assertIsNone(parse_activity_log_request("Explain probability for 30 minutes"))

    def test_agent_records_simple_activity_without_calling_ollama(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "activity.sqlite3"
            database = SqliteDatabase(db_path, copy_legacy=False)
            goal = GoalService(database).create_goal("ML foundations")
            with patch("app.agent.ActivityService", return_value=ActivityService(database)) as service, patch(
                "app.agent.requests.post"
            ) as post:
                answer = DesktopAgent().respond("Log activity: Read papers for 20 minutes for goal ML foundations", [])
            self.assertIn("Recorded activity", answer)
            self.assertIn("ML foundations", answer)
            service.assert_called_once_with()
            post.assert_not_called()
            self.assertEqual(len(ActivityService(database).list_activities(goal_id=goal["id"])), 1)


class PerformanceBudgetTests(unittest.TestCase):
    def test_indexed_progress_query_with_ten_thousand_activities(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = SqliteDatabase(Path(temp) / "perf.sqlite3", copy_legacy=False)
            goal = GoalService(database).create_goal("Benchmark goal")
            stamp = "2026-01-01T00:00:00+00:00"
            with database.connection() as connection:
                connection.executemany(
                    "INSERT INTO activities(id, title, occurred_at, duration_minutes, status, reflection, origin, created_at) VALUES (?, 'Study', ?, 10, 'completed', '', 'ui', ?)",
                    [(f"activity-{index}", stamp, stamp) for index in range(10_000)],
                )
                connection.executemany(
                    "INSERT INTO activity_goals(activity_id, goal_id) VALUES (?, ?)",
                    [(f"activity-{index}", goal["id"]) for index in range(10_000)],
                )
            query = ProgressQuery(database)
            durations: list[float] = []
            for _ in range(20):
                started = time.perf_counter()
                summary = query.goal_progress(goal["id"])
                durations.append((time.perf_counter() - started) * 1000)
            durations.sort()
            p95 = durations[int(len(durations) * 0.95) - 1]
            self.assertEqual(summary.activity_count, 10_000)
            self.assertEqual(summary.activity_minutes, 100_000)
            self.assertLess(p95, 100, f"progress query p95 was {p95:.1f} ms")


if __name__ == "__main__":
    unittest.main()
