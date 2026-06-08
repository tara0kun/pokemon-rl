"""Gen3 バトル環境（Gymnasium準拠）
バトルAIのRL学習用。シミュレータベースでオフライン学習可能。

使い方:
    env = BattleEnv()
    obs, info = env.reset()
    action = agent.predict(obs)  # 0-8: 技1-4, 交代slot1-5
    obs, reward, done, truncated, info = env.step(action)
"""

import gymnasium as gym
import numpy as np
import random
from gymnasium import spaces
from battle_sim import Pokemon, simulate_turn, calc_damage
from type_chart import get_effectiveness
from move_db import get_move, MOVES

# タイプ別の代表技（学習に多様性を持たせる）
TYPE_MOVES = {
    0: [33, 10, 98, 34, 163, 216],      # Normal: Tackle, Scratch, Quick Attack, Body Slam, Slash, Return
    1: [2, 183, 280],                     # Fighting: Karate Chop, Mach Punch, Brick Break
    2: [17, 64, 332],                     # Flying: Wing Attack, Peck, Aerial Ace
    3: [40, 188],                         # Poison: Poison Sting, Sludge Bomb
    4: [91, 341, 89],                     # Ground: Dig, Mud Shot, Earthquake
    5: [88, 317, 157],                    # Rock: Rock Throw, Rock Tomb, Rock Slide
    6: [318, 42],                         # Bug: Silver Wind, Pin Missile
    7: [247],                             # Ghost: Shadow Ball
    8: [232],                             # Steel: Metal Claw
    10: [52, 53, 299],                    # Fire: Ember, Flamethrower, Blaze Kick
    11: [55, 57, 352],                    # Water: Water Gun, Surf, Water Pulse
    12: [22, 75, 202],                    # Grass: Vine Whip, Razor Leaf, Giga Drain
    13: [84, 85, 351],                    # Electric: Thunder Shock, Thunderbolt, Shock Wave
    14: [93, 60, 94],                     # Psychic: Confusion, Psybeam, Psychic
    15: [196, 58],                        # Ice: Icy Wind, Ice Beam
    16: [225, 337],                       # Dragon: Dragonbreath, Dragon Claw
    17: [168, 185, 242],                  # Dark: Thief, Feint Attack, Crunch
}

ALL_TYPES = list(TYPE_MOVES.keys())


