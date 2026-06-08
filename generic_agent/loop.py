"""Main loop.

1 turn = Brain LLM call + execute returned actions.

Cost saver: skip-think micro-loop. After a Brain turn, we step a few
frames and re-screenshot. If the new screenshot is byte-identical to
the one we just sent (cutscene / dialogue waiting), we auto-press A
WITHOUT another Brain call. Brain is invoked again only when the
screen actually changes OR we have auto-pressed A too many times in
a row.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import brain, config, memory, state as state_mod
from .io import EmulatorError, MGBAClient

OPUS_INPUT_PER_MTOK = 5.0
OPUS_OUTPUT_PER_MTOK = 25.0
OPUS_CACHE_READ_PER_MTOK = 0.5
OPUS_CACHE_WRITE_PER_MTOK = 6.25


@dataclass
class Costs:
    total_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    brain_calls: int = 0
    auto_a_presses: int = 0

    def add(self, t: brain.BrainTurn) -> None:
        self.brain_calls += 1
        self.input_tokens += t.input_tokens
        self.output_tokens += t.output_tokens
        self.cache_read_tokens += t.cache_read_tokens
        self.cache_write_tokens += t.cache_creation_tokens

        billed_input = max(0, t.input_tokens)
        usd = (
            billed_input * OPUS_INPUT_PER_MTOK / 1_000_000
            + t.output_tokens * OPUS_OUTPUT_PER_MTOK / 1_000_000
            + t.cache_read_tokens * OPUS_CACHE_READ_PER_MTOK / 1_000_000
            + t.cache_creation_tokens
            * OPUS_CACHE_WRITE_PER_MTOK / 1_000_000
        )
        self.total_usd += usd


@dataclass
class LoopState:
    turn: int = 0
    history: list[str] = field(default_factory=list)
    costs: Costs = field(default_factory=Costs)
    last_screen_hash: str = ""
    consecutive_auto_a: int = 0
    recent_brain_hashes: list[str] = field(default_factory=list)
    stuck_streak: int = 0
    map_history: list[tuple[int, int, int, int]] = field(default_factory=list)
    prev_map_key: tuple[int, int] | None = None
    same_map_streak: int = 0
    rescue_fired_for: tuple[tuple[int, int], int] | None = None


def screen_hash(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def take_screenshot(client: MGBAClient, turn: int) -> Path:
    p = config.SCREENSHOT_DIR / f"turn_{turn:05d}.png"
    client.screenshot(p)
    time.sleep(0.15)
    return p


def execute_actions(
    client: MGBAClient,
    actions: list[brain.BrainAction],
    state: LoopState,
    halt_on_change: bool = True,
) -> str:
    """Run each Brain-issued action. Stops early if screen changes
    unexpectedly mid-sequence (only when batched press_buttons).
    Returns a one-line summary for history.
    """
    if not actions:
        return "no-op"

    summaries: list[str] = []
    for action in actions:
        if action.tool == "press_buttons":
            seq = action.args.get("sequence", [])
            for idx, item in enumerate(seq):
                button = item.get("button", "A")
                frames = int(item.get("frames", 15))
                try:
                    client.tap(button, frames=frames)
                except (EmulatorError, ValueError) as exc:
                    summaries.append(f"press_buttons fail: {exc}")
                    return " | ".join(summaries)
                time.sleep(max(0.02, frames / 60.0 + 0.05))
                if halt_on_change and len(seq) > 1 and idx < len(seq) - 1:
                    pass
            summaries.append(
                "press " + ",".join(
                    str(s.get("button", "?")) for s in seq
                )
            )

        elif action.tool == "wait":
            frames = int(action.args.get("frames", 30))
            time.sleep(max(0.05, frames / 60.0))
            summaries.append(f"wait {frames}")

        elif action.tool == "record_observation":
            note = str(action.args.get("note", "")).strip()
            if note:
                memory.append_note(note, state.turn)
                summaries.append(f"note: {note[:60]}")

        else:
            summaries.append(f"unknown tool: {action.tool}")

    return " | ".join(summaries)


def run(max_turns: int, budget_usd: float | None = None) -> int:
    config.ensure_runtime_dirs()
    client = MGBAClient()
    if not client.ping():
        print(
            "[FAIL] mGBA port 8895 unreachable. "
            "See generic_agent/STARTUP.md."
        )
        return 1

    state = LoopState()
    print(
        f"[start] model={config.MODEL_BRAIN} max_turns={max_turns} "
        f"budget=${budget_usd if budget_usd else 'unbounded'}"
    )

    try:
        while state.turn < max_turns:
            state.turn += 1
            shot = take_screenshot(client, state.turn)
            h = screen_hash(shot)

            if (
                h == state.last_screen_hash
                and state.consecutive_auto_a < 4
            ):
                client.tap("A", frames=10)
                state.consecutive_auto_a += 1
                state.costs.auto_a_presses += 1
                state.history.append("auto-A (screen unchanged)")
                state.last_screen_hash = h
                if state.turn % 10 == 0:
                    print(
                        f"  turn {state.turn}: auto-A "
                        f"(${state.costs.total_usd:.4f})"
                    )
                continue

            state.consecutive_auto_a = 0
            state.last_screen_hash = h

            state.recent_brain_hashes.append(h)
            if len(state.recent_brain_hashes) > 8:
                state.recent_brain_hashes.pop(0)
            dup_count = state.recent_brain_hashes.count(h)
            if dup_count >= 3:
                state.stuck_streak += 1
            else:
                state.stuck_streak = 0

            gs = state_mod.read_state(client)
            map_key = (gs.map_group, gs.map_num)
            if gs.saveblock1_valid:
                if state.prev_map_key == map_key:
                    state.same_map_streak += 1
                else:
                    state.same_map_streak = 0
                state.prev_map_key = map_key
                state.map_history.append(
                    (gs.map_group, gs.map_num, gs.x, gs.y)
                )
                if len(state.map_history) > 5:
                    state.map_history.pop(0)
            summary_parts = [gs.short()]
            if state.same_map_streak >= 10:
                summary_parts.append(
                    f"same map for {state.same_map_streak} Brain turns"
                )
            state_summary = " | ".join(summary_parts)

            rescue_threshold = 30
            rescue_bracket = state.same_map_streak // rescue_threshold
            rescue_key = (map_key, rescue_bracket)
            rescue_active = (
                state.same_map_streak >= rescue_threshold
                and state.rescue_fired_for != rescue_key
            )
            if rescue_active:
                state.rescue_fired_for = rescue_key

            try:
                turn_data = brain.call_brain(
                    screenshot=shot,
                    turn=state.turn,
                    history=state.history,
                    state_summary=state_summary,
                    stuck_streak=state.stuck_streak,
                    rescue_active=rescue_active,
                )
            except Exception as exc:
                print(f"[FAIL] brain call: {exc!r}")
                return 2

            state.costs.add(turn_data)
            summary = execute_actions(client, turn_data.actions, state)
            state.history.append(summary)

            memory.append_run_log({
                "turn": state.turn,
                "actions": [
                    {"tool": a.tool, "args": a.args}
                    for a in turn_data.actions
                ],
                "summary": summary,
                "in_tok": turn_data.input_tokens,
                "out_tok": turn_data.output_tokens,
                "cache_r": turn_data.cache_read_tokens,
                "cache_w": turn_data.cache_creation_tokens,
                "usd": round(state.costs.total_usd, 4),
            })

            stuck_tag = (
                f" stuck={state.stuck_streak}"
                if state.stuck_streak > 0 else ""
            )
            print(
                f"  turn {state.turn}: {summary} | "
                f"in={turn_data.input_tokens} "
                f"out={turn_data.output_tokens} "
                f"cR={turn_data.cache_read_tokens} "
                f"cW={turn_data.cache_creation_tokens}"
                f"{stuck_tag} | "
                f"total=${state.costs.total_usd:.4f}"
            )

            if budget_usd and state.costs.total_usd >= budget_usd:
                print(
                    f"[stop] budget ${budget_usd} reached "
                    f"(actual ${state.costs.total_usd:.4f})"
                )
                break

    except KeyboardInterrupt:
        print("\n[stop] keyboard interrupt")

    print(
        f"[end] turns={state.turn} brain_calls={state.costs.brain_calls} "
        f"auto_A={state.costs.auto_a_presses} "
        f"input_tok={state.costs.input_tokens} "
        f"output_tok={state.costs.output_tokens} "
        f"cache_read={state.costs.cache_read_tokens} "
        f"cache_write={state.costs.cache_write_tokens} "
        f"total=${state.costs.total_usd:.4f}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--turns", type=int, default=100,
        help="max Brain turns (default 100)",
    )
    parser.add_argument(
        "--budget", type=float, default=None,
        help="USD budget; stop when reached",
    )
    args = parser.parse_args()
    return run(max_turns=args.turns, budget_usd=args.budget)


if __name__ == "__main__":
    sys.exit(main())
