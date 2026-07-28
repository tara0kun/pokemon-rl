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
PARTY_STRUCT_SIZE = 100
PARTY_SIZE = 6
PARTY_HP_OFFSET = 0x56         # u16 current HP (outside the encrypted substructs)
GBATTLEMONS = 0x02024084       # BattleMon[], 0x58 bytes each; [1] = opponent
BATTLEMON_SIZE = 0x58
BATTLEMON_SPECIES = 0x00       # u16 internal species id (decrypted in-battle;
                               # gBattleMons is NOT the encrypted party struct)
BATTLEMON_MOVES = 0x0C         # 4 x u16 (active battler's current moves)
BATTLEMON_TYPE1 = 0x21         # u8
BATTLEMON_TYPE2 = 0x22         # u8
BATTLEMON_HP = 0x28            # u16 current HP
BATTLEMON_MAXHP = 0x2C         # u16 max HP
BATTLEMON_PP = 0x24            # 4 x u8 current PP of the active battler's moves
BATTLEMON_LEVEL = 0x2A         # u8 level (between hp u16@0x28 and maxHP u16@0x2C;
                               # live-verified 07-26: read 47/6 for L47 vs L6)
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
    """Type-chart damage multiplier of an attacking type vs a (possibly
    dual-type) defender.

    Why: in gen 3 both of the defender's types apply multiplicatively, so we
    take the attacker's row in _CHART (default 1.0 for unlisted pairings) and
    multiply the def_t1 and def_t2 lookups, skipping the second when the mon
    is single-typed (def_t2 == def_t1) so a 0.5x/2x isn't squared. Returns 0.0
    for an immunity, up to 4.0 for a double super-effective hit.
    """
    row = _CHART.get(atk_type, {})
    mult = row.get(def_t1, 1.0)
    if def_t2 != def_t1:
        mult *= row.get(def_t2, 1.0)
    return mult


def enemy_types(client: MGBAClient) -> tuple[int, int]:
    """(type1, type2) of the opponent's active battler, feeding effectiveness().

    Why gBattleMons[1]: slot 0 is the player's active mon and slot 1 is the
    opponent's, so we offset one BATTLEMON_SIZE before reading the type bytes.
    """
    a = GBATTLEMONS + BATTLEMON_SIZE  # gBattleMons[1] = opponent
    return client.read8(a + BATTLEMON_TYPE1), client.read8(a + BATTLEMON_TYPE2)


def enemy_species(client: MGBAClient) -> int:
    """Internal species id of the opponent's active battler (gBattleMons[1]).

    Reads the u16 at offset +0x00 of the in-battle mon struct. Unlike
    gPlayerParty (encrypted, personality-keyed substructs), gBattleMons is the
    game's already-decrypted working copy, so the species is a plain u16 with no
    XOR/permutation decode — the same slot-1 offset enemy_types() uses. Feeds the
    catch_woods species gate (Shroomish 306 / Slakoth 364 = keep, else flee).
    Returns 0 on a read error so callers treat "unknown" as "not a target".
    """
    try:
        return client.read16(GBATTLEMONS + BATTLEMON_SIZE + BATTLEMON_SPECIES)
    except EmulatorError:
        return 0


def enemy_hp(client: MGBAClient) -> tuple[int, int]:
    """(current_hp, max_hp) of the opponent's active battler, read as u16s.

    Uses the same gBattleMons[1] offset as enemy_types() (slot 1 = opponent).
    """
    a = GBATTLEMONS + BATTLEMON_SIZE
    return client.read16(a + BATTLEMON_HP), client.read16(a + BATTLEMON_MAXHP)


def active_hp(client: MGBAClient) -> int:
    """Current HP of the player's ACTIVE battler (gBattleMons[0]).

    0 => the lead fainted and the game is on the 'choose a POKEMON' send-out
    screen; >0 => it is our turn to pick a move. Used to tell those two
    battle sub-states apart from reliable RAM instead of the flaky
    battle_menu vision signal (the H6a opening-thrash root cause)."""
    return client.read16(GBATTLEMONS + BATTLEMON_HP)


def active_level(client: MGBAClient) -> int:
    """Level of the player's ACTIVE battler (gBattleMons[0]).

    Feeds the over-level fight gate: with a traversal-grade level gap the
    loop fights (one best_move commit ends the battle) instead of running
    FLEE_SEQ's RUN navigation, whose per-press fragility stalled two Rusturf
    Tunnel battles (07-26)."""
    return client.read8(GBATTLEMONS + BATTLEMON_LEVEL)


def enemy_level(client: MGBAClient) -> int:
    """Level of the opponent's active battler (gBattleMons[1] — same slot-1
    offset as enemy_types/enemy_hp)."""
    return client.read8(GBATTLEMONS + BATTLEMON_SIZE + BATTLEMON_LEVEL)


