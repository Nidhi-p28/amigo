from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QDate, QThread, Signal, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QInputDialog,
)

from .domain import AmigoServiceError, GoalStatus
from .config import LEGACY_WORKSPACE_DIR, WORKSPACE_DIR
from .services import ActivityService, GoalService, KnowledgeLinkService, ProgressQuery
from .features import ProjectService
from .knowledge import KnowledgeService
from .storage import SqliteDatabase
from .theme import PRODUCT_STYLE


class GoalEditorDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, goal: dict[str, Any] | None = None) -> None:
        super().__init__(parent)
        self.goal = goal
        self.setWindowTitle("Edit goal" if goal else "Create a goal")
        self.resize(420, 370)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title_edit = QLineEdit(str((goal or {}).get("title") or ""))
        self.title_edit.setPlaceholderText("What do you want to accomplish?")
        self.description_edit = QPlainTextEdit(str((goal or {}).get("description") or ""))
        self.description_edit.setPlaceholderText("Why this matters or what completion means")
        self.description_edit.setFixedHeight(90)
        self.target_enabled = QCheckBox("Set a target date")
        self.target_date = QDateEdit()
        self.target_date.setCalendarPopup(True)
        self.target_date.setDisplayFormat("d MMM yyyy")
        self.target_date.setDate(QDate.currentDate())
        if (goal or {}).get("target_date"):
            parsed = QDate.fromString(str(goal["target_date"]), "yyyy-MM-dd")
            if parsed.isValid():
                self.target_date.setDate(parsed)
                self.target_enabled.setChecked(True)
        self.target_date.setEnabled(self.target_enabled.isChecked())
        self.target_enabled.toggled.connect(self.target_date.setEnabled)
        self.priority = QComboBox()
        self.priority.addItem("High", 1)
        self.priority.addItem("Normal", 2)
        self.priority.addItem("Low", 3)
        selected_priority = int((goal or {}).get("priority") or 2)
        self.priority.setCurrentIndex(max(0, min(2, selected_priority - 1)))
        form.addRow("Goal", self.title_edit)
        form.addRow("Description", self.description_edit)
        form.addRow("Target", self.target_enabled)
        form.addRow("Date", self.target_date)
        form.addRow("Priority", self.priority)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.title_edit.setFocus()

    def values(self) -> dict[str, Any]:
        return {
            "title": self.title_edit.text().strip(),
            "description": self.description_edit.toPlainText().strip(),
            "target_date": self.target_date.date().toString("yyyy-MM-dd") if self.target_enabled.isChecked() else None,
            "priority": int(self.priority.currentData()),
        }


