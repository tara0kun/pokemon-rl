"""Cost-optimized main loop — 3-stage decision flow.

Per turn:
  1. Take screenshot + RAM state.
  2. Compute frame hash (+ map key).
  3. Cache lookup → if hit, execute cached action [$0].
  4. RAM-based default rule → if applicable, execute [$0].
  5. Track screen-frozen streak; below LOCAL threshold do auto-A.
  6. Above LOCAL threshold try LocalRecovery (B mash → random walk) [$0].
  7. Above API threshold call Haiku rescue and cache result [$0.001].

Total cost target: 90%+ reduction vs. always-Brain loop.

Run:
  poke-rl/Scripts/python.exe -m generic_agent.auto_loop --turns 500
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import (
    config,
    explorer as explorer_mod,
    local_brain,
    memory,
    path_memory as path_memory_mod,
    preprocess,
    state as state_mod,
    story_state,
    tile_map as tile_map_mod,
)
from .io import EmulatorError, MGBAClient


LOCAL_RECOVERY_STREAK = 3
API_RESCUE_STREAK = 8
DIALOG_LOOP_LIMIT = 15  # consecutive dialog_continue without pos change
MAP_STUCK_FLUSH_TURNS = 800  # flush this map's cache after N turns stuck on it


@dataclass
class AutoCosts:
    total_usd: float = 0.0
    rescue_calls: int = 0
    navigate_calls: int = 0
    cache_hits: int = 0
    rule_hits: int = 0
    recovery_steps: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class AutoLoopState:
    turn: int = 0
    history: list[str] = field(default_factory=list)
    last_frame_hash: str = ""
    same_hash_streak: int = 0
    last_map_key: tuple[int, int] | None = None
    same_map_streak: int = 0
    last_pos: tuple[int, int] | None = None
    last_action: str = ""
    consecutive_dialog: int = 0
    useless_cache_streak: int = 0
    same_pos_streak: int = 0
    pos_window: list[tuple[int, int, int, int]] = field(
        default_factory=list
    )
    recovery: local_brain.LocalRecovery = field(
        default_factory=local_brain.LocalRecovery
    )
    in_recovery: bool = False
    costs: AutoCosts = field(default_factory=AutoCosts)
    map_visit_counts: dict[tuple[int, int], int] = field(
        default_factory=dict
    )
    new_map_grace: int = 0  # cache-bypass countdown after map transition
    battle_turn: int = 0  # counter inside an active battle (reset on exit)
    recent_maps: list[tuple[int, int]] = field(default_factory=list)
    suppress_dir: str | None = None
    suppress_until_turn: int = 0
    sign_tiles: dict[tuple[int, int, int, int], int] = field(default_factory=dict)
    health_buffer: list[tuple[int, int, int, int]] = field(default_factory=list)
    entry_dir: str | None = None
    force_explore_until_turn: int = 0


def take_screenshot(
    client: MGBAClient, turn: int, session_id: str | None = None
) -> Path:
    if session_id:
        sess_dir = config.DATASET_DIR / "screens" / session_id
        sess_dir.mkdir(parents=True, exist_ok=True)
        p = sess_dir / f"t{turn:05d}.png"
    else:
        p = config.SCREENSHOT_DIR / f"auto_{turn:05d}.png"
    client.screenshot(p)
    time.sleep(0.15)
    return p


def execute_button(
    client: MGBAClient, button: str, frames: int = 15
) -> None:
    try:
        client.tap(button, frames=frames)
        time.sleep(max(0.05, frames / 60.0 + 0.05))
    except (EmulatorError, ValueError) as exc:
        print(f"  [warn] button {button} failed: {exc}")


def run(
    max_turns: int,
    budget_usd: float | None,
    record_dataset: bool = False,
    cnn_model_path: Path | None = None,
) -> int:
    config.ensure_runtime_dirs()
    client = MGBAClient()
    if not client.ping():
        print("[FAIL] mGBA port 8895 unreachable. See STARTUP.md")
        return 1

    cache = local_brain.FrameCache()
    tile_map = tile_map_mod.TileMap()
    cleaned = tile_map.cleanup_phantom_walls()
    if cleaned:
        print(f"[start] cleared {cleaned} phantom 4-way-blocked tiles")
        tile_map.save()
    path_memory = path_memory_mod.TransitionMemory()
    surveyor = explorer_mod.MapSurveyor(tile_map)
    state = AutoLoopState()
    session_id = (
        time.strftime("%Y%m%dT%H%M%S") if record_dataset else None
    )
    if record_dataset:
        print(f"[dataset] recording demonstrations under session_id={session_id}")

    cnn_brain = None
    if cnn_model_path is not None:
        from . import brain_cnn as _bcnn
        cnn_brain = _bcnn.CNNBrain(cnn_model_path)
        print(
            f"[cnn] loaded {cnn_model_path.name} — "
            f"navigate/rescue API calls disabled, $0/turn"
        )
    use_rescue = True
    try:
        from . import rescue_brain  # noqa: F401
    except ImportError:
        use_rescue = False

    print(
        f"[start] auto_loop max_turns={max_turns} "
        f"budget=${budget_usd if budget_usd else 'unbounded'} "
        f"cache_size={len(cache)} rescue={'on' if use_rescue else 'off'}"
    )

    try:
        while state.turn < max_turns:
            state.turn += 1
            shot = take_screenshot(client, state.turn, session_id)
            arr = preprocess.load_png_as_array(shot)
            fhash = preprocess.frame_hash(arr)

            gs = state_mod.read_state(client)
            map_key = (gs.map_group, gs.map_num)

            if state.last_frame_hash == fhash:
                state.same_hash_streak += 1
            else:
                state.same_hash_streak = 0
            state.last_frame_hash = fhash

            if gs.saveblock1_valid:
                if state.last_map_key == map_key:
                    state.same_map_streak += 1
                else:
                    state.same_map_streak = 0
                    prior_count = state.map_visit_counts.get(map_key, 0)
                    if prior_count == 0:
                        state.new_map_grace = 20
                    if state.last_map_key is not None:
                        recent_buttons = [
                            h.split("(")[0] for h in state.history[-12:]
                        ]
                        from_x = (
                            state.last_pos[0]
                            if state.last_pos is not None else None
                        )
                        from_y = (
                            state.last_pos[1]
                            if state.last_pos is not None else None
                        )
                        path_memory.record_transition(
                            state.last_map_key[0],
                            state.last_map_key[1],
                            from_x,
                            from_y,
                            map_key[0],
                            map_key[1],
                            gs.x if gs.saveblock1_valid else None,
                            gs.y if gs.saveblock1_valid else None,
                            recent_buttons,
                        )
                        opposite = {
                            "Up": "Down", "Down": "Up",
                            "Left": "Right", "Right": "Left",
                        }
                        if state.last_action in opposite:
                            state.suppress_dir = opposite[state.last_action]
                            state.suppress_until_turn = state.turn + 12
                            state.entry_dir = state.last_action
                            state.force_explore_until_turn = state.turn + 30
                    state.recent_maps.append(map_key)
                    if len(state.recent_maps) > 10:
                        state.recent_maps.pop(0)
                state.last_map_key = map_key
                state.map_visit_counts[map_key] = (
                    state.map_visit_counts.get(map_key, 0) + 1
                )

            oscillating = False
            if len(state.recent_maps) >= 6:
                last6 = state.recent_maps[-6:]
                uniq6 = set(last6)
                if len(uniq6) == 2 and last6[0] != last6[1]:
                    if all(
                        last6[i] != last6[i + 1] for i in range(5)
                    ):
                        oscillating = True

            if state.turn > state.suppress_until_turn:
                state.suppress_dir = None
            # else: transition / pre-save — keep state unchanged
            if state.new_map_grace > 0:
                state.new_map_grace -= 1

            if (
                state.same_map_streak > 0
                and state.same_map_streak % MAP_STUCK_FLUSH_TURNS == 0
                and gs.saveblock1_valid
            ):
                removed = cache.flush_map(gs.map_group, gs.map_num)
                if removed > 0:
                    print(
                        f"  [flush] map {map_key} stuck "
                        f"{state.same_map_streak} turns "
                        f"→ purged {removed} cache entries"
                    )

            decision: local_brain.LocalDecision | None = None
            decision_source = ""

            pos_now = (gs.x, gs.y) if gs.saveblock1_valid else None
            pos_changed = (
                pos_now is not None and state.last_pos is not None
                and pos_now != state.last_pos
            )
            if pos_changed:
                state.consecutive_dialog = 0
                state.in_recovery = False
                state.recovery.reset()
                state.same_pos_streak = 0
            elif pos_now is not None:
                state.same_pos_streak += 1

            if pos_now is not None:
                state.pos_window.append(
                    (gs.map_group, gs.map_num, pos_now[0], pos_now[1])
                )
                if len(state.pos_window) > 100:
                    state.pos_window.pop(0)
                state.health_buffer.append(
                    (gs.map_group, gs.map_num, pos_now[0], pos_now[1])
                )
                if len(state.health_buffer) > 200:
                    state.health_buffer.pop(0)
                if state.consecutive_dialog >= 8 and gs.saveblock1_valid:
                    sign_key = (
                        gs.map_group, gs.map_num,
                        pos_now[0], pos_now[1],
                    )
                    if sign_key not in state.sign_tiles:
                        state.sign_tiles[sign_key] = state.turn
                        print(
                            f"  [sign-trap] marked "
                            f"map={(gs.map_group, gs.map_num)} "
                            f"pos={pos_now} as dialog trap"
                        )
                tile_map.record_visit(
                    gs.map_group, gs.map_num, pos_now[0], pos_now[1]
                )
                if (
                    state.last_pos is not None
                    and state.last_action in tile_map_mod.DIRECTIONS
                ):
                    overworld_clean = (
                        not gs.in_battle
                        and state.consecutive_dialog == 0
                        and not state.in_recovery
                        and state.same_hash_streak < LOCAL_RECOVERY_STREAK
                    )
                    tile_map.record_attempt(
                        gs.map_group,
                        gs.map_num,
                        state.last_pos[0],
                        state.last_pos[1],
                        state.last_action,
                        moved=(pos_now != state.last_pos),
                        overworld=overworld_clean,
                    )
            stalled = (
                len(state.pos_window) >= 100
                and len(set(state.pos_window)) <= 3
            )
            cycling = False
            if len(state.pos_window) >= 20:
                last20 = state.pos_window[-20:]
                if len(set(last20)) <= 8:
                    cycling = True

            if cycling and gs.saveblock1_valid:
                removed = cache.flush_map(gs.map_group, gs.map_num)
                if removed > 0:
                    print(
                        f"  [cycle-break] map={map_key} "
                        f"{len(set(state.pos_window[-20:]))} uniq in 20t "
                        f"→ purged {removed} cache entries"
                    )
                state.pos_window.clear()
                state.suppress_dir = None
                state.suppress_until_turn = 0

            escape_mode = (
                pos_now is not None
                and state.same_pos_streak >= 25
                and not gs.in_battle
            )

            cached = cache.lookup(fhash, gs.map_group, gs.map_num)
            useless_now = (
                cached is not None
                and not pos_changed
                and state.last_action == cached.button
            )
            if useless_now:
                state.useless_cache_streak += 1
            else:
                state.useless_cache_streak = 0

            force_recovery = (
                stalled
                and state.same_map_streak >= 50
                and not state.in_recovery
                and not gs.in_battle
            )
            bfs_dir: str | None = None
            if force_recovery and pos_now is not None:
                bfs_dir = tile_map.bfs_frontier_direction(
                    gs.map_group,
                    gs.map_num,
                    pos_now[0],
                    pos_now[1],
                    prefer="farthest",
                )
                if bfs_dir is not None:
                    decision = local_brain.LocalDecision(
                        button=bfs_dir,
                        frames=15,
                        source=f"bfs_frontier({bfs_dir})",
                    )
                    decision_source = decision.source
                    state.costs.rule_hits += 1
                    force_recovery = False
            if force_recovery:
                state.consecutive_dialog = DIALOG_LOOP_LIMIT

            if gs.in_battle:
                state.battle_turn += 1
            else:
                state.battle_turn = 0

            if gs.in_battle and decision is None:
                if gs.is_trainer_battle or state.battle_turn <= 2:
                    battle_btn = "A"
                    battle_src = (
                        "battle.trainer.A"
                        if gs.is_trainer_battle
                        else "battle.intro.A"
                    )
                else:
                    offset = (state.battle_turn - 3) % 5
                    battle_btn = ["Down", "Right", "A", "A", "A"][offset]
                    battle_src = f"battle.wild_run[{offset}]"
                decision = local_brain.LocalDecision(
                    button=battle_btn, frames=8, source=battle_src,
                )
                decision_source = decision.source
                state.costs.rule_hits += 1

            if (
                decision is None
                and pos_now is not None
                and not gs.in_battle
                and not state.in_recovery
                and not oscillating
            ):
                survey_dir = surveyor.maybe_step(
                    gs.map_group, gs.map_num,
                    pos_now[0], pos_now[1],
                    state.map_visit_counts.get(map_key, 0),
                    state.same_map_streak,
                )
                if (
                    survey_dir is not None
                    and survey_dir != state.suppress_dir
                ):
                    decision = local_brain.LocalDecision(
                        button=survey_dir,
                        frames=15,
                        source=f"survey({survey_dir})",
                    )
                    decision_source = decision.source
                    state.costs.rule_hits += 1

            if (
                cached is not None
                and pos_now is not None
                and state.last_action == cached.button
                and state.same_pos_streak >= 3
                and cached.button in {"Up", "Down", "Left", "Right"}
            ):
                cache.forget(fhash, gs.map_group, gs.map_num)
                cached = None
                state.useless_cache_streak = 0

            if (
                cached is not None
                and state.suppress_dir is not None
                and cached.button == state.suppress_dir
            ):
                cache.forget(fhash, gs.map_group, gs.map_num)
                cached = None
                state.useless_cache_streak = 0

            cache_blocked_here = False
            if cached and pos_now is not None:
                mk_cur = tile_map._map_key(gs.map_group, gs.map_num)
                tiles_cur = tile_map._store.get(mk_cur, {})
                rec_cur = tiles_cur.get(
                    tile_map._tile_key(pos_now[0], pos_now[1])
                )
                if rec_cur is not None and cached.button in rec_cur.blocked:
                    cache_blocked_here = True
                    cache.forget(fhash, gs.map_group, gs.map_num)
                    state.useless_cache_streak = 0

            if escape_mode and gs.saveblock1_valid:
                cache.forget(fhash, gs.map_group, gs.map_num)
                cached = None
                state.useless_cache_streak = 0

            if (
                decision is None
                and cached
                and not cache_blocked_here
                and not escape_mode
                and not state.in_recovery
                and state.useless_cache_streak < 5
                and not force_recovery
                and state.new_map_grace == 0
                and not surveyor.in_survey()
            ):
                decision = local_brain.LocalDecision(
                    button=cached.button,
                    frames=cached.frames,
                    source=f"cache(hit={cached.hit_count})",
                )
                state.costs.cache_hits += 1
                decision_source = decision.source
            elif (
                cached
                and state.useless_cache_streak >= 5
            ):
                cache.forget(fhash, gs.map_group, gs.map_num)
                state.useless_cache_streak = 0
                state.consecutive_dialog = max(
                    state.consecutive_dialog, DIALOG_LOOP_LIMIT
                )

            if decision is None and state.in_recovery and not escape_mode:
                if state.recovery.exhausted():
                    state.in_recovery = False
                    state.consecutive_dialog = 0
                    state.recovery.reset()
                else:
                    decision = state.recovery.next()
                    decision_source = f"dialog-loop→{decision.source}"
                    state.costs.recovery_steps += 1

            if (
                decision is None
                and not escape_mode
                and state.same_hash_streak < LOCAL_RECOVERY_STREAK
                and state.consecutive_dialog < DIALOG_LOOP_LIMIT
            ):
                rule = local_brain.default_rule_for_state(
                    state.same_map_streak,
                    state.same_hash_streak,
                    pos_changed=pos_changed,
                    last_action=state.last_action,
                )
                if rule is not None:
                    decision = rule
                    decision_source = rule.source
                    state.costs.rule_hits += 1
                    if "dialog_continue" in rule.source and not pos_changed:
                        state.consecutive_dialog += 1
                    else:
                        state.consecutive_dialog = 0

            if (
                decision is None
                and not escape_mode
                and state.consecutive_dialog >= DIALOG_LOOP_LIMIT
            ):
                state.in_recovery = True
                decision = state.recovery.next()
                decision_source = f"dialog-loop→{decision.source}"
                state.costs.recovery_steps += 1

            if (
                decision is None
                and not escape_mode
                and state.same_hash_streak >= LOCAL_RECOVERY_STREAK
                and state.same_hash_streak < API_RESCUE_STREAK
            ):
                state.in_recovery = True
                decision = state.recovery.next()
                decision_source = decision.source
                state.costs.recovery_steps += 1

            def _call_brain(kind: str) -> local_brain.LocalDecision | None:
                if cnn_brain is not None:
                    try:
                        cnn_state: dict[str, object] = {
                            "in_battle": gs.in_battle,
                            "is_trainer": gs.is_trainer_battle,
                            "pos": list(pos_now) if pos_now else None,
                            "map": list(map_key),
                            "suppress_dir": state.suppress_dir,
                            "oscillating": oscillating,
                            "same_pos_streak": state.same_pos_streak,
                            "same_map_streak": state.same_map_streak,
                            "consecutive_dialog": state.consecutive_dialog,
                            "map_visit_count": (
                                state.map_visit_counts.get(map_key, 0)
                            ),
                            "goal_direction": None,
                        }
                        if pos_now is not None:
                            mk_cnn = tile_map._map_key(
                                gs.map_group, gs.map_num
                            )
                            rec_cnn = tile_map._store.get(mk_cnn, {}).get(
                                tile_map._tile_key(pos_now[0], pos_now[1])
                            )
                            if rec_cnn is not None:
                                cnn_state["blocked_here"] = list(rec_cnn.blocked)
                            if not gs.in_battle:
                                cnn_state["bfs_first"] = (
                                    tile_map.bfs_frontier_direction(
                                        gs.map_group, gs.map_num,
                                        pos_now[0], pos_now[1],
                                        prefer="nearest",
                                    )
                                )
                        btn, conf = cnn_brain.predict(shot, cnn_state)
                        return local_brain.LocalDecision(
                            button=btn, frames=15,
                            source=f"cnn({kind},conf={conf:.2f})",
                        )
                    except Exception as exc:
                        print(f"  [warn] CNN predict failed: {exc!r}")
                if not use_rescue:
                    return None
                if budget_usd and state.costs.total_usd >= budget_usd:
                    return None
                from . import rescue_brain as _rb
                parts = [gs.short()]
                cur_blocked: list[str] = []
                if pos_now is not None:
                    parts.append(
                        tile_map.summary_for(
                            gs.map_group, gs.map_num,
                            pos_now[0], pos_now[1]
                        )
                    )
                    mk_brain = tile_map._map_key(gs.map_group, gs.map_num)
                    rec_brain = tile_map._store.get(mk_brain, {}).get(
                        tile_map._tile_key(pos_now[0], pos_now[1])
                    )
                    if rec_brain is not None:
                        cur_blocked = list(rec_brain.blocked)
                if pos_now is not None and not gs.in_battle:
                    mk_for_bfs = tile_map._map_key(
                        gs.map_group, gs.map_num
                    )
                    tiles_on_map = tile_map._store.get(mk_for_bfs, {})
                    is_fresh_map = len(tiles_on_map) < 30
                    bfs_prefer = (
                        "farthest" if is_fresh_map else "nearest"
                    )
                    bfs_first = tile_map.bfs_frontier_direction(
                        gs.map_group, gs.map_num,
                        pos_now[0], pos_now[1],
                        prefer=bfs_prefer,
                    )
                    if bfs_first == state.suppress_dir or oscillating:
                        alt_prefer = (
                            "nearest" if is_fresh_map else "farthest"
                        )
                        alt = tile_map.bfs_frontier_direction(
                            gs.map_group, gs.map_num,
                            pos_now[0], pos_now[1],
                            prefer=alt_prefer,
                        )
                        if alt is not None and alt != state.suppress_dir:
                            bfs_first = alt
                        else:
                            bfs_first = None
                    if (
                        bfs_first is not None
                        and bfs_first not in cur_blocked
                    ):
                        parts.append(f"bfs_to_frontier={bfs_first}")
                if oscillating:
                    osc_maps = sorted({m for m in state.recent_maps[-6:]})
                    parts.append(
                        f"OSCILLATING between maps {osc_maps} - "
                        f"explore CURRENT map interior, not borders"
                    )
                if state.suppress_dir is not None:
                    parts.append(
                        f"AVOID '{state.suppress_dir}' "
                        f"(would re-cross border just came from)"
                    )
                if pos_now is not None and state.sign_tiles:
                    nearby_traps = [
                        (mx, my)
                        for (g, n, mx, my), t in state.sign_tiles.items()
                        if (g, n) == (gs.map_group, gs.map_num)
                        and state.turn - t < 500
                        and abs(mx - pos_now[0]) + abs(my - pos_now[1]) <= 3
                    ]
                    if nearby_traps:
                        parts.append(
                            f"AVOID stepping onto sign tiles "
                            f"{nearby_traps[:4]} (dialog trap)"
                        )
                if escape_mode:
                    parts.append(
                        f"ESCAPE MODE: same pos for "
                        f"{state.same_pos_streak} turns. "
                        f"Pipeline overridden. Press B to close any "
                        f"menu/dialog/sign. If overworld and free, pick "
                        f"a movement direction that is NOT in blocked_here."
                    )
                if state.same_pos_streak >= 5:
                    parts.append(
                        f"STUCK '{state.last_action}' didn't move "
                        f"({state.same_pos_streak}t)"
                    )
                if stalled:
                    uniq = sorted(set(state.pos_window))
                    parts.append(
                        f"STALLED only {len(uniq)} tiles in 100t"
                    )
                story_flags = story_state.infer_flags(state.map_visit_counts)
                story_hint = story_state.hint_for(
                    story_flags,
                    current_map=map_key,
                    visits_this_map=state.map_visit_counts.get(map_key, 0),
                )
                low_visit_maps = [
                    m for m, c in state.map_visit_counts.items()
                    if c <= 5 and m != (0, 0) and m != (0, 255)
                ]
                if low_visit_maps:
                    parts.append(
                        f"under-explored: {low_visit_maps[:3]}"
                    )
                avoid_set = (
                    {state.suppress_dir}
                    if state.suppress_dir is not None
                    else None
                )
                pm_cur_x = pos_now[0] if pos_now is not None else None
                pm_cur_y = pos_now[1] if pos_now is not None else None
                path_line = path_memory.summary_for(
                    gs.map_group, gs.map_num,
                    cur_x=pm_cur_x, cur_y=pm_cur_y,
                    blocked_first_step=cur_blocked,
                    avoid_first=avoid_set,
                )
                if not path_line.startswith("no known"):
                    parts.append(path_line)

                if state.same_map_streak >= 100 and pos_now is not None:
                    cur_visits = state.map_visit_counts.get(map_key, 0)
                    cur_map_str = f"{gs.map_group}-{gs.map_num}"

                    def _onward_score(target_str: str) -> int:
                        inner = path_memory._store.get(target_str, {})
                        score = 0
                        for next_tk in inner:
                            if next_tk == cur_map_str:
                                continue
                            score += 1
                        return score

                    candidates: list[
                        tuple[int, int, int, tuple[int, int], str]
                    ] = []
                    for tk, rec, dist in path_memory.nearby_records(
                        gs.map_group, gs.map_num,
                        pos_now[0], pos_now[1],
                    ):
                        try:
                            tg, tn = (int(v) for v in tk.split("-"))
                        except ValueError:
                            continue
                        target = (tg, tn)
                        t_visits = state.map_visit_counts.get(target, 0)
                        if t_visits >= cur_visits or not rec.seq:
                            continue
                        first_step = rec.seq[0]
                        if first_step not in tile_map_mod.DIRECTIONS:
                            continue
                        if first_step in cur_blocked:
                            continue
                        if first_step == state.suppress_dir:
                            continue
                        onward = _onward_score(tk)
                        candidates.append(
                            (-onward, t_visits, dist, target, first_step)
                        )
                    if candidates:
                        candidates.sort()
                        neg_on, v_visits, v_dist, tgt, gdir = candidates[0]
                        parts.append(
                            f"GOAL_DIRECTION={gdir} (reach "
                            f"map {tgt}: onward_paths={-neg_on}, "
                            f"visits={v_visits} vs current {cur_visits}, "
                            f"from-tile dist={v_dist})"
                        )
                state_summary_full = (
                    f"GOAL: {story_hint} | " + " | ".join(parts)
                )
                try:
                    if kind == "rescue":
                        rb_dec = _rb.call_rescue(
                            screenshot_png=shot,
                            state_summary=state_summary_full,
                            same_map_streak=state.same_map_streak,
                        )
                        state.costs.rescue_calls += 1
                    else:
                        rb_dec = _rb.call_navigate(
                            screenshot_png=shot,
                            state_summary=state_summary_full,
                            last_actions=state.history[-8:],
                        )
                        state.costs.navigate_calls += 1
                except Exception as exc:
                    print(f"  [warn] {kind} call failed: {exc!r}")
                    return None
                cache.remember(
                    fhash, gs.map_group, gs.map_num,
                    rb_dec.button, frames=15,
                )
                cost = rb_dec.cost_usd()
                state.costs.total_usd += cost
                state.costs.input_tokens += rb_dec.input_tokens
                state.costs.output_tokens += rb_dec.output_tokens
                state.costs.cache_read_tokens += rb_dec.cache_read_tokens
                state.costs.cache_write_tokens += (
                    rb_dec.cache_creation_tokens
                )
                return local_brain.LocalDecision(
                    button=rb_dec.button,
                    frames=15,
                    source=f"{kind}({rb_dec.reason[:30]})",
                )

            if (
                decision is None
                and state.same_hash_streak >= API_RESCUE_STREAK
            ):
                decision = _call_brain("rescue")
                if decision is not None:
                    decision_source = decision.source
                    state.same_hash_streak = 0
                else:
                    decision = local_brain.LocalDecision(
                        "B", 10, source="rescue-fallback"
                    )
                    decision_source = decision.source

            if decision is None:
                decision = _call_brain("navigate")
                if decision is not None:
                    decision_source = decision.source

            if decision is None and escape_mode:
                escape_seq = ["B", "Down", "B", "Right", "B", "Up", "B", "Left"]
                btn = escape_seq[state.same_pos_streak % len(escape_seq)]
                decision = local_brain.LocalDecision(
                    btn, 10, source=f"escape-fallback({btn})"
                )
                decision_source = decision.source

            if decision is None:
                if (
                    not gs.in_battle
                    and state.consecutive_dialog == 0
                    and state.same_pos_streak >= 1
                ):
                    cycle = ["B", "Down", "B", "Right", "B", "Up", "B", "Left"]
                    btn = cycle[state.turn % len(cycle)]
                    decision = local_brain.LocalDecision(
                        btn, 10, source=f"fallback({btn})"
                    )
                else:
                    decision = local_brain.LocalDecision(
                        "A", 8, source="fallback"
                    )
                decision_source = decision.source

            anomaly_kind: str | None = None
            if state.same_pos_streak >= 8 and not gs.in_battle:
                anomaly_kind = "pos_stuck"
            elif (
                len(state.recent_maps) >= 6
                and len(set(state.recent_maps[-6:])) == 2
                and not gs.in_battle
            ):
                anomaly_kind = "door_ping"
            elif (
                len(state.pos_window) >= 15
                and len(set(state.pos_window[-15:])) <= 6
                and not gs.in_battle
            ):
                anomaly_kind = "small_circle"
            elif (
                len(state.pos_window) >= 40
                and len(set(state.pos_window[-40:])) <= 12
                and not gs.in_battle
            ):
                anomaly_kind = "med_circle"
            elif (
                state.same_map_streak >= 200
                and gs.saveblock1_valid
                and pos_now is not None
            ):
                mk_anom = tile_map._map_key(gs.map_group, gs.map_num)
                tiles_now = tile_map._store.get(mk_anom, {})
                visited_count = sum(
                    1 for r in tiles_now.values() if r.visits > 0
                )
                if visited_count < 30:
                    anomaly_kind = "map_lockin"
            if anomaly_kind is not None and pos_now is not None:
                escape_pool = [
                    "B", "Up", "Right", "Down", "Left",
                    "B", "Down", "Left", "Up", "Right",
                    "A", "B", "Start", "B",
                ]
                step_idx = (state.same_pos_streak * 3 + state.turn) % len(escape_pool)
                anomaly_btn = escape_pool[step_idx]
                decision = local_brain.LocalDecision(
                    button=anomaly_btn, frames=15,
                    source=f"anomaly_escape({anomaly_kind}:{anomaly_btn})",
                )
                decision_source = decision.source

            door_pingpong = (
                len(state.recent_maps) >= 4
                and state.recent_maps[-1] != state.recent_maps[-2]
                and state.recent_maps[-3] != state.recent_maps[-2]
                and state.recent_maps[-1] == state.recent_maps[-3]
                and state.last_action == decision.button
                and decision.button in {"Up", "Down", "Left", "Right"}
                and not gs.in_battle
            )
            if door_pingpong and pos_now is not None:
                perp_pool = {
                    "Up": ["Right", "Down", "Left"],
                    "Down": ["Left", "Up", "Right"],
                    "Left": ["Down", "Right", "Up"],
                    "Right": ["Up", "Left", "Down"],
                }[decision.button]
                mk_pp = tile_map._map_key(gs.map_group, gs.map_num)
                rec_pp = tile_map._store.get(mk_pp, {}).get(
                    tile_map._tile_key(pos_now[0], pos_now[1])
                )
                cur_blocked_pp = (
                    set(rec_pp.blocked) if rec_pp is not None else set()
                )
                alternatives = [
                    d for d in perp_pool if d not in cur_blocked_pp
                ]
                if alternatives:
                    chosen = alternatives[state.turn % len(alternatives)]
                    decision = local_brain.LocalDecision(
                        button=chosen, frames=15,
                        source=f"door_pingpong_break({chosen})",
                    )
                    decision_source = decision.source

            if (
                state.entry_dir is not None
                and state.turn < state.force_explore_until_turn
                and decision.button in {"Up", "Down", "Left", "Right"}
                and not gs.in_battle
                and pos_now is not None
                and not door_pingpong
            ):
                opp = {
                    "Up": "Down", "Down": "Up",
                    "Left": "Right", "Right": "Left",
                }.get(state.entry_dir)
                perpendicular = {
                    "Up": ("Right", "Left"),
                    "Down": ("Left", "Right"),
                    "Left": ("Up", "Down"),
                    "Right": ("Down", "Up"),
                }[state.entry_dir]
                mk_av = tile_map._map_key(gs.map_group, gs.map_num)
                rec_av = tile_map._store.get(mk_av, {}).get(
                    tile_map._tile_key(pos_now[0], pos_now[1])
                )
                cur_blocked_now = (
                    set(rec_av.blocked) if rec_av is not None else set()
                )
                forced_btn: str | None = None
                if (
                    state.entry_dir not in cur_blocked_now
                    and decision.button != state.entry_dir
                ):
                    forced_btn = state.entry_dir
                elif decision.button == opp:
                    perp_options = [
                        d for d in perpendicular if d not in cur_blocked_now
                    ]
                    if perp_options:
                        forced_btn = perp_options[
                            state.turn % len(perp_options)
                        ]
                if forced_btn is not None:
                    decision = local_brain.LocalDecision(
                        button=forced_btn, frames=15,
                        source=(
                            f"forward_force({forced_btn}"
                            f",entry={state.entry_dir})"
                        ),
                    )
                    decision_source = decision.source

            execute_button(client, decision.button, decision.frames)
            state.last_action = decision.button
            state.last_pos = pos_now
            state.history.append(
                f"{decision.button}({decision_source})"
            )
            if len(state.history) > 20:
                state.history = state.history[-20:]

            if record_dataset:
                src_lower = decision_source.lower()
                trainable = (
                    "navigate" in src_lower
                    or "rescue" in src_lower
                    or "cache" in src_lower
                    or "dialog_continue" in src_lower
                    or "battle" in src_lower
                )
                random_walk = (
                    "recovery.rand" in src_lower
                    or "fallback" in src_lower
                    or "escape-fallback" in src_lower
                )
                if trainable and not random_walk:
                    rel_shot = str(
                        shot.relative_to(config.ROOT)
                    ).replace("\\", "/")
                    demo_blocked: list[str] = []
                    demo_bfs: str | None = None
                    if pos_now is not None:
                        mk_dm = tile_map._map_key(
                            gs.map_group, gs.map_num
                        )
                        rec_dm = tile_map._store.get(mk_dm, {}).get(
                            tile_map._tile_key(pos_now[0], pos_now[1])
                        )
                        if rec_dm is not None:
                            demo_blocked = list(rec_dm.blocked)
                        if not gs.in_battle:
                            demo_bfs = tile_map.bfs_frontier_direction(
                                gs.map_group, gs.map_num,
                                pos_now[0], pos_now[1],
                                prefer="nearest",
                            )
                    memory.append_to_path(
                        config.DATASET_INDEX,
                        {
                            "session_id": session_id,
                            "turn": state.turn,
                            "screenshot": rel_shot,
                            "button": decision.button,
                            "source": decision_source,
                            "fhash": fhash[:12],
                            "map": list(map_key),
                            "pos": list(pos_now) if pos_now else None,
                            "in_battle": gs.in_battle,
                            "is_trainer": gs.is_trainer_battle,
                            "blocked_here": demo_blocked,
                            "bfs_first": demo_bfs,
                            "suppress_dir": state.suppress_dir,
                            "oscillating": oscillating,
                            "same_pos_streak": state.same_pos_streak,
                            "same_map_streak": state.same_map_streak,
                            "consecutive_dialog": state.consecutive_dialog,
                            "map_visit_count": (
                                state.map_visit_counts.get(map_key, 0)
                            ),
                            "goal_direction": None,
                        },
                    )

            memory.append_run_log({
                "turn": state.turn,
                "fhash": fhash[:12],
                "map": map_key,
                "pos": (gs.x, gs.y) if gs.saveblock1_valid else None,
                "button": decision.button,
                "frames": decision.frames,
                "source": decision_source,
                "same_hash": state.same_hash_streak,
                "same_map": state.same_map_streak,
                "cost_usd_total": round(state.costs.total_usd, 5),
            })

            api_call = (
                "rescue" in decision_source
                or "navigate" in decision_source
            )
            if state.turn % 25 == 0 or api_call:
                print(
                    f"  turn {state.turn}: {decision.button:5} "
                    f"[{decision_source}] map={map_key} "
                    f"same_hash={state.same_hash_streak} "
                    f"cache={len(cache)} "
                    f"nav={state.costs.navigate_calls} "
                    f"resc={state.costs.rescue_calls} "
                    f"total=${state.costs.total_usd:.4f}"
                )

            if state.turn % 50 == 0:
                decayed = tile_map.decay(gs.map_group, gs.map_num)
                if decayed > 0:
                    print(
                        f"  [decay] map={map_key} "
                        f"relaxed {decayed} over-blocked tiles"
                    )
                cache.save()
                tile_map.save()
                path_memory.save()
                if gs.saveblock1_valid and pos_now is not None:
                    print(
                        tile_map.ascii_grid(
                            gs.map_group, gs.map_num,
                            pos_now[0], pos_now[1],
                        )
                    )

            if state.turn % 100 == 0 and len(state.health_buffer) >= 20:
                last100 = state.health_buffer[-100:]
                uniq_pos = len(set(last100))
                map_seq = [(g, n) for g, n, _, _ in last100]
                switches = sum(
                    1 for i in range(1, len(map_seq))
                    if map_seq[i] != map_seq[i - 1]
                )
                signs = len([
                    1 for (g, n, _, _) in state.sign_tiles
                    if (g, n) == (gs.map_group, gs.map_num)
                ])
                print(
                    f"  [health] last 100t: "
                    f"uniq_pos={uniq_pos} "
                    f"map_switches={switches} "
                    f"({switches / max(1, len(last100)):.2f}/turn) "
                    f"signs_this_map={signs} "
                    f"oscillating={'YES' if switches > 30 else 'no'}"
                )

    except KeyboardInterrupt:
        print("\n[stop] keyboard interrupt")

    cache.save()
    tile_map.save()
    path_memory.save()
    c = state.costs
    n = max(1, state.turn)
    print(
        f"[end] turns={state.turn} "
        f"cache_hits={c.cache_hits} ({c.cache_hits / n:.1%}) "
        f"rule_hits={c.rule_hits} ({c.rule_hits / n:.1%}) "
        f"recovery_steps={c.recovery_steps} "
        f"navigate_calls={c.navigate_calls} ({c.navigate_calls / n:.1%}) "
        f"rescue_calls={c.rescue_calls} "
        f"cache_size={len(cache)} "
        f"total=${c.total_usd:.4f}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--turns", type=int, default=500)
    parser.add_argument(
        "--budget", type=float, default=None,
        help="USD cap; stop when reached",
    )
    parser.add_argument(
        "--dataset", action="store_true",
        help="Persist screenshots + (image, action) pairs to dataset/ "
             "for imitation learning training",
    )
    parser.add_argument(
        "--cnn",
        default=None,
        help="Path to a trained TinyBrainCNN .pt — when set, navigate/"
             "rescue API calls are replaced by CNN inference ($0/turn)",
    )
    args = parser.parse_args()
    cnn_path = Path(args.cnn) if args.cnn else None
    return run(
        max_turns=args.turns,
        budget_usd=args.budget,
        record_dataset=args.dataset,
        cnn_model_path=cnn_path,
    )


if __name__ == "__main__":
    sys.exit(main())
