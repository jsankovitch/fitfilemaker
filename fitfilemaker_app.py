#!/usr/bin/env python3
"""
fitfilemaker — macOS GUI for merging workout files (.fit / .pwx).

Usage:
    source .venv/bin/activate
    python3 fitfilemaker_app.py

Third-party dependencies — see NOTICE file:
    PySide6   LGPL-3.0    Qt for Python
    fitparse  MIT         FIT file parsing
    fit-tool  BSD-3       FIT file writing
"""

import sys
from pathlib import Path

# Project root on path so app.core.* imports work
sys.path.insert(0, str(Path(__file__).parent))

from datetime import timedelta

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QFileDialog, QFrame,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPushButton, QRadioButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from app.core import fit as fit_core
from app.core import merger
from app.core import pwx as pwx_core
from app.core.fit import FIT_TO_PWX
from app.core.security import detect_and_validate

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FIELD_DISPLAY = {
    "hr":   "Heart Rate",
    "pwr":  "Power",
    "cad":  "Cadence",
    "spd":  "Speed",
    "dist": "Distance",
    "alt":  "Altitude",
}

ACCENT  = "#0066CC"
BG_CARD = "#F5F5F7"


# ---------------------------------------------------------------------------
# Helpers — file loading
# ---------------------------------------------------------------------------

def load_file(path: Path) -> tuple[str, list, object]:
    """
    Load a PWX or FIT file.  Returns (file_type, samples, meta).

    samples  — list of (offset_seconds, {pwx_field: float})
    meta     — for PWX: (ElementTree, start_local)
                for FIT: start_utc (datetime)
    """
    data = path.read_bytes()
    file_type = detect_and_validate(data, path.name)

    if file_type == "pwx":
        tree, start_local, samples = pwx_core.parse(path)
        device_make = _pwx_device_make(tree)
        return file_type, samples, (tree, start_local, device_make)

    else:  # fit
        records = fit_core.parse(path)
        start_utc = records[0]["timestamp"]
        samples = _fit_records_to_samples(records, start_utc)
        return file_type, samples, start_utc


def _pwx_device_make(tree) -> str | None:
    import xml.etree.ElementTree as ET
    NS = {"p": "http://www.peaksware.com/PWX/1/0"}
    workout = tree.getroot().find("p:workout", NS)
    if workout is None:
        return None
    device = workout.find("p:device", NS)
    if device is None:
        return None
    make = device.find("p:make", NS)
    return make.text if make is not None else None


def _fit_records_to_samples(records: list, start_utc) -> list:
    """Convert FIT records (absolute timestamps) to (offset_sec, {pwx_field: val})."""
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


def samples_start_utc(file_type: str, meta) -> object:
    """Return the start UTC datetime for a loaded file."""
    if file_type == "pwx":
        _, start_local, _ = meta
        return pwx_core.local_to_utc(start_local, pwx_core.system_utc_offset())
    else:
        return meta  # already a UTC datetime


def samples_device_make(file_type: str, meta) -> str | None:
    if file_type == "pwx":
        _, _, device_make = meta
        return device_make
    return None


# ---------------------------------------------------------------------------
# Field row widget
# ---------------------------------------------------------------------------

