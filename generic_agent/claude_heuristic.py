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
import os
import random
import sys
import time
from collections import deque
from pathlib import Path

from . import (
    config,
    curriculum as curr_mod,
    goals as goals_mod,
    knn_explorer as knn_mod,
    llm_advisor as llm_mod,
    map_data as map_data_mod,
    memory,
    path_memory as path_memory_mod,
    preprocess,
    reward_state as reward_state_mod,
    screen_features as sf_mod,
    state as state_mod,
    tile_map as tile_map_mod,
)
from .io import EmulatorError, MGBAClient

DIRECTIONS = ("Up", "Right", "Down", "Left")
NORTH_BIAS_ORDER = ("Up", "Right", "Left", "Down")
SOUTH_BIAS_ORDER = ("Down", "Left", "Right", "Up")  # indoor maps: exit is south
INDOOR_TILE_THRESHOLD = 30  # heuristic for "indoor" maps
RUN_CYCLE = ("A", "A", "Down", "Right", "A", "A", "A")


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
) -> tuple[str, str]:
    if reward_state is None:
        reward_state = reward_state_mod.RewardState()
    if screen_signals is None:
        screen_signals = {}
    if screen_signals.get("battle_menu"):
        return "A", "battle_menu_visible:A"
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
        same_pos_streak >= 8
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
    if (
        same_map_streak >= 200
        and not gs.in_battle
        and gs.saveblock1_valid
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
        and (gs.map_group, gs.map_num) != effective_goal_map
    ):
        try:
            mc = map_data_mod.get_cache()
            cur_info = mc.get(gs.map_group, gs.map_num)
        except (OSError, RuntimeError):
            cur_info = None
            mc = None
        if cur_info and mc is not None:
            mh_chain = mc.map_path(
                gs.map_group, gs.map_num,
                effective_goal_map[0], effective_goal_map[1],
                max_hops=8,
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
                for direction, conn in cur_info.connections.items():
                    if conn["map_name"] == next_hop_name:
                        target_tiles |= mc.exit_tiles_toward(
                            gs.map_group, gs.map_num, direction,
                        )
                if not target_tiles:
                    target_tiles |= mc.warp_tiles_for(
                        gs.map_group, gs.map_num, next_hop_name,
                    )
                if target_tiles and cur_info.walkable(gs.x, gs.y):
                    bfs_path = mc.bfs_to_tile(
                        gs.map_group, gs.map_num,
                        (gs.x, gs.y), target_tiles,
                    )
                    if bfs_path:
                        next_btn = bfs_path[0]
                        return next_btn, (
                            f"mapbfs:{next_btn}->{next_hop_name}"
                            f"(dist={len(bfs_path)})"
                        )
                    if bfs_path == [] and (gs.x, gs.y) in target_tiles:
                        step_btn = mc.warp_step_direction(
                            gs.map_group, gs.map_num, gs.x, gs.y,
                        )
                        if step_btn is not None:
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
            return "A", "trainer:A"
        # Try to catch as soon as battle starts (turn 1) while still solo
        # in the party. Treecko at Lv10+ one-shots most wild Pokemon on
        # turn 1 if we just press A → we'd never get to capture anything,
        # so when party is mono and we have balls, throw IMMEDIATELY.
        catch_priority = (
            gs.bag_pokeball_count > 0
            and gs.party_count <= 2
            and gs.party0_hp_frac >= 0.3
        )
        if catch_priority and battle_turn >= 1:
            # 2x2 battle menu cursor starts on FIGHT (top-left).
            # BAG = bottom-left → Down, A → opens bag.
            # Inside bag: POKE BALLS pocket usually first or second.
            # The Down→Right→A→A→A→A sequence below was the legacy
            # attempt that often missed the bag pocket. Replace with a
            # canonical "open BAG → pick first Poke Ball" sequence:
            #   Down (FIGHT→PKMN), Down (PKMN→BAG won't work — BAG is
            #   bottom-right). Actually:
            #     FIGHT(TL) BAG(TR)
            #     PKMN(BL)  RUN(BR)
            #   So BAG = Right of FIGHT. Need Right then A.
            catch_seq = ("Right", "A", "A", "A", "A", "A", "A", "A")
            return catch_seq[battle_turn % len(catch_seq)], "wild_catch_try"
        catch_ready = (
            gs.bag_pokeball_count > 0
            and gs.party0_hp_frac >= 0.5
            and battle_turn >= 4
        )
        if catch_ready and battle_turn % 8 == 0:
            catch_seq = ("Down", "Right", "A", "A", "A", "A")
            return catch_seq[battle_turn % len(catch_seq)], "wild_catch_try"
        if gs.party0_max_hp > 0 and gs.party0_hp_frac >= 0.7:
            return "A", "wild_fight_safe"
        return RUN_CYCLE[battle_turn % len(RUN_CYCLE)], "wild_run"

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
    if same_hash_streak >= 2 and same_pos_streak >= 1:
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
        if best_score > float("-inf"):
            tag = "south_indoor" if bias_order is SOUTH_BIAS_ORDER else "north_outdoor"
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
                if last_map_key is not None:
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
            map_visit_counts[map_key] = (
                map_visit_counts.get(map_key, 0) + 1
            )
            pos_now = (gs.x, gs.y)
            recent_pos.append((map_key[0], map_key[1], gs.x, gs.y))
            if last_pos == pos_now:
                same_pos_streak += 1
                if last_action in DIRECTIONS:
                    tm.record_attempt(
                        *map_key, gs.x, gs.y,
                        last_action, moved=False,
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
                    )
            last_pos = pos_now
        if gs.in_battle:
            battle_turn += 1
        else:
            battle_turn = 0

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

        screen_signals = {}
        if turn % 5 == 0:
            try:
                facing = last_action if last_action in DIRECTIONS else None
                screen_signals = sf_mod.detect_from_path(shot, facing=facing)
            except (OSError, ValueError):
                screen_signals = {}

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

        if llm_buttons_queue:
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
            )
        if "escape" in src:
            escape_dir_index = (escape_dir_index + 1) % 4
        key = src.split(":")[0]
        decisions[key] = decisions.get(key, 0) + 1
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
        goal_directed = src.startswith(("mapbfs", "rival_seek", "rival_talk", "goal_"))
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

        if (
            entry_dir is not None
            and turn < force_explore_until_turn
            and button in DIRECTIONS
            and not gs.in_battle
            and gs.saveblock1_valid
            and not door_pingpong
        ):
            opp = {
                "Up": "Down", "Down": "Up",
                "Left": "Right", "Right": "Left",
            }.get(entry_dir)
            perp_map = {
                "Up": ("Right", "Left"),
                "Down": ("Left", "Right"),
                "Left": ("Up", "Down"),
                "Right": ("Down", "Up"),
            }[entry_dir]
            mk_av = tm._map_key(gs.map_group, gs.map_num)
            rec_av = tm._store.get(mk_av, {}).get(
                tm._tile_key(gs.x, gs.y)
            )
            cur_blocked_now = (
                set(rec_av.blocked) if rec_av is not None else set()
            )
            forced_btn: str | None = None
            if entry_dir not in cur_blocked_now and button != entry_dir:
                forced_btn = entry_dir
            elif button == opp:
                perp_options = [
                    d for d in perp_map if d not in cur_blocked_now
                ]
                if perp_options:
                    forced_btn = perp_options[turn % len(perp_options)]
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
    parser.add_argument("--poll", type=float, default=0.05)
    args = parser.parse_args()
    return run(args.turns, args.dataset, args.poll)


if __name__ == "__main__":
    sys.exit(main())
