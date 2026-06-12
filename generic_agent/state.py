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

from dataclasses import dataclass

from .io import EmulatorError, MGBAClient

SAVEBLOCK1_PTR_ADDR = 0x03005D8C
PLAYER_PARTY_ADDR = 0x020244EC  # gPlayerParty[0] in Emerald (USA)
POKEMON_STRUCT_SIZE = 100
POKEMON_LEVEL_OFFSET = 0x54
POKEMON_HP_OFFSET = 0x56
POKEMON_MAX_HP_OFFSET = 0x58

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
    )
