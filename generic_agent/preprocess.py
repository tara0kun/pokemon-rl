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
import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np

MAX_LONG_EDGE = 480
JPEG_QUALITY = 70

# PWhiddy v2 frame stack count
FRAME_STACK_SIZE = 4


def _require_image(arr: Any, name: str) -> None:
    """Raise ValueError if ``arr`` is not a usable HxW[xC] image array.

    Guards the pure preprocessing functions against empty arrays and
    wrong-rank inputs, which would otherwise surface as opaque cv2 errors.
    """
    if not isinstance(arr, np.ndarray):
        raise ValueError(f"{name} must be a numpy.ndarray, got {type(arr).__name__}")
    if arr.size == 0:
        raise ValueError(f"{name} is an empty array (shape {arr.shape})")
    if arr.ndim not in (2, 3):
        raise ValueError(
            f"{name} must be a 2D or 3D image array, "
            f"got {arr.ndim}D (shape {arr.shape})"
        )


def stack_screens(paths: list[Path], img_size: int = 80) -> np.ndarray:
    """Load up to FRAME_STACK_SIZE screenshots and return a (4, H, W, 3) array.

    Older frames are padded with the most recent frame if fewer than 4
    paths supplied (so the model still sees a consistent 4-stack input
    early in an episode).
    """
    if not paths:
        return np.zeros(
            (FRAME_STACK_SIZE, img_size, img_size, 3), dtype=np.uint8,
        )
    frames: list[np.ndarray] = []
    for p in paths[-FRAME_STACK_SIZE:]:
        try:
            img = cv2.imread(str(p), cv2.IMREAD_COLOR)
            if img is None:
                continue
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            img = cv2.resize(img, (img_size, img_size), interpolation=cv2.INTER_AREA)
            frames.append(img)
        except (cv2.error, OSError):
            continue
    if not frames:
        return np.zeros(
            (FRAME_STACK_SIZE, img_size, img_size, 3), dtype=np.uint8,
        )
    while len(frames) < FRAME_STACK_SIZE:
        frames.insert(0, frames[0])
    return np.stack(frames, axis=0)


def load_png_as_array(path: Path) -> np.ndarray:
    arr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if arr is None:
        raise ValueError(f"failed to load image: {path}")
    return arr


def frame_hash(arr: np.ndarray) -> str:
    """64x64 downsampled MD5 — robust to tiny pixel noise."""
    _require_image(arr, "arr")
    small = cv2.resize(arr, (64, 64), interpolation=cv2.INTER_AREA)
    return hashlib.md5(small.tobytes()).hexdigest()


def frame_embedding(arr: np.ndarray, dim: int = 64) -> np.ndarray:
    """PWhiddy v1 KNN exploration embedding.

    8x8 downsampled grayscale = 64-d uint8 vec. Coarse enough that
    near-duplicate frames cluster but novel scenes are far apart in L2.
    """
    _require_image(arr, "arr")
    if not isinstance(dim, int) or dim <= 0:
        raise ValueError(f"dim must be a positive integer, got {dim!r}")
    side = math.isqrt(dim)
    if side * side != dim:
        raise ValueError(f"dim must be a positive perfect square, got {dim}")
    gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (side, side), interpolation=cv2.INTER_AREA)
    return small.flatten().astype(np.float32)


def frames_differ(
    a: np.ndarray, b: np.ndarray, threshold: float = 0.02
) -> bool:
    """Average pixel diff ratio over 160x144 downsampled grayscale.

    Returns True if mean abs diff exceeds threshold (default 2%).
    """
    if a is None or b is None:
        return True
    _require_image(a, "a")
    _require_image(b, "b")
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
    _require_image(arr, "arr")
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
