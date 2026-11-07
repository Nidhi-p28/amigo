from __future__ import annotations

from typing import Any

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QInputDialog,
    QLineEdit,
)

from .domain import AmigoServiceError
from .features import PROJECT_LINK_TABLES, ProjectService
from .knowledge import KnowledgeService
from .services import GoalService, KnowledgeLinkService
from .storage import SqliteDatabase
from .theme import PRODUCT_STYLE


class ProjectEditorDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, project: dict[str, Any] | None = None, kind: str = "project") -> None:
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("Edit hub" if project else "Create a study hub" if kind == "study" else "Create a project")
        self.setStyleSheet(PRODUCT_STYLE)
        self.resize(430, 330)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title_edit = QLineEdit(str((project or {}).get("title") or ""))
        self.title_edit.setPlaceholderText("Project or study hub name")
        self.description_edit = QPlainTextEdit(str((project or {}).get("description") or ""))
        self.description_edit.setPlaceholderText("What are you working toward?")
        self.description_edit.setFixedHeight(85)
        self.kind = QComboBox()
        self.kind.addItem("Project", "project")
        self.kind.addItem("Study Hub", "study")
        kind_index = self.kind.findData(str((project or {}).get("kind") or kind))
        if kind_index >= 0:
            self.kind.setCurrentIndex(kind_index)
        if project:
            self.kind.setEnabled(False)
        self.target_enabled = QCheckBox("Set a target date")
        self.target_date = QDateEdit()
        self.target_date.setCalendarPopup(True)
        self.target_date.setDisplayFormat("d MMM yyyy")
        self.target_date.setDate(QDate.currentDate())
        target = str((project or {}).get("target_date") or "")
        if target:
            parsed = QDate.fromString(target, "yyyy-MM-dd")
            if parsed.isValid():
                self.target_date.setDate(parsed)
                self.target_enabled.setChecked(True)
        self.target_date.setEnabled(self.target_enabled.isChecked())
        self.target_enabled.toggled.connect(self.target_date.setEnabled)
        form.addRow("Name", self.title_edit)
        form.addRow("Type", self.kind)
        form.addRow("Description", self.description_edit)
        form.addRow("Target", self.target_enabled)
        form.addRow("Date", self.target_date)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.title_edit.setFocus()

    def _accept_if_valid(self) -> None:
        if not self.title_edit.text().strip():
            QMessageBox.information(self, "Name required", "Enter a name for this project or study hub.")
            self.title_edit.setFocus()
            return
        self.accept()

    def values(self) -> dict[str, Any]:
        return {
            "title": self.title_edit.text().strip(),
            "description": self.description_edit.toPlainText().strip(),
            "kind": str(self.kind.currentData()),
            "target_date": self.target_date.date().toString("yyyy-MM-dd") if self.target_enabled.isChecked() else None,
        }