class FieldRow(QWidget):
    """One row in the field table: label | ○ File 1 | ○ File 2 | □ Exclude."""

    def __init__(self, field: str, in_1: bool, in_2: bool, recommended: str, reason: str):
        super().__init__()
        self.field       = field
        self.recommended = recommended

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        # Field label
        lbl = QLabel(FIELD_DISPLAY.get(field, field))
        lbl.setMinimumWidth(110)
        lbl.setFont(QFont(lbl.font().family(), 13))
        layout.addWidget(lbl)

        # Radio buttons
        self._group = QButtonGroup(self)
        self.radio1 = QRadioButton("File 1")
        self.radio2 = QRadioButton("File 2")
        self._group.addButton(self.radio1, 1)
        self._group.addButton(self.radio2, 2)

        self.radio1.setEnabled(in_1)
        self.radio2.setEnabled(in_2)

        # Pre-select recommended source
        if recommended == "a" and in_1:
            self.radio1.setChecked(True)
        elif recommended == "b" and in_2:
            self.radio2.setChecked(True)
        elif in_1:
            self.radio1.setChecked(True)
        elif in_2:
            self.radio2.setChecked(True)

        layout.addWidget(self.radio1)
        layout.addSpacing(16)
        layout.addWidget(self.radio2)
        layout.addSpacing(24)

        # Exclude checkbox
        self.exclude = QCheckBox("Exclude")
        self.exclude.toggled.connect(self._on_exclude)
        layout.addWidget(self.exclude)

        layout.addStretch()

        # Recommendation hint
        hint = QLabel(f"  ★ {reason}")
        hint.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(hint)

    def _on_exclude(self, checked: bool):
        self.radio1.setEnabled(not checked and self._original_in_1)
        self.radio2.setEnabled(not checked and self._original_in_2)

    def showEvent(self, event):
        # Capture original enabled state after layout
        self._original_in_1 = self.radio1.isEnabled() or self.exclude.isChecked()
        self._original_in_2 = self.radio2.isEnabled() or self.exclude.isChecked()
        super().showEvent(event)

    def source(self) -> str | None:
        """Return 'a', 'b', or None (excluded)."""
        if self.exclude.isChecked():
            return None
        if self.radio1.isChecked():
            return "a"
        if self.radio2.isChecked():
            return "b"
        return None


# ---------------------------------------------------------------------------
# File panel widget
# ---------------------------------------------------------------------------

