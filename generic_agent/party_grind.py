"""Opt-in autonomous party training ("party grind" marker mode).

USER-DIRECTED FEATURE (2026-07-28): the party is Sceptile L48 + five L5-17
backups; only the lead ever fights, so the backups never grow. This mode
levels them hands-off. It is OFF unless the marker file exists:

    generic_agent/memory/party_grind.on

(optionally containing a target level as text, default 18 -- the natural
target: Marill/Poochyena/Slakoth all evolve at 18). Delete the marker (or let
the mode finish) and every normal-run behavior is byte-identical: all pgrind_*
goals gate on the marker, and the loop hook gates on the pgrind goal name.

Design (architect/Fable, 2026-07-28):
  * WHO fights is solved by a RAM-verified PARTY REORDER, not by trusting
    blind menu presses: the manual SWITCH-mode attempts failed because a
    dropped press silently desyncs an open-loop script. Here every phase is
    driven by observable state -- gMain.callback2 for which screen is up,
    gPartyMenu cursor bytes for where the cursor is, and the slot-0
    PERSONALITY value (unique per mon, survives evolution, plain u32 at a
    fixed address) as the end-to-end success check -- with re-press-until-
    observed transitions, the same contract that makes walking and the catch
    SM drop-tolerant. A wrong intermediate press can only produce a party
    ORDER scramble, which the next bounded attempt corrects; there is no
    destructive action anywhere in the party menu (no release exists there).
  * WHERE to grind is Rusturf Tunnel (24,4): reachable from Petalburg WITHOUT
    transiting Mauville (the stale-goal trap of 2026-07-25), and a true CAVE
    grind site per the jagged-pass lesson -- probed offline 2026-07-28:
    177 walkable tiles, 174 of them MB_CAVE (0x08) land-encounter floor, and
    all 35 JUMP-behavior tiles are non-walkable decorative rock edging with
    ZERO walkable-adjacent ledges (no frontier leak). Wilds are the canon
    Whismur-only table (L5-8) -- the weakest cave population on the map, the
    only one an L5 backup can safely fight, with no Pursuit/self-destruct.
  * SAFETY for the weak lead is the existing battle cascade, unchanged: it
    already fights at hp>=40%, RUNs under 26%, sends out the next mon (the
    L48 ex-lead) on a faint, and the pgrind heal goals route to the Rustboro
    PC -- so a faint is a short PC trip, never a whiteout (the strong mon is
    always behind the weak one).

gPartyMenu addresses: slotId was MEASURED live at 0x0203CED1 (menu-automation
memory 2026-07-16: Right 0->1, Down 1->2). slotId2 (the SWITCH-mode second
cursor) and action are the next two s8 fields in the pokeemerald struct, so
0x0203CED2/0x0203CED3. They are treated as UNTRUSTED until they behave: phase
gates only proceed when the bytes respond coherently to presses, and the
final authority is the slot-0 personality check -- a wrong offset degrades to
a clean bounded give-up (loop keeps grinding with whoever leads), never a
blind scramble. LIVE-VERIFY these two offsets in small-step test 1 before a
long run.
"""
from __future__ import annotations

import time
from typing import Callable

from . import config
from .io import EmulatorError, MGBAClient
from .state import (
    GMAIN_CB2_ADDR,
    PLAYER_PARTY_ADDR,
    PLAYER_PARTY_COUNT_ADDR,
    POKEMON_LEVEL_OFFSET,
    POKEMON_STRUCT_SIZE,
)

PARTY_GRIND_MARKER = config.MEMORY_DIR / "party_grind.on"
# L18: Marill -> Azumarill, Poochyena -> Mightyena, Slakoth -> Vigoroth all
# evolve here, and Lombre (L17) crosses it in one fight. Override by writing a
# number into the marker file.
PARTY_GRIND_DEFAULT_TARGET = 18

