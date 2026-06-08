"""Brain LLM caller.

1 turn = 1 Claude API call. screenshot + small state → tool_use.

Cost optimization:
- prompt caching on system + tools (~90% discount on reused tokens)
- short history (last 3 action summaries only)
- output cap 512 tokens
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anthropic import Anthropic

from . import config, memory, prompts, tools_schema


@dataclass
class BrainAction:
    tool: str
    args: dict[str, Any]
    raw_text: str


@dataclass
class BrainTurn:
    actions: list[BrainAction]
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    raw_response: Any


_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        config.load_api_key()
        _client = Anthropic()
    return _client


def _b64_image(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    b64 = base64.standard_b64encode(data).decode("ascii")
    media = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media,
            "data": b64,
        },
    }


def _format_history(history: list[str], limit: int = 3) -> str:
    if not history:
        return "(no prior actions)"
    return "\n".join(f"- {h}" for h in history[-limit:])


def _format_notes(notes: list[str]) -> str:
    if not notes:
        return "(no notes yet)"
    trimmed = []
    for n in notes:
        s = n.strip()
        if len(s) > 200:
            s = s[:197] + "..."
        trimmed.append(s)
    return "\n".join(f"- {t}" for t in trimmed)


def call_brain(
    screenshot: Path,
    turn: int,
    history: list[str],
    state_summary: str = "",
    stuck_streak: int = 0,
    rescue_active: bool = False,
) -> BrainTurn:
    client = _get_client()
    notes = memory.recent_notes(limit=6)

    stuck_line = ""
    if stuck_streak >= 3:
        stuck_line = (
            f"\n!! STUCK warning: the screen has been similar for "
            f"{stuck_streak} consecutive Brain turns. Do NOT repeat your "
            f"last action. Try a fundamentally different direction or "
            f"interaction. Save a record_observation noting what failed."
        )

    same_map_n = 0
    if "same map for " in state_summary:
        try:
            same_map_n = int(
                state_summary.split("same map for ")[1].split(" ")[0]
            )
        except (ValueError, IndexError):
            same_map_n = 0
    rescue_line = ""
    if rescue_active:
        rescue_line = (
            "\n!! LONG-STUCK rescue (fires once per stuck episode): you "
            f"have stayed on the same map for {same_map_n} Brain turns. "
            "RAM confirms you are not progressing. THIS turn, call "
            "record_observation ONCE with a single-sentence plan: (a) "
            "what you have been trying, (b) why it fails, (c) a "
            "fundamentally different approach (an unexplored direction, "
            "an object you have not interacted with, a menu you have not "
            "opened). Starting NEXT turn, EXECUTE that plan with "
            "press_buttons — do not save more notes about being stuck."
        )

    user_blocks: list[dict[str, Any]] = [
        _b64_image(screenshot),
        {
            "type": "text",
            "text": (
                f"Turn {turn}.\n"
                f"State: {state_summary or '(none)'}\n"
                f"Recent actions:\n{_format_history(history)}\n"
                f"Memory notes:\n{_format_notes(notes)}"
                f"{stuck_line}"
                f"{rescue_line}\n"
                f"Pick one tool and call it."
            ),
        },
    ]

    response = client.messages.create(
        model=config.MODEL_BRAIN,
        max_tokens=config.MAX_OUTPUT_TOKENS,
        system=[
            {
                "type": "text",
                "text": prompts.SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        tools=tools_schema.TOOLS,
        messages=[{"role": "user", "content": user_blocks}],
    )

    actions: list[BrainAction] = []
    raw_text_parts: list[str] = []
    for block in response.content:
        if block.type == "tool_use":
            actions.append(
                BrainAction(
                    tool=block.name,
                    args=dict(block.input),
                    raw_text="",
                )
            )
        elif block.type == "text":
            raw_text_parts.append(block.text)
    raw_text = "\n".join(raw_text_parts).strip()

    usage = response.usage
    return BrainTurn(
        actions=actions,
        input_tokens=getattr(usage, "input_tokens", 0),
        output_tokens=getattr(usage, "output_tokens", 0),
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_creation_tokens=(
            getattr(usage, "cache_creation_input_tokens", 0) or 0
        ),
        raw_response=raw_text,
    )
