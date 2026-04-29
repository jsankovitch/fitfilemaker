#!/usr/bin/env python3
"""
fitfilemaker — macOS GUI for merging workout files (.fit / .pwx).

Usage:
    source ~/Documents/projects/fitfilemaker/bin/activate
    python3 fitfilemaker_app.py

The venv must live outside iCloud Drive — Qt's plugin scanner returns an
empty directory listing for files under iCloud, breaking the cocoa plugin.

Third-party dependencies — see NOTICE file:
    PySide6   LGPL-3.0    Qt for Python
    fitparse  MIT         FIT file parsing
    fit-tool  BSD-3       FIT file writing
"""

from __future__ import annotations

import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))

# Qt platform plugin fix (must happen before any Qt import)
try:
    import PySide6 as _ps6
    _pp = Path(_ps6.__file__).parent / "Qt" / "plugins" / "platforms"
    if _pp.exists():
        os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", str(_pp))
except Exception:
    pass

from PySide6.QtCore import (
    Qt, QTimer, Signal, QObject, QPointF, QRectF, QSizeF,
)
from PySide6.QtGui import (
    QColor, QFont, QFontDatabase, QPainter, QPainterPath,
    QPalette, QPen, QBrush, QLinearGradient, QDragEnterEvent, QDropEvent,
)
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QSlider, QStackedWidget, QVBoxLayout,
    QWidget,
)

from app.core import fit as fit_core
from app.core import merger
from app.core import pwx as pwx_core
from app.core.fit import FIT_TO_PWX, PWX_TO_FIT
from app.core.security import detect_and_validate

# ---------------------------------------------------------------------------
# Design tokens
# ---------------------------------------------------------------------------

TOKENS_LIGHT: Dict[str, str] = {
    "page_bg":    "#EAECF2",
    "bg":         "#F4F5F8",
    "surface":    "#FEFEFF",
    "surface2":   "#ECEEF3",
    "surface3":   "#E4E6ED",
    "accent":     "#4A55C0",
    "accent_dim": "#EDF0FC",
    "accent_fg":  "#FFFFFF",
    "text":       "#1E2130",
    "text2":      "#5B6080",
    "text3":      "#8B90A8",
    "border":     "#DEE0E8",
    "border2":    "#C2C5D4",
    "good":       "#2E7A4F",
    "good_bg":    "#EAF5EF",
    "warn":       "#7A5A18",
    "warn_bg":    "#FBF4E4",
    "bad":        "#6A6E85",
    "bad_bg":     "#EEF0F5",
}

TOKENS_DARK: Dict[str, str] = {
    "page_bg":    "#111318",
    "bg":         "#1A1D24",
    "surface":    "#202530",
    "surface2":   "#262C38",
    "surface3":   "#2C3340",
    "accent":     "#7B88E8",
    "accent_dim": "#252A45",
    "accent_fg":  "#111318",
    "text":       "#EEF0F7",
    "text2":      "#9298B0",
    "text3":      "#6A6E85",
    "border":     "#2E3244",
    "border2":    "#3C4058",
    "good":       "#6DBF8A",
    "good_bg":    "#1D2E24",
    "warn":       "#D4A840",
    "warn_bg":    "#2A2314",
    "bad":        "#6A6E85",
    "bad_bg":     "#2C3340",
}

# File accent colors (file 1, 2, 3)
FILE_COLORS = ["#4A55C0", "#C46030", "#8855CC"]
FILE_COLORS_DARK = ["#7B88E8", "#D4784A", "#AA77EE"]

# Current active tokens (updated on theme change)
T: Dict[str, str] = {}


def _detect_dark() -> bool:
    hints = QApplication.styleHints()
    return hints.colorScheme() == Qt.ColorScheme.Dark


def _apply_tokens(dark: bool) -> None:
    T.clear()
    T.update(TOKENS_DARK if dark else TOKENS_LIGHT)


def _file_color(index: int, dark: bool = False) -> str:
    palette = FILE_COLORS_DARK if dark else FILE_COLORS
    return palette[index % len(palette)]


def c(token: str) -> QColor:
    return QColor(T.get(token, "#000000"))


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

SANS_FAMILY = "DM Sans"
MONO_FAMILY = "DM Mono"


def _load_fonts() -> None:
    fonts_dir = Path(__file__).parent / "fonts"
    for f in fonts_dir.glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(f))


# ---------------------------------------------------------------------------
# State dataclasses
# ---------------------------------------------------------------------------

FIELD_ORDER = ["hr", "pwr", "cad", "spd", "dist", "alt"]
FIELD_DISPLAY = {
    "hr":   ("Heart Rate", "bpm",  "♥"),
    "pwr":  ("Power",      "W",    "⚡"),
    "cad":  ("Cadence",    "rpm",  "↺"),
    "spd":  ("Speed",      "km/h", "→"),
    "dist": ("Distance",   "km",   "◎"),
    "alt":  ("Altitude",   "m",    "△"),
}


@dataclass
class WorkoutFile:
    id: str           # 'f1', 'f2', 'f3'
    path: Path
    file_type: str    # 'pwx' or 'fit'
    samples: list
    meta: object
    stats: Dict[str, Dict]   # {field_id: stats_dict}
    device_make: Optional[str]
    start_utc: datetime
    duration_sec: float
    size_str: str

    @property
    def label(self) -> str:
        idx = merger.FILE_IDS.index(self.id) if self.id in merger.FILE_IDS else 0
        return f"File {idx + 1}"

    @property
    def color_index(self) -> int:
        return merger.FILE_IDS.index(self.id) if self.id in merger.FILE_IDS else 0

    @property
    def duration_str(self) -> str:
        return _fmt_time(int(self.duration_sec))

    def waveform_data(self, max_points: int = 200) -> Dict[str, list]:
        """Downsample samples to a manageable point count for painting."""
        n = len(self.samples)
        stride = max(1, n // max_points)
        power, hr = [], []
        for i in range(0, n, stride):
            _, flds = self.samples[i]
            power.append(flds.get("pwr", 0.0))
            hr.append(flds.get("hr", 0.0))
        return {"power": power, "hr": hr}


@dataclass
class AppState:
    files: List[WorkoutFile] = field(default_factory=list)
    recommendations: Dict[str, Optional[str]] = field(default_factory=dict)
    field_choices: Dict[str, Optional[str]] = field(default_factory=dict)
    trim_start_pct: float = 0.0
    trim_end_pct: float = 100.0
    cuts: List[Tuple[str, float, float]] = field(default_factory=list)  # (id, s%, e%)
    output_filename: str = ""
    # Overlap data (recomputed when files change)
    base_samples: List = field(default_factory=list)
    extra_fit: Dict[str, list] = field(default_factory=dict)
    base_start_utc: Optional[datetime] = None
    base_file_id: str = "f1"

    @property
    def has_overlap(self) -> bool:
        return bool(self.base_samples)

    @property
    def total_duration_sec(self) -> float:
        if not self.base_samples:
            return 0.0
        return float(self.base_samples[-1][0])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_time(seconds: int) -> str:
    m, s = divmod(abs(int(seconds)), 60)
    return f"{m}:{s:02d}"


def _size_str(path: Path) -> str:
    try:
        b = path.stat().st_size
        if b >= 1_000_000:
            return f"{b / 1_000_000:.1f} MB"
        return f"{b / 1000:.0f} KB"
    except OSError:
        return ""


def _load_workout_file(path: Path, file_id: str) -> WorkoutFile:
    """Parse a file and return a WorkoutFile. Raises ValueError / Exception on error."""
    data = path.read_bytes()
    file_type = detect_and_validate(data, path.name)

    if file_type == "pwx":
        tree, start_local, samples = pwx_core.parse(path)
        make = _pwx_device_make(tree)
        start_utc = pwx_core.local_to_utc(start_local, pwx_core.system_utc_offset())
        meta = (tree, start_local, make)
    else:
        records = fit_core.parse(path)
        start_utc = records[0]["timestamp"]
        samples = _fit_records_to_samples(records, start_utc)
        make = None
        meta = start_utc

    duration_sec = float(samples[-1][0]) if samples else 0.0
    stats = merger.analyze_pwx_fields(samples, make)
    return WorkoutFile(
        id=file_id,
        path=path,
        file_type=file_type,
        samples=samples,
        meta=meta,
        stats=stats,
        device_make=make,
        start_utc=start_utc,
        duration_sec=duration_sec,
        size_str=_size_str(path),
    )


def _pwx_device_make(tree) -> Optional[str]:
    import xml.etree.ElementTree as ET
    NS = {"p": "http://www.peaksware.com/PWX/1/0"}
    workout = tree.getroot().find("p:workout", NS)
    if not workout:
        return None
    device = workout.find("p:device", NS)
    if not device:
        return None
    make = device.find("p:make", NS)
    return make.text if make is not None else None


def _fit_records_to_samples(records: list, start_utc: datetime) -> list:
    samples = []
    for r in records:
        offset = round((r["timestamp"] - start_utc).total_seconds())
        fields = {}
        for fit_field, pwx_field in FIT_TO_PWX.items():
            if fit_field in r and r[fit_field] is not None:
                try:
                    fields[pwx_field] = float(r[fit_field])
                except (TypeError, ValueError):
                    pass
        samples.append((offset, fields))
    return samples


def _samples_to_fit_records(samples: list, start_utc: datetime) -> list:
    from datetime import timedelta
    records = []
    for offset, fields in samples:
        ts = start_utc + timedelta(seconds=offset)
        rec = {"timestamp": ts}
        for pwx_field, val in fields.items():
            rec[PWX_TO_FIT.get(pwx_field, pwx_field)] = val
        records.append(rec)
    return records


def _recompute_overlap(state: AppState) -> None:
    """Update state.base_samples / extra_fit from the loaded files."""
    state.base_samples = []
    state.extra_fit = {}
    state.base_start_utc = None

    if not state.files:
        return

    base = state.files[0]
    state.base_file_id = base.id
    state.base_start_utc = base.start_utc

    if len(state.files) == 1:
        state.base_samples = base.samples
        return

    secondaries = []
    for wf in state.files[1:]:
        records = _samples_to_fit_records(wf.samples, wf.start_utc)
        secondaries.append((wf.id, records))

    try:
        base_overlap, extra = merger.find_overlap_multi(
            base.start_utc, base.samples, secondaries
        )
        state.base_samples = base_overlap
        state.extra_fit = extra
    except ValueError:
        pass  # no common overlap — leave empty


def _recompute_recommendations(state: AppState) -> None:
    """Recompute recommendations from all loaded files' stats."""
    file_stats_map: Dict[str, Dict[str, Dict]] = {wf.id: wf.stats for wf in state.files}
    state.recommendations = {}
    for field in FIELD_ORDER:
        pairs = [(wf.id, file_stats_map[wf.id].get(field)) for wf in state.files]
        fid, _ = merger.recommend_for_files(field, pairs)
        state.recommendations[field] = fid


def _auto_filename(state: AppState) -> str:
    if state.files:
        try:
            prefix = "trimmed" if len(state.files) == 1 else "merged"
            return f"{prefix}_{state.files[0].start_utc.strftime('%Y-%m-%d_%H%M')}"
        except Exception:
            pass
    return "merged_workout"


# ---------------------------------------------------------------------------
# Stylesheet builder
# ---------------------------------------------------------------------------

def _build_qss(dark: bool) -> str:
    tok = TOKENS_DARK if dark else TOKENS_LIGHT
    acc = tok["accent"]
    acc_dim = tok["accent_dim"]
    brd = tok["border2"]
    surf2 = tok["surface2"]
    txt = tok["text"]
    txt3 = tok["text3"]
    return f"""
QMainWindow, QWidget {{ background: {tok["bg"]}; color: {txt}; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{
    background: transparent; width: 6px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {brd}; border-radius: 3px; min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QSlider::groove:horizontal {{
    height: 4px; background: {brd}; border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {acc}; border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {acc}; width: 14px; height: 14px;
    margin: -5px 0; border-radius: 7px;
}}
QLineEdit {{
    background: {surf2}; color: {txt};
    border: 1px solid {brd}; border-radius: 6px;
    padding: 7px 10px; font-size: 13px;
}}
QLineEdit:focus {{ border-color: {acc}; }}
"""


# ---------------------------------------------------------------------------
# Tiny shared widgets
# ---------------------------------------------------------------------------

class HLine(QFrame):
    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.Shape.HLine)
        self.setStyleSheet(f"color: {T.get('border','#DEE0E8')};")


