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
SB1_PLAYER_PARTY_COUNT = 0x0234  # u32 at SaveBlock1.playerPartyCount
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


BATTLE_TYPE_TRAINER = 0x0008
MOVE_ROCK_SMASH = 249  # MOVE_ROCK_SMASH (moves.h)

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
    party0_level: int = 0
    party0_hp: int = 0
    party0_max_hp: int = 0
    party0_species: int = 0  # decrypted species_id (Wingull=278, Grovyle=253, etc)
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
    bag_first_item_id: int = 0
    bag_first_item_qty: int = 0
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
        # gBattleTypeFlags is set but may be stale (whiteout leaves it non-
        # zero in the overworld). Confirm we are ACTUALLY in a battle via the
        # game-mode callback: in the field it is CB2_Overworld; only in a
        # real battle is it a battle callback. This flips instantly on exit,
        # unlike the flag, so it kills the post-whiteout false positive.
        try:
            cb2 = client.read32(GMAIN_CB2_ADDR)
        except EmulatorError:
            cb2 = None
        if cb2 is not None and (cb2 in CB2_OVERWORLD_SET or cb2 in CB2_MENU_SET):
            return False, 0
        return True, v
    return False, 0


def read_state(client: MGBAClient) -> GameState:
    in_battle, flags = _read_battle_flags(client)
    try:
        cb2 = client.read32(GMAIN_CB2_ADDR)
    except EmulatorError:
        cb2 = 0

    ptr = _read_saveblock1_ptr(client)
    if ptr is None:
        return GameState(
            0, 0, 0, 0,
            saveblock1_valid=False,
            in_battle=in_battle,
            battle_flags=flags,
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
        )

    try:
        lv = client.read8(PLAYER_PARTY_ADDR + POKEMON_LEVEL_OFFSET)
        hp = client.read16(PLAYER_PARTY_ADDR + POKEMON_HP_OFFSET)
        max_hp = client.read16(PLAYER_PARTY_ADDR + POKEMON_MAX_HP_OFFSET)
        if lv > 100 or max_hp > 1000:
            lv = hp = max_hp = 0
    except EmulatorError:
        lv = hp = max_hp = 0

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
    badges = 0
    total_flags = 0
    flag_hex = ""
    try:
        party_count = client.read8(ptr + SB1_PLAYER_PARTY_COUNT)
        if party_count > 6:
            party_count = 0
        # Badges = event flags FLAG_BADGE01_GET (0x867) .. BADGE08 (0x86E).
        # Previously `badges` stayed hardcoded 0, so badge_count always read
        # 0 even after earning a badge (Stone Badge won 07-01 but reported
        # as 0). Count the set badge flags.
        badges = 0
        for _bi in range(8):
            _fn = 0x867 + _bi
            _fb = client.read8(ptr + SB1_FLAGS_OFFSET + _fn // 8)
            if (_fb >> (_fn % 8)) & 1:
                badges += 1
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
        flag_byte_r112m = client.read8(ptr + SB1_FLAGS_OFFSET + (0x333 // 8))
        flag_r112_magma = bool(flag_byte_r112m & (1 << (0x333 % 8)))
        flag_byte_mtc = client.read8(ptr + SB1_FLAGS_OFFSET + (0x8B // 8))
        flag_mtc_defeated = bool(flag_byte_mtc & (1 << (0x8B % 8)))
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
        party0_level=lv,
        party0_hp=hp,
        party0_max_hp=max_hp,
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
        bag_pokeball_count=pokeballs,
        bag_first_item_id=first_item_id,
        bag_first_item_qty=first_item_qty,
        badge_count=badges,
        total_event_flags=total_flags,
        event_flag_bytes_hex=flag_hex,
        npcs_on_map=read_npcs_on_map(client, mg, mn),
    )