# gPartyMenu (see module docstring for provenance / trust model).
GPARTY_MENU_SLOT_ADDR = 0x0203CED1   # s8 slotId (measured live)
GPARTY_MENU_SLOT2_ADDR = 0x0203CED2  # s8 slotId2 (derived; runtime-verified)
GPARTY_MENU_ACTION_ADDR = 0x0203CED3  # u8 action (derived; used as a CHANGE
# detector only -- we never compare against a hardcoded enum value, so a
# wrong offset or enum renumbering fails safe)

CB2_OVERWORLD = 0x08085E5D
CB2_BATTLE_MAIN = 0x08038421
# CB2_UpdatePartyMenu. state.py already records this exact callback for the
# in-battle party screen; the field party menu is the same module. The SM
# only requires "this value <=> party menu is up" -- if the field menu ever
# reported something else, phase 1 would give up cleanly (never reach the
# cursor phases).
CB2_PARTY_MENU = 0x081B01B1

# Menu presses must be slow singles (menu-automation memory: fast bursts drop
# during menu animations; 0.75-0.8s singles land reliably -- 07-27 measured).
_TAP_FRAMES = 12
_PRESS_SLEEP = 0.8
_SWAP_ANIM_SLEEP = 2.0   # party slide animation after confirming the swap
_OPEN_ROUNDS = 12        # START-menu walk: 8 items + drop slack
_NAV_MAX = 20            # cursor-navigation presses per phase
_TRIPLE_TRIES = 3        # (A, Down, A) switch-select attempts
_ATTEMPTS = 2            # full-SM retries (state re-derived from RAM each time)


def _sleep(seconds: float) -> None:
    """Module-level so tests can zero it out."""
    time.sleep(seconds)


def party_grind_active() -> bool:
    """True while the opt-in marker exists. This is the ONLY switch: no
    marker, no behavior change anywhere (goals gate on it too)."""
    try:
        return PARTY_GRIND_MARKER.exists()
    except OSError:
        return False


def party_grind_target_level() -> int:
    try:
        txt = PARTY_GRIND_MARKER.read_text(encoding="utf-8").strip()
    except OSError:
        return PARTY_GRIND_DEFAULT_TARGET
    try:
        v = int(txt) if txt else PARTY_GRIND_DEFAULT_TARGET
    except ValueError:
        return PARTY_GRIND_DEFAULT_TARGET
    return v if 2 <= v <= 100 else PARTY_GRIND_DEFAULT_TARGET


def retire_party_grind() -> None:
    try:
        PARTY_GRIND_MARKER.unlink()
    except OSError:
        pass


# --------------------------------------------------------------------------
# Party reads (fixed gPlayerParty globals -- no SaveBlock1 DMA relocation).
# --------------------------------------------------------------------------

def read_slot(client: MGBAClient, slot: int) -> tuple[int, int] | None:
    """(personality, level) for a party slot; None if unreadable or garbage.
    Personality is the plain u32 at struct base +0 (NOT encrypted); level is
    the plain u8 at +0x54. A real mon never has pv==0 or level 0/>100."""
    base = PLAYER_PARTY_ADDR + slot * POKEMON_STRUCT_SIZE
    try:
        pv = client.read32(base)
        lv = client.read8(base + POKEMON_LEVEL_OFFSET)
    except EmulatorError:
        return None
    if pv == 0 or lv == 0 or lv > 100:
        return None
    return pv, lv


def read_party(client: MGBAClient) -> list[tuple[int, int]] | None:
    """[(pv, level)] for the whole party, or None on ANY suspect read -- the
    planner must never act on a torn frame (the DMA/socket-corruption family:
    a garbage level could trigger a spurious rotation)."""
    try:
        n = client.read8(PLAYER_PARTY_COUNT_ADDR)
    except EmulatorError:
        return None
    if not (1 <= n <= 6):
        return None
    out: list[tuple[int, int]] = []
    for i in range(n):
        s = read_slot(client, i)
        if s is None:
            return None
        out.append(s)
    return out


