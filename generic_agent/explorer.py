"""MapSurveyor — systematic $0 map coverage for new / under-explored maps.

When the agent enters a fresh map (low visit count) or stalls early with
few tiles seen, take over the decision pipeline with rule-based exploration:

1. From the current tile, probe a not-yet-tried passable direction.
2. If none, BFS toward the farthest unvisited frontier.
3. Otherwise the map is exhausted — hand back to Brain.

Survey ends when:
- Coverage delta meets the per-map-type target, or
- A hard per-survey turn cap is hit, or
- No more frontiers exist.

Survey is suspended automatically when in_battle / dialog handlers take
over the decision pipeline upstream — auto_loop only consults the
surveyor after those $0 handlers decline to act.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import tile_map as tile_map_mod

PROBE_ORDER = ("Up", "Right", "Down", "Left")

COVERAGE_TARGET_INDOOR = 25
COVERAGE_TARGET_OUTDOOR = 120
MAX_TURNS_PER_SURVEY = 300
STALE_LIMIT = 20  # tiles-grew-by-zero turns before forced exit


@dataclass
class SurveyState:
    active_map: tuple[int, int] | None = None
    turns_in_survey: int = 0
    coverage_at_start: int = 0
    tiles_at_last_progress: int = 0
    stale_turns: int = 0


class MapSurveyor:
    def __init__(self, tile_map: tile_map_mod.TileMap) -> None:
        self.tile_map = tile_map
        self.state = SurveyState()
        self.surveyed_this_session: set[tuple[int, int]] = set()

    def _target_coverage(self, map_group: int) -> int:
        if map_group in (1, 25):
            return COVERAGE_TARGET_INDOOR
        return COVERAGE_TARGET_OUTDOOR

    def _enter(self, map_group: int, map_num: int) -> None:
        mk = self.tile_map._map_key(map_group, map_num)
        tiles = self.tile_map._store.get(mk, {})
        self.state = SurveyState(
            active_map=(map_group, map_num),
            turns_in_survey=0,
            coverage_at_start=len(tiles),
            tiles_at_last_progress=len(tiles),
            stale_turns=0,
        )

    def _exit(self) -> None:
        if self.state.active_map is not None:
            self.surveyed_this_session.add(self.state.active_map)
        self.state = SurveyState()

    def in_survey(self) -> bool:
        return self.state.active_map is not None

    def _should_start(
        self,
        map_group: int,
        map_num: int,
        map_visits: int,
        same_map_streak: int,
    ) -> bool:
        # One-shot per session: already surveyed → defer to Brain forever
        if (map_group, map_num) in self.surveyed_this_session:
            return False
        # Brand-new or barely-visited map → always survey first
        if map_visits <= 5:
            return True
        # Stalling early on a sparsely-mapped map → survey
        mk = self.tile_map._map_key(map_group, map_num)
        tiles = self.tile_map._store.get(mk, {})
        target = self._target_coverage(map_group)
        if same_map_streak >= 30 and len(tiles) < target // 2:
            return True
        return False

    def _pick_direction(
        self, map_group: int, map_num: int, cur_x: int, cur_y: int
    ) -> str | None:
        mk = self.tile_map._map_key(map_group, map_num)
        tiles = self.tile_map._store.get(mk, {})
        cur_key = self.tile_map._tile_key(cur_x, cur_y)
        cur_rec = tiles.get(cur_key)

        if cur_rec is not None:
            blocked = set(cur_rec.blocked)
            tried = cur_rec.tried
            # Pass 1: neighbor unknown / unvisited → highest-info probe
            for d in PROBE_ORDER:
                if d in blocked:
                    continue
                dx, dy = tile_map_mod.DELTA[d]
                nk = self.tile_map._tile_key(cur_x + dx, cur_y + dy)
                neighbor = tiles.get(nk)
                if neighbor is None or neighbor.visits == 0:
                    return d
            # Pass 2: direction never tried here → still informative
            for d in PROBE_ORDER:
                if d in blocked:
                    continue
                if tried.get(d, 0) == 0:
                    return d

        # Pass 3: BFS to farthest frontier (edge bias)
        return self.tile_map.bfs_frontier_direction(
            map_group, map_num, cur_x, cur_y, prefer="farthest",
        )

    def maybe_step(
        self,
        map_group: int,
        map_num: int,
        cur_x: int,
        cur_y: int,
        map_visits: int,
        same_map_streak: int,
    ) -> str | None:
        """Return the survey-driven button, or None to defer to Brain."""
        cur_map = (map_group, map_num)

        # Active session on a different map → end it (we moved)
        if self.state.active_map is not None and self.state.active_map != cur_map:
            self._exit()

        if self.state.active_map == cur_map:
            mk = self.tile_map._map_key(map_group, map_num)
            tiles = self.tile_map._store.get(mk, {})
            target = self._target_coverage(map_group)
            covered = len(tiles) - self.state.coverage_at_start
            if len(tiles) > self.state.tiles_at_last_progress:
                self.state.tiles_at_last_progress = len(tiles)
                self.state.stale_turns = 0
            else:
                self.state.stale_turns += 1
            if (
                self.state.turns_in_survey >= MAX_TURNS_PER_SURVEY
                or covered >= target
                or self.state.stale_turns >= STALE_LIMIT
            ):
                self._exit()
                return None
        else:
            if not self._should_start(
                map_group, map_num, map_visits, same_map_streak
            ):
                return None
            self._enter(map_group, map_num)

        direction = self._pick_direction(
            map_group, map_num, cur_x, cur_y
        )
        if direction is None:
            self._exit()
            return None
        self.state.turns_in_survey += 1
        return direction
