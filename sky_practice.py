#!/usr/bin/env python3
"""
Sky Practice — a transparent, always-on-top practice overlay for Sky: Music sheets.

Load a .skysheet / .json sheet, pick a song, and the app lights the keys you need
to press on the 3x5 Sky instrument. It listens to your keyboard, waits until you
play the current combination, then advances — always previewing the next one so
you can stay ahead of the melody.

Settings (keybinds + loaded songs) persist between launches. Only one instance can
run at a time — launching again brings the existing window to the front.

Cross-platform (Windows / macOS / Linux). Build a standalone binary with PyInstaller.
"""

import sys
import json
import os
import threading
import ctypes
from ctypes import wintypes

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QFileDialog, QSlider, QSizePolicy,
)
from PyQt6.QtGui import (
    QPainter, QColor, QFont, QPainterPath, QBrush, QPen,
    QLinearGradient, QKeyEvent, QMouseEvent, QKeySequence,
)
from PyQt6.QtCore import (
    Qt, QRectF, QPointF, QTimer, QStandardPaths, QLockFile, QDir,
    pyqtSignal, QObject,
)
from PyQt6.QtNetwork import QLocalServer, QLocalSocket


# ----------------------------------------------------------------------------
# Palette
# ----------------------------------------------------------------------------
COL_BG_TOP     = QColor(58, 64, 74)      # #3A404A  window background (top)
COL_BG_BOT     = QColor(44, 49, 58)      # #2C313A  window background (bottom)
COL_KEY_IDLE   = QColor(74, 82, 96)      # slate pad
COL_KEY_IDLE_2 = QColor(62, 69, 82)
# Current combo: deep wine / warm red — "play now"
COL_CURRENT    = QColor(198, 66, 78)     # #C6424E
COL_CURRENT_2  = QColor(150, 38, 52)     # #962634
# Next combo: muted neutral gray — visible but calm, no strong color
COL_NEXT       = QColor(126, 134, 146)   # #7E8692 soft gray
COL_NEXT_2     = QColor(100, 108, 120)   # #646C78
COL_DONE       = QColor(84, 92, 104)
COL_ICON       = QColor(122, 178, 168)   # Sky teal-green icon stroke
COL_TEXT       = QColor(238, 240, 244)
COL_ACCENT     = QColor(198, 66, 78)

# Fixed note names per pad (Sky instrument, never changes).
NOTE_NAMES = ["C", "D", "E", "F", "G",
              "A", "B", "C", "D", "E",
              "F", "G", "A", "B", "C"]
# Icon shape per pad: 'arrow' = diamond w/ side arrows (root C),
# 'diamond' = plain diamond, 'circle' = circle.
ICON_SHAPES = ["arrow", "diamond", "circle", "diamond", "circle",
               "circle", "diamond", "arrow", "diamond", "circle",
               "circle", "diamond", "circle", "diamond", "arrow"]

# Default Sky keybinds (standard 3x5 QWERTY layout used by sky-music).
DEFAULT_KEYBINDS = [
    Qt.Key.Key_Y, Qt.Key.Key_U, Qt.Key.Key_I, Qt.Key.Key_O, Qt.Key.Key_P,
    Qt.Key.Key_H, Qt.Key.Key_J, Qt.Key.Key_K, Qt.Key.Key_L, Qt.Key.Key_Semicolon,
    Qt.Key.Key_N, Qt.Key.Key_M, Qt.Key.Key_Comma, Qt.Key.Key_Period, Qt.Key.Key_Slash,
]

CHORD_WINDOW_MS = 60  # notes within this window group into one combination
APP_KEY = "SkyPractice_singleinstance_v1"  # local server / lock name


def key_to_text(key):
    """Human-readable label for a Qt key code."""
    special = {
        Qt.Key.Key_Semicolon: ";", Qt.Key.Key_Comma: ",", Qt.Key.Key_Period: ".",
        Qt.Key.Key_Slash: "/", Qt.Key.Key_Space: "Space", Qt.Key.Key_Apostrophe: "'",
        Qt.Key.Key_BracketLeft: "[", Qt.Key.Key_BracketRight: "]",
        Qt.Key.Key_Minus: "-", Qt.Key.Key_Equal: "=", Qt.Key.Key_Backslash: "\\",
    }
    if key in special:
        return special[key]
    return QKeySequence(key).toString()


# ----------------------------------------------------------------------------
# Persistent settings (saved to the OS's per-user config folder)
# ----------------------------------------------------------------------------
def settings_path():
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".sky_practice")
    QDir().mkpath(base)
    return os.path.join(base, "settings.json")


