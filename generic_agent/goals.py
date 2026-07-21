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
VISITED_MAPS_FILE = config.MEMORY_DIR / "visited_maps.json"
# Emerald's SaveBlock1 is DMA-relocated every frame; reading a story flag
# mid-relocation (common during battle) intermittently returns 0. Since
# FLAG_RECOVERED_DEVON_GOODS never legitimately clears, latch it to disk on
# first True read so the goal chain can't flicker rescue_peeko <-> peeko_return.
PEEKO_DONE_MARKER = config.MEMORY_DIR / "peeko_done.marker"


def _peeko_done(gs) -> bool:
    """Monotonic 'Peeko rescued' signal, immune to the SaveBlock1 flag flicker."""
    if PEEKO_DONE_MARKER.exists():
        return True
    if bool(getattr(gs, "flag_devon_goods_recovered", False)):
        try:
            PEEKO_DONE_MARKER.parent.mkdir(parents=True, exist_ok=True)
            PEEKO_DONE_MARKER.write_text("1", encoding="utf-8")
        except OSError:
            pass
        return True
    return False


# Route112 (0,27) is one map split by Mt.Chimney into two walkable blobs that
# only connect through the Fiery Path cave. The SOUTH blob (entered from
# Route111) can reach Fallarbor ONLY by crossing Fiery Path; the NORTH blob
# reaches it via Route111 north -> Route113. The map graph collapses a map to
# one node, so it can't express "Route112 -> Fiery Path -> Route112" and the
# router ping-pongs Route111<->Route112. fiery_path_cross fixes that: while in
# the south blob it routes to Fiery Path. "South blob" = the component holding
# the higher-y Fiery Path warp (derived from map data, not a hardcoded tile).
_ROUTE112 = (0, 27)
_FIERY_PATH = (24, 14)


def _in_route112_fiery_south(gs) -> bool:
    if (getattr(gs, "map_group", None), getattr(gs, "map_num", None)) != _ROUTE112:
        return False
    try:
        from . import map_data as _md
        mc = _md.get_cache()
        info = mc.get(*_ROUTE112)
        if info is None:
            return False
        fiery = [
            (w["x"], w["y"]) for w in (info.warps or [])
            if "Fiery" in str(w.get("dest_map", ""))
        ]
        if not fiery:
            return False
        south_warp = max(fiery, key=lambda t: t[1])  # higher y = south side
        tile2cid, _ = mc._components(*_ROUTE112)
        south_cid = tile2cid.get(south_warp)
        return south_cid is not None and tile2cid.get((gs.x, gs.y)) == south_cid
    except (OSError, RuntimeError, KeyError, AttributeError):
        return False


# H17 (Flannery numbers wall, 07-19 night): Rock Tomb 50-power + Super Potion
# (+50) loses the numbers race at L42 — Sunny-boosted Overheat measured ~128
# vs the lead's 137 max HP, so a full-HP lead drops to single digits before
# battle_heal's window even opens, and +50 never out-heals the next hit.
# Grinding the lead to this level flips both races (scaled from the measured
# L42 numbers): HP ~155 / SpD +13% puts the first Overheat at ~73% (survivable
# from full, healable after the White-Herb reset), and Atk ~95 puts Rock Tomb
# (2x vs Fire) into 2HKO range on Torkoal (Def140) / OHKO on Numel+Slugma, so
# at most ~two Overheats land per fight instead of a KO per turn.
# 48: the L42 lead LOST Flannery even with Rock Tomb + battle_heal (verified
# 2026-07-22 — Super Potion +50 can't out-heal Torkoal's Overheat ~128; Sceptile
# healed 2->52 HP and the next Overheat re-KO'd it). So the grind IS needed.
# Nav: bfs_to_tile's ledge model and the pin were CORRECT all along — the
# descent overshoot was run()'s forward_force explore override rewriting the
# BFS's grass-branch turn into "Down" for 30 turns after map entry (fixed
# 2026-07-22, forward_force_override src gate; TestJaggedDescentReplay pins
# the full descent). See daily 2026-07-22.
FLANNERY_GRIND_TARGET_LEVEL = 48

LETTER_DONE_MARKER = config.MEMORY_DIR / "steven_letter_done.marker"
DEVON_DELIVERED_MARKER = config.MEMORY_DIR / "devon_delivered.marker"
ROCK_SMASH_TAUGHT_MARKER = config.MEMORY_DIR / "rock_smash_taught.marker"


def _latched(gs, attr: str, marker) -> bool:
    """Monotonic story-flag latch, immune to the SaveBlock1 DMA drop-flicker.
    Once the flag has been observed True, a disk marker keeps it True forever —
    the flag reading False for a single frame otherwise flips the goal (e.g. the
    Steven-letter flicker flipped the goal between the Dewford sail and the
    deep-cave letter). RISE-flicker protection (a spurious True must not
    false-latch) lives in state.read_state for the future-event gate flags 0x333
    /0x8B, which is where a single garbage read once wrote meteor_theft_done.
    marker and skipped the theft. Mirrors _peeko_done."""
    if marker.exists():
        return True
    if bool(getattr(gs, attr, False)):
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("1", encoding="utf-8")
        except OSError:
            pass
        return True
    return False


def _letter_done(gs) -> bool:
    return _latched(gs, "flag_steven_letter_delivered", LETTER_DONE_MARKER)


def _devon_delivered(gs) -> bool:
    return _latched(gs, "flag_devon_goods_delivered", DEVON_DELIVERED_MARKER)


def _rock_smash_taught(gs) -> bool:
    """Latched 'a party mon knows Rock Smash'. party_moves reads empty on a DMA
    flicker frame -> knows_rock_smash False -> teach_rock_smash re-fires and
    hm_teach re-opens the bag/teach menu long after it was taught (a Route114
    'Teach which POKeMON?' stall, 07-18). Once taught it never un-learns."""
    return _latched(gs, "knows_rock_smash", ROCK_SMASH_TAUGHT_MARKER)


THEFT_DONE_MARKER = config.MEMORY_DIR / "meteor_theft_done.marker"
MTCHIMNEY_DONE_MARKER = config.MEMORY_DIR / "mtchimney_done.marker"


def _theft_done(gs) -> bool:
    """Latched Meteor Falls theft (FLAG_HIDE_ROUTE_112_TEAM_MAGMA, 0x333). The
    whole Badge4 arc is split by this flag: the four pre-cable-car goals gate on
    NOT done, the cable-car..Lavaridge goals gate on done. A single DMA-flicker
    frame reading 0x333 False would re-fire meteor_falls_theft (cur-ungated) and
    yank the agent back north — the reach_mauville<->deliver_devon flip mechanism.
    Genuinely-True-once past event, so a disk latch is the correct discipline."""
    return _latched(gs, "flag_route112_magma_cleared", THEFT_DONE_MARKER)


def _mtchimney_done(gs) -> bool:
    """Latched Mt.Chimney Team Magma defeat (FLAG_DEFEATED_EVIL_TEAM_MT_CHIMNEY,
    0x8B). Gates the Jagged Pass descent + Lavaridge approach. Same DMA-flicker
    discipline as _theft_done."""
    return _latched(gs, "flag_mtchimney_magma_defeated", MTCHIMNEY_DONE_MARKER)


def _in_route112_jagged_pocket(gs) -> bool:
    """True when the player stands in Route112's SW pocket -- the component that
    holds the JaggedPass landing warp, reached only by descending Jagged Pass.
    Mirror of _in_route112_fiery_south. ride_cable_car goes silent here: the
    pocket cannot reach the cable-car station on foot (a one-way ledge seals it),
    so re-targeting the station from the pocket would strand the agent."""
    if (getattr(gs, "map_group", None), getattr(gs, "map_num", None)) != _ROUTE112:
        return False
    try:
        from . import map_data as _md
        mc = _md.get_cache()
        info = mc.get(*_ROUTE112)
        if info is None:
            return False
        jagged = [
            (w["x"], w["y"]) for w in (info.warps or [])
            if "Jagged" in str(w.get("dest_map", ""))
        ]
        if not jagged:
            return False
        tile2cid, _ = mc._components(*_ROUTE112)
        pocket_cid = tile2cid.get(jagged[0])
        return pocket_cid is not None and tile2cid.get((gs.x, gs.y)) == pocket_cid
    except (OSError, RuntimeError, KeyError, AttributeError):
        return False


