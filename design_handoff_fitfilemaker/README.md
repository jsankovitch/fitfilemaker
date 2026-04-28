# Handoff: fitfilemaker UI Redesign

## Overview

fitfilemaker is a fully local macOS desktop app (Python 3.14 + PySide6 6.11.0) that merges two or more cycling workout files (`.fit` and `.pwx`) into a single `.fit` output, primarily for upload to TrainingPeaks or Garmin Connect.

This handoff contains a **high-fidelity interactive prototype** of the redesigned UI. Your task is to **recreate this design in the existing PySide6 codebase**, replacing the current QMainWindow layout. Do not ship the HTML directly — use it as a precise visual and behavioral reference.

---

## About the Design Files

`fitfilemaker.html` is a React/HTML prototype. Open it in a browser to interact with it. It uses:
- **Tweaks panel** (toolbar toggle in the preview environment) to switch between 3 layout variants and 3 color themes
- The **Steps layout + Slate theme** is the agreed direction to implement
- The other variants (Sidebar, One-page; Warm, Sage themes) are exploratory — implement Steps/Slate only unless directed otherwise

---

## Fidelity

**High-fidelity.** Recreate the UI pixel-accurately using PySide6 widgets and QSS stylesheets. Colors, font sizes, spacing, border radii, and interaction states are all specified below and visible in the prototype. Where a precise PySide6 widget match doesn't exist, use `QPainter`/`QPaintEvent` for custom rendering.

---

## Architecture: Window & Layout

### Window
- Single `QMainWindow`, ~980 × 660 px default, fully resizable
- Window title: `fitfilemaker`
- macOS traffic lights handled by the OS — no custom chrome needed for a real `.app`
- Background behind the window: not applicable (native macOS)

### Overall structure (Steps layout)
```
QMainWindow
└── Central widget (QWidget, bg: #F4F5F8)
    └── QVBoxLayout (no margins)
        ├── StepBar (custom QWidget, 40px tall, bg: #FFFFFF, bottom border 1px #E2E4EB)
        ├── ContentArea (QStackedWidget, flex-grow, overflow scroll per page)
        └── NavBar (custom QWidget, 44px tall, bg: #FFFFFF, top border 1px #E2E4EB)
```

---

## Design Tokens (Slate theme, light mode)

### Colors
| Token | Value | Usage |
|---|---|---|
| `--page-bg` | `#EAECF2` | Window background |
| `--bg` | `#F4F5F8` | Step content background |
| `--surface` | `#FEFEFF` | Cards, panels |
| `--surface2` | `#ECEEF3` | Secondary surfaces, expanded areas |
| `--surface3` | `#E4E6ED` | Tertiary, trim-excluded regions |
| `--accent` | `#4A55C0` | Primary action, active states |
| `--accent-dim` | `#EDF0FC` | Accent tint backgrounds |
| `--accent-fg` | `#FFFFFF` | Text on accent backgrounds |
| `--text` | `#1E2130` | Primary text |
| `--text2` | `#5B6080` | Secondary text / labels |
| `--text3` | `#8B90A8` | Muted text / placeholders |
| `--border` | `#DEE0E8` | Default borders |
| `--border2` | `#C2C5D4` | Stronger borders, slider tracks |
| `--good` | `#2E7A4F` | Success / Good quality |
| `--good-bg` | `#EAF5EF` | Success tint |
| `--warn` | `#7A5A18` | Warning / Fair quality |
| `--warn-bg` | `#FBF4E4` | Warning tint |
| `--bad` | `#6A6E85` | Poor / excluded |
| `--bad-bg` | `#EEF0F5` | Poor tint |

### Dark mode equivalents
Apply when `QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark`:
| Token | Dark value |
|---|---|
| `--page-bg` | `#111318` |
| `--bg` | `#1A1D24` |
| `--surface` | `#202530` |
| `--surface2` | `#262C38` |
| `--surface3` | `#2C3340` |
| `--accent` | `#7B88E8` |
| `--accent-dim` | `#252A45` |
| `--accent-fg` | `#111318` |
| `--text` | `#EEF0F7` |
| `--text2` | `#9298B0` |
| `--text3` | `#6A6E85` |
| `--border` | `#2E3244` |
| `--border2` | `#3C4058` |
| `--good` | `#6DBF8A` | 
| `--good-bg` | `#1D2E24` |
| `--warn` | `#D4A840` |
| `--warn-bg` | `#2A2314` |

