"""Autonomous MID-BATTLE healing sub-task (H16): use a Super Potion on the
lead DURING a trainer battle, so Sceptile can out-heal Flannery's Overheat
(140 power, 2x vs grass, Sun-boosted: ~85-128 dmg) while Rock Tomb whittles
Torkoal down. Mirrors field_heal.run_heal_subtask (x-quantity discriminator,
flicker-robust bag reads, Haiku VLM loop) with battle-specific entry/exit:

- Entry is the battle action menu (FIGHT/BAG/POKEMON/RUN in a 2x2 grid:
  FIGHT top-left, BAG top-right — RUN_CYCLE's Down,Right nav to RUN
  bottom-right confirms the layout), NOT the START overlay. The opener is
  deterministic like move_select_sequence: B,B backs out of the move list /
  any mis-opened submenu (harmless at the top menu), Up,Left forces the
  cursor onto FIGHT (no wrap in the Gen-3 battle menu), Right lands BAG, A
  opens it. If we actually fired mid-dialog the presses are harmless and the
  VLM loop recovers from the real screen.
- Using an item CONSUMES the battle turn: after "HP was restored" the bag
  auto-closes and the enemy acts. So we use exactly ONE restore per
  invocation and return; the caller's loop re-evaluates next turn.
- Done-check: the bag's restore count DECREASED (primary — monotonic, and
  the enemy's follow-up attack can't undo it) or the lead's HP fraction
  rose above the pre-use baseline (secondary — it can race the enemy's hit,
  which is exactly why qty is primary). Both sides use max-of-3 reads
  because bag reads flicker low under button load (field_heal 07-19).
"""
from __future__ import annotations

import time
from typing import Callable

from . import config, rescue_brain, state as state_mod
from .io import EmulatorError, MGBAClient

# Fire the heal when the lead drops below this fraction. 0.40 ~= 51/127 HP on
# Sceptile L41: one Super Potion (+50) lifts it back to ~0.79, above the
# threshold, so a successful heal never immediately re-fires. Tune upward
# (<=0.60 stays waste-free: frac + 50/max still < 1.0) if live Flannery runs
# show a max-roll Overheat KOing from just above the line.
HEAL_TRIGGER_FRAC = 0.40
_MAX_STEPS = 16              # a clean battle heal is ~6 VLM steps post-opener
_STEP_SLEEP = 0.7
_HP_EPSILON = 0.05           # "HP rose" must beat read noise / rounding
_SCREEN = config.MEMORY_DIR / "battle_heal_screen.png"

SYSTEM_PROMPT_BATTLE_HEAL = (
    "You are IN A POKEMON BATTLE in Pokemon Emerald and must use a SUPER "
    "POTION from the BAG on your lead Pokemon SCEPTILE. Reply with ONLY a "
    'JSON object: {"button": "<A|B|Up|Down|Left|Right>", "reason": "<short>"}. '
    "The path: on the battle action menu (a 2x2 grid: FIGHT top-left, BAG "
    "top-right, POKEMON bottom-left, RUN bottom-right) move the cursor to "
    "BAG and press A -> the bag opens on an item pocket (press Left/Right to "
    "reach the ITEMS pocket if needed) -> press Down to scroll until the "
    "highlighted row is SUPER POTION (the user message tells you the exact "
    "quantity xN it shows) -> A -> choose USE -> the party screen opens with "
    "the cursor ALREADY on SCEPTILE (top-left, the Pokemon with a non-full "
    "HP bar): press A immediately, do NOT move the cursor. After the 'HP was "
    "restored' message the battle resumes on its own. "
    "HARD RULES: (1) NEVER select FIGHT — if you see the 2x2 list of battle "
    "MOVES with PP counts, you are inside FIGHT: press B to return to the "
    "action menu, then go to BAG. (2) NEVER select RUN — this is a trainer "
    "battle, running is impossible. (3) NEVER press Start or Select — there "
    "is no START menu in battle. (4) The 'It won't have any effect' message "
    "means you used a WRONG item: press B back to the ITEMS pocket, press "
    "Down until the highlighted row is SUPER POTION (the xN quantity from "
    "the user message), then A. Do NOT keep pressing A on the top item. "
    "(5) On a YES/NO box, A confirms YES. (6) If the cursor is on CLOSE BAG "
    "or CANCEL press Up to reach the items. (7) LOOK at the screenshot and "
    "describe the CURRENT screen in your reason (action-menu / move-list / "
    "bag / party-list / dialog) so you do not repeat a button that is not "
    "working."
)

# Deterministic opener from the battle action menu (mirrors
# move_select_sequence's self-correcting B,B prefix): back out of any
# submenu, force the cursor to FIGHT (top-left), step Right onto BAG, open.
_OPEN_BAG_SEQ = ("B", "B", "Up", "Left", "Right", "A")


def should_heal(
    gs: state_mod.GameState | None,
    enemy_cur_hp: int,
    turn: int,
    cooldown_until: int,
    threshold: float = HEAL_TRIGGER_FRAC,
) -> bool:
    """Pure trigger predicate for the mid-battle heal (unit-tested).

    Fires only when it is genuinely worth spending our battle turn on a
    potion: the lead (slot 0 — the only mon the subtask targets) is alive
    but under the threshold, the bag holds a restore, and the OPPONENT is
    alive too. enemy_cur_hp <= 0 covers both the real faint transition
    (spec guard: B-mash territory, a bag press there mis-fires into the
    send-out dialog) and a failed RAM read (-1). Cooldown stops a failed
    VLM run from re-firing every turn and starving the battle of attacks.
    """
    if gs is None or turn < cooldown_until:
        return False
    if gs.party0_max_hp <= 0 or gs.party0_hp <= 0:
        return False  # lead unreadable or fainted — a potion can't help
    if gs.party0_hp_frac >= threshold:
        return False
    if gs.bag_heal_qty <= 0:
        return False
    return enemy_cur_hp > 0