def player_battler_hps(client: MGBAClient, double: bool = False) -> list[int]:
    """Current HP of the player's active battler(s).

    Battler slots interleave sides — 0/2 are ours, 1/3 the foe's — so in a
    double battle our SECOND mon is index 2. active_hp() reads index 0 only
    and is therefore blind to it fainting (the Route111 Twins soft-lock).
    """
    slots = (0, 2) if double else (0,)
    return [
        client.read16(GBATTLEMONS + b * BATTLEMON_SIZE + BATTLEMON_HP)
        for b in slots
    ]


def is_egg(client: MGBAClient, slot: int) -> bool:
    """Whether a party slot holds an EGG rather than a usable Pokemon.

    An egg has HP > 0 but can never be sent into battle, so an HP-only count
    would see a replacement that does not exist. The flag is bit 30 of the IV
    word in the encrypted Misc ('M') substruct — same personality-keyed decode
    as the species read in state.py.
    """
    base = PLAYER_PARTY_ADDR + slot * PARTY_STRUCT_SIZE
    pv = client.read32(base + 0x00)
    key = pv ^ client.read32(base + 0x04)
    m_off = 0x20 + _PERMS[pv % 24].index("M") * 12
    return bool((client.read32(base + m_off + 4) ^ key) & (1 << 30))


def sendable_party_count(client: MGBAClient, party_count: int) -> int:
    """Party members that could actually be sent into battle: HP > 0 and not an
    egg. Tells "a benched mon can replace the fainted one" from "this is all we
    have" — a double battle with no replacement keeps playing 1v2 and must not
    be driven into the party list.

    HP lives outside the encrypted substructs, so only the egg check decodes.
    """
    return sum(
        1
        for slot in range(max(0, min(party_count, PARTY_SIZE)))
        if client.read16(
            PLAYER_PARTY_ADDR + slot * PARTY_STRUCT_SIZE + PARTY_HP_OFFSET
        ) > 0
        and not is_egg(client, slot)
    )


def double_battle_needs_send_out(client: MGBAClient, party_count: int) -> bool:
    """True when a double battle is parked on the 'choose a POKEMON' list.

    One of our two battlers is down AND a benched mon is alive to replace it.
    Both halves matter: without the faint check we would drive the party list
    while it is our turn to attack; without the bench check we would drive it
    during a legitimate 1v2 (no replacement exists, so the game never asks).
    """
    hps = player_battler_hps(client, double=True)
    if not any(h == 0 for h in hps):
        return False
    alive_battlers = sum(1 for h in hps if h > 0)
    return sendable_party_count(client, party_count) > alive_battlers


def active_move_ids(client: MGBAClient) -> list[int]:
    """The four move IDs of the player's ACTIVE battler (gBattleMons[0]).

    Read from the in-battle mon struct, not gPlayerParty[0], so it stays
    correct after the lead faints and a different party member is sent out.
    """
    a = GBATTLEMONS  # gBattleMons[0] = player's active battler
    return [client.read16(a + BATTLEMON_MOVES + i * 2) for i in range(4)]


def active_pp(client: MGBAClient) -> list[int]:
    """Current PP of the player's ACTIVE battler's four move slots.

    best_move_index skips 0-PP moves: selecting a depleted move only pops
    'There's no PP left for this move!' and the turn never resolves, stalling
    the whole battle (grinding one move down to 0 PP on Route106 was exactly
    the Brawly-vs-Meditite stall)."""
    a = GBATTLEMONS
    return [client.read8(a + BATTLEMON_PP + i) for i in range(4)]


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
        pp = active_pp(client)
        et1, et2 = enemy_types(client)
    except EmulatorError:
        return -1
    best_i, best_score = -1, 0.0
    for i, mid in enumerate(moves):
        if mid == 0:
            continue
        if pp[i] == 0:  # depleted — selecting it stalls on "no PP left"
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
    """Self-correcting button sequence to select a move slot from ANY screen.

    Leading B,B first backs out of whatever wrong sub-screen we might be on —
    a SUMMARY page, the POKEMON list, or the BAG that the slot-2/3 Down/Right
    nav can accidentally open when fired a frame off the FIGHT menu (two levels
    deep needs two B's; harmless at the top battle menu / in dialogue). Then
    Up,Up,Left puts the cursor on FIGHT, A opens the move submenu, Up+Left
    forces its cursor to slot 0 (robust to the game remembering the last move),
    then the per-slot nav and A. Being self-correcting lets it fire without a
    vision confirm, so a best move in a bottom slot (e.g. Pursuit once Pound is
    out of PP) still gets picked instead of stalling.
    """
    nav = _SLOT_NAV.get(best_slot, ())
    return ("B", "B", "Up", "Up", "Left", "A", "Up", "Left") + nav + ("A",)