class FilePanel(QGroupBox):
    def __init__(self, label: str):
        super().__init__(label)
        layout = QVBoxLayout(self)

        self.btn = QPushButton("Browse…")
        self.btn.setFixedWidth(100)
        layout.addWidget(self.btn)

        self.status = QLabel("No file selected")
        self.status.setStyleSheet("color: #888; font-size: 12px;")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.path       = None
        self.file_type  = None
        self.samples    = None
        self.meta       = None

    def set_loaded(self, path: Path, file_type: str, samples: list, meta):
        self.path      = path
        self.file_type = file_type
        self.samples   = samples
        self.meta      = meta

        note = " (converted to FIT internally)" if file_type == "pwx" else ""
        self.status.setText(
            f"<b>{path.name}</b><br>"
            f"<span style='color:#0066CC'>{file_type.upper()}{note}</span><br>"
            f"{len(samples)} samples"
        )

    def clear(self):
        self.path = self.file_type = self.samples = self.meta = None
        self.status.setText("No file selected")


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("fitfilemaker")
        self.setMinimumWidth(700)
        self._field_rows: list[FieldRow] = []

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(16)
        root.setContentsMargins(20, 20, 20, 20)

        # Title
        title = QLabel("fitfilemaker")
        title.setFont(QFont(title.font().family(), 22, QFont.Weight.Bold))
        subtitle = QLabel("Select two workout files, choose which data to keep, and merge.")
        subtitle.setStyleSheet("color: #888;")
        root.addWidget(title)
        root.addWidget(subtitle)

        root.addWidget(self._make_separator())

        # File panels
        panels_row = QHBoxLayout()
        self.panel1 = FilePanel("File 1")
        self.panel2 = FilePanel("File 2")
        self.panel1.btn.clicked.connect(lambda: self._browse(self.panel1))
        self.panel2.btn.clicked.connect(lambda: self._browse(self.panel2))
        panels_row.addWidget(self.panel1)
        panels_row.addWidget(self.panel2)
        root.addLayout(panels_row)

        root.addWidget(self._make_separator())

        # Field table (hidden until both files loaded)
        self.field_area_label = QLabel("FIELD SELECTION")
        self.field_area_label.setStyleSheet(
            "color: #555; font-size: 11px; font-weight: bold; letter-spacing: 1px;"
        )
        self.field_area_label.hide()
        root.addWidget(self.field_area_label)

        # Column headers
        self.field_header = self._make_field_header()
        self.field_header.hide()
        root.addWidget(self.field_header)

        # Scroll area for field rows
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.hide()
        self.field_container = QWidget()
        self.field_layout    = QVBoxLayout(self.field_container)
        self.field_layout.setSpacing(0)
        self.field_layout.setContentsMargins(0, 0, 0, 0)
        self.field_layout.addStretch()
        self.scroll.setWidget(self.field_container)
        root.addWidget(self.scroll)

        root.addWidget(self._make_separator())

        # Output row
        output_row = QHBoxLayout()
        out_lbl = QLabel("Output filename:")
        out_lbl.setFixedWidth(130)
        output_row.addWidget(out_lbl)

        self.filename_edit = QLineEdit()
        self.filename_edit.setPlaceholderText("merged_workout")
        output_row.addWidget(self.filename_edit)

        ext_lbl = QLabel(".fit")
        ext_lbl.setStyleSheet("color: #888;")
        output_row.addWidget(ext_lbl)

        self.merge_btn = QPushButton("Merge & Save →")
        self.merge_btn.setEnabled(False)
        self.merge_btn.setFixedWidth(140)
        self.merge_btn.setStyleSheet(
            f"QPushButton {{ background: {ACCENT}; color: white; border-radius: 6px; "
            f"padding: 8px 16px; font-weight: bold; }}"
            f"QPushButton:disabled {{ background: #ccc; }}"
            f"QPushButton:hover:!disabled {{ background: #0055AA; }}"
        )
        self.merge_btn.clicked.connect(self._merge)
        output_row.addWidget(self.merge_btn)

        root.addLayout(output_row)

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------

    def _make_separator(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #ddd;")
        return line

    def _make_field_header(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: #EBEBED; border-radius: 4px;")
        h = QHBoxLayout(w)
        h.setContentsMargins(8, 4, 8, 4)
        for text, width in [("Field", 110), ("File 1", 70), ("File 2", 70), ("Exclude", 70)]:
            lbl = QLabel(text)
            lbl.setFixedWidth(width)
            lbl.setStyleSheet("font-weight: bold; font-size: 12px; color: #555;")
            h.addWidget(lbl)
        h.addStretch()
        return w

    # ------------------------------------------------------------------
    # File browsing
    # ------------------------------------------------------------------

    def _browse(self, panel: FilePanel):
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Open workout file", "",
            "Workout files (*.fit *.pwx);;FIT files (*.fit);;PWX files (*.pwx)"
        )
        if not path_str:
            return

        path = Path(path_str)
        try:
            file_type, samples, meta = load_file(path)
        except ValueError as e:
            QMessageBox.warning(self, "Invalid file", str(e))
            return
        except Exception as e:
            QMessageBox.critical(self, "Error loading file", str(e))
            return

        panel.set_loaded(path, file_type, samples, meta)
        self._update_fields()

    # ------------------------------------------------------------------
    # Field table
    # ------------------------------------------------------------------

    def _update_fields(self):
        """Rebuild field table when both files are loaded."""
        if not (self.panel1.samples and self.panel2.samples):
            self._hide_fields()
            return

        # Get UTC start times for overlap detection
        try:
            start1 = samples_start_utc(self.panel1.file_type, self.panel1.meta)
            start2 = samples_start_utc(self.panel2.file_type, self.panel2.meta)

            # Convert file 2 samples to FIT-style records for merger
            fit_records2 = _samples_to_fit_records(self.panel2.samples, start2)
            fit_overlap, pwx_overlap = merger.find_overlap(
                start1, self.panel1.samples, fit_records2
            )
        except ValueError as e:
            QMessageBox.warning(self, "No overlap", str(e))
            self._hide_fields()
            return

        # Analyze fields
        make1 = samples_device_make(self.panel1.file_type, self.panel1.meta)
        make2 = samples_device_make(self.panel2.file_type, self.panel2.meta)
        stats1 = merger.analyze_pwx_fields(pwx_overlap, make1)
        stats2 = merger.analyze_fit_fields(fit_overlap, make2)

        all_fields = sorted(set(stats1) | set(stats2))

        # Clear old rows
        self._field_rows.clear()
        while self.field_layout.count() > 1:  # keep the trailing stretch
            item = self.field_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Build rows
        for field in all_fields:
            in_1 = field in stats1
            in_2 = field in stats2
            rec, reason = merger.recommend(field, stats1.get(field), stats2.get(field))
            row = FieldRow(field, in_1, in_2, rec, reason)
            # Alternate row background
            if len(self._field_rows) % 2 == 0:
                row.setStyleSheet("background: white;")
            self._field_rows.append(row)
            self.field_layout.insertWidget(self.field_layout.count() - 1, row)

        # Suggested filename
        try:
            if self.panel1.file_type == "pwx":
                _, start_local, _ = self.panel1.meta
                self.filename_edit.setText(f"merged_{start_local.strftime('%Y-%m-%d_%H%M')}")
            else:
                self.filename_edit.setText(f"merged_{start1.strftime('%Y-%m-%d_%H%M')}")
        except Exception:
            self.filename_edit.setText("merged_workout")

        self._show_fields()
        self.merge_btn.setEnabled(True)

        # Store overlap data for merge step
        self._fit_overlap  = fit_overlap
        self._pwx_overlap  = pwx_overlap
        self._start1_utc   = start1

    def _show_fields(self):
        self.field_area_label.show()
        self.field_header.show()
        self.scroll.show()
        self.scroll.setMaximumHeight(min(300, 44 * len(self._field_rows) + 8))

    def _hide_fields(self):
        self.field_area_label.hide()
        self.field_header.hide()
        self.scroll.hide()
        self.merge_btn.setEnabled(False)
        self._field_rows.clear()

    # ------------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------------

    def _merge(self):
        # Collect field choices
        choices: dict[str, str] = {}
        for row in self._field_rows:
            src = row.source()
            if src is not None:
                choices[row.field] = src

        if not choices:
            QMessageBox.warning(self, "Nothing to merge", "All fields are excluded.")
            return

        # Output path
        stem = self.filename_edit.text().strip() or "merged_workout"
        # Sanitize: strip unsafe characters
        import re
        stem = re.sub(r'[^\w\-. ]', '', stem).strip() or "merged_workout"

        save_path, _ = QFileDialog.getSaveFileName(
            self, "Save merged FIT file", f"{stem}.fit", "FIT files (*.fit)"
        )
        if not save_path:
            return

        try:
            merged = merger.build_merged_samples(
                self._pwx_overlap,
                self._fit_overlap,
                self._start1_utc,
                choices,
            )
            fit_core.write(merged, self._start1_utc, Path(save_path))
            QMessageBox.information(self, "Done", f"Saved to:\n{save_path}")
        except Exception as e:
            QMessageBox.critical(self, "Merge failed", str(e))


# ---------------------------------------------------------------------------
# Helpers for overlap with two arbitrary files
# ---------------------------------------------------------------------------

def _samples_to_fit_records(samples: list, start_utc) -> list:
    """Convert (offset, {pwx_field}) samples back to FIT-record-style dicts."""
    from app.core.fit import PWX_TO_FIT
    records = []
    for offset, fields in samples:
        from datetime import timedelta
        ts = start_utc + timedelta(seconds=offset)
        rec = {"timestamp": ts}
        for pwx_field, val in fields.items():
            fit_field = PWX_TO_FIT.get(pwx_field, pwx_field)
            rec[fit_field] = val
        records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("fitfilemaker")
    app.setStyle("macOS")

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
