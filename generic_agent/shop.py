"""Autonomous Poke Mart SHOP sub-task (H14: stock Super Potions for the
Mt.Chimney Team Magma gauntlet, which has no PC and ~10-13 back-to-back
trainers). Buying is a multi-step menu op (clerk dialog -> BUY/SELL multichoice
-> item list -> quantity selector -> YES/NO), so — like hm_teach — we drive it
with the Haiku VLM in a bounded one-shot loop and gate on a hard RAM check
(SUPER_POTION count in the bag). Mirrors hm_teach.run_teach_subtask.

Unlike hm_teach the opener is not START: the agent walks to the counter tile
(3,3) and presses Left+A to talk to the clerk (1,3). The shop menus run inside a
field script so cb2 stays CB2_Overworld — so the overworld cb2 guard is used ONLY
to drive the walk-in, never to inject buttons once the clerk is engaged (that
would corrupt the BUY/SELL multichoice); while parked at (3,3) the VLM drives.
"""
from __future__ import annotations

import time
from typing import Callable

from . import config, rescue_brain, state as state_mod
from .io import EmulatorError, MGBAClient

_MART_MAP = (10, 7)          # MauvilleCity_Mart
_COUNTER_STAND = (3, 3)      # walkable tile the player buys from (clerk at (1,3))
_TARGET_HEAL = 10            # buy up to this many restores (money-permitting)
_MAX_STEPS = 45
_STEP_SLEEP = 0.7
_SCREEN = config.MEMORY_DIR / "shop_screen.png"

SYSTEM_PROMPT_SHOP = (
    "You are operating a Pokemon Emerald POKE MART to BUY SUPER POTIONs. Reply "
    'with ONLY a JSON object: {"button": "<A|B|Up|Down|Left|Right>", '
    '"reason": "<short>"}. '
    "You are already standing at the shop counter facing the clerk. The flow: "
    "press A to talk -> a menu BUY / SELL / SEE YA appears (cursor starts on "
    "BUY) -> press A on BUY -> the item list opens. In the list the order is "
    "POKE BALL, GREAT BALL, SUPER POTION, ... so press Down TWICE to reach SUPER "
    "POTION, then A. A quantity screen shows x01 and a price -> press RIGHT ONCE "
    "to raise it by 10 (the game caps it at what you can afford), then A. On "
    "'That'll be $ ... OK?' press A (YES). On 'Here you are!' / 'Thank you' press "
    "A. When you are back on the item list, press B, then B again (or choose SEE "
    "YA) to leave the shop. "
    "HARD RULES: (1) NEVER output 'Start' or 'Select'. "
    "(2) NEVER choose SELL. (3) Buy ONLY SUPER POTION — never Poke Balls or "
    "anything else; if the cursor is on the wrong item, move to SUPER POTION "
    "first. (4) On any YES/NO, A is YES. (5) If a 'not enough money' message "
    "appears, press A then lower the quantity (Left) and confirm what you can "
    "afford. (6) If RIGHT does not change the quantity, press Up to raise it "
    "instead. (7) LOOK at the screenshot and describe the CURRENT screen in your "
    "reason (overworld / clerk-menu / item-list / quantity / dialog) so you do "
    "not repeat a button that is not working. If the screen looks like the plain "
    "overworld with no menu, press A (you are facing the clerk)."
)


def _read(client: MGBAClient):
    try:
        return state_mod.read_state(client)
    except EmulatorError:
        return None


def _greedy_step(x: int, y: int) -> str | None:
    """One button toward the counter-stand tile (3,3) along the clear Mart
    corridor (door (3,7)/(4,7) -> (3,3)). None if already there."""
    tx, ty = _COUNTER_STAND
    if (x, y) == (tx, ty):
        return None
    if y > ty:
        return "Up"
    if y < ty:
        return "Down"
    if x > tx:
        return "Left"
    if x < tx:
        return "Right"
    return None


