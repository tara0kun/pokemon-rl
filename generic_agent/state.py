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

# SaveBlock1 inner offsets (pokeemerald struct SaveBlock1)
# These are referenced relative to *gSaveBlock1Ptr.
SB1_PLAYER_PARTY_COUNT = 0x0234  # u32 at SaveBlock1.playerPartyCount
SB1_FLAGS_OFFSET = 0x1270        # u8 flags[NUM_FLAG_BYTES]
SB1_VARS_OFFSET = 0x1408         # u16 vars[NUM_VARS]
SB1_BAG_ITEMS = 0x0560           # struct ItemSlot bagPocket_Items[30]
NUM_FLAG_BYTES = 0x12C           # 300 bytes = 2400 event flags (Emerald)

BATTLE_FLAGS_CANDIDATES = [
    0x020243CC,
    0x020238F0,
]


BATTLE_TYPE_TRAINER = 0x0008


@dataclass
class GameState:
    map_group: int
    map_num: int
    x: int
    y: int
    saveblock1_valid: bool
    in_battle: bool = False
    battle_flags: int = 0
    party0_level: int = 0
    party0_hp: int = 0
    party0_max_hp: int = 0
    party_count: int = 0
    flag_birch_met: bool = False
    flag_starter_received: bool = False
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
        return True, v
    return False, 0


def read_state(client: MGBAClient) -> GameState:
    in_battle, flags = _read_battle_flags(client)

    try:
        ptr = client.read32(SAVEBLOCK1_PTR_ADDR)
    except EmulatorError:
        return GameState(
            0, 0, 0, 0,
            saveblock1_valid=False,
            in_battle=in_battle,
            battle_flags=flags,
        )

    if ptr < 0x02000000 or ptr >= 0x02040000:
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

    party_count = 0
    flag_birch = False
    flag_starter = False
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
        flag_byte_birch = client.read8(ptr + SB1_FLAGS_OFFSET + (0x52 // 8))
        flag_birch = bool(flag_byte_birch & (1 << (0x52 % 8)))
        flag_byte_starter = client.read8(ptr + SB1_FLAGS_OFFSET + (0x55 // 8))
        flag_starter = bool(flag_byte_starter & (1 << (0x55 % 8)))
        first_item_id = client.read16(ptr + SB1_BAG_ITEMS + 0)
        first_item_qty = client.read16(ptr + SB1_BAG_ITEMS + 2)
        if first_item_id > 600:
            first_item_id = first_item_qty = 0
        for slot in range(30):
            slot_id = client.read16(ptr + SB1_BAG_ITEMS + slot * 4)
            if slot_id == 0:
                break
            if slot_id == 4:  # POKE_BALL item id
                pokeballs = client.read16(
                    ptr + SB1_BAG_ITEMS + slot * 4 + 2
                )
                break
        flag_bytes = client.read_range(
            ptr + SB1_FLAGS_OFFSET, NUM_FLAG_BYTES,
        )
        total_flags = sum(bin(b).count("1") for b in flag_bytes)
        flag_hex = flag_bytes.hex()
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
        party0_level=lv,
        party0_hp=hp,
        party0_max_hp=max_hp,
        party_count=party_count,
        flag_birch_met=flag_birch,
        flag_starter_received=flag_starter,
        bag_pokeball_count=pokeballs,
        bag_first_item_id=first_item_id,
        bag_first_item_qty=first_item_qty,
        badge_count=badges,
        total_event_flags=total_flags,
        event_flag_bytes_hex=flag_hex,
        npcs_on_map=read_npcs_on_map(client, mg, mn),
    )
