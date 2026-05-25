"""VisualPokemonEnv — 画面観測を追加したラッパー環境。

汎用ポケモンAI計画の基盤。既存のPokemonEnvをラップして、
ゲーム画面（240x160 RGB）を観測に追加する。

使い方:
    # 既存のtrain.pyと同様だが、画面観測付き
    from visual_env import VisualPokemonEnv
    env = VisualPokemonEnv(port=5000, ...)

観測空間:
    Dict({
        "vector": Box(22,)     # 既存の数値観測（互換性維持）
        "screen": Box(80,72,3) # 画面のリサイズ版（240x160 → 80x72に縮小）
        "local_map": Box(21,21,3)  # ExplorationMapの局所ビュー（プレイヤー中心21x21）
    })

設計原則:
    - pokemon_env.pyを変更しない（ラッパーとして動作）
    - 既存の学習を壊さない（vector観測は同一）
    - 画面キャプチャ失敗時は黒画面で代替（学習を止めない）
    - local_mapは将来的にCNN学習済みタイル分類に置き換え可能
"""

import numpy as np
import gymnasium as gym
from gymnasium import spaces
import urllib.request
import tempfile
import os
import json

# 画面キャプチャ設定
SCREEN_W, SCREEN_H = 240, 160
RESIZED_W, RESIZED_H = 80, 72   # CNN入力用に縮小（約1/3）
LOCAL_MAP_SIZE = 21               # プレイヤー中心の局所マップサイズ（21x21）

# タイルの色（local_map描画用）
TILE_COLORS = {
    "walkable":  [0, 200, 0],     # 緑 = 歩ける
    "wall":      [200, 0, 0],     # 赤 = 壁
    "unknown":   [50, 50, 50],    # 暗灰 = 未探索
    "door":      [0, 0, 200],     # 青 = warp/ドア
    "player":    [255, 255, 0],   # 黄 = プレイヤー位置
}


class VisualPokemonEnv(gym.Wrapper):
    """画面観測を追加するラッパー環境。

    既存のPokemonEnvをラップし、画面キャプチャとローカルマップを
    観測に追加する。action_spaceは変更しない。
    """

    def __init__(self, env, capture_screen=True, capture_local_map=True):
        """
        Args:
            env: ベースのPokemonEnv
            capture_screen: 画面キャプチャを有効にするか
            capture_local_map: ローカルマップ観測を有効にするか
        """
        super().__init__(env)

        self.capture_screen = capture_screen
        self.capture_local_map = capture_local_map

        # 元の観測空間を取得
        original_obs_space = env.observation_space
        if isinstance(original_obs_space, spaces.Box):
            vector_dim = original_obs_space.shape[0]
        else:
            vector_dim = 22  # デフォルト

        # 新しい観測空間を定義（Dict）
        obs_spaces = {
            "vector": spaces.Box(
                low=-1.0, high=1.0,
                shape=(vector_dim,),
                dtype=np.float32
            ),
        }

        if capture_screen:
            obs_spaces["screen"] = spaces.Box(
                low=0, high=255,
                shape=(RESIZED_H, RESIZED_W, 3),
                dtype=np.uint8
            )

        if capture_local_map:
            obs_spaces["local_map"] = spaces.Box(
                low=0, high=255,
                shape=(LOCAL_MAP_SIZE, LOCAL_MAP_SIZE, 3),
                dtype=np.uint8
            )

        self.observation_space = spaces.Dict(obs_spaces)

        # 画面キャプチャ用の一時ファイル
        self._screenshot_path = os.path.join(
            tempfile.gettempdir(),
            f"visual_env_{getattr(env, '_port', 5000)}.png"
        )

        # 黒画面（フォールバック用）
        self._black_screen = np.zeros(
            (RESIZED_H, RESIZED_W, 3), dtype=np.uint8)
        self._blank_map = np.full(
            (LOCAL_MAP_SIZE, LOCAL_MAP_SIZE, 3),
            TILE_COLORS["unknown"], dtype=np.uint8)

    def _capture_screen(self):
        """mGBA APIでスクリーンショットを取得してnumpy配列に変換"""
        try:
            port = getattr(self.env, '_port', 5000)
            tmp_win = self._screenshot_path.replace("/", os.sep)
            url = f"http://localhost:{port}/core/screenshot?path={tmp_win}"
            req = urllib.request.Request(url, method="POST")
            with urllib.request.urlopen(req, timeout=1) as resp:
                if resp.status == 200:
                    from PIL import Image
                    img = Image.open(self._screenshot_path)
                    img = img.resize((RESIZED_W, RESIZED_H),
                                     Image.BILINEAR)
                    arr = np.array(img, dtype=np.uint8)
                    img.close()
                    if arr.shape == (RESIZED_H, RESIZED_W, 3):
                        return arr
        except Exception:
            pass
        return self._black_screen.copy()

    def _build_local_map(self):
        """ExplorationMapからプレイヤー中心の局所マップを生成"""
        try:
            env = self.env
            mg = getattr(env, '_cached_map_group', 0)
            mn = getattr(env, '_cached_map_num', 0)
            px = getattr(env, 'prev_x', 0)
            py = getattr(env, 'prev_y', 0)
            emap = getattr(env, '_exploration_map', None)

            if emap is None or not hasattr(emap, 'graph'):
                return self._blank_map.copy()

            half = LOCAL_MAP_SIZE // 2  # 10
            local = np.full((LOCAL_MAP_SIZE, LOCAL_MAP_SIZE, 3),
                           TILE_COLORS["unknown"], dtype=np.uint8)

            graph = emap.graph
            for dy in range(-half, half + 1):
                for dx in range(-half, half + 1):
                    tx, ty = px + dx, py + dy
                    node = (mg, mn, tx, ty)

                    if node in graph:
                        edges = graph[node]

                        # warpチェック
                        is_door = False
                        for d, dest in edges.items():
                            if d.startswith("wall_"):
                                continue
                            if (isinstance(dest, (list, tuple))
                                    and len(dest) >= 2
                                    and (dest[0] != mg or dest[1] != mn)):
                                is_door = True
                                break

                        wall_count = sum(1 for d in edges
                                        if d.startswith("wall_"))

                        if is_door:
                            color = TILE_COLORS["door"]
                        elif wall_count >= 3:
                            color = TILE_COLORS["wall"]
                        else:
                            color = TILE_COLORS["walkable"]
                    else:
                        color = TILE_COLORS["unknown"]

                    my, mx = dy + half, dx + half
                    local[my, mx] = color

            # プレイヤー位置を上書き
            local[half, half] = TILE_COLORS["player"]

            return local
        except Exception:
            return self._blank_map.copy()

    def _build_obs(self, vector_obs):
        """ベクトル観測 + 画面 + ローカルマップを結合"""
        obs = {"vector": vector_obs}

        if self.capture_screen:
            obs["screen"] = self._capture_screen()

        if self.capture_local_map:
            obs["local_map"] = self._build_local_map()

        return obs

    def reset(self, **kwargs):
        obs, info = self.env.reset(**kwargs)
        return self._build_obs(obs), info

    def step(self, action):
        obs, reward, terminated, truncated, info = self.env.step(action)
        return self._build_obs(obs), reward, terminated, truncated, info


