from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCore import QDate, QDateTime, QTime, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDateEdit,
    QDateTimeEdit,
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
    QVBoxLayout,
    QWidget,
)

from .calendar import CalendarQuery, CalendarService
from .domain import AmigoServiceError, CalendarEntryRecord, CalendarItemKind, CalendarItemStatus
from .services import GoalService
from .storage import SqliteDatabase
from .theme import PRODUCT_STYLE


class CalendarItemDialog(QDialog):
    def __init__(
        self,
        database: SqliteDatabase,
        kind: CalendarItemKind,
        parent: QWidget | None = None,
        *,
        entry: dict[str, Any] | None = None,
        selected_day: QDate | None = None,
    ) -> None:
        super().__init__(parent)
        self.database = database
        self.kind = kind
        self.entry = entry
        label = "planned session" if kind == CalendarItemKind.SESSION else "calendar event"
        self.setWindowTitle(f"Edit {label}" if entry else f"Plan a {label}")
        self.setStyleSheet(PRODUCT_STYLE)
        self.resize(500, 540 if kind == CalendarItemKind.SESSION else 430)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title_edit = QLineEdit(str((entry or {}).get("title") or ""))
        self.title_edit.setPlaceholderText("What is this time for?")
        self.start_edit = QDateTimeEdit()
        self.start_edit.setCalendarPopup(True)
        self.start_edit.setDisplayFormat("ddd d MMM yyyy · HH:mm")
        self.end_edit = QDateTimeEdit()
        self.end_edit.setCalendarPopup(True)
        self.end_edit.setDisplayFormat("ddd d MMM yyyy · HH:mm")
        self._set_default_times(selected_day)
        if entry:
            start = QDateTime.fromString(str(entry["starts_at"]), Qt.DateFormat.ISODate).toLocalTime()
            end = QDateTime.fromString(str(entry["ends_at"]), Qt.DateFormat.ISODate).toLocalTime()
            if start.isValid():
                self.start_edit.setDateTime(start)
            if end.isValid():
                self.end_edit.setDateTime(end)
        self.description_edit = QPlainTextEdit(str((entry or {}).get("description") or ""))
        self.description_edit.setPlaceholderText("Optional details")
        self.description_edit.setFixedHeight(82)
        form.addRow("Title", self.title_edit)
        form.addRow("Start", self.start_edit)
        form.addRow("End", self.end_edit)
        form.addRow("Details", self.description_edit)
        layout.addLayout(form)

        self.goal_list: QListWidget | None = None
        self.topic_edit: QLineEdit | None = None
        if kind == CalendarItemKind.SESSION:
            goal_service = GoalService(database)
            linked_goal_ids = {str(value) for value in (entry or {}).get("goal_ids", [])}
            self.goal_list = QListWidget()
            self.goal_list.setToolTip("Goal links organize planned work; sessions do not change goal progress.")
            self.goal_list.setMaximumHeight(105)
            for goal in goal_service.list_goals():
                item = QListWidgetItem(f"{goal['title']} · {goal['status']}")
                item.setData(Qt.ItemDataRole.UserRole, str(goal["id"]))
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked if str(goal["id"]) in linked_goal_ids else Qt.CheckState.Unchecked
                )
                self.goal_list.addItem(item)
            self.topic_edit = QLineEdit("; ".join((entry or {}).get("topic_names", [])))
            self.topic_edit.setPlaceholderText("Topic names separated by semicolons")
            layout.addWidget(QLabel("Linked goals (optional)"))
            layout.addWidget(self.goal_list)
            topic_label = QLabel("Topics (optional; separate multiple topics with semicolons)")
            layout.addWidget(topic_label)
            layout.addWidget(self.topic_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.title_edit.setFocus()

    def _set_default_times(self, selected_day: QDate | None) -> None:
        now = QDateTime.currentDateTime()
        if selected_day is None or not selected_day.isValid():
            start = now.addSecs(3600)
            start.setTime(QTime(start.time().hour(), 0))
        elif selected_day == now.date():
            start = now.addSecs(3600)
            start.setTime(QTime(start.time().hour(), 0))
        else:
            start = QDateTime(selected_day, QTime(9, 0))
        self.start_edit.setDateTime(start)
        self.end_edit.setDateTime(start.addSecs(3600))

    def _accept_if_valid(self) -> None:
        if not self.title_edit.text().strip():
            QMessageBox.information(self, "Title required", "Enter a short title for this calendar item.")
            self.title_edit.setFocus()
            return
        if self.end_edit.dateTime() <= self.start_edit.dateTime():
            QMessageBox.information(self, "Check the time", "The end time must be after the start time.")
            return
        self.accept()

    def values(self) -> dict[str, Any]:
        values: dict[str, Any] = {
            "title": self.title_edit.text().strip(),
            "description": self.description_edit.toPlainText().strip(),
            "starts_at": self.start_edit.dateTime().toString(Qt.DateFormat.ISODate),
            "ends_at": self.end_edit.dateTime().toString(Qt.DateFormat.ISODate),
        }
        if self.kind == CalendarItemKind.SESSION:
            assert self.goal_list is not None and self.topic_edit is not None
            values["goal_ids"] = [
                str(self.goal_list.item(index).data(Qt.ItemDataRole.UserRole))
                for index in range(self.goal_list.count())
                if self.goal_list.item(index).checkState() == Qt.CheckState.Checked
            ]
            values["topic_names"] = [
                name.strip() for name in self.topic_edit.text().split(";") if name.strip()
            ]
        return values


class CalendarPanel(QWidget):
    """Day agenda for planned sessions and calendar events, kept apart from actual work."""

    def __init__(self, database: SqliteDatabase, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.database = database
        self.calendar_service = CalendarService(database)
        self.calendar_query = CalendarQuery(database)
        self.goals = GoalService(database)
        self._entries: list[CalendarEntryRecord] = []
        self.setStyleSheet(PRODUCT_STYLE)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)
        title = QLabel("Calendar · plan ahead")
        title.setObjectName("pageTitle")
        root.addWidget(title)
        help_text = QLabel("Plan work sessions and add general events. Planned time does not count as completed work or goal progress.")
        help_text.setObjectName("pageDescription")
        help_text.setWordWrap(True)
        root.addWidget(help_text)

        date_row = QHBoxLayout()
        self.previous_button = QPushButton("‹")
        self.previous_button.setToolTip("Previous day")
        self.previous_button.clicked.connect(lambda: self._shift_day(-1))
        self.today_button = QPushButton("Today")
        self.today_button.setObjectName("secondaryAction")
        self.today_button.clicked.connect(self._today)
        self.selected_date = QDateEdit(QDate.currentDate())
        self.selected_date.setCalendarPopup(True)
        self.selected_date.setDisplayFormat("dddd, d MMM yyyy")
        self.selected_date.dateChanged.connect(self._refresh)
        self.next_button = QPushButton("›")
        self.next_button.setToolTip("Next day")
        self.next_button.clicked.connect(lambda: self._shift_day(1))
        date_row.addWidget(self.previous_button)
        date_row.addWidget(self.today_button)
        date_row.addWidget(self.selected_date, 1)
        date_row.addWidget(self.next_button)
        root.addLayout(date_row)

        filter_row = QHBoxLayout()
        self.include_cancelled = QCheckBox("Include cancelled")
        self.include_cancelled.toggled.connect(self._refresh)
        self.agenda_label = QLabel("")
        self.agenda_label.setObjectName("metaText")
        filter_row.addWidget(self.include_cancelled)
        filter_row.addStretch(1)
        filter_row.addWidget(self.agenda_label)
        root.addLayout(filter_row)

        self.entry_list = QListWidget()
        self.entry_list.currentRowChanged.connect(self._selection_changed)
        root.addWidget(self.entry_list, 2)
        self.entry_detail = QPlainTextEdit()
        self.entry_detail.setReadOnly(True)
        self.entry_detail.setObjectName("readPanel")
        self.entry_detail.setMaximumHeight(120)
        self.entry_detail.setPlaceholderText("Select a planned session or calendar event to see its details.")
        root.addWidget(self.entry_detail, 1)

        action_row = QHBoxLayout()
        self.plan_session_button = QPushButton("+ Plan session")
        self.plan_session_button.setObjectName("primaryAction")
        self.plan_session_button.clicked.connect(self._create_session)
        self.add_event_button = QPushButton("+ Event")
        self.add_event_button.setObjectName("secondaryAction")
        self.add_event_button.clicked.connect(self._create_event)
        action_row.addWidget(self.plan_session_button)
        action_row.addWidget(self.add_event_button)
        root.addLayout(action_row)
        edit_row = QHBoxLayout()
        self.edit_button = QPushButton("Edit / reschedule")
        self.edit_button.setObjectName("secondaryAction")
        self.edit_button.clicked.connect(self._edit_selected)
        self.cancel_button = QPushButton("Cancel item")
        self.cancel_button.setObjectName("warningAction")
        self.cancel_button.clicked.connect(self._cancel_selected)
        edit_row.addWidget(self.edit_button)
        edit_row.addStretch(1)
        edit_row.addWidget(self.cancel_button)
        root.addLayout(edit_row)
        self.status_line = QLabel("Completed work is logged separately from planned time.")
        self.status_line.setObjectName("footnote")
        self.status_line.setWordWrap(True)
        root.addWidget(self.status_line)
        self._refresh()

    def _shift_day(self, amount: int) -> None:
        self.selected_date.setDate(self.selected_date.date().addDays(amount))

    def _today(self) -> None:
        self.selected_date.setDate(QDate.currentDate())

    def open_entry(self, day_text: str, entry_id: str) -> None:
        selected = QDate.fromString(day_text, Qt.DateFormat.ISODate)
        if not selected.isValid():
            return
        if not self.include_cancelled.isChecked():
            self.include_cancelled.setChecked(True)
        self.selected_date.setDate(selected)
        self._refresh(preserve_id=entry_id)

    def _refresh(self, *_args, preserve_id: str | None = None) -> None:
        selected_id = preserve_id or (self._selected_entry().id if self._selected_entry() else None)
        selected_day = self.selected_date.date().toPython()
        self._entries = self.calendar_query.entries_for_day(
            selected_day, include_cancelled=self.include_cancelled.isChecked()
        )
        self.entry_list.blockSignals(True)
        self.entry_list.clear()
        selected_row = -1
        for index, entry in enumerate(self._entries):
            start = datetime.fromisoformat(entry.starts_at).astimezone()
            end = datetime.fromisoformat(entry.ends_at).astimezone()
            time_text = self._time_text(start, end)
            kind_text = "Session" if entry.kind == CalendarItemKind.SESSION else "Event"
            canceled = " · Cancelled" if entry.status == CalendarItemStatus.CANCELLED else ""
            item = QListWidgetItem(f"{time_text}   ·   {kind_text}   ·   {entry.title}{canceled}")
            item.setData(Qt.ItemDataRole.UserRole, entry.id)
            item.setData(Qt.ItemDataRole.UserRole + 1, entry.kind.value)
            self.entry_list.addItem(item)
            if entry.id == selected_id:
                selected_row = index
        self.entry_list.blockSignals(False)
        self.agenda_label.setText(f"{len(self._entries)} item{'s' if len(self._entries) != 1 else ''}")
        if selected_row < 0 and self._entries:
            selected_row = 0
        self.entry_list.setCurrentRow(selected_row)
        self._selection_changed(selected_row)
        if not self._entries:
            self.status_line.setText("No planned items for this day. Add a session or event; record actual work separately in Goals.")

    @staticmethod
    def _time_text(start: datetime, end: datetime) -> str:
        start_text = start.strftime("%I:%M %p").lstrip("0")
        end_text = end.strftime("%I:%M %p").lstrip("0")
        if start.date() == end.date():
            return f"{start_text}–{end_text}"
        return f"{start_text} {start.strftime('%b %d')} – {end_text} {end.strftime('%b %d')}"

    def _selected_entry(self) -> CalendarEntryRecord | None:
        row = self.entry_list.currentRow()
        return self._entries[row] if 0 <= row < len(self._entries) else None

    def _selection_changed(self, row: int) -> None:
        entry = self._entries[row] if 0 <= row < len(self._entries) else None
        if entry is None:
            if self._entries:
                self.entry_detail.clear()
            else:
                self.entry_detail.setPlainText(
                    "Nothing planned for this day.\nPlan a work session or add an event; log completed work in Goals."
                )
            self.edit_button.setEnabled(False)
            self.cancel_button.setEnabled(False)
            return
        kind = "Planned work session" if entry.kind == CalendarItemKind.SESSION else "Calendar event"
        status = "Scheduled" if entry.status == CalendarItemStatus.SCHEDULED else "Cancelled"
        details = [kind, status]
        if entry.description:
            details.append(entry.description)
        if entry.goal_names:
            details.append(f"Goals: {entry.goal_names}")
        if entry.topic_names:
            details.append(f"Topics: {entry.topic_names}")
        details.append("This item is a plan; log actual work in Goals to update progress.")
        self.entry_detail.setPlainText("\n".join(details))
        active = entry.status == CalendarItemStatus.SCHEDULED
        self.edit_button.setEnabled(active)
        self.cancel_button.setEnabled(active)

    def _create_session(self) -> None:
        self._open_dialog(CalendarItemKind.SESSION)

    def _create_event(self) -> None:
        self._open_dialog(CalendarItemKind.EVENT)

    def _edit_selected(self) -> None:
        entry = self._selected_entry()
        if entry is None or entry.status != CalendarItemStatus.SCHEDULED:
            return
        if entry.kind == CalendarItemKind.SESSION:
            details = self.calendar_service.get_session(entry.id)
        else:
            details = self.calendar_service.get_event(entry.id)
        self._open_dialog(entry.kind, details)

    def _open_dialog(
        self, kind: CalendarItemKind, entry: dict[str, Any] | None = None
    ) -> None:
        dialog = CalendarItemDialog(
            self.database,
            kind,
            self,
            entry=entry,
            selected_day=self.selected_date.date(),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        try:
            if kind == CalendarItemKind.SESSION:
                if entry:
                    result = self.calendar_service.update_session(entry["id"], **values)
                else:
                    result = self.calendar_service.create_session(**values)
            else:
                if entry:
                    result = self.calendar_service.update_event(entry["id"], **values)
                else:
                    result = self.calendar_service.create_event(**values)
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Calendar item not saved", str(exc))
            return
        local_start = datetime.fromisoformat(str(result["starts_at"])).astimezone()
        self.selected_date.setDate(QDate(local_start.year, local_start.month, local_start.day))
        self.status_line.setText(
            f"Saved planned {kind.value}. Planned time is separate from completed activities and milestones."
        )
        self._refresh(preserve_id=str(result["id"]))

    def _cancel_selected(self) -> None:
        entry = self._selected_entry()
        if entry is None or entry.status != CalendarItemStatus.SCHEDULED:
            return
        label = "planned session" if entry.kind == CalendarItemKind.SESSION else "calendar event"
        answer = QMessageBox.question(
            self, f"Cancel {label}", f"Cancel “{entry.title}”? Its record will remain in the calendar history."
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            if entry.kind == CalendarItemKind.SESSION:
                self.calendar_service.cancel_session(entry.id)
            else:
                self.calendar_service.cancel_event(entry.id)
        except AmigoServiceError as exc:
            QMessageBox.warning(self, "Calendar item not cancelled", str(exc))
            return
        self.status_line.setText(f"Cancelled {label}. No activity or goal progress was created.")
        self._refresh(preserve_id=entry.id)