### Typography
- **Primary font:** `DM Sans` (available via Google Fonts / bundle with app). Fallback: `-apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif`
- **Monospace font:** `DM Mono`. Fallback: `"SF Mono", "Menlo", monospace`
- Font smoothing: enable `QFont::StyleStrategy::PreferAntialias`

| Role | Size | Weight | Notes |
|---|---|---|---|
| Step label (active) | 12px | 600 | Letter-spacing -0.01em |
| Step label (inactive) | 12px | 500 | |
| Step number | 9px | 700 | Mono, uppercase, tracking 0.1em |
| Section heading (h2) | 16px | 600 | |
| Body / description | 13px | 400 | Line-height 1.6 |
| Field label | 13px | 600 | |
| Field unit | 10px | 400 | Mono |
| Data value | 12px | 500 | Mono |
| Badge text | 10–11px | 700 | Uppercase, tracking 0.04–0.07em |
| Button text | 12–14px | 600 | |
| Filename | 12–13px | 500 | Mono |

### Border radii
| Token | Value |
|---|---|
| `--r-sm` | 6px |
| `--r` | 10px |
| `--r-lg` | 16px |

### Shadows
- **Card shadow:** `0 2px 8px rgba(30,33,48,0.07), 0 1px 2px rgba(30,33,48,0.05)`
- **Window shadow:** handled by macOS

---

## Step Bar

A horizontal progress indicator at the top of the window.

**Widget:** Custom `QWidget`, height 40px, `bg: --surface`, bottom border 1px `--border`.

**Layout:** `QHBoxLayout` with 4 equal `QPushButton` items (or custom `QLabel`-based tabs).

Each step button:
- Width: 25% of bar
- Content: step number (mono, 9px, 70% opacity) above step label (12px)
- **Active state:** bottom border 2px `--accent`; text color `--accent`; number color `--accent`
- **Completed state:** number shows `✓`; text color `--text2`
- **Default state:** text color `--text3`
- Clicking any step navigates directly to it (non-linear allowed)

Steps in order: `01 Files` → `02 Fields` → `03 Trim` → `04 Export`

---

## Nav Bar (Bottom)

Height: 44px. `bg: --surface`. Top border 1px `--border`.

**Layout:** `QHBoxLayout`, padding 11px 22px.

- **Back button** (left): outlined style — border 1px `--border2`, radius 6px, 12px text, padding 7px 16px. Disabled (opacity 0.38) on first step.
- **Continue button** (right): filled — `bg: --accent`, text `--accent-fg`, radius 6px, 12px, bold. Hidden on last step.

---

## Step 1: Files

**Purpose:** Load two or more workout files.

**Layout:** `QVBoxLayout`, padding 22px, gap 16px, scrollable.

### Section header
- H2: "Load workout files" (16px, 600)
- Body: "Add two or more workout files. Supports `.fit` and `.pwx`." (13px, `--text2`)

### File cards
Each loaded file renders as a card (`QFrame`):
- Background: `--surface`
- Border: 1px `--border`
- Border-radius: 10px
- Shadow: card shadow (see above)

**Card header row** (always visible, clickable to expand):
- Left: 3px × 38px colored vertical bar. File 1: `--accent`. File 2: `oklch(0.62 0.155 32)` ≈ `#C46030`. File 3+: `oklch(0.55 0.165 295)` ≈ `#8855CC`
- Format badge: 10px mono, 700, uppercase; text `--accent`; background `--accent-dim`; padding 2px 7px; radius 5px
- Filename: 12px mono, 500, `--text`; truncate with ellipsis
- Secondary line: 11px, `--text3`: device · duration · start time · file size
- Right: chevron (▼/▲) + × remove button

**Expanded panel** (toggled by clicking header):
- Background: `--surface2`
- Top border: 1px `--border`
- Padding: 12px 14px
- Contains field chips in a wrapping flex/flow layout: one chip per field (Heart Rate, Power, Cadence, Speed, Distance, Altitude)
- Each chip: `--surface` bg, 1px `--border`, radius 6px, padding 7px 10px, min-width ~110px
  - Field name row: 10px `--text3` with icon character
  - Value row: 12px mono 500 `--text` + quality badge
  - Sub-note (if present): 10px `--text3`

