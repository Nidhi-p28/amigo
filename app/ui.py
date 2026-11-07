from __future__ import annotations

import math
import re
import threading
from datetime import date, datetime

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QEvent
from PySide6.QtGui import QAction, QKeyEvent, QTextCursor, QColor, QPainter, QPainterPath, QPen, QCursor
from PySide6.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QStyle,
    QSystemTrayIcon, QVBoxLayout, QWidget, QListWidget, QListWidgetItem,
    QStackedWidget,
)

from .agent import HISTORY_LIMIT, AgentCancelled, DesktopAgent, AgentError
from .cloud import looks_like_web_request
from .config import APP_DATA_DIR, OLLAMA_MODEL, PROJECT_ROOT, STAGE, WAKE_WORD_ENABLED, WORKSPACE_DIR
from .services import ActivityService
from .storage import ConversationStore, SqliteDatabase
from .theme import APP_STYLE
from .voice import SpeechOutputWorker, VoiceInputWorker, WakeWordWorker, list_input_devices


class ListeningIndicator(QWidget):
    """A subtle animated heartbeat line shown while a microphone listener is active."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(54, 22)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(55)
        self._timer.timeout.connect(self._advance)
        self.hide()

    def set_active(self, active: bool) -> None:
        if active:
            self.show()
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            self.hide()
        self.update()

    def _advance(self) -> None:
        self._phase = (self._phase + 0.035) % 1.0
        self.update()

    def paintEvent(self, event) -> None:
        if not self.isVisible():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(62, 104, 143, 58), 1.0))
        center = self.height() / 2.0
        painter.drawLine(2, int(center), self.width() - 2, int(center))
        path = QPainterPath()
        for pixel in range(self.width() - 4):
            x = pixel + 2
            t = ((pixel / max(1, self.width() - 4)) + self._phase) % 1.0
            y = center + 1.2 * math.sin(t * 2.0 * 3.1415926535)
            for peak, height, width in ((0.28, -5.5, 0.018), (0.32, 7.0, 0.012), (0.37, -3.0, 0.02)):
                distance = abs(t - peak)
                distance = min(distance, 1.0 - distance)
                y += height * math.exp(-((distance / width) ** 2))
            point_y = min(self.height() - 2.0, max(2.0, y))
            if pixel == 0:
                path.moveTo(x, point_y)
            else:
                path.lineTo(x, point_y)
        painter.setPen(QPen(QColor(51, 100, 145, 218), 1.65, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(path)


class GrainOverlay(QWidget):
    """Very faint static grain texture over the translucent glass panel."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        import random
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._points = []
        randomizer = random.Random(2407)
        for _ in range(700):
            light = randomizer.random() > 0.42
            # Very fine speckle with enough contrast to remain visible over a
            # pale blue glass gradient without making text difficult to read.
            alpha = randomizer.choice((3, 4, 5, 6, 7))
            self._points.append((randomizer.random(), randomizer.random(), alpha, light))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        width = max(1, self.width())
        height = max(1, self.height())
        for x, y, alpha, light in self._points:
            color = QColor(255, 255, 255, alpha) if light else QColor(54, 83, 108, alpha)
            painter.setPen(QPen(color, 1))
            painter.drawPoint(round(x * width), round(y * height))
        painter.end()


class AgentWorker(QThread):
    completed = Signal(str)
    failed = Signal(str)
    cancelled = Signal()
    chunk = Signal(str)
    delete_confirmation_requested = Signal(str, object)

    def __init__(
        self, agent: DesktopAgent, prompt: str, history: list[dict[str, str]], *, voice_mode: bool = False
    ) -> None:
        super().__init__()
        self.agent = agent
        self.prompt = prompt
        self.history = history
        self.voice_mode = voice_mode
        self.cancel_event = threading.Event()
        self._pending_confirmation: dict[str, object] | None = None

    def cancel(self) -> None:
        self.cancel_event.set()
        self.agent.cancel_current()
        pending = self._pending_confirmation
        if pending is not None:
            event = pending.get("event")
            if isinstance(event, threading.Event):
                event.set()

    def _confirm_delete(self, relative_path: str) -> bool:
        if self.cancel_event.is_set():
            return False
        context: dict[str, object] = {"event": threading.Event(), "approved": False}
        self._pending_confirmation = context
        self.delete_confirmation_requested.emit(relative_path, context)
        event = context["event"]
        assert isinstance(event, threading.Event)
        try:
            while not event.wait(0.1):
                if self.cancel_event.is_set():
                    return False
            return bool(context.get("approved", False)) and not self.cancel_event.is_set()
        finally:
            self._pending_confirmation = None

    def run(self) -> None:
        try:
            answer = self.agent.respond(
                self.prompt, self.history, on_chunk=self.chunk.emit,
                cancel_event=self.cancel_event, confirm_delete=self._confirm_delete,
                voice_mode=self.voice_mode,
            )
            self.completed.emit(answer)
        except AgentCancelled:
            self.cancelled.emit()
        except (AgentError, RuntimeError) as exc:
            if self.cancel_event.is_set():
                self.cancelled.emit()
                return
            self.failed.emit(str(exc))
        except Exception as exc:
            if self.cancel_event.is_set():
                self.cancelled.emit()
                return
            import traceback
            traceback.print_exc()
            self.failed.emit(f"Something went wrong: {exc}")


class HealthWorker(QThread):
    checked = Signal(str)

    def __init__(self, agent: DesktopAgent) -> None:
        super().__init__()
        self.agent = agent

    def run(self) -> None:
        self.checked.emit(self.agent.health_check())


class MessageRow(QWidget):
    def __init__(self, role: str, content: str) -> None:
        super().__init__()
        self.role = role
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 3, 0, 3)
        outer.setSpacing(8)

        bubble = QFrame()
        bubble.setObjectName("userBubble" if role == "user" else "assistantBubble")
        bubble.setMaximumWidth(350 if role == "user" else 390)
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(12, 9, 12, 9)
        bubble_layout.setSpacing(4)

        self.role_label = QLabel("YOU" if role == "user" else "AMIGO")
        self.role_label.setObjectName("messageName")
        self.text_label = QLabel(content)
        self.text_label.setObjectName("messageText")
        self.text_label.setTextFormat(Qt.TextFormat.PlainText)
        self.text_label.setWordWrap(True)
        self.text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.text_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        bubble_layout.addWidget(self.role_label)
        bubble_layout.addWidget(self.text_label)

        if role == "user":
            outer.addStretch(1)
            outer.addWidget(bubble, 0, Qt.AlignmentFlag.AlignRight)
        else:
            outer.addWidget(bubble, 0, Qt.AlignmentFlag.AlignLeft)
            outer.addStretch(1)

    def set_content(self, content: str) -> None:
        self.text_label.setText(content)


