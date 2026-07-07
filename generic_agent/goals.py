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
    # re-targetable every heal/grind cycle until the lead reaches L26.
    "grind_granite_cave", "heal_at_dewford_pc",
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
            # LEVEL GATE (H6b): under L26 the grind goal owns navigation, so
            # this must NOT match — otherwise it routes the agent out of
            # Granite Cave back to Dewford every step and grinding never runs.
            return (
                gs.badge_count >= 1 and gs.badge_count < 2
                and gs.party0_level >= 26
                and cur != (3, 3)
            )
        if c == "seek_brawly":
            # Fires on Dewford Town (planner routes through the gym warp) and
            # inside the gym (walk to Brawly). Coords are map_data/canon, the
            # same frame as gs.x/gs.y — NOT the memory's stale "+7" values.
            # LEVEL GATE (H6b): only challenge Brawly once the lead is L26+.
            # Grovyle learns Leaf Blade (70 STAB) at L26 — below that its moves
            # cap at 40 power (neutral vs Fighting) and it loses to Brawly's
            # 3rd mon (Bulk Up Makuhita L19) by attrition. Under L26 the grind
            # goal below wins instead.
            return (
                gs.badge_count >= 1 and gs.badge_count < 2
                and gs.party0_level >= 26
                and cur in {(0, 11), (3, 3)}
            )
        if c == "heal_low_hp_dewford":
            # Lead hurt (<40% HP) in the Dewford grind loop → route to the
            # Dewford PC nurse so grinding continues instead of stalling once
            # the low-HP whiteout guard starts fleeing every wild battle.
            return (
                1 <= gs.badge_count < 2
                and gs.party0_max_hp > 0
                and gs.party0_hp_frac < 0.4
                and not gs.in_battle
                and cur in {(0, 11), (3, 1), (0, 21), (24, 7)}
            )
        if c == "grind_pre_brawly":
            # Lead below L26 (no Leaf Blade yet) → grind wild battles in
            # Granite Cave (reachable from Dewford via Route106) until strong
            # enough, then seek_brawly takes over.
            return (
                1 <= gs.badge_count < 2
                and gs.party0_level < 26
                and cur in {(0, 11), (3, 1), (0, 21), (24, 7)}
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
        target_map=(24, 7),         # GraniteCave_1F (wild: Zubat/Makuhita/Aron)
        condition="grind_pre_brawly",
        desc="Grovyle < L26 → Granite Cave で野生戦 grind → L26 で Leaf Blade → Brawly",
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
