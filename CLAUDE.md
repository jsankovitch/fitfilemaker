# fitfilemaker — Project Handoff

## Project Goal
A tool to merge and manipulate workout files (.pwx, .fit) for upload to TrainingPeaks and Garmin Connect.

## Background / Use Case
- User attends coached cycling classes. Coach uploads a `.pwx` file (power, cadence, structure, comments — no HR).
- User records simultaneously on a Garmin device, producing a `.fit` file (HR, possibly GPS).
- Goal: merge into one complete file. Coach comments in PWX must always be preserved.

## File Formats
| File | Format | Source | Contains |
|---|---|---|---|
| Coach workout | `.pwx` | TrainingPeaks / coach upload | Power, cadence, structure, comments |
| User recording | `.fit` | Garmin device | HR, GPS, possibly power/cadence |

## Merge Logic
- **Field selection:** User picks source per field — File 1, File 2, or Exclude entirely.
- **No averaging in current UI** (mac app uses radio buttons, not avg option).
- **Timestamp alignment:** Find first overlapping window; discard non-overlapping edges.
- **Interpolation:** Linear interpolation maps the secondary file's data onto the base file's timestamps.
- **Recommendations:** Auto-suggested based on:
  1. Device context — trainer brands (RacerMate, Wahoo, Tacx, etc.) → recommend for pwr/cad/spd/dist; Garmin → recommend for hr
  2. Data quality — zero ratio >80%, placeholder values (0.00001), near-zero variance → flag as poor
- **Output:** Always `.fit` (universally accepted).

## Technical Approach
- `.pwx` is XML — parsed with `xml.etree.ElementTree` (stdlib)
- `.fit` is binary — parsed with `python-fitparse`, written with `fit-tool`
- Timestamp conversion: FIT timestamps are naive UTC datetimes; PWX `<time>` is naive local time — convert using system UTC offset (overridable)
- File type detection: by magic bytes / XML namespace content, never by extension alone

## Project Structure
```
fitfilemaker/
├── app/
│   ├── core/
│   │   ├── pwx.py        # PWX parse/write
│   │   ├── fit.py        # FIT parse/write
│   │   ├── merger.py     # overlap, interpolation, recommendations, merge
│   │   └── security.py   # file validation, filename sanitization
│   ├── api/v1/routes.py  # FastAPI endpoints (web app, shelved)
│   ├── main.py           # FastAPI entry point (shelved)
│   └── static/index.html # Web UI (shelved)
├── fitfilemaker_app.py   # macOS PySide6 GUI (ACTIVE)
├── merge.py              # CLI: merge PWX + FIT
├── pwx_to_fit.py         # CLI: convert PWX → FIT
├── requirements.txt
├── NOTICE                # Open-source license attributions
└── testFiles/            # gitignored
```

## Phases
1. **Phase 1 — CLI (done):**
   - `merge.py` — merges PWX + FIT interactively via CLI
   - `pwx_to_fit.py` — converts PWX → FIT
2. **Phase 2 — macOS GUI (active, `feature/mac-app`):**
   - `fitfilemaker_app.py` — PySide6 native mac app
   - Two file pickers (PWX or FIT), field table with radio buttons, merge to FIT
3. **Phase 3 — Web app (shelved, `feature/web-app`):**
   - FastAPI backend + vanilla JS frontend
   - Can resume later; all core logic is in `app/core/` and reusable

## Repository
- GitHub: https://github.com/jsankovitch/fitfilemaker
- Active branch: `feature/mac-app`
- Shelved branch: `feature/web-app`
- Branching strategy: feature branches off `main`, PR to merge

## Dependencies & Licenses
See `requirements.txt` and `NOTICE` file.
| Package | Version | License | Requirement |
|---|---|---|---|
| fitparse | 1.2.0 | MIT | Include copyright notice |
| fit-tool | 0.9.15 | BSD 3-Clause | Include copyright notice; no Stages Cycling endorsement |
| PySide6 | 6.11.0 | LGPL-3.0 | Include license notice; allow Qt library relinking |

## Key Design Decisions
- **Output always FIT** — universally accepted by TrainingPeaks, Garmin Connect, etc.
- **No user login, no file storage, no telemetry** — privacy by design
- **File type detected by content** (magic bytes / XML namespace), not extension
- **PWX converted to internal sample format** when loaded — no temp files written
- **Recommendations shown but never enforced** — user always has final say
