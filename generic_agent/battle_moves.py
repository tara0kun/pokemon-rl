"""Battle move selection — pick the most damaging move against the current
opponent, read entirely from the game's own RAM/ROM (no hardcoded per-map or
per-encounter data).

Why this exists: the heuristic's FIGHT handler used to blindly select move
slot 1. For Grovyle that is Pound (Normal), which is 0.5x against Roxanne's
Rock/Ground Geodude — the agent chipped for half damage and lost by attrition.
Here we read the party leader's four moves (power+type from the ROM move
table gBattleMoves), read the opponent's types from gBattleMons, score each
damaging move by power x type-effectiveness, and return the best slot so the
FIGHT handler can navigate the move cursor there.

All addresses are Pokemon Emerald (USA). The type-effectiveness chart is
general game mechanics (like knowing Pokemon have types), not map-specific
data, so it does not violate the no-hardcoded-game-data rule.
"""
from __future__ import annotations

from .io import MGBAClient, EmulatorError

# gPlayerParty[0] and the in-battle mon structs (gBattleMons[]) and the ROM
# move-data table (gBattleMoves), all USA Emerald.
PLAYER_PARTY_ADDR = 0x020244EC
GBATTLEMONS = 0x02024084       # BattleMon[], 0x58 bytes each; [1] = opponent
BATTLEMON_SIZE = 0x58
BATTLEMON_MOVES = 0x0C         # 4 x u16 (active battler's current moves)
BATTLEMON_TYPE1 = 0x21         # u8
BATTLEMON_TYPE2 = 0x22         # u8
BATTLEMON_HP = 0x28            # u16 current HP
BATTLEMON_MAXHP = 0x2C         # u16 max HP
GBATTLEMOVES = 0x0831C898      # BattleMove[], 12 bytes each: power@+1, type@+2

# Substruct permutation order (personality % 24); moves live in the 'A'
# (Attacks) substruct. Mirrors state.py's species (G) decode.
_PERMS = [
    "GAEM", "GAME", "GEAM", "GEMA", "GMAE", "GMEA",
    "AGEM", "AGME", "AEGM", "AEMG", "AMGE", "AMEG",
    "EGAM", "EGMA", "EAGM", "EAMG", "EMGA", "EMAG",
    "MGAE", "MGEA", "MAGE", "MAEG", "MEGA", "MEAG",
]

# Gen-3 type IDs: Normal0 Fighting1 Flying2 Poison3 Ground4 Rock5 Bug6 Ghost7
# Steel8 Fire10 Water11 Grass12 Electric13 Psychic14 Ice15 Dragon16 Dark17.
# Only non-1x interactions; default 1.0. 0.0 = immune, 2.0 = super effective.
_CHART: dict[int, dict[int, float]] = {
    0:  {5: 0.5, 7: 0.0, 8: 0.5},
    1:  {0: 2, 2: 0.5, 3: 0.5, 5: 2, 6: 0.5, 7: 0.0, 8: 2, 14: 0.5, 15: 2, 17: 2},
    2:  {1: 2, 5: 0.5, 6: 2, 8: 0.5, 12: 2, 13: 0.5},
    3:  {3: 0.5, 4: 0.5, 5: 0.5, 7: 0.5, 8: 0.0, 12: 2},
    4:  {2: 0.0, 3: 2, 5: 2, 6: 0.5, 8: 2, 10: 2, 12: 0.5, 13: 2},
    5:  {1: 0.5, 2: 2, 4: 0.5, 6: 2, 8: 0.5, 10: 2, 15: 2},
    6:  {1: 0.5, 2: 0.5, 3: 0.5, 7: 0.5, 8: 0.5, 10: 0.5, 12: 2, 14: 2, 17: 2},
    7:  {0: 0.0, 7: 2, 8: 0.5, 14: 2, 17: 0.5},
    8:  {5: 2, 8: 0.5, 10: 0.5, 11: 0.5, 13: 0.5, 15: 2},
    10: {5: 0.5, 6: 2, 8: 2, 10: 0.5, 11: 0.5, 12: 2, 15: 2, 16: 0.5},
    11: {4: 2, 5: 2, 10: 2, 11: 0.5, 12: 0.5, 16: 0.5},
    12: {2: 0.5, 3: 0.5, 4: 2, 5: 2, 6: 0.5, 8: 0.5, 10: 0.5, 11: 2, 12: 0.5, 16: 0.5},
    13: {2: 2, 4: 0.0, 11: 2, 12: 0.5, 13: 0.5, 16: 0.5},
    14: {1: 2, 3: 2, 8: 0.5, 14: 0.5, 17: 0.0},
    15: {2: 2, 4: 2, 8: 0.5, 10: 0.5, 11: 0.5, 12: 2, 15: 0.5, 16: 2},
    16: {8: 0.5, 16: 2},
    17: {1: 0.5, 7: 2, 8: 0.5, 14: 2, 17: 0.5},
}


