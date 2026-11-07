from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from .config import WAKE_WORD_ENABLED, prepare_app_directories
from .ui import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Amigo")
    app.setOrganizationName("Amigo")
    app.setQuitOnLastWindowClosed(False)
    try:
        # Prepare local roots before any UI or workspace action can use them.
        prepare_app_directories()
        window = MainWindow()
    except (OSError, RuntimeError) as exc:
        QMessageBox.critical(None, "Amigo could not start", str(exc))
        return 1

    # When the tray is available, boot as a background companion. The wake word
    # opens the main window; the tray remains the fallback way to show or quit Amigo.
    if QSystemTrayIcon.isSystemTrayAvailable() and WAKE_WORD_ENABLED:
        window.start_in_background()
    else:
        window.show_and_focus()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
