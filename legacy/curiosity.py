"""
RND (Random Network Distillation) 好奇心モジュール
===================================================
参考: Burda et al. (2018) "Exploration by Random Network Distillation"
     https://arxiv.org/abs/1810.12894

【仕組み】
  ・固定ランダムネットワーク（target）: 観測 → 埋め込みベクトル（重みは変えない）
  ・学習可能ネットワーク（predictor）: 観測 → 埋め込みベクトル（target を真似るよう学習）
  ・予測誤差 = 「この状態はどれだけ新しいか」の指標
    → 誤差が大きい（見たことのない状態）→ 内発的報酬が高い
    → 誤差が小さい（何度も訪れた状態）→ 内発的報酬が低い

【エピソードをまたいで持続】
  visited_tiles はエピソードごとにリセットされるが、RND はリセットされない。
  全学習期間を通じて「本当に新しい場所」に報酬を与え続ける。
"""

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim


def _make_mlp(in_dim: int, hidden: int, out_dim: int) -> nn.Sequential:
    return nn.Sequential(
        nn.Linear(in_dim, hidden),
        nn.ReLU(),
        nn.Linear(hidden, out_dim),
    )


class RNDModule:
    """
    RAM 観測ベクトル（OBS_DIM 次元）に対する RND 好奇心モジュール。

    使い方（pokemon_env.py の step() 内）:
        obs = self._get_obs(...)
        intrinsic_r = self.rnd.intrinsic_reward(obs)   # 内発的報酬を計算
        self.rnd.add_obs(obs)                           # バッファに追加

    使い方（train.py の CuriosityUpdateCallback._on_rollout_end()）:
        loss = rnd.update()                             # ロールアウト後に predictor を更新
    """

    def __init__(
        self,
        obs_dim: int     = 4,     # 観測ベクトルの次元数
        embed_dim: int   = 64,    # 埋め込みベクトルの次元数
        hidden: int      = 128,   # 隠れ層のユニット数
        lr: float        = 1e-3,  # predictor の学習率
        reward_scale: float = 0.05,  # 内発的報酬のスケール係数
    ):
        # 固定ランダムターゲット（重みは学習しない）
        self.target = _make_mlp(obs_dim, hidden, embed_dim)
        for p in self.target.parameters():
            p.requires_grad = False

        # 学習可能な予測ネットワーク
        self.predictor = _make_mlp(obs_dim, hidden, embed_dim)
        self.optimizer = optim.Adam(self.predictor.parameters(), lr=lr)

        self.reward_scale = reward_scale
        self.obs_buffer: list = []

        # 報酬の running mean / std（正規化用）
        self._n:    int   = 0
        self._mean: float = 0.0
        self._M2:   float = 0.0

    # ─── Running stats ─────────────────────────────────────────────────────

    def _update_stats(self, r: float) -> None:
        """Welford's online algorithm で平均・分散を更新する。"""
        self._n += 1
        delta = r - self._mean
        self._mean += delta / self._n
        self._M2   += delta * (r - self._mean)

    @property
    def _std(self) -> float:
        if self._n < 10:
            return 1.0  # ウォームアップ期間は正規化しない
        return max((self._M2 / (self._n - 1)) ** 0.5, 0.01)

    # ─── 内発的報酬の計算（forward only） ──────────────────────────────────

    def intrinsic_reward(self, obs: np.ndarray) -> float:
        """
        観測ベクトルから内発的報酬を計算する（勾配なし）。
        ・予測誤差を running std で正規化してからスケーリングする。
        """
        obs_t = torch.FloatTensor(obs).unsqueeze(0)
        with torch.no_grad():
            target_out = self.target(obs_t)
            pred_out   = self.predictor(obs_t)
        err = ((pred_out - target_out) ** 2).mean().item()

        self._update_stats(err)
        normalized = err / self._std
        return float(normalized * self.reward_scale)

    # ─── バッファ管理 ──────────────────────────────────────────────────────

    def add_obs(self, obs: np.ndarray) -> None:
        """ロールアウト中に観測をバッファへ追加する。"""
        self.obs_buffer.append(obs.copy())

    # ─── Predictor の更新 ──────────────────────────────────────────────────

    def update(self) -> float:
        """
        バッファに溜まった観測で predictor を1回更新する。
        ロールアウト終了後に呼ぶ（CuriosityUpdateCallback._on_rollout_end）。
        バッファはクリアされる。更新後の loss 値を返す。
        """
        if not self.obs_buffer:
            return 0.0

        obs_t = torch.FloatTensor(np.array(self.obs_buffer))

        with torch.no_grad():
            target_out = self.target(obs_t)
        pred_out = self.predictor(obs_t)

        loss = ((pred_out - target_out) ** 2).mean()
        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()

        self.obs_buffer.clear()
        return loss.item()
