"""Autonomous HM-teach sub-task (Rock Smash chain, docs/PLAN_lavaridge_flannery).

Teaching an HM is a multi-step bag/party MENU operation with no precedent in the
per-turn rule loop, and a fixed button script is fragile (START-menu order and the
bag/party cursor positions carry over within a session). So we drive it with the
Haiku VLM brain in a bounded one-shot loop, and gate on a hard RAM success check:
a party Pokemon knows MOVE_ROCK_SMASH (249). Cheap (~20 Haiku calls, one time) and
robust to menu-cursor state. Recommended by the architect (Fable) design.
"""
from __future__ import annotations

import time
from typing import Callable

from . import config, rescue_brain, state as state_mod
from .io import EmulatorError, MGBAClient

MOVE_ROCK_SMASH = 249
_MAX_STEPS = 40
_STEP_SLEEP = 0.7
_SCREEN = config.MEMORY_DIR / "hm_teach_screen.png"

SYSTEM_PROMPT_TEACH_HM = (
    "You are operating Pokemon Emerald menus to TEACH the HM move ROCK SMASH to a "
    "party Pokemon. Reply with ONLY a JSON object: "
    '{"button": "<A|B|Up|Down|Left|Right>", "reason": "<short>"}. '
    "The START menu is ALREADY OPEN when you begin — do NOT try to open it. "
    "From the open menu the path is: choose BAG (press A on BAG), switch to the "
    "TMs & HMs pocket (press Right/Left to change pocket), scroll to HM06 ROCK "
    "SMASH, press A, choose USE, then on the party screen move the cursor to a "
    "POOCHYENA and press A to teach it. "
    "HARD RULES: (1) The START menu should ALREADY be open — press A when the "
    "arrow is on BAG. Only if you see PURELY the overworld field (your character "
    "on the map) with NO menu list should you press Start ONCE to open it. NEVER "
    "press Start while any menu, list, or dialog box is visible — it closes it. "
    "(2) NEVER teach it to GROVYLE (the grass starter) — if a Summary or 'teach to "
    "GROVYLE?' screen appears, press B to cancel and pick a POOCHYENA instead. "
    "(3) On the party list, confirm the cursor is on a POOCHYENA before A. "
    "(4) If asked 'Forget a move?' / which move to delete, pick the FIRST move. "
    "(5) LOOK at the screenshot and describe the CURRENT screen in your reason "
    "(overworld / start-menu / bag / party-list / dialog) so you don't repeat a "
    "button that isn't working. "
    "NAVIGATION FACTS (this game): "
    "- The SELECTED bag item is the one with the ► arrow AND whose description "
    "shows in the bottom-left box. If the arrow is on CLOSE BAG, press Up to "
    "reach the items (HM06 ROCK SMASH sits just above CLOSE BAG). "
    "- On the party list, slot0 (GROVYLE) is the big box on the LEFT; the "
    "POOCHYENA are the RIGHT column. From GROVYLE press RIGHT to enter the right "
    "column (Down on the left just goes to CANCEL). Then Up/Down to pick a "
    "POOCHYENA. "
    "- On a YES/NO prompt, A confirms YES."
)


def _read(client: MGBAClient):
    try:
        return state_mod.read_state(client)
    except EmulatorError:
        return None


def _knows_rock_smash(client: MGBAClient) -> bool:
    gs = _read(client)
    return bool(gs and gs.knows_rock_smash)


