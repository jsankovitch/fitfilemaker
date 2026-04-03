#!/usr/bin/env python3
"""
pwx_to_fit.py — Convert a .pwx file to a Garmin .fit activity file.

Usage:
    python3 pwx_to_fit.py <file.pwx> [-o output.fit] [--utc-offset HOURS]

Third-party dependencies — see NOTICE file for full license text:
    fit-tool 0.9.15  BSD 3-Clause  Copyright 2021 Stages Cycling
"""

import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
import xml.etree.ElementTree as ET

from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.profile_type import Event, EventType, FileType, Manufacturer, Sport

PWX_NS = "http://www.peaksware.com/PWX/1/0"

# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

def parse_pwx(path: Path):
    """Return (start_naive_local, list of (offset_seconds, {tag: float}))."""
    tree = ET.parse(path)
    ns = {"p": PWX_NS}
    workout = tree.getroot().find("p:workout", ns)
    start = datetime.fromisoformat(workout.find("p:time", ns).text)

    samples = []
    for sample in workout.findall("p:sample", ns):
        offset = int(sample.find("p:timeoffset", ns).text)
        fields = {}
        for child in sample:
            tag = child.tag.split("}")[-1]
            if tag != "timeoffset":
                try:
                    fields[tag] = float(child.text)
                except (TypeError, ValueError):
                    pass
        samples.append((offset, fields))

    return start, samples

# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

def system_utc_offset() -> timedelta:
    diff = datetime.now() - datetime.now(timezone.utc).replace(tzinfo=None)
    return timedelta(seconds=round(diff.total_seconds() / 900) * 900)

def to_ms(dt_utc: datetime) -> int:
    """Naive UTC datetime → milliseconds since Unix epoch."""
    return round(dt_utc.replace(tzinfo=timezone.utc).timestamp() * 1000)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Convert a .pwx file to a Garmin .fit activity file."
    )
    parser.add_argument("pwx", help="Path to the .pwx file")
    parser.add_argument("-o", "--output", default=None, help="Output .fit path")
    parser.add_argument("--utc-offset", type=float, default=None,
                        help="UTC offset of PWX timestamps in hours (e.g. -5 for CDT). "
                             "Defaults to system timezone.")
    args = parser.parse_args()

    pwx_path = Path(args.pwx)
    output_path = args.output or str(pwx_path.with_suffix(".fit").name)

    print(f"Parsing PWX: {args.pwx}")
    pwx_start_local, samples = parse_pwx(pwx_path)

    utc_offset = timedelta(hours=args.utc_offset) if args.utc_offset is not None \
                 else system_utc_offset()
    pwx_start_utc = pwx_start_local - utc_offset

    print(f"PWX start (local): {pwx_start_local}  [UTC offset: {utc_offset}]")
    print(f"PWX start (UTC):   {pwx_start_utc}")
    print(f"Samples:           {len(samples)}")

    start_ms = to_ms(pwx_start_utc)
    end_ms   = to_ms(pwx_start_utc + timedelta(seconds=samples[-1][0]))

    builder = FitFileBuilder(auto_define=True, min_string_size=50)

    # --- File ID ---
    msg = FileIdMessage()
    msg.type         = FileType.ACTIVITY
    msg.manufacturer = Manufacturer.DEVELOPMENT.value
    msg.product      = 0
    msg.time_created = start_ms
    msg.serial_number = 0
    builder.add(msg)

    # --- Timer start ---
    msg = EventMessage()
    msg.event      = Event.TIMER
    msg.event_type = EventType.START
    msg.timestamp  = start_ms
    builder.add(msg)

    # --- Records ---
    records = []
    hr_vals, pwr_vals, cad_vals, spd_vals = [], [], [], []

    for offset, fields in samples:
        ts_ms = to_ms(pwx_start_utc + timedelta(seconds=offset))
        msg = RecordMessage()
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
            msg.speed = v
            msg.enhanced_speed = v
            spd_vals.append(v)
        if "dist" in fields:
            msg.distance = fields["dist"]
        if "alt" in fields:
            v = fields["alt"]
            msg.altitude = v
            msg.enhanced_altitude = v

        records.append(msg)

    builder.add_all(records)

    # --- Timer stop ---
    msg = EventMessage()
    msg.event      = Event.TIMER
    msg.event_type = EventType.STOP
    msg.timestamp  = end_ms
    builder.add(msg)

    elapsed_ms = end_ms - start_ms

    # --- Lap ---
    msg = LapMessage()
    msg.timestamp          = end_ms
    msg.start_time         = start_ms
    msg.total_elapsed_time = elapsed_ms
    msg.total_timer_time   = elapsed_ms
    if hr_vals:
        msg.avg_heart_rate = round(sum(hr_vals) / len(hr_vals))
        msg.max_heart_rate = max(hr_vals)
    if pwr_vals:
        msg.avg_power = round(sum(pwr_vals) / len(pwr_vals))
        msg.max_power = max(pwr_vals)
    if cad_vals:
        msg.avg_cadence = round(sum(cad_vals) / len(cad_vals))
    if spd_vals:
        msg.avg_speed = sum(spd_vals) / len(spd_vals)
    if samples:
        msg.total_distance = samples[-1][1].get("dist", 0)
    builder.add(msg)

    # --- Session ---
    msg = SessionMessage()
    msg.timestamp          = end_ms
    msg.start_time         = start_ms
    msg.total_elapsed_time = elapsed_ms
    msg.total_timer_time   = elapsed_ms
    msg.sport              = Sport.CYCLING.value
    if hr_vals:
        msg.avg_heart_rate = round(sum(hr_vals) / len(hr_vals))
        msg.max_heart_rate = max(hr_vals)
    if pwr_vals:
        msg.avg_power = round(sum(pwr_vals) / len(pwr_vals))
        msg.max_power = max(pwr_vals)
    if cad_vals:
        msg.avg_cadence = round(sum(cad_vals) / len(cad_vals))
    if spd_vals:
        msg.avg_speed = sum(spd_vals) / len(spd_vals)
    if samples:
        msg.total_distance = samples[-1][1].get("dist", 0)
    builder.add(msg)

    fit_file = builder.build()
    fit_file.to_file(output_path)
    print(f"FIT file written to: {output_path}")


if __name__ == "__main__":
    main()
