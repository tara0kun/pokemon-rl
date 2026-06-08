"""RAM bridge — read player position / map / party from emulator memory.

Targets Pokemon Emerald (USA, Europe) ROM. Addresses from pokeemerald decomp.

References:
- gSaveBlock1Ptr at IWRAM 0x03005D8C (pointer, 32-bit LE)
- SaveBlock1 layout:
    0x00 (2)  pos.x
    0x02 (2)  pos.y
    0x04 (1)  location.mapGroup
    0x05 (1)  location.mapNum

The pointer can be 0 before the save block is initialized (title screen),
in which case we return zeros.
"""
from __future__ import annotations

from dataclasses import dataclass

from .io import EmulatorError, MGBAClient

SAVEBLOCK1_PTR_ADDR = 0x03005D8C


@dataclass
class GameState:
    map_group: int
    map_num: int
    x: int
    y: int
    saveblock1_valid: bool

    def short(self) -> str:
        if not self.saveblock1_valid:
            return "map=(?,?) pos=(?,?) [pre-save]"
        return (
            f"map=({self.map_group},{self.map_num}) "
            f"pos=({self.x},{self.y})"
        )


def _signed16(v: int) -> int:
    return v - 0x10000 if v >= 0x8000 else v


def read_state(client: MGBAClient) -> GameState:
    try:
        ptr = client.read32(SAVEBLOCK1_PTR_ADDR)
    except EmulatorError:
        return GameState(0, 0, 0, 0, saveblock1_valid=False)

    if ptr < 0x02000000 or ptr >= 0x02040000:
        return GameState(0, 0, 0, 0, saveblock1_valid=False)

    try:
        x = _signed16(client.read16(ptr + 0x00))
        y = _signed16(client.read16(ptr + 0x02))
        mg = client.read8(ptr + 0x04)
        mn = client.read8(ptr + 0x05)
    except EmulatorError:
        return GameState(0, 0, 0, 0, saveblock1_valid=False)

    return GameState(
        map_group=mg,
        map_num=mn,
        x=x,
        y=y,
        saveblock1_valid=True,
    )