def plan_action(
    slots: list[tuple[int, int]], target_level: int,
) -> tuple[str, int | None]:
    """Decide what the party order should be. Pure function (unit-tested).

    Returns one of:
      ("grind", None)    slot0 is still below target -- keep training it
      ("swap", i)        promote slot i (the LOWEST below-target mon) to 0
      ("restore", i)     all trained -- put the strongest mon (i) back first
      ("retire", None)   strongest already leads -- delete the marker

    "Strongest" = highest level, so the real lead is restored without any
    species hardcode. slot0-below-target short-circuits so rotation only
    happens when the current trainee finishes (minimum number of reorders)."""
    below = [i for i, (_pv, lv) in enumerate(slots) if lv < target_level]
    if below and 0 in below:
        return ("grind", None)
    if below:
        nxt = min(below, key=lambda i: slots[i][1])
        return ("swap", nxt)
    strongest = max(range(len(slots)), key=lambda i: slots[i][1])
    if strongest != 0:
        return ("restore", strongest)
    return ("retire", None)


# --------------------------------------------------------------------------
# The reorder state machine.
# --------------------------------------------------------------------------

def _read_cb2(client: MGBAClient) -> int:
    try:
        return client.read32(GMAIN_CB2_ADDR)
    except EmulatorError:
        return 0


def _read8(client: MGBAClient, addr: int) -> int:
    try:
        return client.read8(addr)
    except EmulatorError:
        return -1


def _press(client: MGBAClient, btn: str) -> None:
    client.tap(btn, frames=_TAP_FRAMES)
    _sleep(_PRESS_SLEEP)


def _open_party_menu(client: MGBAClient) -> str:
    """Overworld -> field party menu. Returns "ok" / "battle" / "fail".

    The START menu is an overworld OVERLAY (cb2 does not change -- hm_teach
    lesson 07-19), so open-state is not directly readable. Instead: a fast
    path assumes the cursor is at the top (the loop's periodic save sequence
    relies on the same reset), then a bounded probe walk (B, Start, Down, A)
    advances one menu item per round whatever the real overlay state is,
    identifying each landing by cb2. A wild encounter (cave floor step from a
    dropped-press desync) aborts instantly -- the loop's battle machinery
    owns that; the hook retries after its cooldown."""
    for btn in ("Start", "Down", "A"):
        _press(client, btn)
    for _ in range(_OPEN_ROUNDS):
        cb2 = _read_cb2(client)
        if cb2 == CB2_PARTY_MENU:
            return "ok"
        if cb2 == CB2_BATTLE_MAIN:
            return "battle"
        if cb2 == CB2_OVERWORLD:
            # Field, START overlay open or closed, or a SAVE/dialog overlay:
            # B closes a dialog (or the overlay), Start re-toggles, Down
            # advances, A selects. Net effect: one item per round, from any
            # desync, converging on POKEMON within a menu cycle.
            for btn in ("B", "Start", "Down", "A"):
                _press(client, btn)
        else:
            # Some other full screen (Pokedex/Bag/PokeNav/summary/options):
            # B backs out to the overlay; next round advances.
            _press(client, "B")
    return "fail"


def _nav_main_cursor(client: MGBAClient, target_slot: int) -> str:
    """Move gPartyMenu.slotId to target_slot (read-verify every press).
    Layout (measured): slot0 is the big left panel; Right enters the right
    column (slot 1), Down/Up move within it; below slot5 sits CANCEL."""
    stuck = 0
    last = -2
    for _ in range(_NAV_MAX):
        cb2 = _read_cb2(client)
        if cb2 != CB2_PARTY_MENU:
            return "battle" if cb2 == CB2_BATTLE_MAIN else "fail"
        s = _read8(client, GPARTY_MENU_SLOT_ADDR)
        if s == target_slot:
            return "ok"
        if not (0 <= s <= 7):
            return "fail"     # cursor byte not behaving -- do not guess
        stuck = stuck + 1 if s == last else 0
        last = s
        if s == 0:
            btn = "Right"
        elif s > 5:
            btn = "Up"        # CANCEL strip -> back into the column
        elif s < target_slot:
            btn = "Down"
        else:
            btn = "Up"
        if stuck >= 3:
            # A direction that stops registering movement usually means the
            # cursor model is off for this position -- probe the other axis.
            btn = "Right" if btn in ("Up", "Down") else "Down"
            stuck = 0
        _press(client, btn)
    return "fail"


