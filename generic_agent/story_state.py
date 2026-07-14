"""Inferred story progress flags for the navigate prompt.

We never directly read story flags from RAM (would require knowing many
EN Emerald addresses). Instead we infer from observed agent history:
which maps have been visited, how many turns spent there, etc. Then
write a one-line goal hint that the navigate prompt injects at the top.

The inference is deliberately conservative — being wrong about a flag
just changes the hint, never blocks an action.

Map id reference (English Emerald):
  (0, 9)   Littleroot Town outdoor
  (0, 16)  Route 101
  (1, 0)   Player house 1F
  (1, 1)   Player house 2F (bedroom)
  (1, 2)   Rival house 1F
  (1, 3)   Rival house 2F
  (1, 4)   Birch's Lab
  (3, 0)   Oldale Town (if reached)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StoryFlags:
    mom_dialog_done: bool = False
    birch_lab_visited: bool = False
    rival_house_visited: bool = False
    starter_received: bool = False
    route101_explored: bool = False
    oldale_reached: bool = False


_KEY_MAPS = {
    "mom_dialog_done":     [(1, 0)],   # spent time in Player house 1F
    "birch_lab_visited":   [(1, 4)],
    "rival_house_visited": [(1, 2), (1, 3)],
    "route101_explored":   [(0, 16)],
    "oldale_reached":      [(3, 0)],
}

# threshold (visit count) per flag — lower = easier to flip True.
_FLAG_THRESHOLDS = {
    "mom_dialog_done":     5,
    "birch_lab_visited":   5,
    "rival_house_visited": 5,
    "route101_explored":   5,
    "oldale_reached":      1,   # one transition is enough to count
}


def infer_flags(map_visit_counts: dict[tuple[int, int], int]) -> StoryFlags:
    """Infer story flags from where the agent has spent its turns.

    Why: the real story flags aren't read from RAM (that would need many
    EN Emerald addresses), so progress is approximated from observed
    behaviour — a key map visited past its threshold means that story beat
    almost certainly happened. ``starter_received`` is derived from the two
    prerequisite flags rather than thresholded, because Birch's Lab plus
    Route 101 is the only path the story allows to leave with a starter.
    Thresholds stay conservative on purpose: a wrong flag only changes the
    hint, never blocks an action.
    """
    flags = StoryFlags()
    for attr, keys in _KEY_MAPS.items():
        total = sum(map_visit_counts.get(k, 0) for k in keys)
        if total >= _FLAG_THRESHOLDS[attr]:
            setattr(flags, attr, True)
    flags.starter_received = (
        flags.birch_lab_visited and flags.route101_explored
    )
    return flags


def hint_for(
    flags: StoryFlags,
    current_map: tuple[int, int] | None = None,
    visits_this_map: int = 0,
) -> str:
    """Return the goal-hint line matching the furthest-reached flag.

    Why: the navigate prompt wants exactly one line of direction, so the
    branches are ordered latest-progress-first and the first match wins.
    The Oldale-plus-low-visit case is checked before the generic "go north"
    hint so a freshly arrived agent keeps exploring the new town instead of
    immediately walking back south onto Route 101.
    """
    if flags.oldale_reached and current_map == (3, 0) and visits_this_map < 50:
        return (
            "Goal: STAY in Oldale Town and explore. Walk around inside the "
            "town first (Pokemon Center, Pokemon Mart, NPCs) before you "
            "leave. Do NOT walk back south to Route 101."
        )
    if flags.oldale_reached:
        return (
            "Goal: continue NORTH from Oldale Town toward Route 102, "
            "then Petalburg City. Battle wild Pokemon to gain levels."
        )
    if flags.starter_received and not flags.oldale_reached:
        return (
            "Goal: you have a starter. Cross Route 101 NORTH into a "
            "NEW map (Oldale Town). Look for tile transitions — grass, "
            "town border, or stair you have not stepped on. "
            "Do NOT loop on the same path; if Up has not advanced for "
            "many turns, try Left or Right to flank around blockers."
        )
    if flags.birch_lab_visited and not flags.starter_received:
        return (
            "Goal: visit Route 101 north of Littleroot, find Birch "
            "being chased, choose a starter, win the battle."
        )
    if flags.mom_dialog_done and not flags.birch_lab_visited:
        return (
            "Goal: leave the house via south door, walk to Birch's "
            "Lab (south building of Littleroot), talk to the assistant."
        )
    return (
        "Goal: in player's bedroom (2F). Walk to the clock to set "
        "time, then go downstairs to talk to Mom."
    )
