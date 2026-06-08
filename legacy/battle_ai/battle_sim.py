"""Gen3 ダメージ計算シミュレータ
バトルAIの学習環境で使用。実機接続不要でバトルをシミュレート。
"""

import random
from dataclasses import dataclass
from type_chart import get_effectiveness
from move_db import get_move, Move, PHYSICAL_TYPES


@dataclass
class Pokemon:
    """バトル中のポケモン状態"""
    species_id: int
    name: str
    level: int
    hp: int
    max_hp: int
    attack: int
    defense: int
    sp_attack: int
    sp_defense: int
    speed: int
    type1: int
    type2: int       # -1 = なし
    moves: list[int]  # move IDs (max 4)
    pp: list[int]     # current PP


def calc_damage(attacker: Pokemon, defender: Pokemon, move: Move) -> int:
    """Gen3ダメージ計算式
    damage = ((2*level/5+2) * power * A/D) / 50 + 2) * modifier
    modifier = STAB * type_eff * random(0.85-1.0)
    """
    if move.power == 0:
        return 0  # ステータス技

    level = attacker.level

    # 物理/特殊判定（Gen3はタイプで決まる）
    if move.type_id in PHYSICAL_TYPES:
        atk_stat = attacker.attack
        def_stat = defender.defense
    else:
        atk_stat = attacker.sp_attack
        def_stat = defender.sp_defense

    # 基本ダメージ
    base = ((2 * level / 5 + 2) * move.power * atk_stat / def_stat) / 50 + 2

    # STAB (Same Type Attack Bonus)
    stab = 1.5 if (move.type_id == attacker.type1 or move.type_id == attacker.type2) else 1.0

    # タイプ相性
    type_eff = get_effectiveness(move.type_id, defender.type1, defender.type2)

    # ランダム (85-100%)
    rand_factor = random.randint(85, 100) / 100.0

    damage = int(base * stab * type_eff * rand_factor)
    return max(1, damage) if type_eff > 0 else 0


def simulate_turn(attacker: Pokemon, defender: Pokemon,
                  atk_move_idx: int) -> dict:
    """1ターンのバトルをシミュレート（攻撃側のみ）
    Returns: {"damage": int, "effective": float, "fainted": bool}
    """
    if atk_move_idx < 0 or atk_move_idx >= len(attacker.moves):
        return {"damage": 0, "effective": 1.0, "fainted": False}

    move_id = attacker.moves[atk_move_idx]
    move = get_move(move_id)
    if not move:
        return {"damage": 0, "effective": 1.0, "fainted": False}

    # PP check
    if attacker.pp[atk_move_idx] <= 0:
        return {"damage": 0, "effective": 1.0, "fainted": False}

    # PP consume
    attacker.pp[atk_move_idx] -= 1

    # Accuracy check
    if move.accuracy > 0 and random.randint(1, 100) > move.accuracy:
        return {"damage": 0, "effective": 1.0, "fainted": False, "missed": True}

    # Damage calc
    dmg = calc_damage(attacker, defender, move)
    type_eff = get_effectiveness(move.type_id, defender.type1, defender.type2)

    defender.hp = max(0, defender.hp - dmg)
    fainted = defender.hp <= 0

    return {
        "damage": dmg,
        "effective": type_eff,
        "fainted": fainted,
    }


if __name__ == "__main__":
    # テスト: Blaziken vs Geodude
    blaziken = Pokemon(
        species_id=282, name="Blaziken", level=44,
        hp=149, max_hp=149, attack=135, defense=80,
        sp_attack=120, sp_defense=80, speed=100,
        type1=10, type2=1,  # Fire/Fighting
        moves=[299, 280, 53, 14],  # Blaze Kick, Brick Break, Flamethrower, Swords Dance
        pp=[10, 15, 15, 30],
    )
    geodude = Pokemon(
        species_id=74, name="Geodude", level=10,
        hp=30, max_hp=30, attack=25, defense=40,
        sp_attack=15, sp_defense=15, speed=10,
        type1=5, type2=4,  # Rock/Ground
        moves=[33, 88],  # Tackle, Rock Throw
        pp=[35, 15],
    )

    result = simulate_turn(blaziken, geodude, 1)  # Brick Break (Fighting vs Rock/Ground)
    print(f"Brick Break vs Geodude: dmg={result['damage']} eff={result['effective']}x "
          f"fainted={result['fainted']} geodude_hp={geodude.hp}")

    geodude.hp = geodude.max_hp  # reset
    result2 = simulate_turn(blaziken, geodude, 2)  # Flamethrower (Fire vs Rock/Ground)
    print(f"Flamethrower vs Geodude: dmg={result2['damage']} eff={result2['effective']}x "
          f"fainted={result2['fainted']} geodude_hp={geodude.hp}")