def run_teach_subtask(
    client: MGBAClient, log: Callable[[str], None] | None = None,
) -> bool:
    """Teach Rock Smash to the Poochyena HM slave via the VLM. Returns True once a
    party Pokemon knows move 249. Blocks (~<120s) — call as a one-shot sub-task,
    NOT per turn. Safe to re-enter (no-op if already known)."""
    def _log(m: str) -> None:
        if not log:
            return
        try:
            log(m)
        except (UnicodeEncodeError, OSError):
            # Haiku's reason can contain non-console-codepage chars; a logging
            # crash must never abort the teach. Fall back to an ASCII-safe form.
            try:
                log(m.encode("ascii", "replace").decode("ascii"))
            except Exception:  # noqa: BLE001 — logging must not raise
                pass

    if _knows_rock_smash(client):
        return True

    gs0 = _read(client)
    slot0_before = list(gs0.party_moves[0]) if gs0 and gs0.party_moves else []

    # Deterministic opener: raise the START menu, then let the VLM navigate.
    try:
        client.tap("Start", frames=15)
    except EmulatorError:
        return False
    time.sleep(0.6)

    last_btns: list[str] = []
    for step in range(_MAX_STEPS):
        if _knows_rock_smash(client):
            for _ in range(4):  # unwind any remaining menus back to overworld
                try:
                    client.tap("B", frames=12)
                except EmulatorError:
                    break
                time.sleep(0.3)
            _log(f"teach_hm: SUCCESS (step {step})")
            return True
        # NB (verified live 07-19): the START menu is an overworld OVERLAY —
        # game_cb2 stays == the overworld callback while it is open. So cb2 CANNOT
        # tell "field, no menu" from "field, Start-menu open"; the old guard
        # (cb2==overworld -> press Start) toggled the just-opened menu shut every
        # step (the earlier belief that "cb2 flips instantly" was wrong). Fully
        # vision-driven now: the opener above raised the menu, we always screenshot
        # and let the VLM read the real screen, and the toggle-breaker below blocks
        # a mistaken 2nd Start. (The POKEMON party screen does change cb2, but we
        # no longer depend on that to drive the menu.)
        try:
            client.screenshot(_SCREEN)
        except EmulatorError:
            time.sleep(0.5)
            continue
        user_text = (
            f"Step {step}/{_MAX_STEPS}. Recent buttons: {last_btns[-6:]}. "
            "The menu is OPEN. Party: slot0=GROVYLE (never pick), "
            "slot1/2/3=POOCHYENA (TEACH TARGET). "
            "Teach ROCK SMASH to a POOCHYENA. What is the next single button?"
        )
        try:
            resp, _, _ = rescue_brain._call_haiku(
                _SCREEN, SYSTEM_PROMPT_TEACH_HM, user_text,
            )
            raw = resp.content[0].text if getattr(resp, "content", None) else ""
            btn, reason = rescue_brain._parse_response(raw)
        except Exception as exc:  # noqa: BLE001 — API/parse failure -> back out
            btn, reason = "B", f"haiku-err:{exc}"
        # Start keeps cb2=overworld even with the menu open, so we can't read the
        # menu state from RAM. Block only a 2nd consecutive Start (which would
        # toggle the just-opened menu shut); a single Start is allowed so the VLM
        # can reopen the menu if it truly closed. If the menu is in fact open, A
        # advances toward the party screen.
        if btn == "Select":
            btn, reason = "B", f"Select-forbidden(was:{reason[:24]})"
        elif btn == "Start" and last_btns and last_btns[-1] == "Start":
            btn, reason = "A", f"Start-toggle-guard(was:{reason[:24]})"
        _log(f"teach_hm[{step}]: {btn} ({reason})")
        try:
            client.tap(btn, frames=12)
        except EmulatorError:
            break
        last_btns.append(btn)
        time.sleep(_STEP_SLEEP)

    # Give up: unwind menus so the outer loop resumes cleanly.
    for _ in range(6):
        try:
            client.tap("B", frames=12)
        except EmulatorError:
            break
        time.sleep(0.3)
    ok = _knows_rock_smash(client)
    if ok and slot0_before:
        gs1 = _read(client)
        if gs1 and gs1.party_moves and list(gs1.party_moves[0]) != slot0_before:
            _log("teach_hm: WARNING slot0 (Grovyle) moves changed")
    _log(f"teach_hm: {'SUCCESS' if ok else 'gave up'} after {_MAX_STEPS} steps")
    return ok