def load_settings():
    try:
        with open(settings_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(data):
    try:
        with open(settings_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


# ----------------------------------------------------------------------------
# Sheet parsing
# ----------------------------------------------------------------------------
def parse_sheet(path):
    """
    Parse a Sky .skysheet / genshin-music .json file into:
        { 'name': str, 'combos': [ [idx, ...], ... ], 'path': str }
    A combo is a set of note indices (0-14) to be pressed together.
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    songs = raw if isinstance(raw, list) else [raw]
    if not songs:
        raise ValueError("File contains no songs.")
    song = songs[0]

    name = song.get("name", os.path.basename(path))
    notes = extract_notes(song)
    if not notes:
        raise ValueError("Could not find playable notes in this file.")

    notes.sort(key=lambda n: n[1])
    combos = []
    cur = [notes[0][0]]
    cur_t = notes[0][1]
    for idx, t in notes[1:]:
        if t - cur_t <= CHORD_WINDOW_MS:
            if idx not in cur:
                cur.append(idx)
        else:
            combos.append(sorted(cur))
            cur = [idx]
            cur_t = t
    combos.append(sorted(cur))
    return {"name": name, "combos": combos, "path": path}


def extract_notes(song):
    """Return a list of (noteIndex, time_ms) from any supported song shape."""
    out = []
    if isinstance(song.get("notes"), list) and song["notes"] and isinstance(song["notes"][0], list):
        for n in song["notes"]:
            idx = int(n[0]); t = float(n[1])
            if 0 <= idx <= 14:
                out.append((idx, t))
        return out
    if isinstance(song.get("songNotes"), list):
        for n in song["songNotes"]:
            key = n.get("key", "")
            if "Key" in key:
                try:
                    idx = int(key.split("Key")[-1])
                    if 0 <= idx <= 14:
                        out.append((idx, float(n.get("time", 0))))
                except ValueError:
                    pass
        if out:
            return out
    if isinstance(song.get("columns"), list):
        t = 0.0
        for col in song["columns"]:
            if isinstance(col, list) and len(col) >= 2:
                dur, cnotes = col[0], col[1]
            else:
                dur, cnotes = col.get("len", 1), col.get("notes", [])
            for cn in cnotes:
                idx = cn[0] if isinstance(cn, list) else cn
                if 0 <= int(idx) <= 14:
                    out.append((int(idx), t))
            t += float(dur) * 100
        return out
    return out


# ----------------------------------------------------------------------------
# A single instrument key
# ----------------------------------------------------------------------------
class SkyKey:
    IDLE, CURRENT, NEXT, DONE = range(4)

    def __init__(self, index, keybind):
        self.index = index
        self.keybind = keybind          # Qt key code (int), or None if unbound
        self.note = NOTE_NAMES[index]
        self.shape = ICON_SHAPES[index]
        self.state = self.IDLE
        self.rect = QRectF()


# ----------------------------------------------------------------------------
# The instrument canvas — always fully opaque, never affected by the opacity slider
# ----------------------------------------------------------------------------
class Instrument(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.keys = [SkyKey(i, DEFAULT_KEYBINDS[i]) for i in range(15)]
        self.remap_target = None
        self.pressed_flash = {}
        self.setMinimumSize(360, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._pulse = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    def _tick(self):
        import math
        self._pulse = (self._pulse + 0.06) % (2 * math.pi)
        for i in list(self.pressed_flash.keys()):
            self.pressed_flash[i] -= 1
            if self.pressed_flash[i] <= 0:
                del self.pressed_flash[i]
        self.update()

    def set_states(self, current_set, next_set):
        for k in self.keys:
            if k.index in current_set:
                k.state = SkyKey.CURRENT
            elif k.index in next_set:
                k.state = SkyKey.NEXT
            else:
                k.state = SkyKey.IDLE
        self.update()

    def clear_states(self):
        for k in self.keys:
            k.state = SkyKey.IDLE
        self.update()

    def flash(self, index):
        self.pressed_flash[index] = 8
        self.update()

    # -- layout -------------------------------------------------------------
    def _layout_keys(self):
        w, h = self.width(), self.height()
        cols, rows = 5, 3
        pad = min(w, h) * 0.06
        gap = min(w, h) * 0.045
        gw = (w - 2 * pad - (cols - 1) * gap) / cols
        gh = (h - 2 * pad - (rows - 1) * gap) / rows
        size = min(gw, gh)
        grid_w = cols * size + (cols - 1) * gap
        grid_h = rows * size + (rows - 1) * gap
        ox = (w - grid_w) / 2
        oy = (h - grid_h) / 2
        for i, k in enumerate(self.keys):
            r, c = divmod(i, cols)
            k.rect = QRectF(ox + c * (size + gap), oy + r * (size + gap), size, size)

    # -- icon drawing -------------------------------------------------------
    def _draw_icon(self, p, rect, shape, color, width):
        import math
        cx, cy = rect.center().x(), rect.center().y()
        s = rect.width() * 0.5
        pen = QPen(color, width)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)

        if shape == "circle":
            r = s * 0.86
            p.drawEllipse(QPointF(cx, cy), r, r)
            return

        d = s * 0.98
        dia = QPainterPath()
        dia.moveTo(cx, cy - d); dia.lineTo(cx + d, cy)
        dia.lineTo(cx, cy + d); dia.lineTo(cx - d, cy)
        dia.closeSubpath()
        p.drawPath(dia)

        if shape == "arrow":
            a = s * 0.26
            gap = d * 1.02
            for ang in (0, 90, 180, 270):
                rad = math.radians(ang)
                vx = cx + gap * math.cos(rad)
                vy = cy - gap * math.sin(rad)
                px, py = -math.sin(rad), -math.cos(rad)
                tip = QPointF(vx + a * math.cos(rad), vy - a * math.sin(rad))
                b1 = QPointF(vx + a * px, vy + a * py)
                b2 = QPointF(vx - a * px, vy - a * py)
                tri = QPainterPath()
                tri.moveTo(tip); tri.lineTo(b1); tri.lineTo(b2); tri.closeSubpath()
                p.drawPath(tri)

    # -- painting -----------------------------------------------------------
    def paintEvent(self, _):
        import math
        self._layout_keys()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pulse = 0.5 + 0.5 * math.sin(self._pulse)

        for k in self.keys:
            r = k.rect
            radius = r.width() * 0.26
            path = QPainterPath()
            path.addRoundedRect(r, radius, radius)

            if k.state == SkyKey.CURRENT:
                c1, c2 = COL_CURRENT, COL_CURRENT_2
            elif k.state == SkyKey.NEXT:
                c1, c2 = COL_NEXT, COL_NEXT_2
            elif k.state == SkyKey.DONE:
                c1 = c2 = COL_DONE
            else:
                c1, c2 = COL_KEY_IDLE, COL_KEY_IDLE_2

            grad = QLinearGradient(r.topLeft(), r.bottomRight())
            grad.setColorAt(0, c1); grad.setColorAt(1, c2)
            p.fillPath(path, QBrush(grad))

            if k.state == SkyKey.CURRENT:
                glow = QColor(COL_CURRENT); glow.setAlpha(int(90 + 120 * pulse))
                p.setPen(QPen(glow, 3.5 + 2.5 * pulse))
                p.drawPath(path)
            elif k.state == SkyKey.NEXT:
                ring = QColor(170, 178, 190); ring.setAlpha(150)
                p.setPen(QPen(ring, 2.0)); p.drawPath(path)
            else:
                p.setPen(QPen(QColor(255, 255, 255, 22), 1.2)); p.drawPath(path)

            # Sky note icon (teal). Brightens on lit states.
            icon_rect = QRectF(0, 0, r.width() * 0.66, r.height() * 0.66)
            icon_rect.moveCenter(r.center())
            if k.state == SkyKey.CURRENT:
                icol = QColor(255, 228, 224)
            elif k.state == SkyKey.NEXT:
                icol = QColor(226, 232, 238)
            else:
                icol = QColor(COL_ICON)
            self._draw_icon(p, icon_rect, k.shape, icol, max(2.0, r.width() * 0.045))

            if k.index in self.pressed_flash:
                a = int(180 * (self.pressed_flash[k.index] / 8))
                p.setBrush(QColor(255, 255, 255, a)); p.setPen(Qt.PenStyle.NoPen)
                p.drawPath(path)

            # center label: keybind if bound, else the note name
            if k.index == self.remap_target:
                label = "?"
            elif k.keybind is not None:
                label = key_to_text(k.keybind)
            else:
                label = k.note
            f = QFont(self.font()); f.setPointSizeF(max(10.0, r.width() * 0.30))
            f.setWeight(QFont.Weight.Bold); p.setFont(f)
            if k.state == SkyKey.CURRENT:
                p.setPen(QColor(255, 255, 255))
            elif k.state == SkyKey.NEXT:
                p.setPen(QColor(28, 32, 38))
            else:
                p.setPen(COL_TEXT)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, label)

            if k.index == self.remap_target:
                p.setPen(QPen(COL_ACCENT, 3, Qt.PenStyle.DashLine))
                p.setBrush(Qt.BrushStyle.NoBrush); p.drawPath(path)
        p.end()

    # -- interaction --------------------------------------------------------
    def mousePressEvent(self, e: QMouseEvent):
        for k in self.keys:
            if k.rect.contains(e.position()):
                if e.button() == Qt.MouseButton.RightButton:
                    k.keybind = None
                    self.window().status(f"Note {k.note} unbound. Left-click to set a key.")
                    self.window().persist()
                    self.update()
                    return
                self.remap_target = k.index
                self.setFocus()
                self.update()
                self.window().status(
                    f"Press a keyboard key for note {k.note}…   (Esc cancels · right-click clears)")
                return
        super().mousePressEvent(e)

    def keyPressEvent(self, e: QKeyEvent):
        if self.remap_target is not None:
            k = self.keys[self.remap_target]
            if e.key() == Qt.Key.Key_Escape:
                self.window().status("Binding cancelled.")
            elif e.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
                k.keybind = None
                self.window().status(f"Note {k.note} unbound.")
                self.window().persist()
            else:
                for other in self.keys:
                    if other is not k and other.keybind == e.key():
                        other.keybind = None
                k.keybind = e.key()
                self.window().status(f"Note {k.note} → {key_to_text(e.key())}")
                self.window().persist()
            self.remap_target = None
            self.update()
            return
        self.window().handle_play_key(e.key())


# ----------------------------------------------------------------------------
# Background panel — its alpha is controlled by the opacity slider.
# The instrument sits on top and stays fully opaque.
# ----------------------------------------------------------------------------
class GlassPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.bg_alpha = 255  # 0..255, driven by the opacity slider

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(r, 22, 22)
        top = QColor(COL_BG_TOP); top.setAlpha(self.bg_alpha)
        bot = QColor(COL_BG_BOT); bot.setAlpha(self.bg_alpha)
        grad = QLinearGradient(r.topLeft(), r.bottomLeft())
        grad.setColorAt(0, top); grad.setColorAt(1, bot)
        p.fillPath(path, QBrush(grad))
        edge = QColor(255, 255, 255, int(28 * self.bg_alpha / 255))
        p.setPen(QPen(edge, 1.4)); p.drawPath(path)
        p.end()


def style_button(btn):
    btn.setStyleSheet("""
        QPushButton {
            background: rgba(255,255,255,0.08);
            color: #ECEFF4; border: 1px solid rgba(255,255,255,0.16);
            border-radius: 14px; padding: 8px 16px; font-weight: 500;
        }
        QPushButton:hover { background: rgba(255,255,255,0.16); }
        QPushButton:pressed { background: rgba(255,255,255,0.05); }
    """)


# ----------------------------------------------------------------------------
# Global keyboard listener (works even when the app is NOT focused).
# Native OS hooks on Windows/macOS; Linux falls back to focus-only handling.
# All hook callbacks emit key_pressed(int) — thread-safe via pyqtSignal.
# ----------------------------------------------------------------------------

def _build_mac_keycode_to_qt():
    """Map macOS hardware key codes (HIToolbox kVK_*) to Qt.Key."""
    pairs = [
        (0, Qt.Key.Key_A), (1, Qt.Key.Key_S), (2, Qt.Key.Key_D), (3, Qt.Key.Key_F),
        (4, Qt.Key.Key_H), (5, Qt.Key.Key_G), (6, Qt.Key.Key_Z), (7, Qt.Key.Key_X),
        (8, Qt.Key.Key_C), (9, Qt.Key.Key_V), (11, Qt.Key.Key_B), (12, Qt.Key.Key_Q),
        (13, Qt.Key.Key_W), (14, Qt.Key.Key_E), (15, Qt.Key.Key_R), (16, Qt.Key.Key_Y),
        (17, Qt.Key.Key_T), (18, Qt.Key.Key_1), (19, Qt.Key.Key_2), (20, Qt.Key.Key_3),
        (21, Qt.Key.Key_4), (22, Qt.Key.Key_6), (23, Qt.Key.Key_5), (24, Qt.Key.Key_Equal),
        (25, Qt.Key.Key_9), (26, Qt.Key.Key_7), (27, Qt.Key.Key_Minus), (28, Qt.Key.Key_8),
        (29, Qt.Key.Key_0), (30, Qt.Key.Key_BracketRight), (31, Qt.Key.Key_O),
        (32, Qt.Key.Key_U), (33, Qt.Key.Key_BracketLeft), (34, Qt.Key.Key_I),
        (35, Qt.Key.Key_P), (36, Qt.Key.Key_Return), (37, Qt.Key.Key_L),
        (38, Qt.Key.Key_J), (39, Qt.Key.Key_Apostrophe), (40, Qt.Key.Key_K),
        (41, Qt.Key.Key_Semicolon), (42, Qt.Key.Key_Backslash), (43, Qt.Key.Key_Comma),
        (44, Qt.Key.Key_Slash), (45, Qt.Key.Key_N), (46, Qt.Key.Key_M),
        (47, Qt.Key.Key_Period), (48, Qt.Key.Key_Tab), (49, Qt.Key.Key_Space),
    ]
    return dict(pairs)


_MAC_KEYCODE_TO_QT = _build_mac_keycode_to_qt()

_WIN_VK_TO_QT = {
    0x20: Qt.Key.Key_Space,
    0x0D: Qt.Key.Key_Return,
    0x09: Qt.Key.Key_Tab,
    0xBA: Qt.Key.Key_Semicolon,
    0xBB: Qt.Key.Key_Equal,
    0xBC: Qt.Key.Key_Comma,
    0xBD: Qt.Key.Key_Minus,
    0xBE: Qt.Key.Key_Period,
    0xBF: Qt.Key.Key_Slash,
    0xC0: Qt.Key.Key_QuoteLeft,
    0xDB: Qt.Key.Key_BracketLeft,
    0xDC: Qt.Key.Key_Backslash,
    0xDD: Qt.Key.Key_BracketRight,
    0xDE: Qt.Key.Key_Apostrophe,
}


def _win_vk_to_qt(vk):
    """Convert a Windows virtual-key code to Qt.Key, or None if unmapped."""
    if 0x41 <= vk <= 0x5A:
        return getattr(Qt.Key, f"Key_{chr(vk)}")
    if 0x30 <= vk <= 0x39:
        return getattr(Qt.Key, f"Key_{chr(vk)}")
    return _WIN_VK_TO_QT.get(vk)


def _mac_keycode_to_qt(keycode):
    """Convert a macOS hardware key code to Qt.Key, or None if unmapped."""
    return _MAC_KEYCODE_TO_QT.get(int(keycode))


if sys.platform == "win32":
    _WH_KEYBOARD_LL = 13
    _WM_KEYDOWN = 0x0100
    _WM_SYSKEYDOWN = 0x0104
    _WM_QUIT = 0x0012

    class _KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("vkCode", wintypes.DWORD),
            ("scanCode", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    _LowLevelKeyboardProc = ctypes.WINFUNCTYPE(
        ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
    )


class _WindowsKeyHook:
    """Low-level keyboard hook (WH_KEYBOARD_LL) on a daemon thread with a message loop."""

    def __init__(self, on_key):
        self._on_key = on_key
        self._hook_id = None
        self._thread = None
        self._thread_id = None
        self._proc = None
        self._started = threading.Event()
        self._error = None
        self._user32 = ctypes.windll.user32
        self._kernel32 = ctypes.windll.kernel32

    def start(self):
        self._thread = threading.Thread(target=self._run, name="WinKeyHook", daemon=True)
        self._thread.start()
        if not self._started.wait(timeout=3.0):
            raise RuntimeError("Windows keyboard hook thread did not start in time")
        if self._error is not None:
            raise self._error

    def _run(self):
        self._thread_id = self._kernel32.GetCurrentThreadId()

        @_LowLevelKeyboardProc
        def hook_proc(n_code, w_param, l_param):
            if n_code >= 0 and w_param in (_WM_KEYDOWN, _WM_SYSKEYDOWN):
                kb = ctypes.cast(l_param, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                qt_key = _win_vk_to_qt(kb.vkCode)
                if qt_key is not None:
                    self._on_key(int(qt_key))
            return self._user32.CallNextHookEx(None, n_code, w_param, l_param)

        self._proc = hook_proc
        module = self._kernel32.GetModuleHandleW(None)
        self._hook_id = self._user32.SetWindowsHookExW(
            _WH_KEYBOARD_LL, self._proc, module, 0,
        )
        if not self._hook_id:
            self._error = ctypes.WinError()
            self._started.set()
            return

        self._started.set()
        msg = wintypes.MSG()
        while self._user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            self._user32.TranslateMessage(ctypes.byref(msg))
            self._user32.DispatchMessageW(ctypes.byref(msg))

        if self._hook_id:
            self._user32.UnhookWindowsHookEx(self._hook_id)
            self._hook_id = None

    def stop(self):
        if self._thread_id:
            self._user32.PostThreadMessageW(self._thread_id, _WM_QUIT, 0, 0)
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        self._thread_id = None
        self._hook_id = None
        self._proc = None


class _MacOSKeyTap:
    """Listen-only CGEventTap on a daemon thread with CFRunLoop."""

    def __init__(self, on_key):
        self._on_key = on_key
        self._thread = None
        self._run_loop = None
        self._tap = None
        self._started = threading.Event()
        self._error = None

    def start(self):
        self._thread = threading.Thread(target=self._run, name="MacKeyTap", daemon=True)
        self._thread.start()
        if not self._started.wait(timeout=3.0):
            raise RuntimeError("macOS event tap thread did not start in time")
        if self._error is not None:
            raise self._error

    def _run(self):
        try:
            from Quartz import (
                CGEventTapCreate, CGEventTapEnable,
                CGEventGetIntegerValueField, kCGKeyboardEventKeycode,
                kCGSessionEventTap, kCGHeadInsertEventTap, kCGEventTapOptionListenOnly,
                kCGEventKeyDown, kCGEventTapDisabledByTimeout, kCGEventTapDisabledByUserInput,
                CGEventMaskBit,
            )
            from CoreFoundation import (
                CFRunLoopGetCurrent, CFRunLoopRun, CFRunLoopStop,
                CFMachPortCreateRunLoopSource, kCFRunLoopCommonModes, CFRunLoopAddSource,
            )
        except Exception as exc:
            self._error = exc
            self._started.set()
            return

        def callback(proxy, event_type, event, refcon):
            if event_type in (kCGEventTapDisabledByTimeout, kCGEventTapDisabledByUserInput):
                CGEventTapEnable(self._tap, True)
                return event
            if event_type == kCGEventKeyDown:
                keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
                qt_key = _mac_keycode_to_qt(keycode)
                if qt_key is not None:
                    self._on_key(int(qt_key))
            return event

        mask = CGEventMaskBit(kCGEventKeyDown)
        self._tap = CGEventTapCreate(
            kCGSessionEventTap,
            kCGHeadInsertEventTap,
            kCGEventTapOptionListenOnly,
            mask,
            callback,
            None,
        )
        if self._tap is None:
            self._error = RuntimeError(
                "CGEventTapCreate returned None — grant Accessibility and "
                "Input Monitoring, then restart the app.")
            self._started.set()
            return

        self._run_loop = CFRunLoopGetCurrent()
        source = CFMachPortCreateRunLoopSource(None, self._tap, 0)
        CFRunLoopAddSource(self._run_loop, source, kCFRunLoopCommonModes)
        CGEventTapEnable(self._tap, True)
        self._started.set()
        CFRunLoopRun()

        try:
            CGEventTapEnable(self._tap, False)
        except Exception:
            pass

    def stop(self):
        if self._run_loop is not None:
            try:
                from CoreFoundation import CFRunLoopStop
                CFRunLoopStop(self._run_loop)
            except Exception:
                pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        self._run_loop = None
        self._tap = None


class GlobalKeyListener(QObject):
    """Emits key_pressed(qt_key_code:int) for every physical key press, globally.

    Windows: SetWindowsHookExW(WH_KEYBOARD_LL) — listen-only, does not block input.
    macOS: CGEventTap (kCGEventTapOptionListenOnly) — requires Accessibility +
           Input Monitoring; verified via AXIsProcessTrustedWithOptions.
    Linux: no global capture; keys work only when this window is focused.
    """
    key_pressed = pyqtSignal(int)
    status_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._backend = None
        self.active = False

    def _mac_is_trusted(self, prompt=True):
        """Return True if this process may monitor input on macOS."""
        if sys.platform != "darwin":
            return True
        try:
            from ApplicationServices import (
                AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt,
            )
            options = {kAXTrustedCheckOptionPrompt: bool(prompt)}
            return bool(AXIsProcessTrustedWithOptions(options))
        except Exception:
            try:
                from ApplicationServices import AXIsProcessTrusted
                return bool(AXIsProcessTrusted())
            except Exception:
                return False

    def _emit_key(self, qt_key):
        self.key_pressed.emit(int(qt_key))

    def start(self):
        if sys.platform == "linux":
            self.status_changed.emit(
                "Linux: global capture unavailable — keys work when this window is focused.")
            self.active = False
            return False

        if sys.platform == "darwin":
            if not self._mac_is_trusted(prompt=True):
                self.status_changed.emit(
                    "Grant Accessibility and Input Monitoring in System Settings → "
                    "Privacy & Security, then restart the app. Until then keys work "
                    "only when this window is focused.")
                self.active = False
                return False
            try:
                self._backend = _MacOSKeyTap(self._emit_key)
                self._backend.start()
            except Exception as exc:
                self.active = False
                self.status_changed.emit(
                    f"Couldn't start macOS event tap ({exc}). Enable Accessibility "
                    "and Input Monitoring, then restart the app.")
                self._backend = None
                return False
            self.active = True
            self.status_changed.emit(
                "Global key capture on (CGEventTap) — plays even when unfocused.")
            return True

        if sys.platform == "win32":
            try:
                self._backend = _WindowsKeyHook(self._emit_key)
                self._backend.start()
            except Exception as exc:
                self.active = False
                self.status_changed.emit(
                    f"Couldn't start Windows keyboard hook ({exc}).")
                self._backend = None
                return False
            self.active = True
            self.status_changed.emit(
                "Global key capture on (WH_KEYBOARD_LL) — plays even when unfocused.")
            return True

        self.status_changed.emit(
            "Global capture unavailable on this platform — keys work when focused.")
        self.active = False
        return False

    def stop(self):
        if self._backend is not None:
            try:
                self._backend.stop()
            except Exception:
                pass
            self._backend = None
        self.active = False


# ----------------------------------------------------------------------------
# Main window
# ----------------------------------------------------------------------------
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sky Practice")
        self._pinned = True
        self._apply_window_flags(first=True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # On macOS, keep the window visible even when another app is focused.
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        self.resize(600, 560)

        self.songs = []
        self.combos = []
        self.pos_index = 0
        self.satisfied = set()
        self._drag_pos = None
        self._loading = False  # guard while restoring settings
        # chord simultaneity: multi-key combos must be pressed together within
        # this window (ms). Presses that arrive too slowly get reset.
        self.chord_window_ms = 220
        self._chord_timer = QTimer(self)
        self._chord_timer.setSingleShot(True)
        self._chord_timer.timeout.connect(self._chord_timeout)

        self._build_ui()
        self._center()
        self._restore_settings()

        # global keyboard listener — captures keys even when unfocused.
        # Deferred so the window paints immediately; native hook init happens
        # after the UI is up.
        self.global_keys = GlobalKeyListener()
        self.global_keys.key_pressed.connect(self.handle_play_key)
        self.global_keys.status_changed.connect(self.status)
        QTimer.singleShot(0, self.global_keys.start)

    # -- UI ----------------------------------------------------------------
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.panel = GlassPanel()
        outer.addWidget(self.panel)
        root = QVBoxLayout(self.panel)
        root.setContentsMargins(20, 16, 20, 18)
        root.setSpacing(12)

        # title bar
        bar = QHBoxLayout()
        title = QLabel("Sky Practice")
        tf = QFont(self.font()); tf.setPointSize(15); tf.setWeight(QFont.Weight.Bold)
        title.setFont(tf); title.setStyleSheet("color:#C6424E;")
        sub = QLabel("practice overlay"); sub.setStyleSheet("color:#96A0AC;")
        sf = QFont(self.font()); sf.setPointSize(9); sub.setFont(sf)
        bar.addWidget(title); bar.addWidget(sub); bar.addStretch()

        self.pin_btn = QPushButton("📌")
        self.pin_btn.setFixedSize(30, 30)
        self.pin_btn.setCheckable(True); self.pin_btn.setChecked(True)
        self.pin_btn.clicked.connect(self._toggle_pin)
        self.pin_btn.setToolTip("Always on top")
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(30, 30)
        close_btn.clicked.connect(self.close)
        for b in (self.pin_btn, close_btn):
            b.setStyleSheet("""
                QPushButton { background: rgba(255,255,255,0.08); color:#ECEFF4;
                    border:none; border-radius:15px; font-size:13px; }
                QPushButton:hover { background: rgba(255,255,255,0.18); }
                QPushButton:checked { background: rgba(198,66,78,0.4); }
            """)
        bar.addWidget(self.pin_btn); bar.addWidget(close_btn)
        root.addLayout(bar)

        # controls row
        ctrl = QHBoxLayout(); ctrl.setSpacing(8)
        load_btn = QPushButton("Load sheet…")
        style_button(load_btn); load_btn.clicked.connect(self.load_files)
        ctrl.addWidget(load_btn)
        self.song_select = QComboBox()
        self.song_select.setStyleSheet("""
            QComboBox { background: rgba(255,255,255,0.08); color:#ECEFF4;
                border:1px solid rgba(255,255,255,0.16); border-radius:12px;
                padding:7px 12px; }
            QComboBox::drop-down { border:none; width:22px; }
            QComboBox QAbstractItemView {
                background:#2C313A; color:#ECEFF4; selection-background-color:#C6424E;
                selection-color:#FFFFFF; border-radius:8px; outline:none; }
        """)
        self.song_select.currentIndexChanged.connect(self.select_song)
        self.song_select.addItem("No song loaded")
        ctrl.addWidget(self.song_select, 1)
        root.addLayout(ctrl)

        # keybind / library row
        kb = QHBoxLayout(); kb.setSpacing(8)
        self.bind_hint = QLabel("Keys: click a pad, then press a keyboard key to bind it")
        self.bind_hint.setStyleSheet("color:#96A0AC;")
        khf = QFont(self.font()); khf.setPointSize(9); self.bind_hint.setFont(khf)
        kb.addWidget(self.bind_hint, 1)
        clear_btn = QPushButton("Clear songs")
        style_button(clear_btn); clear_btn.clicked.connect(self.clear_songs)
        clear_btn.setToolTip("Remove all loaded songs")
        kb.addWidget(clear_btn)
        reset_btn = QPushButton("Reset keys")
        style_button(reset_btn); reset_btn.clicked.connect(self.reset_keys)
        reset_btn.setToolTip("Restore the default Y U I O P layout")
        kb.addWidget(reset_btn)
        root.addLayout(kb)

        # instrument
        self.instrument = Instrument(self)
        root.addWidget(self.instrument, 1)

        # legend
        leg = QHBoxLayout(); leg.setSpacing(14)
        for color, text in ((COL_CURRENT, "play now"), (COL_NEXT, "up next"),
                            (COL_ICON, "idle")):
            dot = QLabel("●")
            dot.setStyleSheet(f"color: rgb({color.red()},{color.green()},{color.blue()});")
            df = QFont(self.font()); df.setPointSize(11); dot.setFont(df)
            lab = QLabel(text); lab.setStyleSheet("color:#96A0AC;")
            lf = QFont(self.font()); lf.setPointSize(9); lab.setFont(lf)
            leg.addWidget(dot); leg.addWidget(lab)
        leg.addStretch()
        root.addLayout(leg)

        # transport
        tr = QHBoxLayout(); tr.setSpacing(8)
        self.restart_btn = QPushButton("⏮ Restart")
        style_button(self.restart_btn); self.restart_btn.clicked.connect(self.restart)
        self.prev_btn = QPushButton("‹ Back")
        style_button(self.prev_btn); self.prev_btn.clicked.connect(self.step_back)
        self.skip_btn = QPushButton("Skip ›")
        style_button(self.skip_btn); self.skip_btn.clicked.connect(self.step_forward)
        tr.addWidget(self.restart_btn); tr.addWidget(self.prev_btn); tr.addWidget(self.skip_btn)
        tr.addStretch()
        self.progress_lbl = QLabel("—")
        self.progress_lbl.setStyleSheet("color:#96A0AC;")
        tr.addWidget(self.progress_lbl)
        root.addLayout(tr)

        # status
        self.status_lbl = QLabel("Load a .skysheet, pick a song, then play the lit pads. Click a pad to bind a key.")
        self.status_lbl.setStyleSheet("color:#96A0AC;")
        self.status_lbl.setWordWrap(True)
        hf = QFont(self.font()); hf.setPointSize(9); self.status_lbl.setFont(hf)
        root.addWidget(self.status_lbl)

        # opacity — affects the background/chrome only, never the pads
        op = QHBoxLayout()
        op_lbl = QLabel("Background opacity")
        op_lbl.setStyleSheet("color:#96A0AC;"); op_lbl.setFont(hf)
        self.op_slider = QSlider(Qt.Orientation.Horizontal)
        self.op_slider.setRange(15, 100); self.op_slider.setValue(100)
        self.op_slider.valueChanged.connect(self._set_bg_opacity)
        self.op_slider.setStyleSheet("""
            QSlider::groove:horizontal { height:4px; background:rgba(255,255,255,0.15);
                border-radius:2px; }
            QSlider::handle:horizontal { width:14px; height:14px; margin:-6px 0;
                background:#C6424E; border-radius:7px; }
        """)
        op.addWidget(op_lbl); op.addWidget(self.op_slider)
        root.addLayout(op)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _center(self):
        scr = QApplication.primaryScreen().geometry()
        self.move((scr.width() - self.width()) // 2, (scr.height() - self.height()) // 3)

    def _apply_window_flags(self, first=False):
        """Build the window flags. Frameless always; stay-on-top when pinned.
        No 'Tool' flag — that made the window auto-hide on focus loss (macOS)."""
        flags = Qt.WindowType.FramelessWindowHint
        if self._pinned:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        if not first:
            self.show()
        self._raise_native_level()

    def _raise_native_level(self):
        """Keep the window above other apps. Qt's WindowStaysOnTopHint does the
        heavy lifting on all platforms once the 'Tool' flag is gone. On macOS we
        additionally raise the native NSWindow level (if pyobjc is available) so
        the overlay floats above fullscreen apps and joins every Space."""
        if self._pinned:
            self.raise_()
        if sys.platform == "darwin" and self._pinned:
            self._mac_set_native_level()

    def _mac_set_native_level(self):
        """Best-effort: float above fullscreen and show on all Spaces.
        Silently does nothing if pyobjc isn't installed."""
        try:
            import objc
            from AppKit import (
                NSApplication, NSScreenSaverWindowLevel,
                NSWindowCollectionBehaviorCanJoinAllSpaces,
                NSWindowCollectionBehaviorFullScreenAuxiliary,
            )
            handle = self.windowHandle()
            if handle is None:
                return
            # Match the Qt window to its NSWindow by title and lift its level.
            for win in NSApplication.sharedApplication().windows():
                win.setLevel_(NSScreenSaverWindowLevel)
                win.setCollectionBehavior_(
                    NSWindowCollectionBehaviorCanJoinAllSpaces
                    | NSWindowCollectionBehaviorFullScreenAuxiliary
                )
        except Exception:
            pass

    def _toggle_pin(self):
        self._pinned = self.pin_btn.isChecked()
        self._apply_window_flags()
        if self._pinned:
            self.raise_()
        self.persist()

    def _set_bg_opacity(self, v):
        # Only the background panel becomes translucent; pads stay solid.
        self.panel.bg_alpha = int(255 * v / 100)
        self.panel.update()
        self.persist()

    # -- status helper ------------------------------------------------------
    def status(self, msg):
        self.status_lbl.setText(msg)

    # -- persistence --------------------------------------------------------
    def persist(self):
        if self._loading:
            return
        data = {
            "keybinds": [k.keybind for k in self.instrument.keys],
            "song_paths": [s["path"] for s in self.songs if s.get("path")],
            "current_song": self.song_select.currentIndex() if self.songs else -1,
            "bg_opacity": self.op_slider.value(),
            "pinned": self.pin_btn.isChecked(),
            "geometry": [self.x(), self.y(), self.width(), self.height()],
        }
        save_settings(data)

    def _restore_settings(self):
        self._loading = True
        data = load_settings()

        # keybinds
        kb = data.get("keybinds")
        if isinstance(kb, list) and len(kb) == 15:
            for i, k in enumerate(self.instrument.keys):
                k.keybind = kb[i] if kb[i] is not None else None
            self.instrument.update()

        # opacity
        op = data.get("bg_opacity")
        if isinstance(op, int):
            self.op_slider.setValue(op)
            self._set_bg_opacity(op)

        # pinned
        if data.get("pinned") is False:
            self.pin_btn.setChecked(False)
            self._toggle_pin()

        # geometry
        geo = data.get("geometry")
        if isinstance(geo, list) and len(geo) == 4:
            self.setGeometry(int(geo[0]), int(geo[1]), int(geo[2]), int(geo[3]))

        # songs
        loaded = 0
        for path in data.get("song_paths", []):
            if os.path.exists(path):
                try:
                    self.songs.append(parse_sheet(path))
                    loaded += 1
                except Exception:
                    pass
        if self.songs:
            self._refresh_song_list()
            idx = data.get("current_song", 0)
            if not isinstance(idx, int) or idx < 0 or idx >= len(self.songs):
                idx = 0
            self.song_select.setCurrentIndex(idx)
            self.select_song(idx)
            self.status(f"Restored {loaded} song(s) and your keybinds.")

        self._loading = False

    # -- file loading -------------------------------------------------------
    def load_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Load Sky sheet(s)", "",
            "Sky sheets (*.skysheet *.json);;All files (*.*)")
        if not paths:
            return
        added = 0
        for p in paths:
            if any(s.get("path") == p for s in self.songs):
                continue  # skip duplicates
            try:
                self.songs.append(parse_sheet(p))
                added += 1
            except Exception as ex:
                self.status(f"Couldn't read {os.path.basename(p)}: {ex}")
        if added:
            self._refresh_song_list()
            last = len(self.songs) - 1
            self.song_select.setCurrentIndex(last)
            self.select_song(last)
            self.persist()
            self.status(f"Loaded {added} song(s). Pick one and play.")

    def clear_songs(self):
        self.songs = []
        self.combos = []
        self._refresh_song_list()
        self.restart()
        self.persist()
        self.status("All songs removed.")

    def _refresh_song_list(self):
        self.song_select.blockSignals(True)
        self.song_select.clear()
        if not self.songs:
            self.song_select.addItem("No song loaded")
        else:
            for s in self.songs:
                self.song_select.addItem(s["name"])
        self.song_select.blockSignals(False)

    def select_song(self, idx):
        if not self.songs or idx < 0 or idx >= len(self.songs):
            return
        self.combos = self.songs[idx]["combos"]
        self.restart()
        self.persist()
        self.status(f'"{self.songs[idx]["name"]}" — {len(self.combos)} steps. Play the lit pads.')

    def reset_keys(self):
        for i, k in enumerate(self.instrument.keys):
            k.keybind = DEFAULT_KEYBINDS[i]
        self.instrument.remap_target = None
        self.instrument.update()
        self.persist()
        self.status("Keybinds reset to default (Y U I O P / H J K L ; / N M , . /).")

    # -- playback / tracking ------------------------------------------------
    def restart(self):
        self.pos_index = 0
        self.satisfied = set()
        self._refresh_highlight()

    def step_forward(self):
        if self.pos_index < len(self.combos):
            self.pos_index += 1
            self.satisfied = set()
            self._refresh_highlight()

    def step_back(self):
        if self.pos_index > 0:
            self.pos_index -= 1
            self.satisfied = set()
            self._refresh_highlight()

    def _refresh_highlight(self):
        if not self.combos:
            self.instrument.clear_states()
            self.progress_lbl.setText("—")
            return
        if self.pos_index >= len(self.combos):
            self.instrument.clear_states()
            self.progress_lbl.setText("Done ✓")
            self.status("Song complete. ⏮ Restart to go again.")
            return
        current = set(self.combos[self.pos_index])
        nxt = set(self.combos[self.pos_index + 1]) if self.pos_index + 1 < len(self.combos) else set()
        remaining = current - self.satisfied
        self.instrument.set_states(remaining, nxt)
        self.progress_lbl.setText(f"{self.pos_index + 1} / {len(self.combos)}")

    def handle_play_key(self, key):
        if not self.combos or self.pos_index >= len(self.combos):
            return
        idx = None
        for k in self.instrument.keys:
            if k.keybind == key:
                idx = k.index
                break
        if idx is None:
            return
        self.instrument.flash(idx)

        current = set(self.combos[self.pos_index])
        if idx not in current:
            # a key outside the current combo: don't count, and reset progress so
            # the chord must be played cleanly as a group
            if len(current) > 1 and self.satisfied:
                self.satisfied = set()
                self._chord_timer.stop()
                self._refresh_highlight()
            return

        # single-note combo: one press advances immediately
        if len(current) == 1:
            self.pos_index += 1
            self.satisfied = set()
            self._chord_timer.stop()
            self._refresh_highlight()
            return

        # multi-note combo (chord): must press ALL keys together within the window
        if not self.satisfied:
            # first key of the chord starts the simultaneity window
            self._chord_timer.start(self.chord_window_ms)
        self.satisfied.add(idx)

        if current.issubset(self.satisfied):
            # whole chord pressed in time -> advance
            self.pos_index += 1
            self.satisfied = set()
            self._chord_timer.stop()
        self._refresh_highlight()

    def _chord_timeout(self):
        # window elapsed before the full chord was pressed -> reset, must retry
        if self.satisfied:
            self.satisfied = set()
            self._refresh_highlight()

    def keyPressEvent(self, e: QKeyEvent):
        # Rebinding always uses the focused key event (needs the exact key).
        if self.instrument.remap_target is not None:
            self.instrument.keyPressEvent(e)
            return
        if e.key() == Qt.Key.Key_Space:
            self.step_forward(); return
        # If the global listener is running, it already handles play keys — avoid
        # firing twice when the window happens to be focused.
        if getattr(self, "global_keys", None) is not None and self.global_keys.active:
            return
        self.handle_play_key(e.key())

    # -- bring-to-front (called by a second launch) -------------------------
    def raise_to_front(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self._raise_native_level()

    def showEvent(self, e):
        super().showEvent(e)
        # windowHandle() only exists after the first show — assert level now.
        self._raise_native_level()

    def closeEvent(self, e):
        self.persist()
        # stop the global keyboard listener thread before quitting
        if getattr(self, "global_keys", None) is not None:
            self.global_keys.stop()
        super().closeEvent(e)
        # Guarantee the process actually exits (no lingering/zombie process),
        # even with a background local server still referenced by the event loop.
        QApplication.quit()

    # -- window drag --------------------------------------------------------
    def mousePressEvent(self, e: QMouseEvent):
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() < 56:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        else:
            self._drag_pos = None
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent):
        if self._drag_pos is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e: QMouseEvent):
        if self._drag_pos is not None:
            self._drag_pos = None
            self.persist()


# ----------------------------------------------------------------------------
# Single-instance handling
# ----------------------------------------------------------------------------
class SingleInstance:
    """
    Robust single-instance guard.

    Strategy (standard Qt pattern, avoids stale-lock bugs):
      1. Try to CONNECT to an existing local server named APP_KEY.
         - If the connection succeeds, another instance is alive: send "raise"
           so it comes to the front, then this process exits cleanly.
      2. If the connection fails, we are the primary instance: remove any stale
         server (left by a crashed run), then listen so future launches reach us.

    A QLockFile is also held as a secondary guard and is always released on exit.
    """

    def __init__(self):
        self.is_primary = False
        self.server = None
        self.lock = None
        self.on_raise = None  # callback set by the app

    def try_acquire(self):
        # 1) probe for a running instance
        probe = QLocalSocket()
        probe.connectToServer(APP_KEY)
        if probe.waitForConnected(200):
            probe.write(b"raise")
            probe.flush()
            probe.waitForBytesWritten(200)
            probe.disconnectFromServer()
            probe.close()
            return False  # not primary — caller should exit
        probe.abort()
        probe.close()

        # 2) we are primary — clear any stale server, then listen
        QLocalServer.removeServer(APP_KEY)
        self.server = QLocalServer()
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not self.server.listen(APP_KEY):
            # extremely rare: couldn't listen; still run as primary
            self.server = None

        # secondary lock file (released in cleanup)
        self.lock = QLockFile(os.path.join(QDir.tempPath(), APP_KEY + ".lock"))
        self.lock.setStaleLockTime(0)
        self.lock.tryLock(50)

        if self.server is not None:
            self.server.newConnection.connect(self._handle_connection)
        self.is_primary = True
        return True

    def _handle_connection(self):
        conn = self.server.nextPendingConnection()
        if conn is None:
            return

        def read_and_raise():
            conn.readAll()
            if callable(self.on_raise):
                self.on_raise()
            conn.disconnectFromServer()

        conn.readyRead.connect(read_and_raise)

    def cleanup(self):
        try:
            if self.server is not None:
                self.server.close()
                QLocalServer.removeServer(APP_KEY)
                self.server = None
        except Exception:
            pass
        try:
            if self.lock is not None:
                self.lock.unlock()
                self.lock = None
        except Exception:
            pass


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Sky Practice")
    app.setOrganizationName("SkyPractice")
    app.setQuitOnLastWindowClosed(True)

    # --- single-instance check (before building any window) ---
    guard = SingleInstance()
    if not guard.try_acquire():
        # Another instance is already running; it was told to come forward.
        # Exit immediately and cleanly so no zombie/dock icon is left behind.
        return 0

    # Set a clean UI font directly. Qt resolves the first available family and
    # falls back on its own — no full system font scan (that made launch slow).
    ui_font = QFont()
    ui_font.setFamilies(["Inter", "SF Pro Text", "Helvetica Neue", "Segoe UI", "Arial"])
    ui_font.setPointSize(10)
    app.setFont(ui_font)

    win = MainWindow()
    guard.on_raise = win.raise_to_front

    # Ensure the guard is cleaned up no matter how the app exits.
    app.aboutToQuit.connect(guard.cleanup)

    win.show()
    win.raise_to_front()
    rc = app.exec()
    guard.cleanup()
    return rc


if __name__ == "__main__":
    sys.exit(main())
