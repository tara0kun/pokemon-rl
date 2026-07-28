"""RAM bridge — read player position / map / battle-state from emulator memory.

Targets Pokemon Emerald (USA, Europe) ROM. Addresses from pokeemerald decomp.

References:
- gSaveBlock1Ptr at IWRAM 0x03005D8C (pointer, 32-bit LE)
- SaveBlock1 layout:
    0x00 (2)  pos.x
    0x02 (2)  pos.y
    0x04 (1)  location.mapGroup
    0x05 (1)  location.mapNum
- gBattleTypeFlags candidate addresses (we probe all and use first non-zero):
    EN Emerald likely values: 0x020243CC / 0x020238F0 / 0x02022FEC

The pointer can be 0 before the save block is initialized (title screen),
in which case we return zeros.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .io import EmulatorError, MGBAClient

SAVEBLOCK1_PTR_ADDR = 0x03005D8C
PLAYER_PARTY_ADDR = 0x020244EC  # gPlayerParty[0] in Emerald (USA)
# gPlayerPartyCount (0x020244E9) sits 3 bytes before gPlayerParty (the decomp
# u8 + 3 padding). This is the LIVE working count the game uses everywhere and
# is a fixed BSS global (no DMA relocation). Read party count from here, NOT
# from SaveBlock1.playerPartyCount (see SB1_PLAYER_PARTY_COUNT below), which is
# only synced on save and reads stale between a catch/deposit and the next save.
PLAYER_PARTY_COUNT_ADDR = PLAYER_PARTY_ADDR - 3  # 0x020244E9
POKEMON_STRUCT_SIZE = 100
POKEMON_LEVEL_OFFSET = 0x54
POKEMON_HP_OFFSET = 0x56
POKEMON_MAX_HP_OFFSET = 0x58

# gObjectEvents — 16 NPC slots × 36 bytes
# pokeemerald: gObjectEvents at 0x02037350
# struct ObjectEvent { ... currentCoords at 0x10 (2x u16) ... }
OBJECT_EVENTS_ADDR = 0x02037350
OBJECT_EVENT_SIZE = 0x24  # 36 bytes
OBJECT_EVENT_COUNT = 16
OE_FLAGS_OFFSET = 0x00            # u32 bitfield (active=bit0)
OE_GRAPHICS_ID_OFFSET = 0x05      # u8 spriteId
OE_MAP_NUM_OFFSET = 0x09          # u8 mapNum
OE_MAP_GROUP_OFFSET = 0x0A        # u8 mapGroup
OE_CURRENT_X_OFFSET = 0x10        # s16 currentCoords.x
OE_CURRENT_Y_OFFSET = 0x12        # s16 currentCoords.y

# gBackupMapLayout (IWRAM, Emerald USA BPEE) — the LIVE metatile grid, which
# reflects dynamically-changed tiles (e.g. Mauville Gym electric barriers raised/
# lowered by floor switches). struct { s32 width; s32 height; u16 *map; }; .map
# always points to sBackupMapData (0x02032318). Verified live: for Route117 the
# padded width/height (mapW+15, mapH+14) matched the static dims and .map read
# 0x02032318 exactly. Each grid u16: metatile_id 0x03FF, COLLISION 0x0C00 (bits
# 10-11; non-zero = wall), elevation 0xF000. Real map coord (x,y) sits at grid
# (x+7, y+7). Sources: pret/pokeemerald symbols branch + global.fieldmap.h.
BACKUP_MAP_LAYOUT_ADDR = 0x03005DC0
MAP_GRID_OFFSET = 7  # MAP_OFFSET — border padding around the real map
MAPGRID_COLLISION_MASK = 0x0C00
MAPGRID_UNDEFINED = 0x03FF


def read_live_walkable_overrides(
    client: MGBAClient, static_info,
) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
    """Compare the LIVE collision grid to the static map.bin collision.

    Returns (extra_walkable, extra_blocked): tiles whose live walkability
    DIFFERS from static. extra_walkable = static-blocked but live-open (a
    lowered barrier the BFS may now cross); extra_blocked = static-open but
    live-closed (a raised barrier the BFS must avoid). Both empty on any read
    failure or dimension mismatch (so nav safely falls back to static). Lets
    the agent solve dynamic-barrier puzzles like the Mauville Gym, where the
    switch scripts flip the 0x0C00 collision bits in this live grid.
    """
    try:
        width = client.read32(BACKUP_MAP_LAYOUT_ADDR)
        height = client.read32(BACKUP_MAP_LAYOUT_ADDR + 4)
        map_ptr = client.read32(BACKUP_MAP_LAYOUT_ADDR + 8)
    except EmulatorError:
        return set(), set()
    # Sanity: padded dims must match the loaded static map (+15 wide, +14 tall)
    # and the buffer pointer must be the known EWRAM grid. A mismatch means a
    # map transition / bad read -> fall back to static.
    if (
        width != static_info.width + 15
        or height != static_info.height + 14
        or not (0x02000000 <= map_ptr < 0x02040000)
    ):
        return set(), set()
    try:
        raw = client.read_range(map_ptr, width * height * 2)
    except EmulatorError:
        return set(), set()
    if len(raw) < width * height * 2:
        return set(), set()
    extra_walkable: set[tuple[int, int]] = set()
    extra_blocked: set[tuple[int, int]] = set()
    off_base = MAP_GRID_OFFSET
    for y in range(static_info.height):
        gy = y + off_base
        row = (gy * width)
        for x in range(static_info.width):
            gx = x + off_base
            off = (row + gx) * 2
            entry = raw[off] | (raw[off + 1] << 8)
            live_walk = (
                entry != MAPGRID_UNDEFINED
                and (entry & MAPGRID_COLLISION_MASK) == 0
            )
            stat_walk = static_info.walkable(x, y)
            if live_walk and not stat_walk:
                extra_walkable.add((x, y))
            elif not live_walk and stat_walk:
                extra_blocked.add((x, y))
    return extra_walkable, extra_blocked


# SaveBlock1 inner offsets (pokeemerald struct SaveBlock1)
# These are referenced relative to *gSaveBlock1Ptr.
SB1_PLAYER_PARTY_COUNT = 0x0234  # SaveBlock1 mirror; SAVE-only sync (stale
                                 # between catch/deposit and next save). Do NOT
                                 # use for live count -- read PLAYER_PARTY_COUNT_ADDR.
SB1_FLAGS_OFFSET = 0x1270        # u8 flags[NUM_FLAG_BYTES]
SB1_VARS_OFFSET = 0x1408         # u16 vars[NUM_VARS]
SB1_BAG_ITEMS = 0x0560           # struct ItemSlot bagPocket_Items[30]
NUM_FLAG_BYTES = 0x12C           # 300 bytes = 2400 event flags (Emerald)

BATTLE_FLAGS_CANDIDATES = [
    # 0x02022FEC = US Emerald gBattleTypeFlags (JP 0x02022E90 + US->JP
    # offset 0x15C, cross-checked against old-branch pokemon_env.py).
    # 30ed435 (06-24) added it to fix the Roxanne RAM false-negative
    # (0x020243CC reads 0 during the move-select screen); the "36 fix"
    # (06-29) removed it claiming it never clears; restored 07-01.
    # NUANCE (verified 07-01): it clears on a NORMAL battle exit but does
    # NOT clear on a WHITEOUT/loss teardown — it lingers non-zero (0xc)
    # while walking the overworld afterwards. So this flag alone is NOT a
    # reliable in_battle signal; _read_battle_flags gates it on gMain's
    # game-mode callback below.
    0x02022FEC,
    # Unverified fallbacks (some other battle global): both read 0 during
    # the Roxanne fight (the original false-negative) and 0 in overworld.
    0x020243CC,
    0x020238F0,
]

# gMain.callback2 (US Emerald) — the game-mode function pointer. It is
# CB2_Overworld in the field and CB2_BattleMain (etc.) in battle, flipping
# immediately on battle entry/exit, so it distinguishes "actually in battle
# now" from "stale gBattleTypeFlags left over after a whiteout". This is the
# self-clearing signal the old-branch pokemon_env.py used (cb2_overworld).
GMAIN_CB2_ADDR = 0x030022C4
# Field/overworld callback2 values where a set gBattleTypeFlags must NOT be
# read as in-battle. 0x08085E5D = CB2_Overworld (US), live-observed 07-01.
CB2_OVERWORLD_SET = frozenset({0x08085E5D})
# gBattleTypeFlags is stale after a battle (never cleared). The overworld guard
# above catches the field, but a MENU opened later (Pokedex, region map, bag)
# has its own callback2 and was slipping through -> a stale double-battle flag
# read as in-battle froze the loop mashing A in the Pokedex for 4000 turns
# (07-16). Until the battle-callback whitelist (H-cb2) is captured, treat known
# menu callbacks as not-in-battle too. 0x080BB775 = Pokedex/region-map detail,
# live-observed 07-16 (cb2 stable 6/6 while the screen showed the town map).
CB2_MENU_SET = frozenset({0x080BB775})
# Battle callback2 whitelist (the "H-cb2" the comment above waited for).
# 0x08038421 = CB2_BattleMain (US), the single value observed across all 33
# battle episodes (557 turns) of the 2026-07-22 grind session. gBattleTypeFlags
# is STALE after a battle (never zero-cleared), so a set flag must NOT be read
# as in-battle unless the game mode is ACTUALLY a battle. Flipping to a
# whitelist (in-battle ONLY when cb2 is a battle callback) is strictly safer
# than the old menu-exclusion list: an UNKNOWN callback (PokeNav 0x081C7401,
# Trainer Card 0x080C2711, the level-up "forget move" screen 0x081BFAB5) now
# reads NOT-in-battle instead of trapping the loop A-mashing a stale flag (the
# ~3700-turn PokeNav imprisonment of 2026-07-22). Unknown battle variants are
# caught by the vision battle_menu latch in the loop.
CB2_BATTLE_SET = frozenset({
    0x08038421,   # CB2_BattleMain (the battle engine proper)
    0x081B01B1,   # in-battle party screen ("send out which POKeMON?" after a
                  # faint). Live 2026-07-24: gBattleTypeFlags=0xC here, so the
                  # flags gate below keeps the FIELD party menu (flags=0) out of
                  # battle mode even if it shares this callback. Without this the
                  # switch screen read as unknown_ui and ui_escape B-mashed it,
                  # jamming the loop; now the SEND_OUT_SEQ handler drives it.
})

# Game-mode buckets used to gate tile_map recording, the unknown-UI escape,
# and battle dispatch.
MODE_OVERWORLD = "overworld"
MODE_BATTLE = "battle"
MODE_MENU = "menu"
MODE_UNKNOWN_UI = "unknown_ui"


def game_mode_for_cb2(cb2: int) -> str:
    if cb2 in CB2_OVERWORLD_SET:
        return MODE_OVERWORLD
    if cb2 in CB2_BATTLE_SET:
        return MODE_BATTLE
    if cb2 in CB2_MENU_SET:
        return MODE_MENU
    return MODE_UNKNOWN_UI


BATTLE_TYPE_TRAINER = 0x0008
MOVE_ROCK_SMASH = 249  # MOVE_ROCK_SMASH (moves.h)

# Monotonic badge latch, immune to the SaveBlock1 DMA drop-flicker: a
# relocation-time read intermittently returns 0 for a badge flag that IS set,
# which dips badge_count below its true value for a frame and fires stale
# pre-badge goals (mauville_gym_wattson / enter_rustboro_gym / reach_mauville
# fired ~4% of Route111 turns). Badges are never un-earned, so once a badge bit
# is seen True keep it True. Process-lifetime; a fresh valid read re-latches
# after a restart. Safe as a "genuinely-True past event" latch (NOT a
# future-event `not X` gate). reset_flag_latches() is for tests.
_BADGE_BITS_LATCHED: set[int] = set()

# Rise-flicker guard for future-event GATE flags (0x333 theft, 0x8B Mt.Chimney).
# Unlike the past-event latches, a spurious True on these advances the goal PAST
# an event that has not happened: a single garbage/crossed read of 0x333 (under
# multi-loop socket contention, 07-18) reported theft=True, goals._latched wrote
# meteor_theft_done.marker, and the agent skipped Meteor Falls straight to the
# (grunt-blocked) cable car. So read_state reports these True only after
# _RISE_CONFIRM_N CONSECUTIVE True reads; a genuinely-set flag reads True for
# many turns so the guard costs nothing real. Counter is per-flag, in-process.
_RISE_CONFIRM: dict[str, int] = {}
_RISE_CONFIRM_N = 5


def _rise_confirmed(key: str, raw: bool, stable: bool) -> bool:
    """Confirm a future-event gate flag rise only across STABLE overworld frames.

    N=3 was defeated once already: during the Route112 cable-car RIDE (a scripted
    cutscene, cb2 != CB2_Overworld) 0x8B read a garbage True for 3+ consecutive
    read_state calls and false-latched mtchimney_done, skipping the Team Magma
    battle (07-18). The garbage correlates with cutscene/transition frames, so a
    non-stable frame (cb2 not CB2_Overworld: battle, menu, cable car, warp fade)
    RESETS the counter — only a run of N genuine overworld True reads latches. The
    real flag is set in the overworld right after its event and holds True for
    hundreds of turns, so N=5 stable confirmations cost nothing."""
    if raw and stable:
        _RISE_CONFIRM[key] = _RISE_CONFIRM.get(key, 0) + 1
    else:
        _RISE_CONFIRM[key] = 0
    return _RISE_CONFIRM[key] >= _RISE_CONFIRM_N


def reset_flag_latches() -> None:
    global _PARTY0_LEVEL_LAST_GOOD
    global _BALLS_LAST_GOOD, _BALLS_FALL_PENDING, _BALLS_FALL_STREAK
    global _BALLS_FALL_GRADUAL
    _BADGE_BITS_LATCHED.clear()
    _RISE_CONFIRM.clear()
    _PARTY0_LEVEL_LAST_GOOD = 0
    _BALLS_LAST_GOOD = -1
    _BALLS_FALL_PENDING = -1
    _BALLS_FALL_STREAK = 0
    _BALLS_FALL_GRADUAL = True


# Drop-flicker guard for party0_level (same DMA-corruption family as the badge
# latch above): a contended/mid-DMA read8 intermittently returns 0 for the
# lead's level. A REAL slot-0 mon is never level 0, but every `party0_level <
# X` goal gate reads 0 as "under-leveled" — on 07-24 each lvl=0 frame
# resurrected the retired grind_fiery_path goal INSIDE Lavaridge Gym and
# yanked mapbfs backward toward B1F for a turn (the (0,13)-(0,15) up/down
# oscillation against the hole climb). Unlike badges, level is NOT monotonic
# across lead swaps, so this is a last-good CARRY, not a max-latch: any
# positive read (a swapped-in lower-level lead included) replaces the carry;
# only the impossible 0 is substituted. Process-lifetime, like the badge latch.
_PARTY0_LEVEL_LAST_GOOD: int = 0


def _flicker_guarded_level(lv: int) -> int:
    global _PARTY0_LEVEL_LAST_GOOD
    if lv == 0 and _PARTY0_LEVEL_LAST_GOOD > 0:
        return _PARTY0_LEVEL_LAST_GOOD
    if lv > 0:
        _PARTY0_LEVEL_LAST_GOOD = lv
    return lv


# Fall-confirm guard for bag_pokeball_count — the third member of the DMA-
# flicker family (badge max-latch, level last-good carry). The balls-pocket
# scan walks slots via the DMA-relocating SaveBlock1 pointer; a mid-relocation
# frame reads slot ids as 0 and reports 0 balls. Measured live 07-26 at a
# settled Rustboro state: [15, 15, 0, 0, 15, 15, 15, 0, 15, 15] (~30% of
# frames). Each flicker-0 dropped catch_water_route104's `balls > 0` gate for
# one frame and a lower goal yanked nav — the Rustboro<->Route104 boundary
# oscillation (40 turns, zero grass encounters).
#
# Unlike level, 0 IS a legitimate ball count (before buying / after the last
# throw), so a plain last-good carry would mask a genuine drain forever.
# Asymmetric contract instead:
#   RISE (or first read): trusted immediately — buying must register at once.
#   FALL: believed only when BOTH hold:
#     (a) _BALLS_FALL_CONFIRM_N CONSECUTIVE reads at or below the pending
#         lower value — a lone 0 amid 15s never confirms (any >= last_good
#         read resets the streak); AND
#     (b) the fall path is GRADUAL: every fall-frame step (from last_good on
#         entry, then from the pending value) is <= _BALLS_FALL_MAX_STEP.
#         Physical invariant: a real drain loses at most ONE ball per throw
#         turn and read_state runs every turn, so consecutive reads can
#         genuinely differ by at most 1 (2 allows one dropped frame). The
#         in-battle flicker (07-27) produces RUNS of >=5 consecutive raw-0
#         reads — measured [15,...,15,0,0,0,0,0] mid-catch — which defeats
#         the streak alone, but its 15->0 entry step is physically
#         impossible, so the step gate marks the run poisoned and it can
#         never confirm; the next >= last_good read resets. A poisoned run
#         can also REBOUND to an intermediate value (a genuine 14 after a
#         garbage 0-run): n > pending restarts the run with a fresh entry
#         check against last_good, so the genuine drain still registers.
# A genuine drain (3,2,1,0,0,0) passes both gates and confirms after N
# frames — the goals gate on coarse thresholds (>0, <5), so the lag is
# invisible to them.
_BALLS_LAST_GOOD: int = -1        # -1 = nothing confirmed yet (trust first read)
_BALLS_FALL_PENDING: int = -1
_BALLS_FALL_STREAK: int = 0
_BALLS_FALL_GRADUAL: bool = True
_BALLS_FALL_CONFIRM_N = 5
_BALLS_FALL_MAX_STEP = 2


def _flicker_guarded_pokeballs(n: int) -> int:
    global _BALLS_LAST_GOOD, _BALLS_FALL_PENDING, _BALLS_FALL_STREAK
    global _BALLS_FALL_GRADUAL
    if _BALLS_LAST_GOOD < 0 or n >= _BALLS_LAST_GOOD:
        _BALLS_LAST_GOOD = n
        _BALLS_FALL_PENDING = -1
        _BALLS_FALL_STREAK = 0
        _BALLS_FALL_GRADUAL = True
        return n
    # n < last-good: a fall frame.
    if _BALLS_FALL_PENDING >= 0 and n <= _BALLS_FALL_PENDING:
        # continuing the run downward: step from the pending value
        step = _BALLS_FALL_PENDING - n
        _BALLS_FALL_STREAK += 1
    else:
        # entering a fall run (or rebounding above the poisoned pending):
        # step from the last believed value, fresh streak + gradual state
        step = _BALLS_LAST_GOOD - n
        _BALLS_FALL_STREAK = 1
        _BALLS_FALL_GRADUAL = True
    if step > _BALLS_FALL_MAX_STEP:
        _BALLS_FALL_GRADUAL = False
    _BALLS_FALL_PENDING = n
    if _BALLS_FALL_GRADUAL and _BALLS_FALL_STREAK >= _BALLS_FALL_CONFIRM_N:
        _BALLS_LAST_GOOD = n
        _BALLS_FALL_PENDING = -1
        _BALLS_FALL_STREAK = 0
        return n
    return _BALLS_LAST_GOOD


def _balls_carry() -> int:
    """Last believed ball count for frames with NO bag observation at all
    (invalid SaveBlock1 pointer / failed coordinate read — the read_state
    early returns). NOT a guard update: an unreadable frame is the ABSENCE of
    an observation, not a 0-read, so it must neither feed the fall streak nor
    expose the GameState field default of 0. Live 07-27: the in-battle stall's
    guard-bypassing 0s came from exactly these partial frames (the guarded
    main path held 15) — the badge_count=len(_BADGE_BITS_LATCHED) wiring in
    the same early returns is the precedent."""
    return _BALLS_LAST_GOOD if _BALLS_LAST_GOOD >= 0 else 0

# Pokemon substruct order by personality % 24 (Gen 3 box-mon encryption).
_SUBSTRUCT_PERMS = [
    "GAEM", "GAME", "GEAM", "GEMA", "GMAE", "GMEA",
    "AGEM", "AGME", "AEGM", "AEMG", "AMGE", "AMEG",
    "EGAM", "EGMA", "EAGM", "EAMG", "EMGA", "EMAG",
    "MGAE", "MGEA", "MAGE", "MAEG", "MEGA", "MEAG",
]


def _read_party_move_ids(client: MGBAClient, slot: int) -> list[int]:
    """Decrypt gPlayerParty[slot]'s 4 move IDs from the Attacks substruct."""
    base = PLAYER_PARTY_ADDR + slot * POKEMON_STRUCT_SIZE
    pv = client.read32(base + 0x00)
    otid = client.read32(base + 0x04)
    key = pv ^ otid
    a_off = 0x20 + _SUBSTRUCT_PERMS[pv % 24].index("A") * 12
    w0 = client.read32(base + a_off) ^ key
    w1 = client.read32(base + a_off + 4) ^ key
    return [w0 & 0xFFFF, (w0 >> 16) & 0xFFFF, w1 & 0xFFFF, (w1 >> 16) & 0xFFFF]