class HistoryDialog(QDialog):
    def __init__(self, store: ConversationStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Chat history")
        self.resize(390, 430)
        self.store = store
        self.selected_conversation_id: str | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        heading = QLabel("Earlier conversations")
        heading.setStyleSheet("font-size: 16pt; font-weight: 600; color: #1D3032;")
        description = QLabel("Choose a conversation to continue it.")
        description.setStyleSheet("color: #687A75;")
        layout.addWidget(heading)
        layout.addWidget(description)
        self.items = QListWidget()
        for conversation in store.list_conversations(50):
            item = QListWidgetItem()
            title = str(conversation.get("title") or "Untitled chat")
            created = str(conversation.get("updated_at") or "")
            try:
                dt = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone()
                date_label = dt.strftime("%b %d · %I:%M %p")
            except ValueError:
                date_label = "Saved conversation"
            item.setText(f"{title}\n{date_label}  ·  {conversation.get('message_count', 0)} messages")
            item.setData(Qt.ItemDataRole.UserRole, conversation["id"])
            self.items.addItem(item)
        layout.addWidget(self.items, 1)
        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton("Cancel")
        open_button = QPushButton("Open chat")
        open_button.setObjectName("sendButton")
        cancel.clicked.connect(self.reject)
        open_button.clicked.connect(self._accept_selection)
        self.items.itemDoubleClicked.connect(lambda _item: self._accept_selection())
        actions.addWidget(cancel)
        actions.addWidget(open_button)
        layout.addLayout(actions)

    def _accept_selection(self) -> None:
        item = self.items.currentItem()
        if item is None:
            return
        self.selected_conversation_id = str(item.data(Qt.ItemDataRole.UserRole))
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self, database: SqliteDatabase | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Amigo")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )
        screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            minimum_width = max(640, min(1000, available.width() - 16))
            minimum_height = max(420, min(680, available.height() - 16))
            self.setMinimumSize(minimum_width, minimum_height)
            initial_width = min(1680, max(minimum_width, int(available.width() * 0.94)))
            initial_height = min(900, max(minimum_height, int(available.height() * 0.94)))
            self.resize(initial_width, initial_height)
            self.move(
                available.x() + max(0, (available.width() - initial_width) // 2),
                available.y() + max(0, (available.height() - initial_height) // 2),
            )
        else:
            self.setMinimumSize(1000, 680)
            self.resize(1260, 720)
        self.setStyleSheet(APP_STYLE)

        self.store = ConversationStore(database.path) if database is not None else ConversationStore()
        # Starts a fresh conversation for each app launch.
        from .knowledge import KnowledgeService
        from .features import ProfileService

        self.agent = DesktopAgent(
            activity_service=ActivityService(self.store.database),
            knowledge_service=KnowledgeService(self.store.database),
            profile_service=ProfileService(self.store.database),
        )
        self.worker: AgentWorker | None = None
        self.health_worker: HealthWorker | None = None
        self.voice_worker: VoiceInputWorker | None = None
        self.speech_worker: SpeechOutputWorker | None = None
        self._ephemeral_row: MessageRow | None = None
        self._streamed_answer = ""
        self._quitting = False
        self._empty_state: QWidget | None = None
        self._wake_worker: WakeWordWorker | None = None
        self._wake_paused = False
        self._wake_word_failed = False
        self._wake_detected_pending = False
        self._wake_detected_command = ""
        self._manual_mic_pending = False
        self._auto_send_after_voice = False
        self._voice_transcript_ready = False
        self._voice_response_pending = False
        self._active_response_voice = False
        self._pending_send_after_wake_stop = False
        self._voice_send_pending = False
        self._voice_send_timer = QTimer(self)
        self._voice_send_timer.setSingleShot(True)
        self._voice_send_timer.setInterval(1500)
        self._voice_send_timer.timeout.connect(self._send_pending_voice)
        self._input_device_index = self._load_saved_input_device()
        self._drag_position = None

        root = QWidget()
        root.setObjectName("popupFrame")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(22, 16, 22, 18)
        outer.setSpacing(12)

        header = QFrame()
        header.setObjectName("header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 5)
        header_layout.setSpacing(8)

        identity = QHBoxLayout()
        identity.setSpacing(10)
        brand_mark = QLabel("A")
        brand_mark.setObjectName("brandMark")
        brand_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_mark.setFixedSize(34, 34)
        title = QLabel("amigo")
        title.setObjectName("brandName")
        tagline = QLabel("A QUIET DESKTOP COMPANION")
        tagline.setObjectName("brandTag")
        identity_text = QVBoxLayout()
        identity_text.setSpacing(0)
        identity_text.addWidget(title)
        identity_text.addWidget(tagline)
        identity.addWidget(brand_mark)
        identity.addLayout(identity_text)
        header_layout.addLayout(identity)
        header_layout.addSpacing(14)
        self.new_button = QPushButton("+ New chat")
        self.new_button.setObjectName("utilityButton")
        self.new_button.clicked.connect(self.new_conversation)
        self.history_button = QPushButton("History")
        self.history_button.setObjectName("utilityButton")
        self.history_button.clicked.connect(self.open_history)
        self.today_button = QPushButton("Today")
        self.today_button.setObjectName("navButton")
        self.today_button.setCheckable(True)
        self.today_button.setToolTip("Open your daily focus, activity list, goals, and progress heatmap")
        self.today_button.clicked.connect(self.open_today)
        self.goals_button = QPushButton("Goals")
        self.goals_button.setObjectName("navButton")
        self.goals_button.setCheckable(True)
        self.goals_button.setToolTip("Manage goals, milestones, activities, topics, and materials")
        self.goals_button.clicked.connect(self.open_goals)
        self.projects_button = QPushButton("Projects")
        self.projects_button.setObjectName("navButton")
        self.projects_button.setCheckable(True)
        self.projects_button.setToolTip("Connect goals, resources, topics, and work in a project hub")
        self.projects_button.clicked.connect(self.open_projects)
        self.learning_button = QPushButton("Learning")
        self.learning_button.setObjectName("navButton")
        self.learning_button.setCheckable(True)
        self.learning_button.setToolTip("Review topic states, evidence, quizzes, and suggestions")
        self.learning_button.clicked.connect(self.open_learning)
        self.knowledge_button = QPushButton("Knowledge")
        self.knowledge_button.setObjectName("navButton")
        self.knowledge_button.setCheckable(True)
        self.knowledge_button.setToolTip("Search local notes and registered materials")
        self.knowledge_button.clicked.connect(self.open_knowledge)
        self.calendar_button = QPushButton("Calendar")
        self.calendar_button.setObjectName("navButton")
        self.calendar_button.setCheckable(True)
        self.calendar_button.setToolTip("Plan sessions and add calendar events")
        self.calendar_button.clicked.connect(self.open_calendar)
        self.search_button = QPushButton("Search")
        self.search_button.setObjectName("utilityButton")
        self.search_button.clicked.connect(self.open_search)
        self.profile_button = QPushButton("Profile")
        self.profile_button.setObjectName("utilityButton")
        self.profile_button.clicked.connect(self.open_profile)
        self.help_button = QPushButton("?")
        self.help_button.setObjectName("helpButton")
        self.help_button.setToolTip("Simple guide to Amigo's pages and tools")
        self.help_button.setAccessibleName("Help")
        self.help_button.setAccessibleDescription("Open a plain-language guide to Amigo")
        self.help_button.clicked.connect(self.open_help)
        navigation = QFrame()
        navigation.setObjectName("navGroup")
        navigation_layout = QHBoxLayout(navigation)
        navigation_layout.setContentsMargins(3, 3, 3, 3)
        navigation_layout.setSpacing(1)
        for button in (self.today_button, self.goals_button, self.projects_button,
                       self.learning_button, self.knowledge_button, self.calendar_button):
            navigation_layout.addWidget(button)
        header_layout.addWidget(navigation, 1)
        header_layout.addSpacing(8)
        header_layout.addWidget(self.new_button)
        header_layout.addWidget(self.history_button)
        header_layout.addWidget(self.search_button)
        header_layout.addWidget(self.profile_button)
        header_layout.addWidget(self.help_button)
        self.hide_button = QPushButton("×")
        self.hide_button.setObjectName("hideButton")
        self.hide_button.setToolTip("Hide Amigo · the wake word can stay active")
        self.hide_button.clicked.connect(self.close)
        header_layout.addWidget(self.hide_button)
        outer.addWidget(header)

        separator = QFrame()
        separator.setObjectName("separator")
        separator.setFixedHeight(1)
        outer.addWidget(separator)

        status_row = QHBoxLayout()
        self.status_label = QLabel("Starting local assistant…")
        self.status_label.setObjectName("status")
        self.stage_label = QLabel("LOCAL" if STAGE == 1 else ("DESKTOP" if STAGE == 2 else "LOCAL + WEB"))
        self.stage_label.setObjectName("stageTag")
        status_row.addWidget(self.status_label, 1)
        self.listening_indicator = ListeningIndicator()
        self.listening_indicator.setToolTip("Microphone activity · local wake-word detection")
        status_row.addWidget(self.listening_indicator)
        status_row.addWidget(self.stage_label)
        outer.addLayout(status_row)

        main_area = QWidget()
        main_layout = QHBoxLayout(main_area)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(12)

        from .calendar_ui import CalendarPanel
        from .learning_ui import LearningPanel
        from .projects_ui import ProjectsPanel
        from .product_ui import GoalManagerPanel, KnowledgeLibraryPanel
        from .today_ui import TodayPanel

        workspace_frame = QFrame()
        workspace_frame.setObjectName("workspaceColumn")
        workspace_frame.setMinimumWidth(470)
        workspace_layout = QVBoxLayout(workspace_frame)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        self.workspace_stack = QStackedWidget()
        self.today_panel = TodayPanel(self.store.database, workspace_frame)
        self.goals_panel = GoalManagerPanel(self.store.database, workspace_frame)
        self.projects_panel = ProjectsPanel(self.store.database, workspace_frame)
        self.learning_panel = LearningPanel(self.store.database, workspace_frame)
        self.knowledge_panel = KnowledgeLibraryPanel(self.store.database, workspace_frame)
        self.calendar_panel = CalendarPanel(self.store.database, workspace_frame)
        self.knowledge_panel.askRequested.connect(self.ask_knowledge)
        self.today_panel.goalRequested.connect(self._open_goal_from_today)
        self.today_panel.projectRequested.connect(self._open_project_from_today)
        self.today_panel.calendarRequested.connect(self.open_calendar)
        self.today_panel.knowledgeRequested.connect(self.open_knowledge)
        self.today_panel.askRequested.connect(self.ask_project)
        self.projects_panel.askRequested.connect(self.ask_project)
        self.knowledge_panel.compareRequested.connect(self.ask_knowledge)
        self.workspace_stack.addWidget(self.projects_panel)
        self.workspace_stack.addWidget(self.learning_panel)
        self.workspace_stack.addWidget(self.today_panel)
        self.workspace_stack.addWidget(self.goals_panel)
        self.workspace_stack.addWidget(self.knowledge_panel)
        self.workspace_stack.addWidget(self.calendar_panel)
        workspace_layout.addWidget(self.workspace_stack, 1)
        main_layout.addWidget(workspace_frame, 6)

        chat_frame = QFrame()
        chat_frame.setObjectName("chatColumn")
        chat_frame.setMinimumWidth(430)
        chat_layout = QVBoxLayout(chat_frame)
        chat_layout.setContentsMargins(8, 6, 8, 8)
        chat_layout.setSpacing(6)
        chat_header = QFrame()
        chat_header.setObjectName("chatHeader")
        chat_header_layout = QHBoxLayout(chat_header)
        chat_header_layout.setContentsMargins(10, 5, 10, 10)
        chat_title_column = QVBoxLayout()
        chat_title_column.setSpacing(1)
        chat_title = QLabel("Assistant chat")
        chat_title.setObjectName("chatTitle")
        chat_subtitle = QLabel("Your workspace companion")
        chat_subtitle.setObjectName("chatSubtitle")
        chat_title_column.addWidget(chat_title)
        chat_title_column.addWidget(chat_subtitle)
        chat_header_layout.addLayout(chat_title_column, 1)
        local_badge = QLabel("LOCAL BY DEFAULT")
        local_badge.setObjectName("stageTag")
        chat_header_layout.addWidget(local_badge)
        chat_layout.addWidget(chat_header)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chat_content = QWidget()
        self.chat_content.setObjectName("chatContent")
        self.chat_layout = QVBoxLayout(self.chat_content)
        self.chat_layout.setContentsMargins(10, 12, 10, 12)
        self.chat_layout.setSpacing(12)
        self.chat_layout.addStretch(1)
        self.scroll.setWidget(self.chat_content)
        chat_layout.addWidget(self.scroll, 1)

        composer = QFrame()
        composer.setObjectName("composerFrame")
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(8, 8, 8, 6)
        composer_layout.setSpacing(6)
        self.input_box = QPlainTextEdit()
        self.input_box.setObjectName("composer")
        self.input_box.setPlaceholderText("Ask Amigo something…")
        self.input_box.setFixedHeight(88)
        self.input_box.setTabChangesFocus(True)
        composer_layout.addWidget(self.input_box)

        action_row = QHBoxLayout()
        action_row.setSpacing(7)
        self.mic_button = QPushButton("Mic")
        self.mic_button.setObjectName("micButton")
        self.mic_button.setToolTip("Click to record a message. Right-click to choose your microphone.")
        self.mic_button.clicked.connect(self.toggle_listening)
        self.mic_button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.mic_button.customContextMenuRequested.connect(
            lambda pos: self._show_microphone_menu(self.mic_button.mapToGlobal(pos))
        )
        action_row.addWidget(self.mic_button)
        action_row.addStretch(1)
        self.send_button = QPushButton("Send  ↗")
        self.send_button.setObjectName("sendButton")
        self.send_button.clicked.connect(self._send_or_stop_clicked)
        action_row.addWidget(self.send_button)
        composer_layout.addLayout(action_row)
        self.composer_hint = QLabel("Cancel a misheard wake command · Stop a slow request")
        self.composer_hint.setObjectName("composerHint")
        composer_layout.addWidget(self.composer_hint)
        chat_layout.addWidget(composer)
        main_layout.addWidget(chat_frame, 5)
        outer.addWidget(main_area, 1)
        root.setLayout(outer)
        self.setCentralWidget(root)
        # Keep the main surface clean and readable.
        root.installEventFilter(self)

        self.input_box.installEventFilter(self)
        self._show_empty_state()
        self._setup_tray()
        self.open_today()
        QTimer.singleShot(0, self._check_status)

    def _show_empty_state(self) -> None:
        if self._empty_state is not None:
            return
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 28, 8, 14)
        layout.setSpacing(12)
        layout.addStretch(1)
        title = QLabel("What would you like\nto get done?")
        title.setObjectName("emptyTitle")
        title.setWordWrap(True)
        subtitle = QLabel("A little help for the things on your mind.")
        subtitle.setObjectName("emptySub")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        suggestions = QVBoxLayout()
        suggestions.setSpacing(7)
        for suggestion in ("Open Notepad", "Create a folder named ideas", "Find the latest AI news"):
            button = QPushButton(suggestion)
            button.setObjectName("suggestion")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, value=suggestion: self._put_suggestion(value))
            suggestions.addWidget(button)
        layout.addSpacing(5)
        layout.addLayout(suggestions)
        privacy = QLabel("LOCAL BY DEFAULT  ·  WEB ONLY WHEN YOU ASK")
        privacy.setObjectName("privacyNote")
        layout.addWidget(privacy)
        layout.addStretch(2)
        self.chat_layout.insertWidget(max(0, self.chat_layout.count() - 1), panel)
        self._empty_state = panel

    def _put_suggestion(self, text: str) -> None:
        self.input_box.setPlainText(text)
        self.input_box.setFocus()
        self.input_box.moveCursor(QTextCursor.MoveOperation.End)

    def _clear_empty_state(self) -> None:
        if self._empty_state is not None:
            self.chat_layout.removeWidget(self._empty_state)
            self._empty_state.deleteLater()
            self._empty_state = None

    def _load_saved_input_device(self) -> int | None:
        """Restore the chosen mic by name, with compatibility for an older saved index."""
        setting = APP_DATA_DIR / "audio_input_device.txt"
        if not setting.exists():
            legacy_setting = PROJECT_ROOT / "data" / "audio_input_device.txt"
            if legacy_setting.exists():
                setting = legacy_setting
        try:
            saved = setting.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        devices = list_input_devices()
        if saved.isdigit():
            old_index = int(saved)
            if any(device_index == old_index for device_index, _name, _native in devices):
                return old_index
        for device_index, name, _native in devices:
            if name == saved:
                return device_index
        return None

    def _show_microphone_menu(self, global_pos=None) -> None:
        menu = QMenu(self)
        default_action = QAction("System default microphone", menu)
        default_action.setCheckable(True)
        default_action.setChecked(self._input_device_index is None)
        default_action.triggered.connect(lambda: self._set_input_device(None))
        menu.addAction(default_action)
        devices = list_input_devices()
        if not devices:
            unavailable = QAction("No input devices detected", menu)
            unavailable.setEnabled(False)
            menu.addAction(unavailable)
        else:
            menu.addSeparator()
            for device_index, name, native_16k in devices:
                suffix = " · native 16 kHz" if native_16k else " · automatic resampling"
                action = QAction(f"{device_index}: {name}{suffix}", menu)
                action.setCheckable(True)
                action.setChecked(self._input_device_index == device_index)
                action.triggered.connect(
                    lambda _checked=False, index=device_index: self._set_input_device(index)
                )
                menu.addAction(action)
        if global_pos is None:
            global_pos = QCursor.pos()
        menu.exec(global_pos)

    def _set_input_device(self, device_index: int | None) -> None:
        if self.voice_worker is not None and self.voice_worker.isRunning():
            self.status_label.setText("Finish the current recording before changing microphones.")
            return
        if device_index is not None and not any(
            candidate == device_index for candidate, _name, _native in list_input_devices()
        ):
            self.status_label.setText("That microphone is no longer available.")
            return
        self._input_device_index = device_index
        setting = APP_DATA_DIR / "audio_input_device.txt"
        try:
            setting.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        selected = "system default microphone"
        if device_index is not None:
            selected = next((name for index, name, _native in list_input_devices() if index == device_index), f"microphone {device_index}")
        try:
            if device_index is None:
                setting.unlink(missing_ok=True)
            else:
                setting.write_text(selected, encoding="utf-8")
        except OSError:
            pass
        self.status_label.setText(f"Microphone set to {selected}. Testing on next use.")
        if self._wake_worker is not None and self._wake_worker.isRunning():
            self._stop_wake_listener()

    def _setup_tray(self) -> None:
        self.tray = QSystemTrayIcon(self)
        self.tray.setToolTip("Amigo")
        self.tray.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))
        menu = QMenu()
        show_action = QAction("Show Amigo", self)
        show_action.triggered.connect(self.show_and_focus)
        quit_action = QAction("Quit Amigo", self)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(show_action)
        choose_mic_action = QAction("Choose microphone…", self)
        choose_mic_action.triggered.connect(lambda: self._show_microphone_menu())
        menu.addAction(choose_mic_action)
        menu.addSeparator()
        self.wake_toggle_action = QAction("Pause wake word listening", self)
        self.wake_toggle_action.setEnabled(WAKE_WORD_ENABLED)
        self.wake_toggle_action.triggered.connect(self.toggle_wake_word)
        menu.addAction(self.wake_toggle_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick}:
            self.show_and_focus()

    def _check_status(self) -> None:
        self.health_worker = HealthWorker(self.agent)
        self.health_worker.checked.connect(self._set_health_status)
        self.health_worker.start()

    def _set_health_status(self, status: str) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        if self._wake_worker is not None and self._wake_worker.isRunning():
            self.status_label.setText("Waiting for ‘Amigo’")
        else:
            self.status_label.setText(status.replace("Ollama connected · ", "Ready · "))
        self.status_label.setToolTip(f"Workspace: {WORKSPACE_DIR}")

    def _add_message(self, role: str, content: str) -> MessageRow:
        self._clear_empty_state()
        row = MessageRow(role, content)
        self.chat_layout.insertWidget(max(0, self.chat_layout.count() - 1), row)
        QTimer.singleShot(0, self._scroll_to_bottom)
        return row

    def _scroll_to_bottom(self) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def new_conversation(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.status_label.setText("Finish the current reply before starting another chat.")
            return
        if self.voice_worker is not None and self.voice_worker.isRunning():
            self.voice_worker.stop_recording()
        self.store.start_conversation()
        self._clear_chat_view()
        self._show_empty_state()
        self.status_label.setText("New chat · ready")
        self.input_box.clear()
        self.input_box.setFocus()

    def _clear_chat_view(self) -> None:
        self._clear_empty_state()
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def open_history(self) -> None:
        dialog = HistoryDialog(self.store, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.selected_conversation_id:
            return
        conversation_id = dialog.selected_conversation_id
        if self.store.load_conversation(conversation_id):
            self._clear_chat_view()
            history = self.store.recent_messages(100, conversation_id=conversation_id)
            if not history:
                self._show_empty_state()
            else:
                for entry in history:
                    self._add_message(entry["role"], entry["content"])
            self.status_label.setText("Earlier chat · continuing this conversation")

    def open_goals(self) -> None:
        self.workspace_stack.setCurrentWidget(self.goals_panel)
        self.today_button.setChecked(False)
        self.goals_button.setChecked(True)
        self.projects_button.setChecked(False)
        self.learning_button.setChecked(False)
        self.knowledge_button.setChecked(False)
        self.calendar_button.setChecked(False)

    def open_knowledge(self) -> None:
        self.workspace_stack.setCurrentWidget(self.knowledge_panel)
        self.today_button.setChecked(False)
        self.goals_button.setChecked(False)
        self.projects_button.setChecked(False)
        self.learning_button.setChecked(False)
        self.knowledge_button.setChecked(True)
        self.calendar_button.setChecked(False)

    def open_calendar(self) -> None:
        self.workspace_stack.setCurrentWidget(self.calendar_panel)
        self.today_button.setChecked(False)
        self.goals_button.setChecked(False)
        self.projects_button.setChecked(False)
        self.learning_button.setChecked(False)
        self.knowledge_button.setChecked(False)
        self.calendar_button.setChecked(True)

    def open_today(self) -> None:
        self.workspace_stack.setCurrentWidget(self.today_panel)
        self.today_button.setChecked(True)
        self.goals_button.setChecked(False)
        self.projects_button.setChecked(False)
        self.learning_button.setChecked(False)
        self.knowledge_button.setChecked(False)
        self.calendar_button.setChecked(False)
        self.today_panel.refresh()

    def open_projects(self) -> None:
        self.workspace_stack.setCurrentWidget(self.projects_panel)
        self.projects_panel.refresh()
        self.today_button.setChecked(False)
        self.goals_button.setChecked(False)
        self.projects_button.setChecked(True)
        self.learning_button.setChecked(False)
        self.knowledge_button.setChecked(False)
        self.calendar_button.setChecked(False)

    def open_learning(self) -> None:
        self.workspace_stack.setCurrentWidget(self.learning_panel)
        self.learning_panel.refresh()
        self.today_button.setChecked(False)
        self.goals_button.setChecked(False)
        self.projects_button.setChecked(False)
        self.learning_button.setChecked(True)
        self.knowledge_button.setChecked(False)
        self.calendar_button.setChecked(False)

    def open_profile(self) -> None:
        from .features_ui import ProfileDialog

        dialog = ProfileDialog(self.store.database, self)
        dialog.exec()
        self.today_panel.refresh()

    def open_help(self) -> None:
        from .help_ui import HelpDialog

        page_names = {
            self.today_panel: "Today",
            self.goals_panel: "Goals",
            self.projects_panel: "Projects",
            self.learning_panel: "Learning",
            self.knowledge_panel: "Knowledge",
            self.calendar_panel: "Calendar",
        }
        current_page = self.workspace_stack.currentWidget()
        initial_topic = page_names.get(current_page, "Assistant & voice")
        HelpDialog(self, initial_topic).exec()

    def open_search(self) -> None:
        from .features_ui import GlobalSearchDialog

        dialog = GlobalSearchDialog(self.store.database, self)
        dialog.opened.connect(self._open_search_result)
        dialog.exec()

    def _open_search_result(self, kind: str, record_id: str) -> None:
        if kind == "goal":
            self.open_goals()
            self.goals_panel.select_goal(record_id)
        elif kind == "project":
            self.open_projects()
            self.projects_panel.select_project(record_id)
        elif kind == "milestone":
            with self.store.database.connection() as connection:
                row = connection.execute("SELECT goal_id FROM milestones WHERE id=?", (record_id,)).fetchone()
            if row:
                self.open_goals()
                self.goals_panel.select_goal(str(row["goal_id"]))
        elif kind in {"planned_session", "calendar_event"}:
            table = "planned_sessions" if kind == "planned_session" else "calendar_events"
            with self.store.database.connection() as connection:
                row = connection.execute(f"SELECT starts_at FROM {table} WHERE id=?", (record_id,)).fetchone()
            if row:
                starts_at = datetime.fromisoformat(str(row["starts_at"])).astimezone()
                self.open_calendar()
                self.calendar_panel.open_entry(starts_at.date().isoformat(), record_id)
        elif kind == "activity":
            activities = ActivityService(self.store.database).list_activities(limit=500)
            activity = next((item for item in activities if str(item["id"]) == record_id), None)
            if activity:
                self.open_today()
                local_day = datetime.fromisoformat(str(activity["occurred_at"])).astimezone().date()
                self.today_panel.show_activity_day(local_day.isoformat())
        else:
            self.open_knowledge()
            self.knowledge_panel.open_search_result(kind, record_id)

    def ask_project(self, prompt: str) -> None:
        self.input_box.setPlainText(prompt)
        self.send_message()

    def _open_goal_from_today(self, goal_id: str) -> None:
        self.open_goals()
        if goal_id:
            self.goals_panel.select_goal(goal_id)

    def _open_project_from_today(self, project_id: str) -> None:
        self.open_projects()
        if project_id:
            self.projects_panel.select_project(project_id)

    def ask_knowledge(self, prompt: str) -> None:
        self.open_knowledge()
        self.input_box.setPlainText(prompt)
        self.send_message()

    def send_message(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        # Enter in the composer may send during the short voice-review window.
        if self._voice_send_pending:
            self._voice_send_timer.stop()
            self._voice_send_pending = False
            self.send_button.setText("Send  ↗")
        if self.voice_worker is not None and self.voice_worker.isRunning():
            self.status_label.setText("Finish voice recording before sending.")
            return
        prompt = self.input_box.toPlainText().strip()
        if not prompt:
            return
        if self._wake_worker is not None and self._wake_worker.isRunning():
            self._pending_send_after_wake_stop = True
            self.status_label.setText("Pausing wake detection to send your message…")
            self._stop_wake_listener()
            return

        history = self.store.recent_messages(HISTORY_LIMIT)
        self.store.add_message("user", prompt)
        self._add_message("user", prompt)
        self.input_box.clear()
        # Keep the send button live as a Stop control while a model/cloud call runs.
        self.send_button.setText("Stop  ■")
        self.send_button.setEnabled(True)
        self.input_box.setEnabled(False)
        self.mic_button.setEnabled(False)
        self.new_button.setEnabled(False)
        self.history_button.setEnabled(False)
        if STAGE >= 3 and looks_like_web_request(prompt):
            status_text = "Researching the web… this can take longer than local chat."
            thinking_text = "Looking for current information…"
        else:
            status_text = "Thinking locally…"
            thinking_text = "Thinking…"
        self.status_label.setText(status_text)
        self._streamed_answer = ""
        self._ephemeral_row = self._add_message("assistant", thinking_text)

        voice_mode = self._voice_response_pending
        self._voice_response_pending = False
        self._active_response_voice = voice_mode
        self.worker = AgentWorker(self.agent, prompt, history, voice_mode=voice_mode)
        self.worker.chunk.connect(self._on_chunk)
        self.worker.completed.connect(self._on_completed)
        self.worker.failed.connect(self._on_failed)
        self.worker.cancelled.connect(self._on_cancelled)
        self.worker.delete_confirmation_requested.connect(self._confirm_file_delete)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()

    def _send_or_stop_clicked(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self._cancel_active_request()
            return
        if self.voice_worker is not None and self.voice_worker.isRunning():
            self._cancel_voice_capture()
            return
        if self._voice_send_pending:
            self._cancel_pending_voice_send()
            return
        if self.speech_worker is not None and self.speech_worker.isRunning():
            self.speech_worker.stop()
            self.status_label.setText("Stopping speech…")
            return
        self.send_message()

    def _cancel_active_request(self) -> None:
        if self.worker is None or not self.worker.isRunning():
            return
        self.send_button.setText("Stopping…")
        self.send_button.setEnabled(False)
        self.status_label.setText("Stopping the current request…")
        self.worker.cancel()

    def _cancel_voice_capture(self) -> None:
        self._voice_send_timer.stop()
        self._voice_send_pending = False
        self._auto_send_after_voice = False
        self._voice_transcript_ready = False
        self._voice_response_pending = False
        self._wake_detected_pending = False
        self._wake_detected_command = ""
        if self.voice_worker is not None and self.voice_worker.isRunning():
            self.status_label.setText("Cancelling voice capture… transcript will not be sent.")
            self.voice_worker.stop_recording()
        self.input_box.clear()
        self.send_button.setText("Send  ↗")
        self.listening_indicator.set_active(False)

    def _cancel_pending_voice_send(self) -> None:
        self._voice_send_timer.stop()
        self._voice_send_pending = False
        self._auto_send_after_voice = False
        self._voice_transcript_ready = False
        self._voice_response_pending = False
        self.send_button.setText("Send  ↗")
        self.status_label.setText("Voice command cancelled. Check or edit the transcript before sending.")
        if not self._wake_paused and not self._wake_word_failed:
            QTimer.singleShot(250, self._start_wake_listener)

    def _send_pending_voice(self) -> None:
        if not self._voice_send_pending:
            return
        self._voice_send_pending = False
        self.send_button.setText("Send  ↗")
        if self.input_box.toPlainText().strip():
            self.send_message()

    def _queue_voice_send(self) -> None:
        """Give an automatically captured wake-word transcript a brief cancel window."""
        self._voice_send_pending = True
        self.send_button.setText("Cancel")
        self.send_button.setEnabled(True)
        self.status_label.setText("Command captured · press Cancel if incorrect")
        self._voice_send_timer.start()

    def _confirm_file_delete(self, relative_path: str, context: object) -> None:
        """Ask for destructive file-operation confirmation on the UI thread."""
        event = context.get("event") if isinstance(context, dict) else None
        try:
            if not isinstance(context, dict) or not isinstance(event, threading.Event):
                return
            result = QMessageBox.question(
                self,
                "Delete workspace file?",
                f"Amigo wants to permanently delete this file from its workspace:\n\n{relative_path}\n\nThis cannot be undone.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            context["approved"] = result == QMessageBox.StandardButton.Yes
        finally:
            if isinstance(event, threading.Event):
                event.set()

    def _on_chunk(self, part: str) -> None:
        self._streamed_answer += part
        if self._ephemeral_row is not None:
            self._ephemeral_row.set_content(self._streamed_answer)
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _remove_ephemeral(self) -> None:
        if self._ephemeral_row is not None:
            self.chat_layout.removeWidget(self._ephemeral_row)
            self._ephemeral_row.deleteLater()
            self._ephemeral_row = None

    def _on_completed(self, answer: str) -> None:
        self._remove_ephemeral()
        self.store.add_message("assistant", answer)
        self._add_message("assistant", answer)
        if self._active_response_voice:
            self._speak_answer(answer)

    def _on_failed(self, message: str) -> None:
        self._remove_ephemeral()
        readable = message if message.startswith("Error:") else f"I hit a problem: {message}"
        self.store.add_message("assistant", readable)
        self._add_message("assistant", readable)
        if self._active_response_voice:
            self._speak_answer("I couldn't complete that request. Please try again.")

    def _on_cancelled(self) -> None:
        self._remove_ephemeral()
        self._active_response_voice = False
        self._voice_response_pending = False
        self.store.add_message("assistant", "Stopped.")
        self._add_message("assistant", "Stopped.")
        self.status_label.setText("Stopped · your request was cancelled")

    def _worker_finished(self) -> None:
        self.send_button.setText("Send  ↗")
        self.send_button.setEnabled(True)
        self.input_box.setEnabled(True)
        self.mic_button.setEnabled(True)
        self.new_button.setEnabled(True)
        self.history_button.setEnabled(True)
        self.input_box.setFocus()
        if self.speech_worker is not None and self.speech_worker.isRunning():
            self.status_label.setText("Speaking reply…")
        elif self.status_label.text().startswith("Researching"):
            self.status_label.setText("Ready · research finished")
        else:
            self.status_label.setText("Ready · local assistant")
        self.worker = None
        if not self._active_response_voice and not self._wake_paused and not self._wake_word_failed:
            QTimer.singleShot(180, self._start_wake_listener)

    def toggle_listening(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        if self.voice_worker is not None and self.voice_worker.isRunning():
            if self._auto_send_after_voice:
                self._cancel_voice_capture()
                return
            self.mic_button.setText("Finishing…")
            self.mic_button.setEnabled(False)
            self.voice_worker.stop_recording()
            return
        if self._wake_worker is not None and self._wake_worker.isRunning():
            self._manual_mic_pending = True
            self.status_label.setText("Pausing wake listener to open the microphone…")
            self._wake_worker.stop_listening()
            return
        self._start_voice_capture(auto_send=False, auto_stop=False)

    def _start_voice_capture(self, *, auto_send: bool, auto_stop: bool) -> None:
        if self.voice_worker is not None and self.voice_worker.isRunning():
            return
        model_dir = PROJECT_ROOT / "data" / "vosk-model-small-en-us-0.15"
        self._auto_send_after_voice = auto_send
        self._voice_transcript_ready = False
        self.voice_worker = VoiceInputWorker(
            model_dir, auto_stop_on_silence=auto_stop, silence_seconds=1.0 if auto_stop else 1.8,
            max_seconds=18 if auto_stop else 25, input_device=self._input_device_index,
        )
        self.voice_worker.status.connect(self._on_voice_status)
        self.voice_worker.transcribed.connect(self._on_transcribed)
        self.voice_worker.failed.connect(self._on_voice_error)
        self.voice_worker.finished.connect(self._voice_finished)
        self.new_button.setEnabled(False)
        self.history_button.setEnabled(False)
        self.mic_button.setEnabled(True)
        self.mic_button.setText("Listening…" if auto_stop else "Stop mic")
        self.mic_button.setProperty("recording", True)
        self.mic_button.style().unpolish(self.mic_button)
        self.mic_button.style().polish(self.mic_button)
        self.status_label.setText("Preparing offline microphone…")
        if auto_send:
            self.send_button.setText("Cancel")
            self.send_button.setEnabled(True)
        self.listening_indicator.set_active(True)
        self.voice_worker.start()

    def _start_wake_listener(self) -> None:
        if (
            not WAKE_WORD_ENABLED or self._quitting or self._wake_paused or self._wake_word_failed
            or (self._wake_worker is not None and self._wake_worker.isRunning())
            or (self.worker is not None and self.worker.isRunning())
            or (self.voice_worker is not None and self.voice_worker.isRunning())
            or (self.speech_worker is not None and self.speech_worker.isRunning())
        ):
            return
        model_dir = PROJECT_ROOT / "data" / "vosk-model-small-en-us-0.15"
        self._wake_worker = WakeWordWorker(model_dir, self._input_device_index)
        self._wake_worker.status.connect(self._on_wake_status)
        worker = self._wake_worker
        worker.wake_detected.connect(lambda command, current=worker: self._on_wake_detected(command, current))
        worker.failed.connect(lambda message, current=worker: self._on_wake_error(message, current))
        worker.finished.connect(lambda current=worker: self._wake_worker_finished(current))
        self.tray.setToolTip("Amigo · listening for wake word ‘Amigo’ (microphone on)")
        self.status_label.setText("Waiting for ‘Amigo’")
        self.listening_indicator.set_active(True)
        self._wake_worker.start()

    def _stop_wake_listener(self) -> None:
        if self._wake_worker is not None and self._wake_worker.isRunning():
            self._wake_worker.stop_listening()

    def _on_wake_status(self, status: str) -> None:
        if "Wake word active" in status:
            self.status_label.setText("Waiting for ‘Amigo’")
            self.tray.setToolTip("Amigo · wake word active · microphone on")
            self.listening_indicator.set_active(True)
        elif status.startswith("Loading local wake-word"):
            self.status_label.setText("Starting wake listener…")
            self.listening_indicator.set_active(True)
        else:
            self.status_label.setText(status)

    def _on_wake_detected(self, command: str, source_worker: WakeWordWorker | None = None) -> None:
        if source_worker is not None and self._wake_worker is not source_worker:
            return
        self._wake_detected_pending = True
        self._wake_detected_command = command.strip()
        if self._wake_detected_command:
            self.input_box.setPlainText(self._wake_detected_command)
        self._voice_response_pending = True
        self._show_popup_from_wake()
        self.status_label.setText("Command heard · preparing response…" if command.strip() else "Wake word heard · preparing microphone…")
        if not command.strip():
            self.input_box.clear()
        QTimer.singleShot(80, self._maybe_begin_wake_capture)

    def _show_popup_from_wake(self) -> None:
        self._position_popup()
        self.show()
        self.raise_()
        self.activateWindow()
        self.input_box.setFocus()
        self.tray.setToolTip("Amigo · window open")

    def _maybe_begin_wake_capture(self) -> None:
        if not self._wake_detected_pending or self._quitting:
            return
        if self._wake_worker is not None and self._wake_worker.isRunning():
            QTimer.singleShot(100, self._maybe_begin_wake_capture)
            return
        if self.voice_worker is not None and self.voice_worker.isRunning():
            return
        command = self._wake_detected_command
        self._wake_detected_command = ""
        self._wake_detected_pending = False
        if command:
            self.input_box.setPlainText(command)
            self._queue_voice_send()
        else:
            self._start_voice_capture(auto_send=True, auto_stop=True)

    def _wake_worker_finished(self, source_worker: WakeWordWorker | None = None) -> None:
        if source_worker is not None and self._wake_worker is not source_worker:
            return
        self._wake_worker = None
        if self._wake_detected_pending:
            QTimer.singleShot(0, self._maybe_begin_wake_capture)
            return
        if self._manual_mic_pending:
            self._manual_mic_pending = False
            self._start_voice_capture(auto_send=False, auto_stop=False)
            return
        if self._pending_send_after_wake_stop:
            self._pending_send_after_wake_stop = False
            self.listening_indicator.set_active(False)
            QTimer.singleShot(0, self.send_message)
            return
        self.listening_indicator.set_active(False)
        if not self._wake_paused and not self._wake_word_failed:
            QTimer.singleShot(350, self._start_wake_listener)

    def _on_wake_error(self, message: str, source_worker: WakeWordWorker | None = None) -> None:
        if source_worker is not None and self._wake_worker is not source_worker:
            return
        self._wake_word_failed = True
        self.listening_indicator.set_active(False)
        self.wake_toggle_action.setText("Retry wake word listening")
        self.tray.setToolTip("Amigo · wake word unavailable; open from tray")
        self.tray.showMessage(
            "Amigo wake word unavailable", message + " You can still open Amigo from the system tray and use Mic manually.",
            QSystemTrayIcon.MessageIcon.Warning, 5000,
        )
        self.status_label.setText("Wake word unavailable · open Amigo from the tray")

    def toggle_wake_word(self) -> None:
        if self._wake_paused or self._wake_word_failed:
            self._wake_paused = False
            self._wake_word_failed = False
            self.wake_toggle_action.setText("Pause wake word listening")
            self.tray.setToolTip("Amigo · wake word resume requested")
            QTimer.singleShot(150, self._start_wake_listener)
        else:
            self._wake_paused = True
            self.wake_toggle_action.setText("Resume wake word listening")
            self._stop_wake_listener()
            self.tray.setToolTip("Amigo · wake word paused · microphone idle")
            self.tray.showMessage(
                "Wake word paused", "Amigo will not listen for its wake word until you resume it from the tray.",
                QSystemTrayIcon.MessageIcon.Information, 2500,
            )

    def start_in_background(self) -> None:
        """Start hidden as a tray resident; the wake word opens the main window."""
        self.hide()
        QTimer.singleShot(250, self._start_wake_listener)
        QTimer.singleShot(1000, lambda: self.tray.showMessage(
            "Amigo is ready", "Say ‘Amigo’ to open the main window. Wake listening uses your microphone locally.",
            QSystemTrayIcon.MessageIcon.Information, 3500
        ))

    def hide_to_tray(self) -> None:
        self.close()

    def _position_popup(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.resize(
            max(self.minimumWidth(), min(1460, area.width() - 24)),
            max(self.minimumHeight(), min(880, area.height() - 32)),
        )
        x = area.left() + (area.width() - self.width()) // 2
        y = area.top() + (area.height() - self.height()) // 2
        self.move(x, y)

    def _on_voice_status(self, status: str) -> None:
        self.status_label.setText(status)
        if status.startswith("Listening"):
            self.mic_button.setText("Finish")
            self.listening_indicator.set_active(True)

    def _on_transcribed(self, transcript: str) -> None:
        self._voice_transcript_ready = True
        existing = self.input_box.toPlainText().strip()
        self.input_box.setPlainText(f"{existing} {transcript}".strip())
        self.input_box.setFocus()
        self.input_box.moveCursor(QTextCursor.MoveOperation.End)
        if self._auto_send_after_voice:
            self.status_label.setText("Transcript ready · auto-send in 1.8s; press Cancel if incorrect")
        else:
            self.status_label.setText("Transcript ready · review it, then press Send")

    def _on_voice_error(self, message: str) -> None:
        was_automatic = self._auto_send_after_voice
        self._auto_send_after_voice = False
        if was_automatic:
            self._voice_response_pending = False
            self._voice_send_pending = False
            self._voice_send_timer.stop()
            self.send_button.setText("Send  ↗")
        self.status_label.setText("Didn't catch that · try again" if was_automatic else "Voice unavailable · see message")
        if not was_automatic:
            QMessageBox.information(self, "Amigo voice", message)

    def _voice_finished(self) -> None:
        busy = self.worker is not None and self.worker.isRunning()
        auto_send = self._auto_send_after_voice and self._voice_transcript_ready
        self._auto_send_after_voice = False
        self._voice_transcript_ready = False
        self.mic_button.setText("Mic")
        self.mic_button.setEnabled(not busy)
        self.new_button.setEnabled(not busy)
        self.history_button.setEnabled(not busy)
        self.mic_button.setProperty("recording", False)
        self.mic_button.style().unpolish(self.mic_button)
        self.mic_button.style().polish(self.mic_button)
        self.voice_worker = None
        self.listening_indicator.set_active(False)
        if not self.status_label.text().startswith((
            "Transcript ready", "Command heard", "Didn't catch", "Voice unavailable"
        )):
            self.status_label.setText("Ready · microphone off")
        if auto_send:
            # Give the user a chance to cancel or correct a misheard transcript
            # before the model/tool request is submitted.
            self.input_box.setEnabled(True)
            self._queue_voice_send()
            self.status_label.setText("Transcript ready · press Cancel to stop, or wait to send")
        elif not self._wake_paused and not self._wake_word_failed:
            self.send_button.setText("Send  ↗")
            QTimer.singleShot(250, self._start_wake_listener)

    def _speak_answer(self, answer: str) -> None:
        if self.speech_worker is not None and self.speech_worker.isRunning():
            return
        # Spoken replies should be useful, not a recital of links and markup.
        text = re.sub(r"https?://\S+", "", answer)
        text = re.sub(r"(?m)^Sources:\s*$", "", text)
        text = re.sub(r"(?m)^\s*[-*#>`]+\s*", "", text)
        text = re.sub(r"[*_`#]", "", text)
        text = " ".join(text.split()).strip()
        words = text.split()
        if len(words) > 38:
            text = " ".join(words[:34]).rstrip(".,;:") + ". More details are on screen."
        if not text:
            self._active_response_voice = False
            if not self._wake_paused and not self._wake_word_failed:
                QTimer.singleShot(250, self._start_wake_listener)
            return
        self.speech_worker = SpeechOutputWorker(text)
        self.speech_worker.failed.connect(lambda message: self.status_label.setText(message))
        self.speech_worker.finished.connect(self._speech_finished)
        self.status_label.setText("Speaking reply…")
        self.send_button.setText("Stop speech  ■")
        self.send_button.setEnabled(True)
        self.speech_worker.start()

    def _speech_finished(self) -> None:
        self.speech_worker = None
        self.send_button.setText("Send  ↗")
        self.send_button.setEnabled(True)
        self._active_response_voice = False
        if not (self.worker is not None and self.worker.isRunning()):
            self.status_label.setText("Waiting for ‘Amigo’" if WAKE_WORD_ENABLED and not self._wake_paused else "Ready · local assistant")
        if not self._wake_paused and not self._wake_word_failed:
            QTimer.singleShot(250, self._start_wake_listener)

    def eventFilter(self, watched, event):
        # Enter sends; Shift+Enter inserts a newline.
        if watched is self.input_box and event.type() == QEvent.Type.KeyPress:
            if isinstance(event, QKeyEvent) and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    return False
                self.send_message()
                return True
        return super().eventFilter(watched, event)

    def show_and_focus(self) -> None:
        self._position_popup()
        self.show()
        self.raise_()
        self.activateWindow()
        self.input_box.setFocus()
        self.tray.setToolTip("Amigo · window open")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 60:
            self._drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_position is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_position)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_position = None
        super().mouseReleaseEvent(event)

    def closeEvent(self, event) -> None:
        if not self._quitting and (
            self.goals_panel.is_busy() or self.knowledge_panel.is_busy()
        ):
            QMessageBox.information(self, "Indexing in progress", "Wait for the material copy or search indexing to finish before closing Amigo.")
            event.ignore()
            return
        if self._quitting:
            event.accept()
        elif not QSystemTrayIcon.isSystemTrayAvailable():
            if self.voice_worker is not None and self.voice_worker.isRunning():
                self.voice_worker.stop_recording()
            self._stop_wake_listener()
            event.accept()
            QTimer.singleShot(0, QApplication.quit)
        else:
            if self.voice_worker is not None and self.voice_worker.isRunning():
                self._auto_send_after_voice = False
                self.voice_worker.stop_recording()
            event.ignore()
            self.hide()
            self.tray.setToolTip("Amigo · in tray")
            if self.voice_worker is None and not (self.speech_worker is not None and self.speech_worker.isRunning()):
                QTimer.singleShot(350, self._start_wake_listener)

    def quit_app(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, "Amigo is busy", "Wait for the current task to finish before quitting.")
            return
        if self.goals_panel.is_busy() or self.knowledge_panel.is_busy():
            QMessageBox.information(self, "Indexing in progress", "Wait for material import or search indexing to finish before quitting.")
            return
        self._quitting = True
        self._stop_wake_listener()
        if self.voice_worker is not None and self.voice_worker.isRunning():
            self.voice_worker.stop_recording()
            if not self.voice_worker.wait(5000):
                self._quitting = False
                QMessageBox.information(self, "Microphone is stopping", "Wait a moment for Amigo to release the microphone, then choose Quit again.")
                return
        if self._wake_worker is not None and self._wake_worker.isRunning():
            if not self._wake_worker.wait(5000):
                self._quitting = False
                QMessageBox.information(self, "Wake listener is stopping", "Wait a moment for Amigo to release the microphone, then choose Quit again.")
                return
        self.tray.hide()
        QApplication.quit()