def effectiveness(atk_type: int, def_t1: int, def_t2: int) -> float:
    row = _CHART.get(atk_type, {})
    mult = row.get(def_t1, 1.0)
    if def_t2 != def_t1:
        mult *= row.get(def_t2, 1.0)
    return mult


def enemy_types(client: MGBAClient) -> tuple[int, int]:
    a = GBATTLEMONS + BATTLEMON_SIZE  # gBattleMons[1] = opponent
    return client.read8(a + BATTLEMON_TYPE1), client.read8(a + BATTLEMON_TYPE2)


def enemy_hp(client: MGBAClient) -> tuple[int, int]:
    a = GBATTLEMONS + BATTLEMON_SIZE
    return client.read16(a + BATTLEMON_HP), client.read16(a + BATTLEMON_MAXHP)


def active_move_ids(client: MGBAClient) -> list[int]:
    """The four move IDs of the player's ACTIVE battler (gBattleMons[0]).

    Read from the in-battle mon struct, not gPlayerParty[0], so it stays
    correct after the lead faints and a different party member is sent out.
    """
    a = GBATTLEMONS  # gBattleMons[0] = player's active battler
    return [client.read16(a + BATTLEMON_MOVES + i * 2) for i in range(4)]


def party0_move_ids(client: MGBAClient) -> list[int]:
    """Decrypt gPlayerParty[0]'s four move IDs from the Attacks substruct.
    (Kept for reference; best_move_index uses active_move_ids instead.)"""
    pv = client.read32(PLAYER_PARTY_ADDR + 0x00)
    otid = client.read32(PLAYER_PARTY_ADDR + 0x04)
    key = pv ^ otid
    order = _PERMS[pv % 24]
    a_off = 0x20 + order.index("A") * 12
    w0 = client.read32(PLAYER_PARTY_ADDR + a_off + 0) ^ key
    w1 = client.read32(PLAYER_PARTY_ADDR + a_off + 4) ^ key
    return [w0 & 0xFFFF, (w0 >> 16) & 0xFFFF, w1 & 0xFFFF, (w1 >> 16) & 0xFFFF]


def move_power_type(client: MGBAClient, move_id: int) -> tuple[int, int]:
    """Read (base_power, type_id) for a move straight from the ROM table."""
    base = GBATTLEMOVES + move_id * 12
    return client.read8(base + 1), client.read8(base + 2)


def best_move_index(client: MGBAClient) -> int:
    """Return the 0-based slot of the highest power x effectiveness DAMAGING
    move of the party leader vs the current opponent, or -1 if none / unread."""
    try:
        moves = active_move_ids(client)
        et1, et2 = enemy_types(client)
    except EmulatorError:
        return -1
    best_i, best_score = -1, 0.0
    for i, mid in enumerate(moves):
        if mid == 0:
            continue
        try:
            power, mtype = move_power_type(client, mid)
        except EmulatorError:
            continue
        if power == 0:  # status move — no damage
            continue
        score = power * effectiveness(mtype, et1, et2)
        if score > best_score:
            best_score, best_i = score, i
    return best_i


# 2x2 move grid: slot0=top-left, 1=top-right, 2=bottom-left, 3=bottom-right.
_SLOT_NAV = {0: (), 1: ("Right",), 2: ("Down",), 3: ("Down", "Right")}


def move_select_sequence(best_slot: int) -> tuple[str, ...]:
    """Button sequence to select a specific move slot from the FIGHT menu.

    Resets the battle menu cursor to FIGHT (Up,Up,Left), A opens the move
    submenu, Up+Left forces the submenu cursor to slot 0 (robust to the game
    remembering the last-used move), then navigates to the target slot and A.
    """
    nav = _SLOT_NAV.get(best_slot, ())
    return ("Up", "Up", "Left", "A", "Up", "Left") + nav + ("A",)
