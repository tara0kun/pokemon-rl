"""Deterministic local rules engine — handles 90%+ of turns at $0.

3 layers, each tries to produce an action without API call:
1. Frame cache: if we've seen this exact (screen + map) before, replay.
2. RAM-based state: if in dialogue-like / menu-like state, use default rule.
3. Local recovery: if screen frozen N turns, try B then random direction.

Falls through to Brain LLM only when all three fail.

The cache key is (frame_hash, map_group, map_num) — so the same screen
on a different map gets a different cached action. Important because
dialogue at home vs lab needs different next-step intuition.
"""
from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config


@dataclass
class LocalDecision:
    """One decision returned by the local engine."""
    button: str
    frames: int = 15
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "button": self.button,
            "frames": self.frames,
            "source": self.source,
        }


@dataclass
class CacheEntry:
    button: str
    frames: int
    hit_count: int = 1
    last_used_at: float = 0.0


class FrameCache:
    """Persistent (frame_hash, map_key) → action cache."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (config.MEMORY_DIR / "frame_cache.json")
        self._store: dict[str, CacheEntry] = {}
        self._load()

    def _key(self, fhash: str, map_group: int, map_num: int) -> str:
        return f"{map_group}-{map_num}-{fhash}"

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        for k, v in raw.items():
            self._store[k] = CacheEntry(
                button=v["button"],
                frames=int(v.get("frames", 15)),
                hit_count=int(v.get("hit_count", 1)),
                last_used_at=float(v.get("last_used_at", 0.0)),
            )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        out = {
            k: {
                "button": e.button,
                "frames": e.frames,
                "hit_count": e.hit_count,
                "last_used_at": e.last_used_at,
            }
            for k, e in self._store.items()
        }
        self.path.write_text(json.dumps(out, indent=2), encoding="utf-8")

    def lookup(
        self, fhash: str, map_group: int, map_num: int
    ) -> CacheEntry | None:
        entry = self._store.get(self._key(fhash, map_group, map_num))
        if entry is not None:
            entry.hit_count += 1
            entry.last_used_at = time.time()
        return entry

    def flush_map(self, map_group: int, map_num: int) -> int:
        """Drop every entry whose key matches the given map.

        Returns the number of entries removed. Used when an agent is
        stuck on one map for so long that the cache is reinforcing
        wrong moves — clearing forces fresh Brain calls.
        """
        prefix = f"{map_group}-{map_num}-"
        keys = [k for k in self._store if k.startswith(prefix)]
        for k in keys:
            self._store.pop(k, None)
        return len(keys)

    def remember(
        self,
        fhash: str,
        map_group: int,
        map_num: int,
        button: str,
        frames: int = 15,
    ) -> None:
        self._store[self._key(fhash, map_group, map_num)] = CacheEntry(
            button=button,
            frames=frames,
            hit_count=1,
            last_used_at=time.time(),
        )

    def forget(self, fhash: str, map_group: int, map_num: int) -> None:
        self._store.pop(self._key(fhash, map_group, map_num), None)

    def __len__(self) -> int:
        return len(self._store)


class LocalRecovery:
    """Deterministic recovery when screen is frozen.

    Designed for the common Pokemon stuck modes:
      a) dialogue cycle from an NPC that keeps re-engaging
      b) bumping a wall
      c) menu accidentally opened

    State machine (length 12, then exhausted):
      step 0..3:   press A (mash through any open dialog/menu) — frames 6
      step 4..6:   press B (defensively dismiss any leftover menu)
      step 7..8:   press Down twice (try to walk south away from NPC)
      step 9..11:  random non-Up direction
                   (avoids re-triggering north-facing NPCs)

    After step 11 the caller should reset() (consecutive_dialog=0)
    and try again from cache/rules.
    """

    LENGTH = 12

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self._step = 0

    def reset(self) -> None:
        self._step = 0

    def exhausted(self) -> bool:
        return self._step >= self.LENGTH

    def next(self) -> LocalDecision:
        s = self._step
        self._step += 1
        if s <= 3:
            return LocalDecision("A", 6, source=f"recovery.A[{s}]")
        if s <= 6:
            return LocalDecision("B", 8, source=f"recovery.B[{s}]")
        if s <= 8:
            return LocalDecision("Down", 15, source=f"recovery.Down[{s}]")
        d = self._rng.choice(["Down", "Left", "Right"])
        return LocalDecision(d, 15, source=f"recovery.rand[{s}]={d}")


def default_rule_for_state(
    same_map_streak: int,
    same_hash_streak: int,
    pos_changed: bool,
    last_action: str = "",
) -> LocalDecision | None:
    """Cheap default action when state suggests a known pattern.

    Rules (in order of precedence):
    - pos unchanged + screen changed (same_hash == 0) + last action was A:
        → continuing a dialogue, press A again.
    - pos unchanged + screen changed + last action movement (Up/Down/Left/Right):
        → bumped a wall, do NOT auto-press anything; let Brain decide.
    - else: return None (escalate to cache / Brain).
    """
    if not pos_changed and same_hash_streak == 0 and last_action == "A":
        return LocalDecision("A", 8, source="rule.dialog_continue")
    return None
