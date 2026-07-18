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
_MAX_STEPS = 22              # a clean heal is ~7 steps; cap wasted budget on a miss
_STEP_SLEEP = 0.7
_SCREEN = config.MEMORY_DIR / "field_heal_screen.png"

SYSTEM_PROMPT_HEAL = (
    "You are operating Pokemon Emerald menus to HEAL your lead Pokemon "
    "(SCEPTILE, the grass starter) with a SUPER POTION. Reply with ONLY a JSON "
    'object: {"button": "<A|B|Up|Down|Left|Right>", "reason": "<short>"}. '
    "The START menu is ALREADY OPEN when you begin — do NOT try to open it. "
    "The path: choose BAG (A on BAG) -> the ITEMS pocket (press Left/Right to "
    "change pocket if you are not on ITEMS) -> SUPER POTION is NOT the first item; "
    "press Down to scroll the list until the highlighted row shows quantity x6 "
    "(SUPER POTION — you hold exactly 6; every other item shows x1) -> A -> choose "
    "USE -> the party screen opens with the cursor ALREADY on SCEPTILE (top-left, "
    "the only Pokemon with a non-full HP bar): press A immediately, do NOT move the "
    "cursor. After the 'HP restored' message, if SCEPTILE is still not full you may "
    "use another; otherwise back out. "
    "HARD RULES: (1) The START menu (a vertical list POKEDEX/POKEMON/BAG/POKENAV/"
    "SAVE/OPTION/EXIT) should ALREADY be open — press A when the arrow is on BAG. "
    "BAG is the 3rd row (below POKEDEX and POKEMON, ABOVE POKENAV). If pressing A "
    "opened a HOENN MAP / CONDITION / MATCH CALL submenu, you were on POKENAV, not "
    "BAG: press B to go back, then Up once to land on BAG, then A. "
    "Only if you see PURELY the overworld field (your character on the map) with "
    "NO menu list, press Start ONCE to open it. NEVER press Start while any menu, "
    "list, or dialog box is visible — it would close it. (2) The 'It won't have "
    "any effect' message means you used a WRONG item (a x1 item, not SUPER "
    "POTION) — press B back to the ITEMS pocket and press Down until the "
    "highlighted row shows x6, then A. Do NOT keep pressing A on the top item. "
    "The x6 row is the ONLY correct item. (3) On a "
    "YES/NO, A confirms YES. (4) If the arrow is on CLOSE BAG press Up to reach "
    "the items. (5) LOOK at the screenshot and describe the CURRENT screen in "
    "your reason (overworld / start-menu / bag / party-list / dialog) so you do "
    "not repeat a button that is not working."
)


def _read(client: MGBAClient):
    try:
        return state_mod.read_state(client)
    except EmulatorError:
        return None


def _has_heal(client: MGBAClient) -> bool:
    """True if the bag holds a restore. bag_heal_qty flickers to 0 under
    button-press load (a slot-id read returning garbage, like the badge/flag
    flicker), so a single 0 read once false-stopped field_heal ("bag empty" with
    6 Super Potions actually present, 07-19). Confirm a 0 with two more reads."""
    for _ in range(3):
        gs = _read(client)
        if gs and gs.bag_heal_qty > 0:
            return True
    return False


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

    if not _has_heal(client):
        return False  # nothing to heal with (confirmed, flicker-robust)
    if _healed(client):
        return True

    # Clear any overworld dialog before opening the menu: field_heal can fire the
    # instant the agent bumps an NPC, and a dialog box swallows the Start opener
    # (a Jagged Pass biker's "my bicycle bounced..." line left the VLM floundering
    # between the dialog and a mis-navigated START menu, 07-19). B advances/closes
    # a dialog and is a harmless no-op in the clean overworld, so pre-tapping it is
    # always safe here.
    for _ in range(2):
        try:
            client.tap("B", frames=10)
        except EmulatorError:
            return False
        time.sleep(0.35)
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
        # bag emptied mid-way (used the last one but still < 90%) -> stop. Use
        # the flicker-robust check so a load-induced 0 read doesn't false-stop.
        if not _has_heal(client):
            for _ in range(4):
                try:
                    client.tap("B", frames=12)
                except EmulatorError:
                    break
                time.sleep(0.3)
            _log("field_heal: bag empty, stop")
            return _healed(client)
        # NB: the START menu is an overworld OVERLAY — game_cb2 stays == the
        # overworld callback while it is open (verified live 07-19: after one
        # Start, the POKEDEX/BAG/... list is on screen yet cb2 is unchanged). So
        # cb2 CANNOT tell "field, no menu" from "field, Start-menu open"; the old
        # guard (cb2==overworld -> press Start) toggled the menu shut every step
        # and never healed. This sub-task is fully vision-driven: always screenshot
        # and let the VLM read the actual screen. The opener above already opened
        # the menu; the toggle-breaker below stops a mistaken 2nd Start.
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
        if btn == "Select":
            btn, reason = "B", f"Select-forbidden(was:{reason[:24]})"
        elif btn == "Start" and last_btns and last_btns[-1] == "Start":
            # Start keeps cb2=overworld even with the menu open, so we can't read
            # the menu state from RAM; block a 2nd consecutive Start so we never
            # toggle the just-opened menu shut. If the menu is in fact open, A
            # advances into BAG; if it truly failed to open, next step re-Starts.
            btn, reason = "A", f"Start-toggle-guard(was:{reason[:24]})"
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
