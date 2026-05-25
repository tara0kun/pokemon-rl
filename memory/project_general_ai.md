---
name: general_ai_plan
description: 汎用ポケモンAI計画の詳細（アーキテクチャ、進捗、次のタスク）
type: project
---

# 汎用ポケモンAI計画

## 方針（2026-03-20決定）
- **ハイブリッド**: エメラルドクリア（ルールベース）を継続しつつ、汎用AI基盤を並行構築
- **最終目標**: 他ポケモン作品(GBA/DS)でも使える汎用AI
- **原則**: 新ファイルで実験、`pokemon_env.py`は壊さない

## アーキテクチャ（5層部品分解）
1. **画面認識CNN** (`tools/tile_classifier.py`) — 16x16タイル→walkable/wall/unknown/door分類
2. **マップ構築** — タイル分類結果から自動マップ生成（現在はRAM読みで手動BFS）
3. **BFSナビ** — 構築マップ上の経路探索（現在のexploration_mapベース）
4. **バトルRL** — v10.9z176でAIの技選択をPPOに開放（基盤完了）
5. **高レベル判断** — ストーリー進行・回復・レベリングの自律判断

## 現在の進捗

### タイルデータ収集 (`tools/tile_collector.py`) ✅
- **24,900タイル収集済み** (walkable:10881, wall:3006, unknown:10380, door:633)
- mGBAスクリーンショット→16x16分割→ExplorationMapで仮ラベル付与

### タイル分類CNN (`tools/tile_classifier.py`) — 精度改善中
- **全体精度: 80.9%**
- walkable F1=0.86, unknown F1=0.88 → 良好
- **★ wall F1=0.36** → recall 27.5%が低い（wallの73%を誤分類）
- **★ door F1=0.41** → データ数633件と少ない
- **次のアクション**: wall分類改善（クラス重み調整、データ増強）

### バトルRL (v10.9z176) ✅基盤完了
- AIの技選択(Move1-4)をPPOに開放
- OBS_DIM 22→26（PP観測追加）
- ダメージ報酬1.0x、ボタン固定報酬撤去

### 画面ベース環境 (`visual_env.py`) — 未テスト
- 存在するが実行テスト未実施

## 次のタスク（優先順）
1. tile_classifierのwall recall改善
2. タイルデータ追加収集（新エリア）
3. visual_env.pyの動作確認・改善
4. タイル分類→自動マップ構築パイプライン