def read_party0_damaging_pp(client: MGBAClient) -> int:
    """Sum of PP across the party leader's DAMAGING moves (power>0 in the ROM
    gBattleMoves table). Returns -1 if unreadable or the decrypt looks like
    garbage (out-of-range move id / PP), so the caller treats an unknown value
    as "keep grinding". The 4 PP bytes sit in the Attacks substruct's 3rd word
    (a_off+8), same XOR key as the move ids (see _read_party_move_ids)."""
    from . import battle_moves as _bm
    try:
        base = PLAYER_PARTY_ADDR
        pv = client.read32(base + 0x00)
        otid = client.read32(base + 0x04)
        key = pv ^ otid
        a_off = 0x20 + _SUBSTRUCT_PERMS[pv % 24].index("A") * 12
        w0 = client.read32(base + a_off) ^ key
        w1 = client.read32(base + a_off + 4) ^ key
        w2 = client.read32(base + a_off + 8) ^ key
    except EmulatorError:
        return -1
    moves = [w0 & 0xFFFF, (w0 >> 16) & 0xFFFF, w1 & 0xFFFF, (w1 >> 16) & 0xFFFF]
    pps = [w2 & 0xFF, (w2 >> 8) & 0xFF, (w2 >> 16) & 0xFF, (w2 >> 24) & 0xFF]
    # A mis-decrypt (DMA relocation flicker) yields impossible ids/PP -> untrust.
    if any(m > 559 for m in moves) or any(p > 64 for p in pps):
        return -1
    total = 0
    for mid, pp in zip(moves, pps):
        if mid == 0:
            continue
        try:
            power, _ = _bm.move_power_type(client, mid)
        except EmulatorError:
            return -1
        if power > 0:
            total += pp
    return total


