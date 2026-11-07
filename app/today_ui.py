from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime

from PySide6.QtCore import QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

from .domain import AmigoServiceError
from .features import PersonalFeatures, ProjectService, ProfileService
from .services import GoalService, KnowledgeLinkService
from .storage import SqliteDatabase
from .theme import PRODUCT_STYLE, TODAY_STYLE
from .today import TodayService


class DayActivityDialog(QDialog):
    def __init__(self, database: SqliteDatabase, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add to today's list")
        self.setStyleSheet(PRODUCT_STYLE)
        self.resize(420, 230)
        self.goals = GoalService(database)
        self.links = KnowledgeLinkService(database)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("A useful next step")
        self.estimate = QSpinBox()
        self.estimate.setRange(0, 1440)
        self.estimate.setSpecialValueText("No estimate")
        self.estimate.setSuffix(" min")
        self.goal = QComboBox()
        self.goal.addItem("No linked goal", "")
        for item in self.goals.list_goals(status="active"):
            self.goal.addItem(str(item["title"]), str(item["id"]))
        self.topic = QComboBox()
        self.topic.addItem("No linked topic", "")
        for item in self.links.list_topics():
            self.topic.addItem(str(item["name"]), str(item["id"]))
        self.project = QComboBox()
        self.project.addItem("No linked project", "")
        for item in ProjectService(database).list_projects(active_only=True):
            self.project.addItem(str(item["title"]), str(item["id"]))
        form.addRow("Activity", self.title_edit)
        form.addRow("Estimated time", self.estimate)
        form.addRow("Goal", self.goal)
        form.addRow("Topic", self.topic)
        form.addRow("Project or study hub", self.project)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.title_edit.setFocus()

    def _accept_if_valid(self) -> None:
        if not self.title_edit.text().strip():
            QMessageBox.information(self, "Activity required", "Enter a short activity to add to today's list.")
            self.title_edit.setFocus()
            return
        self.accept()

    def values(self) -> dict[str, object]:
        return {
            "title": self.title_edit.text().strip(),
            "estimated_minutes": int(self.estimate.value()) or None,
            "goal_id": str(self.goal.currentData() or "") or None,
            "topic_id": str(self.topic.currentData() or "") or None,
            "project_id": str(self.project.currentData() or "") or None,
        }


class ActivityHeatmap(QWidget):
    daySelected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._month = date.today().replace(day=1)
        self._counts: dict[str, dict[str, int]] = {}
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(1)
        self._grid.setVerticalSpacing(0)

    def set_month(self, month: date, counts: dict[str, dict[str, int]]) -> None:
        self._month = month.replace(day=1)
        self._counts = counts
        while self._grid.count():
            child = self._grid.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.deleteLater()
        for column, label in enumerate(("M", "T", "W", "T", "F", "S", "S")):
            weekday = QLabel(label)
            weekday.setAlignment(Qt.AlignmentFlag.AlignCenter)
            weekday.setObjectName("heatmapWeekday")
            self._grid.addWidget(weekday, 0, column)
        first_weekday, day_count = monthrange(month.year, month.month)
        for day_number in range(1, day_count + 1):
            day = date(month.year, month.month, day_number)
            row, column = divmod(first_weekday + day_number - 1, 7)
            key = day.isoformat()
            count = int(counts.get(key, {}).get("count", 0))
            button = QToolButton()
            button.setText(str(day_number))
            button.setObjectName("heatmapDay")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFixedSize(19, 14)
            button.setProperty("intensity", min(count, 4))
            if day == date.today():
                button.setProperty("today", True)
            button.setToolTip(f"{day.strftime('%A, %B')} {day_number}: {count} recorded activit{'y' if count == 1 else 'ies'}")
            button.clicked.connect(lambda _checked=False, day_text=key: self.daySelected.emit(day_text))
            button.style().unpolish(button)
            button.style().polish(button)
            self._grid.addWidget(button, row + 1, column)
        for column in range(7):
            self._grid.setColumnStretch(column, 1)


class DotMotif(QWidget):
    """Quiet halftone accent for the dashboard header."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(56)
        self.setMaximumWidth(136)
        self.setFixedHeight(34)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        width = max(1, self.width())
        for column in range(max(1, width // 8)):
            x = 3 + column * 8
            ridge = 4 + 10 * abs(((column / max(1, width // 8)) * 2) - 1)
            for row in range(4):
                y = 4 + row * 8
                if abs(y - 18) <= ridge:
                    shade = max(160, 216 - int(abs(y - 18) * 3))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(shade, shade, shade))
                    painter.drawEllipse(x, y, 2, 2)
        painter.end()


class GardenPlant(QWidget):
    """Small plant drawing whose stage comes from recorded goal evidence."""

    def __init__(self, stage: int, status: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.stage = max(0, min(4, int(stage)))
        self.status = status
        self.setFixedSize(42, 46)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        baseline = self.height() - 8
        painter.setPen(QPen(QColor("#B7C7BA"), 1.4))
        painter.drawLine(5, baseline, self.width() - 5, baseline)
        pot = QPainterPath()
        pot.moveTo(14, baseline - 1)
        pot.lineTo(18, baseline + 6)
        pot.lineTo(30, baseline + 6)
        pot.lineTo(34, baseline - 1)
        pot.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#C58B62"))
        painter.drawPath(pot)
        if self.stage == 0:
            painter.setBrush(QColor("#8C6748"))
            painter.drawEllipse(20, baseline - 5, 8, 5)
            painter.end()
            return
        green = QColor("#789E72" if self.status == "paused" else "#4E8A5B")
        dark = QColor("#477651" if self.status == "paused" else "#2F7045")
        center_x = self.width() // 2
        top = max(3, baseline - (8 + self.stage * 6))
        painter.setPen(QPen(dark, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(center_x, baseline - 3, center_x, top)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(green)
        leaf_positions = []
        if self.stage >= 2:
            leaf_positions.extend(((center_x - 11, top + 10), (center_x + 3, top + 16)))
        if self.stage >= 3:
            leaf_positions.extend(((center_x - 12, top + 20), (center_x + 4, top + 25)))
        if self.stage >= 4:
            leaf_positions.extend(((center_x - 13, top + 3), (center_x + 3, top + 5), (center_x - 5, top - 3)))
        if self.stage == 1:
            leaf_positions.append((center_x - 4, top + 2))
        for x, y in leaf_positions:
            painter.save()
            painter.translate(x + 5, y + 3)
            painter.rotate(-32 if x < center_x else 32)
            painter.drawEllipse(-5, -3, 10, 6)
            painter.restore()
        painter.end()


class TodayPanel(QWidget):
    goalRequested = Signal(str)
    projectRequested = Signal(str)
    calendarRequested = Signal()
    knowledgeRequested = Signal()
    askRequested = Signal(str)

    def __init__(self, database: SqliteDatabase, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.database = database
        self.today = TodayService(database)
        self.goals = GoalService(database)
        self.profile = ProfileService(database)
        self._selected_month = date.today().replace(day=1)
        self.setStyleSheet(TODAY_STYLE)

        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(4, 3, 4, 3)
        self.root.setSpacing(3)
        content_layout = self.root

        # 1. Header: Greeting, Date, Halftone Motif, Status, Quick Capture
        heading_row = QHBoxLayout()
        heading_row.setSpacing(5)

        heading_text = QVBoxLayout()
        heading_text.setSpacing(2)
        self.greeting = QLabel("Good day")
        self.greeting.setObjectName("pageTitle")
        self.greeting.setProperty("dashboardHeading", True)
        self.greeting.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.date_label = QLabel("")
        self.date_label.setObjectName("pageDescription")
        self.date_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        heading_text.addWidget(self.greeting)
        heading_text.addWidget(self.date_label)
        heading_row.addLayout(heading_text, 1)

        heading_row.addWidget(DotMotif(), 0, Qt.AlignmentFlag.AlignVCenter)

        self.status = QLabel("Checking today's plan…")
        self.status.setObjectName("footnote")
        self.status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.status.setMaximumWidth(142)
        self.status.setVisible(False)
        heading_row.addWidget(self.status, 0, Qt.AlignmentFlag.AlignVCenter)

        self.capture_button = QPushButton("Capture")
        self.capture_button.setObjectName("primaryAction")
        self.capture_button.setToolTip("Quick capture a task, reflection, note, or goal")
        self.capture_button.setFixedHeight(30)
        self.capture_button.setMinimumWidth(0)
        self.capture_button.setMaximumWidth(92)
        self.capture_button.clicked.connect(self._quick_capture)
        heading_row.addWidget(self.capture_button, 0, Qt.AlignmentFlag.AlignVCenter)

        content_layout.addLayout(heading_row)

        # 2. Main 2-Column Dashboard Grid: Orderly, aligned columns left of the assistant
        columns = QHBoxLayout()
        columns.setSpacing(6)

        # ==========================================
        # COLUMN 1: Daily Focus, Checklist, Explore
        # ==========================================
        self.col1 = QVBoxLayout()
        self.col1.setSpacing(6)

        # Card 1: Focus
        self.focus_card = self._card("focusCard")
        focus_layout = QVBoxLayout(self.focus_card)
        focus_layout.setContentsMargins(8, 6, 8, 6)
        focus_layout.setSpacing(4)

        focus_header = QHBoxLayout()
        focus_title = QLabel("Focus")
        focus_title.setObjectName("sectionTitle")
        focus_title.setToolTip("Today's primary priorities")
        focus_header.addWidget(focus_title, 1)
        self.weekly_review_button = QPushButton("Review")
        self.weekly_review_button.setObjectName("quietAction")
        self.weekly_review_button.setToolTip("Open the weekly review")
        self.weekly_review_button.clicked.connect(self._weekly_review)
        focus_header.addWidget(self.weekly_review_button)
        focus_layout.addLayout(focus_header)

        self.priority_list = QListWidget()
        self.priority_list.setObjectName("priorityList")
        self.priority_list.setWordWrap(True)
        self.priority_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.priority_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.priority_list.itemClicked.connect(self._priority_clicked)
        focus_layout.addWidget(self.priority_list)

        self.priority_empty = QLabel("No focus set for today. Plan a session or goal to set your focus.")
        self.priority_empty.setObjectName("metaText")
        self.priority_empty.setWordWrap(True)
        self.priority_empty.setVisible(False)
        focus_layout.addWidget(self.priority_empty)

        focus_actions = QHBoxLayout()
        focus_actions.setSpacing(8)
        self.replan_button = QPushButton("Plan")
        self.replan_button.setObjectName("secondaryAction")
        self.replan_button.setToolTip("Review goal replanning suggestions")
        self.replan_button.clicked.connect(self._review_replan)
        self.focus_button = QPushButton("Focus")
        self.focus_button.setObjectName("primaryAction")
        self.focus_button.setToolTip("Start a focused work session")
        self.focus_button.clicked.connect(self._focus_session)
        focus_actions.addWidget(self.replan_button)
        focus_actions.addStretch(1)
        focus_actions.addWidget(self.focus_button)
        focus_layout.addLayout(focus_actions)
        self.col1.addWidget(self.focus_card)

        # Card 2: Checklist
        self.activity_card = self._card("activityCard")
        activity_layout = QVBoxLayout(self.activity_card)
        activity_layout.setContentsMargins(14, 12, 14, 12)
        activity_layout.setSpacing(8)

        activity_header = QHBoxLayout()
        activity_heading = QLabel("Checklist")
        activity_heading.setObjectName("sectionTitle")
        activity_heading.setToolTip("Today's planned activities")
        activity_header.addWidget(activity_heading, 1)
        self.activity_count = QLabel("")
        self.activity_count.setObjectName("metaText")
        activity_header.addWidget(self.activity_count)
        activity_layout.addLayout(activity_header)

        self.activity_list = QListWidget()
        self.activity_list.setObjectName("todayActivityList")
        self.activity_list.setWordWrap(True)
        self.activity_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.activity_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.activity_list.itemChanged.connect(self._activity_item_changed)
        activity_layout.addWidget(self.activity_list)

        self.activity_empty = QLabel("Nothing planned yet for today. Add a task or log completed work.")
        self.activity_empty.setObjectName("metaText")
        self.activity_empty.setWordWrap(True)
        self.activity_empty.setMaximumHeight(30)
        self.activity_empty.setVisible(False)
        activity_layout.addWidget(self.activity_empty)

        activity_actions = QHBoxLayout()
        activity_actions.setSpacing(8)
        self.add_activity_button = QPushButton("＋ Add")
        self.add_activity_button.setObjectName("secondaryAction")
        self.add_activity_button.setToolTip("Add a planned activity for today")
        self.add_activity_button.clicked.connect(self._add_activity)
        self.log_activity_button = QPushButton("Log work")
        self.log_activity_button.setObjectName("quietAction")
        self.log_activity_button.setToolTip("Record unplanned work you completed")
        self.log_activity_button.clicked.connect(self._log_unplanned)
        activity_actions.addWidget(self.add_activity_button)
        activity_actions.addWidget(self.log_activity_button)
        activity_actions.addStretch(1)
        activity_layout.addLayout(activity_actions)
        self.col1.addWidget(self.activity_card)

        # Card 3: Explore
        self.curiosity_card = self._card("curiosityCard")
        curiosity_layout = QVBoxLayout(self.curiosity_card)
        curiosity_layout.setContentsMargins(14, 12, 14, 12)
        curiosity_layout.setSpacing(8)

        curiosity_title = QLabel("Explore")
        curiosity_title.setObjectName("sectionTitle")
        curiosity_title.setToolTip("Daily curiosity & discovery prompt")
        curiosity_layout.addWidget(curiosity_title)

        self.curiosity_label = QLabel("Add a note or topic and Amigo can suggest a question from it.")
        self.curiosity_label.setObjectName("metaText")
        self.curiosity_label.setWordWrap(True)
        curiosity_layout.addWidget(self.curiosity_label, 1)

        curiosity_actions = QHBoxLayout()
        curiosity_actions.setSpacing(8)
        self.curiosity_ask_button = QPushButton("Ask")
        self.curiosity_ask_button.setObjectName("secondaryAction")
        self.curiosity_ask_button.setToolTip("Ask Amigo about this question")
        self.curiosity_ask_button.clicked.connect(self._ask_curiosity)
        self.curiosity_save_button = QPushButton("Save")
        self.curiosity_save_button.setObjectName("secondaryAction")
        self.curiosity_save_button.setToolTip("Save this question to Knowledge")
        self.curiosity_save_button.clicked.connect(self._save_curiosity)
        self.curiosity_ignore_button = QPushButton("Skip")
        self.curiosity_ignore_button.setObjectName("quietAction")
        self.curiosity_ignore_button.setToolTip("Show another question later")
        self.curiosity_ignore_button.clicked.connect(self._ignore_curiosity)
        curiosity_actions.addWidget(self.curiosity_ask_button)
        curiosity_actions.addWidget(self.curiosity_save_button)
        curiosity_actions.addWidget(self.curiosity_ignore_button)
        curiosity_actions.addStretch(1)
        curiosity_layout.addLayout(curiosity_actions)
        self.col1.addWidget(self.curiosity_card)

        self.col1.addStretch(1)
        columns.addLayout(self.col1, 1)

        # ==========================================
        # COLUMN 2: Goals, Days, Next, Garden
        # ==========================================
        self.col2 = QVBoxLayout()
        self.col2.setSpacing(6)

        # Card 4: Goals
        self.goal_card = self._card("goalsCard")
        goal_layout = QVBoxLayout(self.goal_card)
        goal_layout.setContentsMargins(8, 6, 8, 6)
        goal_layout.setSpacing(4)

        goal_header = QHBoxLayout()
        goal_heading = QLabel("Goals")
        goal_heading.setObjectName("sectionTitle")
        goal_heading.setToolTip("Active goals & milestone progress")
        goal_header.addWidget(goal_heading, 1)
        self.manage_goals_button = QPushButton("All")
        self.manage_goals_button.setObjectName("quietAction")
        self.manage_goals_button.setToolTip("View all goals")
        self.manage_goals_button.clicked.connect(lambda: self.goalRequested.emit(""))
        goal_header.addWidget(self.manage_goals_button)
        goal_layout.addLayout(goal_header)

        self.goal_items = QWidget()
        self.goal_items.setMinimumWidth(0)
        self.goal_items.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.goal_list_layout = QVBoxLayout(self.goal_items)
        self.goal_list_layout.setContentsMargins(0, 0, 0, 0)
        self.goal_list_layout.setSpacing(6)
        goal_layout.addWidget(self.goal_items)
        self.col2.addWidget(self.goal_card)

        # Card 5: Days (Heatmap)
        self.heatmap_card = self._card("heatmapCard")
        heatmap_layout = QVBoxLayout(self.heatmap_card)
        heatmap_layout.setContentsMargins(7, 5, 7, 5)
        heatmap_layout.setSpacing(2)

        heat_header = QHBoxLayout()
        heat_title = QLabel("Days")
        heat_title.setObjectName("sectionTitle")
        heat_title.setToolTip("Recorded activity heatmap")
        heat_header.addWidget(heat_title, 1)
        self.previous_month_button = QPushButton("‹")
        self.previous_month_button.setObjectName("quietAction")
        self.previous_month_button.setFixedSize(22, 22)
        self.previous_month_button.clicked.connect(lambda: self._shift_month(-1))
        self.month_label = QLabel("")
        self.month_label.setObjectName("metaText")
        self.month_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.month_label.setMinimumWidth(48)
        self.next_month_button = QPushButton("›")
        self.next_month_button.setObjectName("quietAction")
        self.next_month_button.setFixedSize(22, 22)
        self.next_month_button.clicked.connect(lambda: self._shift_month(1))
        heat_header.addWidget(self.previous_month_button)
        heat_header.addWidget(self.month_label)
        heat_header.addWidget(self.next_month_button)
        heatmap_layout.addLayout(heat_header)

        self.heatmap = ActivityHeatmap()
        self.heatmap.daySelected.connect(self._show_day_activities)
        heatmap_layout.addWidget(self.heatmap)

        self.heat_hint = QLabel("Recorded work · Select a day for details")
        self.heat_hint.setObjectName("footnote")
        self.heat_hint.setMaximumHeight(15)
        heatmap_layout.addWidget(self.heat_hint)
        self.col2.addWidget(self.heatmap_card)

        # Card 6: Next (Continue where you left off)
        self.continue_card = self._card("continueCard")
        continue_layout = QHBoxLayout(self.continue_card)
        continue_layout.setContentsMargins(8, 5, 8, 5)
        continue_layout.setSpacing(5)

        next_text_col = QVBoxLayout()
        next_text_col.setSpacing(2)
        next_title = QLabel("Next")
        next_title.setObjectName("sectionTitle")
        next_title.setToolTip("Continue where you left off")
        next_text_col.addWidget(next_title)
        self.continue_label = QLabel("Your recent work will show up here.")
        self.continue_label.setWordWrap(True)
        self.continue_label.setObjectName("metaText")
        self.continue_label.setMaximumHeight(34)
        next_text_col.addWidget(self.continue_label)
        continue_layout.addLayout(next_text_col, 1)

        self.continue_button = QPushButton("Open")
        self.continue_button.setObjectName("secondaryAction")
        self.continue_button.setFixedWidth(64)
        self.continue_button.setToolTip("Continue where you left off")
        self.continue_button.clicked.connect(self._continue)
        self.continue_button.setEnabled(False)
        continue_layout.addWidget(self.continue_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.col2.addWidget(self.continue_card)

        # Card 7: Garden (Living visual growth)
        self.garden_card = self._card("gardenCard")
        garden_layout = QHBoxLayout(self.garden_card)
        garden_layout.setContentsMargins(8, 5, 8, 5)
        garden_layout.setSpacing(6)

        gard_text_col = QVBoxLayout()
        gard_text_col.setSpacing(2)
        garden_title = QLabel("Garden")
        garden_title.setObjectName("sectionTitle")
        garden_title.setToolTip("Growth follows your work")
        gard_text_col.addWidget(garden_title)
        garden_hint = QLabel("Growth follows your work.")
        garden_hint.setObjectName("footnote")
        garden_hint.setWordWrap(True)
        gard_text_col.addWidget(garden_hint)
        garden_layout.addLayout(gard_text_col, 1)

        self.garden_layout = QHBoxLayout()
        self.garden_layout.setSpacing(6)
        garden_layout.addLayout(self.garden_layout)
        self.col2.addWidget(self.garden_card)

        self.col2.addStretch(1)
        columns.addLayout(self.col2, 1)

        content_layout.addLayout(columns, 1)

        self._continue_item: dict[str, object] | None = None
        self._curiosity_item: dict[str, object] | None = None
        self._ignored_note_id: str | None = None
        self._ignored_topic_id: str | None = None
        self.refresh()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "status"):
            self.status.setVisible(self.width() >= 620)
            QTimer.singleShot(0, self._fit_component_heights)

    def show_activity_day(self, day_text: str) -> None:
        self._show_day_activities(day_text)

    @staticmethod
    def _card(object_name: str = "contentCard") -> QFrame:
        frame = QFrame()
        frame.setObjectName(object_name)
        frame.setMinimumWidth(0)
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return frame

    def refresh(self) -> None:
        now = datetime.now().astimezone()
        hour = now.hour
        salutation = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
        profile_name = str(self.profile.get_profile().get("name") or "").strip()
        self.greeting.setText(f"{salutation}, {profile_name}" if profile_name else salutation)
        today = date.today()
        self.date_label.setText(today.strftime("%A, %d %b %Y"))
        try:
            snapshot = self.today.snapshot(today)
        except (AmigoServiceError, ValueError) as exc:
            self.status.setText(str(exc))
            return
        self._render_priorities(snapshot["priorities"])
        self._render_activity_items(snapshot["activity_items"])
        self._render_goals(snapshot["active_goals"])
        self._render_continue(snapshot["continue_item"])
        self._render_curiosity()
        self._render_garden()
        counts = snapshot["heatmap"] if self._selected_month == today.replace(day=1) else self.today.heatmap(
            self._selected_month.year, self._selected_month.month
        )
        self.month_label.setText(self._selected_month.strftime("%b'%y"))
        self.heatmap.set_month(self._selected_month, counts)
        actual_count = len(snapshot["activities"])
        sessions = len(snapshot["sessions"])
        status_text = (
            f"{actual_count} activity{'ies' if actual_count != 1 else ''} recorded today · "
            f"{sessions} planned session{'s' if sessions != 1 else ''}"
        )
        self.status.setText(f"{actual_count} logged · {sessions} sessions")
        self.status.setToolTip(status_text)
        self._fit_component_heights()

    def _fit_component_heights(self) -> None:
        cards = (
            self.focus_card, self.activity_card, self.curiosity_card,
            self.goal_card, self.heatmap_card, self.continue_card, self.garden_card,
        )
        if not all(card.layout() is not None for card in cards):
            return
        compact = self.height() < 520
        self.col1.setSpacing(4 if compact else 8)
        self.col2.setSpacing(4 if compact else 8)
        self.priority_empty.setMaximumHeight(30 if compact else 40)
        self.curiosity_label.setMaximumHeight(36 if compact else 64)
        self.continue_label.setMaximumHeight(30 if compact else 42)
        self.heat_hint.setVisible(not compact)

        for card in cards:
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            card.setMinimumWidth(0)
            layout = card.layout()
            if layout:
                edge = 6 if compact else 10
                vertical = 4 if compact else 8
                layout.setContentsMargins(edge, vertical, edge, vertical)
                layout.setSpacing(3 if compact else 7)
        self.priority_list.setFixedHeight(
            min(2 if compact else 3, self.priority_list.count()) * (26 if compact else 30)
            if self.priority_list.isVisible() else 0
        )
        self.goal_list_layout.setSpacing(3 if compact else 5)
        self.garden_layout.setSpacing(3 if compact else 6)

        if self.activity_list.isVisible():
            visible_rows = min(2 if compact else 4, self.activity_list.count())
            self.activity_list.setFixedHeight(visible_rows * 26 + 2)
        if compact:
            limits = (
                (self.focus_card, 78, 130), (self.activity_card, 68, 128),
                (self.curiosity_card, 76, 118), (self.goal_card, 60, 112),
                (self.heatmap_card, 118, 152), (self.continue_card, 62, 94),
                (self.garden_card, 78, 98),
            )
        else:
            limits = (
                (self.focus_card, 100, 180), (self.activity_card, 88, 196),
                (self.curiosity_card, 92, 148), (self.goal_card, 72, 142),
                (self.heatmap_card, 148, 184), (self.continue_card, 72, 118),
                (self.garden_card, 84, 128),
            )
        for card, minimum, maximum in limits:
            layout = card.layout()
            measured = layout.sizeHint().height() if layout else minimum
            card.setFixedHeight(max(minimum, min(maximum, measured)))

    def _render_priorities(self, priorities: list[dict[str, object]]) -> None:
        self.priority_list.clear()
        if not priorities:
            self.priority_list.setVisible(False)
            self.priority_empty.setVisible(True)
            return
        self.priority_empty.setVisible(False)
        self.priority_list.setVisible(True)
        visible_priorities = 2 if self.height() < 520 else 3
        for priority in priorities[:visible_priorities]:
            title = str(priority.get("title") or "Focus item")
            detail = str(priority.get("reason") or "")
            if priority.get("goal_title"):
                detail += f" · {priority['goal_title']}"
            item = QListWidgetItem(title)
            item.setToolTip(detail or title)
            if priority.get("goal_id"):
                item.setData(Qt.ItemDataRole.UserRole, str(priority["goal_id"]))
            elif priority.get("kind") == "session":
                item.setData(Qt.ItemDataRole.UserRole, "calendar")
            item.setSizeHint(QSize(0, 26))
            self.priority_list.addItem(item)
        self.priority_list.setFixedHeight(self.priority_list.count() * (26 if self.height() < 520 else 30))

    def _priority_clicked(self, item: QListWidgetItem) -> None:
        target = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if target == "calendar":
            self.calendarRequested.emit()
        elif target:
            self.goalRequested.emit(target)

    def _render_activity_items(self, entries: list[dict[str, object]]) -> None:
        self.activity_list.blockSignals(True)
        self.activity_list.clear()
        done = sum(1 for row in entries if row["status"] == "completed")
        self.activity_count.setText(f"{done}/{len(entries)}" if entries else "")
        if not entries:
            self.activity_list.setVisible(False)
            self.activity_empty.setVisible(False)
            self.activity_list.setFixedHeight(0)
            self.activity_list.blockSignals(False)
            return
        self.activity_empty.setVisible(False)
        self.activity_list.setVisible(True)
        for row in entries:
            title = str(row["title"])
            details = " · ".join(str(value) for value in (row.get("goal_name"), row.get("topic_name"), row.get("project_name")) if value)
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, str(row["id"]))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if row["status"] == "completed" else Qt.CheckState.Unchecked)
            estimate = f"About {row['estimated_minutes']} min" if row.get("estimated_minutes") else ""
            item.setToolTip(" · ".join(value for value in (details, estimate, "Checking this records completed work") if value))
            item.setSizeHint(QSize(0, 28))
            self.activity_list.addItem(item)
        visible_rows = min(2 if self.height() < 520 else 4, len(entries))
        self.activity_list.setFixedHeight(visible_rows * 26 + 2)
        self.activity_list.blockSignals(False)

    def _render_goals(self, goals: list[dict[str, object]]) -> None:
        while self.goal_list_layout.count():
            item = self.goal_list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        if not goals:
            empty = QLabel("Add a goal to track your progress.")
            empty.setObjectName("metaText")
            empty.setWordWrap(True)
            self.goal_list_layout.addWidget(empty)
            return
        for goal in goals[:2]:
            button = QPushButton()
            button.setObjectName("goalPreview")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            title = str(goal["title"])
            milestone = str(goal["next_milestone"] or "No pending milestone")
            progress = f"{goal['milestone_completed']}/{goal['milestone_total']} milestones"
            button.setText(f"{title}  ·  {progress}")
            button.setFixedHeight(30)
            button.setToolTip(f"Next milestone: {milestone}. Open goal details.")
            goal_id = str(goal["id"])
            button.clicked.connect(lambda _checked=False, value=goal_id: self.goalRequested.emit(value))
            self.goal_list_layout.addWidget(button)
        if len(goals) > 2:
            more = QLabel(f"+ {len(goals) - 2} more · choose All to see the rest")
            more.setObjectName("footnote")
            self.goal_list_layout.addWidget(more)

    def _render_continue(self, item: dict[str, object] | None) -> None:
        self._continue_item = item
        self.continue_button.setEnabled(item is not None)
        if not item:
            self.continue_label.setText("Your latest activity or note appears here.")
            return
        if item.get("kind") == "note":
            self.continue_label.setText(f"Note · {item['title']} · {item['topic_name']}")
        else:
            location = str(item.get("project_title") or item.get("goal_title") or item.get("topic_names") or "Recent activity")
            self.continue_label.setText(f"{item['title']} · {location}")

    def _activity_item_changed(self, item: QListWidgetItem) -> None:
        item_id = item.data(Qt.ItemDataRole.UserRole)
        if not item_id or item.checkState() != Qt.CheckState.Checked:
            return
        try:
            self.today.complete_activity_item(str(item_id))
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Activity not completed", str(exc))
        self.refresh()
        self.status.setText("Completed work was added to your activity history.")

    def _add_activity(self) -> None:
        dialog = DayActivityDialog(self.database, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.today.add_activity_item(**dialog.values())
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Activity not added", str(exc))
            return
        self.refresh()

    def _log_unplanned(self) -> None:
        from .product_ui import ActivityDialog

        dialog = ActivityDialog(self.database, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            from .services import ActivityService

            ActivityService(self.database).log_activity(**dialog.values())
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Activity not recorded", str(exc))
            return
        self.refresh()
        self.status.setText("Unplanned work was added to your activity history.")

    def _show_day_activities(self, day_text: str) -> None:
        day = date.fromisoformat(day_text)
        activities = self.today.activities_on(day)
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Activities · {day.strftime('%d %B %Y')}")
        dialog.setStyleSheet(PRODUCT_STYLE)
        dialog.resize(460, 340)
        layout = QVBoxLayout(dialog)
        heading = QLabel(day.strftime("%A, %B %d"))
        heading.setObjectName("pageTitle")
        layout.addWidget(heading)
        detail = QPlainTextEdit()
        detail.setReadOnly(True)
        if activities:
            lines = []
            for row in activities:
                duration = f" · {row['duration_minutes']} min" if row.get("duration_minutes") else ""
                links = " · ".join(str(value) for value in (row.get("goal_names"), row.get("topic_names")) if value)
                lines.append(f"{row['local_time']} · {row['title']}{duration}" + (f"\n  {links}" if links else ""))
            detail.setPlainText("\n\n".join(lines))
        else:
            detail.setPlainText("No actual activities were recorded on this day. Planned sessions are shown in Calendar.")
        layout.addWidget(detail, 1)
        close = QPushButton("Close")
        close.setObjectName("secondaryAction")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        dialog.exec()

    def _shift_month(self, amount: int) -> None:
        month_index = self._selected_month.year * 12 + self._selected_month.month - 1 + amount
        year, month_zero = divmod(month_index, 12)
        self._selected_month = date(year, month_zero + 1, 1)
        self.refresh()

    def _continue(self) -> None:
        item = self._continue_item
        if not item:
            return
        if item.get("project_id"):
            self.projectRequested.emit(str(item["project_id"]))
        elif item.get("goal_id"):
            self.goalRequested.emit(str(item["goal_id"]))
        elif item.get("kind") == "note" or item.get("topic_names"):
            self.knowledgeRequested.emit()
        else:
            self.goalRequested.emit("")

    def _render_curiosity(self) -> None:
        self._curiosity_item = PersonalFeatures(self.database).curiosity_prompt(self._ignored_note_id, self._ignored_topic_id)
        enabled = self._curiosity_item is not None
        for button in (self.curiosity_ask_button, self.curiosity_save_button, self.curiosity_ignore_button):
            button.setEnabled(enabled)
        if not self._curiosity_item:
            self.curiosity_label.setText("No new question right now. Add a note or topic to make a discovery prompt.")
            self.curiosity_label.setToolTip(self.curiosity_label.text())
            return
        source = f"\nFrom: {self._curiosity_item['note_title']}" if self._curiosity_item.get("note_id") else ""
        self.curiosity_label.setText(f"{self._curiosity_item['prompt']}{source}")
        self.curiosity_label.setToolTip(self.curiosity_label.text())

    def _ask_curiosity(self) -> None:
        item = self._curiosity_item
        if not item:
            return
        prompt = f"Use my local notes and materials to answer: Explore this question about {item['topic_name']}: {item['prompt']}"
        self.askRequested.emit(prompt)

    def _save_curiosity(self) -> None:
        item = self._curiosity_item
        if not item:
            return
        try:
            PersonalFeatures(self.database).save_discovery(
                f"Curiosity · {str(item['topic_name'])[:185]}", str(item["prompt"]),
                topic_id=str(item["topic_id"]), source_note_id=str(item["note_id"]) if item.get("note_id") else None,
            )
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Discovery not saved", str(exc))
            return
        self.status.setText("Saved the discovery prompt as an editable Knowledge note.")
        self._ignored_topic_id = str(item["topic_id"])
        self._render_curiosity()

    def _ignore_curiosity(self) -> None:
        if self._curiosity_item:
            self._ignored_note_id = str(self._curiosity_item["note_id"]) if self._curiosity_item.get("note_id") else None
            self._ignored_topic_id = str(self._curiosity_item["topic_id"])
        self._render_curiosity()

    def _render_garden(self) -> None:
        while self.garden_layout.count():
            item = self.garden_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        goals = self.today.garden_preview()
        if not goals:
            label = QLabel("Create a goal to plant the first seed.")
            label.setObjectName("metaText")
            label.setWordWrap(True)
            self.garden_layout.addWidget(label)
            return
        visible_goals = goals[:1 if self.width() < 620 else 2]
        for goal in visible_goals:
            activity_count = int(goal.get("activity_count") or 0)
            completed = int(goal.get("milestone_completed") or 0)
            stage = 0 if activity_count == 0 and completed == 0 else min(4, 1 + completed + activity_count // 3)
            if goal["status"] == "completed":
                stage = 4
            plant = QWidget()
            layout = QVBoxLayout(plant)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(1)
            graphic = GardenPlant(stage, str(goal["status"]))
            layout.addWidget(graphic, 0, Qt.AlignmentFlag.AlignHCenter)
            title = QLabel(str(goal["title"]))
            title.setObjectName("gardenGoal")
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            title.setWordWrap(True)
            title.setMaximumWidth(84)
            title.setToolTip(str(goal["title"]))
            layout.addWidget(title)
            stage_label = QLabel(("Seed", "Sprout", "Leaves", "Branches", "Mature")[stage])
            stage_label.setObjectName("footnote")
            stage_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(stage_label)
            self.garden_layout.addWidget(plant)
        if len(goals) > len(visible_goals):
            more = QLabel(f"+{len(goals) - len(visible_goals)}")
            more.setObjectName("footnote")
            more.setToolTip("Open Goals to see more garden plants.")
            self.garden_layout.addWidget(more, 0, Qt.AlignmentFlag.AlignVCenter)

    def _quick_capture(self) -> None:
        choice, accepted = QInputDialog.getItem(
            self, "Quick capture", "What would you like to capture?",
            ("Plan an activity for today", "Record work I completed", "Save a Knowledge note",
             "Create a goal", "Propose a memory"), 0, False,
        )
        if not accepted:
            return
        if choice == "Plan an activity for today":
            self._add_activity()
        elif choice == "Record work I completed":
            self._log_unplanned()
        elif choice == "Save a Knowledge note":
            self._capture_note()
        elif choice == "Create a goal":
            self._capture_goal()
        else:
            self._capture_memory()

    def _capture_note(self) -> None:
        title, accepted = QInputDialog.getText(self, "Quick note", "Note title:")
        if not accepted or not title.strip():
            return
        topic, accepted = QInputDialog.getText(self, "Quick note", "Topic:", text="General")
        if not accepted or not topic.strip():
            return
        content, accepted = QInputDialog.getMultiLineText(self, "Quick note", "What should the note contain?")
        if not accepted:
            return
        try:
            from .knowledge import KnowledgeService

            note = KnowledgeService(self.database).save_note(title, topic, content)
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Note not saved", str(exc))
            return
        self.status.setText(f"Saved Knowledge/{note['relative_path']} and added it to local search.")
        self.refresh()

    def _capture_goal(self) -> None:
        from .product_ui import GoalEditorDialog

        dialog = GoalEditorDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            values = dialog.values()
            goal = self.goals.create_goal(
                values["title"], values["description"], target_date=values["target_date"], priority=values["priority"]
            )
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Goal not created", str(exc))
            return
        self.status.setText(f"Created goal: {goal['title']}.")
        self.refresh()

    def _capture_memory(self) -> None:
        content, accepted = QInputDialog.getMultiLineText(
            self, "Propose a memory", "This remains pending until you approve it. What should Amigo remember?"
        )
        if not accepted or not content.strip():
            return
        try:
            memory = self.profile.propose_memory(content)
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Memory not proposed", str(exc))
            return
        self.status.setText(f"Memory proposal saved as pending ({memory['id'][:8]}). Review it in Profile → Memories.")

    def _weekly_review(self) -> None:
        from .features import PersonalFeatures

        result = PersonalFeatures(self.database).weekly_review()
        text = result.get("summary") or "No activity was recorded this week."
        QMessageBox.information(self, "Weekly review", str(text))

    def _focus_session(self) -> None:
        from .features_ui import FocusSessionDialog

        dialog = FocusSessionDialog(self.database, self)
        dialog.exec()
        self.refresh()

    def _review_replan(self) -> None:
        from .features_ui import GoalReplanDialog

        dialog = GoalReplanDialog(self.database, self)
        dialog.exec()
        self.refresh()

