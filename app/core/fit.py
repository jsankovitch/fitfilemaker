"""
FIT file parsing and writing.
FIT timestamps from fitparse are naive UTC datetimes.
fit-tool expects timestamps in milliseconds since Unix epoch (UTC).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import fitparse
from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.profile_type import Event, EventType, FileType, Manufacturer, Sport

# FIT field names that map to PWX element names
FIT_TO_PWX: Dict[str, str] = {
    "heart_rate": "hr",
    "power":      "pwr",
    "cadence":    "cad",
    "speed":      "spd",
    "distance":   "dist",
    "altitude":   "alt",
}
PWX_TO_FIT: Dict[str, str] = {v: k for k, v in FIT_TO_PWX.items()}


def parse(path: Path) -> List[Dict]:
    """
    Parse a FIT file, returning record messages sorted by timestamp.
    Each record is a dict of {field_name: value}. Timestamps are naive UTC datetimes.
    Only known, named fields are included (unknown_* fields are dropped).
    """
    fit     = fitparse.FitFile(str(path))
    records = []
    for msg in fit.get_messages("record"):
        data = {
            f.name: f.value
            for f in msg
            if f.value is not None and not f.name.startswith("unknown_")
        }
        if "timestamp" in data:
            records.append(data)
    records.sort(key=lambda r: r["timestamp"])
    return records


def write(
    samples: List[Tuple[int, Dict[str, float]]],
    start_utc: datetime,
    output_path: Path,
) -> None:
    """
    Write a FIT activity file from a list of (timeoffset_seconds, {pwx_field: value}).
    start_utc must be a naive datetime in UTC.
    """
    start_ms = _to_ms(start_utc)
    end_ms   = _to_ms(start_utc + timedelta(seconds=samples[-1][0]))

    builder = FitFileBuilder(auto_define=True, min_string_size=50)

    # File ID
    msg = FileIdMessage()
    msg.type          = FileType.ACTIVITY
    msg.manufacturer  = Manufacturer.DEVELOPMENT.value
    msg.product       = 0
    msg.time_created  = start_ms
    msg.serial_number = 0
    builder.add(msg)

    # Timer start
    msg = EventMessage()
    msg.event      = Event.TIMER
    msg.event_type = EventType.START
    msg.timestamp  = start_ms
    builder.add(msg)

    # Records
    records       = []
    hr_vals: List[float]  = []
    pwr_vals: List[float] = []
    cad_vals: List[float] = []
    spd_vals: List[float] = []

    for offset, fields in samples:
        ts_ms = _to_ms(start_utc + timedelta(seconds=offset))
        msg   = RecordMessage()
        msg.timestamp = ts_ms

        if "hr" in fields:
            v = round(fields["hr"])
            msg.heart_rate = v
            hr_vals.append(v)
        if "pwr" in fields:
            v = round(fields["pwr"])
            msg.power = v
            pwr_vals.append(v)
        if "cad" in fields:
            v = round(fields["cad"])
            msg.cadence = v
            cad_vals.append(v)
        if "spd" in fields:
            v = fields["spd"]
            msg.speed          = v
            msg.enhanced_speed = v
            spd_vals.append(v)
        if "dist" in fields:
            msg.distance = fields["dist"]
        if "alt" in fields:
            v = fields["alt"]
            msg.altitude          = v
            msg.enhanced_altitude = v

        records.append(msg)

    builder.add_all(records)

    # Timer stop
    msg = EventMessage()
    msg.event      = Event.TIMER
    msg.event_type = EventType.STOP
    msg.timestamp  = end_ms
    builder.add(msg)

    elapsed_ms = end_ms - start_ms

    # Lap
    lap = LapMessage()
    lap.timestamp          = end_ms
    lap.start_time         = start_ms
    lap.total_elapsed_time = elapsed_ms
    lap.total_timer_time   = elapsed_ms
    _apply_summaries(lap, hr_vals, pwr_vals, cad_vals, spd_vals, samples)
    builder.add(lap)

    # Session
    sess = SessionMessage()
    sess.timestamp          = end_ms
    sess.start_time         = start_ms
    sess.total_elapsed_time = elapsed_ms
    sess.total_timer_time   = elapsed_ms
    sess.sport              = Sport.CYCLING.value
    _apply_summaries(sess, hr_vals, pwr_vals, cad_vals, spd_vals, samples)
    builder.add(sess)

    builder.build().to_file(str(output_path))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_ms(dt_utc: datetime) -> int:
    """Naive UTC datetime → milliseconds since Unix epoch."""
    return round(dt_utc.replace(tzinfo=timezone.utc).timestamp() * 1000)


def _apply_summaries(msg, hr_vals, pwr_vals, cad_vals, spd_vals, samples) -> None:
    if hr_vals:
        msg.avg_heart_rate = round(sum(hr_vals) / len(hr_vals))
        msg.max_heart_rate = max(int(v) for v in hr_vals)
    if pwr_vals:
        msg.avg_power = round(sum(pwr_vals) / len(pwr_vals))
        msg.max_power = max(int(v) for v in pwr_vals)
    if cad_vals:
        msg.avg_cadence = round(sum(cad_vals) / len(cad_vals))
    if spd_vals:
        msg.avg_speed = sum(spd_vals) / len(spd_vals)
    if samples:
        msg.total_distance = samples[-1][1].get("dist", 0)
