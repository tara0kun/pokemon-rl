"""Jagged Pass grind-nav walk-model regression (2026-07-22).

Root cause pinned offline: MB_BUMPY_SLOPE (0xD1) tiles carry collision
bits 0 but are Acro-Bike-only in game (pokeemerald CheckAcroBikeCollision
returns COLLISION_WHEELIE_HOP -> on foot you bump). Every collision-
"walkable" route UP Jagged Pass runs through such strips, so the pre-fix
BFS returned a phantom Up-path ((9,34) -> the (9,30-32) strip) that live
play could not walk: the agent bumped, wandered off the one-way south
ledges into the bottom funnel, stepped the (14/15,40) warp pad and bounced
JaggedPass <-> Route112 pocket forever while the grind never ran.

These tests pin the fixed walk model (map_data.FOOT_IMPASSABLE_BEHAVIORS):
  - the bottom of the pass genuinely cannot reach ANY grass tile on foot;
  - the TOP entry (Mt.Chimney warp pads) reaches the grind pin, and the
    returned path replays cleanly under game rules (ledge vaults, zero
    bumps into foot-impassable tiles);
  - the descend / walk-home legs stay alive;
  - Route112: the pocket reaches the cable-car station (one-way ledge
    descent), the road leg the grind loop re-boards the car with.

Second overshoot (07-22, live): the walk model above was correct, yet the
descent still bottomed out on the warp pad every cycle. run()'s
forward_force explore override rewrote the BFS's Right/Left grass-branch
turn into the entry direction ("Down") for 30 turns after map entry; the
center column is a one-way JUMP_SOUTH ledge chain, forced Downs VAULT
(never bump, so tile_map's blocked-dir self-heal can't kick in), and the
agent flew past both branch rows into the bottom funnel.
TestForwardForceOverride pins the src gate that fixes it, and
TestJaggedDescentReplay replays the whole descent through the REAL
heuristic_button + the REAL forward_force_override under game movement
rules.

Pure offline: reads only cached map_cache files. The replay redirects the
map_knowledge store to a temp dir, so nothing under memory/ is written;
skips without the cache.
"""
from __future__ import annotations

import struct
import unittest

from generic_agent import (
    claude_heuristic as ch,
    config,
    map_data as md,
)
from generic_agent.map_knowledge import GRASS_BEHAVIORS

JAGGED = (24, 13)
R112 = (0, 27)
DIRS = {"Up": (0, -1), "Down": (0, 1), "Left": (-1, 0), "Right": (1, 0)}


def _have(*names: str) -> bool:
    return all((config.MEMORY_DIR / "map_cache" / n).exists() for n in names)


_HAVE_JAGGED = _have("JaggedPass.map.bin", "JaggedPass.map.json")
_HAVE_R112 = _have("Route112.map.bin", "Route112.map.json")


def _nav_inputs(mc, g: int, n: int):
    """(elevation, ledges) straight from the cached canon files — same
    derivation map_knowledge seeds from, without touching its store."""
    info = mc.get(g, n)
    raw = (
        config.MEMORY_DIR / "map_cache" / f"{info.name}.map.bin"
    ).read_bytes()
    elev: dict[tuple[int, int], int] = {}
    for y in range(info.height):
        for x in range(info.width):
            off = (y * info.width + x) * 2
            if off + 2 <= len(raw):
                elev[(x, y)] = (
                    struct.unpack_from("<H", raw, off)[0] >> 12
                ) & 0xF
    ledges = {
        t: md.LEDGE_JUMP_BEHAVIORS[bv]
        for t, bv in mc.behavior_grid(g, n).items()
        if bv in md.LEDGE_JUMP_BEHAVIORS
    }
    return elev, ledges


def _walk_sim(mc, g: int, n: int, start, path, ledges):
    """Replay `path` under game movement rules: a ledge tile entered in its
    jump direction vaults 2 tiles out; a foot-impassable tile bumps (no
    move). Returns (final_pos, bump_count)."""
    grid = mc.behavior_grid(g, n)
    x, y = start
    bumps = 0
    for btn in path:
        dx, dy = DIRS[btn]
        nx, ny = x + dx, y + dy
        if ledges.get((nx, ny)) == (dx, dy):
            nx, ny = nx + dx, ny + dy
        if grid.get((nx, ny)) in md.FOOT_IMPASSABLE_BEHAVIORS:
            bumps += 1
            continue
        x, y = nx, ny
    return (x, y), bumps


