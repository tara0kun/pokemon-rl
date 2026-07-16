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
    "HARD RULES: (1) NEVER output 'Start' — it CLOSES the menu and breaks the task. "
    "Never emit Start under any circumstance. "
    "(2) NEVER teach it to GROVYLE (the grass starter) — if a Summary or 'teach to "
    "GROVYLE?' screen appears, press B to cancel and pick a POOCHYENA instead. "
    "(3) On the party list, confirm the cursor is on a POOCHYENA before A. "
    "(4) If asked 'Forget a move?' / which move to delete, pick the FIRST move. "
    "(5) LOOK at the screenshot and describe the CURRENT screen in your reason "
    "(overworld / start-menu / bag / party-list / dialog) so you don't repeat a "
    "button that isn't working."
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
        # Menu-state guard via gMain.callback2. The VLM has no state memory and
        # kept re-emitting Start, which TOGGLES the menu shut -> every later step
        # ran in the overworld while the model hallucinated a party screen (the
        # whole 145-turn teach failure). So: if the menu is closed (overworld),
        # the only correct move is to (re)open it with Start; while a menu is
        # open, Start is forbidden entirely (it would close it), and the VLM
        # drives. cb2 flips instantly, unlike the stale battle flag.
        gs = _read(client)
        in_overworld = bool(gs and gs.game_cb2 in state_mod.CB2_OVERWORLD_SET)
        if in_overworld:
            _log(f"teach_hm[{step}]: Start (reopen-menu; cb2=overworld)")
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
        # Hard filter: Start while a menu is open would close it. The model is
        # told never to emit Start, but enforce it regardless — drop to B, which
        # backs out one level and self-corrects (over-backing lands in the
        # overworld, which the guard above reopens next step).
        if btn == "Start":
            btn, reason = "B", f"start-forbidden(was:{reason[:40]})"
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