def _enter_switch_mode(client: MGBAClient, target_slot: int) -> str:
    """From the list cursor on target_slot: A (action menu, cursor SUMMARY)
    -> Down (SWITCH) -> A. Verified by the ACTION byte CHANGING (never a
    hardcoded enum compare) while cb2 stays the party menu; a fullscreen
    surprise (SUMMARY opened on a dropped Down) is detected via cb2 and
    backed out. slotId2 spawning on a sane slot is required before the
    second-cursor phase is allowed to press anything.

    Returns "cursor_moved" when the LIST cursor is no longer on target_slot
    (a dropped A turns the following Down into a list move) — the caller
    re-runs the nav phase instead of swapping the wrong slot."""
    for _ in range(_TRIPLE_TRIES):
        s = _read8(client, GPARTY_MENU_SLOT_ADDR)
        if s != target_slot:
            return "cursor_moved"
        a0 = _read8(client, GPARTY_MENU_ACTION_ADDR)
        _press(client, "A")
        _press(client, "Down")
        _press(client, "A")
        cb2 = _read_cb2(client)
        if cb2 == CB2_BATTLE_MAIN:
            return "battle"
        if cb2 != CB2_PARTY_MENU:
            # SUMMARY (or another fullscreen) opened -> back out, retry.
            _press(client, "B")
            _press(client, "B")
            continue
        a1 = _read8(client, GPARTY_MENU_ACTION_ADDR)
        s2 = _read8(client, GPARTY_MENU_SLOT2_ADDR)
        if a1 != a0 and a1 >= 0 and 0 <= s2 <= 6:
            return "ok"
        # Not in switch mode (drop, or an ITEM submenu popup): one B closes
        # any submenu without leaving the party screen; retry the triple.
        _press(client, "B")
    return "fail"


def _switch_into_slot0(client: MGBAClient) -> str:
    """Drive the SWITCH-mode second cursor (slotId2) to slot 0 and confirm.
    Completion = the action byte returning to 0 (choose-mode restored after
    the slide animation). The end-to-end success check is NOT here -- it is
    the slot-0 personality verify in run_reorder."""
    stuck = 0
    last = -2
    for _ in range(_NAV_MAX):
        cb2 = _read_cb2(client)
        if cb2 != CB2_PARTY_MENU:
            return "battle" if cb2 == CB2_BATTLE_MAIN else "fail"
        a = _read8(client, GPARTY_MENU_ACTION_ADDR)
        if a == 0:
            return "ok"       # swap animation done, menu back in choose mode
        s2 = _read8(client, GPARTY_MENU_SLOT2_ADDR)
        if not (0 <= s2 <= 7):
            return "fail"
        if s2 == 0:
            _press(client, "A")
            _sleep(_SWAP_ANIM_SLEEP)
            continue
        stuck = stuck + 1 if s2 == last else 0
        last = s2
        btn = "Left" if s2 == 1 else "Up"
        if stuck >= 3:
            btn = "Left" if btn == "Up" else "Up"
            stuck = 0
        _press(client, btn)
    return "fail"


def _verify_slot0_pv(client: MGBAClient, want_pv: int) -> bool:
    """Two consecutive agreeing reads of the slot-0 personality (torn-read
    guard; personality is a fixed-address plain u32)."""
    streak = 0
    for _ in range(6):
        s = read_slot(client, 0)
        if s is not None and s[0] == want_pv:
            streak += 1
            if streak >= 2:
                return True
        else:
            streak = 0
        _sleep(0.2)
    return False


def _unwind(client: MGBAClient) -> None:
    """B until back in the overworld, plus one extra B for a START overlay
    (cb2-invisible). Bounded; never raises."""
    try:
        for _ in range(6):
            if _read_cb2(client) == CB2_OVERWORLD:
                _press(client, "B")
                return
            _press(client, "B")
    except EmulatorError:
        pass