class ActivityDialog(QDialog):
    def __init__(self, database: SqliteDatabase, parent: QWidget | None = None, goal_id: str | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Record an activity")
        self.resize(440, 420)
        self.database = database
        self.goal_service = GoalService(database)
        self.knowledge_service = KnowledgeLinkService(database)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("What did you work on?")
        self.duration = QSpinBox()
        self.duration.setRange(0, 1440)
        self.duration.setSpecialValueText("Not recorded")
        self.duration.setSuffix(" min")
        self.status = QComboBox()
        self.status.addItem("Completed", "completed")
        self.status.addItem("Partially completed", "partial")
        self.goal = QComboBox()
        self.goal.addItem("No goal", "")
        selected_index = 0
        for item in self.goal_service.list_goals():
            index = self.goal.count()
            self.goal.addItem(str(item["title"]), str(item["id"]))
            if goal_id and str(item["id"]) == goal_id:
                selected_index = index
        self.goal.setCurrentIndex(selected_index)
        self.topic = QComboBox()
        self.topic.setEditable(True)
        self.topic.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.topic.addItem("")
        self.topic.addItems([str(item["name"]) for item in self.knowledge_service.list_topics()])
        self.topic.setCurrentText("")
        self.project = QComboBox()
        self.project.addItem("No project", "")
        for item in ProjectService(database).list_projects(active_only=True):
            self.project.addItem(str(item["title"]), str(item["id"]))
        self.reflection = QPlainTextEdit()
        self.reflection.setPlaceholderText("Optional takeaway or reflection")
        self.reflection.setFixedHeight(85)
        form.addRow("Activity", self.title_edit)
        form.addRow("Duration", self.duration)
        form.addRow("Outcome", self.status)
        form.addRow("Related goal", self.goal)
        form.addRow("Topic", self.topic)
        form.addRow("Project", self.project)
        form.addRow("Takeaway", self.reflection)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.title_edit.setFocus()

    def values(self) -> dict[str, Any]:
        goal_id = str(self.goal.currentData() or "")
        topic_name = self.topic.currentText().strip()
        return {
            "title": self.title_edit.text().strip(),
            "duration_minutes": int(self.duration.value()) or None,
            "status": str(self.status.currentData()),
            "goal_ids": [goal_id] if goal_id else [],
            "topic_names": [topic_name] if topic_name else [],
            "project_ids": [str(self.project.currentData())] if self.project.currentData() else [],
            "reflection": self.reflection.toPlainText().strip(),
        }


class MaterialImportWorker(QThread):
    imported = Signal(object, object)
    failed = Signal(str)

    def __init__(self, database: SqliteDatabase, source: str, topic_name: str) -> None:
        super().__init__()
        self.database = database
        self.source = source
        self.topic_name = topic_name

    def run(self) -> None:
        try:
            record = KnowledgeLinkService(self.database).import_material(self.source, [self.topic_name])
            index_state = KnowledgeService(self.database).index_material(record.id)
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.imported.emit(record, index_state)


class GoalManagerPanel(QWidget):
    """Structured first-release workspace for goals, milestones, and actual work."""

    def __init__(self, database: SqliteDatabase, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.database = database
        self.goal_service = GoalService(database)
        self.activity_service = ActivityService(database)
        self.knowledge_service = KnowledgeLinkService(database)
        self.progress_query = ProgressQuery(database)
        self._import_worker: MaterialImportWorker | None = None
        self._goal_is_completed = False
        self.setStyleSheet(PRODUCT_STYLE)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)
        intro = QLabel("Goals and progress")
        intro.setObjectName("pageTitle")
        root.addWidget(intro)
        intro_hint = QLabel("Set a clear outcome, break it into milestones, and log the work you complete.")
        intro_hint.setObjectName("pageDescription")
        root.addWidget(intro_hint)
        body = QHBoxLayout()
        body.setSpacing(14)

        left_frame = QFrame()
        left_frame.setObjectName("contentCard")
        left_card = QVBoxLayout(left_frame)
        left_card.setContentsMargins(12, 12, 12, 12)
        left_card.setSpacing(9)
        left = QVBoxLayout()
        goal_kicker = QLabel("MY GOALS")
        goal_kicker.setObjectName("sectionKicker")
        left.addWidget(goal_kicker)
        self.goal_list = QListWidget()
        self.goal_list.setObjectName("goalList")
        self.goal_list.setWordWrap(True)
        self.goal_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.goal_list.currentItemChanged.connect(self._selection_changed)
        left.addWidget(self.goal_list, 1)
        left_actions = QHBoxLayout()
        self.add_goal_button = QPushButton("+ Goal")
        self.add_goal_button.setObjectName("primaryAction")
        self.add_goal_button.clicked.connect(self._add_goal)
        self.log_activity_button = QPushButton("Log activity")
        self.log_activity_button.setObjectName("secondaryAction")
        self.log_activity_button.clicked.connect(self._log_activity)
        left_actions.addWidget(self.add_goal_button)
        left_actions.addWidget(self.log_activity_button)
        left.addLayout(left_actions)
        self.import_button = QPushButton("Import a material…")
        self.import_button.setToolTip("Copy a selected original file into Amigo Materials and link it to a topic.")
        self.import_button.setObjectName("quietAction")
        self.import_button.clicked.connect(self._import_material)
        left.addWidget(self.import_button)
        left_card.addLayout(left)
        body.addWidget(left_frame, 1)

        right_frame = QFrame()
        right_frame.setObjectName("contentCard")
        right_frame.setStyleSheet(PRODUCT_STYLE)
        right_card = QVBoxLayout(right_frame)
        right_card.setContentsMargins(14, 12, 14, 12)
        right_card.setSpacing(9)
        right = QVBoxLayout()
        self.goal_title = QLabel("Select a goal")
        self.goal_title.setObjectName("pageTitle")
        self.goal_meta = QLabel("Create a goal to begin recording progress.")
        self.goal_meta.setObjectName("metaText")
        self.goal_meta.setWordWrap(True)
        self.goal_description = QLabel("")
        self.goal_description.setWordWrap(True)
        right.addWidget(self.goal_title)
        right.addWidget(self.goal_meta)
        right.addWidget(self.goal_description)

        progress_row = QHBoxLayout()
        self.milestone_progress = QProgressBar()
        self.milestone_progress.setObjectName("milestoneProgress")
        self.milestone_progress.setTextVisible(False)
        self.milestone_progress.setRange(0, 1)
        self.milestone_progress.setValue(0)
        self.milestone_progress.setFixedHeight(9)
        self.milestone_progress.setVisible(False)
        self.milestone_progress_label = QLabel("Add milestones to track goal progress.")
        self.milestone_progress_label.setObjectName("metaText")
        progress_row.addWidget(self.milestone_progress, 1)
        progress_row.addWidget(self.milestone_progress_label)
        right.addLayout(progress_row)

        topic_header = QHBoxLayout()
        topics_title = QLabel("Topics and materials")
        topics_title.setObjectName("sectionTitle")
        topic_header.addWidget(topics_title, 1)
        self.link_topic_button = QPushButton("Link topic")
        self.link_topic_button.setObjectName("secondaryAction")
        self.link_topic_button.clicked.connect(self._link_topic)
        topic_header.addWidget(self.link_topic_button)
        right.addLayout(topic_header)
        self.topics_label = QLabel("No topics linked yet.")
        self.topics_label.setWordWrap(True)
        right.addWidget(self.topics_label)

        milestone_title = QLabel("Milestones")
        milestone_title.setObjectName("sectionTitle")
        right.addWidget(milestone_title)
        self.milestone_list = QListWidget()
        self.milestone_list.setObjectName("milestoneList")
        self.milestone_list.setMinimumHeight(76)
        self.milestone_list.setWordWrap(True)
        self.milestone_list.itemDoubleClicked.connect(self._complete_selected_milestone)
        self.milestone_list.currentItemChanged.connect(self._milestone_selection_changed)
        right.addWidget(self.milestone_list, 2)
        milestone_actions = QHBoxLayout()
        self.add_milestone_button = QPushButton("+ Milestone")
        self.add_milestone_button.setObjectName("secondaryAction")
        self.add_milestone_button.clicked.connect(self._add_milestone)
        self.complete_milestone_button = QPushButton("Mark complete")
        self.complete_milestone_button.setObjectName("primaryAction")
        self.complete_milestone_button.clicked.connect(self._complete_selected_milestone)
        milestone_actions.addWidget(self.add_milestone_button)
        milestone_actions.addWidget(self.complete_milestone_button)
        right.addLayout(milestone_actions)

        activity_title = QLabel("Recent actual activities")
        activity_title.setObjectName("sectionTitle")
        right.addWidget(activity_title)
        self.activity_list = QListWidget()
        self.activity_list.setObjectName("activityHistoryList")
        self.activity_list.setMinimumHeight(84)
        self.activity_list.setWordWrap(True)
        right.addWidget(self.activity_list, 2)
        history_title = QLabel("Progress history")
        history_title.setObjectName("sectionTitle")
        right.addWidget(history_title)
        self.event_list = QListWidget()
        self.event_list.setObjectName("goalHistoryList")
        self.event_list.setMinimumHeight(76)
        self.event_list.setWordWrap(True)
        right.addWidget(self.event_list, 1)

        right_actions = QHBoxLayout()
        self.edit_goal_button = QPushButton("Edit")
        self.edit_goal_button.setObjectName("secondaryAction")
        self.edit_goal_button.clicked.connect(self._edit_goal)
        self.status_button = QPushButton("Pause goal")
        self.status_button.setObjectName("secondaryAction")
        self.status_button.clicked.connect(self._toggle_pause)
        self.complete_goal_button = QPushButton("Complete goal")
        self.complete_goal_button.setObjectName("warningAction")
        self.complete_goal_button.clicked.connect(self._complete_goal)
        right_actions.addWidget(self.edit_goal_button)
        right_actions.addStretch(1)
        right_actions.addWidget(self.status_button)
        right_actions.addWidget(self.complete_goal_button)
        right.addLayout(right_actions)
        right_card.addLayout(right, 1)
        right_scroll = QScrollArea()
        right_scroll.setObjectName("goalDetailScroll")
        right_scroll.setWidgetResizable(True)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right_scroll.viewport().setStyleSheet("background-color: #F8F7F3;")
        right_scroll.setWidget(right_frame)
        body.addWidget(right_scroll, 2)
        root.addLayout(body, 1)
        self.status_line = QLabel("Original files are copied into Amigo Materials and left unchanged.")
        self.status_line.setObjectName("footnote")
        self.status_line.setWordWrap(True)
        root.addWidget(self.status_line)
        self._refresh_goals()

    def _selected_goal_id(self) -> str | None:
        item = self.goal_list.currentItem()
        value = item.data(Qt.ItemDataRole.UserRole) if item else None
        return str(value) if value else None

    def select_goal(self, goal_id: str) -> None:
        for index in range(self.goal_list.count()):
            item = self.goal_list.item(index)
            if str(item.data(Qt.ItemDataRole.UserRole) or "") == goal_id:
                self.goal_list.setCurrentItem(item)
                return

    def _refresh_goals(self, preserve_id: str | None = None) -> None:
        selected_id = preserve_id or self._selected_goal_id()
        goals = self.goal_service.list_goals()
        self.goal_list.blockSignals(True)
        self.goal_list.clear()
        selected = None
        for goal in goals:
            item = QListWidgetItem(f"{goal['title']}   ·   {goal['status'].title()}")
            item.setData(Qt.ItemDataRole.UserRole, goal["id"])
            item.setToolTip(str(goal.get("description") or ""))
            self.goal_list.addItem(item)
            if str(goal["id"]) == selected_id:
                selected = item
        if not goals:
            empty_item = QListWidgetItem("Your goals will appear here.\nCreate one to start tracking progress.")
            empty_item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.goal_list.addItem(empty_item)
        if selected is None and self.goal_list.count():
            selected = self.goal_list.item(0)
        if selected is not None:
            self.goal_list.setCurrentItem(selected)
        self.goal_list.blockSignals(False)
        self._render_selected_goal()

    def _selection_changed(self, _current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        self._render_selected_goal()

    def _render_selected_goal(self) -> None:
        goal_id = self._selected_goal_id()
        self.milestone_list.clear()
        self.activity_list.clear()
        self.event_list.clear()
        if not goal_id:
            self._goal_is_completed = False
            self.goal_title.setText("No goals yet")
            self.goal_meta.setText("Create a goal, then add milestones and record actual activities.")
            self.goal_description.clear()
            self.topics_label.setText("Topics and materials can be connected after you create a goal.")
            self.milestone_progress.setVisible(False)
            self.milestone_progress_label.setText("Add milestones to track goal progress.")
            for button in (self.link_topic_button, self.add_milestone_button, self.complete_milestone_button,
                           self.edit_goal_button, self.status_button, self.complete_goal_button):
                button.setEnabled(False)
            self.log_activity_button.setEnabled(True)
            self.milestone_list.addItem("Milestones will appear here after you create a goal.")
            for activity in self.activity_service.list_activities(limit=8):
                duration = f" · {activity['duration_minutes']} min" if activity.get("duration_minutes") else ""
                goals = f" · {activity['goal_names']}" if activity.get("goal_names") else ""
                topics = f" · {activity['topic_names']}" if activity.get("topic_names") else ""
                origin = " · by Amigo" if activity.get("origin") == "assistant" else " · by voice" if activity.get("origin") == "voice" else ""
                self.activity_list.addItem(
                    f"{activity['occurred_at'][:16]}  ·  {activity['title']}{duration}{goals}{topics}{origin}"
                )
            if not self.activity_list.count():
                self.activity_list.addItem("No activity logged yet. Record completed work to build a history.")
            self.event_list.addItem("Goal and assistant changes will appear here.")
            return
        goal = self.goal_service.get_goal(goal_id)
        if not goal:
            self._refresh_goals()
            return
        self._goal_is_completed = goal["status"] == GoalStatus.COMPLETED.value
        for button in (self.link_topic_button, self.add_milestone_button, self.edit_goal_button):
            button.setEnabled(True)
        progress = self.progress_query.goal_progress(goal_id)
        self.goal_title.setText(str(goal["title"]))
        target = f" · target {goal['target_date']}" if goal.get("target_date") else ""
        self.goal_meta.setText(
            f"{goal['status'].title()}{target} · Milestones {progress.milestone_completed}/{progress.milestone_total}"
            f" · {progress.activity_count} activities · {progress.activity_minutes} logged minutes"
        )
        self.goal_description.setText(str(goal.get("description") or ""))
        if progress.milestone_total:
            self.milestone_progress.setRange(0, progress.milestone_total)
            self.milestone_progress.setValue(progress.milestone_completed)
            self.milestone_progress.setVisible(True)
            self.milestone_progress_label.setText(
                f"{progress.milestone_completed} of {progress.milestone_total} milestones"
            )
        else:
            self.milestone_progress.setVisible(False)
            self.milestone_progress_label.setText("Add milestones to track goal progress.")
        self.status_button.setEnabled(goal["status"] != GoalStatus.COMPLETED.value)
        self.status_button.setText("Resume goal" if goal["status"] == GoalStatus.PAUSED.value else "Pause goal")
        self.complete_goal_button.setEnabled(goal["status"] != GoalStatus.COMPLETED.value)

        for milestone in self.goal_service.list_milestones(goal_id):
            suffix = "✓" if milestone["status"] == "completed" else "○"
            if milestone.get("target_date"):
                suffix += f"  ·  {milestone['target_date']}"
            item = QListWidgetItem(f"{suffix}  {milestone['title']}")
            item.setData(Qt.ItemDataRole.UserRole, milestone["id"])
            item.setData(Qt.ItemDataRole.UserRole + 1, milestone["status"])
            self.milestone_list.addItem(item)
        if not self.milestone_list.count():
            self.milestone_list.addItem("Break this goal into a few clear, finishable steps.")
        else:
            self.milestone_list.setCurrentRow(0)
        self._milestone_selection_changed(self.milestone_list.currentItem(), None)

        topic_lines: list[str] = []
        for topic in self.knowledge_service.topics_for_goal(goal_id):
            names = [str(material["title"]) for material in self.knowledge_service.materials_for_topic(str(topic["id"]))]
            material_text = f" — {', '.join(names[:4])}" if names else ""
            topic_lines.append(f"• {topic['name']}{material_text}")
        self.topics_label.setText("\n".join(topic_lines) if topic_lines else "No topics linked yet.")

        for activity in self.activity_service.list_activities(goal_id=goal_id, limit=8):
            duration = f" · {activity['duration_minutes']} min" if activity.get("duration_minutes") else ""
            topic = f" · {activity['topic_names']}" if activity.get("topic_names") else ""
            origin = " · by Amigo" if activity.get("origin") == "assistant" else " · by voice" if activity.get("origin") == "voice" else ""
            item = QListWidgetItem(f"{activity['occurred_at'][:16]}  ·  {activity['title']}{duration}{topic}{origin}")
            if activity.get("reflection"):
                item.setToolTip(str(activity["reflection"]))
            self.activity_list.addItem(item)
        if not self.activity_list.count():
            self.activity_list.addItem("No activities yet. Log completed work to build your history.")
        for event in self.goal_service.list_events(goal_id, limit=10):
            event_label = {
                "milestone_completed": "Milestone completed",
                "milestone_reopened": "Milestone reopened",
                "milestone_added": "Milestone added",
                "status_changed": "Goal status changed",
                "created": "Goal created",
                "updated": "Goal updated",
            }.get(str(event["event_type"]), str(event["event_type"]).replace("_", " ").title())
            details = str(event["details"] or "").strip()
            suffix = f" · {details}" if details and details.casefold() != event_label.casefold() else ""
            self.event_list.addItem(f"{event['created_at'][:16]}  ·  {event_label}{suffix}")
        for action in self.activity_service.list_assistant_actions(goal_id=goal_id, limit=10):
            activity_title = f" · {action['activity_title']}" if action.get("activity_title") else ""
            self.event_list.addItem(
                f"{action['created_at'][:16]}  ·  Amigo {action['outcome']}{activity_title}: {action['result_text']}"
            )
        if not self.event_list.count():
            self.event_list.addItem("Goal changes and assistant activity will appear here.")
        self.log_activity_button.setEnabled(True)

    def _milestone_selection_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        status = str(current.data(Qt.ItemDataRole.UserRole + 1) or "") if current else ""
        can_toggle = status in {"pending", "completed"} and not self._goal_is_completed
        self.complete_milestone_button.setText("Reopen milestone" if status == "completed" else "Mark complete")
        self.complete_milestone_button.setEnabled(can_toggle)

    def _add_goal(self) -> None:
        dialog = GoalEditorDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            values = dialog.values()
            self.goal_service.create_goal(
                values["title"], values["description"], target_date=values["target_date"], priority=values["priority"]
            )
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Goal not saved", str(exc))
            return
        self._refresh_goals()

    def _edit_goal(self) -> None:
        goal_id = self._selected_goal_id()
        goal = self.goal_service.get_goal(goal_id) if goal_id else None
        if not goal:
            return
        dialog = GoalEditorDialog(self, goal)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            values = dialog.values()
            self.goal_service.update_goal(goal_id, **values)
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Goal not updated", str(exc))
            return
        self._refresh_goals(goal_id)

    def _add_milestone(self) -> None:
        goal_id = self._selected_goal_id()
        if not goal_id:
            return
        title, accepted = QInputDialog.getText(self, "Add milestone", "Milestone name:")
        if not accepted:
            return
        try:
            self.goal_service.add_milestone(goal_id, title)
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Milestone not saved", str(exc))
            return
        self._refresh_goals(goal_id)

    def _complete_selected_milestone(self, _item: QListWidgetItem | None = None) -> None:
        item = self.milestone_list.currentItem()
        if not item or self._goal_is_completed:
            return
        milestone_id = str(item.data(Qt.ItemDataRole.UserRole))
        try:
            if item.data(Qt.ItemDataRole.UserRole + 1) == "completed":
                self.goal_service.reopen_milestone(milestone_id)
            elif item.data(Qt.ItemDataRole.UserRole + 1) == "pending":
                self.goal_service.complete_milestone(milestone_id)
            else:
                return
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Milestone not updated", str(exc))
            return
        self._refresh_goals(self._selected_goal_id())

    def _toggle_pause(self) -> None:
        goal_id = self._selected_goal_id()
        goal = self.goal_service.get_goal(goal_id) if goal_id else None
        if not goal:
            return
        status = "active" if goal["status"] == GoalStatus.PAUSED.value else "paused"
        action = "Resume" if status == "active" else "Pause"
        answer = QMessageBox.question(self, f"{action} goal", f"{action} “{goal['title']}”?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.goal_service.change_status(goal_id, status)
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Goal not updated", str(exc))
            return
        self._refresh_goals(goal_id)

    def _complete_goal(self) -> None:
        goal_id = self._selected_goal_id()
        goal = self.goal_service.get_goal(goal_id) if goal_id else None
        if not goal:
            return
        answer = QMessageBox.question(
            self, "Complete goal", f"Mark “{goal['title']}” as completed? Its milestones and activity history will be kept."
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.goal_service.change_status(goal_id, "completed")
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Goal not updated", str(exc))
            return
        self._refresh_goals(goal_id)

    def _link_topic(self) -> None:
        goal_id = self._selected_goal_id()
        if not goal_id:
            return
        topic_name, accepted = QInputDialog.getText(self, "Link a topic", "Topic name:")
        if not accepted:
            return
        try:
            self.knowledge_service.link_topic_to_goal(goal_id, topic_name)
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Topic not linked", str(exc))
            return
        self._render_selected_goal()

    def _log_activity(self) -> None:
        dialog = ActivityDialog(self.database, self, self._selected_goal_id())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        try:
            self.activity_service.log_activity(**values)
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Activity not recorded", str(exc))
            return
        self._refresh_goals(self._selected_goal_id())
        self.status_line.setText("Activity recorded from the app.")

    def _import_material(self) -> None:
        if self._import_worker is not None and self._import_worker.isRunning():
            return
        start_folder = LEGACY_WORKSPACE_DIR if LEGACY_WORKSPACE_DIR.is_dir() else WORKSPACE_DIR
        source, _filter = QFileDialog.getOpenFileName(self, "Choose an original material", str(start_folder))
        if not source:
            return
        existing_topics = [str(item["name"]) for item in self.knowledge_service.list_topics()]
        initial = existing_topics[0] if existing_topics else ""
        topic_name, accepted = QInputDialog.getText(
            self, "Connect material to a topic", "Topic name (required):", text=initial
        )
        if not accepted:
            return
        if not topic_name.strip():
            QMessageBox.information(self, "Topic required", "Choose a topic so this material has a clear place in Amigo.")
            return
        self.import_button.setEnabled(False)
        self.status_line.setText("Copying the selected file into Amigo Materials…")
        self._import_worker = MaterialImportWorker(self.database, source, topic_name.strip())
        self._import_worker.imported.connect(self._material_imported)
        self._import_worker.failed.connect(self._material_import_failed)
        self._import_worker.finished.connect(self._material_import_finished)
        self._import_worker.start()

    def _material_imported(self, record: object, index_state: object) -> None:
        title = getattr(record, "title", "Material")
        relative_path = getattr(record, "relative_path", "")
        state = str((index_state or {}).get("status", "failed")) if isinstance(index_state, dict) else "failed"
        if state == "indexed":
            detail = "copied, linked, and indexed for local search"
        else:
            reason = str((index_state or {}).get("error_message", "")) if isinstance(index_state, dict) else ""
            detail = f"copied and linked; search indexing {state}" + (f" · {reason}" if reason else "")
        self.status_line.setText(f"“{title}” · {relative_path} · {detail}. The original file was left unchanged.")
        self._render_selected_goal()

    def _material_import_failed(self, message: str) -> None:
        self.status_line.setText("Material was not imported.")
        QMessageBox.warning(self, "Material not imported", message)

    def _material_import_finished(self) -> None:
        self.import_button.setEnabled(True)
        self._import_worker = None
        self._refresh_goals()

    def is_busy(self) -> bool:
        return bool(self._import_worker is not None and self._import_worker.isRunning())


class MaterialIndexWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, database: SqliteDatabase, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.database = database

    def run(self) -> None:
        try:
            result = KnowledgeService(self.database).reindex_all()
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.completed.emit(result)


class KnowledgeLibraryPanel(QWidget):
    """Local Markdown notes, registered materials, and full-text search."""

    askRequested = Signal(str)
    compareRequested = Signal(str)

    def __init__(self, database: SqliteDatabase, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.database = database
        self.knowledge = KnowledgeService(database)
        self.links = KnowledgeLinkService(database)
        self._import_worker: MaterialImportWorker | None = None
        self._index_worker: MaterialIndexWorker | None = None
        self._note_id: str | None = None
        self._search_results: list[dict[str, Any]] = []
        self.setStyleSheet(PRODUCT_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)
        intro = QLabel("Knowledge · your notes and reading")
        intro.setObjectName("pageTitle")
        root.addWidget(intro)
        help_text = QLabel("Search text from your Markdown notes and registered materials. Sources stay on this device.")
        help_text.setObjectName("pageDescription")
        help_text.setWordWrap(True)
        root.addWidget(help_text)

        search_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search your notes and materials…")
        self.search_edit.returnPressed.connect(self._search)
        self.search_button = QPushButton("Search")
        self.search_button.setObjectName("primaryAction")
        self.search_button.clicked.connect(self._search)
        search_row.addWidget(self.search_edit, 1)
        search_row.addWidget(self.search_button)
        root.addLayout(search_row)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        search_page = QWidget()
        search_layout = QVBoxLayout(search_page)
        self.result_list = QListWidget()
        self.result_list.currentRowChanged.connect(self._show_result)
        self.result_detail = QPlainTextEdit()
        self.result_detail.setReadOnly(True)
        self.result_detail.setPlaceholderText("Search results show a matching passage and its local source.")
        self.result_detail.setMaximumHeight(190)
        self.ask_button = QPushButton("Ask Amigo about these sources")
        self.ask_button.setObjectName("primaryAction")
        self.ask_button.clicked.connect(self._ask_from_results)
        self.ask_button.setEnabled(False)
        search_layout.addWidget(self.result_list, 2)
        search_layout.addWidget(self.result_detail, 1)
        search_layout.addWidget(self.ask_button)
        self.tabs.addTab(search_page, "Search")

        notes_page = QWidget()
        notes_layout = QHBoxLayout(notes_page)
        notes_layout.setContentsMargins(4, 6, 4, 4)
        note_list_panel = QVBoxLayout()
        self.note_list = QListWidget()
        self.note_list.currentItemChanged.connect(self._load_selected_note)
        self.new_note_button = QPushButton("+ New note")
        self.new_note_button.setObjectName("primaryAction")
        self.new_note_button.clicked.connect(self._new_note)
        note_list_panel.addWidget(self.note_list, 1)
        note_list_panel.addWidget(self.new_note_button)
        note_editor_panel = QVBoxLayout()
        self.note_title = QLineEdit()
        self.note_title.setPlaceholderText("Note title")
        self.note_topic = QComboBox()
        self.note_topic.setEditable(True)
        self.note_topic.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.note_topic.setPlaceholderText("Topic")
        self.note_content = QPlainTextEdit()
        self.note_content.setPlaceholderText("Write a Markdown note…")
        self.save_note_button = QPushButton("Save note")
        self.save_note_button.setObjectName("primaryAction")
        self.save_note_button.clicked.connect(self._save_note)
        note_editor_panel.addWidget(self.note_title)
        note_editor_panel.addWidget(self.note_topic)
        note_editor_panel.addWidget(self.note_content, 1)
        note_editor_panel.addWidget(self.save_note_button)
        notes_layout.addLayout(note_list_panel, 2)
        notes_layout.addLayout(note_editor_panel, 4)
        self.tabs.addTab(notes_page, "Notes")

        materials_page = QWidget()
        materials_layout = QVBoxLayout(materials_page)
        material_actions = QHBoxLayout()
        self.import_material_button = QPushButton("Import a material…")
        self.import_material_button.setObjectName("primaryAction")
        self.import_material_button.setToolTip("Copy a file into Amigo Materials and index readable text.")
        self.import_material_button.clicked.connect(self._import_material)
        self.reindex_button = QPushButton("Index all")
        self.reindex_button.setObjectName("secondaryAction")
        self.reindex_button.clicked.connect(self._reindex_all)
        self.compare_materials_button = QPushButton("Compare research…")
        self.compare_materials_button.setObjectName("secondaryAction")
        self.compare_materials_button.clicked.connect(self._compare_materials)
        material_actions.addWidget(self.import_material_button)
        material_actions.addWidget(self.reindex_button)
        material_actions.addWidget(self.compare_materials_button)
        materials_layout.addLayout(material_actions)
        self.material_list = QListWidget()
        materials_layout.addWidget(self.material_list, 1)
        self.material_status = QLabel("Original files are copied into Amigo Materials and left unchanged.")
        self.material_status.setObjectName("footnote")
        self.material_status.setWordWrap(True)
        materials_layout.addWidget(self.material_status)
        self.tabs.addTab(materials_page, "Materials")

        discoveries_page = QWidget()
        discoveries_layout = QHBoxLayout(discoveries_page)
        discoveries_layout.setContentsMargins(4, 6, 4, 4)
        self.discovery_list = QListWidget()
        self.discovery_list.currentItemChanged.connect(self._show_discovery)
        self.discovery_detail = QPlainTextEdit()
        self.discovery_detail.setReadOnly(True)
        self.discovery_detail.setPlaceholderText("Saved curiosity prompts and discoveries appear here.")
        discoveries_layout.addWidget(self.discovery_list, 2)
        discoveries_layout.addWidget(self.discovery_detail, 3)
        self.tabs.addTab(discoveries_page, "Saved")

        self._refresh_topics()
        self._refresh_notes()
        self._refresh_materials()
        self._refresh_discoveries()

    def _refresh_topics(self) -> None:
        current = self.note_topic.currentText()
        self.note_topic.clear()
        self.note_topic.addItems([str(row["name"]) for row in self.links.list_topics()])
        if current:
            self.note_topic.setCurrentText(current)
        elif not self.note_topic.count():
            self.note_topic.setCurrentText("General")

    def _refresh_notes(self, preserve_id: str | None = None) -> None:
        selected_id = preserve_id or self._note_id
        self.note_list.blockSignals(True)
        self.note_list.clear()
        selected = None
        for note in self.knowledge.list_notes():
            item = QListWidgetItem(f"{note['title']}  ·  {note['topic_name']}")
            item.setData(Qt.ItemDataRole.UserRole, note["id"])
            item.setToolTip(f"Knowledge/{note['relative_path']}")
            self.note_list.addItem(item)
            if note["id"] == selected_id:
                selected = item
        if self.note_list.count() == 0:
            empty_item = QListWidgetItem("No notes yet.\nCreate a note to keep a useful idea close.")
            empty_item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.note_list.addItem(empty_item)
        if selected:
            self.note_list.setCurrentItem(selected)
        self.note_list.blockSignals(False)
        if selected:
            self._load_selected_note(selected, None)

    def _load_selected_note(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        note_id = str(current.data(Qt.ItemDataRole.UserRole))
        try:
            note = self.knowledge.read_note(note_id)
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Note unavailable", str(exc))
            return
        self._note_id = note_id
        self.note_title.setText(str(note["title"]))
        self.note_topic.setCurrentText(str(note["topic_name"]))
        self.note_content.setPlainText(str(note["content"]))

    def _new_note(self) -> None:
        self.note_list.clearSelection()
        self._note_id = None
        self.note_title.setText("Untitled note")
        self.note_topic.setCurrentText(self.note_topic.currentText() or "General")
        self.note_content.clear()
        self.note_content.setFocus()

    def _save_note(self) -> None:
        try:
            record = self.knowledge.save_note(
                self.note_title.text(), self.note_topic.currentText(), self.note_content.toPlainText(), self._note_id
            )
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Note not saved", str(exc))
            return
        self._note_id = str(record["id"])
        self._refresh_topics()
        self._refresh_notes(self._note_id)
        self.material_status.setText(f"Saved Knowledge/{record['relative_path']} and updated local search.")

    def _search(self) -> None:
        query = self.search_edit.text().strip()
        self.result_list.clear()
        self.result_detail.clear()
        self._search_results = self.knowledge.search(query)
        for result in self._search_results:
            locator = f" · {result['source_locator']}" if result.get("source_locator") else ""
            topic = f" · {result['topic_name']}" if result.get("topic_name") else ""
            self.result_list.addItem(f"{result['title']}  ·  {self.knowledge.source_path(result)}{locator}{topic}")
        self.ask_button.setEnabled(bool(self._search_results and query))
        if self._search_results:
            self.result_list.setCurrentRow(0)
        else:
            self.result_detail.setPlainText("No matching indexed text. Import a material or write a note, then search again.")

    def _show_result(self, row: int) -> None:
        if row < 0 or row >= len(self._search_results):
            return
        result = self._search_results[row]
        locator = f"\nLocation: {result['source_locator']}" if result.get("source_locator") else ""
        topic = f"\nTopic: {result['topic_name']}" if result.get("topic_name") else ""
        body = str(result.get("excerpt") or result.get("body") or "")
        self.result_detail.setPlainText(
            f"{result['title']}\nSource: {self.knowledge.source_path(result)}{locator}{topic}\n\n{body}"
        )

    def _ask_from_results(self) -> None:
        query = self.search_edit.text().strip()
        if query and self._search_results:
            self.askRequested.emit(f"Use my local notes and materials to answer: {query}")

    def _refresh_materials(self) -> None:
        self.material_list.clear()
        materials = self.knowledge.list_materials()
        for material in materials:
            status = str(material.get("index_status") or "not indexed")
            topics = str(material.get("topic_names") or "unlinked")
            item = QListWidgetItem(f"{material['title']}  ·  {topics}  ·  {status}")
            item.setData(Qt.ItemDataRole.UserRole, str(material["id"]))
            item.setToolTip(str(material.get("error_message") or material["relative_path"]))
            self.material_list.addItem(item)
        if not materials:
            empty_item = QListWidgetItem("No materials yet.\nImport a file to index searchable local text.")
            empty_item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.material_list.addItem(empty_item)

    def _refresh_discoveries(self) -> None:
        from .features import PersonalFeatures

        self.discovery_list.clear()
        for discovery in PersonalFeatures(self.database).saved_discoveries():
            item = QListWidgetItem(f"{discovery['title']}  ·  {discovery.get('topic_name') or 'General'}")
            item.setData(Qt.ItemDataRole.UserRole, str(discovery["id"]))
            self.discovery_list.addItem(item)
        if not self.discovery_list.count():
            empty = QListWidgetItem("Nothing saved from curiosity yet.")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.discovery_list.addItem(empty)

    def _show_discovery(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        from .features import PersonalFeatures

        discovery_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        row = next((item for item in PersonalFeatures(self.database).saved_discoveries()
                    if str(item["id"]) == str(discovery_id)), None) if discovery_id else None
        self.discovery_detail.setPlainText(str(row["content"]) if row else "")

    def open_search_result(self, kind: str, record_id: str) -> None:
        if kind == "note":
            self.tabs.setCurrentIndex(1)
            for index in range(self.note_list.count()):
                item = self.note_list.item(index)
                if str(item.data(Qt.ItemDataRole.UserRole) or "") == record_id:
                    self.note_list.setCurrentItem(item)
                    return
        elif kind == "material":
            self.tabs.setCurrentIndex(2)
            for index in range(self.material_list.count()):
                item = self.material_list.item(index)
                if str(item.data(Qt.ItemDataRole.UserRole) or "") == record_id:
                    self.material_list.setCurrentItem(item)
                    return
        elif kind == "discovery":
            self.tabs.setCurrentIndex(3)
            for index in range(self.discovery_list.count()):
                item = self.discovery_list.item(index)
                if str(item.data(Qt.ItemDataRole.UserRole) or "") == record_id:
                    self.discovery_list.setCurrentItem(item)
                    return
        else:
            self.tabs.setCurrentIndex(0)
            if kind == "topic":
                topic = next((item for item in self.links.list_topics() if str(item["id"]) == record_id), None)
                self.search_edit.setText(str(topic["name"]) if topic else "")
            else:
                result = next((item for item in getattr(self, "_search_results", []) if str(item.get("id")) == record_id), None)
                if result:
                    self.search_edit.setText(str(result.get("title") or ""))
            self._search()

    def _compare_materials(self) -> None:
        from .features_ui import ResearchCompareDialog

        dialog = ResearchCompareDialog(self.database, self)
        dialog.askRequested.connect(self.compareRequested.emit)
        dialog.exec()

    def _import_material(self) -> None:
        if self.is_busy():
            return
        source, _filter = QFileDialog.getOpenFileName(self, "Choose an original material", str(WORKSPACE_DIR))
        if not source:
            return
        topics = [str(item["name"]) for item in self.links.list_topics()]
        topic_name, accepted = QInputDialog.getText(
            self, "Connect material to a topic", "Topic name (required):", text=topics[0] if topics else ""
        )
        if not accepted:
            return
        if not topic_name.strip():
            QMessageBox.information(self, "Topic required", "Choose a topic so this material has a clear place in Amigo.")
            return
        self.import_material_button.setEnabled(False)
        self.material_status.setText("Copying and indexing the selected file in the background…")
        self._import_worker = MaterialImportWorker(self.database, source, topic_name.strip())
        self._import_worker.imported.connect(self._material_imported)
        self._import_worker.failed.connect(self._material_failed)
        self._import_worker.finished.connect(self._import_finished)
        self._import_worker.start()

    def _material_imported(self, record: object, index_state: object) -> None:
        status = str((index_state or {}).get("status", "failed")) if isinstance(index_state, dict) else "failed"
        message = str((index_state or {}).get("error_message", "")) if isinstance(index_state, dict) else ""
        self.material_status.setText(
            f"Imported {getattr(record, 'title', 'material')} and {status} its local search text. "
            + (f"{message} " if message else "")
            + "The original file was left unchanged."
        )
        self._refresh_topics()
        self._refresh_materials()

    def _material_failed(self, message: str) -> None:
        self.material_status.setText("Material was not imported.")
        QMessageBox.warning(self, "Material not imported", message)

    def _import_finished(self) -> None:
        self.import_material_button.setEnabled(True)
        self._import_worker = None
        self._refresh_materials()

    def _reindex_all(self) -> None:
        if self.is_busy():
            return
        self.reindex_button.setEnabled(False)
        self.material_status.setText("Indexing registered materials in the background…")
        self._index_worker = MaterialIndexWorker(self.database, self)
        self._index_worker.completed.connect(self._reindex_completed)
        self._index_worker.failed.connect(self._reindex_failed)
        self._index_worker.finished.connect(self._reindex_finished)
        self._index_worker.start()

    def _reindex_completed(self, result: object) -> None:
        rows = list(result) if isinstance(result, (list, tuple)) else []
        indexed = sum(1 for row in rows if isinstance(row, dict) and row.get("status") == "indexed")
        self.material_status.setText(f"Indexed {indexed} of {len(rows)} registered materials for local search.")
        self._refresh_materials()

    def _reindex_failed(self, message: str) -> None:
        self.material_status.setText(f"Indexing failed: {message}")

    def _reindex_finished(self) -> None:
        self.reindex_button.setEnabled(True)
        self._index_worker = None

    def is_busy(self) -> bool:
        return any(worker is not None and worker.isRunning() for worker in (self._import_worker, self._index_worker))