**Quality badges:**
| Quality | Text | Color | Background |
|---|---|---|---|
| Good | Good | `--good` | `--good-bg` |
| Fair | Fair | `--warn` | `--warn-bg` |
| Poor | Poor | `--bad` | `--bad-bg` |
| None | None | `--text3` | `--surface3` |
Badge style: 10px, 700, uppercase, tracking 0.04em, padding 1px 7px, radius 99px

### Drop zone / Add button
- Dashed border: 2px `--border2`, radius 10px
- Padding: 18px 20px
- On hover/drag-over: border color `--accent`, background `--accent-dim`
- Contains: `+` icon (34×34px, `--accent-dim` bg, radius 6px) + text "Add another file" / "Drop a .fit or .pwx file, or click to browse"
- Clicking opens `QFileDialog` filtered to `.fit, .pwx`

### Success banner
Appears when ≥ 2 files loaded:
- Background `--good-bg`, text `--good`, 13px, radius 6px, padding 11px 15px
- Text: "✓ **N files loaded.** Device context detected — field recommendations are ready on the next step."

---

## Step 2: Fields

**Purpose:** Choose which source file supplies each data field.

**Layout:** `QVBoxLayout`, padding 22px, gap 14px, scrollable.

### Section header
- H2: "Choose data sources"
- Body: "Select which file supplies each field in the merged output. ★ Recommended sources are pre-selected based on device type and data quality."
- Right-aligned "Reset to recommended" button (only visible when any field is overridden): outlined, 11px, `--text2`, padding 6px 12px, radius 6px

### Field rows
Six rows, one per field: Heart Rate (♥), Power (⚡), Cadence (↺), Speed (→), Distance (◎), Altitude (△).

Each row is a `QFrame`:
- Background: `--surface`
- Border: 1px `--border`, radius 10px
- **When overridden:** border becomes 1.5px `--warn` (orange outline)

**Row layout** (`QHBoxLayout`, no spacing):

1. **Field label column** (130px fixed width, right border 1px `--border`):
   - Icon character (15px `--text2`) + field name (13px 600 `--text`)
   - Unit below (10px mono `--text3`)

2. **Source buttons** (one per loaded file, equal width, `QButtonGroup` exclusive):
   - Each button: padding 10px 12px, left border none (between buttons: 1px `--border`)
   - **Selected state:** background `--accent-dim`; 3px left accent bar; file number in `--accent`; value text 600 weight
   - **Unselected state:** transparent background
   - **Disabled** (quality = none): opacity 0.38, not clickable
   - Content per button:
     - File label row: "File N" (10px mono 700 `--text3`/`--accent` if selected) + "★ REC" pill if recommended
     - Data value (12px mono, mono): e.g. "avg 195 W"
     - Quality badge below value
   - "★ REC" pill: 9px, 700, `--accent` text, `--accent-dim` bg, padding 1px 5px, radius 99px

3. **Exclude button** (76px fixed width):
   - Icon: ⊘ (17px, 65% opacity)
   - Label: "Exclude" (11px)
   - **Selected:** background `--bad-bg`, text `--bad`, weight 600
   - **Default:** transparent, text `--text3`

4. **Note toggle** (36px, right border, left border 1px `--border`):
   - Shows "?" character, 13px 600
   - **Active:** background `--accent-dim`, color `--accent`
   - **Default:** color `--text3`

**Expanded note panel** (below the row when note toggle active):
- Top border 1px `--border`
- Background: `--accent-dim`
- Padding: 10px 14px
- "★" prefix in `--accent` 700 + recommendation text 12px `--text2` line-height 1.55
- If overridden: "Override active" pill — 10px 700 `--warn` text, `--warn-bg` bg, padding 1px 6px, radius 99px

### Auto-recommendations (implement this logic)
Detect device type from file metadata:
- **PWX from trainer (e.g. Wahoo KICKR):** prefer for Power, Cadence, Speed, Distance
- **FIT from Garmin watch:** prefer for Heart Rate
- **Indoor session (no altitude variance):** recommend Exclude for Altitude
- **Quality override:** if a field has all-zeros or no variance, flag as Poor and de-prefer regardless of device

---

## Step 3: Trim

**Purpose:** Trim workout start/end and remove mid-workout sections.

**Layout:** `QVBoxLayout`, padding 22px, gap 14px, scrollable.

### Section header
- H2: "Trim workout"
- Body: "Adjust start and end with the sliders. **Drag on the graph** to mark a mid-workout section for removal."

### Graph card
`QFrame`, `--surface` bg, 1px `--border`, radius 10px, shadow.