def _find_slot_by_pv(client: MGBAClient, pv: int) -> int | None:
    for i in range(6):
        s = read_slot(client, i)
        if s is not None and s[0] == pv:
            return i
    return None


def run_reorder(
    client: MGBAClient, target_pv: int,
    log: Callable[[str], None] | None = None,
) -> bool:
    """Put the mon with personality target_pv into party slot 0.

    Bounded synchronous sub-task (~20-40s happy path). Every attempt
    re-derives ALL state from RAM (cb2 / cursor bytes / pv scan), so a
    dropped press, a wrong-pair swap, or a stale attempt can only cost a
    retry, never compound. Returns True only after the slot-0 personality
    verifies. On False the party may be reordered-but-wrong (harmless: the
    grind goals are lead-agnostic and the next hook run retries)."""
    def _log(m: str) -> None:
        if log:
            try:
                log(m)
            except OSError:
                pass

    for attempt in range(_ATTEMPTS):
        try:
            tgt = _find_slot_by_pv(client, target_pv)
            if tgt is None:
                _log("party_grind: target pv not found -- aborting")
                return False
            if tgt == 0:
                return True
            r = _open_party_menu(client)
            if r != "ok":
                _log(f"party_grind[{attempt}]: open_party -> {r}")
                if r == "battle":
                    return False
                _unwind(client)
                continue
            # nav <-> switch-select mini-loop: a dropped A can drift the list
            # cursor (enter_switch reports "cursor_moved"); re-navigate
            # instead of swapping whatever the cursor landed on.
            r = "cursor_moved"
            for _ in range(3):
                r = _nav_main_cursor(client, tgt)
                if r != "ok":
                    break
                r = _enter_switch_mode(client, tgt)
                if r != "cursor_moved":
                    break
            if r != "ok":
                _log(f"party_grind[{attempt}]: nav/switch-select -> {r}")
                if r == "battle":
                    return False
                _unwind(client)
                continue
            r = _switch_into_slot0(client)
            if r != "ok":
                _log(f"party_grind[{attempt}]: switch_confirm -> {r}")
                if r == "battle":
                    return False
                _unwind(client)
                continue
            if _verify_slot0_pv(client, target_pv):
                _unwind(client)
                _log(f"party_grind[{attempt}]: slot0 verified (pv={target_pv:08x})")
                return True
            _log(f"party_grind[{attempt}]: slot0 pv mismatch -- retrying")
            _unwind(client)
        except EmulatorError as exc:
            _log(f"party_grind[{attempt}]: emulator error {exc}")
            return False
    return False


def ensure_lead(
    client: MGBAClient, log: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    """Per-turn entry point for the loop hook (only while the pgrind goal is
    active at the grind site). Cheap when nothing to do (13 fixed-address
    reads). Returns (acted, reason):
      (False, "noop")     slot0 is the right trainee -- fall through to grind
      (True, "swapped")   a reorder ran (loop should `continue` this turn)
      (True, "restored")  strongest mon put back in front (retire next call)
      (True, "retired")   marker deleted -- normal goal chain resumes
      (False, ...)        couldn't act (unreadable / SM gave up) -- cooldown
    """
    def _log(m: str) -> None:
        if log:
            try:
                log(m)
            except OSError:
                pass

    if not party_grind_active():
        return (False, "off")
    slots = read_party(client)
    if slots is None:
        return (False, "unreadable")
    action, idx = plan_action(slots, party_grind_target_level())
    if action == "grind":
        return (False, "noop")
    if action == "retire":
        retire_party_grind()
        _log("party_grind: all backups at target & strongest leads -> marker off")
        return (True, "retired")
    assert idx is not None
    _log(
        f"party_grind: {action} slot{idx} (lv{slots[idx][1]}) -> slot0 "
        f"[party={[lv for _pv, lv in slots]}, target={party_grind_target_level()}]"
    )
    ok = run_reorder(client, slots[idx][0], log=log)
    if not ok:
        return (False, f"{action}_failed")
    return (True, "restored" if action == "restore" else "swapped")
