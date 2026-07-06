#!/usr/bin/env python3
"""
Water Reminder — a cute desktop buddy that reminds you to drink water.

She appears every 30 minutes. Click "I drank water" and she says "Good job!",
or click "Remind later" and she'll come back in 10 minutes.

Run with:  venv/bin/python water_reminder.py
Quit with the ✕ button on the popup, or Ctrl+C in the terminal.
"""

import os
import signal
import sys
from datetime import datetime, timedelta

from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import (
    QColor, QCursor, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
    QPushButton, QVBoxLayout, QWidget,
)

# ----------------------------- settings ------------------------------------
REMINDER_INTERVAL_MIN = 30      # main reminder interval (minutes)
SNOOZE_MIN = 10                 # snooze interval (minutes)
FRAME_DELAY_MS = 100            # animation speed (10 fps)
GOOD_JOB_SECONDS = 3            # how long "Good job!" stays on screen
SHOW_ON_LAUNCH = True           # show once immediately so you can test it
ACTIVE_START_HOUR = 8           # no reminders before 8:00
ACTIVE_END_HOUR = 23            # no reminders at/after 23:00
CHECK_EVERY_MS = 5000           # how often to compare the clock to the deadline

HERE = os.path.dirname(os.path.abspath(__file__))
FRAMES_DIR = os.path.join(HERE, "character")

BUBBLE_BG = QColor("#ff8082")           # her shoe pink
TEXT_ON_BUBBLE = "#ffffff"

STYLE = """
QLabel#message {
    color: #ffffff;
    font-size: 16px;
    font-weight: 700;
    background: transparent;
}
QPushButton#close {
    background: transparent;
    color: rgba(255, 255, 255, 170);
    font-size: 12px;
    padding: 2px 6px;
    border: none;
}
QPushButton#close:hover { color: #ffffff; }
"""

# macOS + translucent windows drop widget background fills, so buttons are
# painted by hand (same approach as the bubble).
PILL_STYLES = {
    "drank": dict(grad=("#ff9fbc", "#f16995"), grad_hover=("#ffb3ca", "#f782a8"),
                  border=QColor("#f07ba2"), text=QColor("#ffffff"), bold=True),
    "snooze": dict(grad=("#ff9fbc", "#f16995"), grad_hover=("#ffb3ca", "#f782a8"),
                   border=QColor("#f07ba2"), text=QColor("#ffffff"), bold=False),
}


class PillButton(QPushButton):
    def __init__(self, text, kind):
        super().__init__(text)
        self.kind = kind
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)
        f = QFont(self.font())
        f.setPixelSize(14)
        f.setBold(PILL_STYLES[kind]["bold"])
        self.setFont(f)
        fm = self.fontMetrics()
        self.setFixedSize(fm.horizontalAdvance(text) + 44, 36)

    def paintEvent(self, event):
        s = PILL_STYLES[self.kind]
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(r, r.height() / 2, r.height() / 2)
        hov = self.underMouse()
        if "grad" in s:
            c0, c1 = s["grad_hover"] if hov else s["grad"]
            grad = QLinearGradient(r.topLeft(), r.bottomRight())
            grad.setColorAt(0, QColor(c0))
            grad.setColorAt(1, QColor(c1))
            p.fillPath(path, grad)
        else:
            p.fillPath(path, s["fill_hover"] if hov else s["fill"])
        p.setPen(QPen(s["border"], 1.5))
        p.drawPath(path)
        p.setPen(s["text"])
        p.setFont(self.font())
        p.drawText(self.rect(), Qt.AlignCenter, self.text())

TAIL_W = 22                     # speech-bubble pointer size
TAIL_H = 11
RADIUS = 16


class Bubble(QWidget):
    """White rounded speech bubble with a pointer on top (toward the buddy)."""

    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WA_TranslucentBackground)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 5)
        shadow.setColor(QColor(200, 90, 130, 55))
        self.setGraphicsEffect(shadow)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        path = QPainterPath()
        path.addRoundedRect(0, TAIL_H, w, h - TAIL_H, RADIUS, RADIUS)
        # pointer triangle, centered
        cx = w / 2
        path.moveTo(cx - TAIL_W / 2, TAIL_H + 1)
        path.lineTo(cx, 0)
        path.lineTo(cx + TAIL_W / 2, TAIL_H + 1)
        path.closeSubpath()
        p.fillPath(path.simplified(), BUBBLE_BG)


