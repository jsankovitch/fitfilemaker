#!/usr/bin/env python3
"""
merge.py — Merge a Garmin .fit file into a coach .pwx file.

Reads power/cadence/structure from the PWX and heart rate (and optionally
other fields) from the FIT, producing a single merged .pwx for upload to
TrainingPeaks.

Usage:
    python3 merge.py <file.pwx> <file.fit> [-o output.pwx] [--utc-offset HOURS]

Third-party dependencies — see NOTICE file for full license text:
    fitparse 1.2.0  MIT License  Copyright (c) 2011-2020 David Cooper et al.
"""

import argparse
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
import xml.etree.ElementTree as ET

import fitparse

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PWX_NS = "http://www.peaksware.com/PWX/1/0"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

# Maps FIT field names → PWX element tag names
FIT_TO_PWX = {
    "heart_rate": "hr",
    "power":      "pwr",
    "cadence":    "cad",
}
PWX_TO_FIT = {v: k for k, v in FIT_TO_PWX.items()}

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_pwx(path: Path):
    """Return (ElementTree, start_datetime_naive_local, list of (offset_int, {tag: text}))."""
    ET.register_namespace("",    PWX_NS)
    ET.register_namespace("xsi", XSI_NS)

    tree = ET.parse(path)
    root = tree.getroot()
    ns = {"p": PWX_NS}

    workout  = root.find("p:workout", ns)
    time_str = workout.find("p:time",   ns).text   # e.g. "2026-04-02T07:31:24"
    start    = datetime.fromisoformat(time_str)     # naive local time

    samples = []
    for sample in workout.findall("p:sample", ns):
        offset = int(sample.find("p:timeoffset", ns).text)
        fields = {}
        for child in sample:
            tag = child.tag.split("}")[-1]
            if tag != "timeoffset":
                fields[tag] = child.text
        samples.append((offset, fields))

    return tree, start, samples


def parse_fit(path: Path) -> list[dict]:
    """Return list of record dicts sorted by timestamp (naive UTC datetimes)."""
    fit     = fitparse.FitFile(str(path))
    records = []
    for msg in fit.get_messages("record"):
        data = {f.name: f.value for f in msg if f.value is not None}
        if "timestamp" in data:
            # fitparse returns naive datetimes already in UTC
            records.append(data)
    records.sort(key=lambda r: r["timestamp"])
    return records

# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

def system_utc_offset() -> timedelta:
    """Return the host system's UTC offset, rounded to the nearest 15 min."""
    diff = datetime.now() - datetime.now(timezone.utc).replace(tzinfo=None)
    return timedelta(seconds=round(diff.total_seconds() / 900) * 900)


def pwx_to_utc(start_local: datetime, offset: timedelta) -> datetime:
    """Convert naive local PWX start time to naive UTC datetime."""
    return start_local - offset   # UTC = local − offset  (e.g. local−(−5h) = local+5h)

# ---------------------------------------------------------------------------
# Overlap & interpolation
# ---------------------------------------------------------------------------

def find_overlap(pwx_start_utc, pwx_samples, fit_records):
    """
    Trim both datasets to their shared time window.
    Returns (fit_overlap, pwx_overlap) as filtered lists.
    """
    pwx_end_utc = pwx_start_utc + timedelta(seconds=pwx_samples[-1][0])
    fit_start   = fit_records[0]["timestamp"]
    fit_end     = fit_records[-1]["timestamp"]

    overlap_start = max(pwx_start_utc, fit_start)
    overlap_end   = min(pwx_end_utc,   fit_end)

    if overlap_start >= overlap_end:
        raise ValueError(
            f"No time overlap between files.\n"
            f"  PWX: {pwx_start_utc} → {pwx_end_utc} UTC\n"
            f"  FIT: {fit_start} → {fit_end} UTC"
        )

    fit_overlap = [
        r for r in fit_records
        if overlap_start <= r["timestamp"] <= overlap_end
    ]
    pwx_overlap = [
        (off, fields) for off, fields in pwx_samples
        if overlap_start <= pwx_start_utc + timedelta(seconds=off) <= overlap_end
    ]
    return fit_overlap, pwx_overlap


def interpolate(fit_records, fit_field, pwx_start_utc, pwx_offsets) -> dict:
    """
    Linearly interpolate a FIT field onto the given PWX timeoffsets.
    Returns {offset: interpolated_value}.
    """
    points = []
    for r in fit_records:
        if fit_field in r:
            t = (r["timestamp"] - pwx_start_utc).total_seconds()
            points.append((t, float(r[fit_field])))

    if not points:
        return {}

    ts = [p[0] for p in points]
    vs = [p[1] for p in points]
    result = {}

    for off in pwx_offsets:
        if off <= ts[0]:
            result[off] = vs[0]
        elif off >= ts[-1]:
            result[off] = vs[-1]
        else:
            for i in range(len(ts) - 1):
                if ts[i] <= off <= ts[i + 1]:
                    ratio = (off - ts[i]) / (ts[i + 1] - ts[i]) if ts[i + 1] != ts[i] else 0
                    result[off] = vs[i] + ratio * (vs[i + 1] - vs[i])
                    break

    return result

