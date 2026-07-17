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


LETTER_DONE_MARKER = config.MEMORY_DIR / "steven_letter_done.marker"
DEVON_DELIVERED_MARKER = config.MEMORY_DIR / "devon_delivered.marker"


def _latched(gs, attr: str, marker) -> bool:
    """Monotonic story-flag latch, immune to the SaveBlock1 DMA flicker. Once
    the flag has been observed True, a disk marker keeps it True forever — the
    flag reading False for a single frame otherwise flips the goal (e.g. the
    Steven-letter flag flicker flipped the goal between the Dewford sail and the
    deep-cave letter, churning the agent in place). Mirrors _peeko_done."""
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
                and not gs.knows_rock_smash
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
                and not gs.flag_route112_magma_cleared
                and _in_route112_fiery_south(gs)
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
                and not gs.flag_route112_magma_cleared
                # (10,0)/(10,5) = the Mauville gym / PC we exit after Wattson.
                and cur in {(0, 2), (10, 0), (10, 5), (0, 26), (0, 27), (0, 28)}
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
        name="reach_fallarbor",
        target_map=(0, 13),       # FallarborTown (via Route111->112->[FieryPath]->113)
        condition="reach_fallarbor",
        desc="Badge4 arc leg1: Mauville→Route112(Fiery Path横断)→Route113→Fallarbor",
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
