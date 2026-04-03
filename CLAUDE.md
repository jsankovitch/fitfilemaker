# Workout File Merger — Project Handoff

## Project Goal
Build a tool to merge two workout files into a single `.pwx` file for upload to TrainingPeaks.

## Background / Use Case
- User attends coached cycling classes. The coach uploads a workout file (`.pwx`) that contains power, cadence, structure, and comments — but **no heart rate data**.
- User simultaneously records the session on a Garmin device, which produces a `.fit` file containing **heart rate (and possibly GPS)**.
- The user wants to merge these into one file to get a complete workout record in TrainingPeaks.
- A complication: the coach sometimes adds comments to the `.pwx` before the user has a chance to manually merge — so **preserving coach comments is a requirement**.

## File Formats
| File | Format | Source | Contains |
|---|---|---|---|
| Coach workout | `.pwx` | TrainingPeaks / coach upload | Power, cadence, structure, comments |
| User recording | `.fit` | Garmin device | HR, GPS, possibly power/cadence |

## Merge Logic
- **User selects which fields to keep from each file** via a prompt (not hardcoded).
- **Timestamp alignment:** The two files may start at different times. Sync by finding the **first overlapping window** and aligning from there. Discard non-overlapping data at the edges.
- **HR interpolation:** Garmin samples ~1s; PWX may differ. Use linear interpolation to map HR onto PWX sample timestamps.
- **Comments:** Always preserved from the `.pwx` (since it's the base file structure).
- **Output format:** `.pwx` (TrainingPeaks accepts this natively).

## Technical Approach
- `.pwx` is XML — parse with Python's `lxml` or `xml.etree`
- `.fit` is binary (Garmin epoch timestamps) — parse with `python-fitparse`
- Timestamp conversion: FIT uses a Garmin epoch offset; PWX uses wall-clock UTC — must convert before alignment

## Planned Phases
1. **Phase 1 (current):** CLI Python proof-of-concept
   - Parse both files
   - Detect overlap window
   - CLI prompt for field selection
   - Merge and output `.pwx`
2. **Phase 2:** Web app wrapper
   - FastAPI backend (reuse Phase 1 logic as a module)
   - Frontend: upload two files → field selection UI → download merged `.pwx`

## Open Questions / Next Steps
- User does not yet have sample `.pwx` / `.fit` files to share for validation — write against published specs and test locally
- Start with the CLI script in a command line environment
