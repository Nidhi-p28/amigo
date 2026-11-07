"""Shared Amigo desktop theme tokens and widget styles."""


APP_STYLE = """
QMainWindow, QWidget {
    color: #292C2F;
    font-family: 'Segoe UI';
    font-size: 10pt;
}
QMainWindow { background: #F3F3F1; border: none; }
QWidget#popupFrame {
    background: #F7F7F5;
    border: 1px solid #D9D9D6;
    border-radius: 18px;
}
QFrame#header { background: transparent; border: none; }
QFrame#navGroup { background: #EBEBE9; border: 1px solid #D8D8D5; border-radius: 11px; }
QFrame#workspaceColumn {
    background-color: #F8F8F6;
    border: 1px solid #D7D7D3;
    border-radius: 16px;
}
QFrame#chatColumn {
    background: #FEFEFD;
    border: 1px solid #D7D7D3;
    border-radius: 16px;
}
QFrame#chatHeader { background: transparent; border-bottom: 1px dotted #D5D5D1; }
QFrame#composerFrame { background: #F1F1EF; border: 1px solid #D8D8D4; border-radius: 12px; }
QFrame#focusCard { background: #FFFFFF; border: 1px dotted #777A7C; border-radius: 10px; }
QFrame#activityCard { background: #FAFAF8; border: 1px dotted #A6A7A5; border-radius: 10px; }
QFrame#goalsCard { background: #F0F0EE; border: 1px dotted #929493; border-radius: 10px; }
QFrame#heatmapCard { background: #F7F7F5; border: 1px dotted #A6A7A5; border-radius: 10px; }
QFrame#continueCard { background: #EEEEEC; border: 1px dotted #A6A7A5; border-radius: 10px; }
QFrame#curiosityCard { background: #FAFAF8; border: 1px dotted #A6A7A5; border-radius: 10px; }
QFrame#gardenCard { background: #E4EEE2; border: 1px solid #BFD1BB; border-radius: 10px; }
QLabel#brandMark {
    background: #E5E5E2;
    color: #292C2F;
    border-radius: 11px;
    font-family: 'Georgia';
    font-size: 18pt;
    font-weight: 600;
}
QLabel#brandName { color: #25282B; font-family: 'Georgia'; font-size: 20pt; font-weight: 500; }
QLabel#brandTag { color: #777B7C; font-size: 7.5pt; letter-spacing: 1.1px; }
QLabel#status { color: #666A6C; font-size: 8.5pt; }
QLabel#stageTag {
    color: #3E4245;
    background: #ECECE9;
    border: 1px solid #D9D9D5;
    border-radius: 8px;
    padding: 5px 9px;
    font-size: 8pt;
    font-weight: 650;
}
QLabel#chatTitle { color: #292C2F; font-size: 11pt; font-weight: 650; }
QLabel#chatSubtitle { color: #767A7B; font-size: 8.5pt; }
QFrame#separator { background: #D6D6D2; max-height: 1px; border: none; }
QScrollArea { border: none; background-color: #FFFEFB; }
QScrollArea#todayScroll { background-color: transparent; border: none; }
QScrollArea#helpDetailScroll { background-color: #FFFEFB; }
QScrollArea#goalDetailScroll { background-color: #F8F7F3; }
QWidget#todayScrollContent { background-color: transparent; }
QWidget#helpDetailContent { background-color: #FFFEFB; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 4px 1px; }
QScrollBar::handle:vertical { background: #D9D9D1; min-height: 28px; border-radius: 4px; }
QScrollBar::handle:vertical:hover { background: #BEC8C0; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QWidget#chatContent { background: transparent; }
QLabel#emptyTitle { color: #292C2F; font-family: 'Georgia'; font-size: 22pt; font-weight: 500; }
QLabel#emptySub { color: #666B6D; font-size: 10pt; }
QLabel#privacyNote { color: #777B7D; font-size: 8pt; }
QFrame#userBubble {
    background: #EEEEEC;
    border: 1px solid #DDDDDA;
    border-radius: 14px;
}
QFrame#assistantBubble {
    background: #F4F4F2;
    border: 1px solid #E1E1DE;
    border-radius: 14px;
}
QLabel#messageName { color: #65696A; font-size: 8pt; font-weight: 650; }
QLabel#messageText { color: #2D3032; font-size: 10pt; }
QPlainTextEdit#composer {
    background: #FEFEFD;
    border: 1px solid #D9D9D5;
    border-radius: 12px;
    padding: 12px;
    color: #292C2F;
    selection-background-color: #D9D9D6;
}
QPlainTextEdit#composer:focus { border: 1px solid #818587; }
QPushButton {
    background: #FEFEFD;
    color: #373B3D;
    border: 1px solid #D7D7D3;
    border-radius: 8px;
    padding: 6px 10px;
    min-height: 19px;
}
QPushButton:hover { background: #EEEEEC; border-color: #AEB0AE; }
QPushButton:focus { border: 1px solid #74797B; }
QPushButton:pressed { background: #E1E1DE; }
QPushButton:disabled { color: #999C9D; background: #F4F4F2; border-color: #E5E5E2; }
QPushButton#navButton {
    background: transparent;
    color: #5F6466;
    border: 1px solid transparent;
    padding: 7px 10px;
    font-weight: 550;
}
QPushButton#navButton:hover { background: #E5E5E2; color: #292C2F; }
QPushButton#navButton:checked {
    background: #DADAD7;
    border-color: #C8C9C6;
    color: #232628;
    font-weight: 700;
}
QPushButton#utilityButton { background: transparent; border: 1px solid transparent; color: #5D6264; }
QPushButton#utilityButton:hover { background: #FEFEFD; border-color: #D4D4D0; }
QPushButton#helpButton {
    background: #F0F0EE;
    color: #44494B;
    border: 1px solid #D6D6D2;
    border-radius: 14px;
    padding: 0;
    min-width: 27px;
    max-width: 27px;
    min-height: 27px;
    max-height: 27px;
    font-family: 'Georgia';
    font-size: 11pt;
    font-weight: 700;
}
QPushButton#helpButton:hover { background: #E5E5E2; border-color: #BEBFBC; color: #25282B; }
QPushButton#primaryAction, QPushButton#sendButton {
    background: #292C2F;
    color: #FFFFFF;
    border: 1px solid #292C2F;
    font-weight: 650;
}
QPushButton#primaryAction:hover, QPushButton#sendButton:hover { background: #111315; border-color: #111315; }
QPushButton#secondaryAction { background: #FEFEFD; color: #373B3D; }
QPushButton#quietAction { background: transparent; border-color: transparent; color: #656A6C; }
QPushButton#warningAction { color: #55595B; background: #F3F3F1; border-color: #D9D9D5; }
QPushButton#hideButton { background: transparent; border: none; color: #777B7D; font-size: 15pt; padding: 2px 7px; }
QPushButton#hideButton:hover { background: #E5E5E2; color: #35393B; }
QPushButton#suggestion {
    background: #FEFEFD;
    border: 1px solid #DFDFDB;
    border-radius: 10px;
    color: #3E4244;
    padding: 10px 12px;
    text-align: left;
}
QPushButton#suggestion:hover { background: #EEEEEC; border-color: #C6C7C4; }
QPushButton#micButton[recording="true"] { background: #E1E1DE; border-color: #AEB0AE; color: #292C2F; }
QLabel#composerHint { color: #777B7D; font-size: 8pt; }
QMenu, QDialog { background: #FEFEFD; color: #292C2F; border: 1px solid #D7D7D3; }
QListWidget {
    background: #FEFEFD;
    color: #303436;
    border: 1px solid #DCDCD8;
    border-radius: 9px;
    padding: 4px;
}
QListWidget::item { padding: 8px 9px; border-radius: 6px; }
QListWidget::item:hover { background: #EEEEEC; }
QListWidget::item:selected { background: #DFDFDC; color: #24282A; }
"""


