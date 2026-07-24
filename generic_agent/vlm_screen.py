"""VLM screen tiebreaker (Option-1, 2026-07-17).

The pixel heuristic in screen_features false-positives battle_menu on menus
(the Pokedex read as a battle -> 4000-turn stall) and gBattleTypeFlags reads
stale-non-zero in menus after a battle. Both made the loop freeze thinking it
was in a battle. The whole cb2-guard / ram_battle_recent / hardcoded menu-CB2
machinery exists to patch that.

Now that the upscaled-PNG frame is legible (preprocess), the VLM classifies
screens 5/5 where the pixel heuristic gets 1/5 (docs H11). So use it as a
tiebreaker: when the loop believes it is in a battle but the frame has been
FROZEN for many turns (a real battle animates; a fake one is static), ask the
VLM once whether this is really a battle. The verdict is cached by frame_hash,
so a static stuck frame costs a single Haiku call (~$0.0005) no matter how many
turns it persists -- negligible, and it replaces the hardcoded CB2 patches with
one general check.
"""
from __future__ import annotations

from pathlib import Path

from . import preprocess, rescue_brain

# frame_hash -> bool (True=battle, False=not battle). Process-lifetime cache.
_verdict_cache: dict[str, bool] = {}

_SYSTEM = (
    "You look at a Pokemon Emerald (GBA) screenshot and decide ONE thing: is "
    "this an ACTIVE BATTLE right now? A battle shows Pokemon on a battlefield "
    "with green/HP bars and/or the FIGHT/BAG/POKEMON/RUN command box or a "
    "move-selection list or a 'choose a Pokemon to send out' party screen "
    "reached from battle. It is NOT a battle if you see the walkable overworld, "
    "the START menu, the BAG, the region map / POKeDEX, or a plain dialog box "
    "in the field. Reply with ONLY JSON: {\"battle\": true} or {\"battle\": false}. "
    "Read the actual pixels; do not assume."
)


def is_battle_screen(shot_path: Path, log=None) -> bool | None:
    """VLM verdict: is this frame an active battle? Cached by frame_hash.

    Returns True/False, or None if the frame can't be read or the API fails
    (caller should then fall back to its existing heuristic, i.e. change
    nothing).
    """
    try:
        arr = preprocess.load_png_as_array(shot_path)
        fhash = preprocess.frame_hash(arr)
    except (OSError, ValueError):
        return None
    if fhash in _verdict_cache:
        return _verdict_cache[fhash]
    try:
        resp, _, _ = rescue_brain._call_haiku(
            shot_path, _SYSTEM, "Is this an active battle? JSON only.",
        )
        raw = resp.content[0].text if getattr(resp, "content", None) else ""
    except Exception as exc:  # noqa: BLE001 — API/parse failure -> no opinion
        if log:
            log(f"vlm_screen: API error, no verdict ({exc})")
        return None
    low = raw.lower()
    # Tolerant parse: the JSON may be fenced or have trailing prose.
    if '"battle": true' in low or '"battle":true' in low:
        verdict = True
    elif '"battle": false' in low or '"battle":false' in low:
        verdict = False
    else:
        # Last resort: look for a bare true/false near "battle".
        verdict = "true" in low and "false" not in low.split("battle")[-1][:20]
    _verdict_cache[fhash] = verdict
    if log:
        log(f"vlm_screen: battle={verdict} (fhash={fhash[:8]})")
    return verdict