_GOAL_ORDER_WEIGHT = {
    "get_starter_via_lab": 0,
    "return_to_lab_for_pokedex": 10,
    "reach_oldale": 20,
    "reach_route_103_rival": 30,
    "reach_route_102": 40,
    "reach_petalburg": 50,
    "reach_rustboro_gym": 60,
    "enter_rustboro_gym": 65,
    "peeko_r104_to_woods": 61,
    "peeko_woods_north": 62,
    "rescue_peeko": 64,
    "peeko_return": 65,
    "dewford_to_woods": 66,
    "dewford_woods_south": 67,
    "dewford_to_briney": 68,
    "dewford_sail": 69,
    "heal_at_dewford_pc": 73,
    "grind_granite_cave": 72,
    "reach_dewford_gym": 70,
    "dewford_gym_brawly": 71,
    "deliver_steven_letter": 80,
    "sail_to_slateport": 81,
    "reach_slateport": 82,
    "deliver_devon_dock": 83,
    "deliver_devon_goods": 84,
    "heal_at_slateport": 84,
    "heal_at_mauville": 85,
    "mauville_gym_wattson": 85,
    "reach_mauville": 86,
}


# Goals whose target_map is the actual completion target (Gym building),
# not just a waypoint. For these, the visited-maps backtrack-suppression
# is skipped: visiting the gym map once doesn't mean we beat the leader.
_GOAL_BYPASS_VISITED = {
    "enter_rustboro_gym", "grind_route_104_north",
    # Dewford journey waypoints re-visit maps (Route104 north AND south are
    # the same map id), so visited-map suppression must not disable them.
    "dewford_to_woods", "dewford_woods_south",
    "dewford_to_briney", "dewford_sail",
    "peeko_r104_to_woods", "peeko_woods_north",
    # rescue_peeko: the tunnel gets marked visited the moment we enter it,
    # mid-quest. peeko_return: Route104 is long-visited by definition.
    "rescue_peeko", "peeko_return",
    # Dewford gym: town + gym get marked visited on first entry, but we must
    # keep re-targeting the gym door / Brawly until the badge is won.
    "reach_dewford_gym", "dewford_gym_brawly",
    # Grind loop: the cave and PC get marked visited immediately but must stay
    # re-targetable every heal/grind cycle until the lead reaches L30.
    "grind_granite_cave", "heal_at_dewford_pc",
    # Post-Brawly: StevensRoom/cave get marked visited during the grind, but the
    # letter goal must stay re-targetable until the delivery flag flips.
    "deliver_steven_letter",
    # Slateport chain: Dewford (Briney) is long-visited; Slateport is the target.
    "sail_to_slateport", "reach_slateport",
    # Devon Goods: Shipyard/Museum get marked visited on entry but the delivery
    # goals must stay re-targetable until the flags flip.
    "deliver_devon_dock", "deliver_devon_goods",
    # Mauville: Route110/Mauville get marked visited on entry, but reach_mauville
    # must stay live so the agent doesn't go goal-less after first touching the
    # city, and mauville_gym_wattson must persist until the badge is won.
    "reach_mauville", "mauville_gym_wattson",
    # Slateport PC gets visited immediately but must stay re-targetable for every
    # heal cycle along the Route110 grind.
    "heal_at_slateport",
    # Mauville PC: re-targetable for every heal cycle through the Wattson gym.
    "heal_at_mauville",
    # Fiery Path is a CROSSING, not a one-shot waypoint: FieryPath (24,14) gets
    # marked visited the instant the agent steps in, but it must keep routing
    # there (and back in on each pass) until it exits into Route112's NORTH
    # blob. Without the bypass, one visit disabled fiery_path_cross and the
    # agent fell to reach_fallarbor, oscillating Route111<->Route112 south.
    "fiery_path_cross",
    # Meteor Falls gets marked visited the instant the agent steps in, but the
    # theft cutscene may not have fired yet (it's mid-room); keep re-targeting
    # the event zone until FLAG 0x333 flips.
    "meteor_falls_theft",
    # Badge4 arc legs 3-6 + final. Each target map is marked visited on first
    # entry, but a whiteout (Flannery beats a Grass lead) sends the agent back to
    # re-board the cable car / re-descend / re-heal / re-fight until the badge is
    # won, so all must stay re-targetable. exit_fiery_path_south is intentionally
    # NOT here: it only fires when cur == FieryPath, where visited-suppression
    # (target_map != cur) can't apply (same as exit_fiery_path_north).
    "ride_cable_car", "mtchimney_defeat_magma", "descend_jagged_pass",
    "heal_at_lavaridge", "lavaridge_gym_flannery", "reach_lavaridge",
    # Flannery grind loop (H17): JaggedPass is marked visited on the first
    # descent, but the grind goal must keep re-targeting it from town/PC on
    # every heal cycle until the lead reaches the target level (the
    # grind_granite_cave bypass, mirrored). The cable-car station is equally
    # long-visited, so the loop's road leg needs the same bypass.
    "grind_pre_flannery", "grind_reboard_cable_car",
    # The Mauville Mart is marked visited on first entry, but buy_potions must
    # stay re-targetable to restock (a whiteout back to Mauville, or before the
    # Flannery gym). field_heal_potion has target_map=None so it never needs it.
    "buy_potions",
}


def _load_visited_maps() -> set[tuple[int, int]]:
    if not VISITED_MAPS_FILE.exists():
        return set()
    try:
        data = json.loads(VISITED_MAPS_FILE.read_text(encoding="utf-8"))
        return {tuple(m) for m in data.get("visited", [])}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return set()


def _save_visited_maps(visited: set[tuple[int, int]]) -> None:
    try:
        VISITED_MAPS_FILE.parent.mkdir(parents=True, exist_ok=True)
        VISITED_MAPS_FILE.write_text(
            json.dumps({"visited": sorted(list(visited))}),
            encoding="utf-8",
        )
    except OSError:
        pass


def record_map_visit(map_group: int, map_num: int) -> None:
    """Persistent map-visit log so goal cascade can skip already-cleared
    early-game waypoints (reach_oldale, reach_route_102, ...) once the
    agent has moved further along the canonical Hoenn route."""
    visited = _load_visited_maps()
    key = (int(map_group), int(map_num))
    if key in visited:
        return
    visited.add(key)
    _save_visited_maps(visited)


