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
_COUNTER_STAND = (3, 3)      # walkable tile the player buys from (clerk at (1,3)).
# Gen3 marts share the interior: RustboroCity_Mart (11,7) has the same clerk
# (1,3) / counter column x=2 geometry (map.json-verified 07-26), so the same
# walk-in works for the Poke Ball purchase.
_TARGET_HEAL = 10            # buy up to this many restores (money-permitting)
_TARGET_BALLS = 10           # Water-catch project: ~4 balls/catch expected
                             # (Marill base rate 190, full HP, plain Poke Ball)
_MAX_STEPS = 45
_STEP_SLEEP = 0.7
_SCREEN = config.MEMORY_DIR / "shop_screen.png"

SYSTEM_PROMPT_SHOP = (
    "You are operating a Pokemon Emerald POKE MART to BUY SUPER POTIONs. Reply "
    'with ONLY a JSON object: {"button": "<A|B|Up|Down|Left|Right>", '
    '"reason": "<short>"}. '
    "You are already standing at the shop counter facing the clerk. Flow: press "
    "A to talk -> a BUY / SELL / SEE YA menu appears (cursor on BUY) -> A -> the "
    "item list opens on the RIGHT. The list order is fixed: (1) POKE BALL $200, "
    "(2) GREAT BALL $600, (3) SUPER POTION $700, (4) ANTIDOTE, ... The little "
    "TRIANGLE ARROW on the left of a row is the cursor = the SELECTED row. It "
    "STARTS on POKE BALL (the TOP row). You want SUPER POTION = the THIRD row. "
    "Move the arrow DOWN to it, then A. A quantity screen (x01 and a price) "
    "opens -> press RIGHT once to raise the count by 10 (auto-capped at what you "
    "can afford) -> A -> on 'That'll be $... OK?' press A (YES) -> on 'Here you "
    "are!'/'Thank you' press A -> back on the list, press B twice to leave. "
    "CRITICAL ITEM-SELECTION RULES: "
    "- NEVER press A while the arrow is on POKE BALL or GREAT BALL (rows 1-2). "
    "Only press A on the item list when the arrow is clearly on the 'SUPER "
    "POTION' text (row 3). "
    "- From the top of the list, SUPER POTION is 2 rows down: press Down, Down. "
    "- If you are NOT SURE which row the arrow is on, press Down (moving toward "
    "SUPER POTION from the top is always safe). Do NOT gamble an A. "
    "- If a 'How many would you like?' dialog names POKE BALL / GREAT BALL / "
    "ANTIDOTE (anything except SUPER POTION), you picked the WRONG item: press B "
    "to cancel, then Down until the arrow is on SUPER POTION, then A. "
    "OTHER HARD RULES: NEVER output 'Start' or 'Select'. NEVER choose SELL. Buy "
    "ONLY SUPER POTION. On any YES/NO, A is YES. If 'not enough money' shows, "
    "press A then Left to lower the count and confirm what you can afford. If "
    "RIGHT does not change the quantity, press Up instead. ALWAYS describe the "
    "CURRENT screen AND which row the arrow is on in your reason. If the screen "
    "is the plain overworld with no menu, press A (you face the clerk)."
)

