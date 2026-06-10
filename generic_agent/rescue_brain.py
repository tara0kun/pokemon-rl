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

SYSTEM_PROMPT_NAVIGATE_LONG = (
    "You pick ONE button press for a Pokemon Emerald agent. "
    'Reply ONLY: {"button": "<A|B|Up|Down|Left|Right|Start|Select>", '
    '"reason": "<<= 12 words>"}\n'
    "HARD PRIORITY ORDER (override every other hint below):\n"
    "1. If 'blocked_here' lists a direction, NEVER pick that direction.\n"
    "2. If 'bfs_to_frontier=<dir>' appears AND <dir> not in blocked_here, "
    "   you MUST pick <dir>. This was computed by BFS over the known "
    "   tile graph; it is the verified first step toward unexplored "
    "   territory and overrides path_memory.\n"
    "3. Only if bfs_to_frontier is absent, fall back to path_memory: "
    "   pick the 'first=<Word>' direction of a 'known exits' entry "
    "   whose target map matches the GOAL hint.\n"
    "4. If neither hint applies on overworld, pick a non-blocked "
    "   direction toward any 'unexplored_nearby' coord "
    "   (compute dx=tx-cur_x, dy=ty-cur_y; |dy|>|dx| → Up if dy<0 else "
    "   Down; else Left if dx<0 else Right).\n"
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
    "PATH MEMORY — read this if present:\n"
    "- A 'path_memory: known exits' fragment lists button sequences "
    "  that PREVIOUSLY led from this map to another map. Tokens are "
    "  compact: U=Up, D=Down, L=Left, R=Right, a=A, b=B.\n"
    "- Example: 'to 3-0 via UUUa first=Up' means the recorded sequence "
    "  starts with Up. Use 'first=<Word>' only if bfs_to_frontier is absent.\n"
    "- If the same map appears as 'no known transitions', you have "
    "  not yet found an exit and must explore.\n"
    "BATTLE MODE: if the State line ends with [in_battle], you are "
    "  in a Pokemon battle. Ignore the tile_map / unexplored_nearby — "
    "  those refer to the overworld. In battle: A confirms the current "
    "  menu / attack. If the cursor is already on 'Fight', press A. "
    "  To pick a strong move, ensure cursor on the first attack and A. "
    "  Run from wild encounters with Down then Right then A.\n"
    "TILE MAP — read this first (overworld only):\n"
    "- The State line includes a 'tile_map:' summary with: "
    "  the count of tiles already seen on this map, the directions "
    "  CONFIRMED BLOCKED at your current tile (after >=3 failed "
    "  attempts), and unexplored frontier tiles within radius 3.\n"
    "- Picking a 'blocked_here' direction will fail. Do NOT pick "
    "  those. Prefer a direction that leads toward an "
    "  'unexplored_nearby' tile.\n"
    "- If 'no_nearby_unexplored' appears, you are likely at a "
    "  story trigger spot — interact with NPC (A) or look for a "
    "  door/stair you missed.\n"
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

SYSTEM_PROMPT_NAVIGATE = (
    "Pick ONE button for a Pokemon Emerald agent. Reply ONLY JSON: "
    '{"button":"<A|B|Up|Down|Left|Right|Start|Select>",'
    '"reason":"<short>"}.\n'
    "Hard rules (follow even if other hints conflict):\n"
    "1. blocked_here directions — NEVER pick them, they will not move.\n"
    "2. unexplored_nearby tiles — prefer moving toward them.\n"
    "3. In dialog (text box visible) — press A, never a direction.\n"
    "4. [in_battle] — press A.\n"
    "Goal: leave the current map by reaching an edge that has not "
    "been confirmed blocked. Do not stand still."
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
    use_thinking: bool = False,
) -> tuple[Any, int, str]:
    client = _get_client()
    image_block, image_bytes, fhash = preprocess.png_path_to_jpeg_block(
        screenshot_png
    )
    extra: dict[str, Any] = {}
    if use_thinking and MODEL_RESCUE.startswith("claude-opus"):
        extra["thinking"] = {"type": "adaptive"}
    response = client.messages.create(
        model=MODEL_RESCUE,
        max_tokens=MAX_OUTPUT_TOKENS + (512 if use_thinking else 0),
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
        **extra,
    )
    return response, image_bytes, fhash


def call_navigate(
    screenshot_png: Path,
    state_summary: str = "",
    last_actions: list[str] | None = None,
    use_thinking: bool = False,
) -> RescueDecision:
    parts = [f"State: {state_summary or '(none)'}"]
    if last_actions:
        parts.append(
            "Recent: " + ",".join(last_actions[-6:])
        )
    parts.append("Button?")
    user_text = "\n".join(parts)
    response, image_bytes, fhash = _call_haiku(
        screenshot_png,
        SYSTEM_PROMPT_NAVIGATE_LONG,
        user_text,
        use_thinking=use_thinking,
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