**A. Legend bar** (top of card):
- Right-aligned, padding 8px 14px, `--surface2` bg, bottom border 1px `--border`
- Items: colored line swatch + "Power (W)" / "Heart Rate (bpm)" / "N cuts" (only if cuts exist)
- Power swatch: 16×3px rect, `--accent`, 70% opacity
- HR swatch: 16×2px rect, `oklch(0.62 0.155 32)` ≈ `#C46030`

**B. Cut tool callout** (below legend, above graph):
- Background: `--accent-dim`, bottom border 1px `--border`, padding 9px 14px
- Left: 28×28px square icon, radius 6px, `--accent` bg, `--accent-fg` text, "✂" character 14px 700
- Right: "Cut tool" label 12px 600 `--accent` + "Click and drag **directly on the graph** to mark a section for removal" 11px `--text2`

**C. Waveform graph** (interactive widget, cursor: crosshair):
- Height: ~110px rendered
- Implement as `QWidget` with custom `paintEvent` using `QPainter`

Draw order (back to front):
1. **Trim-excluded regions:** fill left of trimStart and right of trimEnd with `--surface3` at 72% opacity
2. **Power area fill:** gradient from `--accent` 38% opacity (top) to 4% (bottom), following power path
3. **Power line:** 1.5px stroke, `--accent` 75% opacity
4. **HR line:** 1.5px stroke, `#C46030` 72% opacity
5. **Cut hatch zones:** diagonal stripe pattern (45°, `--bad` color, 45% opacity) for each cut region
6. **Active drag selection:** while user is dragging, fill region with `--bad` 14% + 1px `--bad` stroke 50% opacity
7. **Trim boundary lines:** dashed vertical lines (3px on, 2px off) at trimStart and trimEnd positions, `--accent` 2px

**Mouse interactions:**
- `mousePressEvent`: record start X position as percentage
- `mouseMoveEvent`: update current X, repaint drag preview
- `mouseReleaseEvent`: if drag width > 1.5% of total, commit as a cut; clear drag state
- All percentages are of the widget width

**D. Trim sliders** (below graph):
- Top border 1px `--border`, padding 10px 16px 14px, `QHBoxLayout` gap 20px
- Two `QSlider` (horizontal), each in a `QVBoxLayout`:
  - Label row: "Start" / "End" left-aligned + time value right-aligned (11px `--text2`)
  - `QSlider` with `accentColor` styling (QSS `::handle` and `::sub-page` in `--accent`)
  - Start slider: range 0 to (trimEnd - 1)
  - End slider: range (trimStart + 1) to 100

### Output summary pill
Below the card: single-line `QFrame`, `--surface` bg, 1px `--border`, radius 6px, padding 9px 14px:
- "Output:" `--text2` + duration value (mono 600 `--text`)
- If cuts exist: "· N sections cut (−MM:SS)" in `--text3`

### Cut list
`QVBoxLayout`, gap 5px. Appears below summary when cuts exist.
- Label: "Removed sections" (10px 700 uppercase tracking 0.08em `--text3`)
- Each cut row: `QFrame` `--surface`, 1px `--border`, radius 6px, padding 8px 12px
  - Left: 12×12px hatched swatch (same diagonal stripe pattern, 1px `--bad` border, radius 2px)
  - Time range: "MM:SS – MM:SS" (12px mono `--text`)
  - Right-aligned: "MM:SS removed" (11px `--text3`)
  - × button: 16px `--text3`, no background

---

## Step 4: Export

**Purpose:** Review choices and save the merged `.fit` file.

**Layout:** `QVBoxLayout`, padding 22px, gap 14px, scrollable.

### Section header
- H2: "Export merged file"
- Body: "Review your choices and export a merged `.fit`."

### Waveform preview (read-only)
Same graph as Trim step but non-interactive, height ~72px, inside a `QFrame`:
- Background `--surface2`, border 1px `--border`, radius 6px
- Header bar: "OUTPUT PREVIEW" label (10px 700 uppercase `--text3`) + legend items (right-aligned)
- Shows trim regions (darkened), cut zones (hatched), power area + HR line
- Updates live when user changes trim/cuts on the Trim step

### Summary card
`QFrame`, `--surface` bg, 1px `--border`, radius 10px, shadow.

**Top section** (2-column grid, right column has no left border):

_Left column — Source files:_
- Label: "SOURCE FILES" (10px 700 uppercase `--text3`)
- Each file: colored dot (8×8px circle) + format badge + truncated filename (11px mono `--text2`)

