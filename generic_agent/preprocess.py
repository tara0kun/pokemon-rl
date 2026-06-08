"""Image preprocessing for cost-optimized Brain calls.

Inspired by the user's pokemon_auto_player.py design:
- JPG encoding (vs PNG): 50%+ size reduction
- Resize cap: limit max edge to MAX_LONG_EDGE
- Optional grayscale (Pokemon needs color, default off)

GBA native resolution is 240x160 — already tiny. Mostly we just
re-encode to JPG and keep at native size.
"""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any

import cv2
import numpy as np

MAX_LONG_EDGE = 480
JPEG_QUALITY = 70


def load_png_as_array(path: Path) -> np.ndarray:
    arr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if arr is None:
        raise ValueError(f"failed to load image: {path}")
    return arr


def frame_hash(arr: np.ndarray) -> str:
    """64x64 downsampled MD5 — robust to tiny pixel noise."""
    small = cv2.resize(arr, (64, 64), interpolation=cv2.INTER_AREA)
    return hashlib.md5(small.tobytes()).hexdigest()


def frames_differ(
    a: np.ndarray, b: np.ndarray, threshold: float = 0.02
) -> bool:
    """Average pixel diff ratio over 160x144 downsampled grayscale.

    Returns True if mean abs diff exceeds threshold (default 2%).
    """
    if a is None or b is None:
        return True
    ga = cv2.cvtColor(cv2.resize(a, (160, 144)), cv2.COLOR_BGR2GRAY)
    gb = cv2.cvtColor(cv2.resize(b, (160, 144)), cv2.COLOR_BGR2GRAY)
    diff = float(np.mean(np.abs(ga.astype(float) - gb.astype(float)))) / 255.0
    return diff > threshold


def to_jpeg_b64(
    arr: np.ndarray,
    max_long_edge: int = MAX_LONG_EDGE,
    quality: int = JPEG_QUALITY,
) -> tuple[str, int]:
    """Return (base64 string, byte size) of JPEG-encoded resized image."""
    img = arr
    h, w = img.shape[:2]
    long_edge = max(h, w)
    if long_edge > max_long_edge:
        scale = max_long_edge / long_edge
        img = cv2.resize(
            img,
            (int(w * scale), int(h * scale)),
            interpolation=cv2.INTER_AREA,
        )
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("jpeg encode failed")
    raw = buf.tobytes()
    return base64.standard_b64encode(raw).decode("ascii"), len(raw)


def png_path_to_jpeg_block(path: Path) -> tuple[dict[str, Any], int, str]:
    """Convert PNG path → (Anthropic image block, byte size, frame hash)."""
    arr = load_png_as_array(path)
    fhash = frame_hash(arr)
    b64, size = to_jpeg_b64(arr)
    block = {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": b64,
        },
    }
    return block, size, fhash
