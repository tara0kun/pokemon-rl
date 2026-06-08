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

from . import config, local_brain, memory, preprocess, state as state_mod
from .io import EmulatorError, MGBAClient


LOCAL_RECOVERY_STREAK = 3
API_RESCUE_STREAK = 8


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
    recovery: local_brain.LocalRecovery = field(
        default_factory=local_brain.LocalRecovery
    )
    in_recovery: bool = False
    costs: AutoCosts = field(default_factory=AutoCosts)


def take_screenshot(client: MGBAClient, turn: int) -> Path:
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


def run(max_turns: int, budget_usd: float | None) -> int:
    config.ensure_runtime_dirs()
    client = MGBAClient()
    if not client.ping():
        print("[FAIL] mGBA port 8895 unreachable. See STARTUP.md")
        return 1

    cache = local_brain.FrameCache()
    state = AutoLoopState()
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
            shot = take_screenshot(client, state.turn)
            arr = preprocess.load_png_as_array(shot)
            fhash = preprocess.frame_hash(arr)

            gs = state_mod.read_state(client)
            map_key = (gs.map_group, gs.map_num)

            if state.last_frame_hash == fhash:
                state.same_hash_streak += 1
            else:
                state.same_hash_streak = 0
                state.in_recovery = False
                state.recovery.reset()
            state.last_frame_hash = fhash

            if state.last_map_key == map_key and gs.saveblock1_valid:
                state.same_map_streak += 1
            else:
                state.same_map_streak = 0
            state.last_map_key = map_key

            decision: local_brain.LocalDecision | None = None
            decision_source = ""

            cached = cache.lookup(fhash, gs.map_group, gs.map_num)
            if cached and not state.in_recovery:
                decision = local_brain.LocalDecision(
                    button=cached.button,
                    frames=cached.frames,
                    source=f"cache(hit={cached.hit_count})",
                )
                state.costs.cache_hits += 1
                decision_source = decision.source

            pos_now = (gs.x, gs.y) if gs.saveblock1_valid else None
            pos_changed = (
                pos_now is not None and state.last_pos is not None
                and pos_now != state.last_pos
            )

            if (
                decision is None
                and state.same_hash_streak < LOCAL_RECOVERY_STREAK
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

            if (
                decision is None
                and state.same_hash_streak >= LOCAL_RECOVERY_STREAK
                and state.same_hash_streak < API_RESCUE_STREAK
            ):
                state.in_recovery = True
                decision = state.recovery.next()
                decision_source = decision.source
                state.costs.recovery_steps += 1

            def _call_brain(kind: str) -> local_brain.LocalDecision | None:
                if not use_rescue:
                    return None
                if budget_usd and state.costs.total_usd >= budget_usd:
                    return None
                from . import rescue_brain as _rb
                try:
                    if kind == "rescue":
                        rb_dec = _rb.call_rescue(
                            screenshot_png=shot,
                            state_summary=gs.short(),
                            same_map_streak=state.same_map_streak,
                        )
                        state.costs.rescue_calls += 1
                    else:
                        rb_dec = _rb.call_navigate(
                            screenshot_png=shot,
                            state_summary=gs.short(),
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

            if decision is None:
                decision = local_brain.LocalDecision(
                    "A", 8, source="fallback"
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
                cache.save()

    except KeyboardInterrupt:
        print("\n[stop] keyboard interrupt")

    cache.save()
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
    args = parser.parse_args()
    return run(max_turns=args.turns, budget_usd=args.budget)


if __name__ == "__main__":
    sys.exit(main())
