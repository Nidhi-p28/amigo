from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import uuid
import zipfile
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import KNOWLEDGE_DIR, WORKSPACE_DIR, _is_reparse_point
from .domain import AmigoServiceError
from .knowledge import KnowledgeService
from .services import ActivityService, GoalService, _database, _text
from .storage import SqliteDatabase, _now
from .today import _utc_bounds


PROJECT_LINK_TABLES = {
    "goal": ("project_goals", "goal_id", "goals"),
    "topic": ("project_topics", "topic_id", "topics"),
    "material": ("project_materials", "material_id", "materials"),
    "note": ("project_notes", "note_id", "knowledge_notes"),
    "activity": ("project_activities", "activity_id", "activities"),
}


class ProjectService:
    def __init__(self, database: SqliteDatabase | Path | None = None) -> None:
        self.db = _database(database)

    def create_project(
        self, title: str, description: str = "", *, kind: str = "project", target_date: str | date | None = None
    ) -> dict[str, Any]:
        title = _text(title, "Project name")
        if len(title) > 200:
            raise AmigoServiceError("Project name must be 200 characters or fewer.")
        description = str(description or "").strip()
        if len(description) > 4000:
            raise AmigoServiceError("Project description must be 4,000 characters or fewer.")
        if kind not in {"project", "study"}:
            raise AmigoServiceError("Choose Project or Study Hub.")
        if target_date in (None, ""):
            target = None
        else:
            try:
                target = target_date.isoformat() if isinstance(target_date, date) else date.fromisoformat(str(target_date)).isoformat()
            except ValueError as exc:
                raise AmigoServiceError("Target date must use YYYY-MM-DD format.") from exc
        project_id, timestamp = str(uuid.uuid4()), _now()
        with self.db.connection() as connection:
            connection.execute(
                """INSERT INTO projects(id,title,description,kind,status,target_date,created_at,updated_at)
                   VALUES(?,?,?,?,'active',?,?,?)""",
                (project_id, title, description, kind, target, timestamp, timestamp),
            )
            return dict(connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone())

    def update_project(
        self, project_id: str, *, title: str, description: str, target_date: str | date | None
    ) -> dict[str, Any]:
        title = _text(title, "Project name")
        description = str(description or "").strip()
        if len(title) > 200 or len(description) > 4000:
            raise AmigoServiceError("Project details exceed the supported length.")
        if target_date in (None, ""):
            target = None
        else:
            try:
                target = target_date.isoformat() if isinstance(target_date, date) else date.fromisoformat(str(target_date)).isoformat()
            except ValueError as exc:
                raise AmigoServiceError("Target date must use YYYY-MM-DD format.") from exc
        with self.db.connection() as connection:
            if not connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
                raise AmigoServiceError("That project no longer exists.")
            connection.execute(
                "UPDATE projects SET title=?,description=?,target_date=?,updated_at=? WHERE id=?",
                (title, description, target, _now(), project_id),
            )
            return dict(connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone())

    def change_status(self, project_id: str, status: str) -> dict[str, Any]:
        if status not in {"active", "paused", "completed"}:
            raise AmigoServiceError("Project status must be active, paused, or completed.")
        with self.db.connection() as connection:
            connection.execute("UPDATE projects SET status=?,updated_at=? WHERE id=?", (status, _now(), project_id))
            row = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if not row:
                raise AmigoServiceError("That project no longer exists.")
            return dict(row)

    def list_projects(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        where = "WHERE p.status='active'" if active_only else ""
        with self.db.connection() as connection:
            rows = connection.execute(
                f"""SELECT p.*,
                       (SELECT COUNT(*) FROM project_goals x WHERE x.project_id=p.id) AS goal_count,
                       (SELECT COUNT(*) FROM project_topics x WHERE x.project_id=p.id) AS topic_count,
                       (SELECT COUNT(*) FROM project_materials x WHERE x.project_id=p.id) AS material_count,
                       (SELECT COUNT(*) FROM project_activities x WHERE x.project_id=p.id) AS activity_count
                    FROM projects p {where}
                    ORDER BY CASE p.status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 ELSE 2 END,
                             p.target_date IS NULL,p.target_date,p.updated_at DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        with self.db.connection() as connection:
            project = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if not project:
                return None
            result = dict(project)
            for kind, (link_table, fk, target_table) in PROJECT_LINK_TABLES.items():
                if kind == "goal":
                    query = f"SELECT x.id,x.title,x.status FROM {link_table} l JOIN {target_table} x ON x.id=l.{fk} WHERE l.project_id=? ORDER BY x.title COLLATE NOCASE"
                elif kind == "topic":
                    query = f"SELECT x.id,x.name AS title,'topic' AS status FROM {link_table} l JOIN {target_table} x ON x.id=l.{fk} WHERE l.project_id=? ORDER BY x.name COLLATE NOCASE"
                elif kind == "material":
                    query = f"SELECT x.id,x.title,COALESCE(s.status,'not indexed') AS status FROM {link_table} l JOIN {target_table} x ON x.id=l.{fk} LEFT JOIN material_index_state s ON s.material_id=x.id WHERE l.project_id=? ORDER BY x.title COLLATE NOCASE"
                elif kind == "note":
                    query = f"SELECT x.id,x.title,t.name AS status FROM {link_table} l JOIN {target_table} x ON x.id=l.{fk} JOIN topics t ON t.id=x.topic_id WHERE l.project_id=? ORDER BY x.title COLLATE NOCASE"
                else:
                    query = f"SELECT x.id,x.title,x.status FROM {link_table} l JOIN {target_table} x ON x.id=l.{fk} WHERE l.project_id=? ORDER BY x.occurred_at DESC LIMIT 20"
                result[kind + "s"] = [dict(row) for row in connection.execute(query, (project_id,)).fetchall()]
        return result

    def link(self, project_id: str, kind: str, entity_id: str) -> None:
        if kind not in PROJECT_LINK_TABLES:
            raise AmigoServiceError("Unsupported project link type.")
        table, fk, target = PROJECT_LINK_TABLES[kind]
        timestamp = _now()
        with self.db.connection() as connection:
            if not connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
                raise AmigoServiceError("That project no longer exists.")
            if not connection.execute(f"SELECT 1 FROM {target} WHERE id=?", (entity_id,)).fetchone():
                raise AmigoServiceError(f"That {kind} no longer exists.")
            connection.execute(
                f"INSERT OR IGNORE INTO {table}(project_id,{fk},created_at) VALUES(?,?,?)",
                (project_id, entity_id, timestamp),
            )
            connection.execute("UPDATE projects SET updated_at=? WHERE id=?", (timestamp, project_id))

    def unlink(self, project_id: str, kind: str, entity_id: str) -> None:
        if kind not in PROJECT_LINK_TABLES:
            raise AmigoServiceError("Unsupported project link type.")
        table, fk, _target = PROJECT_LINK_TABLES[kind]
        with self.db.connection() as connection:
            connection.execute(f"DELETE FROM {table} WHERE project_id=? AND {fk}=?", (project_id, entity_id))

    def assistant_context(self, project_id: str) -> str:
        project = self.get_project(project_id)
        if not project:
            raise AmigoServiceError("That project no longer exists.")
        lines = [f"Project: {project['title']}", f"Description: {project['description'] or '(none)'}"]
        for kind, label in (("goals", "Goals"), ("topics", "Topics"), ("materials", "Materials"), ("notes", "Notes")):
            names = [str(item.get("title") or item.get("name")) for item in project.get(kind, [])]
            if names:
                lines.append(f"{label}: " + ", ".join(names[:12]))
        return "\n".join(lines)


LEARNING_STATES = {"not_encountered", "introduced", "learning", "understood", "applied", "needs_revision"}


class LearningService:
    def __init__(self, database: SqliteDatabase | Path | None = None) -> None:
        self.db = _database(database)

    def topics(self) -> list[dict[str, Any]]:
        with self.db.connection() as connection:
            rows = connection.execute(
                """SELECT t.id,t.name,t.description,COALESCE(s.state,'not_encountered') AS learning_state,
                          s.rationale,s.updated_at,
                          (SELECT COUNT(*) FROM learning_evidence e WHERE e.topic_id=t.id) AS evidence_count,
                          (SELECT MAX(a.created_at) FROM learning_quiz_attempts a
                           JOIN learning_quizzes q ON q.id=a.quiz_id WHERE q.topic_id=t.id) AS last_assessed_at
                   FROM topics t LEFT JOIN topic_learning_state s ON s.topic_id=t.id
                   ORDER BY CASE COALESCE(s.state,'not_encountered')
                     WHEN 'needs_revision' THEN 0 WHEN 'learning' THEN 1 WHEN 'introduced' THEN 2
                     WHEN 'understood' THEN 3 WHEN 'applied' THEN 4 ELSE 5 END,t.name COLLATE NOCASE"""
            ).fetchall()
        return [dict(row) for row in rows]

    def set_state(self, topic_id: str, state: str, rationale: str = "") -> dict[str, Any]:
        if state not in LEARNING_STATES:
            raise AmigoServiceError("Choose a supported learning state.")
        rationale = str(rationale or "").strip()[:1000]
        timestamp, evidence_id = _now(), str(uuid.uuid4())
        with self.db.connection() as connection:
            topic = connection.execute("SELECT name FROM topics WHERE id=?", (topic_id,)).fetchone()
            if not topic:
                raise AmigoServiceError("That topic no longer exists.")
            connection.execute(
                """INSERT INTO topic_learning_state(topic_id,state,rationale,updated_at) VALUES(?,?,?,?)
                   ON CONFLICT(topic_id) DO UPDATE SET state=excluded.state,rationale=excluded.rationale,updated_at=excluded.updated_at""",
                (topic_id, state, rationale, timestamp),
            )
            connection.execute(
                """INSERT INTO learning_evidence(id,topic_id,evidence_type,summary,created_at)
                   VALUES(?,?,'state_change',?,?)""",
                (evidence_id, topic_id, f"State set to {state.replace('_',' ')}" + (f": {rationale}" if rationale else ""), timestamp),
            )
            return dict(connection.execute("SELECT * FROM topic_learning_state WHERE topic_id=?", (topic_id,)).fetchone())

    def evidence(self, topic_id: str, limit: int = 30) -> list[dict[str, Any]]:
        with self.db.connection() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM learning_evidence WHERE topic_id=? ORDER BY created_at DESC LIMIT ?",
                (topic_id, max(1, min(int(limit), 100))),
            ).fetchall()]

    def add_reflection(self, topic_id: str, content: str) -> dict[str, Any]:
        content = _text(content, "Learning reflection")
        if len(content) > 4000:
            raise AmigoServiceError("A learning reflection must be 4,000 characters or fewer.")
        evidence_id, timestamp = str(uuid.uuid4()), _now()
        with self.db.connection() as connection:
            if not connection.execute("SELECT 1 FROM topics WHERE id=?", (topic_id,)).fetchone():
                raise AmigoServiceError("That topic no longer exists.")
            connection.execute(
                "INSERT INTO learning_evidence(id,topic_id,evidence_type,summary,created_at) VALUES(?,?,'reflection',?,?)",
                (evidence_id, topic_id, content, timestamp),
            )
            connection.execute(
                """INSERT INTO topic_learning_state(topic_id,state,rationale,updated_at)
                   VALUES(?,'learning','Reflection recorded; this is evidence of work, not a mastery claim.',?)
                   ON CONFLICT(topic_id) DO NOTHING""",
                (topic_id, timestamp),
            )
        return {"id": evidence_id, "topic_id": topic_id, "summary": content, "created_at": timestamp}

    def quiz_source(self, topic_id: str, limit: int = 8) -> dict[str, Any]:
        with self.db.connection() as connection:
            topic = connection.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone()
            if not topic:
                raise AmigoServiceError("That topic no longer exists.")
            chunks = connection.execute(
                """SELECT c.title,c.body,c.source_path,c.source_locator
                   FROM knowledge_chunks c LEFT JOIN material_index_state s ON s.material_id=c.material_id
                   WHERE c.topic_id=? AND (c.note_id IS NOT NULL OR s.status='indexed')
                   ORDER BY CASE WHEN c.note_id IS NOT NULL THEN 0 ELSE 1 END,c.part_no LIMIT ?""",
                (topic_id, max(1, min(int(limit), 12))),
            ).fetchall()
        return {"topic": dict(topic), "chunks": [dict(row) for row in chunks]}

    def save_quiz(self, topic_id: str, questions: list[dict[str, Any]], sources: list[str] | None = None) -> dict[str, Any]:
        if not questions or len(questions) > 12:
            raise AmigoServiceError("A quiz needs between 1 and 12 questions.")
        clean: list[dict[str, Any]] = []
        for question in questions:
            prompt = str(question.get("question") or "").strip()
            options = [str(value).strip() for value in question.get("options", [])]
            answer = question.get("answer_index")
            explanation = str(question.get("explanation") or "").strip()
            if not prompt or len(options) < 2 or len(options) > 6:
                raise AmigoServiceError("Quiz questions must have a prompt and 2 to 6 answer choices.")
            if not isinstance(answer, int) or not 0 <= answer < len(options):
                raise AmigoServiceError("A quiz answer key was invalid. No quiz was saved.")
            clean.append({"question": prompt[:1000], "options": options, "answer_index": answer, "explanation": explanation[:1500]})
        quiz_id, timestamp = str(uuid.uuid4()), _now()
        title = f"{self._topic_name(topic_id)} · quick quiz"
        with self.db.connection() as connection:
            connection.execute(
                """INSERT INTO learning_quizzes(id,topic_id,title,questions_json,sources_json,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (quiz_id, topic_id, title, json.dumps(clean, ensure_ascii=False), json.dumps(sources or [], ensure_ascii=False), timestamp),
            )
        return {"id": quiz_id, "topic_id": topic_id, "title": title, "questions": clean, "sources": sources or []}

    def _topic_name(self, topic_id: str) -> str:
        with self.db.connection() as connection:
            row = connection.execute("SELECT name FROM topics WHERE id=?", (topic_id,)).fetchone()
        if not row:
            raise AmigoServiceError("That topic no longer exists.")
        return str(row["name"])

    def record_attempt(self, quiz_id: str, answers: Iterable[int | None]) -> dict[str, Any]:
        with self.db.connection() as connection:
            quiz = connection.execute("SELECT * FROM learning_quizzes WHERE id=?", (quiz_id,)).fetchone()
        if not quiz:
            raise AmigoServiceError("That quiz no longer exists.")
        questions = json.loads(str(quiz["questions_json"]))
        selected = list(answers)
        if len(selected) != len(questions):
            raise AmigoServiceError("Answer every question before finishing the quiz.")
        normalized: list[int | None] = []
        score = 0
        for index, (answer, question) in enumerate(zip(selected, questions)):
            if answer is not None and (not isinstance(answer, int) or not 0 <= answer < len(question["options"])):
                raise AmigoServiceError(f"Answer {index + 1} is invalid.")
            normalized.append(answer)
            score += int(answer == question["answer_index"])
        total, ratio = len(questions), score / max(1, len(questions))
        attempt_id, timestamp, evidence_id = str(uuid.uuid4()), _now(), str(uuid.uuid4())
        summary = f"Quiz result: {score}/{total} correct ({round(ratio * 100)}%)."
        with self.db.connection() as connection:
            connection.execute(
                """INSERT INTO learning_quiz_attempts(id,quiz_id,score,total,answers_json,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (attempt_id, quiz_id, score, total, json.dumps(normalized), timestamp),
            )
            connection.execute(
                """INSERT INTO learning_evidence(id,topic_id,evidence_type,summary,score,source_id,created_at)
                   VALUES(?,?,'quiz',?,?,?,?)""",
                (evidence_id, quiz["topic_id"], summary, ratio, attempt_id, timestamp),
            )
            if ratio < 0.65:
                topic_id = str(quiz["topic_id"])
                connection.execute(
                    """INSERT INTO topic_learning_state(topic_id,state,rationale,updated_at)
                       VALUES(?,'needs_revision',?,?)
                       ON CONFLICT(topic_id) DO UPDATE SET state='needs_revision',rationale=excluded.rationale,updated_at=excluded.updated_at""",
                    (topic_id, f"Recent quiz evidence was {score}/{total}; review the source before reassessing.", timestamp),
                )
            return {"id": attempt_id, "score": score, "total": total, "ratio": ratio, "answers": normalized, "questions": questions}

    def recommendations(self) -> list[dict[str, Any]]:
        timestamp = _now()
        with self.db.connection() as connection:
            topics = connection.execute(
                """SELECT t.id,t.name,COALESCE(s.state,'not_encountered') AS state,
                          s.updated_at AS state_updated_at,
                          (SELECT score FROM learning_evidence e WHERE e.topic_id=t.id AND e.evidence_type='quiz'
                           ORDER BY e.created_at DESC LIMIT 1) AS latest_score,
                          (SELECT created_at FROM learning_evidence e WHERE e.topic_id=t.id
                           ORDER BY e.created_at DESC LIMIT 1) AS last_evidence
                   FROM topics t LEFT JOIN topic_learning_state s ON s.topic_id=t.id
                   ORDER BY t.name COLLATE NOCASE"""
            ).fetchall()
            for topic in topics:
                score = topic["latest_score"]
                state = str(topic["state"])
                if score is not None and float(score) < 0.65:
                    kind, reason = "review", f"Recent quiz evidence was {round(float(score) * 100)}%. Revisit the source and try again."
                elif state in {"introduced", "learning"}:
                    kind, reason = "assess", "Check what you can explain before moving to the next topic."
                elif state == "understood":
                    kind, reason = "apply", "Try a small exercise or project to demonstrate application."
                else:
                    continue
                previous = connection.execute(
                    """SELECT status,updated_at FROM learning_recommendations
                       WHERE topic_id=? AND recommendation_type=? ORDER BY created_at DESC LIMIT 1""",
                    (topic["id"], kind),
                ).fetchone()
                if previous:
                    if str(previous["status"]) in {"pending", "accepted"}:
                        continue
                    latest_signal = max(
                        str(topic["last_evidence"] or ""), str(topic["state_updated_at"] or "")
                    )
                    if latest_signal <= str(previous["updated_at"]):
                        continue
                if not previous or str(previous["status"]) not in {"pending", "accepted"}:
                    connection.execute(
                        """INSERT INTO learning_recommendations(id,topic_id,recommendation_type,reason,status,created_at,updated_at)
                           VALUES(?,?,?,?,'pending',?,?)""",
                        (str(uuid.uuid4()), topic["id"], kind, reason, timestamp, timestamp),
                    )
            rows = connection.execute(
                """SELECT r.*,t.name AS topic_name FROM learning_recommendations r JOIN topics t ON t.id=r.topic_id
                   WHERE r.status IN ('pending','accepted') ORDER BY r.status,r.created_at DESC LIMIT 30"""
            ).fetchall()
        return [dict(row) for row in rows]

    def update_recommendation(self, recommendation_id: str, status: str) -> None:
        if status not in {"accepted", "postponed", "dismissed"}:
            raise AmigoServiceError("Choose accept, postpone, or dismiss.")
        with self.db.connection() as connection:
            connection.execute(
                "UPDATE learning_recommendations SET status=?,updated_at=? WHERE id=?",
                (status, _now(), recommendation_id),
            )


class ProfileService:
    def __init__(self, database: SqliteDatabase | Path | None = None) -> None:
        self.db = _database(database)

    def get_profile(self) -> dict[str, Any]:
        with self.db.connection() as connection:
            connection.execute("INSERT OR IGNORE INTO user_profile(id,updated_at) VALUES(1,?)", (_now(),))
            return dict(connection.execute("SELECT * FROM user_profile WHERE id=1").fetchone())

    def update_profile(self, **values: str) -> dict[str, Any]:
        allowed = {"name", "bio", "current_context", "aspirations", "interests", "response_style"}
        if set(values) - allowed:
            raise AmigoServiceError("Unsupported profile field.")
        limits = {"name": 100, "bio": 2000, "current_context": 4000, "aspirations": 4000, "interests": 2000, "response_style": 1000}
        cleaned: dict[str, str] = {}
        for key in allowed:
            value = str(values.get(key, "") or "").strip()
            if len(value) > limits[key]:
                raise AmigoServiceError(f"{key.replace('_',' ').title()} is too long.")
            cleaned[key] = value
        with self.db.connection() as connection:
            connection.execute("INSERT OR IGNORE INTO user_profile(id,updated_at) VALUES(1,?)", (_now(),))
            connection.execute(
                """UPDATE user_profile SET name=?,bio=?,current_context=?,aspirations=?,interests=?,response_style=?,updated_at=?
                   WHERE id=1""",
                (*[cleaned[key] for key in ("name", "bio", "current_context", "aspirations", "interests", "response_style")], _now()),
            )
            return dict(connection.execute("SELECT * FROM user_profile WHERE id=1").fetchone())

    def propose_memory(self, content: str, source: str = "user") -> dict[str, Any]:
        content = _text(content, "Memory")
        if len(content) > 1000:
            raise AmigoServiceError("A memory must be 1,000 characters or fewer.")
        memory_id, timestamp = str(uuid.uuid4()), _now()
        with self.db.connection() as connection:
            connection.execute(
                "INSERT INTO personal_memories(id,content,source,status,created_at) VALUES(?,?,?,'pending',?)",
                (memory_id, content, source[:100], timestamp),
            )
            return dict(connection.execute("SELECT * FROM personal_memories WHERE id=?", (memory_id,)).fetchone())

    def memories(self, *, include_rejected: bool = False) -> list[dict[str, Any]]:
        where = "" if include_rejected else "WHERE status != 'rejected'"
        with self.db.connection() as connection:
            return [dict(row) for row in connection.execute(
                f"SELECT * FROM personal_memories {where} ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END,created_at DESC"
            ).fetchall()]

    def decide_memory(self, memory_id: str, *, approve: bool) -> dict[str, Any]:
        status = "approved" if approve else "rejected"
        with self.db.connection() as connection:
            connection.execute(
                "UPDATE personal_memories SET status=?,approved_at=? WHERE id=?",
                (status, _now() if approve else None, memory_id),
            )
            row = connection.execute("SELECT * FROM personal_memories WHERE id=?", (memory_id,)).fetchone()
            if not row:
                raise AmigoServiceError("That memory proposal no longer exists.")
            return dict(row)

    def update_memory(self, memory_id: str, content: str) -> dict[str, Any]:
        content = _text(content, "Memory")
        if len(content) > 1000:
            raise AmigoServiceError("A memory must be 1,000 characters or fewer.")
        with self.db.connection() as connection:
            row = connection.execute("SELECT * FROM personal_memories WHERE id=?", (memory_id,)).fetchone()
            if not row:
                raise AmigoServiceError("That memory proposal no longer exists.")
            if str(row["content"]) != content:
                # Changed content goes back through user approval before entering context.
                connection.execute(
                    "UPDATE personal_memories SET content=?,status='pending',approved_at=NULL,created_at=? WHERE id=?",
                    (content, _now(), memory_id),
                )
            return dict(connection.execute("SELECT * FROM personal_memories WHERE id=?", (memory_id,)).fetchone())

    def delete_memory(self, memory_id: str) -> None:
        with self.db.connection() as connection:
            connection.execute("DELETE FROM personal_memories WHERE id=?", (memory_id,))

    def approved_context(self, *, maximum: int = 1800) -> str:
        profile = self.get_profile()
        lines = []
        for key, label in (("name", "Name"), ("bio", "Bio"), ("current_context", "Current context"), ("aspirations", "Aspirations"), ("interests", "Interests"), ("response_style", "Response preference")):
            if profile.get(key):
                lines.append(f"{label}: {profile[key]}")
        with self.db.connection() as connection:
            memories = connection.execute(
                "SELECT content FROM personal_memories WHERE status='approved' ORDER BY approved_at DESC LIMIT 12"
            ).fetchall()
        if memories:
            lines.append("User-approved memories: " + "; ".join(str(row["content"]) for row in memories))
        return "\n".join(lines)[:maximum]


class PersonalFeatures:
    """Derived summaries, discoveries, search, and user-triggered data export."""

    def __init__(
        self,
        database: SqliteDatabase | Path | None = None,
        *,
        knowledge_root: Path | None = None,
        materials_root: Path | None = None,
    ) -> None:
        self.db = _database(database)
        self.knowledge = KnowledgeService(self.db, knowledge_root=knowledge_root, materials_root=materials_root)

    def weekly_review(self, on: date | None = None) -> dict[str, Any]:
        today = on or date.today()
        monday = today - timedelta(days=today.weekday())
        start, _ = _utc_bounds(monday)
        _, end = _utc_bounds(today + timedelta(days=1) - timedelta(days=1))
        with self.db.connection() as connection:
            activities = connection.execute(
                """SELECT COUNT(*) AS count,COALESCE(SUM(duration_minutes),0) AS minutes
                   FROM activities WHERE occurred_at>=? AND occurred_at<?""", (start, end)
            ).fetchone()
            rows = connection.execute(
                """SELECT a.title,a.occurred_at,a.duration_minutes,GROUP_CONCAT(DISTINCT g.title) AS goals
                   FROM activities a LEFT JOIN activity_goals ag ON ag.activity_id=a.id
                   LEFT JOIN goals g ON g.id=ag.goal_id WHERE a.occurred_at>=? AND a.occurred_at<?
                   GROUP BY a.id ORDER BY a.occurred_at DESC LIMIT 20""", (start, end)
            ).fetchall()
            milestones = connection.execute(
                """SELECT m.title,g.title AS goal_title,m.completed_at FROM milestones m JOIN goals g ON g.id=m.goal_id
                   WHERE m.completed_at>=? AND m.completed_at<? ORDER BY m.completed_at DESC""", (start, end)
            ).fetchall()
            goals_advanced = connection.execute(
                """SELECT g.title,COUNT(DISTINCT a.id) AS activity_count
                   FROM activities a JOIN activity_goals ag ON ag.activity_id=a.id
                   JOIN goals g ON g.id=ag.goal_id WHERE a.occurred_at>=? AND a.occurred_at<?
                   GROUP BY g.id ORDER BY activity_count DESC,g.title COLLATE NOCASE""", (start, end)
            ).fetchall()
            topics_studied = connection.execute(
                """SELECT t.name,COUNT(DISTINCT a.id) AS activity_count
                   FROM activities a JOIN activity_topics at ON at.activity_id=a.id
                   JOIN topics t ON t.id=at.topic_id WHERE a.occurred_at>=? AND a.occurred_at<?
                   GROUP BY t.id ORDER BY activity_count DESC,t.name COLLATE NOCASE""", (start, end)
            ).fetchall()
            upcoming = connection.execute(
                """SELECT m.title,g.title AS goal_title,m.target_date FROM milestones m
                   JOIN goals g ON g.id=m.goal_id WHERE g.status='active' AND m.status='pending'
                   AND m.target_date>=? AND m.target_date<=? ORDER BY m.target_date LIMIT 12""",
                (today.isoformat(), (today + timedelta(days=7)).isoformat()),
            ).fetchall()
        summary_lines = [f"Week of {monday.strftime('%B %d')}: {int(activities['count'])} actual activities, {int(activities['minutes'])} logged minutes."]
        if milestones:
            summary_lines.append("Milestones completed: " + ", ".join(f"{row['title']} ({row['goal_title']})" for row in milestones))
        if rows:
            summary_lines.append("Recent work: " + "; ".join(str(row["title"]) for row in rows[:8]))
        else:
            summary_lines.append("No activity records yet this week.")
        if goals_advanced:
            summary_lines.append("Goals advanced: " + ", ".join(f"{row['title']} ({row['activity_count']} activities)" for row in goals_advanced))
        if topics_studied:
            summary_lines.append("Topics practiced: " + ", ".join(f"{row['name']} ({row['activity_count']})" for row in topics_studied))
        if upcoming:
            summary_lines.append("Coming up: " + "; ".join(f"{row['title']} · {row['goal_title']} · {row['target_date']}" for row in upcoming))
        return {"week_start": monday.isoformat(), "activity_count": int(activities["count"]),
                "activity_minutes": int(activities["minutes"]), "activities": [dict(row) for row in rows],
                "milestones": [dict(row) for row in milestones], "goals_advanced": [dict(row) for row in goals_advanced],
                "topics_studied": [dict(row) for row in topics_studied], "upcoming": [dict(row) for row in upcoming],
                "summary": "\n\n".join(summary_lines)}

    def replanning_suggestions(self, on: date | None = None) -> list[dict[str, Any]]:
        today = on or date.today()
        horizon = (today + timedelta(days=7)).isoformat()
        with self.db.connection() as connection:
            rows = connection.execute(
                """SELECT m.id,m.title,m.target_date,g.id AS goal_id,g.title AS goal_title,g.priority,
                          (SELECT COUNT(*) FROM planned_sessions s JOIN planned_session_goals sg ON sg.session_id=s.id
                           WHERE sg.goal_id=g.id AND s.status='scheduled' AND date(s.starts_at)<=?) AS planned_sessions
                   FROM milestones m JOIN goals g ON g.id=m.goal_id
                   WHERE g.status='active' AND m.status='pending' AND m.target_date IS NOT NULL AND m.target_date<=?
                   ORDER BY m.target_date,g.priority LIMIT 20""", (horizon, horizon)
            ).fetchall()
        suggestions = []
        for row in rows:
            overdue = str(row["target_date"]) < today.isoformat()
            reason = (f"{row['title']} for {row['goal_title']} is overdue." if overdue
                      else f"{row['title']} for {row['goal_title']} is due {row['target_date']}.")
            if not row["planned_sessions"]:
                reason += " Consider scheduling a work session."
            else:
                reason += " Review the remaining time and adjust the plan if needed."
            suggestions.append({"milestone_id": row["id"], "goal_id": row["goal_id"],
                                "target_date": row["target_date"], "reason": reason})
        return suggestions

    def curiosity_prompt(self, exclude_note_id: str | None = None, exclude_topic_id: str | None = None) -> dict[str, Any] | None:
        with self.db.connection() as connection:
            row = connection.execute(
                """SELECT t.id AS topic_id,t.name,n.id AS note_id,n.title,n.updated_at
                   FROM knowledge_notes n JOIN topics t ON t.id=n.topic_id
                   WHERE (? IS NULL OR n.id != ?) AND (? IS NULL OR t.id != ?)
                   ORDER BY n.updated_at DESC,n.id LIMIT 1"""
                , (exclude_note_id, exclude_note_id, exclude_topic_id, exclude_topic_id)
            ).fetchone()
            if not row:
                row = connection.execute(
                    """SELECT t.id AS topic_id,t.name,NULL AS note_id,t.name AS title,NULL AS updated_at
                       FROM topics t WHERE (? IS NULL OR t.id != ?)
                       ORDER BY t.name COLLATE NOCASE LIMIT 1""", (exclude_topic_id, exclude_topic_id)
                ).fetchone()
        if not row:
            return None
        return {"topic_id": row["topic_id"], "topic_name": row["name"], "note_id": row["note_id"],
                "note_title": row["title"], "prompt": f"What is one question you still have about {row['name']}?"}

    def save_discovery(self, title: str, content: str, *, topic_id: str | None = None, source_note_id: str | None = None) -> dict[str, Any]:
        title, content = _text(title, "Discovery title"), _text(content, "Discovery")
        if len(title) > 200 or len(content) > 5000:
            raise AmigoServiceError("Discovery is too long to save.")
        topic_name = "General"
        if topic_id:
            with self.db.connection() as connection:
                topic = connection.execute("SELECT name FROM topics WHERE id=?", (topic_id,)).fetchone()
            if not topic:
                raise AmigoServiceError("That topic no longer exists.")
            topic_name = str(topic["name"])
        note = self.knowledge.save_note(title, topic_name, content)
        record_id, timestamp = str(uuid.uuid4()), _now()
        with self.db.connection() as connection:
            connection.execute(
                "INSERT INTO saved_discoveries(id,title,content,topic_id,source_note_id,created_at) VALUES(?,?,?,?,?,?)",
                (record_id, title, content, topic_id, source_note_id or note["id"], timestamp),
            )
            return dict(connection.execute("SELECT * FROM saved_discoveries WHERE id=?", (record_id,)).fetchone())

    def saved_discoveries(self) -> list[dict[str, Any]]:
        with self.db.connection() as connection:
            return [dict(row) for row in connection.execute(
                """SELECT d.*,t.name AS topic_name FROM saved_discoveries d LEFT JOIN topics t ON t.id=d.topic_id
                   ORDER BY d.created_at DESC"""
            ).fetchall()]

    def global_search(self, query: str, limit: int = 30) -> list[dict[str, Any]]:
        query = str(query or "").strip()
        if len(query) < 2:
            return []
        limit = max(1, min(int(limit), 100))
        like = f"%{query.replace('%', '')}%"
        results: list[dict[str, Any]] = []
        with self.db.connection() as connection:
            for kind, table, title_col, subtitle in (
                ("goal", "goals", "title", "description"),
                ("project", "projects", "title", "description"),
                ("activity", "activities", "title", "reflection"),
                ("topic", "topics", "name", "description"),
            ):
                rows = connection.execute(
                    f"SELECT id,{title_col} AS title,{subtitle} AS subtitle FROM {table} WHERE {title_col} LIKE ? COLLATE NOCASE OR {subtitle} LIKE ? COLLATE NOCASE LIMIT ?",
                    (like, like, limit),
                ).fetchall()
                results.extend({"kind": kind, **dict(row)} for row in rows)
            rows = connection.execute(
                """SELECT id,title,relative_path AS subtitle FROM materials WHERE title LIKE ? COLLATE NOCASE LIMIT ?""",
                (like, limit),
            ).fetchall()
            results.extend({"kind": "material", **dict(row)} for row in rows)
            rows = connection.execute(
                """SELECT n.id,n.title,n.relative_path AS subtitle,t.name AS topic_name FROM knowledge_notes n
                   JOIN topics t ON t.id=n.topic_id WHERE n.title LIKE ? COLLATE NOCASE OR t.name LIKE ? COLLATE NOCASE LIMIT ?""",
                (like, like, limit),
            ).fetchall()
            results.extend({"kind": "note", **dict(row)} for row in rows)
            rows = connection.execute(
                """SELECT id,title,content AS subtitle FROM saved_discoveries
                   WHERE title LIKE ? COLLATE NOCASE OR content LIKE ? COLLATE NOCASE LIMIT ?""",
                (like, like, limit),
            ).fetchall()
            results.extend({"kind": "discovery", **dict(row)} for row in rows)
            rows = connection.execute(
                """SELECT m.id,m.title,g.title AS subtitle,g.id AS goal_id,m.target_date
                   FROM milestones m JOIN goals g ON g.id=m.goal_id
                   WHERE m.title LIKE ? COLLATE NOCASE OR g.title LIKE ? COLLATE NOCASE LIMIT ?""",
                (like, like, limit),
            ).fetchall()
            results.extend({"kind": "milestone", **dict(row)} for row in rows)
            for kind, table in (("planned_session", "planned_sessions"), ("calendar_event", "calendar_events")):
                rows = connection.execute(
                    f"""SELECT id,title,description AS subtitle,starts_at,status FROM {table}
                        WHERE title LIKE ? COLLATE NOCASE OR description LIKE ? COLLATE NOCASE LIMIT ?""",
                    (like, like, limit),
                ).fetchall()
                results.extend({"kind": kind, **dict(row)} for row in rows)
        try:
            for result in self.knowledge.search(query, limit=min(limit, 20)):
                results.append({"kind": "passage", "id": str(result["id"]), "title": str(result["title"]),
                                "subtitle": str(result.get("source_path") or ""), "excerpt": str(result.get("excerpt") or "")})
        except (sqlite3.Error, AmigoServiceError):
            pass
        unique = []
        seen = set()
        for item in results:
            key = (item["kind"], item["id"])
            if key not in seen:
                unique.append(item)
                seen.add(key)
        return unique[:limit]

    def compare_materials(self, material_ids: Iterable[str]) -> dict[str, Any]:
        ids = list(dict.fromkeys(str(value) for value in material_ids))
        if len(ids) != 2:
            raise AmigoServiceError("Choose exactly two materials to compare.")
        with self.db.connection() as connection:
            materials = connection.execute(
                """SELECT m.id,m.title,m.relative_path,s.status,s.character_count
                   FROM materials m LEFT JOIN material_index_state s ON s.material_id=m.id WHERE m.id IN (?,?)""", ids
            ).fetchall()
            if len(materials) != 2:
                raise AmigoServiceError("One of those materials is no longer available.")
            data = []
            for material in materials:
                chunks = connection.execute(
                    """SELECT title,body,source_locator FROM knowledge_chunks WHERE material_id=? ORDER BY part_no LIMIT 8""",
                    (material["id"],),
                ).fetchall()
                data.append({**dict(material), "sections": [dict(row) for row in chunks]})
        return {"materials": data, "guidance": "Compare the research question, methodology, data, results, limitations, and contributions. Verify each point against its cited source section."}

    def export_backup(self, destination: Path | str, *, overwrite: bool = False) -> dict[str, Any]:
        requested = Path(destination).expanduser()
        parent = requested.parent.resolve()
        target = parent / requested.name
        if target.suffix.casefold() != ".zip":
            raise AmigoServiceError("Choose a .zip file for the Amigo backup.")
        if target == self.db.path.resolve():
            raise AmigoServiceError("Choose a backup file separate from Amigo's database.")
        if _is_reparse_point(target) or (target.exists() and target.stat().st_nlink > 1):
            raise AmigoServiceError("The backup destination cannot be a link or hard-linked file.")
        if target.exists() and not overwrite:
            raise AmigoServiceError("That backup file already exists. Confirm replacement first.")
        roots = [(Path(KNOWLEDGE_DIR), "Knowledge"), (Path(WORKSPACE_DIR), "My Materials")]
        for root, _label in roots:
            resolved_root = root.expanduser().resolve()
            if target == resolved_root or resolved_root in target.parents:
                raise AmigoServiceError("Save the backup outside Amigo's managed folders.")
        parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        reservation_created = False
        try:
            descriptor, temporary_name = tempfile.mkstemp(prefix=".amigo-backup-", suffix=".tmp", dir=parent)
            os.close(descriptor)
            temporary = Path(temporary_name)
            with temporary.open("wb") as output, zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                with closing(sqlite3.connect(self.db.path)) as source, closing(sqlite3.connect(":memory:")) as snapshot:
                    source.backup(snapshot)
                    archive.writestr("amigo.sqlite3", snapshot.serialize())
                for root, archive_name in roots:
                    if not root.exists() or _is_reparse_point(root):
                        continue
                    for current, directories, files in os.walk(root, followlinks=False):
                        current_path = Path(current)
                        directories[:] = [name for name in directories if not _is_reparse_point(current_path / name)]
                        for name in files:
                            source_path = current_path / name
                            if _is_reparse_point(source_path):
                                continue
                            relative = source_path.relative_to(root)
                            archive.write(source_path, f"{archive_name}/{relative.as_posix()}")
            if overwrite:
                if _is_reparse_point(target) or (target.exists() and target.stat().st_nlink > 1):
                    raise AmigoServiceError("The backup destination changed to a link or hard-linked file.")
                os.replace(temporary, target)
            else:
                with target.open("xb"):
                    reservation_created = True
                os.replace(temporary, target)
                reservation_created = False
        except Exception:
            if reservation_created:
                target.unlink(missing_ok=True)
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        return {"path": str(target), "size_bytes": target.stat().st_size}


class FocusSessionService:
    def __init__(self, database: SqliteDatabase | Path | None = None) -> None:
        self.db = _database(database)

    def start(
        self, title: str, planned_minutes: int, *, goal_id: str | None = None,
        topic_id: str | None = None, project_id: str | None = None,
    ) -> dict[str, Any]:
        title = _text(title, "Focus session name")
        try:
            planned_minutes = int(planned_minutes)
        except (TypeError, ValueError) as exc:
            raise AmigoServiceError("Focus time must be a whole number of minutes.") from exc
        if not 1 <= planned_minutes <= 240:
            raise AmigoServiceError("Focus time must be between 1 and 240 minutes.")
        session_id, timestamp = str(uuid.uuid4()), _now()
        with self.db.connection() as connection:
            for entity_id, table, label in ((goal_id, "goals", "goal"), (topic_id, "topics", "topic"), (project_id, "projects", "project")):
                if entity_id and not connection.execute(f"SELECT 1 FROM {table} WHERE id=?", (entity_id,)).fetchone():
                    raise AmigoServiceError(f"That {label} no longer exists.")
            connection.execute(
                """INSERT INTO focus_sessions(id,title,started_at,planned_minutes,status,goal_id,topic_id,project_id,created_at)
                   VALUES(?,?,?,?,'running',?,?,?,?)""",
                (session_id, title, timestamp, planned_minutes, goal_id, topic_id, project_id, timestamp),
            )
            return dict(connection.execute("SELECT * FROM focus_sessions WHERE id=?", (session_id,)).fetchone())

    def finish(self, session_id: str, *, cancel: bool = False) -> dict[str, Any]:
        ended = datetime.now(timezone.utc)
        timestamp = ended.isoformat()
        with self.db.connection() as connection:
            session = connection.execute("SELECT * FROM focus_sessions WHERE id=?", (session_id,)).fetchone()
            if not session:
                raise AmigoServiceError("That focus session no longer exists.")
            if session["status"] != "running":
                return dict(session)
            if cancel:
                connection.execute(
                    "UPDATE focus_sessions SET status='cancelled',ended_at=? WHERE id=?", (timestamp, session_id)
                )
                return dict(connection.execute("SELECT * FROM focus_sessions WHERE id=?", (session_id,)).fetchone())
            started = datetime.fromisoformat(str(session["started_at"]))
            elapsed = max(1, min(1440, int((ended - started).total_seconds() // 60)))
            actual_status = "completed" if elapsed >= int(session["planned_minutes"]) else "partial"
            activity_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO activities(id,title,occurred_at,duration_minutes,status,reflection,origin,created_at)
                   VALUES(?,?,?,?,?,'','ui',?)""",
                (activity_id, session["title"], session["started_at"], elapsed, actual_status, timestamp),
            )
            if session["goal_id"]:
                connection.execute("INSERT INTO activity_goals(activity_id,goal_id) VALUES(?,?)", (activity_id, session["goal_id"]))
                connection.execute("UPDATE goals SET updated_at=? WHERE id=?", (timestamp, session["goal_id"]))
            if session["topic_id"]:
                connection.execute("INSERT INTO activity_topics(activity_id,topic_id) VALUES(?,?)", (activity_id, session["topic_id"]))
                if session["goal_id"]:
                    connection.execute(
                        "INSERT OR IGNORE INTO goal_topics(goal_id,topic_id,created_at) VALUES(?,?,?)",
                        (session["goal_id"], session["topic_id"], timestamp),
                    )
            if session["project_id"]:
                connection.execute("INSERT INTO project_activities(project_id,activity_id,created_at) VALUES(?,?,?)", (session["project_id"], activity_id, timestamp))
            if session["topic_id"]:
                connection.execute(
                    """INSERT INTO learning_evidence(id,topic_id,evidence_type,summary,source_id,created_at)
                       VALUES(?,?,'activity',?,?,?)""",
                    (str(uuid.uuid4()), session["topic_id"], f"Focus session: {session['title']} ({elapsed} min).", activity_id, timestamp),
                )
            connection.execute(
                "UPDATE focus_sessions SET status='completed',ended_at=?,activity_id=? WHERE id=?",
                (timestamp, activity_id, session_id),
            )
            return dict(connection.execute("SELECT * FROM focus_sessions WHERE id=?", (session_id,)).fetchone())