PRODUCT_STYLE = """
QWidget { color: #2B2F32; font-family: 'Segoe UI'; font-size: 10pt; }
QDialog { background: #F5F5F3; color: #2B2F32; font-family: 'Segoe UI'; font-size: 10pt; }
QLabel { color: #35393B; background: transparent; }
QLabel#pageTitle { color: #222629; font-family: 'Consolas'; font-size: 18pt; font-weight: 600; }
QLabel#pageTitle[dashboardHeading="true"] { font-size: 13pt; }
QLabel#pageDescription { color: #686D70; font-size: 9pt; }
QLabel#sectionTitle { color: #292D30; font-family: 'Consolas'; font-size: 9.5pt; font-weight: 700; }
QLabel#sectionKicker { color: #686D70; font-family: 'Consolas'; font-size: 8pt; font-weight: 700; letter-spacing: 1px; }
QLabel#metaText { color: #696E70; font-size: 9pt; }
QLabel#footnote { color: #777B7D; font-size: 8pt; }
QFrame#contentCard {
    background: #FEFEFD;
    border: 1px dotted #CACBC8;
    border-radius: 10px;
}
QFrame#helpCard { background: #F3F3F1; border: 1px dotted #D1D1CE; border-radius: 8px; }
QListWidget#milestoneList { background: #F2F2F0; border-color: #D9D9D5; }
QListWidget#activityHistoryList { background: #EFEFED; border-color: #D6D6D2; }
QListWidget#goalHistoryList { background: #F5F5F3; border-color: #DEDEDA; }
QListWidget#milestoneList::item, QListWidget#activityHistoryList::item, QListWidget#goalHistoryList::item {
    padding: 5px 8px;
    border-radius: 5px;
}
QLineEdit, QPlainTextEdit, QComboBox, QDateEdit, QDateTimeEdit, QSpinBox, QListWidget {
    background: #FEFEFD;
    color: #2D3032;
    border: 1px solid #D7D7D3;
    border-radius: 8px;
    padding: 7px 8px;
    selection-background-color: #DADAD7;
}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QDateEdit:focus, QDateTimeEdit:focus, QSpinBox:focus {
    border: 1px solid #777C7E;
}
QPlainTextEdit#readPanel { background: #F4F4F2; border-color: #DEDEDA; }
QProgressBar#milestoneProgress {
    background: #E4E4E1;
    border: none;
    border-radius: 4px;
}
QProgressBar#milestoneProgress::chunk { background: #555A5D; border-radius: 4px; }
QListWidget#priorityList, QListWidget#todayActivityList { border: none; background: transparent; }
QListWidget#priorityList::item {
    background: #F4F4F2;
    border: 1px dotted #DDDDDA;
    border-radius: 8px;
    padding: 3px 6px;
}
QListWidget#todayActivityList::item { padding: 2px 5px; border-bottom: 1px dotted #E0E0DD; }
QPushButton#goalPreview {
    text-align: left;
    background: #F4F4F2;
    border: 1px dotted #D8D8D5;
    border-radius: 9px;
    padding: 4px 6px;
    min-height: 30px;
    color: #34383A;
}
QPushButton#goalPreview:hover { background: #EAEAE8; border-color: #BABBB8; }
QLabel#heatmapWeekday { color: #777B7D; font-size: 8pt; }
QToolButton#heatmapDay {
    background: #EAEAE8;
    border: 1px solid transparent;
    border-radius: 6px;
    color: #4A4E50;
    min-width: 15px;
    min-height: 13px;
    padding: 0;
    font-size: 7.5pt;
}
QToolButton#heatmapDay[intensity="1"] { background: #D8D8D5; }
QToolButton#heatmapDay[intensity="2"] { background: #BEBFBC; }
QToolButton#heatmapDay[intensity="3"] { background: #999C9D; color: #FFFFFF; }
QToolButton#heatmapDay[intensity="4"] { background: #5D6264; color: white; }
QToolButton#heatmapDay[today="true"] { border: 1px solid #303436; font-weight: 700; }
QToolButton#heatmapDay:hover { background: #DFDFDC; border-color: #B5B6B3; }
QListWidget { padding: 5px; }
QListWidget::item { padding: 9px 10px; border-radius: 6px; }
QListWidget::item:hover { background: #F2F4EF; }
QListWidget::item:selected { background: #E7EEE7; color: #2F5846; }
QPushButton {
    background: #FEFEFD;
    color: #383C3E;
    border: 1px solid #D7D7D3;
    border-radius: 8px;
    padding: 7px 10px;
    min-height: 20px;
}
QPushButton:hover { background: #EEEEEC; border-color: #AEB0AE; }
QPushButton:focus { border: 1px solid #777C7E; }
QPushButton:disabled { color: #999C9D; background: #F4F4F2; border-color: #E4E4E1; }
QPushButton#primaryAction { background: #292C2F; color: white; border-color: #292C2F; font-weight: 650; }
QPushButton#primaryAction:hover { background: #111315; border-color: #111315; }
QPushButton#secondaryAction { background: #FEFEFD; color: #383C3E; }
QPushButton#quietAction { background: transparent; border-color: transparent; color: #656A6C; }
QPushButton#warningAction { color: #55595B; background: #F3F3F1; border-color: #D8D8D4; }
QTabWidget::pane { background: #FEFEFD; border: 1px dotted #CACBC8; border-radius: 9px; top: -1px; }
QTabBar::tab { background: transparent; color: #707577; padding: 8px 11px; margin-right: 3px; border: none; }
QTabBar::tab:selected { color: #282C2E; font-weight: 700; border-bottom: 2px solid #6B7072; }
QTabBar::tab:hover { color: #292D30; background: #EAEAE8; }
QCheckBox { color: #505557; spacing: 7px; }
QCalendarWidget QWidget { alternate-background-color: #F0F0EE; color: #34383A; }
QCalendarWidget QToolButton { background: #F1F1EF; color: #303436; border: 1px solid #DDDDDA; border-radius: 5px; padding: 3px 6px; }
QCalendarWidget QToolButton:hover { background: #E4E4E1; }
QCalendarWidget QMenu { background: #FEFEFD; color: #303436; }
QCalendarWidget QSpinBox { background: #FEFEFD; color: #303436; }
QCalendarWidget QAbstractItemView { background: #FEFEFD; color: #34383A; selection-background-color: #E0E0DD; selection-color: #292C2F; outline: 0; }
QCalendarWidget QAbstractItemView:disabled { color: #A4A6A5; }
QFrame#gardenCard QLabel#sectionTitle { color: #35583B; }
QLabel#gardenGoal { color: #243C2A; font-family: 'Segoe UI'; font-size: 8.5pt; font-weight: 600; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 4px 1px; }
QScrollBar::handle:vertical { background: #D9D9D1; min-height: 28px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


TODAY_STYLE = APP_STYLE + "\n" + PRODUCT_STYLE