_Right column — Fields in output:_
- Label: "FIELDS IN OUTPUT" (10px 700 uppercase `--text3`)
- Included fields: pill per field, `--good-bg` bg, `--good` text, 11px 500
  - Text: "{icon} {Label} ({FORMAT})" e.g. "♥ Heart Rate (FIT)"
- Excluded fields: pill per field, `--bad-bg` bg, `--text3` text, 11px, strikethrough

**Filename row** (below summary grid, top border 1px `--border`):
- Label: "Output filename" (11px 600 `--text2`)
- `QLineEdit` (flex) + static ".fit" suffix label (`--surface3` bg, `--text3`, mono)
- Line edit: `--surface2` bg, `--border2` border, radius-left 6px, no-radius right
- Suffix label: radius-right 6px

**Actions row** (bottom, top border 1px `--border`):
- **Merge & Save button** (flex-fill): 14px 600, `--accent` bg, `--accent-fg` text, radius 6px, padding 11px 20px
  - On click: show progress (e.g. "Merging…", opacity 0.75), then on completion: `--good` bg + "✓ Saved — {filename}.fit"
  - Implementation: call existing merge logic, update UI state on completion signal
- **Upload to Strava button** (fixed width): 13px 500, `--surface2` bg, `--text3`, 1px `--border`, radius 6px, disabled
  - "SOON" badge: absolute position top-right, 8px 800 white text, `--warn` bg, radius 99px, padding 2px 5px
  - Leave as placeholder for future implementation

---

## Waveform Data

In the prototype, waveform data is synthesized. In production, generate it from the actual parsed workout data:

- **X axis:** time (uniform sample spacing)
- **Y axis power:** 0–420W. Scale to widget height × 0.88 (leave 12% top margin)
- **Y axis HR:** 75–182bpm. Same scale
- Use `QPainterPath.cubicTo()` or `lineTo()` between sample points (simplify to ~400 points max for performance using Douglas-Peucker or simple stride)
- Power = filled area chart; HR = line only

---

## Interactions to Implement

| Interaction | Implementation |
|---|---|
| Step navigation (click step or Back/Continue) | `QStackedWidget.setCurrentIndex()` |
| File drop on drop zone | `QWidget.setAcceptDrops(True)` + `dropEvent` |
| File browse dialog | `QFileDialog.getOpenFileNames(filter="Workout files (*.fit *.pwx)")` |
| Field source selection | `QButtonGroup(exclusive=True)` per field row |
| Note toggle expand/collapse | `QWidget.setVisible()` on note panel |
| File card expand/collapse | `QWidget.setVisible()` on expanded panel |
| Trim sliders linked | Enforce `start < end` in `valueChanged` signals |
| Drag-to-cut on graph | Custom `QWidget` mouse events (see above) |
| Remove cut | Remove from list, repaint graph |
| Merge & Save | Connect to existing merge logic; show progress; open `QFileDialog.getSaveFileName()` |
| Reset to recommended | Restore `QButtonGroup` selections from recommendation map |

---

## State to Maintain

```python
class AppState:
    files: list[WorkoutFile]           # loaded files, in order
    field_choices: dict[str, str|None]  # field_id → file_id or None (exclude)
    trim_start_pct: float              # 0.0–100.0
    trim_end_pct: float                # 0.0–100.0
    cuts: list[tuple[float, float]]    # list of (start_pct, end_pct) pairs
    output_filename: str
```

---

## Files in This Package

| File | Description |
|---|---|
| `fitfilemaker.html` | Full interactive prototype. Open in a browser. Use Tweaks panel (top-right toggle) to explore layout/theme variants. Steps + Slate is the agreed direction. |
| `README.md` | This document |

---

## Notes for Implementation

1. **PySide6 QSS** maps cleanly to the CSS variables above. Define a global stylesheet on `QApplication` with all token values.
2. **Dark mode:** Connect to `QApplication.styleHints().colorSchemeChanged` signal and re-apply the stylesheet.
3. **Waveform widget:** `pyqtgraph` is recommended for the interactive graph if you want zoom/pan later. For this design, plain `QPainter` in a `QWidget` is sufficient.
4. **Font bundling:** Bundle `DM Sans` and `DM Mono` OTF/TTF files with the app and load via `QFontDatabase.addApplicationFont()`.
5. **The existing core logic** (file parsing, merge algorithm) does not need to change — only the UI layer is being replaced.