SYSTEM_PROMPT_BALLS = (
    "You are operating a Pokemon Emerald POKE MART to BUY POKE BALLs. Reply "
    'with ONLY a JSON object: {"button": "<A|B|Up|Down|Left|Right>", '
    '"reason": "<short>"}. '
    "You are already standing at the shop counter facing the clerk. Flow: press "
    "A to talk -> a BUY / SELL / SEE YA menu appears (cursor on BUY) -> A -> the "
    "item list opens on the RIGHT. The little TRIANGLE ARROW on the left of a "
    "row is the cursor = the SELECTED row. It STARTS on the TOP row, and POKE "
    "BALL ($200) is the TOP row in this shop — so from a fresh list, A "
    "immediately selects POKE BALL. A quantity screen (x01 and a price) opens "
    "-> press RIGHT once to raise the count by 10 (auto-capped at what you can "
    "afford) -> A -> on 'That'll be $... OK?' press A (YES) -> on 'Here you "
    "are!'/'Thank you' press A -> back on the list, press B twice to leave. "
    "CRITICAL ITEM-SELECTION RULES: "
    "- Buy ONLY plain POKE BALL. NEVER press A while the arrow is on TIMER "
    "BALL, REPEAT BALL, GREAT BALL, or any other row. "
    "- If the arrow is NOT on POKE BALL, press Up (POKE BALL is the top row; "
    "Up moves toward it and is always safe). Do NOT gamble an A. "
    "- If a 'How many would you like?' dialog names anything except POKE BALL, "
    "you picked the WRONG item: press B to cancel, then Up until the arrow is "
    "on POKE BALL, then A. "
    "OTHER HARD RULES: NEVER output 'Start' or 'Select'. NEVER choose SELL. On "
    "any YES/NO, A is YES. If 'not enough money' shows, press A then Left to "
    "lower the count and confirm what you can afford. If RIGHT does not change "
    "the quantity, press Up instead. ALWAYS describe the CURRENT screen AND "
    "which row the arrow is on in your reason. If the screen is the plain "
    "overworld with no menu, press A (you face the clerk)."
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
    return _run_mart_subtask(
        client,
        qty_of=lambda gs: gs.bag_heal_qty,
        target_qty=_TARGET_HEAL,
        unit_price=700,
        system_prompt=SYSTEM_PROMPT_SHOP,
        label="heal",
        item_label="SUPER POTIONs",
        log=log,
    )


def run_pokeball_subtask(
    client: MGBAClient, log: Callable[[str], None] | None = None,
) -> bool:
    """Buy Poke Balls at the Rustboro Mart (Water-catch project). Same contract
    as run_shop_subtask: True once >= _TARGET_BALLS in the bag, or bought some
    and the wallet dropped below one ball ($200)."""
    return _run_mart_subtask(
        client,
        qty_of=lambda gs: gs.bag_pokeball_count,
        target_qty=_TARGET_BALLS,
        unit_price=200,
        system_prompt=SYSTEM_PROMPT_BALLS,
        label="balls",
        item_label="POKE BALLs",
        log=log,
    )


def _run_mart_subtask(
    client: MGBAClient,
    *,
    qty_of,
    target_qty: int,
    unit_price: int,
    system_prompt: str,
    label: str,
    item_label: str,
    log: Callable[[str], None] | None = None,
) -> bool:
    """Shared mart-purchase loop (walk-in -> clerk -> VLM-driven menus), gated
    on a hard RAM quantity check (qty_of). Parametrized 07-26 from the proven
    Super-Potion flow so the Poke Ball purchase reuses the identical machinery;
    behavior for run_shop_subtask is unchanged."""
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
    start_qty = qty_of(gs0)
    if start_qty >= target_qty:
        return True

    def _done(gs) -> bool:
        if gs is None:
            return False
        if qty_of(gs) >= target_qty:
            return True
        # bought something and can no longer afford another unit -> stop.
        return qty_of(gs) > start_qty and 0 <= gs.money < unit_price

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
            _log(f"shop: SUCCESS {label} {start_qty}->{qty_of(gs)} (step {step})")
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
            f"You have {qty_of(gs) if gs else '?'} already and about "
            f"${gs.money if gs else '?'}. Buy {item_label} (target "
            f"{target_qty}). What is the next single button?"
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
        # Wrong-item A/B oscillation breaker: the VLM mis-reads the list cursor,
        # A-selects POKE BALL, B-cancels the quantity screen, repeat. If the last
        # 4 buttons alternate A/B and nothing has been bought yet, force a Down to
        # walk the cursor down toward SUPER POTION (safe from the top of the list).
        recent = last_btns[-4:]
        oscillating = (
            len(recent) >= 4
            and all(recent[i] in ("A", "B") and recent[i] != recent[i + 1]
                    for i in range(3))
        )
        if oscillating and btn in ("A", "B") and gs and \
                qty_of(gs) == start_qty:
            btn, reason = "Down", f"osc-break(was:{reason[:24]})"
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
    ok = bool(gs and qty_of(gs) > start_qty)
    _log(f"shop: {'bought some' if ok else 'gave up'} "
         f"({label} {start_qty}->{qty_of(gs) if gs else '?'})")
    return _done(gs)
