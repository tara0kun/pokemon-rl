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
        if c == "no_badge_pre_pokedex":
            # FLAG_ADVENTURE_STARTED = 0x74 is set on Pokedex receipt, which
            # bumps total_event_flags by ~3-5 (the cutscene sets several
            # related flags). Empirically <200 flags = pre-Pokedex.
            return (
                gs.badge_count == 0
                and gs.party_count >= 1
                and gs.total_event_flags < 200
            )
        if c.startswith("badge>="):
            n = int(c.split(">=")[1])
            return gs.badge_count >= n
        return False


GOAL_TABLE: list[Goal] = [
    Goal(
        name="get_starter_via_lab",
        target_map=(1, 4),
        condition="no_party",
        desc="Pokemon 0 匹 → Birch's lab (1-4) で starter 取得",
    ),
    Goal(
        name="reach_oldale",
        target_map=(0, 10),
        condition="first_starter",
        desc="starter 取得 → Oldale Town (0-10) で Pokemon Center heal",
    ),
    Goal(
        name="reach_route_103_rival",
        target_map=(0, 18),
        condition="no_badge_pre_pokedex",
        desc="Pokedex 取得前: Route 103 (0-18) で Rival 戦闘 → VAR_BIRCH_LAB_STATE=4 → 次の lab 訪問で Pokedex auto-trigger → FLAG_ADVENTURE_STARTED set → Oldale 西 Painter gate 解除",
    ),
    Goal(
        name="reach_route_102",
        target_map=(0, 17),
        condition="no_badge",
        desc="Oldale → Route 102 (0-17) 西 → Petalburg",
    ),
    Goal(
        name="reach_petalburg",
        target_map=(0, 0),
        condition="no_badge",
        desc="Route 102 → Petalburg City (0-0)",
    ),
    Goal(
        name="reach_rustboro_gym",
        target_map=(0, 3),
        condition="no_badge",
        desc="Petalburg → Rustboro City (0-3) Stone Badge",
    ),
    Goal(
        name="reach_dewford_gym",
        target_map=(0, 11),
        condition="badge>=1",
        desc="Stone Badge → Dewford Town (0-11)",
    ),
]


def current_goal(gs) -> Goal | None:
    """First matching goal whose target_map differs from agent's current
    map. Skipping already-reached goals lets the goal chain advance: if
    we're at Oldale and `reach_oldale` matches but its target is Oldale,
    fall through to the next match (e.g. `reach_route_103_rival`)."""
    cur = (getattr(gs, "map_group", -1), getattr(gs, "map_num", -1))
    fallback = None
    for g in GOAL_TABLE:
        if not g.matches(gs):
            continue
        if g.target_map == cur:
            if fallback is None:
                fallback = g
            continue
        return g
    return fallback


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
