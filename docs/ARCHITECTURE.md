# ARCHITECTURE — generic_agent の現行構成

> Last verified: 2026-07-06 (HEAD `3e9e23db6`, dev branch)
> 対象読者: このリポジトリを引き継ぐ AI/人間。「ソースを読めば分かるが、読まないと分からない」全体像を先に渡すための文書。
> 各主張には根拠となる `ファイル:行` を付す。コードと矛盾したらコードが正。

## 1. プロジェクトの系譜(3世代)

| 世代 | 実体 | 状態 |
|------|------|------|
| 旧 rule-based RL (23,000行 pokemon_env.py, BFS+PPO) | `old` branch / `legacy/` | 2026-06-08 凍結。import 禁止 |
| VLM agent v1 (Opus vision + tool use → 3段コスト最適化) | `brain.py`+`loop.py`, `auto_loop.py`+`local_brain.py`+`rescue_brain.py` | 残存・非主力 |
| **ヒューリスティック+canon データ hybrid(現行)** | **`claude_heuristic.py`** | **主力。RESUME.md の再開手順が指すループ** |

現行世代の由来: API モデルをデモンストレータにした行動クローンが「デモ自体が Route101 で stuck」という根本問題を継承したため、会話中の Claude の攻略戦略を直接 Python に書き下ろした(claude_heuristic.py:1-27 docstring)。LLM は `POKE_RL_USE_LLM=1` のときだけ advisor として復帰する(claude_heuristic.py:927-931)。

## 2. ランタイム構成

```
mGBA (手動起動1回, lua script port 8895)
  ↕ 1コマンド=1TCP接続, <|END|> 区切り        io.py MGBAClient
  ├─ screenshot → PNG                          claude_heuristic.take_screenshot
  │    ├─ frame_hash (64x64 MD5)               preprocess.py:84
  │    └─ screen_signals (dialog/menu/battle_menu/front_blocked)
  │                                            screen_features.py (cv2領域判定, sub-ms)
  └─ RAM read → GameState                      state.read_state
       (map/pos/party/flags/badges/NPC座標/battle判定)

per-turn 決定 (run() ループ, claude_heuristic.py:975-1526)
  button_queue 優先順: battle_move_queue > catch_seq_queue > llm_buttons_queue
                       > heuristic_button()    claude_heuristic.py:1198-1248
  ↓ 決定後の後処理フィルタ:
    anomaly_escape (pos_stuck/door_ping/small_circle/... ただし goal-directed 中は無効)
    door_pingpong_break / forward_force        claude_heuristic.py:1332-1458
  ↓ client.tap(button, frames=15) + poll 0.6s
```

## 3. heuristic_button の決定カスケード(上が優先)

`claude_heuristic.py:94-902`。おおよそ次の順で最初にマッチした枝が発火する:

1. **battle_menu(vision)可視**: 低HP wild→RUN / 捕獲トライ / 過剰Lv wild→RUN / デフォルト FIGHT カーソルリセット(`Up,Up,Left,A,A,A`)(:133-191)
2. dialog 可視→A / menu 可視→B / front_blocked→pivot(:192-206)
3. rival goal 用 NPC seek(canon script keyword から座標解決)(:207-263)
4. hidden battle probe(pos 停滞 8-30 turn で A/B サイクル)(:264-274)
5. **mapbfs = 中核**: goal の target_map へ canon 地図で BFS(:275-559)
   - `explore_target` hijack は target_pos 付き goal / dewford 系 directed goal 中は抑制(32 fix, :275-301)
   - target_tiles = goal.target_pos(NPC 占有なら隣接タイルに変換して interact)/ exit_tiles / warp_tiles(:346-393)
   - BFS blocked 合成 = NPC + 経験的封鎖 + permanent + 水タイル + 目的地以外の warp(:394-477)
   - 到着済みで interact_target あり → 顔向け+A で NPC 対話(gym leader 戦トリガ)(:535-552)
6. goal_warp: path_memory の遷移レコードで多段 hop(:560-611)
7. in_battle(RAM): trainer→party_walk 自己同期シーケンス / wild→catch/fight/RUN(UI 信号必須ガード付き)(:612-691)
8. dialog_continue / dialog_frozen(:696-715)
9. escape / path_memory exit / force_explore / **reward_pick**(count-based 探索スコア)/ explore_unvisited / BFS frontier / untried / north_bias / random(:717-902)

## 4. 永続知識ストア(generic_agent/memory/)