class BattleEnv(gym.Env):
    """Gen3シングルバトル環境（敵チーム対応版）"""

    metadata = {"render_modes": ["human"]}

    def __init__(self, difficulty: str = "normal"):
        super().__init__()
        # 行動空間: 技1-4(0-3) + 交代slot1-5(4-8) = 9
        self.action_space = spaces.Discrete(9)
        # 観測空間: 30次元
        # 自分: HP%, Lv, type1, type2, 4技(power, type) = 4+4*2 = 12
        # 敵: HP%, Lv, type1, type2 = 4
        # 味方チーム: 5 slots × HP% = 5
        # 敵残数, 自分残数, ターン数 = 3
        # 各技の相性倍率(vs敵) = 4
        # 自分HP実数/100, 敵HP実数/100 = 2
        # Total = 12+4+5+3+4+2 = 30
        self.observation_space = spaces.Box(
            low=0.0, high=2.0, shape=(30,), dtype=np.float32
        )
        self.difficulty = difficulty
        self.my_team: list[Pokemon] = []
        self.enemy_team: list[Pokemon] = []
        self.active_idx: int = 0
        self.enemy_idx: int = 0
        self.turn: int = 0
        self.total_damage_dealt: float = 0
        self.total_damage_taken: float = 0

    @property
    def enemy(self) -> Pokemon | None:
        if self.enemy_idx < len(self.enemy_team):
            return self.enemy_team[self.enemy_idx]
        return None

    def _make_obs(self) -> np.ndarray:
        my = self.my_team[self.active_idx] if self.active_idx < len(self.my_team) else None
        enemy = self.enemy
        if my is None or enemy is None:
            return np.zeros(30, dtype=np.float32)

        obs = []

        # 自分の情報 (10)
        obs.append(my.hp / max(my.max_hp, 1))
        obs.append(my.level / 100.0)
        obs.append(my.type1 / 20.0)
        obs.append(my.type2 / 20.0 if my.type2 >= 0 else 0.0)
        for i in range(4):
            if i < len(my.moves):
                move = get_move(my.moves[i])
                obs.append(move.power / 150.0 if move else 0.0)
                obs.append(move.type_id / 20.0 if move else 0.0)
            else:
                obs.extend([0.0, 0.0])

        # 敵の情報 (4)
        obs.append(enemy.hp / max(enemy.max_hp, 1))
        obs.append(enemy.level / 100.0)
        obs.append(enemy.type1 / 20.0)
        obs.append(enemy.type2 / 20.0 if enemy.type2 >= 0 else 0.0)

        # 味方チームHP (5 slots)
        for i in range(5):
            if i < len(self.my_team):
                obs.append(self.my_team[i].hp / max(self.my_team[i].max_hp, 1))
            else:
                obs.append(0.0)

        # 残数情報 + ターン (3)
        my_alive = sum(1 for p in self.my_team if p.hp > 0)
        enemy_alive = sum(1 for p in self.enemy_team if p.hp > 0)
        obs.append(my_alive / 6.0)
        obs.append(enemy_alive / 6.0)
        obs.append(min(self.turn / 50.0, 1.0))

        # 各技の相性倍率 (4)
        for i in range(4):
            if i < len(my.moves):
                move = get_move(my.moves[i])
                if move and move.power > 0:
                    eff = get_effectiveness(move.type_id, enemy.type1, enemy.type2)
                    obs.append(min(eff / 2.0, 1.0))  # normalize: 4x -> 2.0, 2x -> 1.0
                else:
                    obs.append(0.0)
            else:
                obs.append(0.0)

        # HP実数 (2)
        obs.append(min(my.hp / 100.0, 2.0))
        obs.append(min(enemy.hp / 100.0, 2.0))

        return np.array(obs, dtype=np.float32)

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)

        if self.difficulty == "easy":
            my_lv_range = (15, 25)
            enemy_lv_range = (8, 15)
            my_count, enemy_count = 3, 1
        elif self.difficulty == "hard":
            my_lv_range = (15, 25)
            enemy_lv_range = (15, 28)
            my_count, enemy_count = 3, 3
        else:  # normal
            my_lv_range = (12, 22)
            enemy_lv_range = (12, 22)
            my_count, enemy_count = 3, 2

        self.my_team = [self._random_pokemon(lv=random.randint(*my_lv_range)) for _ in range(my_count)]
        self.enemy_team = [self._random_pokemon(lv=random.randint(*enemy_lv_range)) for _ in range(enemy_count)]
        self.active_idx = 0
        self.enemy_idx = 0
        self.turn = 0
        self.total_damage_dealt = 0
        self.total_damage_taken = 0
        return self._make_obs(), {}

    def _best_move_effectiveness(self, pokemon, enemy) -> float:
        """そのポケモンが敵に出せる最高のタイプ相性倍率を返す"""
        best = 0.0
        for i, mid in enumerate(pokemon.moves):
            if pokemon.pp[i] <= 0:
                continue
            move = get_move(mid)
            if move and move.power > 0:
                eff = get_effectiveness(move.type_id, enemy.type1, enemy.type2)
                if eff > best:
                    best = eff
            elif move is None or move.power == 0:
                continue
        return best if best > 0 else 1.0

    def step(self, action: int):
        reward = 0.0
        done = False
        my = self.my_team[self.active_idx]
        enemy = self.enemy

        if enemy is None or enemy.hp <= 0:
            return self._make_obs(), 0.0, True, False, {}

        if action < 4:
            # 技使用
            if action < len(my.moves) and my.pp[action] > 0:
                move = get_move(my.moves[action])
                enemy_hp_before = enemy.hp
                result = simulate_turn(my, enemy, action)
                dmg = result["damage"]
                eff = result.get("effective", 1.0)
                self.total_damage_dealt += dmg

                # ダメージ報酬（敵HPに対する割合）
                reward += dmg / max(enemy.max_hp, 1) * 2.0

                # タイプ相性ボーナス（段階的）
                if eff >= 4.0:
                    reward += 1.0   # 4倍弱点ヒット
                elif eff >= 2.0:
                    reward += 0.5   # 効果抜群
                elif eff <= 0.5 and eff > 0:
                    reward -= 0.2   # 今ひとつ選択ペナルティ
                elif eff == 0:
                    reward -= 0.5   # 無効技選択ペナルティ

                # STAB（タイプ一致）ボーナス: タイプ一致技を選ぶことを奨励
                if move and move.power > 0:
                    is_stab = (move.type_id == my.type1 or move.type_id == my.type2)
                    if is_stab and eff >= 1.0:
                        reward += 0.2  # STAB + 等倍以上

                # 最適技選択ボーナス: 手持ちで最善の技を選んだか
                if move and move.power > 0:
                    best_eff = self._best_move_effectiveness(my, enemy)
                    if best_eff > 1.0 and eff >= best_eff:
                        reward += 0.3  # 弱点を突ける時に最善技を選んだ

                # ミス（命中失敗）は罰しない（運なので）

                if result["fainted"]:
                    reward += 5.0  # 敵撃破ボーナス
                    # 速攻撃破ボーナス（少ないターンで倒すと追加報酬）
                    reward += max(0, 1.0 - self.turn * 0.02)
                    # 次の敵を出す
                    self.enemy_idx += 1
                    if self.enemy_idx >= len(self.enemy_team):
                        # 全敵撃破ボーナス（残HP率でスケール）
                        my_hp_ratio = sum(p.hp for p in self.my_team) / max(sum(p.max_hp for p in self.my_team), 1)
                        reward += 10.0 + my_hp_ratio * 5.0  # HP多く残して勝つほど高報酬
                        done = True
            else:
                reward -= 0.3  # 無効技ペナルティ（PP切れ/存在しない技）
        elif action < 9:
            # 交代
            target = action - 4
            if target < len(self.my_team) and target != self.active_idx and self.my_team[target].hp > 0:
                old_idx = self.active_idx
                self.active_idx = target
                new_mon = self.my_team[target]

                # 交代の戦略的評価: タイプ有利な味方に交代するならボーナス
                new_best_eff = self._best_move_effectiveness(new_mon, enemy)
                old_best_eff = self._best_move_effectiveness(self.my_team[old_idx], enemy)

                if new_best_eff > old_best_eff and new_best_eff >= 2.0:
                    reward += 0.3   # タイプ有利な味方へ戦略的交代
                elif new_best_eff <= old_best_eff:
                    reward -= 0.15  # 不利or同等への交代は小ペナルティ
                else:
                    reward -= 0.05  # 微妙な交代は最小コスト
            else:
                reward -= 0.3  # 無効交代ペナルティ

        # 敵の攻撃（ランダム技選択）
        enemy = self.enemy  # 更新後の敵
        if not done and enemy and enemy.hp > 0:
            my = self.my_team[self.active_idx]
            valid_moves = [i for i in range(len(enemy.moves)) if enemy.pp[i] > 0]
            if valid_moves:
                enemy_action = random.choice(valid_moves)
                result = simulate_turn(enemy, my, enemy_action)
                self.total_damage_taken += result["damage"]
                reward -= result["damage"] / max(my.max_hp, 1) * 1.0

                if result["fainted"]:
                    # 瀕死→次のポケモンに交代
                    alive = [i for i, p in enumerate(self.my_team) if p.hp > 0 and i != self.active_idx]
                    if alive:
                        self.active_idx = alive[0]
                        reward -= 3.0  # 瀕死ペナルティ
                    else:
                        reward -= 10.0  # 全滅
                        done = True

        self.turn += 1
        if self.turn >= 80:
            # ターン制限: 残HP差で報酬を調整
            enemy_remaining_hp = sum(p.hp for p in self.enemy_team if p.hp > 0)
            my_remaining_hp = sum(p.hp for p in self.my_team if p.hp > 0)
            enemy_total_hp = sum(p.max_hp for p in self.enemy_team)
            my_total_hp = sum(p.max_hp for p in self.my_team)
            # 敵のHP割合が低いほどペナルティ緩和
            enemy_dmg_ratio = 1.0 - enemy_remaining_hp / max(enemy_total_hp, 1)
            reward += enemy_dmg_ratio * 2.0 - 3.0  # -3.0 ~ -1.0
            done = True

        info = {}
        if done:
            info["total_damage_dealt"] = self.total_damage_dealt
            info["total_damage_taken"] = self.total_damage_taken
            info["turns"] = self.turn
            info["enemies_defeated"] = self.enemy_idx
            info["my_alive"] = sum(1 for p in self.my_team if p.hp > 0)

        return self._make_obs(), reward, done, False, info

    def _random_pokemon(self, lv: int = 10) -> Pokemon:
        """多様なポケモンを生成（タイプに合った技を持つ）"""
        t1 = random.choice(ALL_TYPES)
        # 20%の確率で2タイプ
        t2 = random.choice([t for t in ALL_TYPES if t != t1]) if random.random() < 0.2 else -1

        # タイプ一致技 + ノーマル技 からランダムに4つ選択
        available_moves = list(TYPE_MOVES.get(t1, [33]))
        if t2 >= 0:
            available_moves.extend(TYPE_MOVES.get(t2, []))
        # ノーマル技も候補に追加
        available_moves.extend([33, 98, 163])
        # 重複除去してシャッフル
        available_moves = list(set(available_moves))
        random.shuffle(available_moves)
        moves = available_moves[:4]

        # PPを技データから取得
        pp = []
        for mid in moves:
            m = get_move(mid)
            pp.append(m.pp if m else 35)

        # ステータスにばらつきを持たせる
        hp_base = lv * 3 + random.randint(15, 30)
        atk = lv * 2 + random.randint(5, 20)
        dfn = lv + random.randint(5, 20)
        spa = lv * 2 + random.randint(5, 20)
        spd = lv + random.randint(5, 20)
        spd_stat = lv + random.randint(0, 15)

        return Pokemon(
            species_id=random.randint(1, 386),
            name=f"Mon{lv}",
            level=lv,
            hp=hp_base,
            max_hp=hp_base,
            attack=atk,
            defense=dfn,
            sp_attack=spa,
            sp_defense=spd,
            speed=spd_stat,
            type1=t1,
            type2=t2,
            moves=moves,
            pp=pp,
        )


if __name__ == "__main__":
    env = BattleEnv()
    obs, info = env.reset()
    print(f"Obs shape: {obs.shape}")
    print(f"Initial obs: {obs}")
    total_reward = 0
    for step in range(40):
        action = env.action_space.sample()
        obs, reward, done, _, info = env.step(action)
        total_reward += reward
        if done:
            print(f"Battle ended at step {step+1}, total reward: {total_reward:.1f}")
            if info:
                print(f"  Enemies defeated: {info.get('enemies_defeated', '?')}")
                print(f"  My alive: {info.get('my_alive', '?')}")
                print(f"  Turns: {info.get('turns', '?')}")
            break
    if not done:
        print(f"Battle ongoing at step 40, total reward: {total_reward:.1f}")
