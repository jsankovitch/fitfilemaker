"""
Core merge logic: overlap detection, interpolation, averaging, and recommendations.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from app.core.fit import FIT_TO_PWX, PWX_TO_FIT

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

Sample  = Tuple[int, Dict[str, float]]   # (timeoffset_seconds, {field: value})
FitRec  = Dict                           # fitparse record dict (has 'timestamp')


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


# ---------------------------------------------------------------------------
# Interpolation
# ---------------------------------------------------------------------------

def interpolate_fit_field(
    fit_records: List[FitRec],
    fit_field: str,
    pwx_start_utc: datetime,
    pwx_offsets: List[int],
) -> Dict[int, float]:
    """
    Linearly interpolate a FIT field onto PWX timeoffsets.
    Returns {offset: value}.
    """
    points = []
    for r in fit_records:
        if fit_field in r and r[fit_field] is not None:
            t = (r["timestamp"] - pwx_start_utc).total_seconds()
            points.append((t, float(r[fit_field])))

    if not points:
        return {}

    ts = [p[0] for p in points]
    vs = [p[1] for p in points]
    result: Dict[int, float] = {}

    for off in pwx_offsets:
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
# Field analysis & recommendations
# ---------------------------------------------------------------------------

TRAINER_MAKES = {"racermate", "computrainer", "wahoo", "tacx", "zwift", "peloton"}
GARMIN_MAKES  = {"garmin"}
PLACEHOLDER   = 0.00001   # RacerMate filler value


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


def analyze_pwx_fields(
    samples: List[Sample],
    device_make: Optional[str],
) -> Dict[str, Dict]:
    """
    Return per-field stats for a PWX file.
    {field: {values: [...], zero_ratio, placeholder_ratio, has_variance, device_make}}
    """
    field_values: Dict[str, List[float]] = {}
    for _, fields in samples:
        for k, v in fields.items():
            field_values.setdefault(k, []).append(v)

    result = {}
    for field, vals in field_values.items():
        result[field] = {
            "count":             len(vals),
            "zero_ratio":        round(_zero_ratio(vals), 3),
            "placeholder_ratio": round(_placeholder_ratio(vals), 3),
            "has_variance":      _has_variance(vals),
            "device_make":       (device_make or "").lower(),
            "sample_min":        round(min(vals), 2),
            "sample_max":        round(max(vals), 2),
            "sample_avg":        round(sum(vals) / len(vals), 2),
        }
    return result


def analyze_fit_fields(
    records: List[FitRec],
    device_make: Optional[str],
) -> Dict[str, Dict]:
    """
    Return per-field stats for a FIT file, keyed by PWX field name.
    """
    field_values: Dict[str, List[float]] = {}
    for r in records:
        for fit_field, pwx_field in FIT_TO_PWX.items():
            if fit_field in r and r[fit_field] is not None:
                try:
                    field_values.setdefault(pwx_field, []).append(float(r[fit_field]))
                except (TypeError, ValueError):
                    pass

    result = {}
    for field, vals in field_values.items():
        result[field] = {
            "count":             len(vals),
            "zero_ratio":        round(_zero_ratio(vals), 3),
            "placeholder_ratio": round(_placeholder_ratio(vals), 3),
            "has_variance":      _has_variance(vals),
            "device_make":       (device_make or "").lower(),
            "sample_min":        round(min(vals), 2),
            "sample_max":        round(max(vals), 2),
            "sample_avg":        round(sum(vals) / len(vals), 2),
        }
    return result


def recommend(
    field: str,
    pwx_stats: Optional[Dict],
    fit_stats: Optional[Dict],
) -> Tuple[str, str]:
    """
    Return (recommended_source, reason) for a field.
    recommended_source is 'a' (PWX), 'b' (FIT), or 'avg'.
    """
    if pwx_stats is None:
        return "b", "Only available in FIT file"
    if fit_stats is None:
        return "a", "Only available in PWX file"

    pwx_make = pwx_stats.get("device_make", "")
    fit_make = fit_stats.get("device_make", "")

    # Device context rules
    is_trainer      = any(t in pwx_make for t in TRAINER_MAKES)
    is_garmin_fit   = any(g in fit_make for g in GARMIN_MAKES)

    # Quality flags
    pwx_poor = pwx_stats["zero_ratio"] > 0.8 or pwx_stats["placeholder_ratio"] > 0.8
    fit_poor = fit_stats["zero_ratio"] > 0.8 or fit_stats["placeholder_ratio"] > 0.8

    if pwx_poor and not fit_poor:
        return "b", "PWX values appear to be zeros or placeholders"
    if fit_poor and not pwx_poor:
        return "a", "FIT values appear to be zeros or placeholders"

    # Field-specific device context
    if field in ("pwr", "cad", "spd", "dist") and is_trainer:
        return "a", "PWX source is a calibrated trainer"
    if field == "hr" and is_garmin_fit:
        return "b", "FIT source is a Garmin HR sensor"
    if field == "hr" and not pwx_stats["has_variance"]:
        return "b", "PWX heart rate has no variation (likely not recorded)"

    # Default: prefer PWX as the base file
    return "a", "PWX used as default base"


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

def build_merged_samples(
    pwx_overlap: List[Sample],
    fit_overlap: List[FitRec],
    pwx_start_utc: datetime,
    field_choices: Dict[str, str],  # {field: 'a' | 'b' | 'avg'}
) -> List[Sample]:
    """
    Produce the final merged sample list.
    field_choices maps each PWX field name to its source:
      'a'   → keep PWX value
      'b'   → use interpolated FIT value
      'avg' → average PWX and interpolated FIT values
    """
    pwx_offsets = [off for off, _ in pwx_overlap]
    pwx_by_off  = {off: fields for off, fields in pwx_overlap}

    # Pre-interpolate all FIT fields that are needed
    fit_interp: Dict[str, Dict[int, float]] = {}
    for field, source in field_choices.items():
        if source in ("b", "avg"):
            fit_field = PWX_TO_FIT.get(field)
            if fit_field:
                fit_interp[field] = interpolate_fit_field(
                    fit_overlap, fit_field, pwx_start_utc, pwx_offsets
                )

    result: List[Sample] = []
    for off in pwx_offsets:
        pwx_fields = pwx_by_off[off]
        merged: Dict[str, float] = {}

        for field in pwx_fields:
            source = field_choices.get(field, "a")
            pwx_val = pwx_fields[field]

            if source == "a":
                merged[field] = pwx_val
            elif source == "b":
                merged[field] = fit_interp.get(field, {}).get(off, pwx_val)
            elif source == "avg":
                fit_val = fit_interp.get(field, {}).get(off)
                merged[field] = (pwx_val + fit_val) / 2.0 if fit_val is not None else pwx_val

        result.append((off, merged))

    return result
