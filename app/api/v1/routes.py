"""
API v1 routes.

POST /api/v1/analyze  — upload two files, get field inventory + recommendations
POST /api/v1/merge    — upload two files + config, download merged file
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core import fit as fit_core
from app.core import pwx as pwx_core
from app.core import merger
from app.core.security import detect_and_validate, sanitize_filename

router = APIRouter(prefix="/api/v1")

PWX_NS = "http://www.peaksware.com/PWX/1/0"
NS_MAP = {"p": PWX_NS}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_uploaded(data: bytes, file_type: str, filename: str):
    """Parse validated file bytes. Returns (type-specific object, device_make)."""
    if file_type == "pwx":
        tree, start_local, samples = pwx_core.parse(io.BytesIO(data) if False else _bytes_to_path(data, ".pwx"))
        make = _pwx_device_make(tree)
        return ("pwx", tree, start_local, samples, make)
    else:
        records = fit_core.parse(_bytes_to_path(data, ".fit"))
        make = _fit_device_make(records)
        return ("fit", None, None, records, make)


def _bytes_to_path(data: bytes, suffix: str):
    """Write bytes to a tempfile-like object that the parsers can read."""
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        os.write(fd, data)
        os.close(fd)
        from pathlib import Path
        return Path(path)
    except Exception:
        os.close(fd)
        raise


def _cleanup(path):
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


def _pwx_device_make(tree: ET.ElementTree) -> Optional[str]:
    workout = tree.getroot().find("p:workout", NS_MAP)
    if workout is None:
        return None
    device = workout.find("p:device", NS_MAP)
    if device is None:
        return None
    make_el = device.find("p:make", NS_MAP)
    return make_el.text if make_el is not None else None


def _fit_device_make(records: List[Dict]) -> Optional[str]:
    # fitparse doesn't expose device info through record messages;
    # manufacturer is in the file_id message — return None for now.
    # Future: parse file_id message for manufacturer.
    return None


def _utc_offset() -> timedelta:
    return pwx_core.system_utc_offset()


def _pwx_start_utc(start_local: datetime) -> datetime:
    return pwx_core.local_to_utc(start_local, _utc_offset())


# ---------------------------------------------------------------------------
# /analyze
# ---------------------------------------------------------------------------

class FieldInfo(BaseModel):
    in_a: bool
    in_b: bool
    stats_a: Optional[Dict] = None
    stats_b: Optional[Dict] = None
    recommended: str          # 'a', 'b', or 'avg'
    reason: str


class AnalyzeResponse(BaseModel):
    file_a_type: str
    file_b_type: str
    overlap_seconds: int
    pwx_sample_count: int
    fields: Dict[str, FieldInfo]
    suggested_filename: str


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(
    file_a: UploadFile = File(..., description="First file (PWX or FIT)"),
    file_b: UploadFile = File(..., description="Second file (PWX or FIT)"),
):
    data_a = await file_a.read()
    data_b = await file_b.read()

    try:
        type_a = detect_and_validate(data_a, file_a.filename or "file_a")
        type_b = detect_and_validate(data_b, file_b.filename or "file_b")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if type_a == type_b:
        raise HTTPException(
            status_code=422,
            detail=f"Both files are {type_a.upper()} files. Please upload one PWX and one FIT file."
        )

    # Normalize: a=PWX, b=FIT
    if type_a == "fit":
        data_a, data_b = data_b, data_a
        type_a, type_b = type_b, type_a

    path_a = path_b = None
    try:
        path_a = _bytes_to_path(data_a, ".pwx")
        path_b = _bytes_to_path(data_b, ".fit")

        tree, start_local, pwx_samples = pwx_core.parse(path_a)
        fit_records = fit_core.parse(path_b)

        pwx_make = _pwx_device_make(tree)
        fit_make = _fit_device_make(fit_records)

        start_utc = _pwx_start_utc(start_local)

        try:
            fit_overlap, pwx_overlap = merger.find_overlap(start_utc, pwx_samples, fit_records)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

        pwx_stats = merger.analyze_pwx_fields(pwx_overlap, pwx_make)
        fit_stats = merger.analyze_fit_fields(fit_overlap, fit_make)

        all_fields = set(pwx_stats) | set(fit_stats)
        fields_out: Dict[str, FieldInfo] = {}
        for field in sorted(all_fields):
            p = pwx_stats.get(field)
            f = fit_stats.get(field)
            rec, reason = merger.recommend(field, p, f)
            fields_out[field] = FieldInfo(
                in_a=field in pwx_stats,
                in_b=field in fit_stats,
                stats_a=p,
                stats_b=f,
                recommended=rec,
                reason=reason,
            )

        # Suggested filename from PWX start time
        suggested = f"merged_{start_local.strftime('%Y-%m-%d_%H%M')}"

        return AnalyzeResponse(
            file_a_type="pwx",
            file_b_type="fit",
            overlap_seconds=len(pwx_overlap),
            pwx_sample_count=len(pwx_samples),
            fields=fields_out,
            suggested_filename=suggested,
        )
    finally:
        if path_a:
            _cleanup(path_a)
        if path_b:
            _cleanup(path_b)


# ---------------------------------------------------------------------------
# /merge
# ---------------------------------------------------------------------------

@router.post("/merge")
async def merge(
    file_a: UploadFile = File(..., description="PWX file"),
    file_b: UploadFile = File(..., description="FIT file"),
    choices: str = Form(..., description='JSON: {"hr":"b","pwr":"a",...}'),
    output_format: str = Form(..., description='"pwx" or "fit"'),
    filename: str = Form(..., description="Output filename stem (no extension)"),
):
    import json

    data_a = await file_a.read()
    data_b = await file_b.read()

    # Validate inputs
    try:
        type_a = detect_and_validate(data_a, file_a.filename or "file_a")
        type_b = detect_and_validate(data_b, file_b.filename or "file_b")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if type_a == type_b:
        raise HTTPException(status_code=422, detail="Please upload one PWX and one FIT file.")

    if type_a == "fit":
        data_a, data_b = data_b, data_a

    if output_format not in ("pwx", "fit"):
        raise HTTPException(status_code=422, detail="output_format must be 'pwx' or 'fit'.")

    try:
        field_choices: Dict[str, str] = json.loads(choices)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=422, detail="Invalid choices JSON.")

    valid_sources = {"a", "b", "avg"}
    for field, source in field_choices.items():
        if source not in valid_sources:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid source '{source}' for field '{field}'. Must be 'a', 'b', or 'avg'."
            )

    safe_stem = sanitize_filename(filename)
    output_filename = f"{safe_stem}.{output_format}"

    path_a = path_b = output_path = None
    try:
        path_a = _bytes_to_path(data_a, ".pwx")
        path_b = _bytes_to_path(data_b, ".fit")

        tree, start_local, pwx_samples = pwx_core.parse(path_a)
        fit_records = fit_core.parse(path_b)

        start_utc = _pwx_start_utc(start_local)

        try:
            fit_overlap, pwx_overlap = merger.find_overlap(start_utc, pwx_samples, fit_records)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

        merged_samples = merger.build_merged_samples(
            pwx_overlap, fit_overlap, start_utc, field_choices
        )

        # Build output in memory
        if output_format == "pwx":
            import tempfile, os
            fd, out_str = tempfile.mkstemp(suffix=".pwx")
            os.close(fd)
            output_path = __import__("pathlib").Path(out_str)

            patches = {off: fields for off, fields in merged_samples}
            pwx_core.patch_samples(tree, patches)
            pwx_core.write(tree, output_path)

            content = output_path.read_text(encoding="utf-8")
            media_type = "application/xml"
            content_bytes = content.encode("utf-8")
        else:
            import tempfile, os
            fd, out_str = tempfile.mkstemp(suffix=".fit")
            os.close(fd)
            output_path = __import__("pathlib").Path(out_str)

            fit_core.write(merged_samples, start_utc, output_path)
            content_bytes = output_path.read_bytes()
            media_type = "application/octet-stream"

        return StreamingResponse(
            io.BytesIO(content_bytes),
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{output_filename}"',
                "Content-Length": str(len(content_bytes)),
            },
        )
    finally:
        for p in (path_a, path_b, output_path):
            if p:
                _cleanup(p)