class VisualFeatureExtractor:
    """SB3のカスタム特徴抽出器のテンプレート。

    使い方（train_visual.pyで）:
        from stable_baselines3.common.torch_layers import BaseFeaturesExtractor
        import torch.nn as nn

        class PokemonCNN(BaseFeaturesExtractor):
            def __init__(self, observation_space, features_dim=128):
                super().__init__(observation_space, features_dim)
                # screen用CNN
                self.screen_cnn = nn.Sequential(
                    nn.Conv2d(3, 16, 3, stride=2, padding=1),  # 72x80 → 36x40
                    nn.ReLU(),
                    nn.Conv2d(16, 32, 3, stride=2, padding=1), # 36x40 → 18x20
                    nn.ReLU(),
                    nn.Conv2d(32, 32, 3, stride=2, padding=1), # 18x20 → 9x10
                    nn.ReLU(),
                    nn.Flatten(),
                )
                # local_map用CNN
                self.map_cnn = nn.Sequential(
                    nn.Conv2d(3, 16, 3, stride=1, padding=1),  # 21x21
                    nn.ReLU(),
                    nn.Conv2d(16, 16, 3, stride=2, padding=1), # 11x11
                    nn.ReLU(),
                    nn.Flatten(),
                )
                # 結合層
                # screen: 32*9*10 = 2880, map: 16*11*11 = 1936, vector: 22
                self.fc = nn.Sequential(
                    nn.Linear(2880 + 1936 + 22, 256),
                    nn.ReLU(),
                    nn.Linear(256, features_dim),
                    nn.ReLU(),
                )

            def forward(self, observations):
                screen = observations["screen"].float() / 255.0
                screen = screen.permute(0, 3, 1, 2)  # NHWC → NCHW
                local_map = observations["local_map"].float() / 255.0
                local_map = local_map.permute(0, 3, 1, 2)
                vector = observations["vector"]

                s_feat = self.screen_cnn(screen)
                m_feat = self.map_cnn(local_map)
                combined = torch.cat([s_feat, m_feat, vector], dim=1)
                return self.fc(combined)
    """
    pass


def test_visual_env():
    """簡易テスト — VisualPokemonEnvの観測空間を確認"""
    print("=== VisualPokemonEnv Test ===")
    print(f"Screen: {RESIZED_W}x{RESIZED_H} RGB")
    print(f"Local map: {LOCAL_MAP_SIZE}x{LOCAL_MAP_SIZE} RGB")
    print(f"Vector: 22 dim (existing)")
    print()
    print("Observation space structure:")
    print("  Dict({")
    print(f'    "vector": Box({22},),')
    print(f'    "screen": Box({RESIZED_H},{RESIZED_W},3),')
    print(f'    "local_map": Box({LOCAL_MAP_SIZE},{LOCAL_MAP_SIZE},3),')
    print("  })")
    print()
    print("CNN input sizes:")
    print(f"  screen: 3x{RESIZED_H}x{RESIZED_W} = {3*RESIZED_H*RESIZED_W} values")
    print(f"  local_map: 3x{LOCAL_MAP_SIZE}x{LOCAL_MAP_SIZE} = {3*LOCAL_MAP_SIZE*LOCAL_MAP_SIZE} values")
    print(f"  vector: {22} values")
    print(f"  Total: {3*RESIZED_H*RESIZED_W + 3*LOCAL_MAP_SIZE*LOCAL_MAP_SIZE + 22} values")


if __name__ == "__main__":
    test_visual_env()