@dataclass
class Goal:
    name: str
    target_map: tuple[int, int] | None
    condition: str
    desc: str
    # Optional specific tile target within target_map. Used by grinding
    # goals (e.g. Route 104 south grass). When None, mapbfs uses default
    # exit_tiles or warp_tiles. When set, mapbfs targets this exact tile.
    target_pos: tuple[int, int] | None = None

    def matches(self, gs) -> bool:
        c = self.condition
        if c == "no_party":
            return gs.party_count == 0
        if c == "first_starter":
            # Pre-Pokedex Oldale milestone. After Pokedex received the agent
            # has already cycled through Oldale (heal at PC) and should move on.
            try:
                fbytes = bytes.fromhex(gs.event_flag_bytes_hex or "")
                pokedex_received = (
                    (fbytes[0x74 // 8] >> (0x74 % 8)) & 1
                    if len(fbytes) > 0x74 // 8 else 0
                )
            except (ValueError, IndexError):
                pokedex_received = 0
            return (
                gs.party_count == 1
                and gs.badge_count == 0
                and pokedex_received == 0
            )
        if c == "no_badge":
            return gs.badge_count == 0 and gs.party_count >= 1
        if c == "no_badge_pre_pokedex":
            # Pre-Pokedex AND Rival not yet defeated (route 103 rival waypoint)
            try:
                fbytes = bytes.fromhex(gs.event_flag_bytes_hex or "")
                rival_defeated = (
                    (fbytes[0x82 // 8] >> (0x82 % 8)) & 1
                    if len(fbytes) > 0x82 // 8 else 0
                )
            except (ValueError, IndexError):
                rival_defeated = 0
            return (
                gs.badge_count == 0
                and gs.party_count >= 1
                and gs.total_event_flags < 200
                and rival_defeated == 0
            )
        if c == "rival_defeated_no_pokedex":
            # After Rival defeat: FLAG_DEFEATED_RIVAL_ROUTE103 (0x82) set.
            # After Pokedex received: FLAG_ADVENTURE_STARTED (0x74) set.
            # We want this goal active in the gap between those two events.
            try:
                fbytes = bytes.fromhex(gs.event_flag_bytes_hex or "")
                rival_defeated = (
                    (fbytes[0x82 // 8] >> (0x82 % 8)) & 1
                    if len(fbytes) > 0x82 // 8 else 0
                )
                pokedex_received = (
                    (fbytes[0x74 // 8] >> (0x74 % 8)) & 1
                    if len(fbytes) > 0x74 // 8 else 0
                )
            except (ValueError, IndexError):
                rival_defeated = 0
                pokedex_received = 0
            return (
                rival_defeated == 1
                and pokedex_received == 0
                and gs.badge_count == 0
            )
        if c.startswith("badge>="):
            n = int(c.split(">=")[1])
            return gs.badge_count >= n
        # Dewford journey (post Stone Badge). Route 104 (0,19) is split into
        # a NORTH region (Rustboro side, y<34) and a SOUTH beach (Mr.Briney,
        # y>=34) that are NOT walkably connected within the map — you cross
        # via Petalburg Woods (24,11). So the goal chain is position-aware:
        # north Route104 -> Woods -> south Route104 -> Briney's house -> sail.
        cur = (gs.map_group, gs.map_num)
        # Peeko rescue must happen BEFORE the sail is possible. The old
        # proxy "visited Rusturf Tunnel (24,4)" flipped the journey the
        # moment the agent ENTERED the tunnel — before the grunt battle —
        # so it would turn around and leave Peeko behind. Use the real
        # story gate instead: FLAG_RECOVERED_DEVON_GOODS (0x8F), set by
        # RusturfTunnel scripts only after the grunt is beaten. Before it,
        # the Route104 woods crossing runs NORTHWARD (beach -> Rustboro);
        # after it, SOUTHWARD (the Dewford journey).
        peeko_done = _peeko_done(gs)
        # rescue_peeko itself: active until the grunt is actually beaten.
        # (Was condition="badge>=1", which kept targeting the grunt's tile
        # forever after the rescue — the goal never released the agent.)
        if c == "peeko_not_rescued":
            return gs.badge_count >= 1 and not peeko_done
        # Return journey after the rescue: tunnel -> Route116 -> Rustboro
        # -> Route104 north. Without this, reach_dewford_gym (target Dewford
        # (0,11), unreachable by land) wins on these maps and the planner
        # has no path — the agent would wander. One goal suffices: the
        # multi-map planner routes the hops to Route104 (0,19).
        if c == "peeko_return":
            return (
                gs.badge_count >= 1 and peeko_done
                and cur in {(24, 4), (0, 31), (0, 3)}
            )
        # Northward crossing (only while Peeko not yet rescued):
        if c == "peeko_r104_south":
            return (
                gs.badge_count >= 1 and cur == (0, 19)
                and gs.y >= 34 and not peeko_done
            )
        if c == "peeko_in_woods":
            return (
                gs.badge_count >= 1 and cur == (24, 11) and not peeko_done
            )
        # Dewford journey (southward) — only AFTER Peeko rescued:
        if c == "dewford_route104_north":
            return (
                gs.badge_count >= 1 and cur == (0, 19)
                and gs.y < 34 and peeko_done
            )
        if c == "dewford_in_woods":
            return gs.badge_count >= 1 and cur == (24, 11) and peeko_done
        if c == "dewford_route104_south":
            return (
                gs.badge_count >= 1 and cur == (0, 19)
                and gs.y >= 34 and peeko_done
            )
        if c == "dewford_in_briney_house":
            return gs.badge_count >= 1 and cur == (17, 0) and peeko_done
        # Dewford Gym (Brawly / Knuckle Badge). Two position-exclusive legs so
        # ordering can't route the agent back out of the gym:
        #  - approach: Stone Badge earned, Knuckle not yet, and NOT inside the
        #    gym -> head to Dewford Town and its gym door (15,24).
        #  - brawly: inside DewfordTown_Gym (3,3) -> walk to Brawly (11,10).
        if c == "dewford_gym_approach":
            # Get TO Dewford Town from elsewhere. On (0,11) itself this goal has
            # no target_pos so current_goal auto-skips it and seek_brawly wins.
            # LEVEL GATE (H6b): under L30 the grind goal owns navigation, so
            # this must NOT match — otherwise it routes the agent out of the
            # grind area back to Dewford every step and grinding never runs.
            return (
                gs.badge_count >= 1 and gs.badge_count < 2
                and not _letter_done(gs)
                and gs.party0_level >= 30
                and cur != (3, 3)
            )
        if c == "seek_brawly":
            # Fires on Dewford Town (planner routes through the gym warp) and
            # inside the gym (walk to Brawly). Coords are map_data/canon, the
            # same frame as gs.x/gs.y — NOT the memory's stale "+7" values.
            # LEVEL GATE (H6b): only challenge Brawly once the lead is L30+.
            # Grovyle's moves cap at 40 power (neutral vs Fighting) — it does
            # NOT learn Leaf Blade by the mid-20s (checked live at L26). So it
            # needs a raw stat lead (HP+Atk) to out-tempo Brawly's Bulk Up
            # Makuhita L19; L26 still lost at 25 HP, L30 (11 levels up) is the
            # safe margin. Under L30 the grind goal below wins instead.
            return (
                gs.badge_count >= 1 and gs.badge_count < 2
                and not _letter_done(gs)
                and gs.party0_level >= 30
                and cur in {(0, 11), (3, 3)}
            )
        if c == "heal_low_hp_dewford":
            # Lead hurt (<40% HP) on the Dewford side → route to the Dewford PC
            # nurse. Used by the grind loop (pre-Brawly) AND the post-Brawly
            # Steven-letter trek: after Brawly the lead is often near-dead (L30
            # 2/86 vs the Bulk Up Makuhita), and Granite Cave 1F rolls a wild
            # encounter on every floor step, so walking to Steven at 2 HP would
            # faint and whiteout. badge>=1 (not <2) so it also guards the
            # post-badge cave trek; cave B2F/StevensRoom added to the zone.
            return (
                gs.badge_count >= 1
                and gs.party0_max_hp > 0
                and gs.party0_hp_frac < 0.4
                and not gs.in_battle
                and cur in {(0, 11), (3, 1), (3, 3), (0, 21), (24, 7),
                            (24, 8), (24, 9), (24, 10)}
            )
        if c == "grind_pre_brawly":
            # Lead below L30 → grind wild battles in Granite Cave 1F (reachable
            # from Dewford via Route106) until it has the stat lead + Leaf Blade
            # (learnt at L29) to sweep Brawly, then seek_brawly takes over. B1F
            # (24,8) is included so a fall down the ladder keeps the grind goal
            # matching and routes the lead back up to 1F instead of wandering
            # the dark (requires_flash) sublevel goal-less.
            return (
                1 <= gs.badge_count < 2
                and gs.party0_level < 30
                and cur in {(0, 11), (3, 1), (0, 21), (24, 7), (24, 8)}
            )
        if c == "deliver_steven_letter":
            # Post-Brawly (H4): deliver the Letter to Steven in
            # GraniteCave_StevensRoom (24,10). This is the HARD gate for the
            # Dewford->Slateport sail (decomp DewfordTown/scripts.inc does
            # goto_if_unset FLAG_DELIVERED_STEVEN_LETTER). Steven's room is
            # reached DIRECTLY from cave 1F warp (5,10) — both maps bright
            # (requires_flash=False), so no dark B1F/B2F traversal. Active until
            # the flag flips (delivery also grants TM47 Steel Wing).
            return (
                gs.badge_count >= 2
                and not _letter_done(gs)
            )
        if c == "sail_to_slateport":
            # Post-letter (H4b): the letter flag is set, so Mr.Briney at Dewford
            # (12,9) now offers the Slateport sail (a Petalburg/Slateport
            # multichoice — the heuristic picks Slateport). Gated to the
            # Dewford-side maps so once the sail lands the agent on Route109 this
            # goal goes silent (no back-routing to Dewford).
            # NOT gated on devon anymore: this ALSO serves whiteout recovery.
            # A Route110 faint whites the agent out to the last-healed PC — which
            # was Dewford, across the sea — and reach_mauville then routes it onto
            # the impassable Route107/108 open water. Re-sailing from Briney is
            # the only way back, so the goal fires on the whole Dewford side
            # (incl. Route107 0,22) whenever the agent is stranded there, until
            # badge 3. Once healing re-homes the whiteout point to Slateport
            # (heal_at_slateport) this recovery stops being needed.
            return (
                gs.badge_count >= 2
                and _letter_done(gs)
                and gs.badge_count < 3
                and cur in {(0, 11), (3, 1), (3, 3), (0, 21), (0, 22),
                            (24, 7), (24, 8), (24, 9), (24, 10)}
            )
        if c == "reach_slateport":
            # The sail lands on Route109 (0,24); walk north into Slateport City.
            return (
                gs.badge_count >= 2
                and _letter_done(gs)
                and not gs.flag_devon_goods_delivered
                and cur in {(0, 24), (0, 1)}
            )
        if c == "deliver_devon_dock":
            # Devon Goods errand step 1: talk to Dock at Stern's Shipyard (5,5);
            # he redirects you to Capt.Stern (sets FLAG_DOCK_REJECTED 0x94).
            # delivered gate uses the _devon_delivered LATCH, not the raw flag:
            # 0x95 DMA-flickers False on ~12% of live frames, and each flicker
            # re-fired this goal post-delivery and yanked the agent SOUTH back to
            # the Shipyard (it could not make north progress out of Slateport).
            # The latch is written once 0x95 is genuinely True (measured stably
            # True post-delivery) and never re-opens the errand. dock_rejected
            # stays raw (a pre-latch flicker there only wobbles one frame).
            return (
                gs.badge_count >= 2
                and _letter_done(gs)
                and not _devon_delivered(gs)
                and not gs.flag_dock_rejected_devon
                and cur in {(0, 1), (9, 0), (0, 24)}
            )
        if c == "deliver_devon_goods":
            # Step 2: after the Dock redirect, deliver to Capt.Stern on Oceanic
            # Museum 2F (13,6). Sets FLAG_DELIVERED_DEVON_GOODS (0x95) + unblocks
            # Route110 to Mauville. The $50 entry dialog and the 2 Aqua-grunt
            # battles are handled by the heuristic's dialog / (double-)battle
            # handlers on the way to Stern.
            #
            # 0x94 (dock_rejected) DMA-flickers ~37% of frames (measured: 44 of
            # 118 museum frames read False). On the outbound leg we gate on the
            # RAW flag, where a flicker only wobbles direction for one frame and
            # self-corrects. But the Oceanic Museum 1F<->2F stairs is a single
            # tile warp: a None frame there hands nav to the explore heuristic,
            # which routes back onto the warp and ping-pongs the agent across
            # the floors before it can walk the ~10 tiles to Stern. Being ON a
            # museum map already PROVES the Dock was talked (you can only route
            # here via this goal with 0x94 genuinely True), so on the museum
            # maps we drop the 0x94 requirement and stay latched to Stern
            # regardless of the flicker. This cannot premature-trigger before
            # the Dock: a pre-Dock flicker-True never actually walks you into
            # the museum, so `cur in museum` is only reachable post-Dock.
            if cur in {(9, 7), (9, 8)}:
                return (
                    gs.badge_count >= 2
                    and _letter_done(gs)
                    and not _devon_delivered(gs)
                )
            return (
                gs.badge_count >= 2
                and _letter_done(gs)
                and gs.flag_dock_rejected_devon
                and not _devon_delivered(gs)
                and cur in {(0, 1), (9, 0), (0, 24)}
            )
        if c == "reach_mauville":
            # After delivering the Devon Goods, head north to Mauville for the
            # Dynamo Badge. Uses the _devon_delivered LATCH (not the raw 0x95):
            # reach_mauville (needs delivered) and deliver_devon_* (need NOT
            # delivered) are perfectly anti-correlated on the 0x95 flicker, so a
            # single flicker-False frame dropped reach_mauville AND re-fired the
            # delivery goals, yanking the agent south. The monotonic latch keeps
            # this stable north. Below badge 3 so the whole Mauville chain
            # retires once Wattson is beaten. Unrestricted cur: pulls the agent
            # out of the museum onto the Slateport->Route110->Mauville path.
            return (
                _devon_delivered(gs)
                and gs.badge_count < 3
            )
        if c == "heal_at_slateport":
            # Route110 is trainer-dense; a solo Grovyle wears down and whites out
            # (back to Dewford — see sail_to_slateport). Heal at the Slateport PC
            # (9,11) nurse (7,2) when the lead drops below half, from Slateport or
            # Route110. This both prevents the whiteout AND re-homes the whiteout
            # point to Slateport (mainland), breaking the Dewford strand loop.
            return (
                _devon_delivered(gs)
                and gs.badge_count < 3
                and gs.party0_hp_frac < 0.5
                # Include the PC map (9,11) itself: without it the goal drops the
                # instant the agent walks in, reach_mauville fires (no cur gate)
                # and routes it straight back out -> PC<->city bounce, never heals.
                and cur in {(0, 1), (0, 25), (9, 11)}
            )
        if c == "heal_at_mauville":
            # Mauville Gym has 6 trainers before Wattson and no in-gym healing, so
            # a solo Grovyle wears down (observed: reached Wattson at 2/98 HP).
            # When the lead drops below half at Mauville City or inside the Gym,
            # heal at the Mauville PC (10,5) nurse (7,2). Also re-homes the
            # whiteout point to Mauville so a gym faint is a short setback, not a
            # Dewford strand. Re-entering the gym resets the barrier puzzle, but
            # the live-collision + frontier-explore machinery re-solves it.
            return (
                _devon_delivered(gs)
                and gs.badge_count < 3
                and gs.party0_hp_frac < 0.5
                # Include the PC map (10,5): else the goal drops on entry and
                # reach_mauville routes the agent back out -> bounce, never heals.
                and cur in {(0, 2), (10, 0), (10, 5)}
            )
        if c == "mauville_gym_wattson":
            # At Mauville City or inside the Gym: route to / interact with
            # Wattson (5,2). Gated to those two maps so table-order selection
            # keeps it above reach_mauville only where it should win.
            return (
                _devon_delivered(gs)
                and gs.badge_count < 3
                and cur in {(0, 2), (10, 0)}
            )
        # --- Lavaridge / Flannery (Badge 4) arc — docs/PLAN_lavaridge_flannery ---
        # Segment 0: Rock Smash chain (Route111 north is gated by a BREAKABLE_ROCK).
        if c == "get_rock_smash":
            # Get HM06 from the Mauville House1 RockSmashDude (4,4). Retires once
            # received (FLAG_RECEIVED_HM_ROCK_SMASH 0x6B). Existing nav + interact
            # + dialog A-mash complete it; re-talk is idempotent (goto_if_set).
            return (
                gs.badge_count >= 3
                and not gs.flag_badge04_get
                and not gs.flag_rock_smash_hm
            )
        if c == "teach_rock_smash":
            # HM06 received but no party member knows Rock Smash yet -> teach it.
            # This is a bag/party UI SUB-TASK (target_map=None), driven by the VLM
            # in hm_teach via the claude_heuristic hook. Retires when a party mon
            # knows MOVE_ROCK_SMASH (249).
            return (
                gs.badge_count >= 3
                and not gs.flag_badge04_get
                and gs.flag_rock_smash_hm
                and not _rock_smash_taught(gs)
                and not gs.in_battle
            )
        if c == "smash_route111_rock":
            # Know Rock Smash and standing on Route111 with the rock still a live
            # object_event -> walk up and smash it (existing interact machinery;
            # the YES/NO defaults to YES so the dialog A-mash confirms). The rock
            # is FLAG_TEMP so it reappears on map reload -> re-fires + re-smashes
            # (cost 0). (19,100) is the east-lane rock; the tip-guy at (19,101)
            # vanished when HM06 was received (0x34B).
            if not (
                gs.knows_rock_smash
                and gs.badge_count >= 3
                and not gs.flag_badge04_get
                and cur == (0, 26)
            ):
                return False
            return any(
                (nx, ny) == (19, 100)
                for (nx, ny, _g) in getattr(gs, "npcs_on_map", []) or []
            )
        if c == "fiery_path_cross":
            # On Route112 SOUTH, the only way to Fallarbor is across Fiery Path
            # (the map graph can't route Route112->FieryPath->Route112, so
            # reach_fallarbor + hop-fallback ping-pongs Route111<->Route112).
            # Fire here to route to the Fiery Path warp; deactivates once we
            # cross into the north blob (reach_fallarbor then takes over via
            # Route111 north -> Route113). Same Badge4-arc gate as below.
            return (
                gs.badge_count >= 3
                and not gs.flag_badge04_get
                and not _theft_done(gs)
                and _in_route112_fiery_south(gs)
            )
        if c == "exit_fiery_path_north":
            # Inside Fiery Path: head to the NORTH warp pad regardless of which
            # side we entered from. fiery_path_cross only got us IN (its
            # condition needs Route112); once on the FieryPath map neither it nor
            # reach_fallarbor fire, so the agent wandered (goal=None, 1243 turns,
            # never reached the y4 exit). Deactivates the instant cur leaves
            # FieryPath -> Route112 north is picked up by reach_fallarbor.
            # Stateless + direction-agnostic = loop-safe. Same Badge4-arc gate.
            return (
                gs.badge_count >= 3
                and not gs.flag_badge04_get
                and not _theft_done(gs)
                and (gs.map_group, gs.map_num) == _FIERY_PATH
            )
        if c == "reach_fallarbor":
            # First leg of the Lavaridge arc: Mauville -> Route111 -> Route112
            # SOUTH -> [fiery_path_cross routes across Fiery Path] -> Route112
            # NORTH -> Route111 north -> Route113 -> Fallarbor. Route112 is one
            # map split by Mt.Chimney into two blobs joined only by Fiery Path;
            # the crossing is handled by the higher-priority fiery_path_cross
            # goal, not the map graph. Gated to the pre-Fallarbor maps so
            # meteor_falls_theft takes over once we're at/past Fallarbor.
            return (
                gs.badge_count >= 3
                and not gs.flag_badge04_get
                and not _theft_done(gs)
                # (10,0)/(10,5) = the Mauville gym / PC we exit after Wattson.
                and cur in {(0, 2), (10, 0), (10, 5), (0, 26), (0, 27), (0, 28)}
            )
        if c == "meteor_falls_theft":
            # Badge4 arc leg 2. Once at/past Fallarbor (reach_fallarbor's
            # cur-set no longer matches, so this takes over), Prof. Cozmo is at
            # Meteor Falls studying the meteorite; Team Magma takes it and
            # leaves. In Emerald this is a cutscene only (no battle here) that
            # sets FLAG_HIDE_ROUTE_112_TEAM_MAGMA (0x333), clearing the grunt
            # that blocks the Route112 cable car. Nav brings us into the event
            # zone (target_pos); the A-mash dialog brain plays the cutscene.
            # Retire is the flag flip, not reaching the tile. Ungated on cur so
            # it stays live across Fallarbor / Route114 / Meteor Falls' rooms.
            return (
                gs.badge_count >= 3
                and not gs.flag_badge04_get
                and not _theft_done(gs)
            )
        if c == "exit_fiery_path_south":
            # Post-theft mirror of exit_fiery_path_north. To reach the cable car
            # (Route112's SOUTH blob, cid12) from the north the region nav routes
            # us into Fiery Path via its north warp; once IN, walk to the SOUTH
            # warp pad and drop into cid12. Neither ride_cable_car (needs to not
            # be in Fiery) nor anything else routes the inner leg, so without this
            # the agent ping-pongs on the north pad (the exit_fiery_path_north
            # failure, mirrored). Direction chosen by _theft_done: pre-theft =
            # north (to Fallarbor), post-theft = south (to the cable car).
            return (
                gs.badge_count >= 3
                and not gs.flag_badge04_get
                and _theft_done(gs)
                and (gs.map_group, gs.map_num) == _FIERY_PATH
            )
        if c == "ride_cable_car":
            # Badge4 arc leg 3: board the Route112 cable car (attendant interact)
            # up to Mt.Chimney. Cur-NEGATIVE gated (not positive) + retires on
            # badge04 (not 0x8B): a post-0x8B whiteout that sends us back to
            # Mauville must re-board here, and a positive cur-set would go
            # goal-less inside the Fallarbor PC etc. Silent on the Mt.Chimney
            # side (24,12)/(19,1)/JaggedPass, in Lavaridge town (0,12) / its
            # indoor group 4, and in the SW pocket (can't reach the station on
            # foot) so we never re-target the station from a dead end.
            return (
                gs.badge_count >= 3
                and not gs.flag_badge04_get
                and _theft_done(gs)
                and cur not in {(24, 12), (24, 13), (19, 1), (0, 12)}
                and gs.map_group != 4
                and not _in_route112_jagged_pocket(gs)
            )
        if c == "mtchimney_defeat_magma":
            # Badge4 arc leg 4: at Mt.Chimney, beat Tabitha then Maxie (talk-
            # trigger at (13,6)); the win sets FLAG_DEFEATED_EVIL_TEAM_MT_CHIMNEY
            # (0x8B). Cur-ungated (like meteor_falls_theft); ride_cable_car sits
            # above it and wins on the approach side, and goes silent on the
            # Mt.Chimney maps so this wins there.
            return (
                gs.badge_count >= 3
                and not gs.flag_badge04_get
                and _theft_done(gs)
                and not _mtchimney_done(gs)
            )
        if c == "descend_jagged_pass":
            # Badge4 arc leg 5: descend Jagged Pass (foot path, ledges are all
            # walls in the static collision) to Route112's SW pocket, the only
            # on-foot approach to Lavaridge. Gated to the Mt.Chimney-side maps
            # so it doesn't fire once we're already down in the pocket / town.
            return (
                gs.badge_count >= 3
                and not gs.flag_badge04_get
                and _mtchimney_done(gs)
                and cur in {(24, 12), (19, 1), (24, 13)}
            )
        if c == "grind_pre_flannery":
            # H17 grind gate (mirror of grind_pre_brawly's H6b): below the
            # target level this goal owns navigation ON JAGGED PASS ONLY and
            # pins the lead on the bottom grass. ROM-verified 07-22: every
            # collision-"walkable" route UP the pass runs through
            # MB_BUMPY_SLOPE (0xD1) strips — Acro-Bike-only, on foot the
            # player just bumps (pokeemerald CheckAcroBikeCollision) — so
            # the grass is reachable on foot ONLY from the TOP entry
            # (Mt.Chimney warp: 23-25 step path, walk-simulated clean), and
            # NOT from the bottom warp pad / pocket / town. The old cur-set
            # (town/PC/pocket) sent the lead in through the bottom warp
            # toward that unreachable pin, and it bounced JaggedPass <->
            # pocket forever; those maps now belong to
            # grind_reboard_cable_car below, which drives the loop's road
            # leg (town -> pocket -> ledge-hop down -> cable car ->
            # Mt.Chimney -> descend onto the grass from above). Still listed
            # ABOVE descend_jagged_pass (also matching on JaggedPass) so the
            # under-level lead grinds instead of descending past the grass.
            # Sitting above the heal goal, it must yield on its own hp gate
            # (>= 0.5, the exact complement of heal_at_lavaridge's < 0.5): a
            # hurt lead falls through to descend (the walk home IS the PC
            # route) and then heal_at_lavaridge in the pocket/town, while a
            # hurt lead WITH potions is caught by field_heal_potion higher
            # up. Retires at the target level -> descend/reach/gym goals
            # resume Flannery. Wild Numel/Machop/Spoink L20-22 are harmless
            # to the L42+ lead. (If a stray warp drops an under-level lead
            # into the bottom funnel, the pin BFS returns None and wandering
            # re-steps the (14/15,40) pad into the pocket, where the reboard
            # goal picks the loop back up — self-recovering.)
            return (
                gs.badge_count >= 3
                and not gs.flag_badge04_get
                and _mtchimney_done(gs)
                and gs.party0_level < FLANNERY_GRIND_TARGET_LEVEL
                and gs.party0_hp_frac >= 0.5
                and cur == (24, 13)
            )
        if c == "grind_reboard_cable_car":
            # Road leg of the H17 grind loop: the JaggedPass grass can only
            # be ENTERED from the top (bumpy-slope walls, see
            # grind_pre_flannery above), so from the Lavaridge side the
            # under-level lead must re-board the cable car. Matches exactly
            # where ride_cable_car is deliberately silent — town (0,12), the
            # PC (4,5), inside the gym (4,1)/(4,2) (an under-level lead is
            # walked OUT toward the station, never onto the losing Flannery
            # fight), and the Route112 SW pocket — and mirrors ride's
            # station-attendant target. The pocket -> station leg IS
            # walkable: one-way DOWN the pocket ledges then up the south
            # blob (offline BFS 44-45 steps with ledge jumps; the "pocket
            # can't reach the station" note on _in_route112_jagged_pocket
            # is about climbing BACK — the descent is fine). Once out of
            # the pocket on the south blob, ride_cable_car (earlier in
            # GOAL_TABLE) takes over the same target seamlessly; at the
            # station the (6,6) attendant interact fires the ride, and on
            # Mt.Chimney descend_jagged_pass brings the lead down into the
            # pass where grind_pre_flannery pins the grass. Same hp/level
            # gates as the grind so field_heal_potion / heal_at_lavaridge
            # keep their wins on a hurt lead.
            return (
                gs.badge_count >= 3
                and not gs.flag_badge04_get
                and _mtchimney_done(gs)
                and gs.party0_level < FLANNERY_GRIND_TARGET_LEVEL
                and gs.party0_hp_frac >= 0.5
                and (
                    cur in {(0, 12), (4, 5), (4, 1), (4, 2)}
                    or _in_route112_jagged_pocket(gs)
                )
            )
        if c == "heal_at_lavaridge":
            # Flannery (Fire) beats a Grass lead, so whiteout is realistic. One
            # heal at the Lavaridge PC re-homes the whiteout point to Lavaridge,
            # so a loss re-tries the gym locally instead of the long Mauville ->
            # cable car -> Jagged loop (the Slateport/Mauville re-home strategy).
            # (0,27) = the Route112 pocket, the grind loop's walk home: a hurt
            # sub-target lead descends out of Jagged Pass (grind yields on hp,
            # descend walks it down) and lands here, and must route on into the
            # PC instead of going goal-less. South-blob (0,27) frames never
            # reach this: ride_cable_car sits above and wins outside the pocket.
            return (
                gs.badge_count >= 3
                and not gs.flag_badge04_get
                and _mtchimney_done(gs)
                and gs.party0_max_hp > 0
                and gs.party0_hp_frac < 0.5
                and not gs.in_battle
                and cur in {(0, 12), (0, 27), (4, 1), (4, 2), (4, 5)}
            )
        if c == "lavaridge_gym_flannery":
            # Badge4 arc leg 6: beat Flannery (13,9) for FLAG_BADGE04_GET (0x86A).
            # cur-set includes gym B1F (4,2): the hot-spring hole puzzle drops us
            # to B1F mid-fight and the goal must persist (target 1F != cur there,
            # so nav routes back up). Retire = raw badge04 (same as other gyms).
            return (
                gs.badge_count >= 3
                and not gs.flag_badge04_get
                and _mtchimney_done(gs)
                and cur in {(0, 12), (4, 1), (4, 2)}
            )
        if c == "reach_lavaridge":
            # Badge4 arc final leg: from the SW pocket, walk the left exit strip
            # into Lavaridge. Cur-ungated but placed BELOW the gym goal (the
            # mauville_gym_wattson-above-reach_mauville pattern) so inside the gym
            # the gym goal wins; only in the pocket / en route does this drive.
            return (
                gs.badge_count >= 3
                and not gs.flag_badge04_get
                and _mtchimney_done(gs)
            )
        if c == "buy_potions":
            # H14: before the Mt.Chimney Team Magma gauntlet (no PC, ~10-13 mons
            # back-to-back), stock Super Potions at the Mauville Mart. Fire when
            # heal items run low AND we can afford at least one (money -1 =
            # unreadable still fires; only a confirmed 0..699 wallet stops it,
            # since whiteout halves money and buying is the whole point). cur-set
            # is Mauville + Route111/112 so it can pull the agent back from the
            # arc approach, never from Fiery/Lavaridge. Retires when heal >= 6
            # (bought) or the wallet drops below one Super Potion.
            return (
                gs.badge_count >= 3
                and not gs.flag_badge04_get
                and _theft_done(gs)
                and not _mtchimney_done(gs)   # only pre-Magma; (0,27) is also the
                # post-Jagged SW pocket, and post-0x8B whiteout re-homes to
                # Lavaridge (heal_at_lavaridge), never Mauville -- so restocking
                # there is unreachable and firing at the pocket would yank the
                # agent all the way back to Mauville instead of into Lavaridge.
                and gs.bag_heal_qty < 6
                and not (0 <= gs.money < 700)
                and cur in {(0, 2), (10, 5), (10, 7), (0, 26), (0, 27)}
            )
        if c == "field_heal_potion":
            # H14: heal the lead with a Super Potion BETWEEN gauntlet trainers /
            # in the Lavaridge gym (no PC on Mt.Chimney). Fires only with a
            # restore in the bag (empty -> falls through to fight / PC heal, the
            # anti-loop guard), out of battle, below 65% (so a full-HP entry
            # survives Maxie's worst turn). target_map None = UI sub-task, like
            # teach_rock_smash. Placed above mtchimney_defeat_magma / the gym goal.
            return (
                gs.badge_count >= 3
                and not gs.flag_badge04_get
                and gs.party0_max_hp > 0
                # 0.50 (was 0.80 for the now-cleared Maxie boss): on the Jagged
                # Pass descent the lead takes constant chip damage, and firing at
                # <80% churned — a heal sub-task after every trainer that barely
                # dented it, each one pausing the descent. The Lavaridge PC
                # (heal_at_lavaridge) tops the lead to full before Flannery, so
                # here field_heal only needs to prevent a faint: fire on real dips.
                and gs.party0_hp_frac < 0.50
                and gs.bag_heal_qty > 0
                and not gs.in_battle
                and cur in {(24, 12), (24, 13), (4, 1), (4, 2)}
            )
        return False


GOAL_TABLE: list[Goal] = [
    Goal(
        name="get_starter_via_lab",
        target_map=(1, 4),
        condition="no_party",
        desc="Pokemon 0 匹 → Birch's lab (1-4) で starter 取得",
    ),
    Goal(
        name="return_to_lab_for_pokedex",
        target_map=(1, 4),
        condition="rival_defeated_no_pokedex",
        desc="Rival defeated → Birch lab (1-4) 戻りで auto-Pokedex give event 発動 → FLAG_ADVENTURE_STARTED set",
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
    # 37 fix (06-29): 35+ hour wrong strategy 訂正。
    # 旧 grind_route_104_north target=(2,11) は LOTAD grind 前提だったが
    # 33 fix evidence で agent species = Wingull Lv 21 (not Grovyle/Lotad)。
    # Wingull Water Gun (40 power, water type) 2x vs rock = Roxanne の
    # Geodude/Nosepass 一撃可能性高 = **grind 不要**。
    # 直接 Gym 突入が真の戦略。 goal 順序 = enter_rustboro_gym 先。
    Goal(
        name="enter_rustboro_gym",
        target_map=(11, 3),
        target_pos=(5, 2),  # Roxanne canonical position inside Gym
        condition="no_badge",
        desc="Rustboro Gym (11-3) Roxanne (5, 2) 直接接触 → Wingull Water Gun 撃破",
    ),
    Goal(
        name="grind_route_104_north",
        target_map=(0, 19),
        target_pos=(2, 11),
        condition="no_badge",
        desc="(deprecated 37 fix) Wingull Lv 21 で grind 不要だが fallback として保持",
    ),
    # --- Peeko rescue (unlocks Mr.Briney's sail to Dewford) ---
    # Reaching Rusturf Tunnel needs the beach (south Route104) -> Rustboro
    # (north) crossing, which goes through Petalburg Woods NORTHWARD. These
    # two goals run only until Peeko is rescued (Rusturf visited), then the
    # Dewford chain takes over southward.
    Goal(
        name="peeko_r104_to_woods",
        target_map=(24, 11),     # PetalburgWoods (enter from the south)
        condition="peeko_r104_south",
        desc="Peeko journey: Route104 南浜 → Petalburg Woods に入り北上",
    ),
    Goal(
        name="peeko_woods_north",
        target_map=(24, 11),
        target_pos=(14, 5),      # north exit warp -> Route104 north (Rustboro side)
        condition="peeko_in_woods",
        desc="Petalburg Woods を北上し北口 (14,5) から Route104 北へ",
    ),
    # Mr.Briney is hidden in his house (FLAG_HIDE_BRINEYS_HOUSE_MR_BRINEY)
    # until you rescue his Wingull "Peeko" from a Team Aqua grunt in Rusturf
    # Tunnel. Beat the grunt at (14,5) -> Peeko freed -> Briney goes home ->
    # sail available. Normal visited-map suppression retires this goal once
    # the agent has been to Rusturf and left, so the Dewford chain resumes.
    Goal(
        name="rescue_peeko",
        target_map=(24, 4),      # RusturfTunnel
        target_pos=(14, 5),      # Aqua Grunt (Peeko at 14,4) — battle to free
        condition="peeko_not_rescued",
        desc="Peeko 救出: Rusturf Tunnel の Aqua grunt (14,5) 撃退 -> Mr.Briney 帰宅 -> sail 解禁",
    ),
    Goal(
        name="peeko_return",
        target_map=(0, 19),      # Route104 north — start of the Dewford chain
        condition="peeko_return",
        desc="救出後の帰路: Rusturf/Route116/Rustboro -> Route104 北 (以降 dewford chain)",
    ),
    # --- Dewford journey (post Stone Badge) ---
    # Route104 north and the Mr.Briney beach (south) are the SAME map but not
    # walkably connected; the crossing is through Petalburg Woods (24,11).
    # Then Mr.Briney sails you across the water (Route105/106) to Dewford.
    Goal(
        name="dewford_to_woods",
        target_map=(24, 11),  # PetalburgWoods
        condition="dewford_route104_north",
        desc="Stone Badge 後: Route104 北 → Petalburg Woods (24-11) に入る",
    ),
    Goal(
        name="dewford_woods_south",
        target_map=(24, 11),
        target_pos=(16, 38),  # south exit warp -> Route104 south beach
        condition="dewford_in_woods",
        desc="Petalburg Woods を南下し南口 (16,38) から Route104 南浜へ",
    ),
    Goal(
        name="dewford_to_briney",
        target_map=(17, 0),  # Route104_MrBrineysHouse
        condition="dewford_route104_south",
        desc="Route104 南浜 → Mr.Briney's House (17-0) に入る",
    ),
    Goal(
        name="dewford_sail",
        target_map=(17, 0),
        target_pos=(5, 3),  # Mr.Briney NPC — talk to sail to Dewford
        condition="dewford_in_briney_house",
        desc="Mr.Briney (5,3) に話しかけて Dewford へ航海",
    ),
    # Grind loop for Brawly (H6b), placed BEFORE the gym goals so it wins
    # while the lead is under-levelled. heal_at_dewford_pc is first so a hurt
    # lead heals before returning to grind.
    Goal(
        name="heal_at_dewford_pc",
        target_map=(3, 1),          # DewfordTown_PokemonCenter_1F
        target_pos=(7, 3),          # counter below the nurse (map_data 3-1 nurse=(7,2));
        # interact machinery routes to (7,4) and presses Up+A, which the PC
        # counter metatile forwards to the nurse → full heal.
        condition="heal_low_hp_dewford",
        desc="低HP → Dewford PC の nurse で回復 (grind 継続用)",
    ),
    Goal(
        name="grind_granite_cave",
        target_map=(24, 7),         # GraniteCave_1F (bright: requires_flash=False)
        target_pos=(27, 7),         # open interior tile, 12 tiles from every warp/
        # ladder. Caves roll a wild encounter on EVERY floor step, so pinning the
        # lead here (interact-oscillation paces it on this tile) fights wild
        # Makuhita/Zubat/Geodude for XP. Route106 has NO land encounters (canon
        # wild_encounters.json is water/fishing only — verified grass_tiles=[]),
        # so the earlier Route106 pin never triggered a single wild battle.
        condition="grind_pre_brawly",
        desc="Grovyle < L30 → Granite Cave 1F (27,7) で野生戦 grind → ステータス優位で Brawly",
    ),
    Goal(
        name="reach_dewford_gym",
        target_map=(0, 11),
        condition="dewford_gym_approach",
        desc="Dewford Town (0-11) に到達 (以降 seek_brawly が Gym へ誘導)",
    ),
    Goal(
        name="dewford_gym_brawly",
        target_map=(3, 3),        # DewfordTownGym (pitch-black maze, door=Dewford (8,17))
        target_pos=(4, 3),        # Brawly (map_data/canon coords) — reaching triggers battle
        condition="seek_brawly",
        desc="Dewford Town/Gym → Brawly (4,3) に到達して Knuckle Badge 戦",
    ),
    # Post-Brawly (H4): deliver Steven's Letter — the hard gate for the
    # Slateport sail. Placed after the gym goals (badge>=2). heal_at_dewford_pc
    # (badge>=1, above) still wins first when the L30 lead is near-dead.
    Goal(
        name="deliver_steven_letter",
        target_map=(24, 10),      # GraniteCave_StevensRoom (bright; via 1F warp (5,10))
        target_pos=(7, 8),        # Steven's NPC tile — interact machinery routes to a
        # walkable neighbour and faces+A (same pattern as Brawly (4,3)). The room
        # entrance (7,3) is ABOVE Steven, so the agent arrives at (7,7) and must
        # talk facing Down; an earlier (7,9) below-Steven target was unreachable
        # (Steven blocks the path) and left the agent npc_avoid-ing him forever.
        condition="deliver_steven_letter",
        desc="Badge2後: Steven (GraniteCave StevensRoom) に Letter 配達 → TM47 + 渡し船 gate 解除",
    ),
    Goal(
        name="sail_to_slateport",
        target_map=(0, 11),       # DewfordTown
        target_pos=(12, 9),       # Mr.Briney NPC — talk, then pick Slateport (H4b)
        condition="sail_to_slateport",
        desc="Letter配達済 → Dewford の Briney (12,9) に話し Slateport 航海 (multichoice)",
    ),
    Goal(
        name="reach_slateport",
        target_map=(0, 1),        # SlateportCity (sail lands on Route109, walk north)
        condition="reach_slateport",
        desc="Route109 上陸 → 北上して Slateport City 到達",
    ),
    Goal(
        name="deliver_devon_dock",
        target_map=(9, 0),        # SlateportCity_SternsShipyard_1F
        target_pos=(5, 5),        # Dock NPC — talk (redirects to Capt.Stern)
        condition="deliver_devon_dock",
        desc="Slateport: Stern's Shipyard の Dock (5,5) に話す → Museum へ redirect",
    ),
    Goal(
        name="deliver_devon_goods",
        target_map=(9, 8),        # SlateportCity_OceanicMuseum_2F
        target_pos=(13, 6),       # Capt.Stern — deliver Devon Goods → Route110 unblock
        condition="deliver_devon_goods",
        desc="Slateport: Oceanic Museum 2F の Capt.Stern (13,6) に Devon Goods 配達",
    ),
    # heal at the Slateport PC before/along Route110 — listed before the Mauville
    # goals so a low-HP lead heals instead of marching into a whiteout.
    Goal(
        name="heal_at_slateport",
        target_map=(9, 11),       # SlateportCity_PokemonCenter_1F
        target_pos=(7, 3),        # PC counter in front of the Nurse (7,2): stand at
                                  # (7,4), face Up, A -> talk over the MB_COUNTER.
                                  # The nurse's own tile has NO walkable neighbor.
        condition="heal_at_slateport",
        desc="Route110 消耗時: Slateport PC (9,11) の Nurse (7,2) で全回復 → whiteout先をSlateportに固定",
    ),
    # --- Mauville: post-delivery north to the Dynamo Badge (Wattson) ---
    # Devon Goods delivered (0x95) also sets FLAG_HIDE_ROUTE_110_TEAM_AQUA
    # (0x384) in the SAME CaptStern script, so Route110 north is open. These
    # pull the agent OUT of the museum toward Mauville. mauville_gym_wattson
    # is listed BEFORE reach_mauville so that once at Mauville City / inside
    # the gym it wins the table-order selection (reach_mauville's target is
    # Mauville City, which would otherwise route the agent back out of the
    # gym).
    Goal(
        name="heal_at_mauville",
        target_map=(10, 5),       # MauvilleCity_PokemonCenter_1F
        target_pos=(7, 3),        # PC counter in front of the Nurse (7,2): stand at
                                  # (7,4), face Up, A -> talk over the MB_COUNTER.
        condition="heal_at_mauville",
        desc="Gym消耗時: Mauville PC (10,5) の Nurse (7,2) で全回復 → whiteout先をMauvilleに固定",
    ),
    Goal(
        name="mauville_gym_wattson",
        target_map=(10, 0),       # MauvilleCity_Gym
        target_pos=(5, 2),        # Wattson (Gym Leader) NPC tile — interact + face
        condition="mauville_gym_wattson",
        desc="Mauville Gym (10,0) の Wattson (5,2) 撃破 → Dynamo Badge (Badge 3)",
    ),
    Goal(
        name="reach_mauville",
        target_map=(0, 2),        # MauvilleCity (north via Route110 from Slateport)
        condition="reach_mauville",
        desc="Devon Goods 配達後: Route110 北上 → Mauville City 到達 (Wattson へ)",
    ),
    # --- Lavaridge / Flannery (Badge 4) arc — see docs/PLAN_lavaridge_flannery ---
    # Segment 0: Rock Smash chain (before reach_fallarbor; flag-serialized).
    Goal(
        name="get_rock_smash",
        target_map=(10, 2),       # MauvilleCity_House1
        target_pos=(4, 4),        # RockSmashDude — interact to receive HM06
        condition="get_rock_smash",
        desc="Route111 の岩用に Mauville House1 (4,4) で HM06 Rock Smash 受領",
    ),
    Goal(
        name="teach_rock_smash",
        target_map=None,          # UI sub-task, not a nav goal (hm_teach via VLM)
        condition="teach_rock_smash",
        desc="HM06 を Poochyena に教える (bag/party UI, VLM 委譲)",
    ),
    Goal(
        # H14: stock Super Potions at the Mauville Mart before the Mt.Chimney
        # gauntlet. target_pos (2,3) = the counter tile in front of the clerk
        # (1,3); the interact machinery stands the agent at (3,3) and faces Left
        # + A over the MB_COUNTER (nurse-counter geometry). The shop VLM sub-task
        # takes over on arrival.
        name="buy_potions",
        target_map=(10, 7),       # MauvilleCity_Mart
        target_pos=(2, 3),
        condition="buy_potions",
        desc="Mt.Chimney gauntlet 用に Mauville Mart で Super Potion 購入 (VLM shop)",
    ),
    Goal(
        name="smash_route111_rock",
        target_map=(0, 26),       # Route111
        target_pos=(19, 100),     # east-lane BREAKABLE_ROCK (approach from (19,101))
        condition="smash_route111_rock",
        desc="Route111 (19,100) の岩を Rock Smash で砕いて北へ",
    ),
    Goal(
        # Higher priority than reach_fallarbor: on Route112 south, cross Fiery
        # Path first (the map graph can't route the two-Route112 crossing).
        name="fiery_path_cross",
        target_map=(24, 14),      # FieryPath (region nav routes to the in-blob warp)
        condition="fiery_path_cross",
        desc="Route112 南で Fiery Path を横断して北 blob へ (Fallarbor 手前)",
    ),
    Goal(
        # Inner leg of the crossing: once IN Fiery Path, walk to the north warp
        # pad (min-y Fiery->Route112 warp, canon-walkable step-on pad) and warp
        # out into Route112's north blob.
        name="exit_fiery_path_north",
        target_map=(24, 14),      # FieryPath
        target_pos=(26, 4),       # north warp pad (step-on; BFS lands + warps)
        condition="exit_fiery_path_north",
        desc="Fiery Path 内: 北 warp (26,4) を踏んで Route112 北 blob へ抜ける",
    ),
    Goal(
        name="reach_fallarbor",
        target_map=(0, 13),       # FallarborTown (via Route111->112->[FieryPath]->113)
        condition="reach_fallarbor",
        desc="Badge4 arc leg1: Mauville→Route112(Fiery Path横断)→Route113→Fallarbor",
    ),
    Goal(
        # Badge4 arc leg2: enter Meteor Falls (Route114 warp (8,63)→ land ~
        # (27,18)) and walk WEST along the y=18 corridor. The theft is a single
        # coord_event at (14,18) (MagmaStealsMeteoriteScene, VAR_METEOR_FALLS_
        # STATE==0) that fires on STEP-ON, not on A. target_pos is (13,18), the
        # trigger's WEST neighbour: the interact machinery adds the target's
        # neighbours to the BFS set, so approaching from the east it stops on
        # (14,18) (the nearest set member) — a guaranteed step onto the trigger.
        # Targeting (14,18) itself would stop one tile EAST (15,18) and A-mash
        # without stepping on. Cutscene sets FLAG_HIDE_ROUTE_112_TEAM_MAGMA
        # (0x333), which retires the goal and clears the cable-car grunt.
        name="meteor_falls_theft",
        target_map=(24, 0),       # MeteorFalls1F1R
        target_pos=(13, 18),
        condition="meteor_falls_theft",
        desc="Badge4 arc leg2: Meteor Falls (14,18) coord_event で隕石強奪 (0x333 set)",
    ),
    Goal(
        # Inner leg of the southbound Fiery re-cross (post-theft): once IN Fiery
        # Path, walk to the SOUTH warp pad and drop into Route112's cid12 (cable
        # car blob). Mirror of exit_fiery_path_north.
        name="exit_fiery_path_south",
        target_map=(24, 14),      # FieryPath
        target_pos=(26, 36),      # south warp pad -> Route112 (11,36) cid12
        condition="exit_fiery_path_south",
        desc="Fiery Path 内(post-theft): 南 warp (26,36) で Route112 南 blob へ抜ける",
    ),
    Goal(
        name="ride_cable_car",
        target_map=(19, 0),       # Route112_CableCarStation
        target_pos=(6, 6),        # attendant NPC (interact -> YES -> ride)
        condition="ride_cable_car",
        desc="Badge4 arc leg3: Route112 cable car で Mt.Chimney へ",
    ),
    Goal(
        # H14: heal the lead between gauntlet trainers (Mt.Chimney has no PC).
        # target_map None = VLM UI sub-task (field_heal.py), like teach_rock_smash.
        # Placed above mtchimney_defeat_magma so a low-HP frame heals before the
        # next trainer, and above the Lavaridge gym goal so it heals in-place
        # rather than leaving to the PC (which resets the hot-spring puzzle).
        name="field_heal_potion",
        target_map=None,
        condition="field_heal_potion",
        desc="戦間に Super Potion で lead 回復 (Mt.Chimney gauntlet / Lavaridge gym)",
    ),
    Goal(
        name="mtchimney_defeat_magma",
        target_map=(24, 12),      # MtChimney
        target_pos=(13, 6),       # Maxie (Tabitha/grunts en route) -> 0x8B
        condition="mtchimney_defeat_magma",
        desc="Badge4 arc leg4: Mt.Chimney で Tabitha+Maxie 撃破 (0x8B set)",
    ),
    Goal(
        # H17 grind road leg (07-22): the JaggedPass grass is enterable on
        # foot only from the TOP (bumpy-slope walls, see the condition), so
        # the under-level loop re-boards the cable car. Mirrors
        # ride_cable_car's target so the station flow (attendant (6,6)
        # interact -> ride -> Mt.Chimney) is reused verbatim; ride itself is
        # deliberately silent in town/PC/gym/pocket, which is exactly this
        # goal's cur-set.
        name="grind_reboard_cable_car",
        target_map=(19, 0),       # Route112_CableCarStation
        target_pos=(6, 6),        # attendant NPC (interact -> YES -> ride)
        condition="grind_reboard_cable_car",
        desc="Sceptile < L48: Lavaridge 側 → cable car 再搭乗 → 上から Jagged Pass 草地へ",
    ),
    Goal(
        # H17 grind (mirror of grind_granite_cave), listed ABOVE descend so it
        # wins on JaggedPass while the lead is under-levelled. (19,32) is
        # canon MB_TALL_GRASS. NOTE (07-22): the earlier "same walkable
        # component as the bottom warp pair proves two-way walk
        # reachability" argument was contaminated — the raw-collision
        # component glues regions together across MB_BUMPY_SLOPE tiles
        # (collision 0 but Acro-Bike-only in game), so the grass is really
        # top-entry-only; grind_reboard_cable_car above drives the loop's
        # road leg. 3 of the pin's 4 neighbours ((19,31)/(18,32)/(20,32))
        # are grass too, so the pin-pacing keeps stepping on encounter tiles
        # (grass maps roll wilds per grass STEP, unlike cave floors which
        # roll on every step).
        name="grind_pre_flannery",
        target_map=(24, 13),      # JaggedPass
        target_pos=(19, 32),      # bottom grass patch (canon land_mons L20-22)
        condition="grind_pre_flannery",
        desc="Sceptile < L48 → Jagged Pass 草むら (19,32) で野生戦 grind → Flannery 再挑戦",
    ),
    Goal(
        name="descend_jagged_pass",
        target_map=(24, 13),      # JaggedPass
        target_pos=(14, 40),      # bottom warp pad -> Route112 SW pocket (6,46)
        condition="descend_jagged_pass",
        desc="Badge4 arc leg5: Jagged Pass 降下 → Route112 SW pocket",
    ),
    Goal(
        name="heal_at_lavaridge",
        target_map=(4, 5),        # LavaridgeTown_PokemonCenter_1F
        target_pos=(7, 3),        # nurse counter (stand (7,4), Up+A). Re-homes whiteout
        condition="heal_at_lavaridge",
        desc="Lavaridge PC で heal (whiteout 先を Lavaridge に固定、Flannery 敗北 loop 対策)",
    ),
    Goal(
        name="lavaridge_gym_flannery",
        target_map=(4, 1),        # LavaridgeTown_Gym_1F
        target_pos=(13, 9),       # Flannery -> FLAG_BADGE04_GET (0x86A)
        condition="lavaridge_gym_flannery",
        desc="Badge4 arc leg6: Lavaridge Gym Flannery 撃破 (Heat Badge)",
    ),
    Goal(
        name="reach_lavaridge",
        target_map=(0, 12),       # LavaridgeTown (from SW pocket left strip)
        condition="reach_lavaridge",
        desc="Badge4 arc: SW pocket → LavaridgeTown (gym approach)",
    ),
]


def current_goal(gs) -> Goal | None:
    """First matching goal whose target_map differs from agent's current
    map. Skipping already-reached goals lets the goal chain advance: if
    we're at Oldale and `reach_oldale` matches but its target is Oldale,
    fall through to the next match (e.g. `reach_route_103_rival`).

    Additionally, persistent visited-map tracking suppresses backtrack
    goals: once the agent has visited a map farther along the canonical
    progression than this goal's target, the earlier goal is skipped so
    the cascade doesn't drag the agent back east when they cross a
    boundary the wrong way.
    """
    cur = (getattr(gs, "map_group", -1), getattr(gs, "map_num", -1))
    if cur != (-1, -1) and cur != (0, 0):
        record_map_visit(*cur)
    visited = _load_visited_maps()
    fallback = None
    for g in GOAL_TABLE:
        if not g.matches(gs):
            continue
        if (
            g.target_map in visited
            and g.target_map != cur
            and g.name not in _GOAL_BYPASS_VISITED
        ):
            # already cleared this waypoint — don't backtrack.
            # Bypassed for completion-required goals (gyms): visiting
            # the gym map once doesn't satisfy the goal — only beating
            # the leader (badge condition) does.
            continue
        if g.target_map == cur:
            if g.target_pos is not None:
                return g
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
    # limit <= 0 must short-circuit: lines[-0:] == lines[0:] would return ALL notes.
    if limit <= 0:
        return []
    if not GOALS_FILE.exists():
        return []
    try:
        with GOALS_FILE.open("r", encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
        return [json.loads(l).get("note", "") for l in lines]
    except (OSError, json.JSONDecodeError):
        return []
