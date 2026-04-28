"""
Input validation and sanitization.
All file validation is by content, not by filename extension.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath
from typing import Tuple

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

# PWX XML namespace
PWX_NS = "http://www.peaksware.com/PWX/1/0"

# FIT file magic: first 4 bytes of a valid FIT file header
# Byte 0: header length (0x0E = 14 bytes, or 0x0C = 12 bytes for older files)
# Bytes 8-11: ASCII ".FIT"
FIT_MAGIC = b".FIT"
FIT_MAGIC_OFFSET = 8

# Allowed characters in a user-supplied output filename (no path separators, no special chars)
_SAFE_FILENAME = re.compile(r'^[\w\-. ]{1,100}$')


def validate_file_size(data: bytes, label: str = "File") -> None:
    if len(data) > MAX_FILE_SIZE:
        raise ValueError(f"{label} exceeds the 10 MB size limit.")
    if len(data) == 0:
        raise ValueError(f"{label} is empty.")


def validate_pwx(data: bytes) -> None:
    """
    Validate that data is a well-formed PWX XML file.
    Checks: parseable XML, root tag contains PWX namespace.
    """
    try:
        root = ET.fromstring(data.decode("utf-8", errors="replace"))
    except ET.ParseError as e:
        raise ValueError(f"PWX file is not valid XML: {e}")

    if PWX_NS not in root.tag:
        raise ValueError(
            "File does not appear to be a PWX file (missing Peaksware namespace)."
        )


def validate_fit(data: bytes) -> None:
    """
    Validate that data is a FIT binary file.
    Checks: minimum length and '.FIT' magic bytes at offset 8.
    """
    if len(data) < 12:
        raise ValueError("FIT file is too short to be valid.")
    if data[FIT_MAGIC_OFFSET: FIT_MAGIC_OFFSET + 4] != FIT_MAGIC:
        raise ValueError(
            "File does not appear to be a FIT file (missing .FIT magic bytes)."
        )


def detect_and_validate(data: bytes, filename: str) -> str:
    """
    Detect file type by content, validate it, and return 'pwx' or 'fit'.
    Raises ValueError with a user-friendly message on failure.
    """
    validate_file_size(data, filename)

    # Try FIT first (binary check is fast)
    if len(data) >= 12 and data[FIT_MAGIC_OFFSET: FIT_MAGIC_OFFSET + 4] == FIT_MAGIC:
        validate_fit(data)
        return "fit"

    # Try PWX (XML)
    try:
        validate_pwx(data)
        return "pwx"
    except ValueError:
        pass

    raise ValueError(
        f"'{filename}' is not a recognized PWX or FIT file. "
        "Please upload a .pwx or .fit file."
    )


def sanitize_filename(name: str, fallback: str = "merged") -> str:
    """
    Sanitize a user-supplied output filename.
    Strips any extension, path components, and unsafe characters.
    Returns a safe stem; caller appends the correct extension.
    """
    # Strip path components
    stem = PurePosixPath(name).stem
    # Strip the extension if user included one
    stem = stem.rsplit(".", 1)[0] if "." in stem else stem
    # Collapse whitespace
    stem = " ".join(stem.split())

    if not stem or not _SAFE_FILENAME.match(stem):
        return fallback

    return stem