def _read_saveblock1_ptr(client: MGBAClient, tries: int = 4) -> int | None:
    """Return a stable SaveBlock1 pointer, ignoring DMA relocation transients."""
    prev = None
    for _ in range(tries):
        try:
            ptr = client.read32(SAVEBLOCK1_PTR_ADDR)
        except EmulatorError:
            return None
        if 0x02000000 <= ptr < 0x02040000 and ptr == prev:
            return ptr
        prev = ptr
    return None


@dataclass
class GameState:
    map_group: int
    map_num: int
    x: int
    y: int
    saveblock1_valid: bool
    in_battle: bool = False
    battle_flags: int = 0
    game_cb2: int = 0  # gMain.callback2 (game-mode fn ptr); overworld/menu/battle
    # Coarse game mode derived from game_cb2 (game_mode_for_cb2): "overworld" /
    # "battle" / "menu" / "unknown_ui". Gates tile_map recording and the loop's
    # unknown-UI escape (an unwhitelisted callback = a menu we can be trapped in).
    game_mode: str = MODE_OVERWORLD
    party0_level: int = 0
    party0_hp: int = 0
    party0_max_hp: int = 0
    # Sum of PP across the leader's DAMAGING moves (power>0). -1 = unreadable
    # (garbage decrypt / DMA flicker). The Fiery grind yields to a PC heal when
    # this is 0: a lead with 0 damaging PP makes best_move_index return -1, and
    # the grind then flees every wild forever (a healthy-but-zero-XP stall).
    party0_damaging_pp: int = -1
    # Decrypted species id — Gen 3 INTERNAL numbering, NOT National Dex
    # (internal: Grovyle=278, Sceptile=279, Lombre=296; NatDex 279 is
    # Pelipper — that mixup misdiagnosed the lead twice). Name lookup must
    # use the ROM's gSpeciesNames order (see scratch/party_dump.py).
    party0_species: int = 0
    party_count: int = 0
    flag_birch_met: bool = False
    flag_starter_received: bool = False
    # FLAG_RECOVERED_DEVON_GOODS (0x8F): set by RusturfTunnel scripts.inc
    # after beating the Aqua grunt (Peeko rescued, Devon Goods returned).
    # This is the story gate for Mr.Briney's sail to Dewford.
    flag_devon_goods_recovered: bool = False
    # FLAG_DELIVERED_STEVEN_LETTER (0xBD): set in GraniteCave_StevensRoom after
    # handing Steven the Letter (also gives TM47 Steel Wing). Story gate for
    # Mr.Briney's Dewford->Slateport(Route109) sail — decomp DewfordTown/
    # scripts.inc goto_if_unset FLAG_DELIVERED_STEVEN_LETTER.
    flag_steven_letter_delivered: bool = False
    # FLAG_DELIVERED_DEVON_GOODS (0x95): set at Slateport Oceanic Museum 2F on
    # handoff to Capt. Stern; clears the Route110 Team Aqua block to Mauville.
    flag_devon_goods_delivered: bool = False
    # FLAG_DOCK_REJECTED_DEVON_GOODS (0x94): set when Dock at Stern's Shipyard
    # redirects you to find Capt. Stern (who is at the Oceanic Museum). Sequences
    # the Devon Goods errand — visit the Dock first, then the museum.
    flag_dock_rejected_devon: bool = False
    # --- Lavaridge / Flannery (Badge 4) arc gates (canon docs/PLAN_lavaridge_flannery) ---
    # FLAG_HIDE_ROUTE_112_TEAM_MAGMA (0x333): set by the Meteor Falls meteorite-
    # theft cutscene; removes the 2 grunts guarding the Route112 Cable Car.
    flag_route112_magma_cleared: bool = False
    # FLAG_DEFEATED_EVIL_TEAM_MT_CHIMNEY (0x8B): set after beating Tabitha+Maxie at
    # Mt.Chimney; opens the north exit to Jagged Pass -> Lavaridge (the ONLY entry
    # to Lavaridge). Gate for descend_jagged_pass / reach_lavaridge.
    flag_mtchimney_magma_defeated: bool = False
    # FLAG_BADGE04_GET (0x86A, Heat Badge): set on beating Flannery. Retires the
    # whole Lavaridge arc.
    flag_badge04_get: bool = False
    # FLAG_RECEIVED_HM_ROCK_SMASH (0x6B): set when the Mauville House1 RockSmashDude
    # gives HM06. Gate for get_rock_smash (retire once received).
    flag_rock_smash_hm: bool = False
    # party_moves[slot] = [4 move ids] (decrypted Attacks substruct) for each
    # party member. Used to tell whether any Pokemon KNOWS Rock Smash (field-move
    # gate) and to confirm the HM-teach sub-task succeeded. Empty on read failure.
    party_moves: list[list[int]] = field(default_factory=list)
    bag_pokeball_count: int = 0
    # UNGUARDED live Poke Ball count for the catch state machine's per-turn
    # "a ball just left the bag" edge signal. The guarded bag_pokeball_count
    # above lags a real throw by up to _BALLS_FALL_CONFIRM_N frames (by design,
    # to resist the in-battle DMA flicker) so it CANNOT detect a single-ball
    # throw promptly. The raw value flickers to 0 (not to count-1) during DMA,
    # so the SM filters with "exactly ref-1 and >0, 2 consecutive". See the
    # drop-robust catch flow (2026-07-27, Codex+Claude cross-vendor design).
    bag_pokeball_count_raw: int = 0
    bag_first_item_id: int = 0
    bag_first_item_qty: int = 0
    bag_heal_qty: int = 0          # HP restores in the Items pocket (H14)
    bag_super_potion_qty: int = 0  # SUPER_POTION only
    money: int = -1               # -1 = unreadable
    badge_count: int = 0
    total_event_flags: int = 0  # PWhiddy-style: sum of set bits across all flags
    event_flag_bytes_hex: str = ""  # PWhiddy v2 obs: full 300 bytes = 2400 bits
    npcs_on_map: list[tuple[int, int, int]] = field(
        default_factory=list,
    )  # (x, y, graphics_id) for NPCs on the SAME map as player; empty if unread

    @property
    def is_trainer_battle(self) -> bool:
        return self.in_battle and bool(self.battle_flags & BATTLE_TYPE_TRAINER)

    @property
    def is_wild_battle(self) -> bool:
        return self.in_battle and not self.is_trainer_battle

    @property
    def knows_rock_smash(self) -> bool:
        """True if any party member knows MOVE_ROCK_SMASH (249) — the field-move
        gate for smashing rocks."""
        return any(MOVE_ROCK_SMASH in moves for moves in self.party_moves)

    @property
    def party0_hp_frac(self) -> float:
        if self.party0_max_hp <= 0:
            return 1.0
        return max(0.0, min(1.0, self.party0_hp / self.party0_max_hp))

    @property
    def party0_critical(self) -> bool:
        """HP <= 25% — trigger for force-run from wild encounters."""
        return self.party0_max_hp > 0 and self.party0_hp_frac < 0.26

    def short(self) -> str:
        if not self.saveblock1_valid:
            base = "map=(?,?) pos=(?,?) [pre-save]"
        else:
            base = (
                f"map=({self.map_group},{self.map_num}) "
                f"pos=({self.x},{self.y})"
            )
        if self.in_battle:
            suffix = " [in_battle]"
            if self.is_trainer_battle:
                suffix += "[trainer]"
            else:
                suffix += "[wild]"
            return base + suffix
        return base


