"""
学習済みモデルの観察スクリプト
================================
【使い方】
  python evaluate.py
  python evaluate.py --model models/pokemon_50000_steps.zip  # モデルを指定

学習済みモデルが mGBA でポケモンをプレイする様子をリアルタイムで観察できます。
"""

import os
import glob
import argparse
import time

from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecFrameStack

from pokemon_env import PokemonEmeraldEnv

SAVE_DIR = "./models/"


def find_latest_model() -> str | None:
    # pokemon_latest.zip を優先
    latest = os.path.join(SAVE_DIR, "pokemon_latest.zip")
    if os.path.exists(latest):
        return latest

    # チェックポイントから最新を探す
    pattern = os.path.join(SAVE_DIR, "pokemon_*_steps.zip")
    files   = glob.glob(pattern)
    if not files:
        return None

    def extract_steps(path):
        base = os.path.basename(path)
        try:
            return int(base.split("_")[1])
        except (IndexError, ValueError):
            return 0

    return max(files, key=extract_steps)


def evaluate(model_path: str = None, episodes: int = 0):
    """
    Parameters
    ----------
    model_path : 使用するモデルのパス。None の場合は最新を自動選択。
    episodes   : 実行するエピソード数。0 = 無制限。
    """
    if model_path is None:
        model_path = find_latest_model()

    if model_path is None or not os.path.exists(model_path):
        print("モデルが見つかりません。先に train.py を実行してください。")
        return

    print(f"モデルを読み込み中: {model_path}")

    env = DummyVecEnv([lambda: PokemonEmeraldEnv(max_steps=50000, step_delay=0.1)])
    env = VecFrameStack(env, n_stack=4)

    model = PPO.load(model_path, env=env)

    obs        = env.reset()
    ep         = 0
    total_step = 0
    ep_reward  = 0.0
    ep_step    = 0
    start_time = time.time()

    print("エージェントがプレイ中... (Ctrl+C で停止)")
    print("-" * 60)

    try:
        while True:
            action, _ = model.predict(obs, deterministic=True)
            obs, reward, done, info = env.step(action)

            ep_reward  += float(reward[0])
            ep_step    += 1
            total_step += 1

            # 定期的な状態表示
            if total_step % 200 == 0:
                i = info[0]
                elapsed = time.time() - start_time
                print(
                    f"  [EP{ep+1} | Step {ep_step:5d}] "
                    f"バッジ: {i.get('badges','?')} | "
                    f"探索タイル: {i.get('visited_tiles','?')} | "
                    f"HP: {i.get('hp_cur','?')}/{i.get('hp_max','?')} | "
                    f"累計報酬: {ep_reward:8.1f} | "
                    f"経過: {elapsed/60:.1f}分"
                )

            if done[0]:
                ep += 1
                i = info[0]
                print(
                    f"\nエピソード {ep} 終了 | "
                    f"ステップ: {ep_step} | "
                    f"総報酬: {ep_reward:.1f} | "
                    f"バッジ: {i.get('badges','?')} | "
                    f"探索タイル: {i.get('visited_tiles','?')}\n"
                )
                obs        = env.reset()
                ep_reward  = 0.0
                ep_step    = 0

                if episodes > 0 and ep >= episodes:
                    print(f"{episodes} エピソード完了。終了します。")
                    break

    except KeyboardInterrupt:
        print(f"\n停止。総ステップ: {total_step}")

    env.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model",    type=str, default=None, help="モデルファイルのパス")
    parser.add_argument("--episodes", type=int, default=0,    help="実行するエピソード数（0=無制限）")
    args = parser.parse_args()

    evaluate(model_path=args.model, episodes=args.episodes)