class WaterReminder(QWidget):
    def __init__(self):
        super().__init__()
        # Frameless, always on top, no Dock entry, truly transparent background
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # macOS: tool windows normally hide while the app is inactive, and as a
        # background (accessory) app we are almost always "inactive" — without
        # this she would never reappear
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)
        # pop up without stealing focus from whatever the user is doing
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        # ------------------ load animation frames ------------------
        self.frames = []
        try:
            names = sorted(f for f in os.listdir(FRAMES_DIR)
                           if f.endswith((".gif", ".png")))
            self.frames = [QPixmap(os.path.join(FRAMES_DIR, n)) for n in names]
        except OSError as e:
            print("Could not load character frames from", FRAMES_DIR, "-", e)
            sys.exit(1)
        if not self.frames:
            print("No frames found in", FRAMES_DIR)
            sys.exit(1)
        self.frame_index = 0

        # ------------------ character ------------------
        self.char_label = QLabel()
        self.char_label.setPixmap(self.frames[0])
        self.char_label.setAlignment(Qt.AlignCenter)
        self.char_label.setStyleSheet("background: transparent;")

        # ------------------ speech bubble ------------------
        self.bubble = Bubble()

        self.msg = QLabel("")
        self.msg.setObjectName("message")
        self.msg.setAlignment(Qt.AlignCenter)

        btn_close = QPushButton("✕")
        btn_close.setObjectName("close")
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.clicked.connect(QApplication.quit)

        bubble_row = QHBoxLayout(self.bubble)
        bubble_row.setContentsMargins(22, TAIL_H + 10, 8, 12)
        bubble_row.addStretch()
        bubble_row.addWidget(self.msg)
        bubble_row.addStretch()
        bubble_row.addWidget(btn_close, alignment=Qt.AlignTop)

        # ------------------ buttons ------------------
        self.btn_drank = PillButton("💧 I drank water!", "drank")
        self.btn_drank.clicked.connect(self.on_drank)

        self.btn_snooze = PillButton("⏰ Remind later", "snooze")
        self.btn_snooze.clicked.connect(self.on_snooze)

        self.btn_row = QWidget()
        self.btn_row.setStyleSheet("background: transparent;")
        btn_layout = QHBoxLayout(self.btn_row)
        btn_layout.setContentsMargins(0, 10, 0, 4)
        btn_layout.setSpacing(10)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_drank)
        btn_layout.addWidget(self.btn_snooze)
        btn_layout.addStretch()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 10)
        layout.setSpacing(0)
        layout.addWidget(self.char_label)
        layout.addWidget(self.bubble)
        layout.addWidget(self.btn_row)
        self.setStyleSheet(STYLE)

        # ------------------ timers ------------------
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self.animate)
        self.anim_timer.start(FRAME_DELAY_MS)

        # Wall-clock scheduling: QTimers pause while the Mac sleeps, so instead
        # of one long timer we store a deadline and compare it to the real
        # clock every few seconds. After waking from sleep, an overdue
        # reminder fires within seconds instead of being pushed back.
        self.next_due = None
        self.check_timer = QTimer(self)
        self.check_timer.timeout.connect(self.check_due)
        self.check_timer.start(CHECK_EVERY_MS)

        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide)

        if SHOW_ON_LAUNCH:
            QTimer.singleShot(500, self.show_reminder)
        else:
            self.schedule(REMINDER_INTERVAL_MIN)

    # ------------------ helpers ------------------
    def schedule(self, minutes):
        self.next_due = self.clamp_to_active_hours(
            datetime.now() + timedelta(minutes=minutes))

    @staticmethod
    def clamp_to_active_hours(due):
        """Push a deadline that falls outside 8:00-23:00 to the next 8:00."""
        if due.hour >= ACTIVE_END_HOUR:
            due = due + timedelta(days=1)
        elif due.hour >= ACTIVE_START_HOUR:
            return due
        return due.replace(hour=ACTIVE_START_HOUR, minute=0,
                           second=0, microsecond=0)

    def check_due(self):
        if self.next_due is None or datetime.now() < self.next_due:
            return
        # woke up outside active hours with an overdue reminder -> defer to 8:00
        clamped = self.clamp_to_active_hours(datetime.now())
        if clamped > datetime.now():
            self.next_due = clamped
            return
        self.next_due = None
        self.show_reminder()

    def place_window(self):
        self.adjustSize()
        # appear on whichever screen the mouse is on (laptop or extended)
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = geo.right() - self.width() - 30         # bottom-right corner
        y = geo.bottom() - self.height() - 30
        self.move(x, y)

    def animate(self):
        self.char_label.setPixmap(self.frames[self.frame_index])
        self.frame_index = (self.frame_index + 1) % len(self.frames)

    # ------------------ reminder flow ------------------
    def show_reminder(self):
        self.hide_timer.stop()
        self.msg.setText("Time to drink water! 💧")
        self.btn_row.show()
        self.place_window()
        self.show()
        self.raise_()
        QApplication.beep()

    def on_drank(self):
        self.msg.setText("Good job! 🎉 Stay hydrated!")
        self.btn_row.hide()
        self.place_window()
        self.hide_timer.start(GOOD_JOB_SECONDS * 1000)
        self.schedule(REMINDER_INTERVAL_MIN)

    def on_snooze(self):
        self.hide()
        self.schedule(SNOOZE_MIN)


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)   # hidden window ≠ quit
    # hide the Python rocket from the Dock — run as a background (accessory) app
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory)
    except ImportError:
        pass                               # pyobjc not installed → Dock icon stays
    signal.signal(signal.SIGINT, lambda *a: app.quit())   # Ctrl+C works
    # let the interpreter process signals while Qt's event loop runs
    tick = QTimer()
    tick.start(200)
    tick.timeout.connect(lambda: None)
    reminder = WaterReminder()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
