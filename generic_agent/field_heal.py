"""Autonomous BETWEEN-battle healing sub-task (H14): use a Super Potion on the
lead in the overworld, so a solo Sceptile survives the Mt.Chimney Team Magma
gauntlet (no PC, back-to-back trainers) and the Lavaridge gym. Mirrors
hm_teach.run_teach_subtask: START opener + cb2 overworld guard + Haiku VLM loop
gated on a hard RAM check (lead HP fraction). NOT mid-battle — it fires only when
out of battle, between trainers, so it never touches the battle_move machinery.
"""
from __future__ import annotations

import time
from typing import Callable

from . import config, rescue_brain, state as state_mod
from .io import EmulatorError, MGBAClient

_HEAL_TARGET_FRAC = 0.90     # done once the lead is healed to >= this
_MAX_STEPS = 40
_STEP_SLEEP = 0.7
_SCREEN = config.MEMORY_DIR / "field_heal_screen.png"

SYSTEM_PROMPT_HEAL = (
    "You are operating Pokemon Emerald menus to HEAL your lead Pokemon "
    "(SCEPTILE, the grass starter) with a SUPER POTION. Reply with ONLY a JSON "
    'object: {"button": "<A|B|Up|Down|Left|Right>", "reason": "<short>"}. '
    "The START menu is ALREADY OPEN when you begin — do NOT try to open it. "
    "The path: choose BAG (A on BAG) -> the ITEMS pocket (press Left/Right to "
    "change pocket if you are not on ITEMS) -> move the cursor to SUPER POTION "
    "-> A -> choose USE -> on the party screen put the cursor on SCEPTILE (the "
    "big box on the LEFT, slot 0) and press A. After the 'HP restored' message, "
    "if SCEPTILE is still not full you may use another; otherwise back out. "
    "HARD RULES: (1) NEVER output 'Start' — it CLOSES the menu and breaks the "
    "task. Never emit Start. (2) Use SUPER POTION only, on SCEPTILE only (the "
    "LEFT big box); do NOT waste it on the weak POOCHYENA/LOTAD. (3) On a YES/NO, "
    "A confirms YES. (4) If the arrow is on CLOSE BAG press Up to reach the "
    "items. (5) LOOK at the screenshot and describe the CURRENT screen in your "
    "reason (overworld / start-menu / bag / party-list / dialog) so you do not "
    "repeat a button that is not working."
)


def _read(client: MGBAClient):
    try:
        return state_mod.read_state(client)
    except EmulatorError:
        return None


def _healed(client: MGBAClient) -> bool:
    gs = _read(client)
    return bool(gs and gs.party0_max_hp > 0
                and gs.party0_hp_frac >= _HEAL_TARGET_FRAC)


def run_heal_subtask(
    client: MGBAClient, log: Callable[[str], None] | None = None,
) -> bool:
    """Heal the lead with Super Potion(s) via the VLM. Returns True once the lead
    is >= 90% HP (or gives up). Blocks (~<120s); call as a one-shot sub-task, not
    per turn. Safe to re-enter (no-op if already healed / bag empty)."""
    def _log(m: str) -> None:
        if not log:
            return
        try:
            log(m)
        except (UnicodeEncodeError, OSError):
            try:
                log(m.encode("ascii", "replace").decode("ascii"))
            except Exception:  # noqa: BLE001 — logging must never abort the heal
                pass

    gs0 = _read(client)
    if gs0 is None or gs0.bag_heal_qty <= 0:
        return False  # nothing to heal with
    if _healed(client):
        return True

    try:
        client.tap("Start", frames=15)
    except EmulatorError:
        return False
    time.sleep(0.6)

    last_btns: list[str] = []
    for step in range(_MAX_STEPS):
        if _healed(client):
            for _ in range(4):
                try:
                    client.tap("B", frames=12)
                except EmulatorError:
                    break
                time.sleep(0.3)
            _log(f"field_heal: SUCCESS (step {step})")
            return True
        gs = _read(client)
        # bag emptied mid-way (used the last one but still < 90%) -> stop.
        if gs and gs.bag_heal_qty <= 0:
            for _ in range(4):
                try:
                    client.tap("B", frames=12)
                except EmulatorError:
                    break
                time.sleep(0.3)
            _log("field_heal: bag empty, stop")
            return _healed(client)
        # Menu-state guard (hm_teach pattern): overworld -> (re)open with Start;
        # a menu is open -> Start forbidden, the VLM drives.
        in_overworld = bool(gs and gs.game_cb2 in state_mod.CB2_OVERWORLD_SET)
        if in_overworld:
            _log(f"field_heal[{step}]: Start (reopen-menu; cb2=overworld)")
            try:
                client.tap("Start", frames=15)
            except EmulatorError:
                break
            last_btns.append("Start")
            time.sleep(_STEP_SLEEP)
            continue
        try:
            client.screenshot(_SCREEN)
        except EmulatorError:
            time.sleep(0.5)
            continue
        user_text = (
            f"Step {step}/{_MAX_STEPS}. Recent buttons: {last_btns[-6:]}. "
            f"Lead HP is about {int(gs.party0_hp_frac * 100) if gs else '?'}%. "
            f"You have {gs.bag_heal_qty if gs else '?'} restores. Heal SCEPTILE "
            "with a SUPER POTION. What is the next single button?"
        )
        try:
            resp, _, _ = rescue_brain._call_haiku(
                _SCREEN, SYSTEM_PROMPT_HEAL, user_text,
            )
            raw = resp.content[0].text if getattr(resp, "content", None) else ""
            btn, reason = rescue_brain._parse_response(raw)
        except Exception as exc:  # noqa: BLE001 — API/parse failure -> back out
            btn, reason = "B", f"haiku-err:{exc}"
        if btn in ("Start", "Select"):
            btn, reason = "B", f"{btn}-forbidden(was:{reason[:32]})"
        _log(f"field_heal[{step}]: {btn} ({reason})")
        try:
            client.tap(btn, frames=12)
        except EmulatorError:
            break
        last_btns.append(btn)
        time.sleep(_STEP_SLEEP)

    for _ in range(6):
        try:
            client.tap("B", frames=12)
        except EmulatorError:
            break
        time.sleep(0.3)
    ok = _healed(client)
    _log(f"field_heal: {'SUCCESS' if ok else 'gave up'} after {_MAX_STEPS} steps")
    return ok
