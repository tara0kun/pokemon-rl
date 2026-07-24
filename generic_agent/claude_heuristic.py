"""Phase 3+ alternative: autonomous heuristic agent that encodes the
in-conversation Claude's Pokemon Emerald strategy as Python code.

The repeated regression v40-v53 traced back to one root cause: the only
expert demonstrators available (API Sonnet/Opus) were themselves stuck
on Route 101 due to path_memory noise + over-defensive prompt rules,
so every CNN trained by behavior cloning inherited those stuck
patterns. The user's clarification — Claude (this conversation) is
allowed to act as the demonstrator within plan scope, just not the
paid API — opens a different path: I write my own playing strategy
directly in Python.

This agent prioritises:
- avoid blocked directions (hard rule, never press into a wall)
- prefer un-tried directions over re-pressing the same one
- when stalled, rotate through unblocked directions
- bias the rotation toward Up first, then perpendicular, then back
  (Pokemon Emerald early-game progress is overwhelmingly northbound:
  Littleroot -> Route 101 -> Oldale -> Route 102 -> Petalburg, etc.)
- in battle: trainer = A spam; wild = run-sequence cycle
- in dialog (same hash, same pos): A
- escape on local cycles: when 8+ unique pos in 20-turn window OR
  same pos 8+ turns, force a perpendicular flip

It runs independently of API Brain. Per-turn records are written to
the same dataset/demonstrations.jsonl format so the existing
train_imitation.py picks them up unchanged.

Run:
    poke-rl/Scripts/python.exe -m generic_agent.claude_heuristic \\
        --turns 3000 --dataset
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import deque
from pathlib import Path

from . import (
    battle_heal as battle_heal_mod,
    battle_moves as battle_moves_mod,
    config,
    curriculum as curr_mod,
    field_heal,
    goals as goals_mod,
    hm_teach,
    shop as shop_mod,
    knn_explorer as knn_mod,
    llm_advisor as llm_mod,
    map_data as map_data_mod,
    map_knowledge as mk_mod,
    memory,
    path_memory as path_memory_mod,
    preprocess,
    reward_state as reward_state_mod,
    screen_features as sf_mod,
    state as state_mod,
    vlm_screen,
    tile_map as tile_map_mod,
)
from .io import EmulatorError, MGBAClient

DIRECTIONS = ("Up", "Right", "Down", "Left")
NORTH_BIAS_ORDER = ("Up", "Right", "Left", "Down")
SOUTH_BIAS_ORDER = ("Down", "Left", "Right", "Up")  # indoor maps: exit is south
INDOOR_TILE_THRESHOLD = 30  # heuristic for "indoor" maps
# Self-correcting RUN sequence. The old cycle ("A","A",Down,Right,"A"...)
# had no B: one phase desync (a dialog eating the Down) landed the A's on
# POKEMON and the agent then navigated the in-battle party list forever
# (observed 40+ turns at Route116 (30,13)). Leading B backs out of any
# accidentally-opened submenu (party/bag/summary) and also advances battle
# text, so wherever the cycle lands it re-converges on the RUN corner.
RUN_CYCLE = ("B", "A", "Down", "Right", "A", "A")
# Send-out navigation for after the lead faints (trainer battle). A clears
# the faint dialogue -> party list; B backs out of a "Do what"/SUMMARY
# submenu; Down moves off the fainted slot-0 to a healthy member; A opens
# "Do what" (cursor on SEND OUT); A confirms. Used ONLY when the active
# battler's HP is 0 — never at the FIGHT menu (that thrash was H6a).
SEND_OUT_SEQ = ("A", "B", "Down", "A", "A")
# Double-battle drive cycle. The command menu defaults its cursor to FIGHT at the
# START of every turn, and both mons' moves are chosen BEFORE any executes, so
# during selection every target is still alive: 6 plain A's take FIGHT->move->
# (default, live) target for BOTH mons and commit the turn (verified live — a
# Jagged Pass L41-vs-L21 double fell to pure A-mash). The earlier cycle that
# threaded in "Down"/"Left" to reach the 2nd mon / re-pick a target instead
# DRIFTED the command cursor off FIGHT onto POKEMON, where A opens the switch
# menu and the turn never commits — a Lavaridge B1F double froze 268 turns at the
# command menu on exactly that (07-19). The trailing B advances victory/faint
# text (or is a no-op at a command menu — B cannot flee a trainer battle). A mon
# fainting is handled separately: double_battle_needs_send_out() -> SEND_OUT_SEQ
# fires BEFORE this, so this cycle only ever runs the no-faint case.
DOUBLE_BATTLE_SEQ = ("A", "A", "A", "A", "A", "A", "B")
# Flee a wild battle: B,B backs out of any submenu, Up,Up,Left resets the cursor
# to FIGHT (top-left), then Right,Down -> RUN (bottom-right), A selects it. Used
# to leave any wild battle we don't want to fight (traversal, or no damaging
# move left). A wild battle can always be run from.
FLEE_SEQ = ("B", "B", "Up", "Up", "Left", "Right", "Down", "A")
# src prefixes whose button came from GOAL-DIRECTED navigation (a BFS path
# step, or a scripted interaction). run()'s post-processing EXPLORE overrides
# (anomaly_escape, forward_force) must never clobber these: the planner is
# already steering, and "helpfully" pressing a different direction breaks the
# plan exactly where it matters. ONE definition, shared by both gates.
GOAL_DIRECTED_SRC_PREFIXES = ("mapbfs", "rival_seek", "rival_talk", "goal_")

_UI_ESCAPE_CYCLE = ("B", "B", "B", "A")


def ui_escape_button(unknown_ui_streak: int) -> str | None:
    """Escape an unknown-UI screen the loop has no handler for — PokeNav
    (cb2 0x081C7401), Trainer Card, the level-up "forget move?" prompt. Returns
    a button after 3 CONSECUTIVE unknown-UI frames (so a warp-fade transient
    passes through), else None.

    B x3 backs out of any nested menu; the trailing A answers a "give up? /
    stop learning?" YES prompt (B alone loops those). This DETERMINISTICALLY
    declines level-up move learning — safe here (it protects Rock Tomb from an
    A-mash overwrite, and is no worse than today's random A-mash outcome). Root
    fix for the 2026-07-22 ~3700-turn PokeNav imprisonment: with the cb2 battle
    whitelist, PokeNav no longer reads as in_battle, and this backs out of it."""
    if unknown_ui_streak < 3:
        return None
    return _UI_ESCAPE_CYCLE[unknown_ui_streak % len(_UI_ESCAPE_CYCLE)]


def forward_force_override(
    entry_dir: str | None,
    in_force_window: bool,
    button: str,
    src: str,
    blocked_now: set[str],
    turn: int,
) -> str | None:
    """Post-map-entry "keep walking forward" EXPLORE heuristic of run().

    For ~30 turns after a map change, keep pressing the direction the agent
    entered with (entry_dir) so a fresh map is actually walked into instead
    of dithered on the edge; when entry_dir is empirically blocked at this
    tile and the planner wants to walk straight back out (opp), sidestep
    perpendicular. Returns the button to press INSTEAD of `button`, or None
    to leave the decision alone.

    It must never override a goal-directed button (GOAL_DIRECTED_SRC_
    PREFIXES — same gate anomaly_escape already respects). Jagged Pass grind
    (2026-07-22, offline-replayed): every grind cycle re-enters the pass
    from Mt.Chimney with entry_dir="Down", and this override rewrote the
    BFS's Right/Left turn toward the grass into "Down" for 30 turns. On a
    wall that self-heals (the bump records the dir into tile_map's blocked
    and the force yields next turn), but the pass's center column is a chain
    of one-way JUMP_SOUTH ledges — a forced Down VAULTS them without ever
    bumping, so the agent flew past both grass branch rows (y=29/31), the
    re-plan found no path back (one-way), fell to goal_map_explore and
    bottomed out on the Route112 warp pad: pocket -> cable car -> repeat,
    zero grind. The walk model and the BFS were correct all along; only
    this override broke the follow-through.
    """
    if (
        entry_dir is None
        or not in_force_window
        or button not in DIRECTIONS
        or src.startswith(GOAL_DIRECTED_SRC_PREFIXES)
    ):
        return None
    opp = {
        "Up": "Down", "Down": "Up",
        "Left": "Right", "Right": "Left",
    }[entry_dir]
    perp_map = {
        "Up": ("Right", "Left"),
        "Down": ("Left", "Right"),
        "Left": ("Up", "Down"),
        "Right": ("Down", "Up"),
    }[entry_dir]
    if entry_dir not in blocked_now and button != entry_dir:
        return entry_dir
    if button == opp:
        perp_options = [d for d in perp_map if d not in blocked_now]
        if perp_options:
            return perp_options[turn % len(perp_options)]
    return None


def take_screenshot(client: MGBAClient, session_id: str, turn: int) -> Path:
    sess_dir = config.DATASET_DIR / "screens" / session_id
    sess_dir.mkdir(parents=True, exist_ok=True)
    p = sess_dir / f"t{turn:05d}.png"
    client.screenshot(p)
    time.sleep(0.15)
    return p


def _toward(cx: int, cy: int, tx: int, ty: int) -> str:
    dx = tx - cx
    dy = ty - cy
    if abs(dy) > abs(dx):
        return "Down" if dy > 0 else "Up"
    if dx != 0:
        return "Right" if dx > 0 else "Left"
    return "Up"


def heuristic_button(
    gs,
    tm: tile_map_mod.TileMap,
    pm: path_memory_mod.TransitionMemory,
    map_visit_counts: dict[tuple[int, int], int],
    same_pos_streak: int,
    same_hash_streak: int,
    same_map_streak: int,
    last_pos: tuple[int, int] | None,
    last_action: str,
    recent_pos: list[tuple[int, int, int, int]],
    battle_turn: int,
    escape_dir_index: int,
    reward_state: reward_state_mod.RewardState | None = None,
    screen_signals: dict | None = None,
    current_goal: goals_mod.Goal | None = None,
    client: MGBAClient | None = None,
    ram_battle_recent: bool = True,
) -> tuple[str, str]:
    if reward_state is None:
        reward_state = reward_state_mod.RewardState()
    if screen_signals is None:
        screen_signals = {}
    # in_battle ground truth: gs.in_battle comes from the verified US
    # gBattleTypeFlags (0x02022FEC), which reads non-zero for the ENTIRE battle
    # (including the move-select screen) and clears to 0 in the overworld
    # (live-verified). We ALSO latch on the battle_menu vision signal so a one-
    # frame RAM miss can't drop us to overworld nav mid-battle — BUT only when
    # a real battle was confirmed in RAM within the last ~12 turns
    # (ram_battle_recent). Without that gate the vision detector false-positived
    # the Mauville Gym's yellow-checkered floor + electric barriers as a battle
    # menu (RAM in_battle=0, 10/10) and froze the agent at the FIGHT-cursor
    # handler for 3000+ turns, unable to navigate the puzzle. Do NOT OR dialog/
    # menu — those also appear in the overworld.
    _in_battle_real = bool(gs.in_battle) or (
        bool(screen_signals.get("battle_menu")) and ram_battle_recent
    )
    try:
        object.__setattr__(gs, "in_battle", _in_battle_real)
    except Exception:
        pass
    # gs.in_battle is now _in_battle_real, which already discounts a lone vision
    # battle_menu when no RAM battle was seen recently — so this gate skips the
    # gym-floor false positive and lets nav run, while a real battle (RAM-backed)
    # still enters here.
    if screen_signals.get("battle_menu") and gs.in_battle:
        # Indoor maps (museum, gyms) have no wild encounters -> any battle is a
        # scripted TRAINER battle. is_trainer_battle can false-negative here (the
        # battle-flags RAM word reads 0 on the move-select screen), so the RUN
        # guards below must also refuse to flee on indoor maps (RUN loops "No
        # running from a TRAINER battle!"; Wattson would never resolve).
        indoor_battle = False
        try:
            indoor_battle = map_data_mod.get_cache().is_indoor(
                gs.map_group, gs.map_num,
            )
        except Exception:
            indoor_battle = False
        # Low-HP safety guard: in a WILD battle at critical HP (<=25%),
        # another FIGHT or catch turn risks a whiteout (whole party faints
        # -> forced warp to the last Center + money loss), which throws away
        # far more progress than fleeing. Pre-empt both catch and the FIGHT
        # cursor-reset below: route to RUN (bottom-right of the 2x2 command
        # menu = Right+Down+A), reusing the same proven sequence as the
        # over-leveling run guard. Trainer battles are excluded - you cannot
        # run from a trainer, and forcing RUN there loops the "No running
        # from a TRAINER battle!" dialog forever. (Pokemon Center routing is
        # intentionally out of scope here; this only prevents the whiteout.)
        if not gs.is_trainer_battle and not indoor_battle and gs.party0_critical:
            run_seq = ("Right", "Down", "A", "A")
            return run_seq[battle_turn % len(run_seq)], (
                f"wild_run_lowhp@hp{gs.party0_hp}/{gs.party0_max_hp}"
            )
        # Pre-empt: when we have balls AND a mono party AND aren't a
        # trainer battle (you can't catch trainers' Pokemon), try to throw.
        # gs.in_battle is unreliable (RAM false negative), but battle_menu
        # detected via vision means we ARE in battle. Trigger catch on
        # first detect — caller manages a state machine to follow through
        # the bag-select sequence even after battle_menu visibility flips.
        if (
            gs.bag_pokeball_count > 0
            and gs.party_count <= 2
            and gs.party0_hp_frac >= 0.3
            and not gs.is_trainer_battle
            and not indoor_battle
        ):
            return "Right", "wild_catch_try_screen:init"
        # Over-leveling guard: when starter is well above expected wild
        # encounter level AND we lack balls / a 2nd party member, attacking
        # just burns XP without progress. Pick RUN (bottom-right in the
        # 2x2 battle menu: Right+Down+A) instead of mashing A on FIGHT.
        # GUARD: only when wild battle (trainer battles can't be run from -
        # repeatedly trying RUN here would loop the "No running from a
        # TRAINER battle!" dialog forever).
        if (
            not gs.is_trainer_battle
            and not indoor_battle
            and gs.party0_level >= 14
            and gs.party_count == 1
            and gs.bag_pokeball_count == 0
        ):
            run_seq = ("Right", "Down", "A", "A")
            return run_seq[battle_turn % len(run_seq)], (
                f"wild_run_overleveled@lv{gs.party0_level}"
            )
        # Default: reset cursor to FIGHT then A. Pressing A blindly may
        # confirm whatever the cursor sits on (RUN/POKEMON/BAG from the
        # previous turn), so first nudge Up+Up+Left to definitively land
        # on FIGHT (top-left), then A → FIGHT submenu, then A → first
        # move. Empirically verified during Roxanne battle 06-24: simply
        # Left+Up wasn't enough — cursor on POKEMON (bottom-left) needs
        # Up x2 to escape to FIGHT row, then Left to ensure x=0 column.
        cursor_reset_seq = (
            "Up", "Up", "Left", "A", "A", "A"
        )
        return cursor_reset_seq[battle_turn % len(cursor_reset_seq)], (
            "battle_menu_visible:fight_cursor_reset"
        )
    if screen_signals.get("dialog") and not gs.in_battle:
        return "A", "dialog_visible:A"
    if screen_signals.get("menu") and not gs.in_battle:
        return "B", "menu_visible:B"
    if (
        screen_signals.get("front_blocked")
        and not gs.in_battle
        and last_action in DIRECTIONS
        and same_pos_streak >= 3
    ):
        avoid = last_action
        order_alt = [
            d for d in NORTH_BIAS_ORDER if d != avoid
        ]
        return order_alt[0], f"front_blocked_pivot:{order_alt[0]}"
    cur_map = (gs.map_group, gs.map_num)
    rival_goal_targets_here = any(
        g.matches(gs)
        and g.target_map == cur_map
        and "rival" in g.name.lower()
        for g in goals_mod.GOAL_TABLE
    )
    if (
        same_map_streak >= 100
        and not gs.in_battle
        and gs.saveblock1_valid
        and rival_goal_targets_here
    ):
        try:
            mc_w = map_data_mod.get_cache()
            rival_xy = mc_w.find_npc_by_script_keyword(
                gs.map_group, gs.map_num, "rival",
            )
        except (OSError, RuntimeError):
            rival_xy = None
            mc_w = None
        if rival_xy is not None and mc_w is not None:
            rx, ry = rival_xy
            adj = {(rx-1, ry), (rx+1, ry), (rx, ry-1), (rx, ry+1)}
            adj = {t for t in adj if mc_w.get(gs.map_group, gs.map_num).walkable(*t)}
            if (gs.x, gs.y) in adj:
                d = _toward(gs.x, gs.y, rx, ry)
                return "A", f"rival_talk:A@{rx},{ry}"
            path = mc_w.bfs_to_tile(
                gs.map_group, gs.map_num, (gs.x, gs.y), adj,
            )
            if path:
                rs_btn = path[0]
                if same_pos_streak >= 30:
                    rotor = ["Up", "Right", "Down", "Left"]
                    deltas = {"Up": (0, -1), "Right": (1, 0),
                              "Down": (0, 1), "Left": (-1, 0)}
                    cur_info_w = mc_w.get(gs.map_group, gs.map_num)
                    base = rotor.index(rs_btn) if rs_btn in rotor else 0
                    candidates: list[str] = []
                    for k in range(4):
                        d = rotor[(base + k) % 4]
                        if d == last_action and same_pos_streak >= 10:
                            continue
                        dx, dy = deltas[d]
                        nx, ny = gs.x + dx, gs.y + dy
                        if cur_info_w is not None and cur_info_w.walkable(nx, ny):
                            candidates.append(d)
                    if candidates:
                        choice = candidates[(same_pos_streak // 10) % len(candidates)]
                    else:
                        choice = rotor[(base + 1 + (same_pos_streak // 10)) % 4]
                    return choice, (
                        f"rival_seek_pivot:{choice}->{rx},{ry}"
                        f"@streak={same_pos_streak}"
                    )
                return rs_btn, f"rival_seek:{rs_btn}->{rx},{ry}(d={len(path)})"
    if (
        8 <= same_pos_streak < 30
        and last_action in DIRECTIONS
        and not gs.in_battle
        and gs.saveblock1_valid
    ):
        cycle = ("A", "A", "A", "B")
        return cycle[same_pos_streak % len(cycle)], (
            f"hidden_battle_probe:{cycle[same_pos_streak % len(cycle)]}"
            f"@streak={same_pos_streak}"
        )
    explore_target: tuple[int, int] | None = None
    # 32 fix (06-29): target_pos goal active 時は explore_target hijack 抑制。
    # explore_target が別 map を target に設定すると effective_goal_map が
    # override され、 mapbfs が target_pos でなく exit_tiles を target に使う
    # = 50+ hour autonomous で agent が grass tile に到達しない真因。
    same_map_with_target_pos = (
        current_goal is not None
        and getattr(current_goal, "target_pos", None) is not None
        and current_goal.target_map == (gs.map_group, gs.map_num)
    )
    # Directed story-journey goals (e.g. the Dewford chain) must not be
    # hijacked by explore_target: Route104 accumulates a huge same_map_streak
    # (its north/south split kept the agent there for thousands of turns), so
    # without this the hijack fires and overrides the now-routable woods path.
    directed_goal = (
        current_goal is not None
        and current_goal.name.startswith(
            ("dewford", "peeko", "rescue_peeko", "reach",
             "grind", "heal", "deliver", "sail", "mauville",
             "get_rock_smash", "smash_",
             # Badge4 (Lavaridge) arc. Without these the same_map_streak>=200
             # explore hijack overrides the goal with a nearer unexplored map:
             # meteor_falls_theft (target MeteorFalls, a WARP not a connection)
             # got yanked to FallarborTown (dist 65 < 68) on Route114 and the
             # agent oscillated (19,56)<->(19,57), net-zero progress. reach_*
             # goals were already directed (that's why reach_fallarbor worked);
             # these have no target_pos on the approach map so only directed_
             # goal protects them. "fiery_path"/"exit_fiery" retrofit the same
             # guard for the southbound Fiery re-cross to the cable car.
             "meteor_falls", "fiery_path", "exit_fiery",
             "ride_", "mtchimney", "descend_", "lavaridge_")
        )
    )
    # H4b: Mr.Briney's Dewford->Slateport sail multichoice (Petalburg=case 0 /
    # Slateport=case 1). The interact face+A opens Briney's dialog and the
    # greeting advances by A, but the choice box that follows would confirm the
    # default top option (Petalburg) on a plain A and sail us backwards. When the
    # sail goal is active on Dewford and a SELECTION menu is on screen, move the
    # cursor down to Slateport, then confirm. Stateless via last_action: press
    # Down first, and A only once the cursor has already moved down — so A always
    # lands on Slateport (case 1), never the Petalburg default.
    if (
        current_goal is not None
        and current_goal.name == "sail_to_slateport"
        and (gs.map_group, gs.map_num) == (0, 11)
        and not gs.in_battle
        and screen_signals.get("menu")
    ):
        if last_action == "Down":
            return "A", "briney_sail:confirm_slateport"
        return "Down", "briney_sail:cursor_to_slateport"
    if (
        same_map_streak >= 200
        and not gs.in_battle
        and gs.saveblock1_valid
        and not same_map_with_target_pos
        and not directed_goal
    ):
        recent = reward_state.last_visited_maps[-6:]
        nm = pm.find_nearest_unexplored_map(
            gs.map_group, gs.map_num, recent, max_hops=6,
        )
        if nm is not None:
            explore_target = nm[0]
    effective_goal_map = (
        explore_target
        if explore_target is not None
        else (current_goal.target_map if current_goal else None)
    )
    if (
        effective_goal_map is not None
        and not gs.in_battle
        and gs.saveblock1_valid
        and (
            (gs.map_group, gs.map_num) != effective_goal_map
            or same_map_with_target_pos
        )
    ):
        try:
            mc = map_data_mod.get_cache()
            cur_info = mc.get(gs.map_group, gs.map_num)
        except (OSError, RuntimeError):
            cur_info = None
            mc = None
        if cur_info and mc is not None:
            # Part B (hop-fallback): map_path returns the SHORTEST map chain,
            # which is often a first hop we can't actually take -- a
            # connection-lie (an all-wall edge -> no exit tiles, e.g.
            # Route112->Route113 up) or an edge that is physically sealed from
            # here (Route111 south -> the Route113 strip is behind the
            # sandstorm triggers). The BFS below then finds no path and the
            # agent wanders (the 5595-turn Route112 stall). So first BAN the
            # dead first hops and let map_path re-route (Route111 -> Route112 ->
            # Route113 -> Fallarbor). The probe BFS blocks only permanent walls
            # + sandstorm triggers -- the most permissive reachability, so it
            # bans a hop only when it is GENUINELY sealed, never a merely
            # water-/npc-crossable one (those still resolve in the main BFS
            # fallbacks). Interact-on-target-map goals aim at a tile, not a map
            # boundary, so they skip this.
            banned_hops: set[str] = set()
            _is_interact_here = (
                current_goal is not None
                and getattr(current_goal, "target_pos", None) is not None
                and (gs.map_group, gs.map_num) == current_goal.target_map
            )
            if not _is_interact_here and cur_info.walkable(gs.x, gs.y):
                try:
                    _mkp = mk_mod.get_store().get(gs.map_group, gs.map_num)
                    _seal = (
                        mc.permanent_blocked(gs.map_group, gs.map_num)
                        | getattr(_mkp, "blocked_triggers", set())
                    )
                except Exception:
                    _seal = set()
                # Canon ledge jumps for the probe BFS. The probe promises
                # "the most permissive reachability", but without ledges it
                # under-counted: components whose only exit is a one-way
                # jump (Lavaridge 1F Flannery room -> exit area, B1F side
                # rooms -> geyser room) probed as sealed and the hop was
                # wrongly banned. Derived from behavior_grid (the same canon
                # source map_knowledge seeds ledge_jumps from) because
                # knowledge_ledges is loaded further below.
                try:
                    _probe_ledges = {
                        (lx, ly): map_data_mod.LEDGE_JUMP_BEHAVIORS[bv]
                        for (lx, ly), bv in mc.behavior_grid(
                            gs.map_group, gs.map_num,
                        ).items()
                        if bv in map_data_mod.LEDGE_JUMP_BEHAVIORS
                    }
                except Exception:
                    _probe_ledges = {}
                for _hop_try in range(3):
                    _pchain = mc.map_path(
                        gs.map_group, gs.map_num,
                        effective_goal_map[0], effective_goal_map[1],
                        max_hops=8, banned_first_hops=banned_hops,
                    )
                    if not _pchain or _pchain[0] == effective_goal_map:
                        break  # no path, or first hop IS the goal map (nothing to ban)
                    _hop = mc.name_for(*_pchain[0])
                    if not _hop:
                        break
                    _pt: set[tuple[int, int]] = set()
                    for _d, _cs in cur_info.connections.items():
                        if any(c["map_name"] == _hop for c in _cs):
                            _pt |= mc.exit_tiles_toward(
                                gs.map_group, gs.map_num, _d, dest_name=_hop,
                            )
                    if not _pt:
                        _pt |= mc.warp_tiles_for(
                            gs.map_group, gs.map_num, _hop,
                        )
                    if not _pt:
                        banned_hops.add(_hop)  # (i) connection-lie: no exit
                        continue
                    if mc.bfs_to_tile(
                        gs.map_group, gs.map_num, (gs.x, gs.y), _pt,
                        blocked_tiles=_seal - _pt,
                        ledge_jumps=_probe_ledges,
                    ) is None:
                        # (iii) region rescue: on a multi-component warp-maze
                        # map the hop's own warp tiles are often in a walk-
                        # unreachable component, but the hop map IS reachable
                        # by riding same-map warp pairs (Lavaridge gym: the
                        # town-exit warps sit in comp5 while the agent stands
                        # in comp3 — ride hole (0,17) -> B1F -> geyser (8,9)
                        # -> comp5). Banning the hop here collapsed map_path
                        # to None and dropped nav onto polluted path-memory
                        # chains (the 800-turn (5-6,17-18) oscillation,
                        # 2026-07-21). Keep the hop when the region router
                        # has a warp-graph route whose first-hop tile is
                        # walk-reachable; the region routing below then
                        # drives the actual ride.
                        if mc.has_multiple_warp_components(
                            gs.map_group, gs.map_num
                        ):
                            _rt, _ = mc.region_route_targets(
                                gs.map_group, gs.map_num, (gs.x, gs.y),
                                _pchain[0], None,
                            )
                            if _rt and mc.bfs_to_tile(
                                gs.map_group, gs.map_num, (gs.x, gs.y),
                                _rt, blocked_tiles=_seal - _rt,
                                ledge_jumps=_probe_ledges,
                            ) is not None:
                                break  # hop rideable via same-map warps
                        banned_hops.add(_hop)  # (ii) sealed from here
                        continue
                    break  # reachable first hop found
            mh_chain = mc.map_path(
                gs.map_group, gs.map_num,
                effective_goal_map[0], effective_goal_map[1],
                max_hops=8, banned_first_hops=banned_hops,
            )
            if mh_chain is None:
                mh_chain = pm.find_path_to_map(
                    gs.map_group, gs.map_num,
                    effective_goal_map[0], effective_goal_map[1],
                    max_hops=6,
                )
            next_hop_name = None
            if mh_chain:
                next_hop = mh_chain[0]
                next_hop_name = mc.name_for(*next_hop)
            elif effective_goal_map is not None:
                next_hop_name = mc.name_for(*effective_goal_map)
            if next_hop_name:
                target_tiles: set[tuple[int, int]] = set()
                interact_target: tuple[int, int] | None = None
                # If current_goal has explicit target_pos AND we're on
                # the target_map, use that single tile (overrides
                # exit_tiles). Used by grinding goals like
                # grind_route_104_south to navigate to specific grass tile
                # rather than a map-boundary exit.
                if (
                    current_goal is not None
                    and getattr(current_goal, "target_pos", None) is not None
                    and (gs.map_group, gs.map_num) == current_goal.target_map
                ):
                    tpos = current_goal.target_pos
                    # gs.npcs_on_map lists the PLAYER too (its own object
                    # event at (gs.x,gs.y)); exclude it or the agent's own
                    # tile gets wrongly rejected as an approach candidate.
                    npc_now = {
                        (nx, ny) for (nx, ny, _g) in gs.npcs_on_map
                        if (nx, ny) != (gs.x, gs.y)
                    }
                    # A goal target_pos is a tile to REACH AND ACT ON: a gym
                    # leader / NPC you talk to (Brawly at (4,3)) or a grass
                    # tile you step onto. Always route to the target's
                    # walkable neighbours AND set interact_target, so on
                    # arrival we face+A (talk / trigger battle) or step on.
                    # Walking straight onto a leader never starts the battle
                    # (you bump), and a gym leader may sit outside NPC read
                    # range so `tpos not in npc_now` wrongly reads True — the
                    # old walk-onto branch then made BFS aim at the leader's
                    # own tile, the agent bumped it forever, and every bump
                    # registered the approach tile as "blocked" in tile_map,
                    # poisoning empirical_blocked so the leader became
                    # unreachable on the NEXT run (the Dewford (4,4)/(3,3)
                    # stall). Neighbours + interact + the approach-zone
                    # exemption below fix both.
                    interact_target = tpos
                    if cur_info.walkable(*tpos) and tpos not in npc_now:
                        target_tiles.add(tpos)
                    for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                        nb = (tpos[0] + dx, tpos[1] + dy)
                        if cur_info.walkable(*nb) and nb not in npc_now:
                            target_tiles.add(nb)
                else:
                    # Region-aware routing (H4a): on a map split into
                    # disconnected walkable components (Granite Cave floors),
                    # the warp to the next hop may sit in a component the player
                    # can't reach on foot — map_path collapses the map to one
                    # node and targets an unreachable warp, so BFS fails and the
                    # agent wanders. When the map is multi-component, route to
                    # the first-hop warp of the (map,component) warp graph toward
                    # the final goal instead. Single-component maps (≈99%) skip
                    # this entirely and take the legacy path byte-for-byte.
                    region_tiles: set[tuple[int, int]] = set()
                    region_hop: str | None = None
                    if mc.has_multiple_warp_components(
                        gs.map_group, gs.map_num
                    ):
                        # Aim the region router at the COMPONENT holding the
                        # goal tile when the goal names one (Lavaridge gym
                        # hole/geyser nav: an any-component landing bounces
                        # between floors but never into Flannery's walled
                        # room). Cross-map any-component stays as fallback.
                        _goal_tpos = None
                        if (
                            current_goal is not None
                            and getattr(current_goal, "target_pos", None)
                            is not None
                            and tuple(current_goal.target_map)
                            == tuple(effective_goal_map)
                        ):
                            _goal_tpos = current_goal.target_pos
                        region_tiles, region_hop = mc.region_route_targets(
                            gs.map_group, gs.map_num, (gs.x, gs.y),
                            effective_goal_map, mh_chain,
                            target_tile=_goal_tpos,
                        )
                    if region_tiles:
                        target_tiles |= region_tiles
                        # region_hop is the first warp's dest map; drives the
                        # on_goal_warp check + reason string below.
                        next_hop_name = region_hop or next_hop_name
                    else:
                        for direction, conns in cur_info.connections.items():
                            if any(
                                c["map_name"] == next_hop_name for c in conns
                            ):
                                # dest_name: a side can hold >1 connection
                                # (Route111 left = Route113 + Route112); take
                                # ONLY next_hop's strip, not the union.
                                target_tiles |= mc.exit_tiles_toward(
                                    gs.map_group, gs.map_num, direction,
                                    dest_name=next_hop_name,
                                )
                        if not target_tiles:
                            target_tiles |= mc.warp_tiles_for(
                                gs.map_group, gs.map_num, next_hop_name,
                            )
                # Mid door-entry: the approach tile fired "Up" and the game
                # walked us ONTO the non-walkable door warp tile itself (e.g.
                # Dewford (8,17)). BFS can't start on a non-walkable tile
                # (returns None) and that tile is never in target_tiles, so
                # the walkable-gated block below is skipped and the agent
                # falls through to wander logic — which walks it back off the
                # door, oscillating. If we're standing ON the very warp tile
                # that leads to our next hop, one more press finishes it.
                if target_tiles and not cur_info.walkable(gs.x, gs.y):
                    hop_key = (next_hop_name or "").replace("_", "").lower()
                    on_goal_warp = any(
                        w.get("x") == gs.x and w.get("y") == gs.y
                        and w.get("dest_map", "").replace("_", "").lower()
                        == hop_key
                        for w in getattr(cur_info, "warps", []) or []
                    )
                    if on_goal_warp:
                        step_btn = mc.warp_step_direction(
                            gs.map_group, gs.map_num, gs.x, gs.y,
                        )
                        if step_btn is not None:
                            return step_btn, (
                                f"mapbfs_warp_on:{step_btn}->{next_hop_name}"
                            )
                if target_tiles and cur_info.walkable(gs.x, gs.y):
                    npc_tiles = {
                        (nx, ny) for (nx, ny, _gid) in gs.npcs_on_map
                    }
                    # Add empirically-confirmed dead-end tiles to BFS
                    # blocked set. tile_map records "blocked" directions
                    # for each visited tile — if a tile has 4-way blocked
                    # (all 4 dirs failed in past), it's a sink from which
                    # no escape; treat as wall so BFS routes around it.
                    # Catches canon-walkable / game-blocked mismatches
                    # (Route 104 bridge area was chronic stuck because
                    # (31,16) Left/Right/Up all blocked in tile_map but
                    # BFS still routed through it.)
                    empirical_blocked: set[tuple[int, int]] = set()
                    mk = tm._map_key(gs.map_group, gs.map_num)
                    _dir_delta = {
                        "Up": (0, -1), "Down": (0, 1),
                        "Left": (-1, 0), "Right": (1, 0),
                    }
                    for tk, rec in tm._store.get(mk, {}).items():
                        try:
                            tx, ty = (int(p) for p in tk.split(","))
                        except ValueError:
                            continue
                        if len(rec.blocked) >= 3:
                            empirical_blocked.add((tx, ty))
                        # Direction-edge block: 1-way blocked + >=30 fails
                        # = adjacent tile is unreachable from this side
                        # (canon walkable=True but game has water/NPC).
                        for d in rec.blocked:
                            tried_count = rec.tried.get(d, 0)
                            if tried_count >= 200 and d in _dir_delta:
                                dx, dy = _dir_delta[d]
                                empirical_blocked.add((tx + dx, ty + dy))
                    perm_blocked = mc.permanent_blocked(
                        gs.map_group, gs.map_num,
                    )
                    try:
                        # NB: mk_mod is the module-level import. A local
                        # `from . import map_knowledge as mk_mod` here made
                        # mk_mod a function-local, so the Part B probe's earlier
                        # use of mk_mod raised UnboundLocalError -> its seal was
                        # silently empty -> it never banned the sandstorm-sealed
                        # Route113 hop and the agent bounced Mauville<->Route111.
                        mk = mk_mod.get_store().get(
                            gs.map_group, gs.map_num,
                        )
                        knowledge_trainer = mk.trainer_los
                        knowledge_elev = mk.tile_elevation
                        knowledge_ledges = mk.ledge_jumps
                        knowledge_water = mk.water_tiles
                        knowledge_triggers = mk.blocked_triggers
                    except Exception:
                        knowledge_trainer = set()
                        knowledge_elev = {}
                        knowledge_ledges = {}
                        knowledge_water = set()
                        knowledge_triggers = set()
                    # Deep water is walkable in the raw collision layer but
                    # impassable on foot (no Surf) — block it so BFS routes
                    # over the bridges instead of straight through the pond
                    # (the Route104 stall: BFS said "Down" into the pond,
                    # the agent couldn't move, and oscillated).
                    # trainer_los is NOT blocked: post Stone Badge the agent
                    # WINS trainer battles (Part B), and on Route104 the only
                    # water-safe path south passes through the Gina/Mia twins'
                    # line of sight — avoiding it left the Woods unreachable
                    # (198 tiles, ymax=16). With water blocked and trainer_los
                    # NOT blocked the Woods warp is reachable (477 tiles,
                    # ymax=30). So walk into the LOS, fight, and continue.
                    # Block warp tiles that aren't our destination: a straight
                    # BFS path may cross another door and warp us off-map into a
                    # loop (Dewford gym door (15,24) shares the x=15 column with
                    # House2's door (15,15) — the agent kept warping into the
                    # house). Warps in map_data are in agent coords. Protect the
                    # goal's own target tile(s) so we can still step onto it.
                    protected_warps = set(target_tiles)
                    if current_goal is not None and getattr(
                        current_goal, "target_pos", None
                    ) is not None:
                        protected_warps.add(current_goal.target_pos)
                    other_warps = {
                        (w["x"], w["y"])
                        for w in getattr(cur_info, "warps", []) or []
                        if "x" in w and "y" in w
                        and (w["x"], w["y"]) not in protected_warps
                    }
                    bfs_blocked = (
                        npc_tiles | empirical_blocked
                        | perm_blocked | knowledge_water | other_warps
                        | knowledge_triggers
                    )
                    # Never treat the interaction target or its approach
                    # tiles as blocked: face+A bumps against a leader/NPC (or
                    # stepping onto its own tile) register those tiles as
                    # "blocked" in tile_map, which otherwise walls off the
                    # only approach and makes the target unreachable (the
                    # Dewford Brawly (4,4)/(3,3) poisoning). target_tiles are
                    # exactly where we want BFS to end.
                    if interact_target is not None:
                        approach_zone = {interact_target} | {
                            (interact_target[0] + dx, interact_target[1] + dy)
                            for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0))
                        }
                        bfs_blocked -= approach_zone
                    bfs_blocked -= target_tiles
                    bfs_path = mc.bfs_to_tile(
                        gs.map_group, gs.map_num,
                        (gs.x, gs.y), target_tiles,
                        blocked_tiles=bfs_blocked,
                        tile_elevation=knowledge_elev,
                        ledge_jumps=knowledge_ledges,
                    )
                    # Water-misclassification fallback: the metatile parser can
                    # flag walkable LAND as water and wall off the only route to
                    # a goal (Slateport's Route110 north exit is reachable only
                    # across ~5 collision-walkable tiles the parser calls water;
                    # with water blocked EVERY north path failed and the agent
                    # fell back to a polluted path-memory exit into the Shipyard,
                    # oscillating there). When the water-blocked BFS finds NO
                    # path to the goal, retry with water un-blocked so the agent
                    # can cross the misclassified land. This only relaxes
                    # reachability; a genuine surfable-water bump is still caught
                    # by empirical_blocked on the next pass.
                    if not bfs_path and (bfs_blocked & knowledge_water):
                        bfs_path = mc.bfs_to_tile(
                            gs.map_group, gs.map_num,
                            (gs.x, gs.y), target_tiles,
                            blocked_tiles=bfs_blocked - knowledge_water,
                            tile_elevation=knowledge_elev,
                            ledge_jumps=knowledge_ledges,
                        )
                    # Live-collision fallback: dynamic barriers (Mauville Gym
                    # electric gates flipped by floor switches) are invisible to
                    # the static map.bin — the barrier tiles read as walls, so
                    # the goal BFS to Wattson is unreachable no matter what. Read
                    # the LIVE metatile grid (gBackupMapLayout) and re-run the BFS
                    # allowing tiles a switch has OPENED (extra_walkable) and
                    # avoiding tiles a switch has CLOSED (extra_blocked). Only
                    # fires when the static BFS already failed, so the (heavier)
                    # RAM grid read stays rare — puzzle maps only.
                    if not bfs_path and client is not None:
                        try:
                            extra_w, extra_b = (
                                state_mod.read_live_walkable_overrides(
                                    client, cur_info,
                                )
                            )
                        except Exception:
                            extra_w, extra_b = set(), set()
                        if extra_w or extra_b:
                            bfs_path = mc.bfs_to_tile(
                                gs.map_group, gs.map_num,
                                (gs.x, gs.y), target_tiles,
                                blocked_tiles=(bfs_blocked | extra_b) - extra_w,
                                tile_elevation=knowledge_elev,
                                ledge_jumps=knowledge_ledges,
                                extra_walkable=extra_w,
                            )
                            # A live-opened barrier must not strand behind an
                            # unmodeled elevation edge: retry the SAME live
                            # overrides elevation-relaxed (see fallback below).
                            if not bfs_path and knowledge_elev:
                                bfs_path = mc.bfs_to_tile(
                                    gs.map_group, gs.map_num,
                                    (gs.x, gs.y), target_tiles,
                                    blocked_tiles=(
                                        (bfs_blocked | extra_b) - extra_w
                                    ),
                                    ledge_jumps=knowledge_ledges,
                                    extra_walkable=extra_w,
                                )
                    # Elevation-relax fallback (mirrors the water one above):
                    # the elevation-carry BFS models on-foot movement only; if
                    # a goal is reachable in-game through something it can't
                    # see (scripted/forced movement, an unmodeled mechanic, or
                    # bad canon elevation), NO path would strand the agent.
                    # Retrying without elevation restores the pre-elevation
                    # behavior as the floor; a genuinely game-blocked step is
                    # then caught by tile_map empirical learning as before.
                    if not bfs_path and knowledge_elev:
                        bfs_path = mc.bfs_to_tile(
                            gs.map_group, gs.map_num,
                            (gs.x, gs.y), target_tiles,
                            blocked_tiles=bfs_blocked,
                            ledge_jumps=knowledge_ledges,
                        )
                    # Same-map hole/geyser fallback (Segment 4b, Lavaridge
                    # Gym): the goal tile sits in a walkable component that
                    # NO amount of walking reaches — only riding same-map
                    # warp pairs (1F holes <-> B1F geysers) and one-way
                    # ledges gets there, so every tile-BFS above failed.
                    # Ask the region router for the first-hop warp toward
                    # the goal's component and BFS to that warp instead;
                    # stepping on it fires the warp (pokeemerald step-on
                    # trigger) and the next turn re-plans from the landing.
                    # `bfs_path is None` (genuinely unreachable), NOT `not
                    # bfs_path`: an EMPTY path means the agent is already standing
                    # on an interact target tile (a walkable neighbour of the
                    # NPC), and must fall through to the face+A interact below.
                    # `not bfs_path` also caught [] and re-routed to a warp — so
                    # at Flannery's tile the agent walked off to a geyser instead
                    # of starting the gym battle, frozen 500+ turns at (14,9)
                    # (07-19). Only a true None means "no walk reaches it".
                    if (
                        bfs_path is None
                        and interact_target is not None
                        and mc.has_multiple_warp_components(
                            gs.map_group, gs.map_num
                        )
                    ):
                        _rt, _rh = mc.region_route_targets(
                            gs.map_group, gs.map_num, (gs.x, gs.y),
                            (gs.map_group, gs.map_num), None,
                            target_tile=interact_target,
                        )
                        if _rt:
                            bfs_path = mc.bfs_to_tile(
                                gs.map_group, gs.map_num,
                                (gs.x, gs.y), _rt,
                                blocked_tiles=bfs_blocked - _rt,
                                tile_elevation=knowledge_elev,
                                ledge_jumps=knowledge_ledges,
                            )
                            if not bfs_path and knowledge_elev:
                                # elevation-relax mirror (same rationale as
                                # the goal-BFS fallback above)
                                bfs_path = mc.bfs_to_tile(
                                    gs.map_group, gs.map_num,
                                    (gs.x, gs.y), _rt,
                                    blocked_tiles=bfs_blocked - _rt,
                                    ledge_jumps=knowledge_ledges,
                                )
                    if bfs_path:
                        next_btn = bfs_path[0]
                        delta = {
                            "Up": (0, -1), "Down": (0, 1),
                            "Left": (-1, 0), "Right": (1, 0),
                        }.get(next_btn, (0, 0))
                        next_tile = (gs.x + delta[0], gs.y + delta[1])
                        npc_blocking = any(
                            (nx, ny) == next_tile
                            for (nx, ny, _gid) in gs.npcs_on_map
                        )
                        if npc_blocking:
                            perp_pool = {
                                "Up": ["Left", "Right", "Down"],
                                "Down": ["Right", "Left", "Up"],
                                "Left": ["Down", "Up", "Right"],
                                "Right": ["Up", "Down", "Left"],
                            }.get(next_btn, [])
                            for cand in perp_pool:
                                cdx, cdy = {
                                    "Up": (0, -1), "Down": (0, 1),
                                    "Left": (-1, 0), "Right": (1, 0),
                                }[cand]
                                ctile = (gs.x + cdx, gs.y + cdy)
                                if any(
                                    (nx, ny) == ctile
                                    for (nx, ny, _g) in gs.npcs_on_map
                                ):
                                    continue
                                if cur_info.walkable(*ctile):
                                    return cand, (
                                        f"npc_avoid:{cand}<-{next_btn}"
                                        f"@({next_tile[0]},{next_tile[1]})"
                                    )
                        if (
                            same_pos_streak >= 20
                            and last_action == next_btn
                        ):
                            perp = {
                                "Up": "Left", "Down": "Right",
                                "Left": "Up", "Right": "Down",
                            }.get(next_btn, next_btn)
                            return perp, (
                                f"mapbfs_perp:{perp}<-{next_btn}"
                                f"@stuck{same_pos_streak}"
                            )
                        return next_btn, (
                            f"mapbfs:{next_btn}->{next_hop_name}"
                            f"(dist={len(bfs_path)})"
                        )
                    if bfs_path == [] and (gs.x, gs.y) in target_tiles:
                        if (
                            interact_target is not None
                            and interact_target != (gs.x, gs.y)
                        ):
                            ix, iy = interact_target
                            if iy < gs.y:
                                face = "Up"
                            elif iy > gs.y:
                                face = "Down"
                            elif ix < gs.x:
                                face = "Left"
                            else:
                                face = "Right"
                            # Turn to face the NPC first (pressing into the
                            # blocked tile only rotates, no move), then A to
                            # talk -> triggers the gym-leader battle. Alternate
                            # via last_action so we don't mash one button.
                            if last_action == face:
                                return "A", f"npc_interact:A->{ix},{iy}"
                            return face, f"npc_interact:{face}->{ix},{iy}"
                        step_btn = mc.warp_step_direction(
                            gs.map_group, gs.map_num, gs.x, gs.y,
                        )
                        if step_btn is not None:
                            # Just STEPPED ONTO a walkable warp tile this turn
                            # (same_pos_streak==0): a step-onto warp (Fiery Path
                            # entry (11,36)) already fired from the step, so the
                            # read is a mid-fade transient. Pressing warp_step
                            # here injected an extra move into the fade and
                            # bounced the agent straight back out (Route112 ->
                            # Fiery Path -> Route112, never crossing). Settle one
                            # turn with B (harmless during a fade); if we're still
                            # on the warp next turn it's a Woods-style
                            # press-through and warp_step fires then.
                            _on_warp = any(
                                w.get("x") == gs.x and w.get("y") == gs.y
                                for w in getattr(cur_info, "warps", []) or []
                            )
                            if (
                                _on_warp
                                and cur_info.walkable(gs.x, gs.y)
                                and same_pos_streak == 0
                            ):
                                return "B", "warp_settle"
                            # Consumed step-on pad: if we warped in and landed ON
                            # a walkable warp tile that IS the goal target (Fiery
                            # Path north pad (26,4)), warp_step_direction's door
                            # heuristic returns the wall side ("Down" into (26,5))
                            # and we bump forever. A step-on pad only re-fires
                            # after you leave and step back, so dismount to a
                            # walkable neighbour; next turn's BFS re-lands on it.
                            # same_map_streak<=1 = "just warped in" (a Woods-style
                            # press-through warp reached on foot has a big streak
                            # and keeps the old behaviour — test_map_data pins it).
                            if (
                                same_map_streak <= 1
                                and cur_info.walkable(gs.x, gs.y)
                                and any(
                                    w.get("x") == gs.x and w.get("y") == gs.y
                                    for w in getattr(cur_info, "warps", []) or []
                                )
                            ):
                                for _d, (_dx, _dy) in (
                                    ("Up", (0, -1)), ("Left", (-1, 0)),
                                    ("Right", (1, 0)), ("Down", (0, 1)),
                                ):
                                    if cur_info.walkable(gs.x + _dx, gs.y + _dy):
                                        return _d, f"warp_pad_dismount:{_d}"
                            return step_btn, (
                                f"mapbfs_warp:{step_btn}->{next_hop_name}"
                            )
        path_hops = pm.find_path_to_map(
            gs.map_group, gs.map_num,
            effective_goal_map[0], effective_goal_map[1],
            max_hops=6,
        )
        if path_hops:
            next_hop = path_hops[0]
            r = pm.first_transition_record(
                gs.map_group, gs.map_num,
                next_hop[0], next_hop[1],
                prefer_pos=(gs.x, gs.y),
            )
            if r is not None and r.from_pos is not None and r.seq:
                hop_key = f"{next_hop[0]}-{next_hop[1]}"
                if (gs.x, gs.y) == r.from_pos:
                    btn = r.seq[0]
                    if btn == "A":
                        try:
                            mc2 = map_data_mod.get_cache()
                            step = mc2.warp_step_direction(
                                gs.map_group, gs.map_num, gs.x, gs.y,
                            )
                        except (OSError, RuntimeError):
                            step = None
                        if step is not None:
                            btn = step
                            return btn, f"goal_warp_step:{btn}->{hop_key}"
                    return btn, (
                        f"goal_warp:{btn}->{hop_key}"
                        f"@hops={len(path_hops)}"
                    )
                d = _toward(gs.x, gs.y, r.from_pos[0], r.from_pos[1])
                mk = tm._map_key(gs.map_group, gs.map_num)
                rec = tm._store.get(mk, {}).get(tm._tile_key(gs.x, gs.y))
                blocked_here = set(rec.blocked) if rec is not None else set()
                npc_blocked: set[str] = set()
                for npc_x, npc_y, _gid in (gs.npcs_on_map or []):
                    if (npc_x, npc_y) == (gs.x, gs.y):
                        continue
                    if npc_x == gs.x and npc_y == gs.y - 1:
                        npc_blocked.add("Up")
                    if npc_x == gs.x and npc_y == gs.y + 1:
                        npc_blocked.add("Down")
                    if npc_x == gs.x - 1 and npc_y == gs.y:
                        npc_blocked.add("Left")
                    if npc_x == gs.x + 1 and npc_y == gs.y:
                        npc_blocked.add("Right")
                if d not in blocked_here and d not in npc_blocked:
                    return d, (
                        f"goal_toward:{d}->{r.from_pos}@{hop_key}"
                        f"(hops={len(path_hops)})"
                    )
    if gs.in_battle:
        if gs.is_trainer_battle:
            # GUARD: gs.in_battle from state.py 0x02022FEC is a STALE
            # trainer-type flag — once set during a trainer battle it
            # persists into the next overworld, so this branch fires
            # in overworld too. Only act when the screen actually shows
            # a battle/menu/dialog UI; otherwise fall through to
            # overworld navigation.
            # gs.in_battle is now reliable (0x02022FEC clears in the
            # overworld), so we no longer require a vision UI signal to act.
            # Crucially, the post-faint "Choose a POKEMON" LIST screen
            # reports NO signal at all (dialog/menu/battle_menu all False) —
            # the old in_battle_ui gate therefore skipped this handler and
            # left the agent pressing overworld-nav buttons at the party
            # menu forever (the chronic party-select stall). The FIGHT menu
            # is already handled above, so reaching here means some other
            # in-battle screen: A advances battle/faint dialogues, and on
            # the party list Down moves off the fainted lead (slot 0) to a
            # healthy member, first A opens "Do what? SEND OUT/SUMMARY/
            # CANCEL" (cursor defaults to SEND OUT), second A confirms.
            # Self-syncing send-out cycle robust to whichever sub-screen we
            # land on: A clears a faint/battle dialogue (-> party list), B
            # backs out of the "Do what" submenu or a SUMMARY screen (-> back
            # to the list; on the list itself a forced trainer send-out
            # ignores it), Down moves off the fainted lead to a healthy
            # member, then A opens "Do what" (cursor on SEND OUT) and A
            # confirms. Reaching a healthy slot + A + A always sends out.
            # Reached here only as a fallback: the main loop's RAM-based
            # Part B normally fills the queue (best move when it's our turn,
            # SEND_OUT_SEQ when the lead fainted). This runs when the active
            # battler's HP was unreadable — SEND_OUT_SEQ is the safe cycle
            # (self-syncs whichever sub-screen we land on).
            return SEND_OUT_SEQ[battle_turn % len(SEND_OUT_SEQ)], (
                "trainer:party_walk"
            )
        # SAME GUARD for wild-battle path: gs.in_battle from the stale
        # 0x02022FEC flag stays True after a battle ends. Without this
        # gate the wild_fight_safe / wild_catch_try / wild_run branches
        # below fire on the overworld, spamming A or RUN-direction
        # buttons that do nothing useful and freeze nav (observed:
        # wild_fight_safe = 552/600 turns at (28,7) Route 104 outdoor).
        in_battle_ui_wild = (
            screen_signals.get("dialog")
            or screen_signals.get("menu")
            or screen_signals.get("battle_menu")
        )
        if in_battle_ui_wild:
            # Try to catch as soon as battle starts (turn 1) while still
            # solo in the party. Treecko at Lv10+ one-shots most wild
            # Pokemon on turn 1 if we just press A → we'd never get to
            # capture anything, so when party is mono and we have balls,
            # throw IMMEDIATELY.
            catch_priority = (
                gs.bag_pokeball_count > 0
                and gs.party_count <= 2
                and gs.party0_hp_frac >= 0.3
            )
            if catch_priority and battle_turn >= 1:
                # FIGHT(TL) BAG(TR) PKMN(BL) RUN(BR) — BAG = Right of
                # FIGHT, then A. Sequence below opens BAG and picks the
                # first Poke Ball.
                catch_seq = ("Right", "A", "A", "A", "A", "A", "A", "A")
                return (
                    catch_seq[battle_turn % len(catch_seq)],
                    "wild_catch_try",
                )
            catch_ready = (
                gs.bag_pokeball_count > 0
                and gs.party0_hp_frac >= 0.5
                and battle_turn >= 4
            )
            if catch_ready and battle_turn % 8 == 0:
                catch_seq = ("Down", "Right", "A", "A", "A", "A")
                return (
                    catch_seq[battle_turn % len(catch_seq)],
                    "wild_catch_try",
                )
            # Fight down to 40% HP (was 70%): the old threshold left a 40-70%
            # dead band where the agent neither fought (ran instead) nor
            # healed (heal goal fires <40%) — so grinding in Granite Cave,
            # where wild mons hit back, stalled the lead at L26 for thousands
            # of turns. At <40% it runs to end the battle, then the heal goal
            # routes to the PC (restoring HP AND move PP), then it fights
            # again. The <26% party0_critical whiteout guard above still flees
            # genuinely dangerous fights.
            if gs.party0_max_hp > 0 and gs.party0_hp_frac >= 0.4:
                return "A", "wild_fight_safe"
            return (
                RUN_CYCLE[battle_turn % len(RUN_CYCLE)],
                "wild_run",
            )
        # Stale wild-battle flag in overworld — drop through to nav.

    if not gs.saveblock1_valid:
        return "A", "pre-save:A"

    if (
        gs.x == 0 and gs.y == 0
        and gs.map_group == 0 and gs.map_num == 0
    ):
        return "A", "pregame_intro:A"

    if (
        same_pos_streak > 0
        and same_hash_streak == 0
        and last_action == "A"
    ):
        return "A", "dialog_continue"
    # dialog_frozen handler: spam A while screen+pos both frozen — assumes
    # a stuck dialog. EXIT after a short window (>=8 same-hash turns) so
    # we don't burn the whole iter pressing A when there's no actual
    # dialog. Without this escalation, overworld stuck pos triggered
    # dialog_frozen 260/600 turns at Rustboro (22,55) with screen never
    # changing — agent has no way to break out into nav.
    if 2 <= same_hash_streak <= 7 and same_pos_streak >= 1:
        return "A", "dialog_frozen"

    if (
        same_map_streak >= 150
        and last_action not in ("A", "B")
        and same_map_streak % 7 == 0
    ):
        return "A", "npc_sweep:A"
    if (
        same_map_streak >= 200
        and same_map_streak % 11 == 0
    ):
        return "B", "menu_close:B"

    cur_x, cur_y = gs.x, gs.y
    cur_map = (gs.map_group, gs.map_num)
    mk = tm._map_key(*cur_map)
    rec = tm._store.get(mk, {}).get(tm._tile_key(cur_x, cur_y))
    blocked = set(rec.blocked) if rec is not None else set()
    tried = dict(rec.tried) if rec is not None else {}
    tiles = tm._store.get(mk, {})

    last_20 = recent_pos[-20:]
    uniq_20 = len(set(last_20))

    # Keep-in-goal-map exploration (BEFORE the generic escape rotation, so in a
    # puzzle gym it drives instead of the agent thrashing in place): when we are
    # ON the goal's target map but its target tile is unreachable (the goal BFS
    # above found nothing — e.g. Wattson is walled off behind the Mauville Gym
    # electric barriers and even the live-collision retry failed because the
    # switches aren't set yet), do NOT let the escape rotation / path-memory exit
    # route us out. Walk unvisited tiles to step on the floor SWITCHES that toggle
    # the barriers; once a switch opens a path the live-collision BFS above
    # reaches Wattson.
    goal_map_stuck = (
        current_goal is not None
        and (gs.map_group, gs.map_num) == current_goal.target_map
        and getattr(current_goal, "target_pos", None) is not None
        and gs.saveblock1_valid
        and not gs.in_battle
    )
    if goal_map_stuck:
        fdir = tm.bfs_frontier_direction(
            gs.map_group, gs.map_num, cur_x, cur_y, prefer="nearest",
        )
        if fdir and fdir not in blocked:
            return fdir, f"goal_map_explore:{fdir}"
        # Frontier exhausted (every accessible tile already visited) yet the
        # target is still unreachable — e.g. we healed, re-entered the gym, and
        # the barrier puzzle RESET, but the switch tiles are all "visited" so
        # there is no new frontier. Re-walk pseudo-randomly to step on the floor
        # switches AGAIN; the live-collision BFS above catches the frame a switch
        # re-opens the path to Wattson. Vary by position + streak so we don't
        # wall into one tile.
        seed = (cur_x * 31 + cur_y * 17 + same_map_streak) % 4
        rot = ["Up", "Right", "Down", "Left"]
        for k in range(4):
            cand = rot[(seed + k) % 4]
            if cand not in blocked:
                return cand, f"goal_map_rewalk:{cand}"

    if same_pos_streak >= 12 or (
        len(last_20) >= 20 and uniq_20 <= 4
    ):
        rotation = ["Up", "Right", "Down", "Left"]
        order = [
            d for d in rotation[escape_dir_index:] + rotation[:escape_dir_index]
            if d not in blocked
        ] or rotation
        return order[0], f"escape:{order[0]}"

    cur_map_key = f"{gs.map_group}-{gs.map_num}"
    from_paths = pm._store.get(cur_map_key, {})

    def _onward_score(target_str: str) -> int:
        inner = pm._store.get(target_str, {})
        return sum(1 for n in inner if n != cur_map_key)

    candidate: tuple[tuple[int, int], str, object] | None = None
    best_key: tuple[int, int, int] | None = None
    for tk, records in from_paths.items():
        try:
            tg, tn = (int(v) for v in tk.split("-"))
        except ValueError:
            continue
        t_visits = map_visit_counts.get((tg, tn), 0)
        onward = _onward_score(tk)
        for r in records:
            if r.from_pos is None or not r.seq:
                continue
            key = (-onward, t_visits, len(r.seq))
            if best_key is None or key < best_key:
                candidate = ((tg, tn), tk, r)
                best_key = key
            break
    if candidate is not None:
        target_record = candidate[2]
        target_pos = target_record.from_pos
        seq = target_record.seq
        if (cur_x, cur_y) == target_pos:
            return seq[0], f"path_memory_exit:{seq[0]}->{candidate[1]}"
        d = _toward(cur_x, cur_y, target_pos[0], target_pos[1])
        if d not in blocked:
            return d, f"toward_exit:{d}->{target_pos}"

    force_marker = config.MEMORY_DIR / "force_explore.flag"
    force_marker_active = force_marker.exists()
    if (
        (same_map_streak >= 400 or force_marker_active)
        and not gs.in_battle
        and gs.saveblock1_valid
    ):
        far_dir = tm.bfs_frontier_direction(
            gs.map_group, gs.map_num, cur_x, cur_y, prefer="farthest",
        )
        if far_dir and far_dir not in blocked:
            return far_dir, (
                f"force_explore:far_frontier:{far_dir}"
                f"@streak={same_map_streak}"
            )
        cp = reward_state.pick_checkpoint(
            (gs.map_group, gs.map_num),
        )
        if cp is not None:
            cp_g, cp_n, cp_x, cp_y = cp
            if (cp_g, cp_n) == (gs.map_group, gs.map_num):
                d = _toward(cur_x, cur_y, cp_x, cp_y)
                if d not in blocked:
                    return d, (
                        f"force_explore:checkpoint:{d}->({cp_x},{cp_y})"
                    )
            cp_map_key = f"{cp_g}-{cp_n}"
            cp_records = pm._store.get(
                f"{gs.map_group}-{gs.map_num}", {}
            ).get(cp_map_key)
            if cp_records:
                for r in cp_records:
                    if r.from_pos and r.seq:
                        if (cur_x, cur_y) == r.from_pos:
                            return r.seq[0], (
                                f"force_explore:warp:{r.seq[0]}->{cp_map_key}"
                            )
                        d = _toward(
                            cur_x, cur_y, r.from_pos[0], r.from_pos[1],
                        )
                        if d not in blocked:
                            return d, (
                                f"force_explore:toward_warp:{d}"
                                f"->{r.from_pos}"
                            )
                        break

    tiles_known = len(tiles)
    bias_order = (
        SOUTH_BIAS_ORDER
        if tiles_known < INDOOR_TILE_THRESHOLD and same_map_streak > 50
        else NORTH_BIAS_ORDER
    )
    scored: list[tuple[float, str, str]] = []
    for d in bias_order:
        if d in blocked:
            continue
        score = reward_state.score_direction(
            d, gs.map_group, gs.map_num, cur_x, cur_y,
            tm._store, blocked, same_map_streak,
        )
        scored.append((score, d, "reward_scored"))
    if scored:
        scored.sort(key=lambda t: t[0], reverse=True)
        best_score, best_dir, _ = scored[0]
        # 38 fix (06-29 user 指示): unvisited tile への積極的 approach。
        # reward_pick が chronic 時 same direction picking で stuck する。
        # reward_pick で best_score を返す前に、 unvisited tile 方向が
        # 存在し、 かつ same_pos_streak >= 5 (chronic 兆候) なら unvisited 優先。
        if best_score > float("-inf"):
            tag = "south_indoor" if bias_order is SOUTH_BIAS_ORDER else "north_outdoor"
            if same_pos_streak >= 5:
                unvisited_chronic: list[str] = []
                for d in NORTH_BIAS_ORDER:
                    if d in blocked:
                        continue
                    dx, dy = tile_map_mod.DELTA[d]
                    nk = tm._tile_key(cur_x + dx, cur_y + dy)
                    neighbor = tiles.get(nk)
                    if neighbor is None or neighbor.visits == 0:
                        unvisited_chronic.append(d)
                if unvisited_chronic:
                    return unvisited_chronic[0], (
                        f"explore_unvisited_chronic:{unvisited_chronic[0]}"
                        f"@streak={same_pos_streak}"
                    )
            return best_dir, f"reward_pick:{best_dir}@{best_score:.1f}/{tag}"

    unexplored_dirs: list[str] = []
    for d in NORTH_BIAS_ORDER:
        if d in blocked:
            continue
        dx, dy = tile_map_mod.DELTA[d]
        nk = tm._tile_key(cur_x + dx, cur_y + dy)
        neighbor = tiles.get(nk)
        if neighbor is None or neighbor.visits == 0:
            unexplored_dirs.append(d)
    if unexplored_dirs:
        choice = unexplored_dirs[0]
        return choice, f"explore_unvisited:{choice}"

    bfs = tm.bfs_frontier_direction(
        gs.map_group, gs.map_num, cur_x, cur_y, prefer="farthest"
    )
    if bfs is not None and bfs not in blocked:
        return bfs, f"bfs_far:{bfs}"

    for d in NORTH_BIAS_ORDER:
        if d in blocked:
            continue
        if tried.get(d, 0) == 0:
            return d, f"untried:{d}"

    for d in NORTH_BIAS_ORDER:
        if d not in blocked:
            return d, f"north_bias:{d}"

    rng = random.Random(gs.x * 31 + gs.y * 17 + battle_turn)
    return rng.choice(DIRECTIONS), "random"


def run(
    max_turns: int,
    record_dataset: bool,
    poll_period_sec: float,
) -> int:
    config.ensure_runtime_dirs()
    client = MGBAClient()
    if not client.ping():
        print("[FAIL] mGBA port 8895 unreachable. See STARTUP.md")
        return 1

    tm = tile_map_mod.TileMap()
    cleaned = tm.cleanup_phantom_walls()
    if cleaned:
        print(f"[start] cleared {cleaned} phantom 4-way-blocked tiles")
        tm.save()
    pm = path_memory_mod.TransitionMemory()
    knn = knn_mod.KNNExplorer(dim=64)
    knn_path = config.MEMORY_DIR / "knn_explorer.npz"
    knn.load(knn_path)
    curriculum = curr_mod.CurriculumIndex()
    curriculum.load()
    use_llm = (
        os.environ.get("POKE_RL_USE_LLM", "0") == "1"
        and config.load_api_key() is not None
    )
    advisor = llm_mod.LLMAdvisor() if use_llm else None
    llm_buttons_queue: list[str] = []
    last_consult_turn = -100
    prev_map_for_consult = None

    session_id = time.strftime("%Y%m%dT%H%M%S")
    print(
        f"[start] claude_heuristic session={session_id} "
        f"turns={max_turns} record_dataset={record_dataset}"
    )

    last_pos: tuple[int, int] | None = None
    last_action = ""
    last_map_key: tuple[int, int] | None = None
    # Previous stable frame's battle/HP, to fingerprint a WHITEOUT map change
    # (faint -> teleport to a PC). Such transitions are not walkable edges and
    # must not be recorded in path_memory (a bogus 0-27->0-2 from_pos=(31,45)
    # whiteout edge made goal_toward walk toward a fainting spot for ~3500 turns).
    prev_frame_in_battle = False
    prev_frame_lead_hp = -1
    same_pos_streak = 0
    same_hash_streak = 0
    same_map_streak = 0
    battle_turn = 0
    last_frame_hash = ""
    recent_pos: deque[tuple[int, int, int, int]] = deque(maxlen=100)
    map_visit_counts: dict[tuple[int, int], int] = {}
    escape_dir_index = 0
    history_buttons: list[str] = []
    entry_dir: str | None = None
    force_explore_until_turn = 0
    catch_phase_remaining = 0
    catch_seq_queue: list[str] = []
    battle_move_queue: list[str] = []
    battle_trainer_latch = False
    battle_double_latch = False
    last_ram_battle_turn = -999
    unknown_ui_streak = 0
    teach_cooldown_until = 0
    shop_cooldown_until = 0
    heal_cooldown_until = 0
    battle_heal_cooldown_until = 0
    rs = reward_state_mod.RewardState()
    rs.load()
    checkpoint_target: tuple[int, int, int, int] | None = None
    checkpoint_target_until = 0
    prev_in_battle = False
    prev_hp = 0
    prev_level = 0
    prev_party_count = 0
    prev_first_item_id = 0
    prev_event_flags = 0
    prev_badge_count = 0
    prev_battle_menu = False  # latch: keep detecting every turn once a
    # battle menu is seen, so animation frames don't drop us to nav

    decisions: dict[str, int] = {}

    for turn in range(1, max_turns + 1):
        shot = take_screenshot(client, session_id, turn)
        arr = preprocess.load_png_as_array(shot)
        fhash = preprocess.frame_hash(arr)
        if last_frame_hash == fhash:
            same_hash_streak += 1
        else:
            same_hash_streak = 0
        last_frame_hash = fhash

        gs = state_mod.read_state(client)
        map_key = (gs.map_group, gs.map_num)

        if gs.saveblock1_valid:
            if last_map_key == map_key:
                same_map_streak += 1
            else:
                same_map_streak = 0
                # Skip WHITEOUT teleports: a map change whose previous frame was
                # in battle or had a fainted lead is a faint->PC warp, not a
                # walkable edge. Recording it poisons goal_toward with a
                # teleport (the (31,45)->Mauville bug). Real warps (doors, cable
                # car interact) start from an overworld, non-fainted frame.
                is_whiteout = prev_frame_in_battle or prev_frame_lead_hp == 0
                if last_map_key is not None and not is_whiteout:
                    pm.record_transition(
                        last_map_key[0], last_map_key[1],
                        last_pos[0] if last_pos else None,
                        last_pos[1] if last_pos else None,
                        map_key[0], map_key[1],
                        gs.x, gs.y,
                        history_buttons[-8:],
                    )
                    if last_pos is not None:
                        rs.record_new_map(
                            last_map_key, last_pos, map_key,
                            (gs.x, gs.y), turn,
                        )
                    if last_action in DIRECTIONS:
                        entry_dir = last_action
                        force_explore_until_turn = turn + 30
            last_map_key = map_key
            prev_frame_in_battle = bool(gs.in_battle)
            prev_frame_lead_hp = int(getattr(gs, "party0_hp", -1))
            map_visit_counts[map_key] = (
                map_visit_counts.get(map_key, 0) + 1
            )
            pos_now = (gs.x, gs.y)
            recent_pos.append((map_key[0], map_key[1], gs.x, gs.y))
            # Only OVERWORLD frames record collision: a direction press during a
            # battle/menu/unknown-UI freezes the sprite in place, and recording
            # that as moved=False permanently poisons the tile (the record gate
            # existed in tile_map but was never wired -> Route112's road self-
            # contaminated from FLEE presses during grass battles).
            overworld_frame = getattr(gs, "game_mode", "overworld") == "overworld"
            if last_pos == pos_now:
                same_pos_streak += 1
                if last_action in DIRECTIONS:
                    tm.record_attempt(
                        *map_key, gs.x, gs.y,
                        last_action, moved=False,
                        overworld=overworld_frame,
                    )
            else:
                same_pos_streak = 0
                tm.record_visit(*map_key, gs.x, gs.y)
                if (
                    last_pos is not None
                    and last_action in DIRECTIONS
                ):
                    tm.record_attempt(
                        *map_key, last_pos[0], last_pos[1],
                        last_action, moved=True,
                        overworld=overworld_frame,
                    )
            last_pos = pos_now
            # Periodically relax over-blocked tiles (mirror of auto_loop:1077,
            # which the claude_heuristic loop never wired -> Route112 stayed
            # poisoned across sessions). With the unblock-on-success semantics
            # above this is a backstop for tiles the agent no longer walks over.
            if turn % 50 == 0:
                try:
                    tm.decay(*map_key)
                except (OSError, RuntimeError):
                    pass
        # gs.in_battle has a known RAM false-negative on English Emerald
        # (the BATTLE_FLAGS_CANDIDATES addresses stay 0 during the
        # move-select screen), so screen vision backs it up. But a lone vision
        # battle_menu is trusted ONLY when a real battle was RAM-confirmed within
        # the last ~12 turns (ram_battle_recent) — else the Mauville Gym floor
        # false-positives it and freezes nav. gs.in_battle here is the raw RAM
        # read (heuristic_button, which overrides it, runs later this turn).
        ss_for_battle = locals().get("screen_signals") or {}
        # Track consecutive unknown-UI (unwhitelisted cb2) frames so ui_escape
        # can back out of a menu the loop has no handler for (PokeNav, Trainer
        # Card, the level-up "forget move?" prompt). NOT gated on
        # ram_battle_recent: the forget-move screen IS mid-battle, and gating it
        # out reproduces the 175-turn imprisonment the architect measured.
        if getattr(gs, "game_mode", "overworld") == "unknown_ui":
            unknown_ui_streak += 1
        else:
            unknown_ui_streak = 0
        if gs.in_battle:
            last_ram_battle_turn = turn
        ram_battle_recent = (turn - last_ram_battle_turn) <= 12
        in_battle_seen = gs.in_battle or (
            bool(ss_for_battle.get("battle_menu")) and ram_battle_recent
        )
        # VLM tiebreaker (Option-1 / H11): a real battle animates; a FROZEN
        # "battle" that never resolves is the pixel-heuristic false-positive or
        # a stale gBattleTypeFlags read on a menu (the 4000-turn Pokedex stall).
        # When we've believed we're in a battle for a while AND the frame has
        # been static, ask the VLM once (cached by frame_hash) whether it is
        # really a battle; if not, drop the false battle so nav resumes. This
        # generalizes the hardcoded menu-CB2 guard. Cheap: fires only when
        # genuinely stuck, one Haiku call per static frame.
        if (
            in_battle_seen and client is not None
            and battle_turn >= 20 and same_hash_streak >= 8
        ):
            try:
                verdict = vlm_screen.is_battle_screen(shot, log=print)
            except Exception:
                verdict = None
            if verdict is False:
                object.__setattr__(gs, "in_battle", False)
                in_battle_seen = False
                last_ram_battle_turn = -999  # clear the recency latch
                print(
                    f"  [vlm_screen] turn {turn}: frozen 'battle' is NOT a "
                    f"battle -> dropping false in_battle (battle_turn="
                    f"{battle_turn}, frozen={same_hash_streak})"
                )
        if in_battle_seen:
            battle_turn += 1
            # Trainer-battle latch: gBattleTypeFlags DMA-reads 0 on the move-
            # select screen, so is_trainer_battle false-negatives mid-battle and
            # the wild-flee path fires -> FLEE_SEQ backs out of the FIGHT menu
            # every turn and NO move ever commits. Observed as a Route110 trainer
            # SOFT-LOCK: the agent pressed B forever, never won, never whited
            # out. Once the TRAINER bit is seen in THIS battle, keep it set for
            # the rest of the battle (OR 0x8 into battle_flags) so both the run
            # dispatch and heuristic_button consistently FIGHT (never flee). The
            # indoor-fight guard only covered buildings; trainers on outdoor
            # routes need this. Resets to wild between battles (see else).
            if gs.battle_flags & 0x8:
                battle_trainer_latch = True
            if battle_trainer_latch:
                object.__setattr__(
                    gs, "battle_flags", gs.battle_flags | 0x8,
                )
            # Double-battle latch (BATTLE_TYPE_DOUBLE = 0x1): the SAME DMA-read-0
            # false-negative hits the double bit on the move-select screen. When
            # 0x1 drops mid-turn the double branch is skipped and control falls to
            # the single-battle move_select_sequence, which cannot answer the
            # double target-select prompt and cycles back to the command menu
            # forever (a Jagged Pass double vs Shroomish+Magnemite froze 25 min at
            # full-cursor, 07-19). Once the double bit is seen in THIS battle, keep
            # it set for the rest of the battle so the double handler stays live.
            if gs.battle_flags & 0x1:
                battle_double_latch = True
            if battle_double_latch:
                object.__setattr__(
                    gs, "battle_flags", gs.battle_flags | 0x1,
                )
        else:
            battle_turn = 0
            battle_trainer_latch = False
            battle_double_latch = False

        if turn > 1 and gs.saveblock1_valid:
            r_battle = rs.record_battle_event(
                turn,
                prev_in_battle=prev_in_battle,
                cur_in_battle=gs.in_battle,
                prev_hp=prev_hp,
                cur_hp=gs.party0_hp,
                cur_max_hp=gs.party0_max_hp,
                prev_level=prev_level,
                cur_level=gs.party0_level,
                prev_party_count=prev_party_count,
                cur_party_count=gs.party_count,
                prev_first_item_id=prev_first_item_id,
                cur_first_item_id=gs.bag_first_item_id,
            )
            r_event = rs.record_event_flag_delta(
                turn,
                prev_flags=prev_event_flags,
                cur_flags=gs.total_event_flags,
            )
            r_smp = rs.record_same_map_penalty(turn, same_map_streak)
            r_heal = rs.record_healing(
                turn,
                prev_hp=prev_hp,
                cur_hp=gs.party0_hp,
                cur_max_hp=gs.party0_max_hp,
            )
            r_badge = rs.record_badge_delta(
                turn,
                prev_badges=prev_badge_count,
                cur_badges=gs.badge_count,
            )
            r_coord = rs.record_coord_visit(
                turn, gs.map_group, gs.map_num, gs.x, gs.y,
            )
            if (
                prev_hp > 0
                and gs.party0_hp == 0
                and gs.party0_max_hp > 0
                and not gs.in_battle
            ):
                rs.record_death(turn)
        prev_in_battle = gs.in_battle
        prev_hp = gs.party0_hp
        prev_level = gs.party0_level
        prev_party_count = gs.party_count
        prev_first_item_id = gs.bag_first_item_id
        prev_event_flags = gs.total_event_flags
        prev_badge_count = gs.badge_count

        # Root-cause fix (07-01): the ONLY branch that presses A on FIGHT
        # keys off THIS turn's battle_menu vision signal, but detection was
        # throttled to turn%5==0 — so on 4/5 battle turns the FIGHT branch
        # was dead and control fell through to overworld nav, pressing
        # movement buttons at the battle screen (the entire-session Roxanne
        # failure). detect_from_path is a sub-ms CPU pixel pass on the PNG
        # already captured this turn (no API, no emulator round-trip), so
        # compute EVERY turn while a battle is even suspected; keep the
        # 5-turn cadence in the overworld to preserve tuned nav behavior.
        battle_suspected = bool(gs.in_battle) or prev_battle_menu
        screen_signals = {}
        if battle_suspected or turn % 5 == 0:
            try:
                facing = last_action if last_action in DIRECTIONS else None
                screen_signals = sf_mod.detect_from_path(shot, facing=facing)
            except (OSError, ValueError):
                screen_signals = {}
        prev_battle_menu = bool(screen_signals.get("battle_menu"))

        if advisor is not None and not llm_buttons_queue:
            cur_map_tuple = (gs.map_group, gs.map_num)
            map_changed = (
                prev_map_for_consult is not None
                and cur_map_tuple != prev_map_for_consult
            )
            hp_frac_now = (
                gs.party0_hp_frac
                if gs.party0_max_hp > 0 else 1.0
            )
            if llm_mod.should_consult(
                screen_signals, same_pos_streak, map_changed,
                last_consult_turn, turn, gs.in_battle,
                same_map_streak=same_map_streak,
                hp_frac=hp_frac_now,
            ):
                advice = advisor.consult(
                    shot, gs, screen_signals,
                    same_pos_streak, same_map_streak,
                )
                if advice and advice.buttons:
                    llm_buttons_queue = list(advice.buttons)
                    last_consult_turn = turn
                    print(
                        f"  [LLM] {advice.buttons} :: {advice.reason} "
                        f"(${advice.cost_usd:.4f}, total ${advisor.total_cost:.3f})"
                    )
            prev_map_for_consult = cur_map_tuple

        if turn % 10 == 0 and gs.saveblock1_valid:
            try:
                arr = preprocess.load_png_as_array(shot)
                emb = preprocess.frame_embedding(arr, dim=64)
                novel = knn.query_or_add(emb, threshold=180.0)
                rs.record_knn_novelty(turn, novel)
            except (OSError, ValueError):
                pass

        if (
            gs.saveblock1_valid
            and not gs.in_battle
            and turn % 25 == 0
        ):
            try:
                m = curr_mod.record_milestone_if_new(
                    client, gs, curriculum,
                )
                if m:
                    decisions["curriculum_milestone"] = (
                        decisions.get("curriculum_milestone", 0) + 1
                    )
                    print(
                        f"[curriculum] new milestone map=({m.map_g},{m.map_n}) "
                        f"@({m.pos_x},{m.pos_y}) badges={m.badge_count} "
                        f"flags={m.total_event_flags}"
                    )
            except (OSError, RuntimeError):
                pass

        cur_goal = goals_mod.current_goal(gs) if gs.saveblock1_valid else None

        # HM-teach sub-task hook (Rock Smash chain): teach_rock_smash is a bag/
        # party MENU operation (target_map=None), not a nav goal. When active and
        # stable in the overworld, hand off to the VLM-driven menu driver for a
        # bounded one-shot; it presses its own buttons and returns once a party
        # mon knows Rock Smash. Placed BEFORE the battle/nav dispatch so the None
        # target never reaches mapbfs. A cooldown avoids hammering it if a run
        # gives up.
        if (
            cur_goal is not None
            and cur_goal.name == "teach_rock_smash"
            and gs.saveblock1_valid
            and not gs.in_battle
            and turn >= teach_cooldown_until
        ):
            print(f"  [hm_teach] turn {turn}: teaching Rock Smash via VLM…")
            try:
                ok = hm_teach.run_teach_subtask(client, log=print)
            except Exception as _exc:  # noqa: BLE001 — never crash the loop
                print(f"  [hm_teach] error: {_exc}")
                ok = False
            if not ok:
                teach_cooldown_until = turn + 30
            last_action = "teach_rock_smash"
            continue

        # Shop sub-task hook (H14): buy_potions fires inside the Mauville Mart.
        # The VLM driver walks to the counter, talks to the clerk, and buys Super
        # Potions. Fires the moment we're in the Mart (before the interact backstop
        # A-mashes the clerk). Short cooldown so a give-up doesn't hammer it.
        if (
            cur_goal is not None
            and cur_goal.name == "buy_potions"
            and (gs.map_group, gs.map_num) == (10, 7)
            and gs.saveblock1_valid
            and not gs.in_battle
            and turn >= shop_cooldown_until
        ):
            print(f"  [shop] turn {turn}: buying Super Potions via VLM…")
            try:
                ok = shop_mod.run_shop_subtask(client, log=print)
            except Exception as _exc:  # noqa: BLE001 — never crash the loop
                print(f"  [shop] error: {_exc}")
                ok = False
            if not ok:
                shop_cooldown_until = turn + 15
            last_action = "buy_potions"
            continue

        # Field-heal sub-task hook (H14): field_heal_potion (target_map=None) uses
        # a Super Potion on the lead between gauntlet trainers / in the gym. Out of
        # battle only, so the battle_move machinery is untouched.
        if (
            cur_goal is not None
            and cur_goal.name == "field_heal_potion"
            and gs.saveblock1_valid
            and not gs.in_battle
            and turn >= heal_cooldown_until
        ):
            print(f"  [field_heal] turn {turn}: healing lead via VLM…")
            try:
                ok = field_heal.run_heal_subtask(client, log=print)
            except Exception as _exc:  # noqa: BLE001 — never crash the loop
                print(f"  [field_heal] error: {_exc}")
                ok = False
            if not ok:
                heal_cooldown_until = turn + 25
            last_action = "field_heal_potion"
            continue

        # Part B — trainer-battle decision, driven by RAM not vision.
        # H6a root cause: the refill used to be gated on the flaky
        # screen_signals["battle_menu"] vision flag. When it missed the FIGHT
        # menu the queue stayed empty and control fell to the party_seq
        # (send-out) cycle, whose B/Down thrash the FIGHT menu — so the lead
        # (Grovyle L24) never attacked in the opening and got worn down and
        # fainted vs Brawly's Machop. Fix: tell "our turn to pick a move"
        # from "lead fainted, choose a replacement" via the active battler's
        # HP (gBattleMons[0]) — reliable RAM — and always queue a productive
        # sequence, never the FIGHT-menu thrash.
        # Flush any leftover battle queue once the battle ends: a full
        # move_select_sequence queued during the win/faint dialogue (in_battle
        # still True) would otherwise leak its remaining direction+A presses
        # into the overworld (Fable review F2/F5).
        if not gs.in_battle and (battle_move_queue or llm_buttons_queue):
            battle_move_queue = []
            llm_buttons_queue = []
        # Double battle (BATTLE_TYPE_DOUBLE = battle_flags & 0x1; e.g. the Route
        # 109 Tuber twins): the single-battle move_select_sequence loops forever
        # — after it picks the lead's move the game asks for the SECOND mon's
        # move and the sequence's leading B,B backs that out, so no turn ever
        # commits (a 3784-turn Route109 stall vs a full-HP Wingull was exactly
        # this). Just mash A: it takes FIGHT -> a move -> the default target for
        # BOTH mons, advancing the turn; the L32 lead overpowers the L13 doubles.
        # ...but when one of OUR mons faints, the game opens the party list to
        # pick its replacement, and there A only re-confirms whatever the cursor
        # already sits on — "GROVYLE is already in battle!" or a fainted mon —
        # so the cursor never moves and no replacement is ever sent. Observed on
        # Route111 vs the Twins: 900 turns frozen with BOTH foes still at full
        # HP. The lead-faint check below cannot catch it either, because battler
        # slots interleave sides (0/2 ours, 1/3 theirs): our second mon is index
        # 2, and active_hp() reads index 0. Navigate the list only when a benched
        # mon actually exists — with no replacement the game keeps playing 1v2
        # and SEND_OUT_SEQ would thrash the FIGHT menu (the Route109 stall).
        elif (
            not battle_move_queue and gs.in_battle
            and (getattr(gs, "battle_flags", 0) & 0x1)
        ):
            try:
                needs_send_out = battle_moves_mod.double_battle_needs_send_out(
                    client, gs.party_count,
                )
            except (OSError, RuntimeError, EmulatorError):
                needs_send_out = False
            battle_move_queue = (
                list(SEND_OUT_SEQ) if needs_send_out
                else list(DOUBLE_BATTLE_SEQ)
            )
        elif not battle_move_queue and gs.in_battle and gs.is_trainer_battle:
            try:
                active_hp = battle_moves_mod.active_hp(client)
            except (OSError, RuntimeError, EmulatorError):
                active_hp = -1
            if active_hp == 0:
                # Lead fainted -> navigate the party list and send out.
                battle_move_queue = list(SEND_OUT_SEQ)
            elif active_hp > 0:
                # Is it the OPPONENT that just fainted? In SHIFT battle style
                # the game then asks "<Leader> is about to send out X. Will
                # you switch?" (YES/NO) — and a move_select_sequence fired
                # here presses A on YES, opening the party menu with no B to
                # back out (the Machop->Makuhita stall). When the enemy HP is
                # 0 we press B until the next mon is in and enemy HP > 0
                # again, then best-move resumes.
                at_fight_menu = bool(screen_signals.get("battle_menu"))
                try:
                    enemy_cur_hp = battle_moves_mod.enemy_hp(client)[0]
                except (OSError, RuntimeError, EmulatorError):
                    enemy_cur_hp = -1
                if enemy_cur_hp == 0 and not at_fight_menu:
                    # Opponent fainted and we are NOT choosing a move (a switch
                    # prompt, victory text, or a level-up move-learn prompt is
                    # up). Drained one-per-turn: B,B answers a SHIFT "Will you
                    # switch? NO" and backs out nested text; the trailing A
                    # escapes the move-learn ping-pong that pure-B can NEVER
                    # leave — after a weak mon KOs a foe and levels, "Delete a
                    # move? YES/NO" <-> "Stop learning? YES/NO" both take B as
                    # NO, so ["B"] alone bounces between them forever (the
                    # ~1500-turn Jagged Pass trainer freeze, 07-24). A answers
                    # YES: either it stops the learn (protecting Rock Tomb) or
                    # opens the forget screen (cb2 0x081BFAB5) where the
                    # ui_escape exception declines it. The A lands only AFTER
                    # B,B consumed any SHIFT prompt, so it never mis-confirms a
                    # switch (the old Machop->Makuhita party-menu stall). Gated
                    # on NOT the FIGHT menu so a transient enemy-HP=0 read never
                    # skips our attack on our genuine turn (Fable F4).
                    battle_move_queue = ["B", "B", "A"]
                else:
                    # Mid-battle heal (H16, Flannery): Overheat (140, 2x vs
                    # grass, Sun-boosted, ~85-128 dmg) out-paces Rock Tomb
                    # unless the lead out-heals it — live runs whiteout with
                    # move selection alone. On OUR turn, if the lead is under
                    # the threshold and the bag holds a restore, spend the
                    # turn on a Super Potion instead of a move. should_heal
                    # requires enemy_cur_hp > 0 (the ==0 faint transition is
                    # B-mash territory — spec guard — and -1 means unread),
                    # and the cooldown keeps a failed VLM run from re-firing
                    # every turn and starving the battle of attacks. Above
                    # the threshold / no potion, behavior is byte-identical
                    # to before.
                    if battle_heal_mod.should_heal(
                        gs, enemy_cur_hp, turn, battle_heal_cooldown_until,
                    ):
                        print(
                            f"  [battle_heal] turn {turn}: lead "
                            f"{gs.party0_hp}/{gs.party0_max_hp}"
                            f" ({gs.party0_hp_frac:.0%}) <"
                            f" {battle_heal_mod.HEAL_TRIGGER_FRAC:.0%},"
                            f" restores={gs.bag_heal_qty}"
                            " -> Super Potion via VLM"
                        )
                        try:
                            ok = battle_heal_mod.run_battle_heal_subtask(
                                client, log=print,
                            )
                        except Exception as _exc:  # noqa: BLE001
                            print(f"  [battle_heal] error: {_exc}")
                            ok = False
                        if not ok:
                            battle_heal_cooldown_until = turn + 12
                        last_action = "battle_heal"
                        continue
                    # Our turn — pick the best damaging move from RAM. The
                    # sequence now self-corrects (leading B,B backs out of any
                    # menu it might mis-open), so fire it for ANY slot without
                    # a vision confirm — otherwise a best move in a bottom slot
                    # (Pursuit/Quick Attack once Pound is out of PP) never gets
                    # picked on a vision miss and the blind "A" stalls on the
                    # depleted move. -1 (all PP gone) -> A selects Struggle.
                    try:
                        best_slot = battle_moves_mod.best_move_index(client)
                    except (OSError, RuntimeError, EmulatorError):
                        best_slot = -1
                    if best_slot >= 0:
                        battle_move_queue = list(
                            battle_moves_mod.move_select_sequence(best_slot)
                        )
                    else:
                        battle_move_queue = ["A"]
            # active_hp == -1 (unreadable): leave queue empty -> heuristic
            # SEND_OUT_SEQ fallback, harmless and self-corrects next turn.
        elif (
            not battle_move_queue and gs.in_battle
            and not gs.is_trainer_battle
            # gs.in_battle (0x02022FEC) STAYS True in the overworld after a
            # battle; without a live battle-UI vision signal this branch fired
            # move_select in the dark Granite Cave overworld and froze there.
            # Require the same battle UI decide()'s wild handler gates on.
            and (
                screen_signals.get("battle_menu")
                or screen_signals.get("dialog")
                or screen_signals.get("menu")
            )
        ):
            try:
                enemy_cur_hp = battle_moves_mod.enemy_hp(client)[0]
            except (OSError, RuntimeError, EmulatorError):
                enemy_cur_hp = -1
            try:
                our_active_hp = battle_moves_mod.active_hp(client)
            except (OSError, RuntimeError, EmulatorError):
                our_active_hp = -1
            if our_active_hp == 0:
                # OUR active mon just fainted in a WILD battle -> the game opens
                # the party list to pick a replacement. The trainer path had
                # SEND_OUT_SEQ for this; the wild path did NOT, so the agent
                # looped the low-HP RUN sequence at hp0 forever (Route112: lead
                # fainted, 33 wild_run_lowhp@hp0 presses, no progress). active_hp
                # is gBattleMons[0] = the CURRENT active battler, so it reflects
                # a just-sent replacement (not the still-fainted party slot 0).
                # SEND_OUT_SEQ navigates to a usable mon; if the whole party is
                # down its A presses ride the whiteout text out to the Center.
                battle_move_queue = list(SEND_OUT_SEQ)
            elif enemy_cur_hp == 0:
                # Wild enemy fainted -> the battle is ending and post-KO text
                # plays: XP, and at L29 "Grovyle wants to learn LEAF BLADE /
                # delete a move?". Mash A: it advances the text AND answers the
                # move-learn prompt toward YES (forget the top move, learn Leaf
                # Blade). A move_select_sequence here would fire its leading B,B
                # and CANCEL the learn — and the Move Relearner is at Fallarbor
                # (unreachable), so Leaf Blade (the move that lets L29+ sweep
                # Brawly) would be lost permanently. A wild battle has no SHIFT
                # prompt so A never mis-opens a menu. Runs at ANY HP/ball state
                # (a low-HP KO hits the same learn prompt, and decide()'s run
                # sequence would navigate the YES/NO cursor the wrong way).
                battle_move_queue = ["A"]
            elif gs.party0_hp_frac >= 0.4 and gs.bag_pokeball_count == 0:
                # Only the grind goal wants wild XP. For every other goal we are
                # just crossing an encounter zone (the Granite Cave letter/sail
                # trek, later routes), where fighting each wild mon is slow and
                # invites PP-starvation stalls (Leaf Blade/Pursuit run dry, then
                # Quick Attack is immune vs a Ghost Sableye and the battle never
                # ends). So during traversal, flee every wild battle.
                is_grind = (
                    cur_goal is not None
                    and cur_goal.name.startswith("grind")
                )
                # Indoor maps (museum, gyms) have NO wild encounters: ANY battle
                # there is a scripted TRAINER battle. But the battle-flags RAM
                # word DMA-reads 0 on the move-select screen, so is_trainer_battle
                # false-negatives and lands us in this "wild" branch. Fleeing a
                # trainer loops "No running from a TRAINER battle!" (the Oceanic
                # Museum Aqua grunts only got won by FLEE_SEQ's stray A presses;
                # Wattson at parity would not). Force a fight on indoor maps.
                indoor = False
                try:
                    indoor = map_data_mod.get_cache().is_indoor(
                        gs.map_group, gs.map_num,
                    )
                except Exception:
                    indoor = False
                # Flee ONLY on a confirmed outdoor wild encounter: a VALID map
                # read that is not indoor and not the grind. A flicker frame
                # reads map (0,0) -> is_indoor False -> the old code fled and
                # FLEE_SEQ's Up presses walked the agent onto stairs, bouncing
                # it across the Shipyard 1F<->2F during a vision-battle_menu
                # false positive (the phantom-battle trap north of the museum).
                if not is_grind and not indoor and gs.saveblock1_valid:
                    battle_move_queue = list(FLEE_SEQ)
                else:
                    # Grind or indoor trainer battle: pick a move with PP from
                    # RAM instead of decide()'s blind "A" on the highlighted
                    # (often depleted) first move — a depleted "A" only pops
                    # "There's no PP left!" and the turn never resolves (the old
                    # Route106 grind froze at L26). Fire the full self-correcting
                    # cursor sequence for ANY slot.
                    try:
                        best_slot = battle_moves_mod.best_move_index(client)
                    except (OSError, RuntimeError, EmulatorError):
                        best_slot = -1
                    if best_slot >= 0:
                        battle_move_queue = list(
                            battle_moves_mod.move_select_sequence(best_slot)
                        )
                    elif indoor or not gs.saveblock1_valid:
                        # Indoor trainer (can't flee) or an unconfirmed flicker
                        # frame (must not flee-bounce): A-mash (FIGHT -> first
                        # move; an over-leveled lead still wins).
                        battle_move_queue = ["A"]
                    else:
                        # Grinding, no damaging move -> flee rather than loop a
                        # 0-damage move forever.
                        battle_move_queue = list(FLEE_SEQ)

        if battle_move_queue:
            button = battle_move_queue.pop(0)
            src = f"battle_move:{button}@rem{len(battle_move_queue)}"
            decisions["battle_move"] = decisions.get("battle_move", 0) + 1
        elif catch_seq_queue:
            button = catch_seq_queue.pop(0)
            src = f"catch_seq_followup:{button}@rem{len(catch_seq_queue)}"
            decisions["catch_seq_followup"] = (
                decisions.get("catch_seq_followup", 0) + 1
            )
            if not catch_seq_queue:
                catch_phase_remaining = 0
        elif llm_buttons_queue:
            valid = {"A","B","Up","Down","Left","Right","Start","Select"}
            llm_btn = llm_buttons_queue.pop(0)
            if llm_btn in valid:
                button = llm_btn
                src = f"llm:{llm_btn}"
                decisions["llm"] = decisions.get("llm", 0) + 1
            else:
                button, src = heuristic_button(
                    gs, tm, pm,
                    map_visit_counts=map_visit_counts,
                    same_pos_streak=same_pos_streak,
                    same_hash_streak=same_hash_streak,
                    same_map_streak=same_map_streak,
                    last_pos=last_pos,
                    last_action=last_action,
                    recent_pos=list(recent_pos),
                    battle_turn=battle_turn,
                    escape_dir_index=escape_dir_index,
                    reward_state=rs,
                    screen_signals=screen_signals,
                    current_goal=cur_goal,
                    client=client,
                    ram_battle_recent=ram_battle_recent,
                )
        else:
            button, src = heuristic_button(
                gs, tm, pm,
                map_visit_counts=map_visit_counts,
                same_pos_streak=same_pos_streak,
                same_hash_streak=same_hash_streak,
                same_map_streak=same_map_streak,
                last_pos=last_pos,
                last_action=last_action,
                recent_pos=list(recent_pos),
                battle_turn=battle_turn,
                escape_dir_index=escape_dir_index,
                reward_state=rs,
                screen_signals=screen_signals,
                current_goal=cur_goal,
                client=client,
                ram_battle_recent=ram_battle_recent,
            )
        # Unknown-UI escape: an unwhitelisted callback means a menu the loop has
        # no handler for froze the sprite. Override whatever nav produced (its
        # direction presses do nothing in a menu) with B,B,B,A to back out.
        # GATE (2026-07-24): do NOT fire during a battle sub-screen — a real
        # battle was confirmed within the last few turns (ram_battle_recent) and
        # its own handler (SEND_OUT_SEQ etc.) must drive it. Blindly B,B,B,A'ing
        # the in-battle party screen corrupted the send-out (Sceptile fainted +
        # stuck). The ONE battle sub-screen we DO escape is the level-up
        # "forget move?" prompt (cb2 0x081BFAB5): A-mash there can overwrite Rock
        # Tomb, so we deterministically decline it.
        forced_ui = None
        if not ram_battle_recent or gs.game_cb2 == 0x081BFAB5:
            forced_ui = ui_escape_button(unknown_ui_streak)
        if forced_ui is not None:
            button, src = forced_ui, f"ui_escape:{forced_ui}@{unknown_ui_streak}"
        if "escape" in src:
            escape_dir_index = (escape_dir_index + 1) % 4
        if src.startswith("wild_catch_try_screen:init") and not catch_seq_queue:
            catch_seq_queue = ["A", "A", "A", "A", "A", "A", "A"]
            catch_phase_remaining = len(catch_seq_queue)
        key = src.split(":")[0]
        decisions[key] = decisions.get(key, 0) + 1
        # Per-turn decision trace. The 100-turn stdout summary hides WHY the
        # agent moved (goal/src per step), which made the Route116 turn-around
        # undiagnosable from logs alone. Grep-able, one JSON object per line.
        try:
            with open(
                config.LOG_DIR / f"decisions_{session_id}.jsonl",
                "a", encoding="utf-8",
            ) as _df:
                _df.write(json.dumps({
                    "turn": turn,
                    "map": [gs.map_group, gs.map_num],
                    "pos": [gs.x, gs.y],
                    "goal": cur_goal.name if cur_goal else None,
                    "button": button,
                    "src": src,
                    "inb": bool(gs.in_battle),
                    "cb2": gs.game_cb2,
                    "bfl": gs.battle_flags,
                    "lvl": gs.party0_level,
                }) + "\n")
        except OSError:
            pass
        history_buttons.append(button)
        if len(history_buttons) > 20:
            history_buttons.pop(0)

        if record_dataset:
            rel_shot = str(shot.relative_to(config.ROOT)).replace("\\", "/")
            cur_blocked = []
            bfs_first = None
            tile_visits = 0
            if gs.saveblock1_valid:
                mk = tm._map_key(gs.map_group, gs.map_num)
                rec = tm._store.get(mk, {}).get(
                    tm._tile_key(gs.x, gs.y)
                )
                if rec is not None:
                    cur_blocked = list(rec.blocked)
                    tile_visits = int(rec.visits)
                if not gs.in_battle:
                    bfs_first = tm.bfs_frontier_direction(
                        gs.map_group, gs.map_num,
                        gs.x, gs.y, prefer="nearest",
                    )
            memory.append_to_path(
                config.DATASET_INDEX,
                {
                    "session_id": session_id,
                    "turn": turn,
                    "screenshot": rel_shot,
                    "button": button,
                    "source": f"claude_heuristic:{src}",
                    "fhash": fhash[:12],
                    "map": list(map_key) if gs.saveblock1_valid else None,
                    "pos": [gs.x, gs.y] if gs.saveblock1_valid else None,
                    "in_battle": gs.in_battle,
                    "is_trainer": gs.is_trainer_battle,
                    "blocked_here": cur_blocked,
                    "bfs_first": bfs_first,
                    "suppress_dir": None,
                    "oscillating": False,
                    "same_pos_streak": same_pos_streak,
                    "same_map_streak": same_map_streak,
                    "consecutive_dialog": 0,
                    "map_visit_count": 0,
                    "goal_direction": None,
                    "party0_hp": gs.party0_hp,
                    "party0_max_hp": gs.party0_max_hp,
                    "party0_level": gs.party0_level,
                    "badge_count": gs.badge_count,
                    "total_event_flags": gs.total_event_flags,
                    "event_flag_bytes_hex": gs.event_flag_bytes_hex,
                    "recent_actions": list(history_buttons)[-3:],
                    "opponent_level": 0,
                    "screen_dialog": bool(screen_signals.get("dialog")),
                    "screen_menu": bool(screen_signals.get("menu")),
                },
            )

        anomaly_kind: str | None = None
        rp_list = list(recent_pos)
        if same_pos_streak >= 8 and not gs.in_battle:
            anomaly_kind = "pos_stuck"
        elif (
            len(rp_list) >= 6
            and len({(g, n) for g, n, _, _ in rp_list[-6:]}) == 2
            and not gs.in_battle
        ):
            anomaly_kind = "door_ping"
        elif (
            len(rp_list) >= 15
            and len(set(rp_list[-15:])) <= 6
            and not gs.in_battle
        ):
            anomaly_kind = "small_circle"
        elif (
            len(rp_list) >= 40
            and len(set(rp_list[-40:])) <= 12
            and not gs.in_battle
        ):
            anomaly_kind = "med_circle"
        elif (
            same_map_streak >= 200
            and gs.saveblock1_valid
        ):
            mk_anom = tm._map_key(gs.map_group, gs.map_num)
            tiles_now = tm._store.get(mk_anom, {})
            visited_count = sum(
                1 for r in tiles_now.values() if r.visits > 0
            )
            if visited_count < 30:
                anomaly_kind = "map_lockin"
        goal_directed = src.startswith(GOAL_DIRECTED_SRC_PREFIXES)
        if (
            anomaly_kind is not None
            and gs.saveblock1_valid
            and not goal_directed
        ):
            escape_pool = [
                "B", "Up", "Right", "Down", "Left",
                "B", "Down", "Left", "Up", "Right",
                "B", "B", "A", "B",
            ]
            step_idx = (same_pos_streak * 3 + turn) % len(escape_pool)
            button = escape_pool[step_idx]
            src = f"anomaly_escape:{anomaly_kind}:{button}"
            decisions["anomaly_escape"] = (
                decisions.get("anomaly_escape", 0) + 1
            )

        recent_maps_list = [
            (g, n) for g, n, _, _ in list(recent_pos)[-6:]
        ]
        door_pingpong = (
            len(recent_maps_list) >= 4
            and recent_maps_list[-1] != recent_maps_list[-2]
            and recent_maps_list[-3] != recent_maps_list[-2]
            and recent_maps_list[-1] == recent_maps_list[-3]
            and last_action == button
            and button in DIRECTIONS
            and not gs.in_battle
        )
        if door_pingpong and gs.saveblock1_valid:
            perp_pool = {
                "Up": ["Right", "Down", "Left"],
                "Down": ["Left", "Up", "Right"],
                "Left": ["Down", "Right", "Up"],
                "Right": ["Up", "Left", "Down"],
            }[button]
            mk_pp = tm._map_key(gs.map_group, gs.map_num)
            rec_pp = tm._store.get(mk_pp, {}).get(
                tm._tile_key(gs.x, gs.y)
            )
            cur_blocked_pp = (
                set(rec_pp.blocked) if rec_pp is not None else set()
            )
            alternatives = [
                d for d in perp_pool if d not in cur_blocked_pp
            ]
            if alternatives:
                button = alternatives[turn % len(alternatives)]
                src = f"door_pingpong_break:{button}"
                decisions["door_pingpong_break"] = (
                    decisions.get("door_pingpong_break", 0) + 1
                )

        # forward_force: see forward_force_override's docstring. The src
        # gate inside it (goal_directed skip) is the 07-22 Jagged Pass
        # overshoot fix — extracted to a module function so the offline
        # descent replay (test_jagged_nav) exercises the REAL logic.
        if (
            not gs.in_battle
            and gs.saveblock1_valid
            and not door_pingpong
        ):
            mk_av = tm._map_key(gs.map_group, gs.map_num)
            rec_av = tm._store.get(mk_av, {}).get(
                tm._tile_key(gs.x, gs.y)
            )
            cur_blocked_now = (
                set(rec_av.blocked) if rec_av is not None else set()
            )
            forced_btn = forward_force_override(
                entry_dir, turn < force_explore_until_turn,
                button, src, cur_blocked_now, turn,
            )
            if forced_btn is not None:
                button = forced_btn
                src = f"forward_force:{button},entry={entry_dir}"
                decisions["forward_force"] = (
                    decisions.get("forward_force", 0) + 1
                )

        try:
            client.tap(button, frames=15)
        except (EmulatorError, ValueError) as exc:
            print(f"  [warn] button {button} failed: {exc}")
        time.sleep(poll_period_sec)
        if advisor is not None and src.startswith("llm:"):
            try:
                gs_after = state_mod.read_state(client)
                if gs_after.saveblock1_valid:
                    advisor.push_history(
                        (gs.x, gs.y), (gs.map_group, gs.map_num),
                        [button],
                        (gs_after.x, gs_after.y),
                        (gs_after.map_group, gs_after.map_num),
                        moved=(gs.x, gs.y, gs.map_group, gs.map_num) !=
                              (gs_after.x, gs_after.y, gs_after.map_group, gs_after.map_num),
                    )
            except (EmulatorError, ValueError):
                pass
        last_action = button

        if turn % 100 == 0:
            print(
                f"  turn {turn}: pos={last_pos} map={map_key} "
                f"same_pos={same_pos_streak} same_map={same_map_streak} "
                f"in_battle={gs.in_battle}"
            )
        if turn % 100 == 0:
            tm.save()
            pm.save()
            rs.save()

        if (
            turn > 0
            and turn % 150 == 0
            and gs.saveblock1_valid
            and not gs.in_battle
        ):
            try:
                snap_path = config.MEMORY_DIR / "savestate_autosnap.ss1"
                client.save_state_file(snap_path, flags=1)
                decisions["autosave_savestate"] = (
                    decisions.get("autosave_savestate", 0) + 1
                )
            except (EmulatorError, OSError) as exc:
                print(f"  [warn] savestate snap failed: {exc}")

        if (
            turn > 0
            and turn % 500 == 0
            and gs.saveblock1_valid
            and not gs.in_battle
            and gs.party0_max_hp > 0
        ):
            try:
                save_seq = [
                    "Start", "Down", "Down", "Down", "Down", "Down",
                    "A", "A", "A", "A", "B", "B", "B",
                ]
                for sb in save_seq:
                    client.tap(sb, frames=15)
                    time.sleep(0.3)
                decisions["ingame_report"] = (
                    decisions.get("ingame_report", 0) + 1
                )
            except (EmulatorError, ValueError) as exc:
                print(f"  [warn] in-game report failed: {exc}")

    tm.save()
    pm.save()
    rs.save()
    if gs.saveblock1_valid and not gs.in_battle:
        try:
            client.save_state_file(
                config.MEMORY_DIR / "savestate_final.ss1", flags=1
            )
        except (EmulatorError, OSError):
            pass
    try:
        knn.save(knn_path)
    except OSError:
        pass
    print(
        f"[end] turns={max_turns} decisions={decisions} "
        f"reward_cumulative={rs.cumulative_reward:.1f} "
        f"checkpoints={len(rs.cells)} "
        f"unique_maps={len(rs.last_visited_maps)}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--turns", type=int, default=500)
    parser.add_argument("--dataset", action="store_true")
    # Pokemon Emerald frame timing: tile-walk costs ~16 frames @ 60fps =
    # 266ms. button hold (frames=15 = 250ms) + this poll delay should
    # exceed walk-finish window or agent's next-tile movement queues
    # incorrectly. Empirically:
    # - 0.05s: chronic stuck (31,15-17) bridge area, no movement
    # - 0.3s: range expanded 4→11 tile but still bridge-bound
    # - manual 1.5s walks (frames=25): consistent successful movement
    # Bump to 0.6s = matches walk completion + small buffer. Throughput
    # 12x slower than original but actual movement reliable.
    parser.add_argument("--poll", type=float, default=0.6)
    args = parser.parse_args()
    return run(args.turns, args.dataset, args.poll)


if __name__ == "__main__":
    sys.exit(main())
