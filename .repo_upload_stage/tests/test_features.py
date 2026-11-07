from __future__ import annotations

import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.domain import AmigoServiceError
from app.features import (
    FocusSessionService,
    LearningService,
    PersonalFeatures,
    ProfileService,
    ProjectService,
)
from app.services import GoalService, KnowledgeLinkService, ProgressQuery
from app.storage import SqliteDatabase
from app.today import TodayService


class ExtendedFeatureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = SqliteDatabase(self.root / "amigo.sqlite3", copy_legacy=False)
        self.goals = GoalService(self.database)
        self.links = KnowledgeLinkService(self.database)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _goal_and_topic(self) -> tuple[dict, str]:
        goal = self.goals.create_goal("Study calculus")
        topic = self.links.link_topic_to_goal(goal["id"], "Derivatives")
        return goal, topic["topic_id"]

    def test_today_checklist_completion_creates_actual_activity_once(self) -> None:
        goal, topic_id = self._goal_and_topic()
        project = ProjectService(self.database).create_project("Calculus study", kind="study")
        today = TodayService(self.database)
        planned = today.add_activity_item(
            "Work through examples", goal_id=goal["id"], topic_id=topic_id, project_id=project["id"]
        )

        first = today.complete_activity_item(planned["id"])
        second = today.complete_activity_item(planned["id"])

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(1, ProgressQuery(self.database).goal_progress(goal["id"]).activity_count)
        self.assertEqual("completed", today.activity_items(planned["activity_date"])[0]["status"])
        with self.database.connection() as connection:
            self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM project_activities WHERE project_id=?", (project["id"],)).fetchone()[0])
            self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM learning_evidence WHERE topic_id=? AND evidence_type='activity'", (topic_id,)).fetchone()[0])

    def test_learning_quiz_results_drive_evidence_and_review_state(self) -> None:
        _goal, topic_id = self._goal_and_topic()
        learning = LearningService(self.database)
        learning.set_state(topic_id, "learning", "Working through examples")
        quiz = learning.save_quiz(topic_id, [{
            "question": "What is the derivative of x²?", "options": ["2x", "x"], "answer_index": 0,
            "explanation": "Apply the power rule.",
        }])

        attempt = learning.record_attempt(quiz["id"], [1])

        self.assertEqual(0, attempt["score"])
        self.assertEqual("needs_revision", learning.topics()[0]["learning_state"])
        self.assertTrue(any(row["recommendation_type"] == "review" for row in learning.recommendations()))
        self.assertGreaterEqual(len(learning.evidence(topic_id)), 2)

    def test_learning_recommendation_actions_persist_until_new_evidence(self) -> None:
        _goal, topic_id = self._goal_and_topic()
        learning = LearningService(self.database)
        learning.set_state(topic_id, "learning")

        initial = learning.recommendations()
        self.assertEqual(1, len(initial))
        recommendation_id = initial[0]["id"]

        learning.update_recommendation(recommendation_id, "accepted")
        accepted = learning.recommendations()
        self.assertEqual([recommendation_id], [row["id"] for row in accepted])
        self.assertEqual("accepted", accepted[0]["status"])

        learning.update_recommendation(recommendation_id, "postponed")
        self.assertEqual([], learning.recommendations())
        self.assertEqual([], learning.recommendations())

        learning.add_reflection(topic_id, "I can explain the chain rule now.")
        refreshed = learning.recommendations()
        self.assertEqual(1, len(refreshed))
        self.assertNotEqual(recommendation_id, refreshed[0]["id"])

    def test_memory_edit_returns_approved_text_to_pending_review(self) -> None:
        profile = ProfileService(self.database)
        memory = profile.propose_memory("I prefer short answers")
        self.assertEqual("pending", memory["status"])
        self.assertNotIn("short answers", profile.approved_context())
        profile.decide_memory(memory["id"], approve=True)
        self.assertIn("short answers", profile.approved_context())

        edited = profile.update_memory(memory["id"], "I prefer concise, structured answers")

        self.assertEqual("pending", edited["status"])
        self.assertNotIn("concise", profile.approved_context())
        profile.decide_memory(memory["id"], approve=True)
        self.assertIn("concise", profile.approved_context())

    def test_focus_session_completion_records_actual_work_and_links(self) -> None:
        goal, topic_id = self._goal_and_topic()
        project = ProjectService(self.database).create_project("Practice set")
        sessions = FocusSessionService(self.database)
        session = sessions.start("Solve problems", 25, goal_id=goal["id"], topic_id=topic_id, project_id=project["id"])
        started = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
        with self.database.connection() as connection:
            connection.execute("UPDATE focus_sessions SET started_at=? WHERE id=?", (started, session["id"]))

        result = sessions.finish(session["id"])

        self.assertEqual("completed", result["status"])
        self.assertIsNotNone(result["activity_id"])
        self.assertEqual(1, ProgressQuery(self.database).goal_progress(goal["id"]).activity_count)
        with self.database.connection() as connection:
            activity = connection.execute("SELECT duration_minutes,status FROM activities WHERE id=?", (result["activity_id"],)).fetchone()
            self.assertEqual("completed", activity["status"])
            self.assertGreaterEqual(activity["duration_minutes"], 30)
            self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM project_activities WHERE project_id=?", (project["id"],)).fetchone()[0])

    def test_discovery_is_saved_as_searchable_knowledge_and_opens_as_saved_record(self) -> None:
        _goal, topic_id = self._goal_and_topic()
        features = PersonalFeatures(
            self.database, knowledge_root=self.root / "Knowledge", materials_root=self.root / "Materials"
        )
        saved = features.save_discovery("Curiosity · Derivatives", "Why does the chain rule work?", topic_id=topic_id)

        results = features.global_search("chain rule")

        self.assertIn(saved["id"], {row["id"] for row in features.saved_discoveries()})
        self.assertTrue(any(row["kind"] == "discovery" and row["id"] == saved["id"] for row in results))
        self.assertTrue(any(row["kind"] == "passage" for row in results))

    def test_backup_contains_database_and_managed_user_materials_and_refuses_managed_path(self) -> None:
        knowledge_root = self.root / "Knowledge"
        materials_root = self.root / "Materials"
        knowledge_root.mkdir()
        materials_root.mkdir()
        (knowledge_root / "note.md").write_text("A useful note", encoding="utf-8")
        (materials_root / "handout.txt").write_text("An imported handout", encoding="utf-8")
        backup = self.root / "amigo-export.zip"
        features = PersonalFeatures(self.database)

        with patch("app.features.KNOWLEDGE_DIR", knowledge_root), patch("app.features.WORKSPACE_DIR", materials_root):
            result = features.export_backup(backup)
            with zipfile.ZipFile(backup) as archive:
                self.assertEqual(b"A useful note", archive.read("Knowledge/note.md"))
                self.assertEqual(b"An imported handout", archive.read("My Materials/handout.txt"))
                self.assertIn("amigo.sqlite3", archive.namelist())
            self.assertGreater(result["size_bytes"], 0)
            with self.assertRaises(AmigoServiceError):
                features.export_backup(knowledge_root / "nested" / "bad.zip")


if __name__ == "__main__":
    unittest.main()
