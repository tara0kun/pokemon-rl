"""Curriculum learning via mGBA savestates.

Idea (from Go-Explore + PWhiddy): once the agent reaches a new
milestone (first time on a new map), snapshot the emulator state.
Future iters can load that snapshot instead of replaying intro
from scratch — drastically more demo collection on hard maps.

Anti-pattern guard: the project rule forbids `saveStateLoad` to
"bypass story progression". Resuming from an EARLIER milestone the
agent already reached on its own is not bypassing — it's the same
as continuing from a save point. We only auto-resume from the
LATEST milestone we've achieved; we never load arbitrary states.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config

CURRICULUM_INDEX = config.MEMORY_DIR / "curriculum_index.json"
CURRICULUM_DIR = config.MEMORY_DIR / "curriculum_savestates"


@dataclass
class Milestone:
    map_g: int
    map_n: int
    pos_x: int
    pos_y: int
    savestate_path: str
    timestamp: float
    party_count: int = 0
    badge_count: int = 0
    total_event_flags: int = 0


@dataclass
class CurriculumIndex:
    milestones: list[Milestone] = field(default_factory=list)

    def load(self) -> None:
        if not CURRICULUM_INDEX.exists():
            return
        try:
            data = json.loads(CURRICULUM_INDEX.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        self.milestones = [Milestone(**m) for m in data.get("milestones", [])]

    def save(self) -> None:
        CURRICULUM_INDEX.parent.mkdir(parents=True, exist_ok=True)
        CURRICULUM_INDEX.write_text(
            json.dumps(
                {"milestones": [m.__dict__ for m in self.milestones]},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )

    def has_milestone(self, map_g: int, map_n: int) -> bool:
        return any(
            m.map_g == map_g and m.map_n == map_n for m in self.milestones
        )

    def best_milestone(self) -> Milestone | None:
        """Return the milestone with highest progression signal
        (badges first, then event flags, then party count)."""
        if not self.milestones:
            return None
        def key(m: Milestone) -> tuple[int, int, int]:
            return (m.badge_count, m.total_event_flags, m.party_count)
        return max(self.milestones, key=key)


def record_milestone_if_new(
    client, gs, idx: CurriculumIndex,
) -> Milestone | None:
    """Called by heuristic when a new map is first entered. Snapshots
    a savestate to disk + records metadata."""
    if not gs.saveblock1_valid or gs.in_battle:
        return None
    if idx.has_milestone(gs.map_group, gs.map_num):
        return None
    CURRICULUM_DIR.mkdir(parents=True, exist_ok=True)
    fn = f"milestone_m{gs.map_group}_{gs.map_num}_pos{gs.x}_{gs.y}.ss1"
    p = CURRICULUM_DIR / fn
    try:
        client.save_state_file(p, flags=1)
    except (OSError, RuntimeError):
        return None
    m = Milestone(
        map_g=gs.map_group,
        map_n=gs.map_num,
        pos_x=gs.x,
        pos_y=gs.y,
        savestate_path=str(p),
        timestamp=time.time(),
        party_count=gs.party_count,
        badge_count=gs.badge_count,
        total_event_flags=gs.total_event_flags,
    )
    idx.milestones.append(m)
    idx.save()
    return m
