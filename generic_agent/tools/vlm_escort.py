"""VLM-driven escort: each turn, ask Haiku 4.5 to pick one button.

Designed for the chronic-stuck spots where the scripted escorts kept
hitting NPC dialogs / wild encounters / PP depletion in unpredictable
combinations. The VLM sees the screenshot directly, so it can choose
the right button for "no PP" dialogs, Yes/No prompts, battle menus,
and overworld navigation without us hand-coding each case.

Cost: Haiku 4.5 ~ $0.002 / call. Budget caps at $0.50 by default
(~250 calls); override with --budget-usd.

Run: poke-rl/Scripts/python.exe -m generic_agent.tools.vlm_escort \
        --target-map 0,19 --budget-usd 0.50 --max-turns 200
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from anthropic import Anthropic

from generic_agent import config, io as io_mod, map_data, preprocess, state as state_mod


MODEL = "claude-haiku-4-5"
MAX_TOKENS = 80
VALID_BUTTONS = {"A", "B", "Up", "Down", "Left", "Right", "Start", "Select"}


SYSTEM_PROMPT = (
    "You guide an automated Pokemon Emerald agent. You MUST reply with ONLY "
    "a JSON object - no prose, no explanation, no markdown. First character "
    "of your reply MUST be '{'. Schema:\n"
    '{"button": "<A|B|Up|Down|Left|Right|Start|Select>", '
    '"reason": "<<= 8 words>"}\n'
    'Example reply: {"button": "Left", "reason": "walk west toward Route 104"}\n\n'
    "Hoenn early game map IDs (canonical):\n"
    "- (0,0) Petalburg City\n"
    "- (0,3) Rustboro City (Stone Badge gym)\n"
    "- (0,10) Oldale Town\n"
    "- (0,17) Route 102 (between Petalburg east + Oldale west)\n"
    "- (0,19) Route 104 (west of Petalburg, leads north to Rustboro)\n"
    "- (0,16) Route 101 (south of Oldale)\n\n"
    "Directional facts:\n"
    "- Petalburg WEST edge -> Route 104\n"
    "- Petalburg EAST edge -> Route 102\n"
    "- Route 104 NORTH -> Petalburg Woods -> Rustboro City\n"
    "- Pokemon Centers heal HP+PP; nurse counter at top of PC interior\n\n"
    "Choose buttons that make progress toward the GOAL_MAP target. "
    "Watch screenshot for: dialog text box (advance with A), battle menu "
    "(FIGHT/BAG/POKEMON/RUN - pick RUN when over-leveled), Yes/No prompts, "
    "or walls (you walked into a wall, try another direction)."
)


def compute_bfs_hint(g, goal_map: tuple[int, int]) -> str:
    """Canonical BFS hint from current pos to a tile that connects to
    goal_map. Returns 'BFS_DIRECTION=<dir>' or '' if no useful hint."""
    mc = map_data.get_cache()
    info = mc.get(g.map_group, g.map_num)
    if info is None:
        return ""

    # If goal is current map, no inter-map hop needed.
    cur_map = (g.map_group, g.map_num)
    if cur_map == goal_map:
        return ""

    # Find connection direction toward goal_map, then collect that edge's
    # walkable tiles as BFS targets.
    chain = mc.map_path(*cur_map, *goal_map, max_hops=10)
    if not chain:
        return ""
    next_map = chain[0]
    next_name = mc.name_for(*next_map) or ""

    target_tiles: set[tuple[int, int]] = set()
    for direction, conn in info.connections.items():
        if conn.get("map_name") == next_name:
            target_tiles |= mc.exit_tiles_toward(
                g.map_group, g.map_num, direction
            )
    if not target_tiles:
        target_tiles |= mc.warp_tiles_for(
            g.map_group, g.map_num, next_name
        )
    if not target_tiles or not info.walkable(g.x, g.y):
        return ""

    # Block dynamic NPC interaction zones (NPC tile + adjacents), but
    # never block agent's own tile.
    npc_tiles = set()
    for (nx, ny, gid) in g.npcs_on_map:
        if gid == 0:
            continue
        npc_tiles.add((nx, ny))
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            npc_tiles.add((nx + dx, ny + dy))
    npc_tiles.discard((g.x, g.y))

    path = mc.bfs_to_tile(
        g.map_group, g.map_num, (g.x, g.y), target_tiles,
        blocked_tiles=npc_tiles,
    )
    if path is None:
        path = mc.bfs_to_tile(
            g.map_group, g.map_num, (g.x, g.y), target_tiles
        )
    if not path:
        return ""
    return f"BFS_DIRECTION={path[0]}  (path_len={len(path)}, " \
           f"first_5={','.join(path[:5])})"


def build_user_text(
    g, goal_map: tuple[int, int], history: list[tuple[str, tuple]]
) -> str:
    state_summary = (
        f"pos=({g.x},{g.y}) map=({g.map_group},{g.map_num}) "
        f"hp={g.party0_hp}/{g.party0_max_hp} lv={g.party0_level} "
        f"party={g.party_count} balls={g.bag_pokeball_count} "
        f"in_battle={g.in_battle}"
    )
    bfs_hint = compute_bfs_hint(g, goal_map)
    bfs_line = f"\n{bfs_hint}\n" if bfs_hint else "\n"
    history_text = ""
    if history:
        lines = []
        for btn, after_pos in history[-8:]:
            lines.append(f"  {btn} -> pos={after_pos}")
        history_text = "Recent actions:\n" + "\n".join(lines) + "\n"
    return (
        f"GOAL_MAP=({goal_map[0]},{goal_map[1]})\n"
        f"STATE: {state_summary}"
        f"{bfs_line}"
        f"{history_text}"
        "STRICT: If BFS_DIRECTION is given and the screen shows the overworld "
        "(not in a dialog/menu/battle), you MUST pick that direction. "
        "BFS already accounts for walls + NPCs. Only override when a dialog "
        "is on screen (then A) or a menu (then B). Pick the next button."
    )


def parse_button(text: str) -> tuple[str | None, str]:
    txt = text.strip()
    if txt.startswith("```"):
        lines = txt.split("\n")
        txt = "\n".join(l for l in lines if not l.startswith("```"))
    try:
        obj = json.loads(txt)
    except json.JSONDecodeError:
        return None, "json parse fail"
    btn = obj.get("button", "")
    reason = obj.get("reason", "")
    if btn not in VALID_BUTTONS:
        return None, f"invalid button: {btn}"
    return btn, reason


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-map", type=str, default="0,19",
                        help="goal map as 'group,num' (default 0,19 Route 104)")
    parser.add_argument("--budget-usd", type=float, default=0.50)
    parser.add_argument("--max-turns", type=int, default=200)
    args = parser.parse_args()

    g_part, n_part = args.target_map.split(",")
    goal_map = (int(g_part), int(n_part))

    key = config.load_api_key()
    if not key:
        print("[err] no API key")
        return 1
    client = Anthropic(api_key=key)
    mgba = io_mod.MGBAClient()

    g0 = state_state = state_mod.read_state(mgba)
    print(f"start: pos=({g0.x},{g0.y}) m=({g0.map_group},{g0.map_num}) "
          f"hp={g0.party0_hp}/{g0.party0_max_hp} goal={goal_map}")

    if (g0.map_group, g0.map_num) == goal_map:
        print("already on target map")
        return 0

    total_cost = 0.0
    history: list[tuple[str, tuple]] = []
    shot_path = config.LOG_DIR / "vlm_escort_shot.png"
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)

    for turn in range(args.max_turns):
        if total_cost >= args.budget_usd:
            print(f"BUDGET EXHAUSTED: ${total_cost:.4f} >= ${args.budget_usd}")
            return 2

        g = state_mod.read_state(mgba)
        if not g.saveblock1_valid:
            mgba.tap("A", frames=10); time.sleep(0.3)
            continue

        if (g.map_group, g.map_num) == goal_map:
            print(f"DONE: reached {goal_map} at ({g.x},{g.y}) turn={turn}")
            print(f"total cost: ${total_cost:.4f}")
            return 0

        mgba.screenshot(str(shot_path))
        try:
            image_block, _bytes, _hash = preprocess.png_path_to_jpeg_block(
                shot_path
            )
        except Exception as e:
            print(f"[err] screenshot encode: {e}")
            mgba.tap("A", frames=10); time.sleep(0.3)
            continue

        user_text = build_user_text(g, goal_map, history)
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=[
                    {"type": "text", "text": SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral"}}
                ],
                messages=[
                    {
                        "role": "user",
                        "content": [
                            image_block,
                            {"type": "text", "text": user_text},
                        ],
                    }
                ],
            )
        except Exception as e:
            print(f"[err] API: {e}")
            time.sleep(2)
            continue

        usage = resp.usage
        in_tok = getattr(usage, "input_tokens", 0)
        out_tok = getattr(usage, "output_tokens", 0)
        cached_in = getattr(usage, "cache_read_input_tokens", 0) or 0
        cached_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cost = (
            (in_tok - cached_in - cached_create) * 1.0 / 1_000_000
            + out_tok * 5.0 / 1_000_000
            + cached_in * 0.1 / 1_000_000
            + cached_create * 1.25 / 1_000_000
        )
        total_cost += cost

        raw = ""
        for block in resp.content:
            if hasattr(block, "text"):
                raw += block.text
        button, reason = parse_button(raw)
        if button is None:
            print(f"  t{turn}: parse fail: {raw[:60]!r}")
            mgba.tap("A", frames=10); time.sleep(0.3)
            continue

        mgba.tap(button, frames=18)
        time.sleep(0.4)
        g_after = state_mod.read_state(mgba)
        if g_after.saveblock1_valid:
            after_pos = (g_after.x, g_after.y, g_after.map_group, g_after.map_num)
        else:
            after_pos = ("?", "?", "?", "?")
        print(f"  t{turn}: {button} -> pos=({g_after.x},{g_after.y}) m=({g_after.map_group},{g_after.map_num}) "
              f":: {reason} (${cost:.4f}, total ${total_cost:.4f})")
        history.append((button, (g_after.x, g_after.y, g_after.map_group, g_after.map_num)))

    print(f"max_turns reached, total cost ${total_cost:.4f}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
