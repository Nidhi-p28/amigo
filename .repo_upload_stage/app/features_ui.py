from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import zipfile

from PySide6.QtCore import QDate, QDateTime, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QFileDialog,
    QInputDialog,
)

from .domain import AmigoServiceError
from .features import FocusSessionService, PersonalFeatures, ProfileService
from .knowledge import KnowledgeService
from .services import GoalService, KnowledgeLinkService
from .storage import SqliteDatabase
from .theme import PRODUCT_STYLE


class FocusSessionDialog(QDialog):
    def __init__(self, database: SqliteDatabase, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.database = database
        self.sessions = FocusSessionService(database)
        self.setWindowTitle("Focus session")
        self.setStyleSheet(PRODUCT_STYLE)
        self.resize(430, 340)
        self.session_id: str | None = None
        self._session_started: datetime | None = None
        self._closing_after_finish = False
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title_edit = QLineEdit("Focused work")
        self.minutes = QSpinBox()
        self.minutes.setRange(1, 240)
        self.minutes.setValue(25)
        self.minutes.setSuffix(" min")
        self.goal = QComboBox()
        self.goal.addItem("No linked goal", "")
        for item in GoalService(database).list_goals(status="active"):
            self.goal.addItem(str(item["title"]), str(item["id"]))
        self.topic = QComboBox()
        self.topic.addItem("No linked topic", "")
        for item in KnowledgeLinkService(database).list_topics():
            self.topic.addItem(str(item["name"]), str(item["id"]))
        self.project = QComboBox()
        self.project.addItem("No linked project", "")
        from .features import ProjectService

        for item in ProjectService(database).list_projects(active_only=True):
            self.project.addItem(str(item["title"]), str(item["id"]))
        form.addRow("Session", self.title_edit)
        form.addRow("Planned duration", self.minutes)
        form.addRow("Goal", self.goal)
        form.addRow("Topic", self.topic)
        form.addRow("Project or study hub", self.project)
        layout.addLayout(form)
        self.timer_label = QLabel("Ready when you are")
        self.timer_label.setObjectName("pageTitle")
        self.timer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.timer_label)
        self.status_label = QLabel("Finishing early records the elapsed time as partial work. Cancelling records no activity.")
        self.status_label.setObjectName("footnote")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        actions = QHBoxLayout()
        self.start_button = QPushButton("Start focus")
        self.start_button.setObjectName("primaryAction")
        self.start_button.clicked.connect(self._start)
        self.finish_button = QPushButton("Finish and log work")
        self.finish_button.setObjectName("secondaryAction")
        self.finish_button.setEnabled(False)
        self.finish_button.clicked.connect(self._finish_early)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("quietAction")
        self.cancel_button.clicked.connect(self.reject)
        actions.addWidget(self.start_button)
        actions.addWidget(self.finish_button)
        actions.addStretch(1)
        actions.addWidget(self.cancel_button)
        layout.addLayout(actions)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)

    def _start(self) -> None:
        try:
            session = self.sessions.start(
                self.title_edit.text(), self.minutes.value(),
                goal_id=str(self.goal.currentData() or "") or None,
                topic_id=str(self.topic.currentData() or "") or None,
                project_id=str(self.project.currentData() or "") or None,
            )
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Focus session not started", str(exc))
            return
        self.session_id = str(session["id"])
        self._session_started = datetime.fromisoformat(str(session["started_at"]))
        self.title_edit.setEnabled(False)
        self.minutes.setEnabled(False)
        self.goal.setEnabled(False)
        self.topic.setEnabled(False)
        self.project.setEnabled(False)
        self.start_button.setEnabled(False)
        self.finish_button.setEnabled(True)
        self.cancel_button.setText("Cancel session")
        self.timer.start()
        self._tick()

    def _tick(self) -> None:
        if not self._session_started:
            return
        elapsed = max(0, int((datetime.now(timezone.utc) - self._session_started.astimezone(timezone.utc)).total_seconds()))
        remaining = max(0, self.minutes.value() * 60 - elapsed)
        self.timer_label.setText(f"{remaining // 60:02d}:{remaining % 60:02d}")
        if remaining <= 0:
            self._complete()

    def _finish_early(self) -> None:
        if not self.session_id:
            return
        elapsed = int((datetime.now(timezone.utc) - (self._session_started or datetime.now(timezone.utc)).astimezone(timezone.utc)).total_seconds() // 60)
        if elapsed < 1:
            QMessageBox.information(self, "Keep focusing", "Let the focus session run for at least one minute before logging it.")
            return
        answer = QMessageBox.question(
            self, "Finish session early?",
            f"Log about {elapsed} minute(s) of actual work as a partial activity?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._complete()

    def _complete(self) -> None:
        if not self.session_id:
            return
        self.timer.stop()
        try:
            result = self.sessions.finish(self.session_id)
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Focus session not saved", str(exc))
            return
        self._closing_after_finish = True
        self.status_label.setText("Actual work was recorded in activity history.")
        self.timer_label.setText("Session complete")
        QMessageBox.information(self, "Focus session complete", "The activity was saved with its actual elapsed time.")
        self.accept()

    def reject(self) -> None:
        if self.session_id and not self._closing_after_finish:
            answer = QMessageBox.question(
                self, "Cancel focus session?", "Cancel without recording an activity?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                self.sessions.finish(self.session_id, cancel=True)
            except AmigoServiceError as exc:
                QMessageBox.warning(self, "Session could not be cancelled", str(exc))
                return
            self.timer.stop()
        super().reject()


class ProfileDialog(QDialog):
    def __init__(self, database: SqliteDatabase, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.database = database
        self.profile = ProfileService(database)
        self.features = PersonalFeatures(database)
        self.setWindowTitle("Profile and settings")
        self.setStyleSheet(PRODUCT_STYLE)
        self.resize(550, 520)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)

        profile_page = QWidget()
        profile_layout = QVBoxLayout(profile_page)
        form = QFormLayout()
        existing = self.profile.get_profile()
        self.fields: dict[str, QPlainTextEdit | QLineEdit] = {}
        for key, label, height in (
            ("name", "Name", 0), ("bio", "Short bio", 56), ("current_context", "Current context", 70),
            ("aspirations", "Aspirations", 70), ("interests", "Interests", 56), ("response_style", "Response preference", 56),
        ):
            if height:
                field = QPlainTextEdit(str(existing.get(key) or ""))
                field.setFixedHeight(height)
            else:
                field = QLineEdit(str(existing.get(key) or ""))
            self.fields[key] = field
            form.addRow(label, field)
        profile_layout.addLayout(form)
        profile_hint = QLabel("Only this profile information and approved memories are included as optional local assistant context.")
        profile_hint.setObjectName("footnote")
        profile_hint.setWordWrap(True)
        profile_layout.addWidget(profile_hint)
        self.save_profile_button = QPushButton("Save profile")
        self.save_profile_button.setObjectName("primaryAction")
        self.save_profile_button.clicked.connect(self._save_profile)
        profile_layout.addWidget(self.save_profile_button, 0, Qt.AlignmentFlag.AlignRight)
        tabs.addTab(profile_page, "Profile")

        memory_page = QWidget()
        memory_layout = QVBoxLayout(memory_page)
        memory_hint = QLabel("New memories stay pending until you approve them. Editing an approved memory sends the changed text back for your approval.")
        memory_hint.setObjectName("pageDescription")
        memory_hint.setWordWrap(True)
        memory_layout.addWidget(memory_hint)
        self.memory_list = QListWidget()
        self.memory_list.currentItemChanged.connect(self._memory_selection_changed)
        memory_layout.addWidget(self.memory_list, 1)
        self.memory_detail = QPlainTextEdit()
        self.memory_detail.setReadOnly(True)
        self.memory_detail.setMaximumHeight(80)
        memory_layout.addWidget(self.memory_detail)
        memory_actions = QHBoxLayout()
        self.propose_memory_button = QPushButton("Propose memory…")
        self.propose_memory_button.setObjectName("secondaryAction")
        self.propose_memory_button.clicked.connect(self._propose_memory)
        self.approve_memory_button = QPushButton("Approve")
        self.approve_memory_button.setObjectName("primaryAction")
        self.approve_memory_button.clicked.connect(lambda: self._decide_memory(True))
        self.reject_memory_button = QPushButton("Decline")
        self.reject_memory_button.setObjectName("warningAction")
        self.reject_memory_button.clicked.connect(lambda: self._decide_memory(False))
        self.edit_memory_button = QPushButton("Edit…")
        self.edit_memory_button.setObjectName("secondaryAction")
        self.edit_memory_button.clicked.connect(self._edit_memory)
        self.delete_memory_button = QPushButton("Delete")
        self.delete_memory_button.setObjectName("quietAction")
        self.delete_memory_button.clicked.connect(self._delete_memory)
        for widget in (self.propose_memory_button, self.approve_memory_button, self.reject_memory_button,
                       self.edit_memory_button, self.delete_memory_button):
            memory_actions.addWidget(widget)
        memory_layout.addLayout(memory_actions)
        tabs.addTab(memory_page, "Memories")
        self._refresh_memories()

        data_page = QWidget()
        data_layout = QVBoxLayout(data_page)
        data_title = QLabel("Your data")
        data_title.setObjectName("sectionTitle")
        data_layout.addWidget(data_title)
        data_description = QLabel("Export a local backup containing the Amigo database, Knowledge notes, and copied My Materials. Your source originals remain where they are.")
        data_description.setObjectName("pageDescription")
        data_description.setWordWrap(True)
        data_layout.addWidget(data_description)
        self.export_button = QPushButton("Export backup…")
        self.export_button.setObjectName("primaryAction")
        self.export_button.clicked.connect(self._export_backup)
        data_layout.addWidget(self.export_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.export_status = QLabel("")
        self.export_status.setObjectName("footnote")
        self.export_status.setWordWrap(True)
        data_layout.addWidget(self.export_status)
        data_layout.addStretch(1)
        tabs.addTab(data_page, "Data")
        close = QPushButton("Close")
        close.setObjectName("secondaryAction")
        close.clicked.connect(self.accept)
        layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)

    def _value(self, field: QPlainTextEdit | QLineEdit) -> str:
        return field.toPlainText() if isinstance(field, QPlainTextEdit) else field.text()

    def _save_profile(self) -> None:
        try:
            self.profile.update_profile(**{key: self._value(field) for key, field in self.fields.items()})
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Profile not saved", str(exc))
            return
        QMessageBox.information(self, "Profile saved", "Your profile was updated.")

    def _refresh_memories(self, preserve_id: str | None = None) -> None:
        memories = self.profile.memories()
        self.memory_list.clear()
        selected = None
        for memory in memories:
            item = QListWidgetItem(f"{memory['content'][:90]}  ·  {memory['status']}")
            item.setData(Qt.ItemDataRole.UserRole, str(memory["id"]))
            self.memory_list.addItem(item)
            if memory["id"] == preserve_id:
                selected = item
        if not memories:
            empty = QListWidgetItem("No saved memories. Proposals need your approval first.")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.memory_list.addItem(empty)
        if selected:
            self.memory_list.setCurrentItem(selected)
        self._memory_selection_changed(self.memory_list.currentItem(), None)

    def _selected_memory_id(self) -> str | None:
        item = self.memory_list.currentItem()
        value = item.data(Qt.ItemDataRole.UserRole) if item else None
        return str(value) if value else None

    def _memory_selection_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        memory_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        memory = next((item for item in self.profile.memories() if item["id"] == memory_id), None) if memory_id else None
        self.memory_detail.setPlainText(str(memory["content"]) if memory else "")
        pending = bool(memory and memory["status"] == "pending")
        self.approve_memory_button.setEnabled(pending)
        self.reject_memory_button.setEnabled(pending)
        self.edit_memory_button.setEnabled(memory is not None)
        self.delete_memory_button.setEnabled(memory is not None)

    def _propose_memory(self) -> None:
        content, accepted = QInputDialog.getMultiLineText(
            self, "Propose a memory", "What should Amigo remember? It will not be used unless you approve it."
        )
        if not accepted or not content.strip():
            return
        try:
            memory = self.profile.propose_memory(content)
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Memory not saved", str(exc))
            return
        self._refresh_memories(str(memory["id"]))

    def _decide_memory(self, approve: bool) -> None:
        memory_id = self._selected_memory_id()
        if not memory_id:
            return
        try:
            self.profile.decide_memory(memory_id, approve=approve)
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Memory not updated", str(exc))
            return
        self._refresh_memories(memory_id)

    def _edit_memory(self) -> None:
        memory_id = self._selected_memory_id()
        if not memory_id:
            return
        memory = next((item for item in self.profile.memories() if item["id"] == memory_id), None)
        if not memory:
            return
        content, accepted = QInputDialog.getMultiLineText(
            self, "Edit memory", "Changed text will need approval again:", str(memory["content"])
        )
        if not accepted:
            return
        try:
            updated = self.profile.update_memory(memory_id, content)
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Memory not updated", str(exc))
            return
        self._refresh_memories(memory_id)
        if updated["status"] == "pending":
            self.memory_detail.setPlainText(str(updated["content"]) + "\n\nPending your approval.")

    def _delete_memory(self) -> None:
        memory_id = self._selected_memory_id()
        if not memory_id:
            return
        answer = QMessageBox.question(self, "Delete memory", "Delete this memory permanently?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.profile.delete_memory(memory_id)
        self._refresh_memories()

    def _export_backup(self) -> None:
        destination, _filter = QFileDialog.getSaveFileName(self, "Export Amigo backup", "Amigo-backup.zip", "ZIP archive (*.zip)")
        if not destination:
            return
        target = Path(destination)
        overwrite = False
        if target.exists():
            answer = QMessageBox.question(self, "Replace backup?", f"Replace the existing file?\n{target}")
            if answer != QMessageBox.StandardButton.Yes:
                return
            overwrite = True
        try:
            result = self.features.export_backup(target, overwrite=overwrite)
        except (AmigoServiceError, OSError, zipfile.BadZipFile) as exc:
            QMessageBox.warning(self, "Backup not exported", str(exc))
            return
        self.export_status.setText(f"Backup exported to {result['path']} · {int(result['size_bytes']) // 1024} KB")


class GlobalSearchDialog(QDialog):
    opened = Signal(str, str)

    def __init__(self, database: SqliteDatabase, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.features = PersonalFeatures(database)
        self.setWindowTitle("Search Amigo")
        self.setStyleSheet(PRODUCT_STYLE)
        self.resize(610, 450)
        layout = QVBoxLayout(self)
        self.query = QLineEdit()
        self.query.setPlaceholderText("Search goals, milestones, projects, plans, activities, topics, notes, and materials…")
        self.query.returnPressed.connect(self._search)
        layout.addWidget(self.query)
        self.results = QListWidget()
        self.results.itemDoubleClicked.connect(self._open_selected)
        layout.addWidget(self.results, 1)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(120)
        layout.addWidget(self.detail)
        actions = QHBoxLayout()
        search_button = QPushButton("Search")
        search_button.setObjectName("primaryAction")
        search_button.clicked.connect(self._search)
        self.open_button = QPushButton("Open")
        self.open_button.setObjectName("secondaryAction")
        self.open_button.clicked.connect(self._open_selected)
        close = QPushButton("Close")
        close.setObjectName("quietAction")
        close.clicked.connect(self.reject)
        actions.addWidget(search_button)
        actions.addStretch(1)
        actions.addWidget(close)
        actions.addWidget(self.open_button)
        layout.addLayout(actions)
        self._items: dict[str, dict[str, Any]] = {}
        self.results.currentItemChanged.connect(self._show_detail)

    def _search(self) -> None:
        self.results.clear()
        self.detail.clear()
        try:
            rows = self.features.global_search(self.query.text())
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Search unavailable", str(exc))
            return
        self._items.clear()
        for row in rows:
            key = f"{row['kind']}:{row['id']}"
            item = QListWidgetItem(f"{row['kind'].title()}  ·  {row['title']}  ·  {row.get('subtitle','')}")
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setToolTip(str(row.get("excerpt") or row.get("subtitle") or ""))
            self.results.addItem(item)
            self._items[key] = row
        if rows:
            self.results.setCurrentRow(0)
        else:
            self.detail.setPlainText("No matching records. Try a title, topic, or distinctive phrase from a note.")

    def _selected(self) -> dict[str, Any] | None:
        item = self.results.currentItem()
        key = item.data(Qt.ItemDataRole.UserRole) if item else None
        return self._items.get(str(key)) if key else None

    def _show_detail(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        result = self._selected()
        if result:
            self.detail.setPlainText(str(result.get("excerpt") or result.get("subtitle") or result.get("title")))

    def _open_selected(self, *_args) -> None:
        result = self._selected()
        if result:
            self.opened.emit(str(result["kind"]), str(result["id"]))
            self.accept()


class ResearchCompareDialog(QDialog):
    askRequested = Signal(str)

    def __init__(self, database: SqliteDatabase, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.features = PersonalFeatures(database)
        self.materials = KnowledgeService(database).list_materials()
        self.setWindowTitle("Compare research materials")
        self.setStyleSheet(PRODUCT_STYLE)
        self.resize(650, 500)
        layout = QVBoxLayout(self)
        hint = QLabel("Choose two indexed materials. The comparison uses only extracted local source text and shows section paths for verification.")
        hint.setObjectName("pageDescription")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        self.first = QComboBox()
        self.second = QComboBox()
        for material in self.materials:
            label = f"{material['title']} · {material['index_status'] or 'not indexed'}"
            self.first.addItem(label, str(material["id"]))
            self.second.addItem(label, str(material["id"]))
        if self.second.count() > 1:
            self.second.setCurrentIndex(1)
        form.addRow("First material", self.first)
        form.addRow("Second material", self.second)
        layout.addLayout(form)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setPlaceholderText("Choose two indexed materials to view their source sections.")
        layout.addWidget(self.detail, 1)
        self.compare_button = QPushButton("Load source sections")
        self.compare_button.setObjectName("secondaryAction")
        self.compare_button.clicked.connect(self._compare)
        self.ask_button = QPushButton("Ask Amigo for a grounded comparison")
        self.ask_button.setObjectName("primaryAction")
        self.ask_button.clicked.connect(self._ask)
        self.ask_button.setEnabled(len(self.materials) >= 2)
        actions = QHBoxLayout()
        close = QPushButton("Close")
        close.setObjectName("quietAction")
        close.clicked.connect(self.reject)
        actions.addWidget(self.compare_button)
        actions.addStretch(1)
        actions.addWidget(close)
        actions.addWidget(self.ask_button)
        layout.addLayout(actions)

    def _compare(self) -> None:
        if self.first.currentData() == self.second.currentData():
            QMessageBox.information(self, "Choose two materials", "Select two different materials.")
            return
        try:
            result = self.features.compare_materials([str(self.first.currentData()), str(self.second.currentData())])
        except AmigoServiceError as exc:
            self.detail.setPlainText(str(exc))
            return
        chunks = []
        for material in result["materials"]:
            chunks.append(f"{material['title']}\nIndex state: {material['status'] or 'not indexed'}\n")
            if not material["sections"]:
                chunks.append("No indexed source sections. Index this file before comparing.\n")
            for section in material["sections"]:
                chunks.append(f"[{section['source_locator'] or section['title']}]\n{str(section['body'])[:1600]}\n")
        chunks.append(result["guidance"])
        self.detail.setPlainText("\n".join(chunks))

    def _ask(self) -> None:
        if self.first.currentData() == self.second.currentData():
            QMessageBox.information(self, "Choose two materials", "Select two different materials.")
            return
        selected = []
        for combo in (self.first, self.second):
            material_id = str(combo.currentData())
            material = next((item for item in self.materials if str(item["id"]) == material_id), None)
            if material:
                selected.append(str(material["title"]))
        if len(selected) == 2:
            self.askRequested.emit(
                "Use my local notes and materials to answer: Compare these two registered sources strictly from retrieved text: "
                f"{selected[0]} and {selected[1]}. Compare the research problem, methodology, data, results, limitations, and contributions. "
                "Keep the sources separate and cite the source path or section for every comparison. If a detail is missing, say so."
            )
            self.accept()


class GoalReplanDialog(QDialog):
    def __init__(self, database: SqliteDatabase, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.database = database
        self.features = PersonalFeatures(database)
        self.goals = GoalService(database)
        self.suggestions = self.features.replanning_suggestions()
        self.setWindowTitle("Review goal plan")
        self.setStyleSheet(PRODUCT_STYLE)
        self.resize(560, 400)
        layout = QVBoxLayout(self)
        hint = QLabel("These are suggestions based on active goals, milestone dates, and scheduled sessions. Nothing changes until you choose a new date and confirm.")
        hint.setObjectName("pageDescription")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.items = QListWidget()
        for suggestion in self.suggestions:
            item = QListWidgetItem(str(suggestion["reason"]))
            item.setData(Qt.ItemDataRole.UserRole, str(suggestion["milestone_id"]))
            item.setData(Qt.ItemDataRole.UserRole + 1, str(suggestion["target_date"]))
            self.items.addItem(item)
        if not self.suggestions:
            empty = QListWidgetItem("No overdue or near-term milestones need attention.")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.items.addItem(empty)
        layout.addWidget(self.items, 1)
        form = QFormLayout()
        self.new_date = QDateEdit()
        self.new_date.setCalendarPopup(True)
        self.new_date.setDisplayFormat("d MMM yyyy")
        self.new_date.setDate(QDate.currentDate().addDays(7))
        form.addRow("Proposed new milestone date", self.new_date)
        layout.addLayout(form)
        self.status = QLabel("")
        self.status.setObjectName("footnote")
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        close = QPushButton("Close")
        close.setObjectName("quietAction")
        close.clicked.connect(self.reject)
        self.apply_button = QPushButton("Reschedule milestone…")
        self.apply_button.setObjectName("primaryAction")
        self.apply_button.setEnabled(bool(self.suggestions))
        self.apply_button.clicked.connect(self._apply)
        actions.addStretch(1)
        actions.addWidget(close)
        actions.addWidget(self.apply_button)
        layout.addLayout(actions)
        self.items.currentItemChanged.connect(self._select_suggestion)
        if self.suggestions:
            self.items.setCurrentRow(0)

    def _select_suggestion(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current:
            parsed = QDate.fromString(str(current.data(Qt.ItemDataRole.UserRole + 1) or ""), "yyyy-MM-dd")
            suggested = parsed.addDays(7) if parsed.isValid() else QDate.currentDate().addDays(7)
            minimum = QDate.currentDate().addDays(1)
            if not suggested.isValid() or suggested < minimum:
                suggested = minimum
            self.new_date.setDate(suggested)

    def _apply(self) -> None:
        item = self.items.currentItem()
        milestone_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not milestone_id:
            return
        milestone = next((row for row in self.suggestions if row["milestone_id"] == milestone_id), None)
        if not milestone:
            return
        new_date = self.new_date.date().toString("yyyy-MM-dd")
        answer = QMessageBox.question(
            self, "Confirm schedule change",
            f"Reschedule this milestone to {new_date}? This will be recorded in goal history.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.goals.reschedule_milestone(str(milestone_id), new_date)
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Milestone not rescheduled", str(exc))
            return
        self.suggestions = self.features.replanning_suggestions()
        self.status.setText("Milestone date updated and recorded in progress history.")
        self.accept()
