"""Claude Plays Pokemon (Anthropic, 2025) hierarchical goal stack.

CPP demonstrated that LLM agents do far better with explicit goal
decomposition: a high-level objective ("get Stone Badge"), one or two
mid-level subgoals ("reach Petalburg Gym"), and a low-level executor
(button presses). It also kept a persistent "memory notes" file across
turns so the agent didn't repeatedly retry the same dead-ends.

For our $0 heuristic, we encode the early-game progression as a
goal table — each entry is conditional on a specific RAM signal
(event flag count, party count, or badge count). The CURRENT goal
is inferred at every step; the heuristic uses its target_map as a
soft routing hint when picking a direction.

Goal selection is data-driven from gs (no fragile hard-coded checks
beyond the Pokemon-Emerald early-game milestones). All maps named
here are from pokeemerald decomp; treat them as labels the heuristic
prefers, never as hard requirements.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import config

GOALS_FILE = config.MEMORY_DIR / "goal_notes.jsonl"


@dataclass
class Goal:
    name: str
    target_map: tuple[int, int] | None
    condition: str
    desc: str

    def matches(self, gs) -> bool:
        c = self.condition
        if c == "no_party":
            return gs.party_count == 0
        if c == "first_starter":
            return gs.party_count == 1 and gs.badge_count == 0
        if c == "no_badge":
            return gs.badge_count == 0 and gs.party_count >= 1
        if c.startswith("badge>="):
            n = int(c.split(">=")[1])
            return gs.badge_count >= n
        return False


GOAL_TABLE: list[Goal] = [
    Goal(
        name="get_starter_via_lab",
        target_map=(1, 4),
        condition="no_party",
        desc="Pokemon 0 匹 → Birch's lab (1-4) で starter 取得 (LLM が Route 101 経由 Birch 救助も指示する)",
    ),
    Goal(
        name="reach_route_102",
        target_map=(0, 17),
        condition="first_starter",
        desc="starter 取得 → Oldale (0-17) 経由 Route 102",
    ),
    Goal(
        name="reach_petalburg_gym",
        target_map=(0, 11),
        condition="no_badge",
        desc="Route 102 → Petalburg City gym",
    ),
    Goal(
        name="reach_rustboro_gym",
        target_map=(0, 13),
        condition="badge>=1",
        desc="Stone Badge → Rustboro gym",
    ),
    Goal(
        name="reach_dewford_gym",
        target_map=(0, 14),
        condition="badge>=2",
        desc="Knuckle Badge → Dewford gym",
    ),
    Goal(
        name="reach_mauville_gym",
        target_map=(0, 15),
        condition="badge>=3",
        desc="Dynamo Badge → Mauville gym",
    ),
]


def current_goal(gs) -> Goal | None:
    for g in GOAL_TABLE:
        if g.matches(gs):
            return g
    return None


def append_note(note: str) -> None:
    """CPP-style persistent memory — write a short note for later runs."""
    try:
        GOALS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with GOALS_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"note": note}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read_notes(limit: int = 20) -> list[str]:
    if not GOALS_FILE.exists():
        return []
    try:
        with GOALS_FILE.open("r", encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
        return [json.loads(l).get("note", "") for l in lines]
    except (OSError, json.JSONDecodeError):
        return []
