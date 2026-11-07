from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .theme import PRODUCT_STYLE


HELP_SECTIONS: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
    "Today": (
        "Your daily landing page. Use it to see what is planned and what you have actually done.",
        (
            ("Today's focus", "Shows useful next steps, such as a planned work session or a goal milestone that is due."),
            ("Activity checklist", "Add tasks you plan to do. Checking one records that you finished it. Estimated minutes are only a rough plan; actual time is recorded only when you enter it."),
            ("Log unplanned work", "Record something you already did, even if it was not on your checklist."),
            ("Continue where you left off", "Jump back to your latest activity or note."),
            ("Activity calendar", "A marked day means work was recorded that day. Select a day to see its activity records."),
            ("Quick capture", "Quickly add a task, record finished work, save a note, create a goal, or propose a memory."),
            ("Curiosity and garden", "Curiosity offers a question from your notes or topics. Garden plants grow as you record work and complete milestones."),
            ("Weekly review, focus, and replan", "Review your week, time a focus session, or choose a new date for a milestone. Amigo asks before changing a milestone date."),
        ),
    ),
    "Goals": (
        "A goal is an outcome you want to reach over time.",
        (
            ("Milestones", "Break a goal into smaller checkpoints. Mark a milestone complete when you reach it."),
            ("Activities", "Record work you have done and connect it to a goal or topic. Planned work by itself does not count as completed work."),
            ("Progress and history", "Progress is summarized from completed milestones and recorded activities. Goal changes are kept in the history."),
            ("Pause and resume", "Pause a goal when you are taking a break. Resume it when you are ready to work on it again."),
        ),
    ),
    "Projects": (
        "Projects and Study Hubs keep related work together without copying it.",
        (
            ("Create a hub", "Use a Project for an ongoing effort or a Study Hub for a subject you are learning."),
            ("Connect existing work", "Link goals, topics, notes, materials, and activities so they are easier to find together."),
            ("Ask about this hub", "Ask Amigo for help using the hub's connected local notes and materials."),
            ("Pause or complete", "Pause a hub temporarily, or mark it complete when the work is finished."),
        ),
    ),
    "Learning": (
        "Use Learning to keep track of how you are getting on with each topic.",
        (
            ("Learning state", "Choose a simple state such as Learning, Understood, or Needs revision. It is your current view, not a grade."),
            ("Evidence history", "Reflections, activity, and quiz results give you a record of what you have tried."),
            ("Source-based quizzes", "Amigo can make questions from readable notes and materials linked to a topic. Add or index a source first."),
            ("Suggestions", "Suggestions point to a possible next step. You can accept, postpone, or dismiss them."),
        ),
    ),
    "Knowledge": (
        "Knowledge is your local library of notes and copied materials.",
        (
            ("Notes", "Write and update topic-linked Markdown notes. Search can find text inside notes."),
            ("Materials", "Importing copies a file into Amigo's Materials folder and leaves the original where it is. Readable files can be indexed for search."),
            ("Ask about sources", "Ask Amigo a question about local notes or indexed files. Check the source path shown with the answer."),
            ("Compare research", "Choose two indexed files to inspect their source sections, or ask Amigo to compare them with references."),
        ),
    ),
    "Calendar": (
        "Calendar is for planning future time and events.",
        (
            ("Work sessions", "Plan time you hope to spend working. A planned session does not mean the work is finished."),
            ("Events", "Add appointments or other dates you want to keep track of."),
            ("Actual work", "Record finished work from Today, Goals, or a completed focus session. It stays separate from your schedule."),
        ),
    ),
    "Assistant & voice": (
        "Chat with Amigo for help, explanations, and actions in your workspace.",
        (
            ("Ask a question", "Type in the box at the bottom of the chat and press Send. Use local-notes wording when you want an answer from your Knowledge library."),
            ("Voice", "Press Mic to record a message. You can review or edit the words before sending."),
            ("Local and web answers", "Amigo normally uses its local model. Requests for current web information may use the configured web research service."),
            ("History", "Open an earlier chat from History, or choose New chat to start a separate conversation."),
        ),
    ),
    "Search": (
        "Search looks across your Amigo workspace in one place.",
        (
            ("What you can find", "Search goals, milestones, projects, planned sessions, events, activities, topics, notes, materials, saved discoveries, and indexed passages."),
            ("Open a result", "Double-click a result or select it and choose Open. Amigo takes you to the related page or source."),
        ),
    ),
    "Profile & data": (
        "Profile lets you choose what personal details Amigo can use to tailor local replies.",
        (
            ("Profile details", "Add your name, interests, goals, or response preferences. You can edit or clear them at any time."),
            ("Memories", "A proposed memory is not used until you approve it. Editing approved text sends it back for approval. You can delete a memory whenever you like."),
            ("Backup", "Export a ZIP containing your Amigo database, Knowledge notes, and copied Materials."),
        ),
    ),
}


class HelpDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, initial_topic: str = "Today") -> None:
        super().__init__(parent)
        self.setWindowTitle("Amigo quick guide")
        self.setStyleSheet(PRODUCT_STYLE)
        self.resize(640, 480)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Amigo quick guide")
        title.setObjectName("pageTitle")
        header.addWidget(title, 1)
        close = QPushButton("Done")
        close.setObjectName("secondaryAction")
        close.clicked.connect(self.accept)
        header.addWidget(close)
        root.addLayout(header)
        intro = QLabel("Choose a section to see what its buttons and tools do, in plain words.")
        intro.setObjectName("pageDescription")
        root.addWidget(intro)

        body = QHBoxLayout()
        body.setSpacing(12)
        self.topic_list = QListWidget()
        self.topic_list.setObjectName("helpTopics")
        self.topic_list.setFixedWidth(178)
        body.addWidget(self.topic_list)

        detail_frame = QFrame()
        detail_frame.setObjectName("contentCard")
        detail_layout = QVBoxLayout(detail_frame)
        detail_layout.setContentsMargins(14, 12, 14, 12)
        self.detail_scroll = QScrollArea()
        self.detail_scroll.setObjectName("helpDetailScroll")
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.viewport().setStyleSheet("background-color: #FFFEFB;")
        self.detail_body = QWidget()
        self.detail_body.setObjectName("helpDetailContent")
        self.detail_body.setStyleSheet(PRODUCT_STYLE)
        self.detail_layout = QVBoxLayout(self.detail_body)
        self.detail_layout.setContentsMargins(2, 2, 2, 2)
        self.detail_layout.setSpacing(10)
        self.detail_scroll.setWidget(self.detail_body)
        detail_layout.addWidget(self.detail_scroll)
        body.addWidget(detail_frame, 1)
        root.addLayout(body, 1)

        for topic in HELP_SECTIONS:
            self.topic_list.addItem(QListWidgetItem(topic))
        self.topic_list.currentRowChanged.connect(self._show_topic)
        start = next((index for index, topic in enumerate(HELP_SECTIONS) if topic == initial_topic), 0)
        self.topic_list.setCurrentRow(start)

    def _show_topic(self, row: int) -> None:
        topics = list(HELP_SECTIONS)
        if not 0 <= row < len(topics):
            return
        topic = topics[row]
        intro, items = HELP_SECTIONS[topic]
        while self.detail_layout.count():
            child = self.detail_layout.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()

        heading = QLabel(topic)
        heading.setObjectName("pageTitle")
        self.detail_layout.addWidget(heading)
        summary = QLabel(intro)
        summary.setObjectName("pageDescription")
        summary.setWordWrap(True)
        self.detail_layout.addWidget(summary)
        for label_text, body_text in items:
            card = QFrame()
            card.setObjectName("helpCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(11, 9, 11, 9)
            card_layout.setSpacing(3)
            label = QLabel(label_text)
            label.setObjectName("sectionTitle")
            body = QLabel(body_text)
            body.setObjectName("metaText")
            body.setWordWrap(True)
            card_layout.addWidget(label)
            card_layout.addWidget(body)
            self.detail_layout.addWidget(card)
        self.detail_layout.addStretch(1)
