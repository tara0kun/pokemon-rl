"""Opus-driven navigation for Pokemon Emerald Birch event.

Past attempts failed because rescue_brain calls each frame independently
(no memory of "I already tried Up 39 times here"). This script keeps a
history and forwards it so Opus can reason about WHY past attempts didn't
work and try something different.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import anthropic

from generic_agent import config, io as io_mod, preprocess, state as state_mod


SYSTEM_PROMPT = (
    "You guide a Pokemon Emerald automated agent to get its starter Pokemon "
    "from Professor Birch's lab. You are looking at the GBA screen (240x160 "
    "RGB). The agent is currently lost in the early game with no Pokemon.\n\n"
    "KEY EMERALD FACTS:\n"
    "- Birch's lab is in Littleroot Town\n"
    "- To trigger starter event: walk north from Littleroot through Route 101, "
    "Birch will be attacked by Zigzagoon, you choose a Pokeball to fight\n"
    "- If party_count > 0 the event is done\n\n"
    "Your reply MUST be a JSON object: "
    '{"buttons": ["<sequence of 1-8 buttons>"], "reason": "<short reason>"}\n'
    "Buttons: A B Up Down Left Right Start Select. "
    "Look at the screenshot CAREFULLY. Note NPC sprites, walls, doors, "
    "dialog text. If past attempts of a certain direction failed, try a "
    "DIFFERENT direction. Walls/NPCs block movement. Dialog boxes need A."
)


def main() -> int:
    api_key = config.load_api_key()
    if not api_key:
        print("[err] no ANTHROPIC_API_KEY")
        return 1
    cl = anthropic.Anthropic(api_key=api_key)
    mgba = io_mod.MGBAClient()

    history: list[dict] = []
    total_in = total_out = 0
    max_calls = 25
    snap_dir = config.DATASET_DIR / "opus_nav"
    snap_dir.mkdir(parents=True, exist_ok=True)

    for i in range(max_calls):
        g = state_mod.read_state(mgba)
        if g.party_count >= 1:
            print(f"\n=== STARTER ACQUIRED party={g.party_count} ===")
            break
        shot = snap_dir / f"opus_{i:02d}.png"
        mgba.screenshot(shot)
        time.sleep(0.2)

        image_block, image_bytes, fhash = preprocess.png_path_to_jpeg_block(shot)

        hist_text = ""
        if history:
            recent = history[-8:]
            lines = []
            for h in recent:
                lines.append(
                    f'  before pos=({h["before_x"]},{h["before_y"]}) m=({h["before_g"]},{h["before_n"]}) '
                    f'-> pressed {h["btns"]} -> after pos=({h["after_x"]},{h["after_y"]}) '
                    f'm=({h["after_g"]},{h["after_n"]}) (moved={h["moved"]})'
                )
            hist_text = "Past attempts:\n" + "\n".join(lines) + "\n\n"

        user_text = (
            f"{hist_text}"
            f"Current state: pos=({g.x},{g.y}) map=({g.map_group},{g.map_num}) "
            f"party={g.party_count} hp={g.party0_hp}/{g.party0_max_hp} "
            f"flags={g.total_event_flags}.\n"
            "Look at the screen and choose a sequence of 1-8 buttons to make "
            "PROGRESS toward getting the starter Pokemon. If past attempts in "
            "a direction failed, try a DIFFERENT approach."
        )

        try:
            resp = cl.messages.create(
                model="claude-opus-4-8",
                max_tokens=512,
                system=SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": [image_block, {"type": "text", "text": user_text}],
                    }
                ],
            )
        except Exception as exc:
            print(f"[err call {i}]: {exc!r}")
            break

        total_in += resp.usage.input_tokens
        total_out += resp.usage.output_tokens

        text = ""
        for block in resp.content:
            if hasattr(block, "text"):
                text += block.text

        # Parse JSON
        text_clean = text.strip()
        if text_clean.startswith("```"):
            text_clean = text_clean.split("```")[1]
            if text_clean.startswith("json"):
                text_clean = text_clean[4:]
        text_clean = text_clean.strip()
        try:
            parsed = json.loads(text_clean)
            buttons = parsed.get("buttons", [])
            reason = parsed.get("reason", "")
        except json.JSONDecodeError:
            print(f"[err call {i}] failed parse: {text[:100]}")
            continue

        if not buttons:
            print(f"[err call {i}] empty buttons")
            continue

        cost = (resp.usage.input_tokens * 5.0 + resp.usage.output_tokens * 25.0) / 1_000_000
        print(
            f"#{i}: pos=({g.x},{g.y}) m=({g.map_group},{g.map_num}) -> "
            f"{buttons} :: {reason[:60]} [${cost:.4f}]"
        )

        # Execute buttons
        before = (g.x, g.y, g.map_group, g.map_num)
        for btn in buttons[:8]:
            try:
                mgba.tap(str(btn).strip(), frames=15)
                time.sleep(0.3)
            except Exception:
                break
        after_g = state_mod.read_state(mgba)
        history.append({
            "before_x": before[0], "before_y": before[1],
            "before_g": before[2], "before_n": before[3],
            "btns": buttons,
            "after_x": after_g.x, "after_y": after_g.y,
            "after_g": after_g.map_group, "after_n": after_g.map_num,
            "moved": (after_g.x, after_g.y, after_g.map_group, after_g.map_num) != before,
        })

    total_cost = (total_in * 5.0 + total_out * 25.0) / 1_000_000
    print(f"\n=== DONE total_cost=${total_cost:.4f} (in={total_in} out={total_out} tokens) ===")
    g = state_mod.read_state(mgba)
    print(f"final: pos=({g.x},{g.y}) map=({g.map_group},{g.map_num}) party={g.party_count} flags={g.total_event_flags}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
