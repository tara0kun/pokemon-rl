"""Lightweight Brain call for rescue only — Haiku 4.5 + JSON output.

This is the "Stage 3" rescue invoked by auto_loop when local recovery
fails. Designed to be CHEAP:
- Haiku 4.5: $1 / $5 per 1M tokens (1/5 of Opus)
- JPEG image (smaller than PNG)
- max_tokens=120: forces tight output
- No tool_use overhead (strict JSON)
- No history / notes context (each call is independent)

Returns a single-button decision; the caller decides hold duration.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anthropic import Anthropic

from . import config, preprocess

VALID_BUTTONS = {
    "A", "B", "Start", "Select",
    "Up", "Down", "Left", "Right", "L", "R",
}

MODEL_RESCUE = "claude-opus-4-8"
MODEL_HAIKU = "claude-haiku-4-5"
MAX_OUTPUT_TOKENS = 120

SYSTEM_PROMPT_RESCUE = (
    "You help an automated Pokemon Emerald agent get unstuck. "
    "Look at the screenshot and reply with ONLY a JSON object: "
    '{"button": "<A|B|Start|Select|Up|Down|Left|Right>", '
    '"reason": "<<= 12 words>"}. '
    "No prose, no code fences. The agent has been stuck — choose a "
    "button that is most likely to make progress."
)

SYSTEM_PROMPT_NAVIGATE = (
    "You pick ONE button press for a Pokemon Emerald agent. "
    'Reply ONLY: {"button": "<A|B|Up|Down|Left|Right|Start|Select>", '
    '"reason": "<<= 12 words>"}\n'
    "GAME GEOGRAPHY (Pokemon Emerald):\n"
    "- Littleroot Town (Mishiro): starting town. Player house is "
    "  upper-left, Brendan/May house upper-right, Birch's Lab south-"
    "  center. Route 101 EXIT is at the NORTH of the town.\n"
    "- Route 101 connects Littleroot (south) to Oldale (north).\n"
    "- Goal sequence: 1) Wake at home 2) Mom dialog 3) Visit Birch's "
    "  Lab (assistant says Birch is on Route 101) 4) Try to leave "
    "  via NORTH exit 5) Birch chased by Poochyena cutscene 6) Pick "
    "  starter from his bag 7) Battle Poochyena 8) Birch leads back "
    "  to Lab 9) Receive starter + Pokedex 10) Head NORTH through "
    "  Route 101, 102 to Petalburg, then north to Rustboro Gym.\n"
    "MECHANICS:\n"
    "- Building EXITS: south doors (green rectangle near doormat). "
    "  Walk Down to reach.\n"
    "- Town EXITS: walk to the appropriate edge (e.g. NORTH to leave "
    "  Littleroot for Route 101).\n"
    "- Press A to talk to an NPC one tile in front of you.\n"
    "- Dialog auto-advances with A; never press direction during "
    "  dialog or it will be eaten.\n"
    "- In battle: Fight = A. Cursor on attack = A. Run from wild = "
    "  Down then Right then A.\n"
    "- If you bump a wall, switch direction; do NOT repeat the same "
    "  failed move.\n"
    "- Watch the 'Recent actions' list — if you have pressed the same "
    "  direction many times with no map change, try perpendicular."
)

SYSTEM_PROMPT = SYSTEM_PROMPT_RESCUE  # backward compat


@dataclass
class RescueDecision:
    button: str
    reason: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    image_bytes: int
    frame_hash: str

    def cost_usd(self) -> float:
        return (
            self.input_tokens * 5.0 / 1_000_000
            + self.output_tokens * 25.0 / 1_000_000
            + self.cache_read_tokens * 0.5 / 1_000_000
            + self.cache_creation_tokens * 6.25 / 1_000_000
        )


_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        config.load_api_key()
        _client = Anthropic()
    return _client


def _parse_response(raw_text: str) -> tuple[str, str]:
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if "```" in text[3:] else text[3:]
        text = text.replace("json", "", 1).strip()
    try:
        data = json.loads(text)
        button = str(data.get("button", "")).strip()
        if button not in VALID_BUTTONS:
            for word in text.split():
                cleaned = word.strip(",.;:\"'{}[]")
                if cleaned in VALID_BUTTONS:
                    button = cleaned
                    break
        reason = str(data.get("reason", "")).strip()[:120]
    except (json.JSONDecodeError, ValueError):
        button = ""
        for word in text.split():
            cleaned = word.strip(",.;:\"'{}[]")
            if cleaned in VALID_BUTTONS:
                button = cleaned
                break
        reason = "parse-fallback"
    if button not in VALID_BUTTONS:
        button = "B"
        reason = "no-valid-button-fallback"
    return button, reason


def _call_haiku(
    screenshot_png: Path,
    system_prompt: str,
    user_text: str,
) -> tuple[Any, int, str]:
    client = _get_client()
    image_block, image_bytes, fhash = preprocess.png_path_to_jpeg_block(
        screenshot_png
    )
    response = client.messages.create(
        model=MODEL_RESCUE,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": [
                    image_block,
                    {"type": "text", "text": user_text},
                ],
            }
        ],
    )
    return response, image_bytes, fhash


def call_navigate(
    screenshot_png: Path,
    state_summary: str = "",
    last_actions: list[str] | None = None,
) -> RescueDecision:
    parts = [f"State: {state_summary or '(none)'}"]
    if last_actions:
        parts.append(
            "Recent actions (oldest→newest): "
            + ",".join(last_actions[-8:])
        )
    parts.append("Pick the next button.")
    user_text = "\n".join(parts)
    response, image_bytes, fhash = _call_haiku(
        screenshot_png, SYSTEM_PROMPT_NAVIGATE, user_text
    )
    return _decision_from_response(response, image_bytes, fhash)


def call_rescue(
    screenshot_png: Path,
    state_summary: str = "",
    same_map_streak: int = 0,
) -> RescueDecision:
    user_text = (
        f"State: {state_summary or '(none)'}\n"
        f"Stuck on this screen for {same_map_streak} consecutive turns."
    )
    response, image_bytes, fhash = _call_haiku(
        screenshot_png, SYSTEM_PROMPT_RESCUE, user_text
    )
    return _decision_from_response(response, image_bytes, fhash)


def _decision_from_response(
    response: Any, image_bytes: int, fhash: str
) -> RescueDecision:

    raw_text_parts = []
    for block in response.content:
        if getattr(block, "type", "") == "text":
            raw_text_parts.append(block.text)
    raw_text = "\n".join(raw_text_parts).strip()
    button, reason = _parse_response(raw_text)
    usage = response.usage
    return RescueDecision(
        button=button,
        reason=reason,
        input_tokens=getattr(usage, "input_tokens", 0),
        output_tokens=getattr(usage, "output_tokens", 0),
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_creation_tokens=(
            getattr(usage, "cache_creation_input_tokens", 0) or 0
        ),
        image_bytes=image_bytes,
        frame_hash=fhash,
    )
