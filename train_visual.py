"""train_visual.py — 画面観測CNNポリシーの実験用学習スクリプト。

汎用ポケモンAI計画の実験環境。既存のtrain.pyと並行して動作可能。
VisualPokemonEnvで画面+ローカルマップ観測を追加し、
CnnPolicy相当のカスタムポリシーで学習する。

使い方:
    # 実験実行（既存学習とは別ポートまたは別タイミングで）
    python train_visual.py --port 5000 --steps 10000

    # ローカルマップのみ（画面なし、軽量版）
    python train_visual.py --port 5000 --steps 10000 --no-screen

注意:
    - 既存のtrain.py学習中に同ポートで実行しないこと
    - 実験用。本番はtrain.pyが担当
"""

import argparse
import numpy as np
import torch
import torch.nn as nn
from gymnasium import spaces

from stable_baselines3 import PPO
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor

from visual_env import (
    VisualPokemonEnv, RESIZED_W, RESIZED_H, LOCAL_MAP_SIZE
)


class PokemonVisualExtractor(BaseFeaturesExtractor):
    """画面 + ローカルマップ + ベクトル観測を処理するCNN特徴抽出器。

    Dict観測空間:
        screen:    (72, 80, 3)  → CNN → 2880次元
        local_map: (21, 21, 3)  → CNN → 1936次元
        vector:    (22,)        → そのまま
    → 結合 → FC → 128次元特徴ベクトル
    """

    def __init__(self, observation_space: spaces.Dict, features_dim: int = 128):
        super().__init__(observation_space, features_dim)

        self.has_screen = "screen" in observation_space.spaces
        self.has_map = "local_map" in observation_space.spaces

        extractors = {}
        total_concat_size = 0

        # screen CNN
        if self.has_screen:
            h, w = observation_space["screen"].shape[:2]
            self.screen_cnn = nn.Sequential(
                nn.Conv2d(3, 16, 3, stride=2, padding=1),
                nn.ReLU(),
                nn.Conv2d(16, 32, 3, stride=2, padding=1),
                nn.ReLU(),
                nn.Conv2d(32, 32, 3, stride=2, padding=1),
                nn.ReLU(),
                nn.Flatten(),
            )
            # 計算: 72x80 → 36x40 → 18x20 → 9x10 → 32*9*10 = 2880
            with torch.no_grad():
                dummy = torch.zeros(1, 3, h, w)
                screen_out = self.screen_cnn(dummy).shape[1]
            total_concat_size += screen_out
        else:
            self.screen_cnn = None

        # local_map CNN
        if self.has_map:
            ms = observation_space["local_map"].shape[0]  # 21
            self.map_cnn = nn.Sequential(
                nn.Conv2d(3, 16, 3, stride=1, padding=1),
                nn.ReLU(),
                nn.Conv2d(16, 16, 3, stride=2, padding=1),
                nn.ReLU(),
                nn.Flatten(),
            )
            with torch.no_grad():
                dummy = torch.zeros(1, 3, ms, ms)
                map_out = self.map_cnn(dummy).shape[1]
            total_concat_size += map_out
        else:
            self.map_cnn = None

        # vector
        vector_dim = observation_space["vector"].shape[0]
        total_concat_size += vector_dim

        # 結合FC
        self.fc = nn.Sequential(
            nn.Linear(total_concat_size, 256),
            nn.ReLU(),
            nn.Linear(256, features_dim),
            nn.ReLU(),
        )

    def forward(self, observations):
        parts = []

        if self.screen_cnn is not None:
            screen = observations["screen"].float() / 255.0
            screen = screen.permute(0, 3, 1, 2)  # NHWC → NCHW
            parts.append(self.screen_cnn(screen))

        if self.map_cnn is not None:
            local_map = observations["local_map"].float() / 255.0
            local_map = local_map.permute(0, 3, 1, 2)
            parts.append(self.map_cnn(local_map))

        parts.append(observations["vector"])

        combined = torch.cat(parts, dim=1)
        return self.fc(combined)


def make_visual_env(port=5000, capture_screen=True, capture_local_map=True):
    """VisualPokemonEnvを作成"""
    from pokemon_env import PokemonEnv
    base_env = PokemonEnv(port=port)
    return VisualPokemonEnv(
        base_env,
        capture_screen=capture_screen,
        capture_local_map=capture_local_map,
    )


def main():
    parser = argparse.ArgumentParser(description="Visual Pokemon RL Training")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--steps", type=int, default=10000)
    parser.add_argument("--no-screen", action="store_true",
                        help="画面キャプチャなし（ローカルマップのみ）")
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--save-path", default="models/visual_ppo")
    args = parser.parse_args()

    print("=== Visual Pokemon RL Training ===")
    print(f"Port: {args.port}")
    print(f"Steps: {args.steps}")
    print(f"Screen: {not args.no_screen}")
    print(f"Learning rate: {args.lr}")

    # 環境作成
    env = make_visual_env(
        port=args.port,
        capture_screen=not args.no_screen,
        capture_local_map=True,
    )

    # ポリシー設定
    policy_kwargs = dict(
        features_extractor_class=PokemonVisualExtractor,
        features_extractor_kwargs=dict(features_dim=128),
    )

    # PPOモデル作成
    model = PPO(
        "MultiInputPolicy",
        env,
        policy_kwargs=policy_kwargs,
        learning_rate=args.lr,
        n_steps=256,
        batch_size=64,
        n_epochs=4,
        verbose=1,
        device="cpu",  # GPU利用可能なら "auto" に変更
    )

    print(f"\nModel architecture:")
    print(f"  Policy: MultiInputPolicy + PokemonVisualExtractor")
    print(f"  Features dim: 128")
    print(f"  Observation space: {env.observation_space}")
    print(f"  Action space: {env.action_space}")

    # 学習
    print(f"\nStarting training for {args.steps} steps...")
    model.learn(total_timesteps=args.steps)

    # 保存
    import os
    os.makedirs(os.path.dirname(args.save_path), exist_ok=True)
    model.save(args.save_path)
    print(f"Model saved to {args.save_path}")

    env.close()


if __name__ == "__main__":
    main()