def _signed16(v: int) -> int:
    return v - 0x10000 if v >= 0x8000 else v


def read_npcs_on_map(
    client: MGBAClient, cur_map_group: int, cur_map_num: int,
) -> list[tuple[int, int, int]]:
    """Read all ACTIVE NPCs whose map matches the player's.

    Returns [(x, y, graphics_id), ...] for sprites currently on the
    same map. Empty list on read failure or when no NPCs are loaded.
    """
    out: list[tuple[int, int, int]] = []
    try:
        for i in range(OBJECT_EVENT_COUNT):
            base = OBJECT_EVENTS_ADDR + i * OBJECT_EVENT_SIZE
            flags = client.read32(base + OE_FLAGS_OFFSET)
            if not (flags & 0x1):
                continue
            mg = client.read8(base + OE_MAP_GROUP_OFFSET)
            mn = client.read8(base + OE_MAP_NUM_OFFSET)
            if mg != cur_map_group or mn != cur_map_num:
                continue
            x = client.read16(base + OE_CURRENT_X_OFFSET)
            y = client.read16(base + OE_CURRENT_Y_OFFSET)
            if x >= 0x8000:
                x -= 0x10000
            if y >= 0x8000:
                y -= 0x10000
            x -= 7
            y -= 7
            gid = client.read8(base + OE_GRAPHICS_ID_OFFSET)
            out.append((int(x), int(y), int(gid)))
    except EmulatorError:
        return out
    return out


