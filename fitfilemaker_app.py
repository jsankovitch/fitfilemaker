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

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QFileDialog, QFormLayout,
    QFrame, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QRadioButton,
    QSizePolicy, QTabWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from app.core import fit as fit_core
from app.core import merger
from app.core import pwx as pwx_core
from app.core.fit import FIT_TO_PWX, PWX_TO_FIT
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

ACCENT = "#0066CC"


# ---------------------------------------------------------------------------
# App-wide palette (Fusion + Apple-inspired light colours)
# ---------------------------------------------------------------------------

def _build_palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window,          QColor("#F5F5F7"))
    p.setColor(QPalette.ColorRole.WindowText,      QColor("#1C1C1E"))
    p.setColor(QPalette.ColorRole.Base,            QColor("#FFFFFF"))
    p.setColor(QPalette.ColorRole.AlternateBase,   QColor("#F5F5F7"))
    p.setColor(QPalette.ColorRole.ToolTipBase,     QColor("#FFFFFF"))
    p.setColor(QPalette.ColorRole.ToolTipText,     QColor("#1C1C1E"))
    p.setColor(QPalette.ColorRole.Text,            QColor("#1C1C1E"))
    p.setColor(QPalette.ColorRole.Button,          QColor("#E5E5EA"))
    p.setColor(QPalette.ColorRole.ButtonText,      QColor("#1C1C1E"))
    p.setColor(QPalette.ColorRole.BrightText,      QColor("#FFFFFF"))
    p.setColor(QPalette.ColorRole.Link,            QColor(ACCENT))
    p.setColor(QPalette.ColorRole.Highlight,       QColor(ACCENT))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    # Disabled
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor("#AEAEB2"))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,       QColor("#AEAEB2"))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#AEAEB2"))
    return p


# ---------------------------------------------------------------------------
# File loading helpers
# ---------------------------------------------------------------------------

def load_file(path: Path) -> tuple:
    """Returns (file_type, samples, meta).
    samples — list of (offset_seconds, {pwx_field: float})
    meta    — PWX: (ElementTree, start_local, device_make) | FIT: start_utc
    """
    data = path.read_bytes()
    file_type = detect_and_validate(data, path.name)

    if file_type == "pwx":
        tree, start_local, samples = pwx_core.parse(path)
        make = _pwx_device_make(tree)
        return file_type, samples, (tree, start_local, make)
    else:
        records   = fit_core.parse(path)
        start_utc = records[0]["timestamp"]
        samples   = _fit_records_to_samples(records, start_utc)
        return file_type, samples, start_utc


def _pwx_device_make(tree) -> str | None:
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


def _fit_records_to_samples(records: list, start_utc) -> list:
    from datetime import timedelta
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


def _samples_to_fit_records(samples: list, start_utc) -> list:
    from datetime import timedelta
    records = []
    for offset, fields in samples:
        ts  = start_utc + timedelta(seconds=offset)
        rec = {"timestamp": ts}
        for pwx_field, val in fields.items():
            rec[PWX_TO_FIT.get(pwx_field, pwx_field)] = val
        records.append(rec)
    return records


def start_utc_for(file_type: str, meta):
    if file_type == "pwx":
        _, start_local, _ = meta
        return pwx_core.local_to_utc(start_local, pwx_core.system_utc_offset())
    return meta


def device_make_for(file_type: str, meta) -> str | None:
    if file_type == "pwx":
        _, _, make = meta
        return make
    return None


# ---------------------------------------------------------------------------
# Centred-widget helper for table cells
# ---------------------------------------------------------------------------

def _centred(widget: QWidget) -> QWidget:
    container = QWidget()
    layout    = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(widget)
    return container


# ---------------------------------------------------------------------------
# File panel
# ---------------------------------------------------------------------------

class FilePanel(QGroupBox):
    def __init__(self, label: str):
        super().__init__(label)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self.btn = QPushButton("Browse…")
        self.btn.setFixedWidth(90)
        layout.addWidget(self.btn)

        self.status = QLabel("No file selected")
        self.status.setStyleSheet("color: #8E8E93;")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.path      = None
        self.file_type = None
        self.samples   = None
        self.meta      = None

    def set_loaded(self, path: Path, file_type: str, samples: list, meta):
        self.path = path; self.file_type = file_type
        self.samples = samples; self.meta = meta
        note = " → FIT internally" if file_type == "pwx" else ""
        self.status.setText(
            f"<b>{path.name}</b><br>"
            f"<span style='color:{ACCENT}'>{file_type.upper()}{note}</span>"
            f"&nbsp;&nbsp;{len(samples):,} samples"
        )

    def clear(self):
        self.path = self.file_type = self.samples = self.meta = None
        self.status.setText("No file selected")