class QualityBadge(QLabel):
    _MAP = {
        "good": ("GOOD", "good",    "good_bg"),
        "fair": ("FAIR", "warn",    "warn_bg"),
        "bad":  ("POOR", "bad",     "bad_bg"),
        "none": ("NONE", "text3",   "surface3"),
    }

    def __init__(self, quality: str = "none"):
        super().__init__()
        self.set_quality(quality)

    def set_quality(self, quality: str) -> None:
        label, fg_tok, bg_tok = self._MAP.get(quality, self._MAP["none"])
        fg = T.get(fg_tok, "#888")
        bg = T.get(bg_tok, "#eee")
        self.setText(label)
        self.setStyleSheet(
            f"QLabel {{ background: {bg}; color: {fg}; border-radius: 99px;"
            f" padding: 1px 7px; font-size: 10px; font-weight: 700;"
            f" letter-spacing: 0.04em; }}"
        )


class FormatBadge(QLabel):
    def __init__(self, fmt: str):
        super().__init__(fmt.upper())
        acc = T.get("accent", "#4A55C0")
        adm = T.get("accent_dim", "#EDF0FC")
        self.setStyleSheet(
            f"QLabel {{ background: {adm}; color: {acc}; border-radius: 5px;"
            f" padding: 2px 7px; font-size: 10px; font-weight: 700;"
            f" letter-spacing: 0.07em; font-family: '{MONO_FAMILY}'; }}"
        )


# ---------------------------------------------------------------------------
# Waveform widget (shared by Trim and Export steps)
# ---------------------------------------------------------------------------

class WaveformWidget(QWidget):
    """
    Paints power (area + line) and HR (line) waveforms.
    In interactive mode, supports drag-to-cut mouse interactions.
    """
    cut_added = Signal(str, float, float)  # id, start_pct, end_pct

    def __init__(self, interactive: bool = True, height: int = 110):
        super().__init__()
        self._interactive = interactive
        self._target_h = height
        self.setMinimumHeight(height)
        self.setMaximumHeight(height)
        if interactive:
            self.setCursor(Qt.CursorShape.CrossCursor)

        # Data (list of floats, pre-downsampled)
        self._power: List[float] = []
        self._hr:    List[float] = []

        # Trim / cut state
        self._trim_start: float = 0.0   # 0–100
        self._trim_end:   float = 100.0
        self._cuts: List[Tuple[str, float, float]] = []

        # Drag state
        self._drag_start: Optional[float] = None
        self._drag_cur:   Optional[float] = None

    # ── Public setters ──────────────────────────────────────────────

    def set_data(self, power: List[float], hr: List[float]) -> None:
        self._power = power
        self._hr = hr
        self.update()

    def set_trim(self, start_pct: float, end_pct: float) -> None:
        self._trim_start = start_pct
        self._trim_end = end_pct
        self.update()

    def set_cuts(self, cuts: List[Tuple[str, float, float]]) -> None:
        self._cuts = cuts
        self.update()

    # ── Painting ────────────────────────────────────────────────────

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # Background
        p.fillRect(0, 0, w, h, c("surface"))

        if self._power or self._hr:
            self._paint_waveform(p, w, h)

        # Trim-excluded regions
        if self._trim_start > 0:
            ex = QColor(T.get("surface3", "#E4E6ED"))
            ex.setAlphaF(0.82)
            p.fillRect(0, 0, int(w * self._trim_start / 100), h, ex)
        if self._trim_end < 100:
            ex = QColor(T.get("surface3", "#E4E6ED"))
            ex.setAlphaF(0.82)
            x = int(w * self._trim_end / 100)
            p.fillRect(x, 0, w - x, h, ex)

        # Cut zones (diagonal hatch)
        for _, cs, ce in self._cuts:
            self._paint_hatch(p, w, h, cs, ce)

        # Trim boundary lines
        pen = QPen(c("accent"), 2.0, Qt.PenStyle.DashLine)
        pen.setDashPattern([4, 3])
        p.setPen(pen)
        if self._trim_start > 0:
            x = int(w * self._trim_start / 100)
            p.drawLine(x, 0, x, h)
        if self._trim_end < 100:
            x = int(w * self._trim_end / 100)
            p.drawLine(x, 0, x, h)

        # Active drag preview
        if self._drag_start is not None and self._drag_cur is not None:
            ds = min(self._drag_start, self._drag_cur) / 100.0 * w
            de = max(self._drag_start, self._drag_cur) / 100.0 * w
            if de - ds > 2:
                drag_fill = QColor(T.get("bad", "#6A6E85"))
                drag_fill.setAlphaF(0.14)
                p.fillRect(int(ds), 0, int(de - ds), h, drag_fill)
                drag_stroke = QColor(T.get("bad", "#6A6E85"))
                drag_stroke.setAlphaF(0.5)
                p.setPen(QPen(drag_stroke, 1))
                p.drawRect(int(ds), 0, int(de - ds), h)

        p.end()

    def _paint_waveform(self, p: QPainter, w: int, h: int) -> None:
        def _path(data: List[float], max_val: float) -> QPainterPath:
            n = len(data)
            if n < 2:
                return QPainterPath()
            path = QPainterPath()
            for i, v in enumerate(data):
                x = i / (n - 1) * w
                y = h - (v / max_val) * h * 0.88
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            return path

        # Power
        if self._power:
            max_pwr = max(max(self._power), 1.0)
            pwr_path = _path(self._power, max_pwr)

            # Fill
            area = QPainterPath(pwr_path)
            area.lineTo(w, h)
            area.lineTo(0, h)
            area.closeSubpath()
            grad = QLinearGradient(0, 0, 0, h)
            top = QColor(T.get("accent", "#4A55C0"))
            top.setAlphaF(0.38)
            bot = QColor(T.get("accent", "#4A55C0"))
            bot.setAlphaF(0.04)
            grad.setColorAt(0, top)
            grad.setColorAt(1, bot)
            p.fillPath(area, QBrush(grad))

            # Line
            line_col = QColor(T.get("accent", "#4A55C0"))
            line_col.setAlphaF(0.75)
            p.setPen(QPen(line_col, 1.5))
            p.drawPath(pwr_path)

        # HR
        if self._hr and any(v > 0 for v in self._hr):
            max_hr = max(max(self._hr), 1.0)
            hr_path = _path(self._hr, max_hr)
            hr_col = QColor("#C46030")
            hr_col.setAlphaF(0.72)
            p.setPen(QPen(hr_col, 1.5))
            p.drawPath(hr_path)

    def _paint_hatch(self, p: QPainter, w: int, h: int,
                     start_pct: float, end_pct: float) -> None:
        x1 = int(w * start_pct / 100)
        x2 = int(w * end_pct   / 100)
        if x2 <= x1:
            return
        bad = QColor(T.get("bad", "#6A6E85"))
        bad.setAlphaF(0.45)
        p.save()
        p.setClipRect(x1, 0, x2 - x1, h)
        pen = QPen(bad, 2.5)
        p.setPen(pen)
        step = 7
        for offset in range(-h, w + h, step):
            p.drawLine(x1 + offset, 0, x1 + offset + h, h)
        p.restore()

    # ── Mouse (interactive only) ─────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if not self._interactive:
            return
        self._drag_start = event.position().x() / self.width() * 100
        self._drag_cur = self._drag_start
        self.update()

    def mouseMoveEvent(self, event) -> None:
        if not self._interactive or self._drag_start is None:
            return
        self._drag_cur = max(0.0, min(100.0, event.position().x() / self.width() * 100))
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if not self._interactive or self._drag_start is None:
            return
        end = max(0.0, min(100.0, event.position().x() / self.width() * 100))
        s = min(self._drag_start, end)
        e = max(self._drag_start, end)
        if e - s > 1.5:
            cut_id = str(uuid.uuid4())[:8]
            self.cut_added.emit(cut_id, s, e)
        self._drag_start = None
        self._drag_cur = None
        self.update()


# ---------------------------------------------------------------------------
# Step 1 — Files
# ---------------------------------------------------------------------------