def _read_battle_flags(client: MGBAClient) -> tuple[bool, int]:
    """Probe candidate addresses for gBattleTypeFlags.

    Empirically (English Emerald, USA), 0x020243CC and 0x020238F0 are
    both 0 while standing in the overworld. We declare in_battle=True
    only when one of them holds a value that fits the gBattleTypeFlags
    bitfield (non-zero, fits in a single 32-bit field, and stays under
    the typical max of 0x00010000 for the documented flag combinations).
    Stricter than "any non-zero" to avoid false positives from candidate
    addresses that turn out to be wrong.
    """
    for addr in BATTLE_FLAGS_CANDIDATES:
        try:
            v = client.read32(addr)
        except EmulatorError:
            continue
        if v == 0:
            continue
        if v >= 0x00010000:
            continue
        # gBattleTypeFlags is set but may be stale (whiteout / a menu opened
        # after a battle leaves it non-zero). Confirm we are ACTUALLY in a
        # battle via the game-mode callback WHITELIST: in-battle only when cb2
        # is a battle callback. This flips instantly on exit, unlike the flag,
        # and — unlike the old overworld/menu EXCLUSION list — an unknown menu
        # callback (PokeNav etc.) reads not-in-battle instead of imprisoning
        # the loop. Unknown battle variants are recovered by the loop's vision
        # battle_menu latch.
        try:
            cb2 = client.read32(GMAIN_CB2_ADDR)
        except EmulatorError:
            cb2 = None
        if cb2 is not None and cb2 in CB2_BATTLE_SET:
            return True, v
        return False, 0
    return False, 0