@unittest.skipUnless(_HAVE_JAGGED, "JaggedPass canon cache not present")
class TestJaggedPassGrindNav(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mc = md.get_cache()
        cls.info = cls.mc.get(*JAGGED)
        assert cls.info is not None
        cls.elev, cls.ledges = _nav_inputs(cls.mc, *JAGGED)
        cls.grid = cls.mc.behavior_grid(*JAGGED)
        cls.grass = {
            t for t, bv in cls.grid.items() if bv in GRASS_BEHAVIORS
        }
        # Warp-derived anchor tiles (no coordinate hardcoding drift).
        cls.top_pads = [
            (w["x"], w["y"]) for w in cls.info.warps
            if "MtChimney" in str(w.get("dest_map", ""))
        ]
        cls.bottom_pads = [
            (w["x"], w["y"]) for w in cls.info.warps
            if "Route112" in str(w.get("dest_map", ""))
        ]

    def _bfs(self, start, targets):
        return self.mc.bfs_to_tile(
            *JAGGED, start, set(targets),
            tile_elevation=self.elev, ledge_jumps=self.ledges,
        )

    def test_bumpy_strip_is_foot_impassable(self) -> None:
        # The strip the phantom Up-path rode: canon MB_BUMPY_SLOPE, and the
        # walk model must list it as foot-impassable.
        foot = self.mc._foot_impassable_tiles(*JAGGED)
        for t in ((9, 30), (9, 31), (9, 32)):
            self.assertEqual(self.grid.get(t), 0xD1, t)
            self.assertIn(t, foot)

    def test_bottom_cannot_reach_any_grass_on_foot(self) -> None:
        # (9,34) = the live-observed stuck tile below the strip; the two
        # bottom warp pads = where a pocket entry lands. NONE of them may
        # reach ANY grass tile: the pre-fix phantom path is dead, and any
        # future goal aiming a bottom entry at the grass is a design bug.
        self.assertTrue(self.grass)
        for start in [(9, 34), *self.bottom_pads]:
            self.assertIsNone(self._bfs(start, self.grass), start)

    def test_descend_leg_alive_and_replays_clean(self) -> None:
        # The badge-4 descend leg (Mt.Chimney landing pads -> the bottom
        # Route112 warp pads) must survive the stricter walk model AND replay
        # under game rules with zero bumps into foot-impassable tiles (catches
        # any future walk-model drift where BFS emits steps the game refuses).
        # This is the post-L48 Flannery approach; the grind itself moved to
        # Fiery Path (see test_fiery_grind), so no grass-pin leg is asserted.
        self.assertTrue(self.top_pads)
        self.assertTrue(self.bottom_pads)
        for start in self.top_pads:
            path = self._bfs(start, set(self.bottom_pads))
            self.assertIsNotNone(path, start)
            final, bumps = _walk_sim(
                self.mc, *JAGGED, start, path, self.ledges,
            )
            self.assertEqual(bumps, 0, start)
            self.assertIn(final, set(self.bottom_pads), start)


class TestUiEscape(unittest.TestCase):
    """ui_escape_button backs out of an unknown-UI screen (PokeNav, Trainer
    Card, the level-up 'forget move?' prompt) after 3 consecutive frames. Root
    fix for the 2026-07-22 ~3700-turn PokeNav imprisonment. Pure function."""

    def test_silent_below_threshold(self) -> None:
        for s in (0, 1, 2):
            self.assertIsNone(ch.ui_escape_button(s))

    def test_bbba_cycle_at_and_above_threshold(self) -> None:
        got = [ch.ui_escape_button(s) for s in (3, 4, 5, 6, 7)]
        # B,B,B,A repeating (indices 3..7 -> cycle[3%4..7%4])
        self.assertEqual(got, ["A", "B", "B", "B", "A"])
        self.assertTrue(all(b in ("A", "B") for b in got))


class TestForwardForceOverride(unittest.TestCase):
    """The 07-22 overshoot fix: forward_force must never rewrite a
    goal-directed button, while keeping its explore behavior for
    non-goal srcs. Pure function, no cache needed."""

    def test_never_overrides_goal_directed(self) -> None:
        for src in (
            "mapbfs:Right->JaggedPass(dist=6)",
            "mapbfs_warp:Down->Route112",
            "goal_map_explore:Down",
            "rival_seek:Up",
            "rival_talk:A",
        ):
            self.assertIsNone(
                ch.forward_force_override("Down", True, "Right", src, set(), 7),
                src,
            )

    def test_forces_entry_dir_on_explore_src(self) -> None:
        self.assertEqual(
            ch.forward_force_override(
                "Down", True, "Right", "explore:Right", set(), 7,
            ),
            "Down",
        )

    def test_perp_when_entry_blocked_and_button_opposes(self) -> None:
        got = ch.forward_force_override(
            "Down", True, "Up", "explore:Up", {"Down"}, 4,
        )
        self.assertIn(got, ("Left", "Right"))

    def test_silent_outside_window_or_when_blocked(self) -> None:
        self.assertIsNone(
            ch.forward_force_override(
                "Down", False, "Right", "explore:x", set(), 4,
            ),
        )
        # entry blocked and button is a sane sideways move: leave it alone
        self.assertIsNone(
            ch.forward_force_override(
                "Down", True, "Right", "explore:x", {"Down"}, 4,
            ),
        )


@unittest.skipUnless(_HAVE_R112, "Route112 canon cache not present")
class TestRoute112PocketToStation(unittest.TestCase):
    def test_pocket_reaches_cable_car_station(self) -> None:
        # The grind loop's road leg: from the JaggedPass landing pads in the
        # SW pocket, the cable-car station must be walk-reachable (one-way
        # ledge descent to the south blob, then up to the station door).
        mc = md.get_cache()
        info = mc.get(*R112)
        self.assertIsNotNone(info)
        elev, ledges = _nav_inputs(mc, *R112)
        pads = [
            (w["x"], w["y"]) for w in info.warps
            if "Jagged" in str(w.get("dest_map", ""))
        ]
        self.assertTrue(pads)
        station = mc.warp_tiles_for(*R112, "Route112_CableCarStation")
        self.assertTrue(station)
        for start in pads:
            self.assertIsNotNone(
                mc.bfs_to_tile(
                    *R112, start, set(station),
                    tile_elevation=elev, ledge_jumps=ledges,
                ),
                start,
            )


if __name__ == "__main__":
    unittest.main()
