"""Gen3 バトルAI学習スクリプト
battle_env.pyのBattleEnvでPPOエージェントを学習。
シミュレータベースなのでmGBA不要、高速学習可能。

使い方:
    cd battle_ai
    python train_battle.py          # 学習開始
    python train_battle.py --eval   # 学習済みモデル評価
"""

import argparse
import sys
import os

# battle_aiディレクトリをパスに追加
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv
from stable_baselines3.common.callbacks import EvalCallback
from stable_baselines3.common.monitor import Monitor
from battle_env import BattleEnv


MODEL_PATH = "battle_ppo_model"
LOG_DIR = "battle_logs"


def make_env():
    """環境ファクトリ
    ★ v10.9z255: default を hard 難易度に変更 (v254 hard モデル汎化性能95.5%normal 66.5%hard)
    """
    return BattleEnv(difficulty="hard")


def train(total_timesteps: int = 300_000):
    # ★ v10.9z254: default 500k → 300k (実測効果ピークに近い)
    #   100k: 頭打ち / 300k: +α / 500k: 過学習リスク
    """PPOで学習"""
    os.makedirs(LOG_DIR, exist_ok=True)

    env = DummyVecEnv([make_env for _ in range(4)])
    eval_env = DummyVecEnv([lambda: Monitor(make_env())])

    eval_callback = EvalCallback(
        eval_env,
        best_model_save_path=f"./{LOG_DIR}/best_model",
        log_path=f"./{LOG_DIR}/eval",
        eval_freq=10_000,
        n_eval_episodes=20,
        deterministic=True,
    )

    model = PPO(
        "MlpPolicy",
        env,
        learning_rate=3e-4,
        n_steps=512,
        batch_size=64,
        n_epochs=10,
        gamma=0.99,
        gae_lambda=0.95,
        clip_range=0.2,
        ent_coef=0.01,
        verbose=1,
        tensorboard_log=f"./{LOG_DIR}/tb",
    )

    print(f"Starting training for {total_timesteps} steps...")
    model.learn(
        total_timesteps=total_timesteps,
        callback=eval_callback,
        progress_bar=False,
    )

    model.save(MODEL_PATH)
    print(f"Model saved to {MODEL_PATH}")


def evaluate(n_episodes: int = 50):
    """学習済みモデルの評価"""
    model = PPO.load(MODEL_PATH)
    env = BattleEnv()

    wins = 0
    total_reward = 0
    total_turns = 0

    for ep in range(n_episodes):
        obs, _ = env.reset()
        ep_reward = 0
        done = False

        while not done:
            action, _ = model.predict(obs, deterministic=True)
            obs, reward, done, _, info = env.step(action)
            ep_reward += reward

        total_reward += ep_reward
        total_turns += info.get("turns", 0)
        # 勝利 = 敵全滅（全敵撃破）
        if info.get("enemies_defeated", 0) >= len(env.enemy_team):
            wins += 1

    avg_reward = total_reward / n_episodes
    avg_turns = total_turns / n_episodes
    win_rate = wins / n_episodes * 100
    print(f"Evaluation: {n_episodes} episodes")
    print(f"  Avg reward: {avg_reward:.2f}")
    print(f"  Win rate: {win_rate:.1f}%")
    print(f"  Avg turns: {avg_turns:.1f}")


def main():
    parser = argparse.ArgumentParser(description="Train Battle AI")
    parser.add_argument("--eval", action="store_true", help="Evaluate model")
    parser.add_argument("--steps", type=int, default=300_000, help="Training steps")
    parser.add_argument("--difficulty", default="hard",
                        choices=["easy", "normal", "hard"],
                        help="Battle difficulty (default: hard)")
    args = parser.parse_args()

    # ★ v10.9z255: difficulty flag で動的指定 (train_hard.py 統合)
    global make_env
    def make_env_diff():
        return BattleEnv(difficulty=args.difficulty)
    make_env = make_env_diff

    if args.eval:
        evaluate()
    else:
        train(args.steps)


if __name__ == "__main__":
    main()
