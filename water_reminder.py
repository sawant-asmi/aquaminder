#!/usr/bin/env python3
"""
Water Reminder — a cute desktop buddy that reminds you to drink water.

She appears every 30 minutes. Click "I drank water" and she says "Good job!",
or click "Remind later" and she'll come back in 10 minutes.

Run with:  venv/bin/python water_reminder.py
Quit with the ✕ button on the popup, or Ctrl+C in the terminal.
"""

import json
import os
import random
import signal
import sys
from datetime import date, datetime, timedelta

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
STATE_FILE = os.path.expanduser("~/.aquaminder.json")   # counter + position

REMINDER_MSGS = [
    "Time to drink water! 💧",
    "Hydration check! 💦",
    "Water break? You've earned it 🥤",
    "Your plants get water. Why not you? 🌱",
    "Psst… water time! 🫧",
    "A sip for me? 🥺💧",
    "Glug glug o'clock! ⏰💦",
]
GOODJOB_MSGS = [
    "Good job! 🎉 Stay hydrated!",
    "Yay! 🥳 Keep it up!",
    "Proud of you! 💖",
    "Hydration hero! 🦸‍♀️💧",
    "Cheers! 🥂 See you soon!",
]


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(**updates):
    d = load_state()
    d.update(updates)
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(d, f)
    except OSError:
        pass


def glasses_today():
    d = load_state()
    if d.get("date") == date.today().isoformat():
        return int(d.get("glasses", 0))
    return 0


def save_glasses(n):
    save_state(date=date.today().isoformat(), glasses=n)

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
QLabel#panelBig {
    color: #ffffff;
    font-size: 16px;
    font-weight: 700;
    background: transparent;
}
QLabel#panelSmall {
    color: rgba(255, 255, 255, 215);
    font-size: 13px;
    font-weight: 600;
    background: transparent;
}
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

        # ------------------ hidden panel (double-click her to open) ---------
        self.panel = Bubble()
        panel_col = QVBoxLayout(self.panel)
        panel_col.setContentsMargins(26, TAIL_H + 14, 26, 18)
        panel_col.setSpacing(9)

        self.panel_count = QLabel("")
        self.panel_count.setObjectName("panelBig")
        self.panel_count.setAlignment(Qt.AlignCenter)

        self.panel_next = QLabel("")
        self.panel_next.setObjectName("panelSmall")
        self.panel_next.setAlignment(Qt.AlignCenter)

        btn_pause = PillButton("⏸ Pause 1 hour", "snooze")
        btn_pause.clicked.connect(self.on_pause)
        btn_quit = PillButton("👋 Quit AquaMinder", "snooze")
        btn_quit.clicked.connect(QApplication.quit)

        panel_col.addWidget(self.panel_count)
        panel_col.addWidget(self.panel_next)
        panel_col.addWidget(btn_pause, alignment=Qt.AlignCenter)
        panel_col.addWidget(btn_quit, alignment=Qt.AlignCenter)
        self.panel.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 10)
        layout.setSpacing(0)
        layout.addWidget(self.char_label)
        layout.addWidget(self.bubble)
        layout.addWidget(self.btn_row)
        layout.addWidget(self.panel)
        self.setStyleSheet(STYLE)
        self.awaiting = False          # a reminder is on screen, unanswered
        self._drag_start = None
        self._dragged = False

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
        st = load_state()
        if "pos_fx" in st:
            # she was dragged: use the remembered spot (stored as a fraction
            # of the screen, so it maps onto any monitor)
            x = geo.x() + int(st["pos_fx"] * max(geo.width() - self.width(), 1))
            y = geo.y() + int(st["pos_fy"] * max(geo.height() - self.height(), 1))
        else:
            x = geo.right() - self.width() - 30     # bottom-right corner
            y = geo.bottom() - self.height() - 30
        self.move(x, y)

    # ------------------ dragging ------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start = event.globalPosition().toPoint()
            self._drag_window_pos = self.pos()
            self._dragged = False

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton and self._drag_start is not None:
            delta = event.globalPosition().toPoint() - self._drag_start
            if self._dragged or delta.manhattanLength() > 6:
                self._dragged = True
                self.move(self._drag_window_pos + delta)

    def mouseReleaseEvent(self, event):
        if self._dragged:
            screen = self.screen() or QApplication.primaryScreen()
            geo = screen.availableGeometry()
            fx = (self.x() - geo.x()) / max(geo.width() - self.width(), 1)
            fy = (self.y() - geo.y()) / max(geo.height() - self.height(), 1)
            save_state(pos_fx=min(max(fx, 0.0), 1.0),
                       pos_fy=min(max(fy, 0.0), 1.0))
        self._drag_start = None
        self._dragged = False

    def animate(self):
        self.char_label.setPixmap(self.frames[self.frame_index])
        self.frame_index = (self.frame_index + 1) % len(self.frames)

    # ------------------ reminder flow ------------------
    def show_reminder(self):
        self.hide_timer.stop()
        self.awaiting = True
        self.panel.hide()
        self.msg.setText(random.choice(REMINDER_MSGS))
        self.bubble.show()
        self.btn_row.show()
        self.place_window()
        self.show()
        self.raise_()
        QApplication.beep()

    def on_drank(self):
        self.awaiting = False
        n = glasses_today() + 1
        save_glasses(n)
        self.msg.setText(random.choice(GOODJOB_MSGS))
        self.btn_row.hide()
        self.place_window()
        self.hide_timer.start(GOOD_JOB_SECONDS * 1000)
        self.schedule(REMINDER_INTERVAL_MIN)

    def on_snooze(self):
        self.awaiting = False
        self.hide()
        self.schedule(SNOOZE_MIN)

    # ------------------ hidden panel (double-click) ------------------
    def mouseDoubleClickEvent(self, event):
        self.toggle_panel()

    def toggle_panel(self):
        if self.panel.isVisible():
            self.panel.hide()
            self.bubble.show()
            self.btn_row.show()
            if self.awaiting:
                self.place_window()
            else:
                self.hide()            # nothing pending -> she slips away
            return
        self.hide_timer.stop()
        n = glasses_today()
        self.panel_count.setText(
            f"💧 {n} glass{'es' if n != 1 else ''} today")
        if self.awaiting:
            self.panel_next.setText("She's waiting for you 💧")
        elif self.next_due:
            self.panel_next.setText(
                "Next reminder: " + self.next_due.strftime("%-I:%M %p"))
        else:
            self.panel_next.setText("")
        self.bubble.hide()
        self.btn_row.hide()
        self.panel.show()
        self.place_window()

    def on_pause(self):
        self.awaiting = False
        self.next_due = self.clamp_to_active_hours(
            datetime.now() + timedelta(hours=1))
        self.panel.hide()
        self.bubble.show()
        self.btn_row.show()
        self.hide()


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