# ---------------------------------------------------------------------------
# Field table (QTableWidget-based for proper column alignment)
# ---------------------------------------------------------------------------

COL_FIELD   = 0
COL_FILE1   = 1
COL_FILE2   = 2
COL_EXCLUDE = 3
COL_NOTE    = 4

COL_WIDTHS  = [130, 70, 70, 70, 0]   # 0 = stretch


class FieldTable(QWidget):
    """Displays field-selection rows with radio buttons and an exclude checkbox."""

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Field", "File 1", "File 2", "Exclude", "Note"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setAlternatingRowColors(True)

        hh = self.table.horizontalHeader()
        for col, width in enumerate(COL_WIDTHS):
            if width:
                hh.resizeSection(col, width)
            else:
                hh.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        for col in range(len(COL_WIDTHS) - 1):
            hh.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(COL_NOTE, QHeaderView.ResizeMode.Stretch)

        hh.setStyleSheet(
            "QHeaderView::section { background: #E5E5EA; border: none; "
            "padding: 4px 8px; font-weight: 600; font-size: 12px; color: #3C3C43; }"
        )
        self.table.setStyleSheet(
            "QTableWidget { border: none; outline: none; } "
            "QTableWidget::item { padding: 2px 8px; }"
        )

        layout.addWidget(self.table)

        # Per-row state
        self._groups:  list[QButtonGroup] = []
        self._radios1: list[QRadioButton] = []
        self._radios2: list[QRadioButton] = []
        self._excludes: list[QCheckBox]   = []
        self._in1:     list[bool]         = []
        self._in2:     list[bool]         = []

    @property
    def fields(self) -> list[str]:
        return [
            self.table.item(r, COL_FIELD).data(Qt.ItemDataRole.UserRole)
            for r in range(self.table.rowCount())
        ]

    def populate(self, stats1: dict, stats2: dict):
        """Build or refresh rows for all fields in either file."""
        all_fields = sorted(set(stats1) | set(stats2))
        existing   = self.fields

        # Add rows for new fields
        for field in all_fields:
            if field not in existing:
                self._add_row(field)

        # Update enable state and recommendations for every row
        for row, field in enumerate(self.fields):
            in_1 = field in stats1
            in_2 = field in stats2
            self._in1[row] = in_1
            self._in2[row] = in_2

            self._radios1[row].setEnabled(in_1 and not self._excludes[row].isChecked())
            self._radios2[row].setEnabled(in_2 and not self._excludes[row].isChecked())

            rec, reason = merger.recommend(
                field, stats1.get(field), stats2.get(field)
            )
            # Only change selection if neither is already chosen
            if not self._radios1[row].isChecked() and not self._radios2[row].isChecked():
                if rec == "a" and in_1:
                    self._radios1[row].setChecked(True)
                elif rec == "b" and in_2:
                    self._radios2[row].setChecked(True)
                elif in_1:
                    self._radios1[row].setChecked(True)
                elif in_2:
                    self._radios2[row].setChecked(True)

            note_item = self.table.item(row, COL_NOTE)
            if note_item:
                note_item.setText(f"★  {reason}" if (in_1 and in_2) else "")

        self._resize()

    def _add_row(self, field: str):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setRowHeight(row, 36)

        # Field name
        item = QTableWidgetItem(FIELD_DISPLAY.get(field, field))
        item.setData(Qt.ItemDataRole.UserRole, field)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.table.setItem(row, COL_FIELD, item)

        # Radio buttons (no text — header provides context)
        group  = QButtonGroup(self)
        radio1 = QRadioButton()
        radio2 = QRadioButton()
        group.addButton(radio1, 1)
        group.addButton(radio2, 2)
        self.table.setCellWidget(row, COL_FILE1,   _centred(radio1))
        self.table.setCellWidget(row, COL_FILE2,   _centred(radio2))

        # Exclude checkbox (no text)
        exclude = QCheckBox()
        exclude.toggled.connect(lambda checked, r=row: self._on_exclude(r, checked))
        self.table.setCellWidget(row, COL_EXCLUDE, _centred(exclude))

        # Note (left-aligned)
        note_item = QTableWidgetItem("")
        note_item.setForeground(QColor("#8E8E93"))
        note_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.table.setItem(row, COL_NOTE, note_item)

        self._groups.append(group)
        self._radios1.append(radio1)
        self._radios2.append(radio2)
        self._excludes.append(exclude)
        self._in1.append(False)
        self._in2.append(False)

    def _on_exclude(self, row: int, checked: bool):
        self._radios1[row].setEnabled(not checked and self._in1[row])
        self._radios2[row].setEnabled(not checked and self._in2[row])

    def _resize(self):
        rows = self.table.rowCount()
        row_h = 36
        header_h = self.table.horizontalHeader().height()
        self.table.setMinimumHeight(header_h + rows * row_h + 4)
        self.table.setMaximumHeight(header_h + rows * row_h + 4)

    def choices(self) -> dict:
        """Return {field: 'a' | 'b'} for all non-excluded rows."""
        result = {}
        for row, field in enumerate(self.fields):
            if self._excludes[row].isChecked():
                continue
            if self._radios1[row].isChecked():
                result[field] = "a"
            elif self._radios2[row].isChecked():
                result[field] = "b"
        return result