def _read(client: MGBAClient):
    try:
        return state_mod.read_state(client)
    except EmulatorError:
        return None


def _has_heal(client: MGBAClient) -> bool:
    """True if the bag holds a restore. Same flicker hardening as
    field_heal._has_heal: bag_heal_qty reads 0 under button-press load, so a
    single 0 read must not stop the heal — confirm with up to 3 reads."""
    for _ in range(3):
        gs = _read(client)
        if gs and gs.bag_heal_qty > 0:
            return True
    return False


def _stable_heal_qty(client: MGBAClient) -> int:
    """Max of 3 bag_heal_qty reads. The flicker direction is DOWN (a garbage
    slot-id read drops items from the count), so the max is the trustworthy
    value; a genuine consumption lowers all three reads."""
    best = 0
    for _ in range(3):
        gs = _read(client)
        if gs and gs.bag_heal_qty > best:
            best = gs.bag_heal_qty
    return best


def run_battle_heal_subtask(
    client: MGBAClient, log: Callable[[str], None] | None = None,
) -> bool:
    """Use ONE Super Potion on the lead from inside a trainer battle via the
    VLM. Returns True once a restore was consumed (or the lead turned out to
    be already above the threshold). Blocks (~<60s); the battle is frozen
    while a menu is open, so the pace is safe. Gives up on an empty bag, a
    fainted lead, or the step cap — the caller's cooldown then lets the
    normal best-move flow resume."""
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
    if gs0 is None or gs0.party0_max_hp <= 0:
        return False
    if gs0.party0_hp <= 0:
        _log("battle_heal: lead fainted, cannot potion — abort")
        return False
    if gs0.party0_hp_frac >= HEAL_TRIGGER_FRAC:
        # A flicker-low HP read fired the trigger; re-read says healthy. No-op
        # success so the caller does not cooldown-penalize it.
        _log("battle_heal: lead already above threshold, no-op")
        return True
    base_qty = _stable_heal_qty(client)
    if base_qty <= 0:
        _log("battle_heal: no restores in bag, abort")
        return False
    base_frac = gs0.party0_hp_frac
    _log(
        f"battle_heal: start hp={gs0.party0_hp}/{gs0.party0_max_hp} "
        f"({base_frac:.0%}) restores={base_qty}"
    )

    for btn in _OPEN_BAG_SEQ:
        try:
            client.tap(btn, frames=12)
        except EmulatorError:
            return False
        time.sleep(0.4)

    def _consumed() -> bool:
        qty_now = _stable_heal_qty(client)
        if 0 < qty_now < base_qty:
            return True
        gs = _read(client)
        return bool(
            gs and gs.party0_max_hp > 0
            and gs.party0_hp_frac > base_frac + _HP_EPSILON
        )

    last_btns: list[str] = []
    for step in range(_MAX_STEPS):
        if _consumed():
            # The battle bag auto-closes after a use; a couple of B's only
            # advance the resuming battle text and are safe.
            for _ in range(2):
                try:
                    client.tap("B", frames=12)
                except EmulatorError:
                    break
                time.sleep(0.3)
            _log(f"battle_heal: SUCCESS (step {step}, used 1 restore)")
            return True
        gs = _read(client)
        if gs and gs.party0_max_hp > 0 and gs.party0_hp <= 0:
            _log("battle_heal: lead fainted mid-subtask, abort")
            return False
        if not _has_heal(client):
            for _ in range(3):
                try:
                    client.tap("B", frames=12)
                except EmulatorError:
                    break
                time.sleep(0.3)
            _log("battle_heal: bag empty, stop")
            return False
        try:
            client.screenshot(_SCREEN)
        except EmulatorError:
            time.sleep(0.5)
            continue
        sp_qty = gs.bag_super_potion_qty if gs else 0
        sp_hint = (
            f"The SUPER POTION row shows quantity x{sp_qty}. "
            if sp_qty > 0 else
            "Pick the item literally named SUPER POTION (restores 50 HP). "
        )
        user_text = (
            f"Step {step}/{_MAX_STEPS}. Recent buttons: {last_btns[-6:]}. "
            f"Lead HP is about {int(gs.party0_hp_frac * 100) if gs else '?'}%. "
            f"{sp_hint}"
            "Use it on SCEPTILE. What is the next single button?"
        )
        try:
            resp, _, _ = rescue_brain._call_haiku(
                _SCREEN, SYSTEM_PROMPT_BATTLE_HEAL, user_text,
            )
            raw = resp.content[0].text if getattr(resp, "content", None) else ""
            btn, reason = rescue_brain._parse_response(raw)
        except Exception as exc:  # noqa: BLE001 — API/parse failure -> back out
            btn, reason = "B", f"haiku-err:{exc}"
        if btn in ("Start", "Select"):
            # No START menu exists in battle; both keys are dead-or-worse.
            btn, reason = "B", f"{btn}-forbidden(was:{reason[:24]})"
        _log(f"battle_heal[{step}]: {btn} ({reason})")
        try:
            client.tap(btn, frames=12)
        except EmulatorError:
            break
        last_btns.append(btn)
        time.sleep(_STEP_SLEEP)

    # Step cap / IO break: back out to the action menu and report the truth.
    for _ in range(3):
        try:
            client.tap("B", frames=12)
        except EmulatorError:
            break
        time.sleep(0.3)
    ok = _consumed()
    _log(
        f"battle_heal: {'SUCCESS' if ok else 'gave up'} after cap "
        f"({_MAX_STEPS} steps)"
    )
    return ok
