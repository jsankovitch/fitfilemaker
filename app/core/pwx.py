"""
PWX file parsing and writing.
PWX is XML (http://www.peaksware.com/PWX/1/0).
Timestamps in the file are naive local-time datetimes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Tuple, Dict
import xml.etree.ElementTree as ET

PWX_NS  = "http://www.peaksware.com/PWX/1/0"
XSI_NS  = "http://www.w3.org/2001/XMLSchema-instance"
NS_MAP  = {"p": PWX_NS}

# Fields present in a <sample> element (excluding timeoffset)
SAMPLE_FIELDS = ("hr", "spd", "pwr", "cad", "dist", "alt")


def _register_ns() -> None:
    ET.register_namespace("",    PWX_NS)
    ET.register_namespace("xsi", XSI_NS)


def parse(path: Path) -> Tuple[ET.ElementTree, datetime, List[Tuple[int, Dict[str, float]]]]:
    """
    Parse a PWX file.

    Returns:
        tree        — the ElementTree (needed for writing back)
        start_local — naive local datetime from <time>
        samples     — list of (timeoffset_seconds, {field: float_value})
    """
    _register_ns()
    tree    = ET.parse(path)
    workout = tree.getroot().find("p:workout", NS_MAP)
    start   = datetime.fromisoformat(workout.find("p:time", NS_MAP).text)

    samples: List[Tuple[int, Dict[str, float]]] = []
    for sample in workout.findall("p:sample", NS_MAP):
        offset = int(sample.find("p:timeoffset", NS_MAP).text)
        fields: Dict[str, float] = {}
        for child in sample:
            tag = child.tag.split("}")[-1]
            if tag != "timeoffset":
                try:
                    fields[tag] = float(child.text)
                except (TypeError, ValueError):
                    pass
        samples.append((offset, fields))

    return tree, start, samples


def write(tree: ET.ElementTree, path: Path) -> None:
    """Write a (potentially modified) PWX ElementTree to a file."""
    _register_ns()
    tree.write(str(path), encoding="unicode", xml_declaration=True)


def patch_samples(
    tree: ET.ElementTree,
    patches: Dict[int, Dict[str, float]],
) -> None:
    """
    Update sample field values in-place on the ElementTree.

    patches: {timeoffset: {field: new_value}}
    """
    workout = tree.getroot().find("p:workout", NS_MAP)
    for sample in workout.findall("p:sample", NS_MAP):
        offset = int(sample.find("p:timeoffset", NS_MAP).text)
        if offset not in patches:
            continue
        field_patches = patches[offset]
        for child in sample:
            tag = child.tag.split("}")[-1]
            if tag in field_patches:
                child.text = str(round(field_patches[tag]))


def local_to_utc(start_local: datetime, utc_offset: timedelta) -> datetime:
    """Convert naive local PWX start time to naive UTC."""
    return start_local - utc_offset


def system_utc_offset() -> timedelta:
    diff = datetime.now() - datetime.now(timezone.utc).replace(tzinfo=None)
    return timedelta(seconds=round(diff.total_seconds() / 900) * 900)