def read_state(client: MGBAClient) -> GameState:
    in_battle, flags = _read_battle_flags(client)
    try:
        cb2 = client.read32(GMAIN_CB2_ADDR)
    except EmulatorError:
        cb2 = 0

    ptr = _read_saveblock1_ptr(client)
    if ptr is None:
        # An invalid-pointer frame must still report the badges earned so far
        # (from the latch), not 0 -- else a badge-gated goal (reach_mauville:
        # badge_count < 3) fires on the flicker frame.
        return GameState(
            0, 0, 0, 0,
            saveblock1_valid=False,
            in_battle=in_battle,
            battle_flags=flags,
            badge_count=len(_BADGE_BITS_LATCHED),
            bag_pokeball_count=_balls_carry(),
            bag_pokeball_count_raw=_balls_carry(),
        )

    try:
        x = _signed16(client.read16(ptr + 0x00))
        y = _signed16(client.read16(ptr + 0x02))
        mg = client.read8(ptr + 0x04)
        mn = client.read8(ptr + 0x05)
    except EmulatorError:
        return GameState(
            0, 0, 0, 0,
            saveblock1_valid=False,
            in_battle=in_battle,
            battle_flags=flags,
            badge_count=len(_BADGE_BITS_LATCHED),
            bag_pokeball_count=_balls_carry(),
            bag_pokeball_count_raw=_balls_carry(),
        )

    try:
        lv = client.read8(PLAYER_PARTY_ADDR + POKEMON_LEVEL_OFFSET)
        hp = client.read16(PLAYER_PARTY_ADDR + POKEMON_HP_OFFSET)
        max_hp = client.read16(PLAYER_PARTY_ADDR + POKEMON_MAX_HP_OFFSET)
        if lv > 100 or max_hp > 1000:
            lv = hp = max_hp = 0
    except EmulatorError:
        lv = hp = max_hp = 0
    # hp is NOT carried (0 is a real faint); level 0 is always a bad read.
    lv = _flicker_guarded_level(lv)

    damaging_pp = read_party0_damaging_pp(client)

    # Decrypt party0 species (Pokemon Emerald box-pokemon encryption)
    # See 06-29 audit: agent lead was misidentified as Grovyle for 35+ hours.
    party0_species_id = 0
    try:
        pv = client.read32(PLAYER_PARTY_ADDR + 0x00)
        otid = client.read32(PLAYER_PARTY_ADDR + 0x04)
        key = pv ^ otid
        # Substruct order is determined by personality % 24.
        perms = [
            "GAEM", "GAME", "GEAM", "GEMA", "GMAE", "GMEA",
            "AGEM", "AGME", "AEGM", "AEMG", "AMGE", "AMEG",
            "EGAM", "EGMA", "EAGM", "EAMG", "EMGA", "EMAG",
            "MGAE", "MGEA", "MAGE", "MAEG", "MEGA", "MEAG",
        ]
        order = perms[pv % 24]
        g_idx = order.index("G")
        g_offset = 0x20 + g_idx * 12  # G substruct base
        enc1 = client.read32(PLAYER_PARTY_ADDR + g_offset)
        dec1 = enc1 ^ key
        party0_species_id = dec1 & 0xFFFF
        if party0_species_id > 1000:
            party0_species_id = 0
    except EmulatorError:
        party0_species_id = 0

    party_count = 0
    flag_birch = False
    flag_starter = False
    flag_devon = False
    flag_steven_letter = False
    flag_devon_delivered = False
    flag_dock_rejected = False
    flag_r112_magma = False
    flag_mtc_defeated = False
    flag_badge4 = False
    flag_rock_smash = False
    party_moves: list[list[int]] = []
    pokeballs = 0
    first_item_id = 0
    first_item_qty = 0
    bag_heal_qty = 0
    bag_super_potion_qty = 0
    money = -1
    badges = 0
    total_flags = 0
    flag_hex = ""
    try:
        # Live count from the fixed gPlayerParty* globals, NOT the SaveBlock1
        # mirror (which is stale between a catch and the next save -- Marill
        # caught 07-27 read live=6 but SB1 mirror still 5 until save).
        party_count = client.read8(PLAYER_PARTY_COUNT_ADDR)
        if party_count > 6:
            party_count = 0
        # Badges = event flags FLAG_BADGE01_GET (0x867) .. BADGE08 (0x86E).
        # Previously `badges` stayed hardcoded 0, so badge_count always read
        # 0 even after earning a badge (Stone Badge won 07-01 but reported
        # as 0). Count the set badge flags.
        for _bi in range(8):
            _fn = 0x867 + _bi
            try:
                _fb = client.read8(ptr + SB1_FLAGS_OFFSET + _fn // 8)
            except EmulatorError:
                # Under button-press load a badge read8 intermittently returns a
                # corrupted/non-numeric reply (~5% of reads observed 07-18). It
                # must NOT drop a latched badge, and it must NOT abort this loop:
                # the count used to be coupled to the read (badges += 1 inside
                # the loop), so a mid-loop raise jumped to the outer except with
                # badges partial (0) EVEN THOUGH the latch held {0,1,2} -- which
                # dipped badge_count below 3 and re-fired reach_mauville, yanking
                # the agent back toward Mauville on Route114 (goal oscillation).
                continue
            if (_fb >> (_fn % 8)) & 1:
                _BADGE_BITS_LATCHED.add(_bi)  # earned -> never drops (DMA flicker)
        # Count from the latch, decoupled from the per-bit reads above: badge_count
        # is now len(latched bits), immune to any single frame's read failures.
        badges = len(_BADGE_BITS_LATCHED)
        flag_byte_birch = client.read8(ptr + SB1_FLAGS_OFFSET + (0x52 // 8))
        flag_birch = bool(flag_byte_birch & (1 << (0x52 % 8)))
        flag_byte_starter = client.read8(ptr + SB1_FLAGS_OFFSET + (0x55 // 8))
        flag_starter = bool(flag_byte_starter & (1 << (0x55 % 8)))
        flag_byte_devon = client.read8(ptr + SB1_FLAGS_OFFSET + (0x8F // 8))
        flag_devon = bool(flag_byte_devon & (1 << (0x8F % 8)))
        flag_byte_letter = client.read8(ptr + SB1_FLAGS_OFFSET + (0xBD // 8))
        flag_steven_letter = bool(flag_byte_letter & (1 << (0xBD % 8)))
        flag_byte_dgd = client.read8(ptr + SB1_FLAGS_OFFSET + (0x95 // 8))
        flag_devon_delivered = bool(flag_byte_dgd & (1 << (0x95 % 8)))
        flag_byte_dr = client.read8(ptr + SB1_FLAGS_OFFSET + (0x94 // 8))
        flag_dock_rejected = bool(flag_byte_dr & (1 << (0x94 % 8)))
        # Lavaridge arc gates
        # Rise-confirm the two future-event gates only on stable overworld
        # frames (cb2 == CB2_Overworld) -- cutscene/transition reads are garbage.
        _cb2_stable = cb2 in CB2_OVERWORLD_SET
        flag_byte_r112m = client.read8(ptr + SB1_FLAGS_OFFSET + (0x333 // 8))
        flag_r112_magma = _rise_confirmed(
            "r112_magma", bool(flag_byte_r112m & (1 << (0x333 % 8))), _cb2_stable)
        flag_byte_mtc = client.read8(ptr + SB1_FLAGS_OFFSET + (0x8B // 8))
        flag_mtc_defeated = _rise_confirmed(
            "mtc_defeated", bool(flag_byte_mtc & (1 << (0x8B % 8))), _cb2_stable)
        flag_byte_b4 = client.read8(ptr + SB1_FLAGS_OFFSET + (0x86A // 8))
        flag_badge4 = bool(flag_byte_b4 & (1 << (0x86A % 8)))
        flag_byte_rs = client.read8(ptr + SB1_FLAGS_OFFSET + (0x6B // 8))
        flag_rock_smash = bool(flag_byte_rs & (1 << (0x6B % 8)))
        first_item_id = client.read16(ptr + SB1_BAG_ITEMS + 0)
        first_item_qty_enc = client.read16(ptr + SB1_BAG_ITEMS + 2)
        # 35 fix (06-29): Pokemon Emerald bag quantities are XOR-encrypted
        # with SaveBlock2 security_key bottom 16 bits. Previously displayed
        # raw encrypted value (61548 / 22819 = noise).
        try:
            sb2_ptr_for_key = client.read32(0x03005D90)
            sec_key = client.read32(sb2_ptr_for_key + 0xAC)
            first_item_qty = first_item_qty_enc ^ (sec_key & 0xFFFF)
        except EmulatorError:
            first_item_qty = first_item_qty_enc
        if first_item_id > 600 or first_item_qty > 999:
            first_item_id = first_item_qty = 0
        for slot in range(30):
            slot_id = client.read16(ptr + SB1_BAG_ITEMS + slot * 4)
            if slot_id == 0:
                break
            if slot_id == 4:  # POKE_BALL item id (unusual — usually in balls pocket)
                pokeballs = client.read16(
                    ptr + SB1_BAG_ITEMS + slot * 4 + 2
                )
                break
        # Pokemon Emerald keeps Poke Balls in a SEPARATE balls pocket at
        # SaveBlock1 + 0x650 (16 slots × 4 bytes). The Items pocket
        # (0x560) holds Potion/Antidote/etc but not balls in normal play.
        # Quantities are XOR-encrypted with the SaveBlock2 security key.
        try:
            sb2_ptr = client.read32(0x03005D90)
            security_key = client.read32(sb2_ptr + 0xAC)
            for slot in range(16):
                slot_id = client.read16(ptr + 0x650 + slot * 4)
                if slot_id == 0:
                    break
                if slot_id == 4:  # POKE_BALL
                    qty_enc = client.read16(ptr + 0x650 + slot * 4 + 2)
                    pokeballs += qty_enc ^ (security_key & 0xFFFF)
                    break
        except EmulatorError:
            pass
    except EmulatorError:
        pass

    # Heal-item counts + money (H14: buy_potions / field_heal_potion gates).
    # Items pocket 0x560 x30 slots; count HP restores {POTION 13, FULL_RESTORE
    # 19, MAX_POTION 20, HYPER_POTION 21, SUPER_POTION 22}. Money = SB1+0x490 XOR
    # the FULL 32-bit SaveBlock2 key (item qty uses only the low 16). Independent
    # try so a failure here never voids the flag/party reads below.
    try:
        sb2p = client.read32(0x03005D90)
        key = client.read32(sb2p + 0xAC)
        for slot in range(30):
            sid = client.read16(ptr + SB1_BAG_ITEMS + slot * 4)
            if sid == 0:
                break
            if sid in (13, 19, 20, 21, 22):
                q = client.read16(ptr + SB1_BAG_ITEMS + slot * 4 + 2) ^ (
                    key & 0xFFFF)
                if 0 < q <= 99:  # stack cap 99; DMA-garbage guard
                    bag_heal_qty += q
                    if sid == 22:
                        bag_super_potion_qty += q
        m = client.read32(ptr + 0x490) ^ key
        money = m if 0 <= m <= 999999 else -1
    except EmulatorError:
        pass

    # 34 fix (06-29): Independent try block for flag bytes read.
    # Previously bundled with bag/party reads — when any earlier read
    # raised EmulatorError, flag_hex stayed empty silently.
    try:
        flag_bytes = client.read_range(
            ptr + SB1_FLAGS_OFFSET, NUM_FLAG_BYTES,
        )
        total_flags = sum(bin(b).count("1") for b in flag_bytes)
        flag_hex = flag_bytes.hex()
    except EmulatorError:
        pass

    # Independent try for party move IDs (field-move gate: knows_rock_smash).
    # Reads gPlayerParty (fixed addr, not SaveBlock1). ~3 read32/slot.
    try:
        _pm: list[list[int]] = []
        for slot in range(min(max(party_count, 0), 6)):
            _pm.append(_read_party_move_ids(client, slot))
        party_moves = _pm
    except EmulatorError:
        pass

    return GameState(
        map_group=mg,
        map_num=mn,
        x=x,
        y=y,
        saveblock1_valid=True,
        in_battle=in_battle,
        battle_flags=flags,
        game_cb2=cb2,
        game_mode=game_mode_for_cb2(cb2),
        party0_level=lv,
        party0_hp=hp,
        party0_max_hp=max_hp,
        party0_damaging_pp=damaging_pp,
        party0_species=party0_species_id,
        party_count=party_count,
        flag_birch_met=flag_birch,
        flag_starter_received=flag_starter,
        flag_devon_goods_recovered=flag_devon,
        flag_steven_letter_delivered=flag_steven_letter,
        flag_devon_goods_delivered=flag_devon_delivered,
        flag_dock_rejected_devon=flag_dock_rejected,
        flag_route112_magma_cleared=flag_r112_magma,
        flag_mtchimney_magma_defeated=flag_mtc_defeated,
        flag_badge04_get=flag_badge4,
        flag_rock_smash_hm=flag_rock_smash,
        party_moves=party_moves,
        # Read-root fall-confirm guard (see _flicker_guarded_pokeballs): every
        # balls-gated goal (catch >0, buy_pokeballs <5) sees the guarded value.
        bag_pokeball_count=_flicker_guarded_pokeballs(pokeballs),
        bag_pokeball_count_raw=pokeballs,  # unguarded; catch SM throw-edge signal
        bag_first_item_id=first_item_id,
        bag_first_item_qty=first_item_qty,
        bag_heal_qty=bag_heal_qty,
        bag_super_potion_qty=bag_super_potion_qty,
        money=money,
        badge_count=badges,
        total_event_flags=total_flags,
        event_flag_bytes_hex=flag_hex,
        npcs_on_map=read_npcs_on_map(client, mg, mn),
    )