| ファイル | 内容 | 書き手 |
|---|---|---|
| tile_map.json | タイル毎 visits / tried / blocked(3回失敗で封鎖)| tile_map.py |
| path_memory.json | map 遷移レコード(from_pos, to_pos, 直前ボタン列)| path_memory.py |
| reward_state.json | 探索スコア状態・checkpoint(Go-Explore lite)・PWhiddy 系 reward 集計 | reward_state.py |
| map_knowledge/<g>-<n>.json | **canon 意味レイヤ**: grass/water/trainer LOS/標高/ledge/warp/NPC | map_knowledge.py |
| map_cache/ | pokeemerald decomp から DL した map.bin / map.json / layouts | map_data.py |
| goal_notes.jsonl, visited_maps.json, peeko_done.marker | goal 用の永続状態 | goals.py |
| savestate_*.ss1 | 定期スナップ(150turn毎)+ 手動チェックポイント | claude_heuristic.py:1492-1505 |
| knn_explorer.npz | フレーム新規性 KNN | knn_explorer.py |

canon(map_data)と経験(tile_map)の役割分担: **canon は「歩けるはず」、経験は「実際に歩けなかった」**。BFS は canon を土台に経験的封鎖で修正する。「ハードコード禁止」ルールとの整合は map_data.py:8-18 の設計根拠コメント参照(座標をコードに埋めず、ランタイムでデータ取得)。

## 5. goal システム(goals.py)

RAM シグナル(badge_count / party_count / event flags / 現在 map+座標)だけで条件判定する宣言的 GOAL_TABLE(goals.py:277-419)。ストーリー進行は Littleroot → … → Rustboro(Stone Badge)→ Peeko 救出 → Dewford 航海 → Brawly の chain。要点:

- `current_goal()` は「target が現在地の goal はスキップして次へ」+ visited-map による後戻り抑制。ただし gym 系・журney 系は `_GOAL_BYPASS_VISITED` で抑制除外(goals.py:76-89, 422-459)
- Route104 は北(Rustboro側)と南浜(Briney)が**同一 map なのに徒歩接続なし**。位置(y座標)を条件に含めて chain を分岐(goals.py:199-213)
- 一度 True になったら二度と False にならない story flag は **disk latch**(`peeko_done.marker`)にする — SaveBlock1 DMA flicker 対策(goals.py:30-48)

## 6. LLM レイヤ(現行はすべて任意/補助)

| モジュール | モデル | 用途 | 起動条件 |
|---|---|---|---|
| llm_advisor.py | (config.MODEL_BRAIN) | stuck 時の button 列提案 | env `POKE_RL_USE_LLM=1` + API key |
| rescue_brain.py | Haiku 4.5 + JSON-strict | auto_loop 系 navigate/rescue | auto_loop 使用時のみ |
| brain.py + loop.py | Opus + tool use | 旧世代フルLLMループ | 手動起動のみ |

## 7. dual_dev(開発自動化, generic_agent/dual_dev/)

Claude Code CLI(architect/reviewer)+ Codex CLI(implementer)+ 決定論的ゲートの半自律開発ループ。詳細は dual_dev/README.md。要点:
- Claude subprocess からは `ANTHROPIC_API_KEY`/`AUTH_TOKEN` を strip → **subscription ログイン経路のみ使用、API credit を誤消費しない**(README:11-13)
- gates.py が RAM write / saveStateLoad / pokemon_env import / path 逸脱 / diff 上限 / py_compile を機械判定(gates.py:12-33, 159-216)
- run 状態は runs/ に永続、usage-limit で自動 pause → `--resume` 継続

## 8. 起動・再開・停止(運用)

- 起動: STARTUP.md(mGBA + lua load のみ手動)。メインループ: `poke-rl/Scripts/python.exe -m generic_agent.claude_heuristic --turns 8000 --poll 0.6`
- 再開: RESUME.md(セッション中断時のスナップショット文書。**このパターンを毎中断時に更新するのが運用ルール**)
- 停止: python 全 kill は禁止(dual_dev 巻き添え)— コマンドラインで絞って kill(RESUME.md 末尾)
- deploy 前チェック: docs/MISTAKE_PREVENTION_CHECKLIST.md(06-29 制定、patch-tower 防止)
- 日次記録: generic_agent/daily_progress/YYYY-MM-DD.md

## 9. 学習トラック(休眠中)

dataset/demonstrations.jsonl に per-turn 記録(claude_heuristic.py:1279-1330)→ train_imitation / brain_cnn.py / env_ppo.py が behavior cloning / PPO 用。現在は heuristic 本体の進行が優先で、学習系は Phase 3 の再開待ち。
