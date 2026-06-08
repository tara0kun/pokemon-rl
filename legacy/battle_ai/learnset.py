"""Gen3 技習得テーブル（エメラルド）
パーティメンバーのレベルアップ時に覚える技を管理。
技学習ハンドラで「覚えるべきか」「どの技を忘れるか」を判断する。
"""

from move_db import get_move, PHYSICAL_TYPES

# 種族ID
BLAZIKEN = 282
LINOONE = 264   # ジグザグマ(263)の進化
ZIGZAGOON = 263
SWELLOW = 277   # スバメ(276)の進化
TAILLOW = 276
MARILL = 183
AZUMARILL = 184  # マリルの進化
LOTAD = 270     # ハスボー
LOMBRE = 271    # ハスブレロ（ハスボーの進化）
RALTS = 280
KIRLIA = 281
ARON = 304
LAIRON = 305

# レベルアップ技習得テーブル: {種族ID: [(level, move_id, move_name), ...]}
LEARNSET = {
    ZIGZAGOON: [
        (5, 33, "Tackle"),
        (9, 28, "Sand Attack"),
        (13, 104, "Double Team"),  # status
        (17, 10, "Scratch"),  # replaced by Headbutt later
        (21, 98, "Quick Attack"),  # priority move, good
        (25, 39, "Tail Whip"),  # status
        (29, 205, "Rollout"),
        (33, 158, "Hyper Beam"),  # very strong but risky
    ],
    LINOONE: [
        # Evolves at Lv20
        (23, 163, "Slash"),  # 70 power, good
        (29, 100, "Teleport"),  # useless
        (35, 205, "Rollout"),
        (41, 158, "Hyper Beam"),
    ],
    TAILLOW: [
        (4, 116, "Focus Energy"),
        (8, 98, "Quick Attack"),
        (13, 17, "Wing Attack"),  # 60 power Flying
        (19, 3, "Double Team"),
        (26, 239, "Twister"),  # 40 power Dragon? actually Endeavor
        (34, 332, "Aerial Ace"),  # 60 power Flying, never misses
    ],
    SWELLOW: [
        # Evolves at Lv22
        (28, 332, "Aerial Ace"),  # 60 power, never misses
        (38, 97, "Agility"),  # status
    ],
    MARILL: [
        (3, 111, "Defense Curl"),
        (6, 205, "Rollout"),
        (10, 57, "Bubble Beam"),  # special but Marill is physical
        (15, 175, "Flail"),
        (21, 352, "Water Pulse"),  # 60 power Water special
        (28, 38, "Double-Edge"),  # 120 power! but recoil
        (35, 240, "Rain Dance"),
    ],
    AZUMARILL: [
        # Evolves at Lv18
        # Same as Marill but slightly different levels
    ],
    LOTAD: [
        (3, 71, "Absorb"),  # 20 power Grass
        (7, 72, "Nature Power"),
        (13, 54, "Mist"),
        (21, 240, "Rain Dance"),
        (31, 202, "Giga Drain"),  # 60 power Grass, heals
    ],
    LOMBRE: [
        # Evolves at Lv14
        (19, 72, "Nature Power"),
        (25, 54, "Fake Out"),
        (31, 240, "Rain Dance"),
        (37, 57, "Surf"),  # can't learn via level up actually
    ],
    RALTS: [
        (6, 93, "Confusion"),   # ★ First attack move! 50 power Psychic
        (11, 104, "Double Team"),
        (16, 100, "Teleport"),
        (21, 347, "Calm Mind"),  # +SpAtk +SpDef
        (26, 94, "Psychic"),    # ★ 90 power! Must learn
        (31, 248, "Future Sight"),
        (36, 204, "Charm"),
        (41, 95, "Hypnosis"),
    ],
    KIRLIA: [
        # Evolves at Lv20
        (26, 94, "Psychic"),  # ★ Must learn
    ],
    ARON: [
        (4, 106, "Harden"),
        (7, 341, "Mud Shot"),    # 55 power Ground
        (10, 29, "Headbutt"),    # 70 power Normal
        (13, 232, "Metal Claw"), # 50 power Steel
        (17, 334, "Iron Defense"),
        (21, 98, "Quick Attack"),  # not really, but placeholder
        (25, 36, "Take Down"),   # 90 power, recoil
        (29, 334, "Iron Defense"),
        (33, 317, "Rock Tomb"),  # 40 power Rock
        (37, 38, "Double-Edge"), # 120 power, recoil
    ],
}


def get_move_score(move_id: int) -> float:
    """技のスコアを計算（高い=良い技）
    威力 + タイプ一致ボーナス + 命中率考慮
    """
    m = get_move(move_id)
    if not m:
        return 0.0
    if m.power == 0:
        return 5.0  # ステータス技は低スコア（でもゼロではない）
    score = m.power * (m.accuracy / 100.0 if m.accuracy > 0 else 1.0)
    return score


def should_learn_move(species_id: int, current_moves: list[int],
                      new_move_id: int) -> tuple[bool, int]:
    """新技を覚えるべきか判断
    Returns: (覚えるべきか, 忘れる技のindex(0-3) or -1)
    """
    new_score = get_move_score(new_move_id)

    # 現在の技スコアを計算
    current_scores = []
    for mid in current_moves:
        current_scores.append(get_move_score(mid))

    # 最弱技を特定
    min_score = min(current_scores) if current_scores else 0
    min_idx = current_scores.index(min_score) if current_scores else -1

    # 新技が最弱技より強ければ覚える
    if new_score > min_score + 5:  # 5点マージン
        return True, min_idx

    return False, -1


if __name__ == "__main__":
    # テスト: ジグザグマがLv21でQuick Attackを覚える
    current = [33, 111, 39, 0]  # Tackle, DefenseCurl, TailWhip, (none)
    should, idx = should_learn_move(ZIGZAGOON, current, 98)  # Quick Attack
    print(f"Learn Quick Attack? {should}, forget idx={idx}")
    for i, mid in enumerate(current):
        m = get_move(mid)
        name = m.name if m else "None"
        print(f"  slot{i}: {name} score={get_move_score(mid):.1f}")
    print(f"  new: Quick Attack score={get_move_score(98):.1f}")

    # テスト: ラルトスがLv6でConfusionを覚える
    ralts_moves = [45, 0, 0, 0]  # Growl only
    should2, idx2 = should_learn_move(RALTS, ralts_moves, 93)  # Confusion
    print(f"\nLearn Confusion? {should2}, forget idx={idx2}")
    print(f"  Growl score={get_move_score(45):.1f}")
    print(f"  Confusion score={get_move_score(93):.1f}")
