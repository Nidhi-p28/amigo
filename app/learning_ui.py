from __future__ import annotations

import json

import requests
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QInputDialog,
)

from .config import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_NUM_CTX
from .domain import AmigoServiceError
from .features import LearningService
from .storage import SqliteDatabase
from .theme import PRODUCT_STYLE


QUIZ_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "minItems": 5,
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "options": {
                        "type": "array",
                        "minItems": 4,
                        "maxItems": 4,
                        "items": {"type": "string"},
                    },
                    "answer_index": {"type": "integer", "minimum": 0, "maximum": 3},
                    "explanation": {"type": "string"},
                },
                "required": ["question", "options", "answer_index", "explanation"],
            },
        },
    },
    "required": ["questions"],
}


class QuizGenerationWorker(QThread):
    created = Signal(object)
    failed = Signal(str)

    def __init__(self, database: SqliteDatabase, topic_id: str) -> None:
        super().__init__()
        self.database = database
        self.topic_id = topic_id

    @staticmethod
    def _json_object(value: str) -> dict:
        text = str(value or "").strip()
        if "</think>" in text:
            text = text.rsplit("</think>", 1)[-1].strip()
        decoder = json.JSONDecoder()
        saw_object = False
        for start, character in enumerate(text):
            if character != "{":
                continue
            saw_object = True
            try:
                parsed, _end = decoder.raw_decode(text[start:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and isinstance(parsed.get("questions"), list):
                return parsed
        if saw_object:
            raise ValueError("The local model returned an incomplete quiz. Try generating it again.")
        raise ValueError("The local model returned text instead of quiz JSON. Try generating it again.")

    def run(self) -> None:
        try:
            service = LearningService(self.database)
            source = service.quiz_source(self.topic_id, limit=4)
            chunks = source["chunks"]
            if not chunks:
                raise AmigoServiceError("Add or index a note or readable material for this topic before creating a quiz.")
            excerpts = []
            sources = []
            for chunk in chunks:
                locator = f"{chunk['source_path']} {chunk['source_locator']}".strip()
                excerpts.append(f"SOURCE: {locator}\nSECTION: {chunk['title']}\n{str(chunk['body'])[:1200]}")
                if locator and locator not in sources:
                    sources.append(locator)
            prompt = (
                f"Create 5 multiple-choice questions for the topic {source['topic']['name']!r}, using only the source excerpts below. "
                "Test understanding and application rather than copying sentences. Avoid facts that are not in the excerpts. "
                "Return exactly five questions as one JSON object. Each question must have a short question, exactly four short options, "
                "a zero-based answer_index from 0 to 3, and a brief explanation. Do not include markdown or any text outside the JSON. "
                "Every answer and explanation must be supported by the excerpts.\n\n"
                + "\n\n".join(excerpts)
            )
            response = requests.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json={"model": OLLAMA_MODEL, "stream": False, "format": QUIZ_RESPONSE_SCHEMA, "think": False,
                      "messages": [
                          {"role": "system", "content": "Create a concise quiz from only the supplied source text. Follow the required JSON schema exactly."},
                          {"role": "user", "content": prompt},
                      ],
                      "options": {"temperature": 0, "num_ctx": OLLAMA_NUM_CTX, "num_predict": 2200}},
                timeout=(5, 180),
            )
            response.raise_for_status()
            response_data = response.json()
            if response_data.get("done_reason") == "length":
                raise ValueError("The quiz response was cut off before completion. Try generating it again.")
            content = str(response_data.get("message", {}).get("content") or "")
            parsed = self._json_object(content)
            saved = service.save_quiz(self.topic_id, parsed["questions"], sources)
        except requests.RequestException as exc:
            self.failed.emit(f"Couldn't reach the local model to generate the quiz: {exc}")
        except (ValueError, json.JSONDecodeError, AmigoServiceError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"Quiz generation failed: {exc}")
        else:
            self.created.emit(saved)


class QuizTakeDialog(QDialog):
    def __init__(self, quiz: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.quiz = quiz
        self.setWindowTitle(str(quiz["title"]))
        self.setStyleSheet(PRODUCT_STYLE + """
            QScrollArea#quizQuestionScroll {
                background: #FEFEFD;
                border: 1px solid #DEDEDA;
                border-radius: 9px;
            }
            QWidget#quizQuestionContent { background: #FEFEFD; }
            QWidget#quizQuestionCard {
                background: #F4F4F2;
                border: 1px solid #E1E1DE;
                border-radius: 8px;
            }
            QLabel#quizQuestionTitle { color: #292D30; background: transparent; }
            QRadioButton#quizAnswerOption {
                color: #34383A;
                background: transparent;
                spacing: 8px;
            }
            QRadioButton#quizAnswerOption::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid #777C7E;
                border-radius: 8px;
                background: #FEFEFD;
            }
            QRadioButton#quizAnswerOption::indicator:checked {
                background: #292C2F;
                border-color: #292C2F;
            }
        """)
        self.resize(560, 520)
        self._groups: list[QButtonGroup] = []
        layout = QVBoxLayout(self)
        heading = QLabel(str(quiz["title"]))
        heading.setObjectName("pageTitle")
        layout.addWidget(heading)
        source_text = "\n".join(str(item) for item in quiz.get("sources", []))
        source = QLabel(f"Questions are generated from your indexed local sources.\n{source_text}")
        source.setObjectName("footnote")
        source.setWordWrap(True)
        layout.addWidget(source)
        scroll = QScrollArea()
        scroll.setObjectName("quizQuestionScroll")
        scroll.setWidgetResizable(True)
        question_body = QWidget()
        question_body.setObjectName("quizQuestionContent")
        question_layout = QVBoxLayout(question_body)
        for index, question in enumerate(quiz["questions"], 1):
            card = QWidget()
            card.setObjectName("quizQuestionCard")
            card_layout = QVBoxLayout(card)
            label = QLabel(f"{index}. {question['question']}")
            label.setWordWrap(True)
            label.setObjectName("quizQuestionTitle")
            card_layout.addWidget(label)
            group = QButtonGroup(card)
            group.setExclusive(True)
            for option_index, option in enumerate(question["options"]):
                radio = QRadioButton(str(option))
                radio.setObjectName("quizAnswerOption")
                radio.setProperty("option_index", option_index)
                group.addButton(radio, option_index)
                card_layout.addWidget(radio)
            self._groups.append(group)
            question_layout.addWidget(card)
        question_layout.addStretch(1)
        scroll.setWidget(question_body)
        layout.addWidget(scroll, 1)
        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)
        actions = QHBoxLayout()
        self.submit_button = QPushButton("Check answers")
        self.submit_button.setObjectName("primaryAction")
        self.submit_button.clicked.connect(self._validate_and_accept)
        self.close_button = QPushButton("Close")
        self.close_button.setObjectName("secondaryAction")
        self.close_button.clicked.connect(self.reject)
        actions.addStretch(1)
        actions.addWidget(self.close_button)
        actions.addWidget(self.submit_button)
        layout.addLayout(actions)
        self.quiz_result: dict | None = None

    def _validate_and_accept(self) -> None:
        if any(group.checkedButton() is None for group in self._groups):
            QMessageBox.information(self, "Finish the quiz", "Choose an answer for each question first.")
            return
        self.accept()

    def answers(self) -> list[int]:
        return [int(group.checkedId()) for group in self._groups]

    def show_result(self, result: dict) -> None:
        self.quiz_result = result
        lines = [f"Score: {result['score']} / {result['total']}. A quiz score is one piece of evidence, not a mastery claim."]
        for index, (question, answer) in enumerate(zip(result["questions"], result["answers"]), 1):
            correct = answer == question["answer_index"]
            lines.append(f"{index}. {'Correct' if correct else 'Review'} — {question['explanation']}")
        self.result_label.setText("\n".join(lines))
        self.submit_button.setEnabled(False)
        self.close_button.setText("Done")
        self.close_button.clicked.disconnect()
        self.close_button.clicked.connect(self.accept)


class LearningPanel(QWidget):
    def __init__(self, database: SqliteDatabase, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.database = database
        self.learning = LearningService(database)
        self._selected_topic_id: str | None = None
        self._worker: QuizGenerationWorker | None = None
        self.setStyleSheet(PRODUCT_STYLE)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)
        title = QLabel("Learning & review")
        title.setObjectName("pageTitle")
        root.addWidget(title)
        hint = QLabel("Track what you have encountered, practised, explained, or applied. Evidence informs suggestions; it does not automatically declare mastery.")
        hint.setObjectName("pageDescription")
        hint.setWordWrap(True)
        root.addWidget(hint)
        body = QHBoxLayout()
        body.setSpacing(12)
        topic_card = QFrame()
        topic_card.setObjectName("contentCard")
        topic_layout = QVBoxLayout(topic_card)
        topic_layout.setContentsMargins(12, 12, 12, 12)
        section = QLabel("Topics")
        section.setObjectName("sectionTitle")
        topic_layout.addWidget(section)
        self.topic_list = QListWidget()
        self.topic_list.currentItemChanged.connect(self._selection_changed)
        topic_layout.addWidget(self.topic_list, 1)
        body.addWidget(topic_card, 2)

        detail = QFrame()
        detail.setObjectName("contentCard")
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(14, 12, 14, 12)
        self.topic_title = QLabel("Select a topic")
        self.topic_title.setObjectName("pageTitle")
        self.topic_meta = QLabel("Learning state and evidence will appear here.")
        self.topic_meta.setObjectName("metaText")
        detail_layout.addWidget(self.topic_title)
        detail_layout.addWidget(self.topic_meta)
        state_row = QHBoxLayout()
        state_row.addWidget(QLabel("Learning state"))
        self.state_combo = QComboBox()
        for value, label in (("not_encountered", "Not encountered"), ("introduced", "Introduced"),
                             ("learning", "Learning"), ("understood", "Understood"),
                             ("applied", "Applied"), ("needs_revision", "Needs revision")):
            self.state_combo.addItem(label, value)
        state_row.addWidget(self.state_combo, 1)
        self.save_state_button = QPushButton("Save state")
        self.save_state_button.setObjectName("secondaryAction")
        self.save_state_button.clicked.connect(self._save_state)
        state_row.addWidget(self.save_state_button)
        detail_layout.addLayout(state_row)
        evidence_header = QHBoxLayout()
        evidence_label = QLabel("Evidence history")
        evidence_label.setObjectName("sectionTitle")
        evidence_header.addWidget(evidence_label, 1)
        self.reflection_button = QPushButton("Add reflection")
        self.reflection_button.setObjectName("quietAction")
        self.reflection_button.clicked.connect(self._add_reflection)
        evidence_header.addWidget(self.reflection_button)
        detail_layout.addLayout(evidence_header)
        self.evidence_list = QListWidget()
        detail_layout.addWidget(self.evidence_list, 2)
        actions = QHBoxLayout()
        self.quiz_button = QPushButton("Generate source-based quiz")
        self.quiz_button.setObjectName("primaryAction")
        self.quiz_button.clicked.connect(self._generate_quiz)
        self.quiz_status = QLabel("")
        self.quiz_status.setObjectName("footnote")
        actions.addWidget(self.quiz_button)
        actions.addWidget(self.quiz_status, 1)
        detail_layout.addLayout(actions)
        recommendations_label = QLabel("Suggested next steps")
        recommendations_label.setObjectName("sectionTitle")
        detail_layout.addWidget(recommendations_label)
        self.recommendation_list = QListWidget()
        self.recommendation_list.setMaximumHeight(115)
        detail_layout.addWidget(self.recommendation_list)
        recommendation_actions = QHBoxLayout()
        self.accept_recommendation = QPushButton("Accept")
        self.accept_recommendation.setObjectName("secondaryAction")
        self.accept_recommendation.clicked.connect(lambda: self._update_recommendation("accepted"))
        self.postpone_recommendation = QPushButton("Postpone")
        self.postpone_recommendation.setObjectName("quietAction")
        self.postpone_recommendation.clicked.connect(lambda: self._update_recommendation("postponed"))
        self.dismiss_recommendation = QPushButton("Dismiss")
        self.dismiss_recommendation.setObjectName("warningAction")
        self.dismiss_recommendation.clicked.connect(lambda: self._update_recommendation("dismissed"))
        recommendation_actions.addWidget(self.accept_recommendation)
        recommendation_actions.addWidget(self.postpone_recommendation)
        recommendation_actions.addWidget(self.dismiss_recommendation)
        recommendation_actions.addStretch(1)
        detail_layout.addLayout(recommendation_actions)
        self.recommendation_list.currentItemChanged.connect(self._recommendation_selection_changed)
        for button in (self.accept_recommendation, self.postpone_recommendation, self.dismiss_recommendation):
            button.setEnabled(False)
        body.addWidget(detail, 5)
        root.addLayout(body, 1)
        self._refresh_topics()
        self._refresh_recommendations()

    def _refresh_topics(self, preserve_id: str | None = None) -> None:
        topics = self.learning.topics()
        self.topic_list.blockSignals(True)
        self.topic_list.clear()
        selected = None
        for topic in topics:
            item = QListWidgetItem(f"{topic['name']}  ·  {str(topic['learning_state']).replace('_',' ').title()}")
            item.setData(Qt.ItemDataRole.UserRole, str(topic["id"]))
            self.topic_list.addItem(item)
            if str(topic["id"]) == (preserve_id or self._selected_topic_id):
                selected = item
        if not topics:
            empty = QListWidgetItem("Create a topic by linking it to a goal or note.")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.topic_list.addItem(empty)
        if selected:
            self.topic_list.setCurrentItem(selected)
        elif topics:
            self.topic_list.setCurrentRow(0)
        self.topic_list.blockSignals(False)
        self._render_topic()

    def refresh(self) -> None:
        """Reload topics and recommendations when the Learning page is opened."""
        self._refresh_topics(self._selected_topic_id)
        self._refresh_recommendations()

    def _current_topic(self) -> dict[str, Any] | None:
        item = self.topic_list.currentItem()
        topic_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        return next((row for row in self.learning.topics() if row["id"] == topic_id), None) if topic_id else None

    def _selection_changed(self, _current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        self._render_topic()

    def _render_topic(self) -> None:
        topic = self._current_topic()
        self._selected_topic_id = str(topic["id"]) if topic else None
        self.evidence_list.clear()
        self.quiz_button.setEnabled(topic is not None and self._worker is None)
        self.save_state_button.setEnabled(topic is not None)
        self.reflection_button.setEnabled(topic is not None)
        if not topic:
            self.topic_title.setText("Learning builds from evidence")
            self.topic_meta.setText("Connect notes or materials to a topic, then record how you engage with it.")
            self.recommendation_list.clear()
            return
        self.topic_title.setText(str(topic["name"]))
        self.topic_meta.setText(
            f"State: {str(topic['learning_state']).replace('_',' ').title()} · "
            f"{topic['evidence_count']} evidence items · "
            f"{'assessed ' + str(topic['last_assessed_at'])[:10] if topic['last_assessed_at'] else 'not assessed yet'}"
        )
        index = self.state_combo.findData(str(topic["learning_state"]))
        if index >= 0:
            self.state_combo.setCurrentIndex(index)
        for row in self.learning.evidence(str(topic["id"])):
            ratio = f" · {round(float(row['score']) * 100)}%" if row.get("score") is not None else ""
            self.evidence_list.addItem(f"{str(row['created_at'])[:16]} · {row['evidence_type'].title()}{ratio}\n{row['summary']}")

    def _save_state(self) -> None:
        if not self._selected_topic_id:
            return
        try:
            self.learning.set_state(self._selected_topic_id, str(self.state_combo.currentData()))
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Learning state not saved", str(exc))
            return
        self._refresh_topics(self._selected_topic_id)
        self._refresh_recommendations()

    def _add_reflection(self) -> None:
        if not self._selected_topic_id:
            return
        text, accepted = QInputDialog.getMultiLineText(self, "Learning reflection", "What did you understand, try, or find difficult?")
        if not accepted or not text.strip():
            return
        try:
            self.learning.add_reflection(self._selected_topic_id, text)
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Reflection not saved", str(exc))
            return
        self._refresh_topics(self._selected_topic_id)
        self._refresh_recommendations()

    def _generate_quiz(self) -> None:
        if not self._selected_topic_id or self._worker is not None:
            return
        self.quiz_button.setEnabled(False)
        self.quiz_status.setText("Generating from local sources…")
        self._worker = QuizGenerationWorker(self.database, self._selected_topic_id)
        self._worker.created.connect(self._quiz_created)
        self._worker.failed.connect(self._quiz_failed)
        self._worker.finished.connect(self._quiz_finished)
        self._worker.start()

    def _quiz_created(self, quiz: object) -> None:
        if not isinstance(quiz, dict):
            self.quiz_status.setText("Quiz could not be opened.")
            return
        dialog = QuizTakeDialog(quiz, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.quiz_status.setText("Quiz saved for this topic; no attempt was recorded.")
            return
        try:
            result = self.learning.record_attempt(str(quiz["id"]), dialog.answers())
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Quiz result not saved", str(exc))
            return
        dialog.show_result(result)
        dialog.exec()
        self.quiz_status.setText(f"Recorded {result['score']}/{result['total']} as learning evidence.")
        self._refresh_topics(self._selected_topic_id)
        self._refresh_recommendations()

    def _quiz_failed(self, message: str) -> None:
        self.quiz_status.setText("Quiz not generated.")
        QMessageBox.information(self, "Quiz unavailable", message)

    def _quiz_finished(self) -> None:
        self._worker = None
        self.quiz_button.setEnabled(self._selected_topic_id is not None)

    def _refresh_recommendations(self) -> None:
        selected_item = self.recommendation_list.currentItem()
        selected_id = str(selected_item.data(Qt.ItemDataRole.UserRole) or "") if selected_item else ""
        self.recommendation_list.clear()
        recommendations = self.learning.recommendations()
        selected_row: int | None = None
        for row in recommendations:
            state = "Accepted · " if row["status"] == "accepted" else ""
            item = QListWidgetItem(
                f"{state}{row['topic_name']} · {row['recommendation_type'].title()} · {row['reason']}"
            )
            item.setData(Qt.ItemDataRole.UserRole, str(row["id"]))
            item.setData(Qt.ItemDataRole.UserRole + 1, str(row["status"]))
            self.recommendation_list.addItem(item)
            if str(row["id"]) == selected_id:
                selected_row = self.recommendation_list.count() - 1
        if not recommendations:
            empty = QListWidgetItem("No recommendations yet. Add topic evidence to get a reasoned next step.")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.recommendation_list.addItem(empty)
        if selected_row is not None:
            self.recommendation_list.setCurrentRow(selected_row)
        self._recommendation_selection_changed(self.recommendation_list.currentItem(), None)

    def _recommendation_selection_changed(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        status = str(current.data(Qt.ItemDataRole.UserRole + 1) or "") if current else ""
        self.accept_recommendation.setEnabled(status == "pending")
        self.postpone_recommendation.setEnabled(status in {"pending", "accepted"})
        self.dismiss_recommendation.setEnabled(status in {"pending", "accepted"})

    def _update_recommendation(self, status: str) -> None:
        item = self.recommendation_list.currentItem()
        recommendation_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not recommendation_id:
            return
        try:
            self.learning.update_recommendation(str(recommendation_id), status)
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Recommendation not updated", str(exc))
            return
        self._refresh_recommendations()