class ProjectsPanel(QWidget):
    askRequested = Signal(str)

    def __init__(self, database: SqliteDatabase, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.database = database
        self.projects = ProjectService(database)
        self.goals = GoalService(database)
        self.links = KnowledgeLinkService(database)
        self.knowledge = KnowledgeService(database)
        self._current_id: str | None = None
        self.setStyleSheet(PRODUCT_STYLE)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)
        heading = QLabel("Projects & Study Hubs")
        heading.setObjectName("pageTitle")
        root.addWidget(heading)
        hint = QLabel("Connect goals, topics, notes, materials, and actual work around one ongoing effort.")
        hint.setObjectName("pageDescription")
        hint.setWordWrap(True)
        root.addWidget(hint)
        body = QHBoxLayout()
        body.setSpacing(12)
        left = QFrame()
        left.setObjectName("contentCard")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(11, 11, 11, 11)
        self.project_list = QListWidget()
        self.project_list.currentItemChanged.connect(self._selection_changed)
        left_layout.addWidget(self.project_list, 1)
        create_row = QHBoxLayout()
        self.add_project_button = QPushButton("+ Project")
        self.add_project_button.setObjectName("primaryAction")
        self.add_project_button.clicked.connect(lambda: self._create("project"))
        self.add_study_button = QPushButton("+ Study Hub")
        self.add_study_button.setObjectName("secondaryAction")
        self.add_study_button.clicked.connect(lambda: self._create("study"))
        create_row.addWidget(self.add_project_button)
        create_row.addWidget(self.add_study_button)
        left_layout.addLayout(create_row)
        body.addWidget(left, 2)

        right = QFrame()
        right.setObjectName("contentCard")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(14, 12, 14, 12)
        self.title_label = QLabel("Select a project")
        self.title_label.setObjectName("pageTitle")
        self.summary_label = QLabel("A hub keeps existing Amigo records connected; it does not duplicate them.")
        self.summary_label.setObjectName("metaText")
        self.summary_label.setWordWrap(True)
        right_layout.addWidget(self.title_label)
        right_layout.addWidget(self.summary_label)
        self.description = QPlainTextEdit()
        self.description.setReadOnly(True)
        self.description.setObjectName("readPanel")
        self.description.setMaximumHeight(90)
        right_layout.addWidget(self.description)

        relation_header = QHBoxLayout()
        relation_title = QLabel("Connected work")
        relation_title.setObjectName("sectionTitle")
        relation_header.addWidget(relation_title, 1)
        self.link_button = QPushButton("Connect existing…")
        self.link_button.setObjectName("secondaryAction")
        self.link_button.clicked.connect(self._link_existing)
        relation_header.addWidget(self.link_button)
        right_layout.addLayout(relation_header)
        self.related_list = QListWidget()
        self.related_list.setObjectName("projectRelatedList")
        right_layout.addWidget(self.related_list, 1)
        actions = QHBoxLayout()
        self.edit_button = QPushButton("Edit details")
        self.edit_button.setObjectName("secondaryAction")
        self.edit_button.clicked.connect(self._edit)
        self.status_button = QPushButton("Pause hub")
        self.status_button.setObjectName("secondaryAction")
        self.status_button.clicked.connect(self._toggle_status)
        self.complete_button = QPushButton("Complete hub")
        self.complete_button.setObjectName("quietAction")
        self.complete_button.clicked.connect(self._complete_project)
        self.ask_button = QPushButton("Ask about this hub")
        self.ask_button.setObjectName("primaryAction")
        self.ask_button.clicked.connect(self._ask)
        actions.addWidget(self.edit_button)
        actions.addWidget(self.status_button)
        actions.addWidget(self.complete_button)
        actions.addStretch(1)
        actions.addWidget(self.ask_button)
        right_layout.addLayout(actions)
        body.addWidget(right, 5)
        root.addLayout(body, 1)
        self.status = QLabel("")
        self.status.setObjectName("footnote")
        root.addWidget(self.status)
        self._refresh_list()

    def refresh(self) -> None:
        """Reload hub summaries and linked records when the page is opened."""
        self._refresh_list(self._current_id)

    def _refresh_list(self, preserve_id: str | None = None) -> None:
        selected_id = preserve_id or self._current_id
        projects = self.projects.list_projects()
        self.project_list.blockSignals(True)
        self.project_list.clear()
        selected = None
        for project in projects:
            kind = "Study Hub" if project["kind"] == "study" else "Project"
            item = QListWidgetItem(f"{project['title']}  ·  {kind} · {project['status']}")
            item.setData(Qt.ItemDataRole.UserRole, str(project["id"]))
            self.project_list.addItem(item)
            if str(project["id"]) == selected_id:
                selected = item
        if not projects:
            empty = QListWidgetItem("No projects yet.\nCreate a hub to gather related work.")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.project_list.addItem(empty)
        elif selected is None:
            selected = self.project_list.item(0)
        if selected:
            self.project_list.setCurrentItem(selected)
        self.project_list.blockSignals(False)
        self._render_project()

    def _selected_id(self) -> str | None:
        item = self.project_list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item and item.data(Qt.ItemDataRole.UserRole) else None

    def select_project(self, project_id: str) -> None:
        for index in range(self.project_list.count()):
            item = self.project_list.item(index)
            if str(item.data(Qt.ItemDataRole.UserRole) or "") == project_id:
                self.project_list.setCurrentItem(item)
                break

    def _selection_changed(self, _current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        self._render_project()

    def _render_project(self) -> None:
        self._current_id = self._selected_id()
        project = self.projects.get_project(self._current_id) if self._current_id else None
        self.related_list.clear()
        enabled = project is not None
        for widget in (self.link_button, self.edit_button, self.status_button, self.complete_button, self.ask_button):
            widget.setEnabled(enabled)
        if not project:
            self.title_label.setText("Your work, in one place")
            self.summary_label.setText("Projects and Study Hubs connect existing records without copying their content.")
            self.description.clear()
            self.related_list.addItem("Connected goals, topics, notes, materials, and activities will appear here.")
            return
        self.title_label.setText(str(project["title"]))
        target = f" · target {project['target_date']}" if project.get("target_date") else ""
        self.summary_label.setText(f"{'Study Hub' if project['kind']=='study' else 'Project'} · {project['status'].title()}{target}")
        self.description.setPlainText(str(project.get("description") or "Add a description to explain what this hub is for."))
        counts = []
        for key, label in (("goals", "goals"), ("topics", "topics"), ("notes", "notes"), ("materials", "materials"), ("activities", "activities")):
            entries = project.get(key, [])
            counts.append(f"{len(entries)} {label}")
            for entry in entries:
                title = str(entry.get("title") or entry.get("name") or "Untitled")
                self.related_list.addItem(f"{label[:-1].title()}  ·  {title}  ·  {entry.get('status','linked')}")
        self.status_button.setText("Resume hub" if project["status"] == "paused" else "Pause hub")
        self.status_button.setEnabled(project["status"] != "completed")
        self.complete_button.setEnabled(project["status"] != "completed")
        self.status.setText(" · ".join(counts))

    def _create(self, kind: str) -> None:
        dialog = ProjectEditorDialog(self, kind=kind)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            project = self.projects.create_project(**dialog.values())
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Hub not created", str(exc))
            return
        self._refresh_list(str(project["id"]))

    def _edit(self) -> None:
        project_id = self._selected_id()
        project = self.projects.get_project(project_id) if project_id else None
        if not project:
            return
        dialog = ProjectEditorDialog(self, project)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.projects.update_project(project_id, **{key: dialog.values()[key] for key in ("title", "description", "target_date")})
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Hub not updated", str(exc))
            return
        self._refresh_list(project_id)

    def _toggle_status(self) -> None:
        project_id = self._selected_id()
        project = self.projects.get_project(project_id) if project_id else None
        if not project or project["status"] == "completed":
            return
        status = "active" if project["status"] == "paused" else "paused"
        try:
            self.projects.change_status(project_id, status)
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Hub not updated", str(exc))
            return
        self._refresh_list(project_id)

    def _complete_project(self) -> None:
        project_id = self._selected_id()
        project = self.projects.get_project(project_id) if project_id else None
        if not project or project["status"] == "completed":
            return
        answer = QMessageBox.question(
            self, "Complete this hub?", f"Mark {project['title']} as completed? You can still view its linked work."
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.projects.change_status(project_id, "completed")
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Hub not updated", str(exc))
            return
        self._refresh_list(project_id)

    def _link_existing(self) -> None:
        project_id = self._selected_id()
        if not project_id:
            return
        labels = ("Goal", "Topic", "Material", "Knowledge note", "Activity")
        label, accepted = QInputDialog.getItem(self, "Connect existing record", "Record type", labels, 0, False)
        if not accepted:
            return
        kind = {"Goal": "goal", "Topic": "topic", "Material": "material", "Knowledge note": "note", "Activity": "activity"}[label]
        if kind == "goal":
            records = self.goals.list_goals()
            pairs = [(str(row["id"]), str(row["title"])) for row in records]
        elif kind == "topic":
            records = self.links.list_topics()
            pairs = [(str(row["id"]), str(row["name"])) for row in records]
        elif kind == "material":
            records = self.knowledge.list_materials()
            pairs = [(str(row["id"]), str(row["title"])) for row in records]
        elif kind == "note":
            records = self.knowledge.list_notes()
            pairs = [(str(row["id"]), f"{row['title']} · {row['topic_name']}") for row in records]
        else:
            from .services import ActivityService

            records = ActivityService(self.database).list_activities(limit=100)
            pairs = [(str(row["id"]), str(row["title"])) for row in records]
        if not pairs:
            QMessageBox.information(self, "Nothing to connect", f"Create or import a {kind} first.")
            return
        counts: dict[str, int] = {}
        for _identifier, name in pairs:
            counts[name] = counts.get(name, 0) + 1
        choices = [
            (f"{name} · {identifier[:8]}" if counts[name] > 1 else name, identifier)
            for identifier, name in pairs
        ]
        choice, accepted = QInputDialog.getItem(
            self, "Choose a record", f"{label} to connect", [name for name, _identifier in choices], 0, False
        )
        if not accepted:
            return
        entity_id = next(identifier for name, identifier in choices if name == choice)
        try:
            self.projects.link(project_id, kind, entity_id)
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Record not connected", str(exc))
            return
        self._refresh_list(project_id)

    def _ask(self) -> None:
        project_id = self._selected_id()
        if not project_id:
            return
        try:
            context = self.projects.assistant_context(project_id)
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Hub unavailable", str(exc))
            return
        self.askRequested.emit(
            "Use my local notes and materials to answer: Help me with this project using the connected context below. "
            "Keep recommendations grounded in my linked local materials.\n\n" + context + "\n\nWhat should I work on next?"
        )