# ---------------------------------------------------------------------------
# Workout details tab
# ---------------------------------------------------------------------------

class WorkoutDetailsTab(QWidget):
    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(10)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. Morning Ride")
        self.name_edit.setMaxLength(100)
        form.addRow("Workout name:", self.name_edit)

        outer.addLayout(form)
        outer.addStretch()

        note = QLabel("Additional metadata fields will appear here in future releases.")
        note.setStyleSheet("color: #8E8E93; font-size: 12px;")
        outer.addWidget(note)

    @property
    def workout_name(self) -> str:
        return self.name_edit.text().strip()

    @workout_name.setter
    def workout_name(self, value: str):
        self.name_edit.setText(value)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("fitfilemaker")
        self.setMinimumWidth(720)

        # Overlap state (set after both files analysed)
        self._fit_overlap  = None
        self._pwx_overlap  = None
        self._start1_utc   = None

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(14)
        root.setContentsMargins(24, 20, 24, 20)

        # Title
        title = QLabel("fitfilemaker")
        f = title.font()
        f.setPointSize(22)
        f.setWeight(QFont.Weight.Bold)
        title.setFont(f)
        subtitle = QLabel("Select two workout files, choose which data to keep, and merge.")
        subtitle.setStyleSheet("color: #8E8E93;")
        root.addWidget(title)
        root.addWidget(subtitle)
        root.addWidget(_hline())

        # File panels
        panels = QHBoxLayout()
        panels.setSpacing(12)
        self.panel1 = FilePanel("File 1")
        self.panel2 = FilePanel("File 2")
        self.panel1.btn.clicked.connect(lambda: self._browse(self.panel1))
        self.panel2.btn.clicked.connect(lambda: self._browse(self.panel2))
        panels.addWidget(self.panel1)
        panels.addWidget(self.panel2)
        root.addLayout(panels)
        root.addWidget(_hline())

        # Tabs (hidden until at least one file is loaded)
        self.tabs = QTabWidget()
        self.tabs.hide()

        self.field_table   = FieldTable()
        self.details_tab   = WorkoutDetailsTab()
        self.details_tab.name_edit.textChanged.connect(self._sync_filename)

        self.tabs.addTab(self.field_table, "Fields")
        self.tabs.addTab(self.details_tab, "Workout Details")
        root.addWidget(self.tabs)

        root.addWidget(_hline())

        # Output row
        out_row = QHBoxLayout()
        out_row.setSpacing(8)
        out_row.addWidget(QLabel("Output filename:"))

        self.filename_edit = QLineEdit()
        self.filename_edit.setPlaceholderText("merged_workout")
        self.filename_edit.textChanged.connect(self._sync_name_from_filename)
        out_row.addWidget(self.filename_edit)

        ext = QLabel(".fit")
        ext.setStyleSheet("color: #8E8E93;")
        out_row.addWidget(ext)

        self.merge_btn = QPushButton("Merge and Save")
        self.merge_btn.setEnabled(False)
        self.merge_btn.setFixedWidth(150)
        self.merge_btn.setStyleSheet(
            "QPushButton {"
            f"  background-color: {ACCENT}; color: white;"
            "  border-radius: 6px; padding: 7px 16px;"
            "  font-weight: 600;"
            "}"
            "QPushButton:hover:!disabled { background-color: #0055B3; }"
            "QPushButton:disabled { background-color: #C7C7CC; color: white; }"
        )
        self.merge_btn.clicked.connect(self._merge)
        out_row.addWidget(self.merge_btn)
        root.addLayout(out_row)

        # Prevent filename sync from looping
        self._syncing = False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _sync_filename(self, name: str):
        """Workout name → filename (without disturbing cursor)."""
        if self._syncing:
            return
        self._syncing = True
        safe = re.sub(r'[^\w\-. ]', '', name).strip()
        if safe:
            self.filename_edit.setText(safe)
        self._syncing = False

    def _sync_name_from_filename(self, text: str):
        """Filename → workout name (keeps them loosely in sync)."""
        if self._syncing:
            return
        self._syncing = True
        self.details_tab.name_edit.setText(text)
        self._syncing = False

    def _set_suggested_name(self, value: str):
        self._syncing = True
        self.filename_edit.setText(value)
        self.details_tab.workout_name = value
        self._syncing = False

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
    # Field table population
    # ------------------------------------------------------------------

    def _update_fields(self):
        has_1 = self.panel1.samples is not None
        has_2 = self.panel2.samples is not None

        if not has_1:
            self.tabs.hide()
            self.merge_btn.setEnabled(False)
            return

        # At least File 1 is loaded — show tabs
        self.tabs.show()

        # Build stats for whichever files we have
        start1 = start_utc_for(self.panel1.file_type, self.panel1.meta)
        make1  = device_make_for(self.panel1.file_type, self.panel1.meta)
        stats1 = merger.analyze_pwx_fields(self.panel1.samples, make1)
        stats2: dict = {}

        if has_2:
            try:
                start2      = start_utc_for(self.panel2.file_type, self.panel2.meta)
                fit_records2 = _samples_to_fit_records(self.panel2.samples, start2)
                fit_overlap, pwx_overlap = merger.find_overlap(
                    start1, self.panel1.samples, fit_records2
                )
                make2  = device_make_for(self.panel2.file_type, self.panel2.meta)
                stats2 = merger.analyze_fit_fields(fit_overlap, make2)

                self._fit_overlap = fit_overlap
                self._pwx_overlap = pwx_overlap
                self._start1_utc  = start1
            except ValueError as e:
                QMessageBox.warning(self, "No overlap", str(e))
                has_2 = False

        self.field_table.populate(stats1, stats2)
        self.merge_btn.setEnabled(has_1 and has_2)

        # Suggest a filename from the first file's start time
        if not self.filename_edit.text():
            try:
                if self.panel1.file_type == "pwx":
                    _, start_local, _ = self.panel1.meta
                    self._set_suggested_name(
                        f"merged_{start_local.strftime('%Y-%m-%d_%H%M')}"
                    )
                else:
                    self._set_suggested_name(
                        f"merged_{start1.strftime('%Y-%m-%d_%H%M')}"
                    )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------------

    def _merge(self):
        choices = self.field_table.choices()
        if not choices:
            QMessageBox.warning(self, "Nothing to merge", "All fields are excluded.")
            return

        stem = re.sub(r'[^\w\-. ]', '', self.filename_edit.text().strip()) or "merged_workout"
        save_path, _ = QFileDialog.getSaveFileName(
            self, "Save merged FIT file", f"{stem}.fit", "FIT files (*.fit)"
        )
        if not save_path:
            return

        try:
            merged = merger.build_merged_samples(
                self._pwx_overlap, self._fit_overlap, self._start1_utc, choices
            )
            fit_core.write(merged, self._start1_utc, Path(save_path))
            QMessageBox.information(self, "Saved", f"Merged file saved to:\n{save_path}")
        except Exception as e:
            QMessageBox.critical(self, "Merge failed", str(e))


# ---------------------------------------------------------------------------
# Separator helper
# ---------------------------------------------------------------------------

def _hline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("color: #D1D1D6;")
    return line


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("fitfilemaker")
    app.setStyle("Fusion")
    app.setPalette(_build_palette())

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
