"""Sonnet/Haiku in-loop advisor — drives critical moments (dialog, menu,
new map) in real time, leaving routine navigation to the cheap heuristic.

CPP-architecture port: Claude Plays Pokemon (Anthropic, 2025) progressed
farther than pure-RL PWhiddy precisely because the LLM SAW the screen.
We adopt the same idea but sparsely:
- ~10-30 LLM calls/min only when state demands reasoning
- otherwise free heuristic + CNN

Cost model (Haiku 4.5, $1/M input + $5/M output, cache hits at $0.10/M):
- ~1750 input + 50 output per call ≈ $0.001 / call uncached, $0.0002 cached
- 600 calls/h ≈ $0.60 uncached, $0.12 cached
"""
from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anthropic

from . import config, pokemon_emerald_knowledge

MODEL = "claude-haiku-4-5"
MAX_TOKENS = 256
HISTORY_LEN = 6


@dataclass
class Advice:
    buttons: list[str]
    reason: str
    goal: str
    cost_usd: float
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0


@dataclass
class LLMAdvisor:
    history: list[dict] = field(default_factory=list)
    client: Any = None
    total_cost: float = 0.0
    total_calls: int = 0

    def _ensure_client(self) -> bool:
        if self.client is not None:
            return True
        key = config.load_api_key()
        if not key:
            return False
        self.client = anthropic.Anthropic(api_key=key)
        return True

    def push_history(self, before_pos, before_map, btns, after_pos, after_map, moved):
        self.history.append({
            "before": f"pos=({before_pos[0]},{before_pos[1]}) "
                     f"m=({before_map[0]},{before_map[1]})",
            "btns": btns,
            "after": f"pos=({after_pos[0]},{after_pos[1]}) "
                    f"m=({after_map[0]},{after_map[1]})",
            "moved": moved,
        })
        if len(self.history) > HISTORY_LEN:
            self.history = self.history[-HISTORY_LEN:]

    def consult(
        self,
        screenshot_path: Path,
        gs,
        screen_signals: dict,
        same_pos_streak: int,
        same_map_streak: int,
    ) -> Advice | None:
        if not self._ensure_client():
            return None

        # encode image as base64
        try:
            with open(screenshot_path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode("ascii")
        except OSError:
            return None

        hist_text = ""
        if self.history:
            lines = []
            for h in self.history[-HISTORY_LEN:]:
                lines.append(
                    f"  before {h['before']} → {h['btns']} → "
                    f"after {h['after']} (moved={h['moved']})"
                )
            hist_text = "Recent actions:\n" + "\n".join(lines) + "\n\n"

        state_summary = (
            f"State: pos=({gs.x},{gs.y}) map=({gs.map_group},{gs.map_num}) "
            f"party={gs.party_count} hp={gs.party0_hp}/{gs.party0_max_hp} "
            f"flags={gs.total_event_flags} in_battle={gs.in_battle}. "
            f"Same pos for {same_pos_streak} turns, same map for "
            f"{same_map_streak} turns."
        )
        screen_text = ", ".join(k for k, v in screen_signals.items() if v) or "(no special signal)"
        user_text = (
            f"{hist_text}{state_summary}\n"
            f"Screen features detected: {screen_text}\n\n"
            "Choose buttons to make progress on the next milestone."
        )

        try:
            resp = self.client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=[
                    {
                        "type": "text",
                        "text": pokemon_emerald_knowledge.system_prompt_for_advisor(),
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": img_b64,
                                },
                            },
                            {"type": "text", "text": user_text},
                        ],
                    }
                ],
            )
        except (anthropic.APIError, anthropic.APIConnectionError, anthropic.RateLimitError):
            return None

        text = ""
        for block in resp.content:
            if hasattr(block, "text"):
                text += block.text

        text_clean = text.strip()
        if text_clean.startswith("```"):
            text_clean = text_clean.split("```")[1]
            if text_clean.startswith("json"):
                text_clean = text_clean[4:]
        text_clean = text_clean.strip()

        try:
            data = json.loads(text_clean)
            buttons = [str(b).strip() for b in data.get("buttons", []) if str(b).strip()]
            reason = str(data.get("reason", ""))[:80]
            goal = str(data.get("goal", ""))[:60]
        except (json.JSONDecodeError, ValueError, TypeError):
            return None

        if not buttons:
            return None

        u = resp.usage
        in_t = getattr(u, "input_tokens", 0)
        out_t = getattr(u, "output_tokens", 0)
        cached_t = getattr(u, "cache_read_input_tokens", 0) or 0
        cost = (
            (in_t - cached_t) * 1.0 / 1_000_000
            + cached_t * 0.10 / 1_000_000
            + out_t * 5.0 / 1_000_000
        )
        self.total_cost += cost
        self.total_calls += 1

        return Advice(
            buttons=buttons,
            reason=reason,
            goal=goal,
            cost_usd=cost,
            input_tokens=in_t,
            output_tokens=out_t,
            cached_tokens=cached_t,
        )


def should_consult(
    screen_signals: dict,
    same_pos_streak: int,
    map_changed_recent: bool,
    last_consult_turn: int,
    cur_turn: int,
    in_battle: bool,
    same_map_streak: int = 0,
    hp_frac: float = 1.0,
    min_interval: int = 4,
) -> bool:
    """Trigger LLM only on situations the heuristic can't reason about."""
    if cur_turn - last_consult_turn < min_interval:
        return False
    if screen_signals.get("dialog"):
        return True
    if screen_signals.get("menu"):
        return True
    if screen_signals.get("letter_entry"):
        return True
    if in_battle and (cur_turn - last_consult_turn >= 12):
        return True
    if map_changed_recent:
        return True
    if same_pos_streak >= 30:
        return True
    if same_map_streak >= 300 and (cur_turn - last_consult_turn >= 80):
        return True
    if (
        hp_frac < 0.3
        and same_pos_streak >= 10
        and (cur_turn - last_consult_turn >= 60)
    ):
        return True
    return False