class FieldChip(QFrame):
    def __init__(self, field_id: str, stats: Optional[Dict]):
        super().__init__()
        label, unit, icon = FIELD_DISPLAY.get(field_id, (field_id, "", ""))
        quality = stats["quality"] if stats else "none"
        display_val = stats["display_value"] if stats else "No data"
        sub_val = stats.get("sub_value") if stats else None

        self.setStyleSheet(
            f"QFrame {{ background: {T.get('surface','#FEFEFF')};"
            f" border: 1px solid {T.get('border','#DEE0E8')};"
            f" border-radius: 6px; }}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(3)

        hdr = QLabel(f"{icon} {label}")
        hdr.setStyleSheet(f"color: {T.get('text3','#8B90A8')}; font-size: 10px; border: none;")
        lay.addWidget(hdr)

        row = QHBoxLayout()
        row.setSpacing(5)
        val_lbl = QLabel(display_val)
        val_lbl.setStyleSheet(
            f"color: {T.get('text','#1E2130')}; font-size: 12px; font-weight: 500;"
            f" font-family: '{MONO_FAMILY}'; border: none;"
        )
        row.addWidget(val_lbl)
        row.addWidget(QualityBadge(quality))
        row.addStretch()
        lay.addLayout(row)



class FileCard(QFrame):
    remove_clicked = Signal(str)   # file_id

    def __init__(self, wf: WorkoutFile, dark: bool = False):
        super().__init__()
        self._file_id = wf.id
        self._expanded = True
        color = _file_color(wf.color_index, dark)

        self.setStyleSheet(
            f"FileCard {{ background: {T.get('surface','#FEFEFF')};"
            f" border: 1px solid {T.get('border','#DEE0E8')};"
            f" border-radius: 10px; }}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header row ──────────────────────────────────────────────
        self._header = QWidget()
        self._header.setFixedHeight(56)
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.setStyleSheet("background: transparent;")
        hlay = QHBoxLayout(self._header)
        hlay.setContentsMargins(0, 0, 12, 0)
        hlay.setSpacing(10)

        # Colored bar
        bar = QFrame()
        bar.setFixedSize(3, 56)
        bar.setStyleSheet(f"background: {color}; border-radius: 2px; border: none;")
        hlay.addWidget(bar)

        # File info
        info = QVBoxLayout()
        info.setContentsMargins(0, 11, 0, 11)
        info.setSpacing(2)
        top_row = QHBoxLayout()
        top_row.setSpacing(7)
        top_row.addWidget(FormatBadge(wf.file_type))
        name = QLabel(wf.path.name)
        name.setStyleSheet(
            f"color: {T.get('text','#1E2130')}; font-size: 12px; font-weight: 500;"
            f" font-family: '{MONO_FAMILY}';"
        )
        name.setTextFormat(Qt.TextFormat.PlainText)
        top_row.addWidget(name, 1)
        info.addLayout(top_row)

        device_str = wf.device_make or "Unknown device"
        sub = QLabel(f"{device_str} · {wf.duration_str} · {wf.size_str}")
        sub.setStyleSheet(f"color: {T.get('text3','#8B90A8')}; font-size: 11px;")
        info.addWidget(sub)
        hlay.addLayout(info, 1)

        # Chevron + Remove
        self._chevron = QLabel("▲")
        self._chevron.setStyleSheet(f"color: {T.get('text3','#8B90A8')}; font-size: 10px;")
        hlay.addWidget(self._chevron)

        remove_btn = QPushButton("×")
        remove_btn.setFixedSize(24, 24)
        remove_btn.setStyleSheet(
            f"QPushButton {{ background: none; border: none; color: {T.get('text3','#8B90A8')};"
            f" font-size: 18px; border-radius: 3px; }}"
            f"QPushButton:hover {{ color: {T.get('text','#1E2130')}; }}"
        )
        remove_btn.clicked.connect(lambda: self.remove_clicked.emit(self._file_id))
        hlay.addWidget(remove_btn)

        root.addWidget(self._header)
        self._header.mousePressEvent = lambda _: self._toggle()

        # ── Expanded panel ───────────────────────────────────────────
        self._expand_widget = QFrame()
        self._expand_widget.setStyleSheet(
            f"QFrame {{ background: {T.get('surface2','#ECEEF3')};"
            f" border-top: 1px solid {T.get('border','#DEE0E8')}; border-radius: 0; }}"
        )
        exp_lay = QVBoxLayout(self._expand_widget)
        exp_lay.setContentsMargins(14, 12, 14, 12)
        exp_lay.setSpacing(0)

        chips = QWidget()
        chips.setStyleSheet("background: transparent;")
        chips_lay = _FlowLayout(chips, h_spacing=7, v_spacing=7)
        for fid in FIELD_ORDER:
            chips_lay.addWidget(FieldChip(fid, wf.stats.get(fid)))
        exp_lay.addWidget(chips)
        root.addWidget(self._expand_widget)

        self._update_chevron()

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._expand_widget.setVisible(self._expanded)
        self._update_chevron()

    def _update_chevron(self) -> None:
        self._chevron.setText("▲" if self._expanded else "▼")
        self._expand_widget.setVisible(self._expanded)


class DropZone(QFrame):
    browse_clicked = Signal()

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self._hover = False
        self._update_style()

        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(14)

        self._icon_lbl = QLabel("+")
        self._icon_lbl.setFixedSize(34, 34)
        self._icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_lbl.setStyleSheet(
            f"QLabel {{ background: {T.get('accent_dim','#EDF0FC')};"
            f" color: {T.get('accent','#4A55C0')}; border-radius: 6px;"
            f" font-size: 22px; font-weight: 300; }}"
        )
        lay.addWidget(self._icon_lbl)

        txt = QVBoxLayout()
        txt.setSpacing(2)
        self._main_lbl = QLabel("Add another file")
        self._main_lbl.setStyleSheet(
            f"color: {T.get('text','#1E2130')}; font-size: 13px; font-weight: 500;"
        )
        sub = QLabel("Drop a .fit or .pwx file, or click to browse")
        sub.setStyleSheet(f"color: {T.get('text3','#8B90A8')}; font-size: 12px;")
        txt.addWidget(self._main_lbl)
        txt.addWidget(sub)
        lay.addLayout(txt, 1)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_disabled_look(self, disabled: bool) -> None:
        self.setEnabled(not disabled)
        self.setProperty("disabled_look", disabled)
        self._main_lbl.setText("Maximum 3 files loaded" if disabled else "Add another file")
        self._update_style()

    def _update_style(self) -> None:
        border_col = T.get("accent", "#4A55C0") if self._hover else T.get("border2", "#C2C5D4")
        bg = T.get("accent_dim", "#EDF0FC") if self._hover else "transparent"
        opacity = "0.5" if not self.isEnabled() else "1"
        self.setStyleSheet(
            f"DropZone {{ border: 2px dashed {border_col}; border-radius: 10px;"
            f" background: {bg}; opacity: {opacity}; }}"
        )

    def mousePressEvent(self, _event) -> None:
        if self.isEnabled():
            self.browse_clicked.emit()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._hover = True
            self._update_style()

    def dragLeaveEvent(self, _event) -> None:
        self._hover = False
        self._update_style()

    def dropEvent(self, event: QDropEvent) -> None:
        self._hover = False
        self._update_style()
        urls = event.mimeData().urls()
        if urls:
            event.acceptProposedAction()
            self.browse_clicked.emit()  # let MainWindow open dialog (simplest)


class FilesStep(QWidget):
    files_changed = Signal()

    def __init__(self, state: AppState, dark: bool = False):
        super().__init__()
        self._state = state
        self._dark = dark
        self._cards: Dict[str, FileCard] = {}

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        self._lay = QVBoxLayout(inner)
        self._lay.setContentsMargins(22, 22, 22, 22)
        self._lay.setSpacing(16)

        # Header
        h2 = QLabel("Load workout files")
        h2.setStyleSheet(
            f"color: {T.get('text','#1E2130')}; font-size: 17px; font-weight: 600;"
        )
        body = QLabel(
            "Add two or more workout files. Supports <b>.fit</b> and <b>.pwx</b>."
            " Device context is detected automatically."
        )
        body.setStyleSheet(f"color: {T.get('text2','#5B6080')}; font-size: 13px;")
        body.setWordWrap(True)
        self._lay.addWidget(h2)
        self._lay.addWidget(body)

        # Cards container
        self._cards_widget = QWidget()
        self._cards_widget.setStyleSheet("background: transparent;")
        self._cards_lay = QVBoxLayout(self._cards_widget)
        self._cards_lay.setContentsMargins(0, 0, 0, 0)
        self._cards_lay.setSpacing(9)
        self._lay.addWidget(self._cards_widget)

        # Drop zone
        self._drop_zone = DropZone()
        self._drop_zone.browse_clicked.connect(self._browse)
        self._lay.addWidget(self._drop_zone)

        # Banner (hidden until ≥2 files)
        self._banner = QFrame()
        self._banner.setStyleSheet(
            f"QFrame {{ background: {T.get('good_bg','#EAF5EF')};"
            f" border-radius: 6px; border: none; }}"
        )
        b_lay = QHBoxLayout(self._banner)
        b_lay.setContentsMargins(15, 11, 15, 11)
        b_lay.setSpacing(8)
        tick = QLabel("✓")
        tick.setStyleSheet(f"color: {T.get('good','#2E7A4F')}; font-weight: 700; font-size: 14px;")
        self._banner_text = QLabel()
        self._banner_text.setStyleSheet(
            f"color: {T.get('good','#2E7A4F')}; font-size: 13px;"
        )
        b_lay.addWidget(tick)
        b_lay.addWidget(self._banner_text, 1)
        self._lay.addWidget(self._banner)
        self._banner.hide()

        self._lay.addStretch()

        scroll.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self.refresh()

    def refresh(self) -> None:
        # Remove cards for deleted files
        existing_ids = {wf.id for wf in self._state.files}
        for fid in list(self._cards):
            if fid not in existing_ids:
                card = self._cards.pop(fid)
                self._cards_lay.removeWidget(card)
                card.deleteLater()

        # Add cards for new files
        for wf in self._state.files:
            if wf.id not in self._cards:
                card = FileCard(wf, self._dark)
                card.remove_clicked.connect(self._remove_file)
                self._cards[wf.id] = card
                self._cards_lay.addWidget(card)

        n = len(self._state.files)
        self._drop_zone.set_disabled_look(n >= 3)

        if n >= 2:
            self._banner_text.setText(
                f"<b>{n} files loaded.</b> Device context detected — "
                f"field recommendations are ready on the next step."
            )
            self._banner.show()
        elif n == 1:
            self._banner_text.setText(
                "<b>1 file loaded.</b> You can trim and export this file, "
                "or add a second file to merge."
            )
            self._banner.show()
        else:
            self._banner.hide()

    def _browse(self) -> None:
        if len(self._state.files) >= 3:
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open workout file", "",
            "Workout files (*.fit *.pwx);;FIT files (*.fit);;PWX files (*.pwx)"
        )
        for path_str in paths:
            if len(self._state.files) >= 3:
                break
            self._load_path(Path(path_str))

    def _load_path(self, path: Path) -> None:
        used_ids = {wf.id for wf in self._state.files}
        for candidate in merger.FILE_IDS:
            if candidate not in used_ids:
                file_id = candidate
                break
        else:
            return

        try:
            wf = _load_workout_file(path, file_id)
        except ValueError as e:
            QMessageBox.warning(self, "Invalid file", str(e))
            return
        except Exception as e:
            QMessageBox.critical(self, "Error loading file", str(e))
            return

        self._state.files.append(wf)
        if not self._state.output_filename:
            self._state.output_filename = _auto_filename(self._state)
        self.files_changed.emit()

    def _remove_file(self, file_id: str) -> None:
        self._state.files = [wf for wf in self._state.files if wf.id != file_id]
        self.files_changed.emit()


# ---------------------------------------------------------------------------
# Step 2 — Fields
# ---------------------------------------------------------------------------

class SourceButton(QFrame):
    clicked = Signal()

    def __init__(self, wf: WorkoutFile, field_id: str,
                 is_rec: bool, dark: bool = False):
        super().__init__()
        self._wf = wf
        self._field_id = field_id
        self._is_rec = is_rec
        self._dark = dark
        self._selected = False

        stats = wf.stats.get(field_id)
        quality = stats["quality"] if stats else "none"
        display_val = stats["display_value"] if stats else "No data"
        self._quality = quality
        self._display = display_val

        self.setEnabled(quality != "none")
        if quality != "none":
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._build_ui()

    def mousePressEvent(self, event) -> None:
        if self.isEnabled():
            self.clicked.emit()
        super().mousePressEvent(event)

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(3)

        # Label row
        label_row = QHBoxLayout()
        label_row.setSpacing(5)
        file_lbl = QLabel(self._wf.label)
        file_lbl.setObjectName("file_lbl")
        file_lbl.setStyleSheet(
            f"color: {T.get('text3','#8B90A8')}; font-size: 10px; font-weight: 700;"
            f" letter-spacing: 0.05em; font-family: '{MONO_FAMILY}';"
        )
        label_row.addWidget(file_lbl)

        if self._is_rec:
            rec_pill = QLabel("★ REC")
            rec_pill.setStyleSheet(
                f"QLabel {{ background: {T.get('accent_dim','#EDF0FC')};"
                f" color: {T.get('accent','#4A55C0')}; border-radius: 99px;"
                f" padding: 1px 5px; font-size: 9px; font-weight: 700;"
                f" letter-spacing: 0.04em; }}"
            )
            label_row.addWidget(rec_pill)
        label_row.addStretch()
        lay.addLayout(label_row)

        # Value
        val_lbl = QLabel(self._display)
        val_lbl.setObjectName("val_lbl")
        val_lbl.setStyleSheet(
            f"color: {T.get('text2','#5B6080')}; font-size: 12px;"
            f" font-family: '{MONO_FAMILY}';"
        )
        lay.addWidget(val_lbl)

        # Quality badge — pill should not stretch full width
        self._badge = QualityBadge(self._quality)
        badge_row = QHBoxLayout()
        badge_row.setContentsMargins(0, 0, 0, 0)
        badge_row.setSpacing(0)
        badge_row.addWidget(self._badge)
        badge_row.addStretch()
        lay.addLayout(badge_row)

        self._apply_selected_style()

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._apply_selected_style()

    def _apply_selected_style(self) -> None:
        bg = T.get("accent_dim", "#EDF0FC") if self._selected else "transparent"
        acc = T.get("accent", "#4A55C0")
        border_left = f"border-left: 3px solid {acc};" if self._selected else "border-left: 3px solid transparent;"
        self.setStyleSheet(
            f"QFrame {{ background: {bg}; border: none; {border_left} }}"
        )
        # Update child label colors
        file_lbl = self.findChild(QLabel, "file_lbl")
        val_lbl = self.findChild(QLabel, "val_lbl")
        if file_lbl:
            col = acc if self._selected else T.get("text3", "#8B90A8")
            file_lbl.setStyleSheet(
                f"color: {col}; font-size: 10px; font-weight: 700;"
                f" letter-spacing: 0.05em; font-family: '{MONO_FAMILY}';"
            )
        if val_lbl:
            col = T.get("text", "#1E2130") if self._selected else T.get("text2", "#5B6080")
            wt = "600" if self._selected else "400"
            val_lbl.setStyleSheet(
                f"color: {col}; font-size: 12px; font-weight: {wt};"
                f" font-family: '{MONO_FAMILY}';"
            )


class FieldRow(QFrame):
    choice_changed = Signal(str, object)   # field_id, file_id | None

    def __init__(self, field_id: str, state: AppState, dark: bool = False):
        super().__init__()
        self._field_id = field_id
        self._state = state
        self._dark = dark
        self._note_open = False
        self._src_buttons: Dict[Optional[str], SourceButton] = {}

        label, unit, icon = FIELD_DISPLAY.get(field_id, (field_id, "", ""))
        rec_fid = state.recommendations.get(field_id)
        choice = state.field_choices.get(field_id)

        self._build(label, unit, icon, rec_fid, choice)
        self._update_border()

    def _build(self, label: str, unit: str, icon: str,
               rec_fid: Optional[str], choice: Optional[str]) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Main row
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        row_lay = QHBoxLayout(row)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.setSpacing(0)

        # Field label column
        lbl_col = QWidget()
        lbl_col.setFixedWidth(130)
        lbl_col.setStyleSheet("background: transparent;")
        lbl_lay = QVBoxLayout(lbl_col)
        lbl_lay.setContentsMargins(14, 13, 14, 13)
        lbl_lay.setSpacing(1)
        name_row = QHBoxLayout()
        name_row.setSpacing(5)
        name_row.addWidget(QLabel(icon))
        name_lbl = QLabel(label)
        name_lbl.setStyleSheet(
            f"color: {T.get('text','#1E2130')}; font-size: 13px; font-weight: 600;"
        )
        name_row.addWidget(name_lbl)
        name_row.addStretch()
        lbl_lay.addLayout(name_row)
        unit_lbl = QLabel(unit)
        unit_lbl.setStyleSheet(
            f"color: {T.get('text3','#8B90A8')}; font-size: 10px;"
            f" font-family: '{MONO_FAMILY}';"
        )
        lbl_lay.addWidget(unit_lbl)
        row_lay.addWidget(lbl_col)

        # Separator between label column and source buttons
        sep_lbl = QFrame()
        sep_lbl.setFrameShape(QFrame.Shape.VLine)
        sep_lbl.setStyleSheet(f"color: {T.get('border','#DEE0E8')}; max-width: 1px;")
        row_lay.addWidget(sep_lbl)

        # Source buttons
        src_area = QWidget()
        src_area.setStyleSheet("background: transparent;")
        src_lay = QHBoxLayout(src_area)
        src_lay.setContentsMargins(0, 0, 0, 0)
        src_lay.setSpacing(0)

        for wf in self._state.files:
            is_rec = (wf.id == rec_fid)
            btn = SourceButton(wf, self._field_id, is_rec, self._dark)
            btn.set_selected(choice == wf.id)
            btn.clicked.connect(lambda _, fid=wf.id: self._on_src(fid))
            self._src_buttons[wf.id] = btn
            src_lay.addWidget(btn)
            # Separator line between buttons
            if wf is not self._state.files[-1]:
                sep = QFrame()
                sep.setFrameShape(QFrame.Shape.VLine)
                sep.setStyleSheet(f"color: {T.get('border','#DEE0E8')}; max-width: 1px;")
                src_lay.addWidget(sep)

        row_lay.addWidget(src_area, 1)

        # Full-height separator before Exclude
        sep_excl = QFrame()
        sep_excl.setFrameShape(QFrame.Shape.VLine)
        sep_excl.setStyleSheet(f"color: {T.get('border','#DEE0E8')}; max-width: 1px;")
        row_lay.addWidget(sep_excl)

        # Exclude button
        self._excl_btn = QPushButton("⊘\nExclude")
        self._excl_btn.setFixedWidth(76)
        self._excl_btn.clicked.connect(lambda: self._on_src(None))
        self._update_excl_style(choice is None)
        row_lay.addWidget(self._excl_btn)

        # Full-height separator before note button
        sep_note = QFrame()
        sep_note.setFrameShape(QFrame.Shape.VLine)
        sep_note.setStyleSheet(f"color: {T.get('border','#DEE0E8')}; max-width: 1px;")
        row_lay.addWidget(sep_note)

        # Note toggle — small rounded square button
        self._note_btn = QPushButton("?")
        self._note_btn.setFixedSize(36, 36)
        self._note_btn.setCheckable(True)
        self._note_btn.setStyleSheet(
            f"QPushButton {{ background: {T.get('surface2','#ECEEF3')};"
            f" border: 1px solid {T.get('border2','#C2C5D4')}; border-radius: 6px;"
            f" color: {T.get('text3','#8B90A8')}; font-size: 13px; font-weight: 500;"
            f" margin: 8px 10px; padding: 0; }}"
            f"QPushButton:checked {{ background: {T.get('accent_dim','#EDF0FC')};"
            f" color: {T.get('accent','#4A55C0')};"
            f" border-color: {T.get('accent','#4A55C0')}; }}"
        )
        self._note_btn.toggled.connect(self._toggle_note)
        row_lay.addWidget(self._note_btn)
        root.addWidget(row)

        # Note panel
        self._note_panel = QFrame()
        self._note_panel.setStyleSheet(
            f"QFrame {{ background: {T.get('accent_dim','#EDF0FC')};"
            f" border-top: 1px solid {T.get('border','#DEE0E8')}; border-radius: 0; }}"
        )
        np_lay = QHBoxLayout(self._note_panel)
        np_lay.setContentsMargins(14, 10, 14, 10)
        np_lay.setSpacing(8)
        star = QLabel("★")
        star.setStyleSheet(
            f"color: {T.get('accent','#4A55C0')}; font-weight: 700; font-size: 12px;"
        )
        np_lay.addWidget(star)
        self._note_text = QLabel()
        self._note_text.setStyleSheet(
            f"color: {T.get('text2','#5B6080')}; font-size: 12px; line-height: 1.55;"
        )
        self._note_text.setWordWrap(True)
        np_lay.addWidget(self._note_text, 1)
        self._override_pill = QLabel("Override active")
        self._override_pill.setStyleSheet(
            f"QLabel {{ background: {T.get('warn_bg','#FBF4E4')};"
            f" color: {T.get('warn','#7A5A18')}; border-radius: 99px;"
            f" padding: 1px 6px; font-size: 10px; font-weight: 700; }}"
        )
        np_lay.addWidget(self._override_pill)
        root.addWidget(self._note_panel)
        self._note_panel.hide()

        self._refresh_note()

    def _on_src(self, file_id: Optional[str]) -> None:
        for fid, btn in self._src_buttons.items():
            btn.set_selected(fid == file_id)
        self._update_excl_style(file_id is None)
        self._state.field_choices[self._field_id] = file_id
        self._update_border()
        self._refresh_note()
        self.choice_changed.emit(self._field_id, file_id)

    def _update_excl_style(self, excluded: bool) -> None:
        if excluded:
            bg = T.get("bad_bg", "#EEF0F5")
            fg = T.get("bad", "#6A6E85")
            wt = "600"
        else:
            bg = "transparent"
            fg = T.get("text3", "#8B90A8")
            wt = "400"
        self._excl_btn.setStyleSheet(
            f"QPushButton {{ background: {bg}; color: {fg}; font-weight: {wt};"
            f" border: none; }}"
        )

    def _update_border(self) -> None:
        choice = self._state.field_choices.get(self._field_id)
        rec = self._state.recommendations.get(self._field_id)
        overridden = choice != rec
        border = (f"border: 1.5px solid {T.get('warn','#7A5A18')};"
                  if overridden else
                  f"border: 1px solid {T.get('border','#DEE0E8')};")
        self.setStyleSheet(
            f"FieldRow {{ background: {T.get('surface','#FEFEFF')};"
            f" {border} border-radius: 10px; }}"
        )

    def _toggle_note(self, checked: bool) -> None:
        self._note_panel.setVisible(checked)

    def _refresh_note(self) -> None:
        pairs = [(wf.id, wf.stats.get(self._field_id)) for wf in self._state.files]
        _, reason = merger.recommend_for_files(self._field_id, pairs)
        self._note_text.setText(reason)
        choice = self._state.field_choices.get(self._field_id)
        rec = self._state.recommendations.get(self._field_id)
        self._override_pill.setVisible(choice != rec)

    def reset_to_rec(self) -> None:
        rec = self._state.recommendations.get(self._field_id)
        self._on_src(rec)


class FieldsStep(QWidget):
    def __init__(self, state: AppState, dark: bool = False):
        super().__init__()
        self._state = state
        self._dark = dark
        self._rows: Dict[str, FieldRow] = {}

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        self._lay = QVBoxLayout(inner)
        self._lay.setContentsMargins(22, 22, 22, 22)
        self._lay.setSpacing(14)

        # Header
        hdr_row = QHBoxLayout()
        h2 = QLabel("Choose data sources")
        h2.setStyleSheet(
            f"color: {T.get('text','#1E2130')}; font-size: 17px; font-weight: 600;"
        )
        hdr_row.addWidget(h2, 1)
        self._reset_btn = QPushButton("Reset to recommended")
        self._reset_btn.setStyleSheet(
            f"QPushButton {{ background: {T.get('surface2','#ECEEF3')};"
            f" color: {T.get('text2','#5B6080')};"
            f" border: 1px solid {T.get('border2','#C2C5D4')};"
            f" border-radius: 6px; padding: 6px 12px; font-size: 11px; font-weight: 600; }}"
        )
        self._reset_btn.clicked.connect(self._reset_all)
        self._reset_btn.hide()
        hdr_row.addWidget(self._reset_btn)
        self._lay.addLayout(hdr_row)

        body = QLabel(
            "Select which file supplies each field in the merged output. "
            "<span style='color:#4A55C0;font-weight:500'>★ Recommended</span>"
            " sources are pre-selected based on device type and data quality."
        )
        body.setStyleSheet(f"color: {T.get('text2','#5B6080')}; font-size: 13px;")
        body.setWordWrap(True)
        self._lay.addWidget(body)

        # Field rows
        self._rows_container = QVBoxLayout()
        self._rows_container.setSpacing(5)
        self._lay.addLayout(self._rows_container)
        self._lay.addStretch()

        scroll.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def refresh(self) -> None:
        # Clear existing rows
        for row in self._rows.values():
            self._rows_container.removeWidget(row)
            row.deleteLater()
        self._rows.clear()

        if not self._state.files:
            return

        for fid in FIELD_ORDER:
            row = FieldRow(fid, self._state, self._dark)
            row.choice_changed.connect(self._on_choice_changed)
            self._rows[fid] = row
            self._rows_container.addWidget(row)

        self._update_reset_btn()

    def _on_choice_changed(self, _field_id: str, _file_id) -> None:
        self._update_reset_btn()

    def _update_reset_btn(self) -> None:
        overridden = any(
            self._state.field_choices.get(fid) != self._state.recommendations.get(fid)
            for fid in FIELD_ORDER
        )
        self._reset_btn.setVisible(overridden)

    def _reset_all(self) -> None:
        for fid, row in self._rows.items():
            row.reset_to_rec()
        self._update_reset_btn()


# ---------------------------------------------------------------------------
# Step 3 — Trim
# ---------------------------------------------------------------------------

class TrimStep(QWidget):
    state_changed = Signal()

    def __init__(self, state: AppState):
        super().__init__()
        self._state = state

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(22, 22, 22, 22)
        lay.setSpacing(14)

        # Header
        h2 = QLabel("Trim workout")
        h2.setStyleSheet(
            f"color: {T.get('text','#1E2130')}; font-size: 17px; font-weight: 600;"
        )
        body = QLabel(
            "Adjust start and end with the sliders. "
            "<b>Drag on the graph</b> to mark a mid-workout section for removal."
        )
        body.setStyleSheet(f"color: {T.get('text2','#5B6080')}; font-size: 13px;")
        body.setWordWrap(True)
        lay.addWidget(h2)
        lay.addWidget(body)

        # Graph card
        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background: {T.get('surface','#FEFEFF')};"
            f" border: 1px solid {T.get('border','#DEE0E8')};"
            f" border-radius: 10px; }}"
        )
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(0, 0, 0, 0)
        card_lay.setSpacing(0)

        # Legend bar
        legend = QFrame()
        legend.setStyleSheet(
            f"QFrame {{ background: {T.get('surface2','#ECEEF3')};"
            f" border-bottom: 1px solid {T.get('border','#DEE0E8')}; border-radius: 0; }}"
        )
        leg_lay = QHBoxLayout(legend)
        leg_lay.setContentsMargins(14, 8, 14, 8)
        leg_lay.addStretch()
        self._pwr_legend = self._legend_item("#4A55C0", "Power (W)", 3)
        self._hr_legend  = self._legend_item("#C46030", "Heart Rate (bpm)", 2)
        leg_lay.addWidget(self._pwr_legend)
        leg_lay.addWidget(self._hr_legend)
        self._cuts_legend = self._legend_item(T.get("bad","#6A6E85"), "0 cuts", 0, hatch=True)
        self._cuts_legend.hide()
        leg_lay.addWidget(self._cuts_legend)
        card_lay.addWidget(legend)

        # Cut tool callout
        callout = QFrame()
        callout.setStyleSheet(
            f"QFrame {{ background: {T.get('accent_dim','#EDF0FC')};"
            f" border-bottom: 1px solid {T.get('border','#DEE0E8')}; border-radius: 0; }}"
        )
        co_lay = QHBoxLayout(callout)
        co_lay.setContentsMargins(14, 9, 14, 9)
        co_lay.setSpacing(10)
        scissor_box = QLabel("✂")
        scissor_box.setFixedSize(28, 28)
        scissor_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scissor_box.setStyleSheet(
            f"QLabel {{ background: {T.get('accent','#4A55C0')};"
            f" color: {T.get('accent_fg','#FFFFFF')}; border-radius: 6px;"
            f" font-size: 14px; font-weight: 700; }}"
        )
        co_lay.addWidget(scissor_box)
        co_txt = QVBoxLayout()
        co_txt.setSpacing(1)
        cut_title = QLabel("Cut tool")
        cut_title.setStyleSheet(
            f"color: {T.get('accent','#4A55C0')}; font-size: 12px; font-weight: 600;"
        )
        cut_desc = QLabel(
            "Click and drag <b>directly on the graph</b> to mark a section for removal"
        )
        cut_desc.setStyleSheet(f"color: {T.get('text2','#5B6080')}; font-size: 11px;")
        co_txt.addWidget(cut_title)
        co_txt.addWidget(cut_desc)
        co_lay.addLayout(co_txt, 1)
        card_lay.addWidget(callout)

        # Waveform
        self._waveform = WaveformWidget(interactive=True, height=110)
        self._waveform.cut_added.connect(self._on_cut_added)
        card_lay.addWidget(self._waveform)

        # Sliders
        slider_frame = QFrame()
        slider_frame.setStyleSheet(
            f"QFrame {{ border-top: 1px solid {T.get('border','#DEE0E8')}; border-radius: 0; }}"
        )
        sl_lay = QHBoxLayout(slider_frame)
        sl_lay.setContentsMargins(16, 10, 16, 14)
        sl_lay.setSpacing(20)

        self._start_slider, self._start_val = self._make_slider("Start", sl_lay)
        self._end_slider,   self._end_val   = self._make_slider("End",   sl_lay)

        self._start_slider.setRange(0, 99)
        self._end_slider.setRange(1, 100)
        self._start_slider.setValue(0)
        self._end_slider.setValue(100)
        self._start_slider.valueChanged.connect(self._on_start_changed)
        self._end_slider.valueChanged.connect(self._on_end_changed)
        card_lay.addWidget(slider_frame)
        lay.addWidget(card)

        # Output summary
        self._summary_frame = QFrame()
        self._summary_frame.setStyleSheet(
            f"QFrame {{ background: {T.get('surface','#FEFEFF')};"
            f" border: 1px solid {T.get('border','#DEE0E8')}; border-radius: 6px; }}"
        )
        sum_lay = QHBoxLayout(self._summary_frame)
        sum_lay.setContentsMargins(14, 9, 14, 9)
        sum_lay.setSpacing(6)
        out_lbl = QLabel("Output:")
        out_lbl.setStyleSheet(f"color: {T.get('text2','#5B6080')}; font-size: 12px;")
        sum_lay.addWidget(out_lbl)
        self._output_dur = QLabel("–")
        self._output_dur.setStyleSheet(
            f"color: {T.get('text','#1E2130')}; font-size: 12px; font-weight: 600;"
            f" font-family: '{MONO_FAMILY}';"
        )
        sum_lay.addWidget(self._output_dur)
        self._cut_summary = QLabel()
        self._cut_summary.setStyleSheet(f"color: {T.get('text3','#8B90A8')}; font-size: 12px;")
        sum_lay.addWidget(self._cut_summary)
        sum_lay.addStretch()
        lay.addWidget(self._summary_frame)

        # Cut list
        self._cut_list_label = QLabel("REMOVED SECTIONS")
        self._cut_list_label.setStyleSheet(
            f"color: {T.get('text3','#8B90A8')}; font-size: 10px; font-weight: 700;"
            f" letter-spacing: 0.08em;"
        )
        lay.addWidget(self._cut_list_label)
        self._cut_list_label.hide()

        self._cut_list_widget = QWidget()
        self._cut_list_widget.setStyleSheet("background: transparent;")
        self._cut_list_lay = QVBoxLayout(self._cut_list_widget)
        self._cut_list_lay.setContentsMargins(0, 0, 0, 0)
        self._cut_list_lay.setSpacing(5)
        lay.addWidget(self._cut_list_widget)
        self._cut_list_widget.hide()
        lay.addStretch()

        scroll.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def _legend_item(self, color: str, text: str, thickness: int,
                     hatch: bool = False) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)
        swatch = QLabel()
        swatch.setFixedSize(16, max(2, thickness))
        swatch.setStyleSheet(f"background: {color};")
        lay.addWidget(swatch)
        lbl = QLabel(text)
        lbl.setObjectName("text")
        lbl.setStyleSheet(f"color: {T.get('text3','#8B90A8')}; font-size: 11px;")
        lay.addWidget(lbl)
        return w

    def _make_slider(self, label: str, parent_lay: QHBoxLayout):
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)
        hdr = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setStyleSheet(f"color: {T.get('text2','#5B6080')}; font-size: 11px;")
        val = QLabel("0:00")
        val.setStyleSheet(
            f"color: {T.get('text','#1E2130')}; font-size: 11px; font-weight: 500;"
            f" font-family: '{MONO_FAMILY}';"
        )
        hdr.addWidget(lbl)
        hdr.addStretch()
        hdr.addWidget(val)
        slider = QSlider(Qt.Orientation.Horizontal)
        lay.addLayout(hdr)
        lay.addWidget(slider)
        parent_lay.addWidget(container, 1)
        return slider, val

    def refresh(self) -> None:
        """Called when files change or overlap recomputed."""
        self._state.trim_start_pct = 0.0
        self._state.trim_end_pct = 100.0
        self._state.cuts = []
        self._start_slider.setValue(0)
        self._end_slider.setValue(100)
        self._rebuild_waveform_data()
        self._rebuild_cut_list()
        self._update_summary()

    def _rebuild_waveform_data(self) -> None:
        power: List[float] = []
        hr:    List[float] = []

        if self._state.base_samples:
            for _, flds in self._state.base_samples[::max(1, len(self._state.base_samples)//200)]:
                power.append(flds.get("pwr", 0.0))
                hr.append(flds.get("hr", 0.0))

        # If HR not in base, check secondary files
        if not any(v > 0 for v in hr):
            for fid, records in self._state.extra_fit.items():
                for r in records[::max(1, len(records)//200)]:
                    hr_val = r.get("heart_rate")
                    if hr_val:
                        hr.append(float(hr_val))
                if any(v > 0 for v in hr):
                    break

        self._waveform.set_data(power, hr)
        self._waveform.set_trim(self._state.trim_start_pct, self._state.trim_end_pct)
        self._waveform.set_cuts(self._state.cuts)

        self._hr_legend.setVisible(any(v > 0 for v in hr))
        self._pwr_legend.setVisible(any(v > 0 for v in power))

    def _on_start_changed(self, val: int) -> None:
        if val >= self._end_slider.value():
            self._start_slider.setValue(self._end_slider.value() - 1)
            return
        self._state.trim_start_pct = float(val)
        self._update_slider_labels()
        self._waveform.set_trim(self._state.trim_start_pct, self._state.trim_end_pct)
        self._update_summary()
        self.state_changed.emit()

    def _on_end_changed(self, val: int) -> None:
        if val <= self._start_slider.value():
            self._end_slider.setValue(self._start_slider.value() + 1)
            return
        self._state.trim_end_pct = float(val)
        self._update_slider_labels()
        self._waveform.set_trim(self._state.trim_start_pct, self._state.trim_end_pct)
        self._update_summary()
        self.state_changed.emit()

    def _update_slider_labels(self) -> None:
        dur = self._state.total_duration_sec
        start_sec = int(self._state.trim_start_pct / 100 * dur)
        end_sec   = int(self._state.trim_end_pct   / 100 * dur)
        self._start_val.setText(_fmt_time(start_sec))
        self._end_val.setText(_fmt_time(end_sec))

    def _on_cut_added(self, cut_id: str, start_pct: float, end_pct: float) -> None:
        self._state.cuts.append((cut_id, start_pct, end_pct))
        self._waveform.set_cuts(self._state.cuts)
        self._rebuild_cut_list()
        self._update_summary()
        self.state_changed.emit()

    def _rebuild_cut_list(self) -> None:
        while self._cut_list_lay.count():
            item = self._cut_list_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        has_cuts = bool(self._state.cuts)
        self._cut_list_label.setVisible(has_cuts)
        self._cut_list_widget.setVisible(has_cuts)
        self._cuts_legend.setVisible(has_cuts)
        if has_cuts:
            lbl = self._cuts_legend.findChild(QLabel, "text")
            if lbl:
                n = len(self._state.cuts)
                lbl.setText(f"{n} cut{'s' if n != 1 else ''}")

        dur = self._state.total_duration_sec
        for cut_id, s, e in self._state.cuts:
            start_sec = int(s / 100 * dur)
            end_sec   = int(e / 100 * dur)
            removed   = end_sec - start_sec
            row = self._make_cut_row(cut_id, start_sec, end_sec, removed)
            self._cut_list_lay.addWidget(row)

    def _make_cut_row(self, cut_id: str, start_sec: int,
                       end_sec: int, removed: int) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame {{ background: {T.get('surface','#FEFEFF')};"
            f" border: 1px solid {T.get('border','#DEE0E8')}; border-radius: 6px; }}"
        )
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(10)

        time_lbl = QLabel(f"{_fmt_time(start_sec)} – {_fmt_time(end_sec)}")
        time_lbl.setStyleSheet(
            f"color: {T.get('text','#1E2130')}; font-size: 12px;"
            f" font-family: '{MONO_FAMILY}';"
        )
        lay.addWidget(time_lbl, 1)

        rem_lbl = QLabel(f"{_fmt_time(removed)} removed")
        rem_lbl.setStyleSheet(f"color: {T.get('text3','#8B90A8')}; font-size: 11px;")
        lay.addWidget(rem_lbl)

        del_btn = QPushButton("×")
        del_btn.setFixedSize(22, 22)
        del_btn.setStyleSheet(
            f"QPushButton {{ background: none; border: none;"
            f" color: {T.get('text3','#8B90A8')}; font-size: 16px; }}"
            f"QPushButton:hover {{ color: {T.get('text','#1E2130')}; }}"
        )
        del_btn.clicked.connect(lambda: self._remove_cut(cut_id))
        lay.addWidget(del_btn)
        return frame

    def _remove_cut(self, cut_id: str) -> None:
        self._state.cuts = [(cid, s, e) for cid, s, e in self._state.cuts if cid != cut_id]
        self._waveform.set_cuts(self._state.cuts)
        self._rebuild_cut_list()
        self._update_summary()
        self.state_changed.emit()

    def _update_summary(self) -> None:
        dur = self._state.total_duration_sec
        active = dur * (self._state.trim_end_pct - self._state.trim_start_pct) / 100.0
        cut_sec = sum((e - s) / 100.0 * dur for _, s, e in self._state.cuts)
        output_sec = max(0, active - cut_sec)
        self._output_dur.setText(_fmt_time(int(output_sec)))
        if self._state.cuts:
            self._cut_summary.setText(
                f"· {len(self._state.cuts)} section"
                f"{'s' if len(self._state.cuts) != 1 else ''} cut"
                f" (−{_fmt_time(int(cut_sec))})"
            )
        else:
            self._cut_summary.setText("")


# ---------------------------------------------------------------------------
# Step 4 — Export
# ---------------------------------------------------------------------------

class ExportStep(QWidget):
    def __init__(self, state: AppState, dark: bool = False):
        super().__init__()
        self._state = state
        self._dark = dark
        self._merge_state = "idle"  # idle | working | done

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(22, 22, 22, 22)
        lay.setSpacing(16)

        # Header
        h2 = QLabel("Export merged file")
        h2.setStyleSheet(
            f"color: {T.get('text','#1E2130')}; font-size: 17px; font-weight: 600;"
        )
        body = QLabel(
            "Review your choices, name the file, and export a merged <b>.fit</b>."
        )
        body.setStyleSheet(f"color: {T.get('text2','#5B6080')}; font-size: 13px;")
        lay.addWidget(h2)
        lay.addWidget(body)

        # Waveform preview (read-only)
        self._waveform = WaveformWidget(interactive=False, height=72)
        preview_card = QFrame()
        preview_card.setStyleSheet(
            f"QFrame {{ background: {T.get('surface2','#ECEEF3')};"
            f" border: 1px solid {T.get('border','#DEE0E8')}; border-radius: 6px; }}"
        )
        pv_lay = QVBoxLayout(preview_card)
        pv_lay.setContentsMargins(0, 0, 0, 0)
        pv_lay.setSpacing(0)

        pv_hdr = QFrame()
        pv_hdr.setStyleSheet(
            f"QFrame {{ border-bottom: 1px solid {T.get('border','#DEE0E8')}; border-radius: 0; }}"
        )
        pv_hdr_lay = QHBoxLayout(pv_hdr)
        pv_hdr_lay.setContentsMargins(12, 7, 12, 7)
        out_lbl = QLabel("OUTPUT PREVIEW")
        out_lbl.setStyleSheet(
            f"color: {T.get('text3','#8B90A8')}; font-size: 10px; font-weight: 700;"
            f" letter-spacing: 0.08em;"
        )
        pv_hdr_lay.addWidget(out_lbl)
        pv_hdr_lay.addStretch()
        pv_lay.addWidget(pv_hdr)
        pv_lay.addWidget(self._waveform)
        lay.addWidget(preview_card)

        # Summary card
        summary_card = QFrame()
        summary_card.setStyleSheet(
            f"QFrame {{ background: {T.get('surface','#FEFEFF')};"
            f" border: 1px solid {T.get('border','#DEE0E8')}; border-radius: 10px; }}"
        )
        sc_lay = QVBoxLayout(summary_card)
        sc_lay.setContentsMargins(0, 0, 0, 0)
        sc_lay.setSpacing(0)

        # Two-column: source files + fields in output
        cols_widget = QWidget()
        cols_lay = QHBoxLayout(cols_widget)
        cols_lay.setContentsMargins(0, 0, 0, 0)
        cols_lay.setSpacing(0)

        # Left col — source files
        self._src_col = QWidget()
        src_col_lay = QVBoxLayout(self._src_col)
        src_col_lay.setContentsMargins(16, 14, 16, 14)
        src_col_lay.setSpacing(6)
        src_hdr = QLabel("SOURCE FILES")
        src_hdr.setStyleSheet(
            f"color: {T.get('text3','#8B90A8')}; font-size: 10px; font-weight: 700;"
            f" letter-spacing: 0.07em;"
        )
        src_col_lay.addWidget(src_hdr)
        self._src_list_lay = QVBoxLayout()
        self._src_list_lay.setSpacing(5)
        src_col_lay.addLayout(self._src_list_lay)
        cols_lay.addWidget(self._src_col, 1)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(f"color: {T.get('border','#DEE0E8')}; max-width: 1px;")
        cols_lay.addWidget(sep)

        # Right col — fields
        self._fld_col = QWidget()
        fld_col_lay = QVBoxLayout(self._fld_col)
        fld_col_lay.setContentsMargins(16, 14, 16, 14)
        fld_col_lay.setSpacing(6)
        fld_hdr = QLabel("FIELDS IN OUTPUT")
        fld_hdr.setStyleSheet(
            f"color: {T.get('text3','#8B90A8')}; font-size: 10px; font-weight: 700;"
            f" letter-spacing: 0.07em;"
        )
        fld_col_lay.addWidget(fld_hdr)
        self._fields_flow = QWidget()
        self._fields_flow.setStyleSheet("background: transparent;")
        self._fields_flow_lay = _FlowLayout(self._fields_flow, h_spacing=5, v_spacing=5)
        fld_col_lay.addWidget(self._fields_flow)
        cols_lay.addWidget(self._fld_col, 1)
        sc_lay.addWidget(cols_widget)

        # Filename row
        fn_frame = QFrame()
        fn_frame.setStyleSheet(
            f"QFrame {{ border-top: 1px solid {T.get('border','#DEE0E8')}; border-radius: 0; }}"
        )
        fn_lay = QHBoxLayout(fn_frame)
        fn_lay.setContentsMargins(16, 14, 16, 14)
        fn_lay.setSpacing(8)
        fn_lbl = QLabel("Output filename")
        fn_lbl.setStyleSheet(
            f"color: {T.get('text2','#5B6080')}; font-size: 11px; font-weight: 600;"
        )
        fn_lay.addWidget(fn_lbl)
        self._filename_edit = QLineEdit()
        self._filename_edit.setStyleSheet(
            f"QLineEdit {{ background: {T.get('surface2','#ECEEF3')};"
            f" border: 1px solid {T.get('border2','#C2C5D4')};"
            f" border-right: none; border-radius: 6px 0 0 6px; padding: 8px 11px;"
            f" font-family: '{MONO_FAMILY}'; font-size: 13px; }}"
        )
        self._filename_edit.textChanged.connect(self._on_filename_changed)
        fn_lay.addWidget(self._filename_edit, 1)
        ext_lbl = QLabel(".fit")
        ext_lbl.setStyleSheet(
            f"QLabel {{ background: {T.get('surface3','#E4E6ED')};"
            f" color: {T.get('text3','#8B90A8')};"
            f" border: 1px solid {T.get('border2','#C2C5D4')};"
            f" border-radius: 0 6px 6px 0; padding: 8px 11px;"
            f" font-family: '{MONO_FAMILY}'; font-size: 13px; }}"
        )
        fn_lay.addWidget(ext_lbl)
        sc_lay.addWidget(fn_frame)

        # Actions row
        act_frame = QFrame()
        act_frame.setStyleSheet(
            f"QFrame {{ border-top: 1px solid {T.get('border','#DEE0E8')}; border-radius: 0; }}"
        )
        act_lay = QHBoxLayout(act_frame)
        act_lay.setContentsMargins(16, 14, 16, 14)
        act_lay.setSpacing(9)

        self._merge_btn = QPushButton("Merge & Save")
        self._merge_btn.clicked.connect(self._do_merge)
        self._update_merge_btn_style()
        act_lay.addWidget(self._merge_btn, 1)

        strava_btn = QPushButton("🟠  Upload to Strava")
        strava_btn.setEnabled(False)
        strava_btn.setStyleSheet(
            f"QPushButton {{ background: {T.get('surface2','#ECEEF3')};"
            f" color: {T.get('text3','#8B90A8')};"
            f" border: 1px solid {T.get('border','#DEE0E8')};"
            f" border-radius: 6px; padding: 11px 14px; font-size: 13px; }}"
        )
        act_lay.addWidget(strava_btn)
        sc_lay.addWidget(act_frame)
        lay.addWidget(summary_card)
        lay.addStretch()

        scroll.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def refresh(self) -> None:
        self._merge_state = "idle"
        self._update_merge_btn_style()
        self._filename_edit.setText(self._state.output_filename)
        self._rebuild_source_list()
        self._rebuild_fields()
        self._rebuild_waveform()

    def refresh_waveform(self) -> None:
        self._rebuild_waveform()

    def _rebuild_source_list(self) -> None:
        while self._src_list_lay.count():
            item = self._src_list_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        dark = _detect_dark()
        for wf in self._state.files:
            row = QHBoxLayout()
            dot = QLabel("●")
            dot.setStyleSheet(
                f"color: {_file_color(wf.color_index, dark)}; font-size: 8px;"
            )
            row.addWidget(dot)
            row.addWidget(FormatBadge(wf.file_type))
            name = QLabel(wf.path.name)
            name.setStyleSheet(
                f"color: {T.get('text2','#5B6080')}; font-size: 11px;"
                f" font-family: '{MONO_FAMILY}';"
            )
            row.addWidget(name, 1)
            container = QWidget()
            container.setStyleSheet("background: transparent;")
            container.setLayout(row)
            self._src_list_lay.addWidget(container)

    def _rebuild_fields(self) -> None:
        while self._fields_flow_lay.count():
            item = self._fields_flow_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for fid in FIELD_ORDER:
            label, unit, icon = FIELD_DISPLAY.get(fid, (fid, "", ""))
            choice = self._state.field_choices.get(fid)
            if choice is None:
                pill = QLabel(f"{icon} {label}")
                pill.setStyleSheet(
                    f"QLabel {{ background: {T.get('bad_bg','#EEF0F5')};"
                    f" color: {T.get('text3','#8B90A8')}; border-radius: 99px;"
                    f" padding: 3px 8px; font-size: 11px; text-decoration: line-through; }}"
                )
            else:
                wf = next((f for f in self._state.files if f.id == choice), None)
                fmt = wf.file_type.upper() if wf else "?"
                pill = QLabel(f"{icon} {label} ({fmt})")
                pill.setStyleSheet(
                    f"QLabel {{ background: {T.get('good_bg','#EAF5EF')};"
                    f" color: {T.get('good','#2E7A4F')}; border-radius: 99px;"
                    f" padding: 3px 8px; font-size: 11px; font-weight: 500; }}"
                )
            self._fields_flow_lay.addWidget(pill)

    def _rebuild_waveform(self) -> None:
        power: List[float] = []
        hr:    List[float] = []
        if self._state.base_samples:
            stride = max(1, len(self._state.base_samples) // 200)
            for _, flds in self._state.base_samples[::stride]:
                power.append(flds.get("pwr", 0.0))
                hr.append(flds.get("hr", 0.0))
        if not any(v > 0 for v in hr):
            for records in self._state.extra_fit.values():
                stride = max(1, len(records) // 200)
                for r in records[::stride]:
                    v = r.get("heart_rate")
                    if v:
                        hr.append(float(v))
                if any(v > 0 for v in hr):
                    break
        self._waveform.set_data(power, hr)
        self._waveform.set_trim(self._state.trim_start_pct, self._state.trim_end_pct)
        self._waveform.set_cuts(self._state.cuts)

    def _on_filename_changed(self, text: str) -> None:
        safe = re.sub(r"[^\w\-. ]", "", text).strip()
        self._state.output_filename = safe
        if self._merge_state == "idle":
            self._update_merge_btn_style()

    def _update_merge_btn_style(self) -> None:
        name = self._state.output_filename or "merged_workout"
        if self._merge_state == "done":
            bg = T.get("good", "#2E7A4F")
            text = f"✓  Saved — {name}.fit"
        elif self._merge_state == "working":
            bg = T.get("accent", "#4A55C0")
            text = "Merging…"
        else:
            bg = T.get("accent", "#4A55C0")
            text = f"Merge & Save  {name}.fit"

        self._merge_btn.setText(text)
        self._merge_btn.setEnabled(self._merge_state == "idle")
        opacity = "0.75" if self._merge_state == "working" else "1"
        self._merge_btn.setStyleSheet(
            f"QPushButton {{ background: {bg}; color: {T.get('accent_fg','#FFFFFF')};"
            f" border: none; border-radius: 6px; padding: 11px 20px;"
            f" font-size: 14px; font-weight: 600; opacity: {opacity}; }}"
            f"QPushButton:disabled {{ background: {bg}; color: {T.get('accent_fg','#FFFFFF')}; }}"
        )

    def _do_merge(self) -> None:
        if not self._state.has_overlap:
            QMessageBox.warning(self, "Not ready", "Load at least two files with overlapping data.")
            return

        choices = self._state.field_choices
        active_choices = {f: fid for f, fid in choices.items() if fid is not None}
        if not active_choices:
            QMessageBox.warning(self, "Nothing to merge", "All fields are excluded.")
            return

        stem = re.sub(r"[^\w\-. ]", "", self._state.output_filename).strip() or "merged_workout"
        save_path, _ = QFileDialog.getSaveFileName(
            self, "Save merged FIT file", f"{stem}.fit", "FIT files (*.fit)"
        )
        if not save_path:
            return

        self._merge_state = "working"
        self._update_merge_btn_style()
        QApplication.processEvents()

        try:
            dur = self._state.total_duration_sec
            base = merger.apply_trim_and_cuts(
                self._state.base_samples,
                dur,
                self._state.trim_start_pct,
                self._state.trim_end_pct,
                [(s, e) for _, s, e in self._state.cuts],
            )
            merged = merger.build_merged_samples_multi(
                base,
                self._state.extra_fit,
                self._state.base_start_utc,
                self._state.base_file_id,
                choices,
            )
            fit_core.write(merged, self._state.base_start_utc, Path(save_path))
            self._merge_state = "done"
        except Exception as e:
            self._merge_state = "idle"
            QMessageBox.critical(self, "Merge failed", str(e))
        finally:
            self._update_merge_btn_style()


# ---------------------------------------------------------------------------
# Step bar + Nav bar
# ---------------------------------------------------------------------------

STEPS = [
    ("files",  "Files",  "01"),
    ("fields", "Fields", "02"),
    ("trim",   "Trim",   "03"),
    ("export", "Export", "04"),
]


class StepBar(QWidget):
    step_clicked = Signal(str)

    def __init__(self):
        super().__init__()
        self.setFixedHeight(56)
        self.setStyleSheet(
            f"background: {T.get('surface','#FEFEFF')};"
            f" border-bottom: 1px solid {T.get('border','#DEE0E8')};"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._buttons: Dict[str, QPushButton] = {}
        for step_id, label, num in STEPS:
            btn = QPushButton()
            btn.setFlat(True)
            btn.setProperty("step_id", step_id)
            btn.setProperty("num", num)
            btn.setProperty("label", label)
            btn.clicked.connect(lambda _, sid=step_id: self.step_clicked.emit(sid))
            self._buttons[step_id] = btn
            lay.addWidget(btn, 1)

        self._active = "files"
        self._completed: set = set()
        self._can_advance: bool = False
        self._update_styles()

    def set_active(self, step_id: str, completed: set, can_advance: bool = True) -> None:
        self._active = step_id
        self._completed = completed
        self._can_advance = can_advance
        self._update_styles()

    def _update_styles(self) -> None:
        for step_id, btn in self._buttons.items():
            num = btn.property("num")
            label = btn.property("label")
            is_active = step_id == self._active
            is_done = step_id in self._completed
            is_locked = not self._can_advance and not is_active and not is_done

            num_text = "✓" if is_done else num
            if is_active:
                col = T.get("accent", "#4A55C0")
            elif is_locked:
                col = T.get("border2", "#C2C5D4")
            elif is_done:
                col = T.get("text2", "#5B6080")
            else:
                col = T.get("text3", "#8B90A8")
            border = f"border-bottom: 2px solid {T.get('accent','#4A55C0')};" if is_active else "border-bottom: 2px solid transparent;"
            wt = "600" if is_active else "500"
            btn.setText(f"{num_text}\n{label}")
            btn.setStyleSheet(
                f"QPushButton {{ background: none; {border} color: {col};"
                f" font-size: 12px; font-weight: {wt}; padding: 10px 0 8px 0; }}"
            )
            btn.setCursor(
                Qt.CursorShape.ArrowCursor if is_locked else Qt.CursorShape.PointingHandCursor
            )


class NavBar(QWidget):
    back_clicked     = Signal()
    continue_clicked = Signal()

    def __init__(self):
        super().__init__()
        self.setFixedHeight(44)
        self.setStyleSheet(
            f"background: {T.get('surface','#FEFEFF')};"
            f" border-top: 1px solid {T.get('border','#DEE0E8')};"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(22, 0, 22, 0)

        self._back_btn = QPushButton("← Back")
        self._back_btn.setStyleSheet(
            f"QPushButton {{ background: none;"
            f" border: 1px solid {T.get('border2','#C2C5D4')}; border-radius: 6px;"
            f" color: {T.get('text','#1E2130')}; font-size: 12px; font-weight: 500;"
            f" padding: 7px 16px; }}"
            f"QPushButton:disabled {{ opacity: 0.38; }}"
        )
        self._back_btn.clicked.connect(self.back_clicked)
        lay.addWidget(self._back_btn)
        lay.addStretch()

        self._cont_btn = QPushButton("Continue →")
        self._cont_btn.setStyleSheet(
            f"QPushButton {{ background: {T.get('accent','#4A55C0')};"
            f" color: {T.get('accent_fg','#FFFFFF')}; border: none; border-radius: 6px;"
            f" font-size: 12px; font-weight: 600; padding: 7px 18px; }}"
        )
        self._cont_btn.clicked.connect(self.continue_clicked)
        lay.addWidget(self._cont_btn)

    def update_for_step(self, idx: int, can_advance: bool = True) -> None:
        self._back_btn.setEnabled(idx > 0)
        self._back_btn.setStyleSheet(
            f"QPushButton {{ background: none;"
            f" border: 1px solid {T.get('border2','#C2C5D4')}; border-radius: 6px;"
            f" color: {T.get('text','#1E2130') if idx > 0 else T.get('text3','#8B90A8')};"
            f" font-size: 12px; font-weight: 500; padding: 7px 16px; }}"
            f"QPushButton:disabled {{ opacity: 0.38; }}"
        )
        is_last = idx >= len(STEPS) - 1
        self._cont_btn.setVisible(not is_last)
        self._cont_btn.setEnabled(can_advance)
        bg = T.get("accent", "#4A55C0") if can_advance else T.get("surface3", "#E4E6ED")
        fg = T.get("accent_fg", "#FFFFFF") if can_advance else T.get("text3", "#8B90A8")
        self._cont_btn.setStyleSheet(
            f"QPushButton {{ background: {bg}; color: {fg}; border: none;"
            f" border-radius: 6px; font-size: 12px; font-weight: 600; padding: 7px 18px; }}"
            f"QPushButton:disabled {{ background: {bg}; color: {fg}; }}"
        )


# ---------------------------------------------------------------------------
# Flow layout (wrapping chip layout)
# ---------------------------------------------------------------------------

class _FlowLayout(QVBoxLayout):
    """Simple wrapping layout — uses fixed rows of QHBoxLayout."""

    def __init__(self, parent: QWidget, h_spacing: int = 6, v_spacing: int = 6):
        super().__init__(parent)
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(v_spacing)
        self._h_spacing = h_spacing
        self._current_row: Optional[QHBoxLayout] = None
        self._new_row()

    def _new_row(self) -> None:
        self._current_row = QHBoxLayout()
        self._current_row.setContentsMargins(0, 0, 0, 0)
        self._current_row.setSpacing(self._h_spacing)
        self.addLayout(self._current_row)

    def addWidget(self, widget) -> None:  # type: ignore[override]
        if self._current_row is None:
            self._new_row()
        self._current_row.addWidget(widget)

    def count(self) -> int:
        total = 0
        for i in range(super().count()):
            item = super().itemAt(i)
            if item and item.layout():
                total += item.layout().count()
        return total

    def takeAt(self, index: int):
        flat = []
        for i in range(super().count()):
            item = super().itemAt(i)
            if item and item.layout():
                row = item.layout()
                for j in range(row.count()):
                    flat.append((row, j, row.itemAt(j)))
        if index < len(flat):
            row, j, item = flat[index]
            row.takeAt(j)
            return item
        return None


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, dark: bool = False):
        super().__init__()
        self._dark = dark
        self.setWindowTitle("fitfilemaker")
        self.setMinimumSize(1060, 680)
        self.resize(1160, 740)

        self._state = AppState()
        self._step_idx = 0

        central = QWidget()
        central.setStyleSheet(f"background: {T.get('bg','#F4F5F8')};")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Step bar
        self._step_bar = StepBar()
        self._step_bar.step_clicked.connect(self._go_to_step_id)
        root.addWidget(self._step_bar)

        # Stacked pages
        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        # Instantiate steps
        self._files_step   = FilesStep(self._state, dark)
        self._fields_step  = FieldsStep(self._state, dark)
        self._trim_step    = TrimStep(self._state)
        self._export_step  = ExportStep(self._state, dark)

        self._files_step.files_changed.connect(self._on_files_changed)
        self._trim_step.state_changed.connect(self._on_trim_changed)

        self._stack.addWidget(self._files_step)    # 0
        self._stack.addWidget(self._fields_step)   # 1
        self._stack.addWidget(self._trim_step)     # 2
        self._stack.addWidget(self._export_step)   # 3

        # Nav bar
        self._nav_bar = NavBar()
        self._nav_bar.back_clicked.connect(self._go_back)
        self._nav_bar.continue_clicked.connect(self._go_forward)
        root.addWidget(self._nav_bar)

        self._update_chrome()

        # Dark mode watcher
        QApplication.styleHints().colorSchemeChanged.connect(self._on_scheme_changed)

    # ── Navigation ───────────────────────────────────────────────────

    def _go_to_step_id(self, step_id: str) -> None:
        if step_id != "files" and not self._state.files:
            return
        idx = next((i for i, (sid, _, _) in enumerate(STEPS) if sid == step_id), 0)
        self._go_to_index(idx)

    def _go_back(self) -> None:
        self._go_to_index(self._step_idx - 1)

    def _go_forward(self) -> None:
        if not self._state.files:
            return
        self._go_to_index(self._step_idx + 1)

    def _go_to_index(self, idx: int) -> None:
        if idx > 0 and not self._state.files:
            idx = 0
        idx = max(0, min(len(STEPS) - 1, idx))
        leaving = self._step_idx
        self._step_idx = idx
        self._stack.setCurrentIndex(idx)

        # Refresh the step we're entering if needed
        step_id = STEPS[idx][0]
        if step_id == "fields" and leaving != idx:
            self._fields_step.refresh()
        elif step_id == "trim" and leaving != idx:
            self._trim_step.refresh()
        elif step_id == "export":
            self._export_step.refresh()

        self._update_chrome()

    def _update_chrome(self) -> None:
        can_advance = bool(self._state.files)
        completed = set(STEPS[i][0] for i in range(self._step_idx))
        self._step_bar.set_active(STEPS[self._step_idx][0], completed, can_advance)
        self._nav_bar.update_for_step(self._step_idx, can_advance)

    # ── State updates ────────────────────────────────────────────────

    def _on_files_changed(self) -> None:
        _recompute_overlap(self._state)
        _recompute_recommendations(self._state)
        for fid in FIELD_ORDER:
            self._state.field_choices[fid] = self._state.recommendations.get(fid)
        self._trim_step.refresh()
        self._files_step.refresh()
        # If all files removed while on a later step, return to Files
        if not self._state.files and self._step_idx > 0:
            self._go_to_index(0)
        else:
            self._update_chrome()

    def _on_trim_changed(self) -> None:
        self._export_step.refresh_waveform()

    # ── Dark mode ────────────────────────────────────────────────────

    def _on_scheme_changed(self) -> None:
        dark = _detect_dark()
        self._dark = dark
        _apply_tokens(dark)
        QApplication.instance().setStyleSheet(_build_qss(dark))
        # Rebuild UI (simplest approach: re-show triggers repaint)
        self.update()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("fitfilemaker")
    app.setStyle("Fusion")

    _load_fonts()

    dark = _detect_dark()
    _apply_tokens(dark)
    app.setStyleSheet(_build_qss(dark))

    font = QFont(SANS_FAMILY)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(font)

    window = MainWindow(dark)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