def run_shop_subtask(
    client: MGBAClient, log: Callable[[str], None] | None = None,
) -> bool:
    """Buy Super Potions at the Mauville Mart via the VLM. Returns True once the
    bag holds >= _TARGET_HEAL restores, or once money is too low to buy more
    (bought at least one). Blocks (~<120s); call as a one-shot sub-task, not per
    turn. Safe to re-enter (no-op if already stocked)."""
    def _log(m: str) -> None:
        if not log:
            return
        try:
            log(m)
        except (UnicodeEncodeError, OSError):
            try:
                log(m.encode("ascii", "replace").decode("ascii"))
            except Exception:  # noqa: BLE001 — logging must never abort the buy
                pass

    gs0 = _read(client)
    if gs0 is None:
        return False
    start_heal = gs0.bag_heal_qty
    if start_heal >= _TARGET_HEAL:
        return True

    def _done(gs) -> bool:
        if gs is None:
            return False
        if gs.bag_heal_qty >= _TARGET_HEAL:
            return True
        # bought something and can no longer afford a Super Potion -> stop.
        return gs.bag_heal_qty > start_heal and 0 <= gs.money < 700

    last_btns: list[str] = []
    for step in range(_MAX_STEPS):
        gs = _read(client)
        if _done(gs):
            for _ in range(6):  # unwind menus/dialog back to the overworld
                try:
                    client.tap("B", frames=12)
                except EmulatorError:
                    break
                time.sleep(0.3)
            _log(f"shop: SUCCESS heal {start_heal}->{gs.bag_heal_qty} (step {step})")
            return True

        # Walk-in phase: only while on the overworld AND not yet at the counter.
        # cb2==overworld here means no menu is up; a deterministic step is safe.
        in_overworld = bool(gs and gs.game_cb2 in state_mod.CB2_OVERWORLD_SET)
        at_counter = bool(gs and (gs.x, gs.y) == _COUNTER_STAND)
        if gs and in_overworld and not at_counter:
            step_btn = _greedy_step(gs.x, gs.y)
            if step_btn is not None:
                _log(f"shop[{step}]: walk {step_btn} @({gs.x},{gs.y})->{_COUNTER_STAND}")
                try:
                    client.tap(step_btn, frames=12)
                except EmulatorError:
                    break
                last_btns.append(step_btn)
                time.sleep(0.4)
                continue

        # Open phase: parked at the counter, no menu yet -> face clerk + talk.
        if gs and in_overworld and at_counter:
            _log(f"shop[{step}]: open (Left+A @counter)")
            try:
                client.tap("Left", frames=10)
                time.sleep(0.25)
                client.tap("A", frames=12)
            except EmulatorError:
                break
            last_btns.append("A")
            time.sleep(_STEP_SLEEP)
            continue

        # Menu phase: a shop menu/dialog is up -> the VLM drives it.
        try:
            client.screenshot(_SCREEN)
        except EmulatorError:
            time.sleep(0.5)
            continue
        user_text = (
            f"Step {step}/{_MAX_STEPS}. Recent buttons: {last_btns[-6:]}. "
            f"You have {gs.bag_heal_qty if gs else '?'} restores and about "
            f"${gs.money if gs else '?'}. Buy SUPER POTIONs (target "
            f"{_TARGET_HEAL}). What is the next single button?"
        )
        try:
            resp, _, _ = rescue_brain._call_haiku(
                _SCREEN, SYSTEM_PROMPT_SHOP, user_text,
            )
            raw = resp.content[0].text if getattr(resp, "content", None) else ""
            btn, reason = rescue_brain._parse_response(raw)
        except Exception as exc:  # noqa: BLE001 — API/parse failure -> back out
            btn, reason = "B", f"haiku-err:{exc}"
        if btn in ("Start", "Select"):
            btn, reason = "B", f"{btn}-forbidden(was:{reason[:32]})"
        _log(f"shop[{step}]: {btn} ({reason})")
        try:
            client.tap(btn, frames=12)
        except EmulatorError:
            break
        last_btns.append(btn)
        time.sleep(_STEP_SLEEP)

    for _ in range(6):  # give up -> unwind so the outer loop resumes cleanly
        try:
            client.tap("B", frames=12)
        except EmulatorError:
            break
        time.sleep(0.3)
    gs = _read(client)
    ok = bool(gs and gs.bag_heal_qty > start_heal)
    _log(f"shop: {'bought some' if ok else 'gave up'} "
         f"(heal {start_heal}->{gs.bag_heal_qty if gs else '?'})")
    return _done(gs)