# ---------------------------------------------------------------------------
# Field selection prompt
# ---------------------------------------------------------------------------

def prompt_field_selection(pwx_fields: set, fit_fields_as_pwx: set) -> dict:
    """
    Interactively ask the user which source to use for each field.
    Returns {pwx_tag: 'pwx' | 'fit'}.
    """
    choices = {}
    all_fields = sorted(pwx_fields | fit_fields_as_pwx)

    print("\n--- Field Source Selection ---")
    print("Each field can come from the PWX (coach file) or FIT (Garmin recording).\n")

    for field in all_fields:
        in_pwx = field in pwx_fields
        in_fit = field in fit_fields_as_pwx

        if in_pwx and not in_fit:
            print(f"  {field:6s}  PWX only  → keeping PWX")
            choices[field] = "pwx"
        elif in_fit and not in_pwx:
            print(f"  {field:6s}  FIT only  → keeping FIT")
            choices[field] = "fit"
        else:
            while True:
                ans = input(f"  {field:6s}  in both   → use [P]WX or [F]IT? ").strip().upper()
                if ans in ("P", "PWX"):
                    choices[field] = "pwx"
                    break
                elif ans in ("F", "FIT"):
                    choices[field] = "fit"
                    break
                print("    Please enter P or F.")

    return choices

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Merge a Garmin .fit file into a coach .pwx file."
    )
    parser.add_argument("pwx",  help="Path to the .pwx file (coach workout)")
    parser.add_argument("fit",  help="Path to the .fit file (Garmin recording)")
    parser.add_argument("-o", "--output", default="merged.pwx",
                        help="Output file path (default: merged.pwx)")
    parser.add_argument("--utc-offset", type=float, default=None,
                        help="UTC offset of the PWX timestamps in hours "
                             "(e.g. -5 for CDT). Defaults to system timezone.")
    args = parser.parse_args()

    # --- Parse ---
    print(f"Parsing PWX:  {args.pwx}")
    tree, pwx_start_local, pwx_samples = parse_pwx(Path(args.pwx))

    print(f"Parsing FIT:  {args.fit}")
    fit_records = parse_fit(Path(args.fit))

    # --- Timestamps ---
    if args.utc_offset is not None:
        utc_offset = timedelta(hours=args.utc_offset)
    else:
        utc_offset = system_utc_offset()

    pwx_start_utc = pwx_to_utc(pwx_start_local, utc_offset)
    fit_start     = fit_records[0]["timestamp"]
    fit_end       = fit_records[-1]["timestamp"]

    print(f"\nPWX start (local): {pwx_start_local}  [UTC offset: {utc_offset}]")
    print(f"PWX start (UTC):   {pwx_start_utc}")
    print(f"FIT range  (UTC):  {fit_start}  →  {fit_end}")

    # --- Overlap ---
    try:
        fit_overlap, pwx_overlap = find_overlap(pwx_start_utc, pwx_samples, fit_records)
    except ValueError as e:
        print(f"\nERROR: {e}")
        print("Tip: pass --utc-offset to specify the PWX file's UTC offset manually.")
        sys.exit(1)

    pwx_offsets = [off for off, _ in pwx_overlap]
    print(f"\nOverlap window: {len(pwx_overlap)} PWX samples, {len(fit_overlap)} FIT records")

    # --- Field selection ---
    pwx_fields        = set(pwx_samples[0][1].keys())
    fit_fields_as_pwx = {FIT_TO_PWX[f] for f in FIT_TO_PWX if
                         any(f in r for r in fit_overlap)}

    choices = prompt_field_selection(pwx_fields, fit_fields_as_pwx)

    # --- Interpolate FIT fields ---
    fit_data = {}
    for pwx_tag, source in choices.items():
        if source == "fit":
            fit_field = PWX_TO_FIT.get(pwx_tag)
            if fit_field:
                fit_data[pwx_tag] = interpolate(
                    fit_overlap, fit_field, pwx_start_utc, pwx_offsets
                )

    # --- Patch the XML tree ---
    ns             = {"p": PWX_NS}
    overlap_set    = set(pwx_offsets)
    workout        = tree.getroot().find("p:workout", ns)

    for sample in workout.findall("p:sample", ns):
        offset = int(sample.find("p:timeoffset", ns).text)
        if offset not in overlap_set:
            continue
        for child in sample:
            tag = child.tag.split("}")[-1]
            if tag == "timeoffset":
                continue
            if choices.get(tag) == "fit" and tag in fit_data and offset in fit_data[tag]:
                val = fit_data[tag][offset]
                child.text = str(round(val))

    # --- Write output ---
    tree.write(args.output, encoding="unicode", xml_declaration=True)
    print(f"\nMerged file written to: {args.output}")


if __name__ == "__main__":
    main()
