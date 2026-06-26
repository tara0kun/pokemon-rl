"""Opus-driven one-shot rescue: push agent through Route 101 to Oldale.

Stops as soon as map_num == 17 (Oldale Town). Caps spend at ~25 calls
($0.20 budget) so a runaway loop can't burn through a lot of money.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import anthropic

from generic_agent import config, io as io_mod, preprocess, state as state_mod


SYSTEM_PROMPT = (
    "You guide a Pokemon Emerald automated agent NORTH from Route 101 to "
    "OLDALE TOWN (map 0,17) so it can heal at the Pokemon Center.\n\n"
    "Current situation: party_count=1 (already have starter Pokemon Lv5). "
    "HP is low (10/19). Agent is bouncing between Littleroot (0,9) and "
    "Route 101 (0,16) but never reaching Oldale.\n\n"
    "KEY FACTS:\n"
    "- Route 101 grass corridor runs SOUTH-NORTH; player must keep pressing "
    "UP through wild grass\n"
    "- Wild Pokemon battles will trigger; when in_battle is true, attack with "
    "A x4 (Tackle is starter's first move and beats most wild Pokemon)\n"
    "- If HP < 6, RUN: Down, Right, A, A\n"
    "- Oldale Town is just past the north grass — keep walking UP past tall "
    "grass tiles until map changes from 0,16 to 0,17\n\n"
    "Reply ONLY a JSON: "
    '{"buttons": ["Up","Up","Up","Up","Up"], "reason": "<<= 15 words>"}.'
)


def main() -> int:
    api_key = config.load_api_key()
    if not api_key:
        print("[err] no ANTHROPIC_API_KEY")
        return 1
    cl = anthropic.Anthropic(api_key=api_key)
    mgba = io_mod.MGBAClient()

    snap_dir = config.DATASET_DIR / "opus_to_oldale"
    snap_dir.mkdir(parents=True, exist_ok=True)
    history: list[dict] = []
    total_in = total_out = 0
    max_calls = 25

    for i in range(max_calls):
        g = state_mod.read_state(mgba)
        if g.map_group == 0 and g.map_num == 17:
            print(f"\n=== REACHED OLDALE TOWN! (after {i} calls) ===")
            break
        shot = snap_dir / f"oldale_{i:02d}.png"
        mgba.screenshot(shot)
        time.sleep(0.2)

        img_block, _b, _h = preprocess.png_path_to_jpeg_block(shot)

        hist_text = ""
        if history:
            lines = []
            for h in history[-5:]:
                lines.append(
                    f"  m=({h['m1']}) -> {h['btns']} -> m=({h['m2']}) moved={h['moved']}"
                )
            hist_text = "Recent: \n" + "\n".join(lines) + "\n\n"

        user_text = (
            f"{hist_text}"
            f"State: pos=({g.x},{g.y}) map=({g.map_group},{g.map_num}) "
            f"hp={g.party0_hp}/{g.party0_max_hp} in_battle={g.in_battle}.\n"
            "Send 4-8 buttons to push agent toward Oldale Town (map 0,17)."
        )

        try:
            resp = cl.messages.create(
                model="claude-opus-4-8",
                max_tokens=256,
                system=SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            img_block,
                            {"type": "text", "text": user_text},
                        ],
                    }
                ],
            )
        except Exception as exc:
            print(f"[err {i}] {exc!r}")
            break

        total_in += resp.usage.input_tokens
        total_out += resp.usage.output_tokens

        text = ""
        for block in resp.content:
            if hasattr(block, "text"):
                text += block.text
        clean = text.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        clean = clean.strip()
        try:
            data = json.loads(clean)
            buttons = [str(b).strip() for b in data.get("buttons", [])]
            reason = str(data.get("reason", ""))[:60]
        except Exception:
            print(f"[err {i}] parse: {text[:80]}")
            continue

        cost = (resp.usage.input_tokens * 5.0 + resp.usage.output_tokens * 25.0) / 1_000_000
        print(
            f"#{i}: pos=({g.x},{g.y}) m=({g.map_group},{g.map_num}) "
            f"hp={g.party0_hp}/{g.party0_max_hp} -> {buttons[:6]} "
            f":: {reason} [${cost:.4f}]"
        )

        before_m = (g.map_group, g.map_num)
        before_pos = (g.x, g.y)
        for btn in buttons[:8]:
            if btn not in {"A","B","Up","Down","Left","Right","Start","Select"}:
                continue
            try:
                mgba.tap(btn, frames=15)
                time.sleep(0.3)
            except Exception:
                break
        after_g = state_mod.read_state(mgba)
        history.append({
            "m1": f"{before_m[0]},{before_m[1]}",
            "btns": buttons[:6],
            "m2": f"{after_g.map_group},{after_g.map_num}",
            "moved": (before_pos[0], before_pos[1]) != (after_g.x, after_g.y)
                    or before_m != (after_g.map_group, after_g.map_num),
        })

    total_cost = (total_in * 5.0 + total_out * 25.0) / 1_000_000
    g_final = state_mod.read_state(mgba)
    print(
        f"\n=== END party={g_final.party_count} pos=({g_final.x},{g_final.y}) "
        f"map=({g_final.map_group},{g_final.map_num}) flags={g_final.total_event_flags} ==="
    )
    print(f"Total Opus cost: ${total_cost:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
