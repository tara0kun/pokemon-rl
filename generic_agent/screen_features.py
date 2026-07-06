"""Lightweight screen feature detection — no OCR, no API, just cv2.

The frame_hash signal in preprocess.py only tells us whether the screen
changed. We need richer signals: is there a dialog box open? is there a
menu open? is there an NPC sprite in front of the player?

Approach: region-based checks tuned to Pokemon Emerald's fixed UI
layout (240x160 GBA framebuffer). Each detector is one cv2 op (no model
load, sub-ms). Plays well with heuristic.py — gives it a Boolean signal
"there's a dialog" that the frame_hash approximation misses on quick
text transitions.

Detectors:
- dialog_open(img):  bottom text box high white ratio + black text
- menu_open(img):    right-side menu strip white ratio
- letter_entry(img): on the name-entry screen (block of keyboard tiles)
- battle_panel(img): bottom battle menu (FIGHT/PKMN/ITEM/RUN)
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

GBA_W, GBA_H = 240, 160

# Pokemon Emerald dialog box: bottom strip, white background with black text.
DIALOG_TOP_Y = 112
DIALOG_BOTTOM_Y = 152
DIALOG_LEFT_X = 8
DIALOG_RIGHT_X = 232

# Pokemon Emerald Start menu: right side strip, multiple white panels.
MENU_LEFT_X = 160
MENU_RIGHT_X = 232
MENU_TOP_Y = 8
MENU_BOTTOM_Y = 152

# Letter entry screen: dense grid of letter tiles in middle.
LETTERS_TOP_Y = 56
LETTERS_BOTTOM_Y = 128


def _load_img(path: str | Path) -> np.ndarray | None:
    try:
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is None:
            return None
        if img.shape[0] != GBA_H or img.shape[1] != GBA_W:
            img = cv2.resize(img, (GBA_W, GBA_H))
        return img
    except (cv2.error, OSError):
        return None


def _white_ratio(img: np.ndarray, threshold: int = 220) -> float:
    if img.size == 0:
        return 0.0
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return float((gray > threshold).mean())


def _dark_ratio(img: np.ndarray, threshold: int = 80) -> float:
    if img.size == 0:
        return 0.0
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return float((gray < threshold).mean())


def dialog_open(img: np.ndarray) -> bool:
    """True if a text dialog box is currently visible.

    Dialog detection = high white ratio in bottom strip AND clearly
    lower white ratio in upper half. This distinguishes a dialog
    (predominantly white bottom band) from an all-white scene like a
    transition or a building interior with white walls.
    """
    bottom = img[DIALOG_TOP_Y:DIALOG_BOTTOM_Y, DIALOG_LEFT_X:DIALOG_RIGHT_X]
    upper = img[0:DIALOG_TOP_Y, 0:GBA_W]
    wb = _white_ratio(bottom)
    wu = _white_ratio(upper)
    return wb > 0.45 and (wb - wu) > 0.20


def menu_open(img: np.ndarray) -> bool:
    """True if Start menu is visible on the right side."""
    region = img[MENU_TOP_Y:MENU_BOTTOM_Y, MENU_LEFT_X:MENU_RIGHT_X]
    w = _white_ratio(region)
    return w > 0.55


def letter_entry(img: np.ndarray) -> bool:
    """True if we're stuck on the name-entry keyboard screen."""
    region = img[LETTERS_TOP_Y:LETTERS_BOTTOM_Y, 0:GBA_W]
    w = _white_ratio(region)
    return w > 0.40


BATTLE_MENU_LEFT_X = 132
BATTLE_MENU_RIGHT_X = 232
BATTLE_MENU_TOP_Y = 96
BATTLE_MENU_BOTTOM_Y = 152

PLAYER_TILE_X = 112
PLAYER_TILE_Y = 56
PLAYER_TILE_SIZE = 16


def battle_menu_open(img: np.ndarray) -> bool:
    """Detect FIGHT/PKMN/ITEM/RUN 4-option menu (bottom-right quadrant).

    The 4-option menu has a 2x2 white-box layout with text. A dialog
    box (full-width) has the entire bottom strip white. We require
    that the LEFT bottom strip is NOT white (battle menu is right-side
    only, dialog is full-width).
    """
    region_r = img[
        BATTLE_MENU_TOP_Y:BATTLE_MENU_BOTTOM_Y,
        BATTLE_MENU_LEFT_X:BATTLE_MENU_RIGHT_X,
    ]
    region_l = img[
        BATTLE_MENU_TOP_Y:BATTLE_MENU_BOTTOM_Y,
        8:BATTLE_MENU_LEFT_X - 16,
    ]
    if region_r.size == 0 or region_l.size == 0:
        return False
    wr = _white_ratio(region_r)
    wl = _white_ratio(region_l)
    return wr > 0.50 and wl < 0.35


def victory_or_levelup(img: np.ndarray) -> bool:
    """Detect EXP/level-up celebration screens (full-screen white flash)."""
    if img.size == 0:
        return False
    w = _white_ratio(img)
    return w > 0.85


def front_tile_blocked(img: np.ndarray, facing: str) -> bool:
    """Crude blocker detection: compare 16x16 tile in front of player to
    the 16x16 tile under the player. If they look very different
    (large mean color delta + high edge density in front tile), assume
    blocker (NPC/object/wall).
    """
    under = img[
        PLAYER_TILE_Y:PLAYER_TILE_Y + PLAYER_TILE_SIZE,
        PLAYER_TILE_X:PLAYER_TILE_X + PLAYER_TILE_SIZE,
    ]
    delta_xy = {
        "Up": (0, -PLAYER_TILE_SIZE),
        "Down": (0, PLAYER_TILE_SIZE),
        "Left": (-PLAYER_TILE_SIZE, 0),
        "Right": (PLAYER_TILE_SIZE, 0),
    }.get(facing)
    if delta_xy is None:
        return False
    fx = PLAYER_TILE_X + delta_xy[0]
    fy = PLAYER_TILE_Y + delta_xy[1]
    if fx < 0 or fy < 0 or fx + PLAYER_TILE_SIZE > GBA_W or fy + PLAYER_TILE_SIZE > GBA_H:
        return False
    front = img[fy:fy + PLAYER_TILE_SIZE, fx:fx + PLAYER_TILE_SIZE]
    if front.size == 0 or under.size == 0:
        return False
    diff = float(np.abs(front.astype(np.int16) - under.astype(np.int16)).mean())
    front_gray = cv2.cvtColor(front, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(front_gray, 50, 150)
    edge_density = float(edges.mean() / 255.0)
    return diff > 55 and edge_density > 0.20


def detect_all(img: np.ndarray, facing: str | None = None) -> dict:
    out = {
        "dialog": dialog_open(img),
        "menu": menu_open(img),
        "letter_entry": letter_entry(img),
        "battle_menu": battle_menu_open(img),
        "victory_or_levelup": victory_or_levelup(img),
    }
    if facing is not None:
        out["front_blocked"] = front_tile_blocked(img, facing)
    return out


def detect_from_path(path: str | Path, facing: str | None = None) -> dict:
    img = _load_img(path)
    if img is None:
        # Keep fallback shape aligned with detect_all() so missing facing omits front_blocked consistently.
        out = {
            "dialog": False, "menu": False, "letter_entry": False,
            "battle_menu": False, "victory_or_levelup": False,
        }
        if facing is not None:
            out["front_blocked"] = False
        return out
    return detect_all(img, facing=facing)
