"""
Core merge logic: overlap detection, interpolation, recommendations, trim/cut, and merge.
"""

from __future__ import annotations

import statistics
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from app.core.fit import FIT_TO_PWX, PWX_TO_FIT

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

Sample  = Tuple[int, Dict[str, float]]   # (timeoffset_seconds, {field: value})
FitRec  = Dict                           # fitparse record dict (has 'timestamp')

FILE_IDS = ["f1", "f2", "f3"]


def _file_label(file_id: str) -> str:
    try:
        return f"File {FILE_IDS.index(file_id) + 1}"
    except ValueError:
        return file_id


# ---------------------------------------------------------------------------
# Overlap
# ---------------------------------------------------------------------------

def find_overlap(
    pwx_start_utc: datetime,
    pwx_samples: List[Sample],
    fit_records: List[FitRec],
) -> Tuple[List[FitRec], List[Sample]]:
    """
    Trim both datasets to their shared time window.
    Raises ValueError if no overlap exists.
    """
    pwx_end_utc = pwx_start_utc + timedelta(seconds=pwx_samples[-1][0])
    fit_start   = fit_records[0]["timestamp"]
    fit_end     = fit_records[-1]["timestamp"]

    overlap_start = max(pwx_start_utc, fit_start)
    overlap_end   = min(pwx_end_utc,   fit_end)

    if overlap_start >= overlap_end:
        raise ValueError(
            f"No time overlap between files.\n"
            f"  Base: {pwx_start_utc} → {pwx_end_utc} UTC\n"
            f"  Secondary: {fit_start} → {fit_end} UTC"
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


def find_overlap_multi(
    base_start_utc: datetime,
    base_samples: List[Sample],
    secondaries: List[Tuple[str, List[FitRec]]],  # [(file_id, fit_records), ...]
) -> Tuple[List[Sample], Dict[str, List[FitRec]]]:
    """
    Find the time window common to the base file and all secondary files.
    Returns (trimmed_base_samples, {file_id: trimmed_fit_records}).
    Raises ValueError if no common overlap exists.
    """
    base_end_utc = base_start_utc + timedelta(seconds=base_samples[-1][0])
    window_start = base_start_utc
    window_end   = base_end_utc

    for _, records in secondaries:
        if not records:
            continue
        window_start = max(window_start, records[0]["timestamp"])
        window_end   = min(window_end,   records[-1]["timestamp"])

    if window_start >= window_end:
        raise ValueError("No common time overlap between all loaded files.")

    trimmed_base = [
        (off, fields) for off, fields in base_samples
        if window_start <= base_start_utc + timedelta(seconds=off) <= window_end
    ]
    trimmed_secondary: Dict[str, List[FitRec]] = {}
    for fid, records in secondaries:
        trimmed_secondary[fid] = [
            r for r in records
            if window_start <= r["timestamp"] <= window_end
        ]

    if not trimmed_base:
        raise ValueError("No base samples fall within the common overlap window.")

    return trimmed_base, trimmed_secondary


# ---------------------------------------------------------------------------
# Interpolation
# ---------------------------------------------------------------------------

def interpolate_fit_field(
    fit_records: List[FitRec],
    fit_field: str,
    base_start_utc: datetime,
    base_offsets: List[int],
) -> Dict[int, float]:
    """
    Linearly interpolate a FIT field onto base timeoffsets.
    Returns {offset: value}.
    """
    points = []
    for r in fit_records:
        if fit_field in r and r[fit_field] is not None:
            t = (r["timestamp"] - base_start_utc).total_seconds()
            points.append((t, float(r[fit_field])))

    if not points:
        return {}

    ts = [p[0] for p in points]
    vs = [p[1] for p in points]
    result: Dict[int, float] = {}

    for off in base_offsets:
        if off <= ts[0]:
            result[off] = vs[0]
        elif off >= ts[-1]:
            result[off] = vs[-1]
        else:
            for i in range(len(ts) - 1):
                if ts[i] <= off <= ts[i + 1]:
                    span = ts[i + 1] - ts[i]
                    ratio = (off - ts[i]) / span if span else 0.0
                    result[off] = vs[i] + ratio * (vs[i + 1] - vs[i])
                    break

    return result


# ---------------------------------------------------------------------------
# Field analysis
# ---------------------------------------------------------------------------

TRAINER_MAKES = {"racermate", "computrainer", "wahoo", "tacx", "zwift", "peloton"}
GARMIN_MAKES  = {"garmin"}
PLACEHOLDER   = 0.00001


def _zero_ratio(values: List[float]) -> float:
    if not values:
        return 1.0
    return sum(1 for v in values if v == 0) / len(values)


def _placeholder_ratio(values: List[float]) -> float:
    if not values:
        return 1.0
    return sum(1 for v in values if abs(v - PLACEHOLDER) < 1e-6) / len(values)


def _has_variance(values: List[float]) -> bool:
    if len(values) < 2:
        return False
    try:
        return statistics.stdev(values) > 0.5
    except statistics.StatisticsError:
        return False


def _quality_rating(stats: Dict) -> str:
    """Return 'bad', 'fair', or 'good' from a stats dict."""
    if stats["zero_ratio"] > 0.8 or stats["placeholder_ratio"] > 0.8:
        return "bad"
    if stats["zero_ratio"] > 0.3 or not stats["has_variance"]:
        return "fair"
    return "good"


def _display_value(field: str, vals: List[float]) -> Tuple[str, Optional[str]]:
    """Return (primary_label, sub_label|None) for display in the UI."""
    non_zero = [v for v in vals if v > 0.001]
    if not non_zero:
        return "All zeros", None
    avg = sum(non_zero) / len(non_zero)
    if field == "hr":
        lo, hi = min(non_zero), max(non_zero)
        return f"avg {avg:.0f} bpm", f"Range {lo:.0f}–{hi:.0f}"
    if field == "pwr":
        return f"avg {avg:.0f} W", f"Peak {max(non_zero):.0f} W"
    if field == "cad":
        return f"avg {avg:.0f} rpm", None
    if field == "spd":
        return f"avg {avg:.1f} km/h", None
    if field == "dist":
        total_km = max(non_zero) / 1000.0
        return f"{total_km:.1f} km total", None
    if field == "alt":
        lo, hi = min(non_zero), max(non_zero)
        return f"avg {avg:.0f} m", f"Gain {max(0, hi - lo):.0f} m"
    return f"avg {avg:.2f}", None


def _build_stats(field: str, vals: List[float], device_make: Optional[str]) -> Dict:
    display_val, sub_val = _display_value(field, vals)
    base = {
        "count":             len(vals),
        "zero_ratio":        round(_zero_ratio(vals), 3),
        "placeholder_ratio": round(_placeholder_ratio(vals), 3),
        "has_variance":      _has_variance(vals),
        "device_make":       (device_make or "").lower(),
        "sample_min":        round(min(vals), 2),
        "sample_max":        round(max(vals), 2),
        "sample_avg":        round(sum(vals) / len(vals), 2),
        "display_value":     display_val,
        "sub_value":         sub_val,
    }
    base["quality"] = _quality_rating(base)
    return base


def analyze_pwx_fields(
    samples: List[Sample],
    device_make: Optional[str],
) -> Dict[str, Dict]:
    """Return per-field stats for a PWX/base file."""
    field_values: Dict[str, List[float]] = {}
    for _, fields in samples:
        for k, v in fields.items():
            field_values.setdefault(k, []).append(v)
    return {field: _build_stats(field, vals, device_make)
            for field, vals in field_values.items()}


def analyze_fit_fields(
    records: List[FitRec],
    device_make: Optional[str],
) -> Dict[str, Dict]:
    """Return per-field stats for a FIT file, keyed by PWX field name."""
    field_values: Dict[str, List[float]] = {}
    for r in records:
        for fit_field, pwx_field in FIT_TO_PWX.items():
            if fit_field in r and r[fit_field] is not None:
                try:
                    field_values.setdefault(pwx_field, []).append(float(r[fit_field]))
                except (TypeError, ValueError):
                    pass
    return {field: _build_stats(field, vals, device_make)
            for field, vals in field_values.items()}


# ---------------------------------------------------------------------------
# Recommendations (multi-file)
# ---------------------------------------------------------------------------

def recommend_for_files(
    field: str,
    file_stats: List[Tuple[str, Optional[Dict]]],  # [(file_id, stats|None), ...]
) -> Tuple[Optional[str], str]:
    """
    Return (recommended_file_id | None, reason) for up to 3 files.
    None means exclude.
    """
    available = [(fid, s) for fid, s in file_stats if s is not None]

    if not available:
        return None, "No files contain this field"
    if len(available) == 1:
        fid, _ = available[0]
        return fid, f"Only available in {_file_label(fid)}"

    # Filter out poor quality
    good = [(fid, s) for fid, s in available
            if s["zero_ratio"] <= 0.8 and s["placeholder_ratio"] <= 0.8]

    if not good:
        good = available  # all are poor — fall through to device context

    poor_ids = {fid for fid, _ in available if fid not in {g[0] for g in good}}
    if poor_ids and len(good) == 1:
        fid = good[0][0]
        poor_labels = ", ".join(_file_label(p) for p in sorted(poor_ids))
        return fid, f"{poor_labels} values are zeros or placeholders"
    if poor_ids and len(good) >= 1:
        poor_labels = ", ".join(_file_label(p) for p in sorted(poor_ids))

    candidates = good if good else available

    # Device context: trainer → prefer for power metrics
    if field in ("pwr", "cad", "spd", "dist"):
        for fid, s in candidates:
            if any(t in s.get("device_make", "") for t in TRAINER_MAKES):
                return fid, f"{_file_label(fid)} source is a calibrated trainer"

    # Device context: Garmin → prefer for HR
    if field == "hr":
        for fid, s in candidates:
            if any(g in s.get("device_make", "") for g in GARMIN_MAKES):
                return fid, f"{_file_label(fid)} source is a Garmin HR sensor"
        # No-variance HR means not recorded
        no_var = [(fid, s) for fid, s in candidates if not s.get("has_variance", True)]
        has_var = [(fid, s) for fid, s in candidates if s.get("has_variance", True)]
        if no_var and has_var:
            bad_label = _file_label(no_var[0][0])
            return has_var[0][0], f"{bad_label} HR has no variation (likely not recorded)"

    # Indoor altitude check — recommend exclude if all sources are flat
    if field == "alt":
        if all(not s.get("has_variance", True) for _, s in candidates):
            return None, "All files show flat altitude — recommended to exclude (indoor session)"

    # Default: prefer f1 if it's a good candidate, else first available
    for fid, _ in candidates:
        if fid == "f1":
            return fid, f"{_file_label(fid)} used as default base"

    fid = candidates[0][0]
    return fid, f"{_file_label(fid)} used as default"


def recommend(
    field: str,
    pwx_stats: Optional[Dict],
    fit_stats: Optional[Dict],
) -> Tuple[str, str]:
    """
    Two-file recommendation (legacy interface). Returns ('a'|'b', reason).
    """
    file_stats = [("f1", pwx_stats), ("f2", fit_stats)]
    fid, reason = recommend_for_files(field, file_stats)
    src = "a" if fid == "f1" else "b"
    return src, reason


# ---------------------------------------------------------------------------
# Trim & Cut
# ---------------------------------------------------------------------------

def apply_trim_and_cuts(
    samples: List[Sample],
    total_duration_sec: float,
    trim_start_pct: float,
    trim_end_pct: float,
    cuts: List[Tuple[float, float]],  # [(start_pct, end_pct), ...]
) -> List[Sample]:
    """
    Filter samples to the trim window and remove cut sections.
    Re-numbers offsets starting at 0, collapsing cut gaps.
    """
    if not samples:
        return []

    trim_s = trim_start_pct / 100.0 * total_duration_sec
    trim_e = trim_end_pct   / 100.0 * total_duration_sec
    cut_sec = [(s / 100.0 * total_duration_sec, e / 100.0 * total_duration_sec)
               for s, e in cuts]

    def in_cut(offset: float) -> bool:
        return any(cs <= offset < ce for cs, ce in cut_sec)

    kept = [
        (off, flds) for off, flds in samples
        if trim_s <= off <= trim_e and not in_cut(off)
    ]
    if not kept:
        return []

    # Re-number: subtract trim_start, then subtract accumulated cut gaps
    result: List[Sample] = []
    for off, flds in kept:
        removed = trim_s
        for cs, ce in cut_sec:
            if ce <= off:
                removed += (ce - cs)
        result.append((round(off - removed), flds))
    return result


# ---------------------------------------------------------------------------
# Merge (multi-file)
# ---------------------------------------------------------------------------

def build_merged_samples_multi(
    base_samples: List[Sample],
    extra_fit: Dict[str, List[FitRec]],   # {file_id: fit_records in overlap window}
    base_start_utc: datetime,
    base_file_id: str,
    field_choices: Dict[str, Optional[str]],  # {field: file_id | None}
) -> List[Sample]:
    """
    Produce the final merged sample list from up to 3 sources.
    field_choices maps each field to a file_id or None (exclude).
    """
    offsets    = [off for off, _ in base_samples]
    base_by_off = {off: fields for off, fields in base_samples}

    # Pre-interpolate all needed fields from each secondary source
    interp: Dict[str, Dict[int, float]] = {}  # {field: {offset: value}}
    for field, chosen_fid in field_choices.items():
        if chosen_fid is None or chosen_fid == base_file_id:
            continue
        if chosen_fid not in extra_fit:
            continue
        fit_field = PWX_TO_FIT.get(field)
        if fit_field:
            interp[field] = interpolate_fit_field(
                extra_fit[chosen_fid], fit_field, base_start_utc, offsets
            )

    result: List[Sample] = []
    for off in offsets:
        base_fields = base_by_off[off]
        merged: Dict[str, float] = {}

        all_fields = set(base_fields) | set(field_choices)
        for field in all_fields:
            chosen_fid = field_choices.get(field)
            if chosen_fid is None:
                continue  # excluded

            if chosen_fid == base_file_id or chosen_fid not in extra_fit:
                if field in base_fields:
                    merged[field] = base_fields[field]
            else:
                interp_val = interp.get(field, {}).get(off)
                if interp_val is not None:
                    merged[field] = interp_val
                elif field in base_fields:
                    merged[field] = base_fields[field]

        result.append((off, merged))
    return result


def build_merged_samples(
    pwx_overlap: List[Sample],
    fit_overlap: List[FitRec],
    pwx_start_utc: datetime,
    field_choices: Dict[str, str],
) -> List[Sample]:
    """Legacy two-file merge interface (kept for CLI compatibility)."""
    choices = {
        field: ("f1" if src == "a" else "f2" if src == "b" else None)
        for field, src in field_choices.items()
    }
    return build_merged_samples_multi(
        pwx_overlap, {"f2": fit_overlap}, pwx_start_utc, "f1", choices
    )
