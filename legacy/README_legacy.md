# Pokemon Emerald RL Agent

> Pokemon Emerald (GBA) を自動プレイする強化学習エージェント。 3 つの mGBA エミュレータを並列実行し、 ルールベース BFS ナビゲーションと PPO によるバトル学習を組み合わせた hybrid 構成。

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.0+-orange.svg)](https://pytorch.org/)
[![Gymnasium](https://img.shields.io/badge/gymnasium-custom%20env-green.svg)](https://gymnasium.farama.org/)
[![stable-baselines3](https://img.shields.io/badge/SB3-PPO-orange.svg)](https://stable-baselines3.readthedocs.io/)

---

## 何をしているのか

ゲームエミュレータ (mGBA) の RAM を読み書きし、 ボタン入力を送ることで、 Pokemon Emerald を「自分で進める」 エージェントを実装しています。 主に 3 つの問題を扱います:

1. **ナビゲーション**: マップ上で目的地まで歩く (例: ジムへ行く、 ポケモンセンターで回復)
2. **バトル**: 野生・トレーナー戦で技を選択して勝つ
3. **長期戦略**: ストーリー進行、 レベリング、 アイテム管理 — どの順で何をやるか

これらを **ルールベース (BFS による経路探索 + finite state machine)** と **強化学習 (PPO によるバトル技選択)** の hybrid で解いています。

## なぜやっているのか / 目指していること

**短期目標**: Pokemon Emerald を「クリア」する自律エージェントを作る。 ストーリー全 8 ジム + 殿堂入りまで。

**中期目標**: ここで培った 5 層分解アーキテクチャ (画面認識 → マップ構築 → 経路探索 → バトル AI → 高レベル判断) を、 **他の Pokemon シリーズ (FRLG / DPPt / HGSS 等) でも転用可能な汎用 RL agent framework** に発展させる。

**学んでいること / 示したいこと**:
- **大規模 (~14,000 行) Python codebase の継続的設計・refactoring**
- 強化学習 (Gymnasium custom env, PPO) の実問題への適用
- エミュレータ・低レイヤー (RAM 読み書き、 socket protocol) との接続
- **continuous monitoring + iterative debugging** workflow の構築
- **canonical data** (オープンソースの decompilation) を活用した探索コスト削減

## アーキテクチャ

```
┌─────────────────────────────────────────────────────────────┐
│  train.py  (PPO loop, stable-baselines3, 3 parallel envs)  │
│      ↓                                                       │
│  pokemon_env.py  (custom Gymnasium environment, ~14k lines) │
│  ├─ 26-dim observation space (HP/PP/位置/相手 type 等)       │
│  ├─ 6 discrete actions (A/B/Up/Down/Left/Right)             │
│  ├─ Reward shaping (探索/レベリング/バトル勝利)              │
│  └─ Rule-based action override (BFS path / battle handler)  │
│      ↓ HTTP                                                  │
│  mgba-http  (custom socket protocol, <|END|> terminator)    │
│      ↓                                                       │
│  mGBA × 3 instances  (ports 8888 / 8889 / 8890)             │
└─────────────────────────────────────────────────────────────┘
```

### 5 層分解 (汎用化を見据えた設計)

| Layer | 役割 | 主要 file |
|---|---|---|
| **L1 画面認識** | CNN で tile を分類 (walkable/wall/door) | `tools/tile_classifier.py` |
| **L2 マップ構築** | 探索済 tile を graph として持つ (`exploration_map.json`) | `pokemon_env.py` (MapGraph) |
| **L3 経路探索** | BFS で目的地までの最短 path 計算 | `pokemon_env.py` (`bfs_to_position`) |
| **L4 バトル AI** | PPO で技選択を学習 (別 module で実験) | `battle_ai/train_battle.py` |
| **L5 高レベル判断** | story 進行 / heal / レベリング切替 | `pokemon_env.py` (rule-based) |

## 主要 component

| File / Directory | 役割 |
|---|---|
| `pokemon_env.py` | Custom Gymnasium env (~14,000 行)。 RAM 読み取り、 action chain、 reward shaping、 全体制御 |
| `train.py` | PPO 学習 entry point (stable-baselines3) |
| `tools/monitor.py` | 3-port 状態 monitor (位置/HP/PP/stuck 検出) |
| `tools/mapping_audit.py` | `exploration_map.json` の整合性チェック (canonical map 寸法 vs 実探索 tile) |
| `tools/canon_map_inject.py` | pokeemerald decomp の `map.bin` (16-bit block format) を解析し、 探索 graph に passable/wall を事前注入 |
| `tools/tile_classifier.py` | CNN tile 分類器 (SE block + Focal Loss + Mixup) |
| `battle_ai/` | バトル専用 PPO agent (env から分離した standalone module) |
| `exploration_map.json` | 8,200+ tile の探索 graph (実学習で蓄積したデータ) |

## 技術的工夫

### 1. ルールベース + RL hybrid

完全な RL でゼロから学習させると探索コストが膨大なため、 経路探索や heal cycle は決定論的に解き、 学習の余地を「バトル技選択」 「stuck 時の脱出」 に絞っています。 学習の進捗に応じて override 率を段階的に下げる設計 (`_ai_original_action` で AI 提案 action を保持)。

### 2. Parallel multi-instance training

3 つの mGBA インスタンスで独立した PPO env を並列実行。 各 port が独自の save state を持ち、 monitor.py が定期的に `BestSave (Slot 8)` で最良 instance を保存。 1 instance が stuck しても他で進捗できる冗長性。

### 3. Canonical data injection

迷路型マップ (Brawly Gym 等) では random walk による探索が突破不能でした。 解決策として、 **pokeemerald decompilation** ([pret/pokeemerald](https://github.com/pret/pokeemerald)) から `data/layouts/<MapName>/map.bin` を取得し、 16-bit block format (bits 0-9 metatile、 bits 10-11 collision) を解析、 walkable tile + wall を `exploration_map.json` に事前注入します。

```python
# tools/canon_map_inject.py の中核
blocks = struct.unpack(f"<{w*h}H", map_bin_data)
for cy in range(h):
    for cx in range(w):
        coll = (blocks[cy*w + cx] >> 10) & 0x3
        if coll == 0:
            passable.add((cx, cy))
```

Brawly Gym で **coverage 12% → 30%** に改善、 BFS path が Brawly NPC まで到達可能になりました。

### 4. Continuous monitoring + iterative deployment

開発中は `monitor.py` を 10 分毎に実行して 3 port 全状態をチェック。 problem 検出時にスクリーンショット + ログから root cause を特定し、 `pokemon_env.py` を patch して即時 deploy → 再起動 → 効果検証、 のループを構築しました。

`mapping_audit.py` で `exploration_map.json` の異常値 (canonical 寸法外の garbage tile) を継続的に検出・自動 purge する仕組みもあります。

## 学習スタック

- **言語**: Python 3.10+
- **ML**: PyTorch / stable-baselines3 (PPO) / Gymnasium
- **CV**: 自作 CNN (Squeeze-Excitation residual blocks + Focal Loss + Mixup augmentation)
- **データ**: pokeemerald decompilation (canonical map data, GPL-licensed)
- **エミュレータ**: mGBA + 自作 socket bridge (lua script + HTTP wrapper)

## 動作要件

- Windows / macOS / Linux (主に Windows 11 で開発)
- Python 3.10 以上
- mGBA 0.10+ ×3 instances
- Pokemon Emerald ROM (各自で正規入手 — **本リポジトリには含まれません**)
- HTTP bridge (例: [mgba-http](https://github.com/nikouu/mGBA-http))

## セットアップ

```bash
# 1. clone
git clone https://github.com/tara0kun/pokemon-rl.git
cd pokemon-rl

# 2. venv + 依存 install
python -m venv poke-rl
poke-rl/Scripts/activate   # Windows (Linux/Mac: source poke-rl/bin/activate)
pip install -r requirements.txt

# 3. mGBA × 3 を起動し、 各々で Pokemon Emerald ROM を開く + lua socket script load
#    (詳細は mgba_scripts/ 配下の lua file を参照)

# 4. 学習開始
PYTHONUNBUFFERED=1 python -u train.py > training.log 2>&1 &

# 5. 状態 monitor
python tools/monitor.py
```

## ライセンス

MIT License (詳細は [LICENSE](LICENSE))

本リポジトリのコードのみが対象。 Pokemon Emerald ROM および任天堂・Game Freak の知的財産権は本リポジトリと一切関係ありません。

## Author

[@tara0kun](https://github.com/tara0kun) — IPUT Tokyo
