"""Count-based exploration scoring + Go-Explore lite checkpoint archive.

Simpler than Q-learning: per-direction score combines tile visit counts
(intrinsic motivation) and map-progress signals. Checkpoint archive
remembers positions that previously led to discovering a new map, so
when the agent is stuck we can warp the planning back to a known-good
springboard and explore from there (the Go-Explore idea).

Both stores persist to disk so multi-iteration loops accumulate
knowledge. No API calls, no RAM writes — derived state only.

Scoring formula per candidate direction d from current tile T:
    target = neighbor(T, d)
    score(d) = 0
    if target unvisited:           score += NEW_TILE_BONUS
    else:                          score -= log(1 + target.visits)
    if target tile has > 5 visits: score -= over-visit penalty
    if d is in blocked_here:       score = -inf (caller must filter)
    score -= 0.05 * same_map_streak  (encourage map change over time)

Go-Explore archive:
    cells = set of (map_g, map_n, x, y) tuples where the agent was JUST
    BEFORE discovering a new map (these are the "springboards" that
    unlocked new territory).
    When detect_stuck() triggers, pick a random checkpoint from a map
    DIFFERENT from the current map; tell the caller to navigate there
    via path_memory; once reached, explore fresh from that spot.
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config

REWARD_FILE = config.MEMORY_DIR / "reward_state.json"

NEW_TILE_BONUS = 100.0
NEW_MAP_BONUS = 500.0  # used in step_reward only — logged not scored
OVERVISIT_THRESHOLD = 5
OVERVISIT_FACTOR = 1.0
SAME_MAP_DECAY = 0.5  # was 0.05 — 10x stronger nudge off stuck maps
CHECKPOINT_MAX = 50
RECENT_MAP_PENALTY_PER_VISIT = 30.0  # subtracted from reward_pick scores
RECENT_MAP_WINDOW = 4  # last N maps treated as "too familiar"

# PWhiddy v2 reward weights (port from baselines/v2/red_gym_env_v2.py)
EVENT_FLAG_WEIGHT = 4.0
HEAL_WEIGHT = 10.0
BADGE_WEIGHT = 10.0
OP_LEVEL_WEIGHT = 0.2
DEATH_PENALTY = -0.1
STUCK_PENALTY_PER_TICK = -0.05
STUCK_THRESHOLD_TICKS = 600

# PufferLib reward shaping (denser intermediate signals)
LEVEL_UP_WEIGHT = 1.0
CATCH_WEIGHT = 5.0
ITEM_PICKUP_WEIGHT = 0.5


@dataclass
class RewardState:
    cells: set[tuple[int, int, int, int]] = field(default_factory=set)
    last_visited_maps: list[tuple[int, int]] = field(default_factory=list)
    cumulative_reward: float = 0.0
    log: list[tuple[int, float, str]] = field(default_factory=list)
    # PWhiddy v2 stuck tracker: count visits per coord
    coord_visits: dict[str, int] = field(default_factory=dict)
    max_badges_seen: int = 0
    max_event_flags_seen: int = 0
    death_count: int = 0
    total_heal_amount: float = 0.0

    def load(self, path: Path = REWARD_FILE) -> None:
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        self.cells = {tuple(c) for c in data.get("cells", [])}
        self.last_visited_maps = [
            tuple(m) for m in data.get("last_visited_maps", [])
        ]
        self.cumulative_reward = float(data.get("cumulative_reward", 0.0))
        self.coord_visits = dict(data.get("coord_visits", {}))
        self.max_badges_seen = int(data.get("max_badges_seen", 0))
        self.max_event_flags_seen = int(data.get("max_event_flags_seen", 0))
        self.death_count = int(data.get("death_count", 0))
        self.total_heal_amount = float(data.get("total_heal_amount", 0.0))

    def save(self, path: Path = REWARD_FILE) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cells": [list(c) for c in self.cells],
            "last_visited_maps": [list(m) for m in self.last_visited_maps],
            "cumulative_reward": self.cumulative_reward,
            "coord_visits": self.coord_visits,
            "max_badges_seen": self.max_badges_seen,
            "max_event_flags_seen": self.max_event_flags_seen,
            "death_count": self.death_count,
            "total_heal_amount": self.total_heal_amount,
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )

    def score_direction(
        self,
        direction: str,
        cur_map_g: int,
        cur_map_n: int,
        cur_x: int,
        cur_y: int,
        tile_map_store: dict[str, dict[str, Any]],
        blocked: set[str],
        same_map_streak: int,
    ) -> float:
        if direction in blocked:
            return float("-inf")
        delta = {
            "Up": (0, -1), "Down": (0, 1),
            "Left": (-1, 0), "Right": (1, 0),
        }.get(direction)
        if delta is None:
            return float("-inf")
        nx = cur_x + delta[0]
        ny = cur_y + delta[1]
        mk = f"{cur_map_g}-{cur_map_n}"
        tiles = tile_map_store.get(mk, {})
        if hasattr(tiles, "get") and "tiles" in tiles:
            tiles = tiles["tiles"]
        target = None
        if isinstance(tiles, dict):
            target = tiles.get(f"{nx},{ny}")
        score = 0.0
        if target is None:
            score += NEW_TILE_BONUS
        else:
            visits = getattr(target, "visits", 0) if hasattr(target, "visits") else 0
            if visits == 0:
                score += NEW_TILE_BONUS
            else:
                score -= math.log(1 + visits)
                if visits > OVERVISIT_THRESHOLD:
                    score -= OVERVISIT_FACTOR * (visits - OVERVISIT_THRESHOLD)
        score -= SAME_MAP_DECAY * same_map_streak
        # Penalize the CURRENT MAP if it's been recently revisited a lot —
        # gives a uniform negative tilt that makes "leave this map" more
        # attractive when familiar transitions are encoded in path_memory.
        recent = self.last_visited_maps[-RECENT_MAP_WINDOW:]
        if (cur_map_g, cur_map_n) in recent:
            score -= RECENT_MAP_PENALTY_PER_VISIT * recent.count(
                (cur_map_g, cur_map_n)
            )
        return score

    def record_new_map(
        self,
        prev_map: tuple[int, int],
        prev_pos: tuple[int, int],
        new_map: tuple[int, int],
        new_pos: tuple[int, int],
        turn: int,
    ) -> float:
        is_new = new_map not in self.last_visited_maps
        if is_new:
            self.last_visited_maps.append(new_map)
            if len(self.last_visited_maps) > 200:
                self.last_visited_maps = self.last_visited_maps[-200:]
        springboard = (prev_map[0], prev_map[1], prev_pos[0], prev_pos[1])
        self.cells.add(springboard)
        if len(self.cells) > CHECKPOINT_MAX:
            self.cells = set(list(self.cells)[-CHECKPOINT_MAX:])
        reward = NEW_MAP_BONUS if is_new else 50.0
        self.cumulative_reward += reward
        self.log.append((turn, reward, f"new_map={new_map} from={springboard}"))
        if len(self.log) > 300:
            self.log = self.log[-300:]
        return reward

    def record_event_flag_delta(
        self, turn: int, prev_flags: int, cur_flags: int,
    ) -> float:
        """PWhiddy v2 event reward: 4 points per newly-set flag.

        Pokemon Emerald has 2400 event flags. Each NPC interaction,
        story milestone, item pickup flips one. Densest progression
        signal available without screen understanding.
        """
        if cur_flags <= prev_flags:
            return 0.0
        delta = cur_flags - prev_flags
        reward = EVENT_FLAG_WEIGHT * delta
        self.cumulative_reward += reward
        self.max_event_flags_seen = max(self.max_event_flags_seen, cur_flags)
        self.log.append(
            (turn, reward, f"event_flags+{delta} (total={cur_flags})"),
        )
        if len(self.log) > 300:
            self.log = self.log[-300:]
        return reward

    def record_healing(
        self, turn: int, prev_hp: int, cur_hp: int, cur_max_hp: int,
    ) -> float:
        """PWhiddy v2 healing reward: heal_amount * 10 when HP rises.

        Captures Pokemon Center visits and item heals — both strong
        progression signals (player chose to recover).
        """
        if cur_max_hp <= 0 or cur_hp <= prev_hp:
            return 0.0
        heal_frac = (cur_hp - prev_hp) / cur_max_hp
        if heal_frac < 0.5:
            return 0.0
        reward = HEAL_WEIGHT * heal_frac
        self.cumulative_reward += reward
        self.total_heal_amount += heal_frac
        self.log.append(
            (turn, reward, f"heal_frac={heal_frac:.2f}"),
        )
        if len(self.log) > 300:
            self.log = self.log[-300:]
        return reward

    def record_badge_delta(
        self, turn: int, prev_badges: int, cur_badges: int,
    ) -> float:
        """PWhiddy v2 badge reward: 10 per new badge."""
        if cur_badges <= prev_badges:
            return 0.0
        delta = cur_badges - prev_badges
        reward = BADGE_WEIGHT * delta
        self.cumulative_reward += reward
        self.max_badges_seen = max(self.max_badges_seen, cur_badges)
        self.log.append((turn, reward, f"BADGE+{delta} (total={cur_badges})"))
        if len(self.log) > 300:
            self.log = self.log[-300:]
        return reward

    def record_coord_visit(
        self, turn: int, map_g: int, map_n: int, x: int, y: int,
    ) -> float:
        """PWhiddy v2 stuck penalty + cell tracking.

        Tracks visit count per (map, x, y). Penalty after 600 visits
        to the same coord — discourages infinite-loop wandering.
        """
        key = f"{map_g}-{map_n}-{x}-{y}"
        count = self.coord_visits.get(key, 0) + 1
        self.coord_visits[key] = count
        if len(self.coord_visits) > 5000:
            keys_to_drop = list(self.coord_visits.keys())[:-4000]
            for k in keys_to_drop:
                self.coord_visits.pop(k, None)
        if count >= STUCK_THRESHOLD_TICKS:
            reward = STUCK_PENALTY_PER_TICK
            self.cumulative_reward += reward
            return reward
        return 0.0

    def record_death(self, turn: int) -> float:
        self.death_count += 1
        reward = DEATH_PENALTY
        self.cumulative_reward += reward
        self.log.append((turn, reward, f"DEATH (count={self.death_count})"))
        return reward

    def record_knn_novelty(self, turn: int, is_novel: bool) -> float:
        """PWhiddy v1 KNN exploration: small reward per novel frame."""
        if not is_novel:
            return 0.0
        reward = 5.0
        self.cumulative_reward += reward
        self.log.append((turn, reward, "knn_novel_frame"))
        return reward

    def record_dialog_progress(
        self, turn: int, prev_dialog: bool, cur_dialog: bool,
        prev_frame_hash: str, cur_frame_hash: str,
    ) -> float:
        """Reward for advancing through a dialog (text changing while dialog is open)."""
        if not cur_dialog:
            return 0.0
        if not prev_dialog:
            return 0.5
        if prev_frame_hash != cur_frame_hash:
            reward = 1.0
            self.cumulative_reward += reward
            return reward
        return 0.0

    def record_menu_action(
        self, turn: int, prev_menu: bool, cur_menu: bool,
    ) -> float:
        """Reward for opening/closing a menu intentionally."""
        if prev_menu == cur_menu:
            return 0.0
        reward = 0.5
        self.cumulative_reward += reward
        return reward

    def record_same_map_penalty(
        self, turn: int, same_map_streak: int,
    ) -> float:
        """Escalating penalty for sitting on one map too long.
        Tier 1: -0.5 per turn after 200 turns same map  (was -0.05/500)
        Tier 2: -1.0 per turn after 500 turns
        Tier 3: -2.0 per turn after 1500 turns
        Drastically heavier than before so PPO/CNN learn "leave this map
        is way more profitable than re-pressing direction".
        """
        if same_map_streak < 200:
            return 0.0
        if same_map_streak < 500:
            r = -0.5
        elif same_map_streak < 1500:
            r = -1.0
        else:
            r = -2.0
        self.cumulative_reward += r
        return r

    def record_npc_interact(
        self, turn: int, button: str, prev_dialog: bool, cur_dialog: bool,
    ) -> float:
        """Reward when pressing A in front of something opens a NEW dialog
        (interacting with NPC / object). Helps the agent learn "talk to NPC = good"."""
        if button != "A":
            return 0.0
        if prev_dialog or not cur_dialog:
            return 0.0
        reward = 2.0
        self.cumulative_reward += reward
        self.log.append((turn, reward, "npc_interact_opened_dialog"))
        return reward

    def record_battle_event(
        self,
        turn: int,
        prev_in_battle: bool,
        cur_in_battle: bool,
        prev_hp: int,
        cur_hp: int,
        cur_max_hp: int,
        prev_level: int,
        cur_level: int,
        prev_party_count: int,
        cur_party_count: int,
        prev_first_item_id: int,
        cur_first_item_id: int,
    ) -> float:
        reward = 0.0
        notes: list[str] = []
        if prev_in_battle and not cur_in_battle:
            if cur_hp > 0:
                reward += 50.0
                notes.append("battle_won_or_fled")
            elif cur_hp == 0 and cur_max_hp > 0:
                reward -= 200.0
                notes.append("whiteout")
        if cur_level > prev_level and cur_level > 0:
            reward += 200.0 * (cur_level - prev_level)
            notes.append(f"level_up({prev_level}->{cur_level})")
        if cur_party_count > prev_party_count and cur_party_count > 0:
            reward += 500.0
            notes.append(f"caught({prev_party_count}->{cur_party_count})")
        if (
            cur_first_item_id != prev_first_item_id
            and cur_first_item_id > 0
        ):
            reward += 50.0
            notes.append(f"item_changed({cur_first_item_id})")
        if reward != 0.0:
            self.cumulative_reward += reward
            self.log.append((turn, reward, ",".join(notes)))
            if len(self.log) > 300:
                self.log = self.log[-300:]
        return reward

    def pick_checkpoint(
        self,
        cur_map: tuple[int, int],
        rng: random.Random | None = None,
    ) -> tuple[int, int, int, int] | None:
        candidates = [c for c in self.cells if (c[0], c[1]) != cur_map]
        if not candidates:
            candidates = list(self.cells)
        if not candidates:
            return None
        r = rng if rng is not None else random.Random()
        return r.choice(candidates)
