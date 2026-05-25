# Pokemon Emerald RL Agent

> Multi-instance reinforcement learning agent that plays Pokemon Emerald autonomously via mGBA emulator. Combines rule-based navigation with RL for battle strategy, parallel training across 3 emulator instances, and canonical map data injection from the pokeemerald decompilation.

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.0+-orange.svg)](https://pytorch.org/)
[![stable-baselines3](https://img.shields.io/badge/stable--baselines3-PPO-green.svg)](https://stable-baselines3.readthedocs.io/)

## ハイライト

- **14,000+ lines** の custom Gymnasium 環境 ([`pokemon_env.py`](pokemon_env.py))
- **3 instances parallel** で mGBA 並列学習 (port 8888/8889/8890)
- **PPO + ルールベース hybrid** = 安定動作 + 学習発展
- **Canonical map data injection** ([`tools/canon_map_inject.py`](tools/canon_map_inject.py)): pokeemerald decomp の `map.bin` (16-bit block format) を解析し、 BFS-walkable tile + wall を `exploration_map.json` に事前注入することで探索を大幅短縮
- **継続的監視 + 自動 patch deploy** workflow ([`tools/monitor.py`](tools/monitor.py))
- **Badge 1 / Badge 2 取得** 達成 (Brawly Gym 撃破)

## アーキテクチャ

```
┌──────────────────────────────────────────────────────┐
│  train.py  (PPO loop, stable-baselines3, 3 env)     │
│      ↓                                                │
│  pokemon_env.py  (custom Gymnasium env)              │
│  - 26-dim observation                                │
│  - Rule-based + RL hybrid action selection           │
│  - BFS pathfinding on exploration_map.json           │
│  - Battle handler (FIGHT/SWITCH/RUN logic)           │
│  - Heal cycle (PC nav, HP/PP recovery)               │
│      ↓ HTTP                                          │
│  mgba-http  (custom socket protocol, |END| terminator)│
│      ↓                                               │
│  mGBA × 3 instances                                  │
└──────────────────────────────────────────────────────┘
```

## 主要 component

| File | 役割 | 行数 |
|---|---|---|
| `pokemon_env.py` | Custom Gym env: state/reward/action chain (~14k 行) | 14,000+ |
| `train.py` | PPO training entry point | ~150 |
| `tools/monitor.py` | 3-port status checker + cadence violation detector | ~600 |
| `tools/mapping_audit.py` | exploration_map.json validation vs pokeemerald canon | ~150 |
| `tools/canon_map_inject.py` | pokeemerald map.bin → exploration_map injection | ~200 |
| `tools/tile_classifier.py` | CNN-based tile type classifier (walkable/wall/door) | ~400 |
| `battle_ai/` | Standalone PPO battle agent (separated module) | ~500 |

## 技術スタック

- **Python 3.10+** / PyTorch / stable-baselines3 (PPO)
- **Gymnasium** (custom env, 26-dim obs, 6 discrete actions)
- **mGBA emulator** + custom HTTP/socket bridge
- **pokeemerald decompilation** ([pret/pokeemerald](https://github.com/pret/pokeemerald)) で canonical map data 取得
- **BFS pathfinding** on graph-based exploration map
- **CNN tile classifier** (Squeeze-Excitation residual blocks, Focal Loss, Mixup)

## 設計上の工夫

### 1. Rule-based + RL hybrid
完全 RL は探索 cost が高いため、 主要 nav は rule-based BFS、 battle 選択を RL で学習する hybrid 構成。 学習進捗に応じて override 率を段階的削減する設計 (`_ai_original_action`)。

### 2. Parallel multi-instance training
3 つの mGBA インスタンスで独立 PPO env を並列実行。 各 port が独自 save state を持ち、 BestSave (Slot 8) で進捗最良 instance を保持。

### 3. Canonical data injection
maze 形式 map (Brawly Gym 等) で random walk が突破不能な場合、 pokeemerald decomp の `map.bin` (1008 bytes = 18×28×2、 bits 10-11 が collision) を解析して passable/wall を事前注入。 Brawly Gym では **coverage 12.1% → 30%** で BFS path 33 to Brawly 計算可能化、 2 日 chronic stuck を突破。

### 4. iterative root-cause debugging
記録された patch history (v10.9z269b〜z278 等) は、 chronic stuck 問題に対する真因深掘り pattern を示す:
- nav 上書きの優先順位問題発見
- wall_hits 累積による偽 wall 化検出
- battle menu cycle 中の偽 faint emergence
- PC heal complete 検出条件の段階的緩和

各 patch は targeted fix で副作用最小化を意図 (`patch tower prevention` 警戒)。

## 学習過程の証跡

`daily_progress/YYYY-MM-DD.md` 形式で日次進捗・反省・教訓を記録。 `memory/feedback_*.md` に user feedback と自分の遵守状態を記録。 これらは ML 開発の試行錯誤と継続改善の証跡。

主要 milestone:
- **2026-04-XX**: Badge 1 取得 (Roxanne)
- **2026-05-23**: Badge 2 取得 (Brawly) ← canon data injection 後
- 進行中: Granite Cave → Badge 3 (Wattson)

## セットアップ

```bash
# 1. ROM 配置 (各自で用意 — repo には含まない)
# 例: Pokemon Emerald.gba を mGBA で開く

# 2. mGBA × 3 起動 (ports 8888/8889/8890)

# 3. venv 作成
python -m venv poke-rl
poke-rl/Scripts/activate  # Windows
pip install -r requirements.txt

# 4. 学習開始
PYTHONUNBUFFERED=1 python -u train.py > training_current.log 2>&1 &

# 5. 監視
poke-rl/Scripts/python.exe tools/monitor.py
```

詳細手順は [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md)、 動作要件は [`CLAUDE.md`](CLAUDE.md)。

## 注意

- **ROM file は含まれません** (著作権)。 各自で正規入手要。
- 開発は Windows 11 + Python 3.10 で実施。 mGBA 0.10+ 推奨。
- `exploration_map.json` は本リポジトリでの学習 8000+ tiles のデータ。 ゼロから始める場合は空 dict で初期化可能。

## ライセンス

MIT (詳細 LICENSE)
