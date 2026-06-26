# Pokemon VLM Agent

> **Cost-optimized Vision Language Model agent that plays Pokemon Emerald.**
> Claude reads the screen, calls a tool, presses one button — and a frame cache + local rule engine keeps the cost down to roughly $0.0004 per turn.
>
> *Portfolio project by [tara0kun](https://github.com/tara0kun) — IPUT Tokyo, IoT Systems Course, 2028 graduate (28卒).*

---

## なぜ作ったか — 問題意識

Pokemon Emerald (GBA) を AI に「画面を見ながら」 プレイさせたい。 ただしクリア機 — 単に動作するだけでなく:

1. **汎用性**: 1 タイトル専用の bot ではなく、 ROM を差し替えるだけで他 Pokemon 作品 (FireRed / Crystal 等) でも動く設計。
2. **コスト効率**: VLM (Claude 等) を毎 turn 呼ぶと 1 時間 数万円規模で破綻する。 90%+ を cache / rule で処理し、 Brain LLM call を最小化。
3. **学習可能性 (portfolio として)**: 5 層分解 architecture を実装することで、 画面認識・マップ構築・経路探索・戦闘・高レベル判断 を別々に評価可能にする。

---

## プロジェクトの 2 つの フェーズ

このリポジトリは **2 つの異なるアプローチ** を持っており、 git branch で分離管理しています。

| branch | 内容 | 状態 |
|--------|------|------|
| `main` / `dev` | Phase 2: VLM agent | **active** |
| **`old`** | **Phase 1: rule-based RL agent** | **凍結 (snapshot 保存)** |

### Phase 1 (2026-02〜06-07): rule-based RL agent — **凍結**

GitHub の [`old` branch](https://github.com/tara0kun/pokemon-rl/tree/old) に snapshot 保存。 ローカルで参照する場合:

```bash
git fetch origin
git checkout old
# ここで pokemon_env.py、 train.py 等 23,000 行の RL コードが root に展開される
```

**規模と技術**:
- `pokemon_env.py` 約 23,000 行のカスタム Gymnasium 環境
- BFS pathfinding + Stable-Baselines3 PPO で戦闘 (`battle_ai/`)
- mGBA × 3 並列インスタンス、 自前 lua + Python socket bridge
- pokeemerald decomp を直接 parse して canonical map data を BFS graph に注入

**達成度**: バッジ 2 取得、 Brawly Gym coverage 30%。 ただし以下の構造的問題で頭打ち。

### Phase 2 (2026-06-08〜): VLM agent — **現プロジェクト** ([generic_agent/](generic_agent/))
- Claude Opus 4.8 / Haiku 4.5 が screenshot を見て次の 1 ボタンを decide
- ROM 知識 はゼロ (system prompt の Pokemon 一般知識のみ)
- 3-layer Brain で 90%+ 無料判定 + Brain は新画面のみ
- frame_hash + map_id の永続 cache でセッション跨ぎの reuse

---

## なぜ Phase 1 → Phase 2 に pivot したか

旧 rule-based RL は技術的には精緻 (23,000 行 + decomp parser + PPO) だったが、 4 つの構造的問題で **将来性なし** と判断:

### 問題 1: patch-tower 連鎖崩壊
新エリア攻略のたびに `pokemon_env.py` にハードコード patch (#1〜#28、 2 週間で 28 patch) を追加。 1 patch が別の場所で副作用 → さらに patch → 連鎖。 **2 週間で 1 patch が「動く」 → 翌週には別の何かが壊れる** の繰り返し。 部分修正ではもう収束しない状態に。

### 問題 2: 「汎用 AI」 と謳いつつ Emerald 専用 bot
「他 Pokemon 作品にも転用可能」 と当初の portfolio で書いたが、 実態は:
- 座標 `(11, 10)` のように ROM 固有の数値が散在
- map_id `(24, 7)` 等の Emerald 固有テーブルを前提
- pokeemerald decomp のメモリアドレス (0x02022FEC 等) を直書き
→ FireRed や Crystal で 1 行も動かない。 **「汎用」 を名乗れない実態**。

### 問題 3: AI と呼べない
- LLM や ML model は補助的 (タイル分類 CNN が val_acc 35.7% で停滞中)
- 実際の意思決定は **23,000 行の if-else が全部やっている**
- 「これは AI ではなく人間が書いた攻略 script」 が面接でバレるリスク

### 問題 4: コスト効率の概念がない
- 並列 3 instance + tensorboard + RL training で電気代が嵩むだけ
- 「1 turn いくら」 という cost monitoring 不在
- portfolio として「効率的なシステム設計」 を語れない

---

### なぜ VLM agent (Phase 2) は解決するか

| 旧 Phase 1 の問題 | Phase 2 の解決策 |
|-----------------|------------------|
| patch-tower 連鎖崩壊 | ハードコード一切なし。 prompt + cache + RAM bridge で完結 |
| Emerald 専用 | ROM 切替で他作品も動作 (将来 FireRed / Crystal で検証予定) |
| AI と呼べない | Claude Opus 4.8 が screenshot を見て decide = 本物の VLM agent |
| コスト不明 | 1 turn $0.0004 平均、 cache hit 89% でほぼ無料、 budget cap で hard limit |

**Anthropic 公式の Claude Plays Pokemon (Red を 2025 年にクリア) と同等 architecture を、 cost-optimized で再現** という portfolio narrative が成立。

完全な pivot 経緯と意思決定の詳細: [generic_agent/daily_progress/2026-06-08.md](generic_agent/daily_progress/2026-06-08.md)

---

## ゴール (3 段階)

| 段階 | 達成基準 | 状態 (2026-06-26) |
|------|---------|----------------------|
| **Goal 1: 序盤完了** | starter 受領 + Route 102 到達 | ✅ **達成**: starter 取得 + 30+ unique map 訪問 |
| **Goal 2: Gym 1 (ツツジ)** | Rustboro City で Roxanne 撃破 + バッジ 1 | ⏳ **autonomous で Gym 内部到達**: 19 fix + 真の root cause 11 段解明、 Roxanne 戦自体は LOTAD Lv 不足で whiteout 繰り返し、 grass area grinding strategy 試行中 |
| **Goal 3: ポケモンリーグ殿堂入り** | 8 バッジ + チャンピオン撃破 | 🎯 最終目標 |
| **Stretch: 汎用化** | FireRed / Crystal で同じ codebase 動作確認 | 🎯 portfolio 主軸 |

### 2026-06-22 → 06-26 milestone (5 day autonomous run)
- **19 fix + 2 tool + 30+ daily_progress** 全 git push
- **真の root cause 11 段** 解明・治療: RAM false-negative, cursor reset, party walk, screen-UI gate, goal logic, warp door, in_battle stale flag, dialog frozen, poll timing, empirical exit/blocked tile, BFS reachability
- **autonomous nav**: bridge 14 hour chronic stuck 突破 → Rustboro 越境 → Gym 内部到達
- **mGBA crash auto-recovery tool** (uiautomation 経由) で 24/7 robust 化
- **tile_map empirical accumulation**: canon-game walkable mismatch を自動学習する仕組み実証 (Route 104 10 4-way blocked、 Rustboro 5 蓄積)

### Goal 2 残課題
- LOTAD Lv 10+ grinding (BUBBLE 2x rock で Geodude 一撃可能)
- (10,30) Petalburg Woods warp 経由で Route 104 south grass area 到達
- Roxanne 再戦 + Stone Badge 取得

---

## Daily progress timeline (簡潔履歴)

各日の主成果と milestone。 詳細は [generic_agent/daily_progress/](generic_agent/daily_progress/) 参照。

| 日付 | 何をしたか | 主成果 |
|---|---|---|
| **2026-06-08** | rule-based RL → VLM agent 大転換、 新 architecture (auto_loop + 3-layer Brain + RAM bridge + cache) v6 実装 | 旧 23,000 行コード `old` branch に凍結 |
| **2026-06-09** | Brain navigate 完成、 Birch event 突破、 portfolio README v1 | 序盤 cutscene 通過自動化 |
| **2026-06-10** | cycle 9-13: explorer 改善 + ban list で coverage 拡大 | unique pos 100+ 達成 |
| **2026-06-11** | cycle 14-20: path_memory + dialog handling 改善、 v34 で score 279 | wild battle 発生 area (Route 102) 到達、 whiteout 確認 |
| **2026-06-12** | cycle 21-24: 位置認識 path_memory (Option X) deploy、 v46 で stuck 1408→4 turn 劇減 | mechanical stuck 解消 |
| **2026-06-15** | Hybrid LLM-in-loop architecture + PWhiddy v2 reward + novelty exploration | **STARTER autonomous 取得**、 milestone |
| **2026-06-18** | Rival waypoint インフラ (`find_npc_by_script_keyword`)、 heur_rc=-1 timeout 4 修正 | iter 30+ 連続 heur_rc=0 安定走行 |
| **2026-06-19** | anomaly_escape override bug 修正、 score 83 plateau 30+ iter 連続 解消 | mapbfs / rival_seek が anomaly_escape に上書きされる critical bug fix |
| **2026-06-19 PM** | catch infrastructure 完成 (Balls pocket + SaveBlock2 XOR decode)、 wild_catch_try_screen 早期 path | iter 394 で 800 demo 安定収集 |
| **2026-06-22** | Wally event 突破、 5 day chronic stuck of Petalburg 終焉 | **Route 104 + Petalburg Woods 突破 → Rustboro 直前** |
| **2026-06-23** | Gym 内 Marc 撃破 + LOS 回避 (Josh/Tommy x=1/x=2 column) | **🏆 Roxanne battle 開始**、 Stone Badge 寸前 |
| **2026-06-24** | 6 day RAM false-negative bug 発見・治療 (30ed435 BATTLE_FLAGS 0x02022FEC)、 4 fix deploy | RAM/cursor/screen/goal 4 root cause 治療、 ただし実 game 進捗ゼロ |
| **2026-06-25** | 14 hour autonomous + savestate restore で Roxanne 再戦、 13 fix → 16 fix 累計 deploy、 mGBA auto-recovery tool | **真の root cause 7 段解明**、 bridge chronic stuck 完全治療、 Gym 内部到達 |
| **2026-06-26** | 19 fix (target_pos + grind goal) + 20 fix (permanent_blocked Twin LOS/Haley)、 main branch protection、 PR #1 open、 repo 整頓 (tools 17→10) | autonomous nav 完成検証、 ただし Roxanne 撃破は LOTAD grinding 必要 |

---

## アーキテクチャ

### 概観

```
            ┌───────────────────────────────────────────┐
            │  mGBA (Pokemon Emerald [USA] ROM)         │
            │   ↑ button send       ↓ screenshot + RAM  │
            └────────────┬──────────────────────────────┘
                         │  socket (port 8895, lua server)
                         ▼
┌──────────────────────────────────────────────────────────┐
│ generic_agent/auto_loop.py  ←  main control loop          │
│                                                           │
│  per turn:                                                │
│   1. take screenshot, hash it (64x64 MD5)                 │
│   2. read SaveBlock1 → map_group, map_num, x, y           │
│      + probe gBattleTypeFlags → in_battle, battle_flags   │
│   3. layered decision (first match wins):                 │
│                                                           │
│     ┌─── A. FrameCache hit  →  cached button ($0)         │
│     │      key = (frame_hash, map_id)                     │
│     │      invalidated when same action fails 5x          │
│     │                                                     │
│     ├─── B. Local rule  →  e.g. dialog_continue ($0)      │
│     │      pos unchanged + last=A → press A again         │
│     │                                                     │
│     ├─── C. LocalRecovery state machine  ($0)             │
│     │      A×4 → B×3 → Down×2 → non-Up random×3           │
│     │      (escapes NPC dialog loops)                     │
│     │                                                     │
│     ├─── D. Brain LLM "navigate" (Opus 4.8)               │
│     │      JPG-encoded image (max edge 480, q70)          │
│     │      + state summary + STUCK / STALLED warnings     │
│     │      + last 8 action history                        │
│     │      + tile_map summary (tiles seen, blocked_here,  │
│     │        unexplored_nearby frontier)                  │
│     │      → JSON {button, reason}                        │
│     │      result is cached for future hits               │
│     │                                                     │
│     └─── E. Brain LLM "rescue" (Opus 4.8)                 │
│            fires when same screen 8+ turns                │
│                                                           │
│   side-channel:                                           │
│     • TileMap.record_visit / record_attempt every turn    │
│     • map_stuck_flush every 800 turns on same map         │
│     • stalled detector (<=3 unique tiles in 100 turns)    │
│       → 1st: TileMap.bfs_frontier_direction (free, exact) │
│       → 2nd: force LocalRecovery, $0 deterministic walk   │
│     • battle short-circuit: in_battle → press A ($0)      │
│       (battle UI ignored by tile_map / BFS planner)       │
│                                                           │
│   4. send button to mGBA                                  │
│   5. log to memory/run_log.jsonl                          │
└──────────────────────────────────────────────────────────┘
```

### 主要ファイル ([generic_agent/](generic_agent/))

| ファイル | 責務 |
|---------|------|
| `auto_loop.py` | メインループ、 budget / state 管理 |
| `local_brain.py` | FrameCache、 LocalRecovery state machine、 rule 判定、 map-stuck flush |
| `tile_map.py` | 永続 tile-level collision map (`(map_id, x, y) → {visits, tried, blocked}`)。 frontier 計算 + Brain summary 生成 + BFS frontier finder (`prefer='nearest'` / `'farthest'`、 default farthest で既知エリアの edge を狙う) |
| `story_state.py` | map 訪問履歴から story flags (mom_done, lab_visited, starter_received, oldale_reached 等) を推定。 `hint_for()` が Brain navigate prompt 先頭に「[GOAL] ...」 を 1 行で注入 |
| `rescue_brain.py` | Anthropic API 呼出し、 Opus 4.8 + JSON strict output、 navigate / rescue prompt、 tile_map summary 注入 |
| `preprocess.py` | JPG 変換、 frame_hash、 frames_differ |
| `state.py` | SaveBlock1 pointer 経由で map / pos を RAM read + battle 検出 (gBattleTypeFlags 候補アドレス probing) |
| `io.py` | mGBA socket protocol (`<\|END\|>` terminator) |
| `memory.py` | notes.jsonl + run_log.jsonl 永続化 |
| `manual.py` | デバッグ用手動 1-shot 操作 (ボタン送信 + state snapshot) |
| `smoke_test.py` | 接続 / 画面 / RAM の動作確認スクリプト |
| `config.py` | path / model / API key auto-load |

---

## 実測パフォーマンス (2026-06-08〜09 セッション)

### 累積 cost と進捗

| run | model | turn | コスト | 主な breakthrough |
|-----|-------|------|--------|-------------------|
| v6 | 無 (no API) | 500 | $0 | LocalRecovery で NPC dialog 脱出、 22 位置探索 |
| v7 | Haiku 4.5 | 1000 | $0.07 | Birch Lab 入場、 cache poisoning 検出 → invalidation 実装 |
| v8 | Haiku 4.5 | 1500 | $0.49 | Pokemon 知識を prompt に注入、 23 位置 |
| v9 | Haiku 4.5 | 2000 | $0.55 | story-aware prompt、 47 位置探索 |
| v10 | Opus 4.8 | 2000 | $1.51 | Vision 誤読 改善、 ただし NE 角で stuck |
| **v11** | Opus 4.8 | 2000 | $0.53 | **Route 101 cutscene 突破 → Birch 救援 → starter 選択** |
| v11c-e | Opus 4.8 | 6000 | $2.66 | starter 正式受領、 **11 unique map** 探索 |
| **合計** | - | **15,000** | **~$5.81** | starter + 序盤完了 |

### コストプロファイル
- **平均 $0.0004 / turn** (89% cache hit 時)
- Opus 4.8 navigate 1 call: ~$0.005
- cache hit / local rule / recovery: **$0**
- 500 turn 走行で $0.2 - 1.0 (cache 蓄積による)

### 達成済 milestone
- ✅ Mom dialog + 時計設定 (2F bedroom)
- ✅ Birch Lab アシスタント会話
- ✅ Rival (May) 初対面 dialog
- ✅ Route 101 北口 NPC 突破 → cutscene 起動
- ✅ Birch + Poochyena 救援 cutscene 完走
- ✅ Starter 選択 + Poochyena 戦闘勝利
- ✅ Birch Lab で正式に starter + ポケモン図鑑 受領
- ✅ **Oldale Town (map 3,0) 到達** (cycle 6 で story hint 注入後)
- ✅ 11+ unique map 訪問

### 反復改善サイクル (2026-06-09、 7 cycle)

「分析 → 改善 → 検証 → daily_progress + git push」 を 1 セッション内で 7 回実施:

| cycle | 改善 | 結果 (positions / maps / cost) |
|-------|------|--------------------------------|
| 0 (baseline v12) | - | 40 / 1 / $0.87 |
| 1 | cache.flush_map + recovery 改善 | 11 / 1 / $0.78 |
| 2 | tile_map (collision tracking) | 35 / 3 / $1.50 |
| 3 | BFS frontier finder (nearest) | 68 / 3 / $1.50 |
| 4 | battle RAM bridge | 76 / 3 / $1.50 |
| 5 | BFS farthest preference | 88 / 3 / $1.50 |
| **6** | **story_state goal hint** | **95 / 6 / $1.50** ★ Oldale 到達 |
| 7 | new_map_grace + STAY hint | 23 / 2 / $1.50 (variance) |

cycle 1 baseline 比で **positions 2.4×、 maps 6×** を同 cost で達成。 cycle 7 で Brain variance 問題 (cache 学習されない path の再現性) を honest documentation。

---

## 2026-06-15 更新: Hybrid LLM-in-loop + 真の自律 self-check

詳細は [generic_agent/daily_progress/2026-06-15.md](generic_agent/daily_progress/2026-06-15.md) 参照。

### 主な前進
- **starter Pokemon 取得を完全自律で達成** (5 時間 manual + Opus 試行で失敗 → hybrid アーキテクチャ実装 1.5 時間で達成)
- party_count: 0 → **1 (Lv5)**
- event flags: 180 → **193**
- curriculum milestones: 4 → **8** (Route 101, May 家, lab 内部 等含む)
- demos: 115K → **179K**
- BC val best_score: 72 → **225**

### アーキテクチャの主な変更
| component | 内容 |
|-----------|------|
| `reward_state.py` | PWhiddy v2 port (event×4、 heal×10、 badge×10、 stuck escalation) + dense intermediate signals |
| `brain_cnn.py` | STATE_DIM 28 → 2,464 (全 2,400 event bit + HP/lvl/badges/recent_actions/opp_lvl) |
| `state.py` | gObjectEvents 読込 (16 NPC sprites)、 wall vs NPC blocker 区別 |
| `llm_advisor.py` + `pokemon_emerald_knowledge.py` | Haiku 4.5 を dialog/menu/stuck で sparse 召喚、 3,687 chars の Emerald 知識 prompt |
| `path_memory.py` | `find_path_to_map` (multi-hop BFS)、 `find_nearest_unexplored_map` (novelty drive) |
| `goals.py` | 6 段階階層 goal、 `get_starter` の target_map バグ修正 ((1,0) → (1,4) Birch's lab) |
| `tools/autonomous_monitor.py` | 60 秒毎 tick + 30 分毎 `self_check_analyze` (8 iter scores + milestones 分析) |
| `tools/train_imitation.py` + `train_ppo.py` | CPU → **RTX 5060 cu128 GPU** (epoch 13× 高速化)、 SB3 PPO env (30K timesteps 検証完了) |

### 設計の honest 限界
- PPO 30K は学習指標は positive (explained_variance 0.11) だが story trigger 学習には数百万 steps 必要 (PWhiddy 並み)
- Champion クリアは現スタックでは届かない見込み (PWhiddy v2 も 3 badge で頭打ち)
- BC val score の variance は大 (max_button_frac=1.0 残存)

---

## 技術的な学び

### 主要 bug & fix
1. **cache poisoning**: 同じ frame_hash で誤った action が cache されると無限ループ → `useless_cache_streak >= 5` で `cache.forget()` + LocalRecovery エスカレーション
2. **state machine 中断 bug**: hash 変化 で `in_recovery=False` 強制 reset していた → dialog text scroll で recovery が永遠 step 0 → 削除して修正、 recovery は `pos_changed` か `exhausted` のみで reset
3. **rescue prompt 暴走**: long-stuck rescue が毎 turn 発火 → note 累積で token 爆発 → `rescue_fired_for` で 1 episode 1 回 only
4. **RAM read empty string**: lua の transient state で空文字 → `int("")` crash → `_parse_int` helper で robust 化

### 設計判断
- **JP ROM ではなく EN ROM**: Vision (Opus) は日本語 dialog の文脈理解が弱い → 英語 ROM で 3 マップ認識 + メモ保存が 一気に動いた
- **Opus vs Haiku ハイブリッド**: Haiku 4.5 は cost-efficient だが Vision で誤読 → Opus 4.8 を default、 Haiku は将来 fallback 用
- **永続 cache の威力**: `(frame_hash, map_id) → action` を JSON file に保存することで、 同じ画面の再来訪コストが完全に $0
- **prompt に story 知識を埋め込む**: Pokemon Emerald の地理 (Littleroot → Route 101 → Petalburg → Rustboro) を system prompt に書いておくと、 「north に出口」 判定の精度が劇的に上がる

---

## 起動方法

### 1. 環境変数 (1 回限り)
```powershell
setx ANTHROPIC_API_KEY "sk-ant-api03-..."
```
VSCode / PowerShell を再起動して反映。

### 2. mGBA 起動 (各セッション 1 回、 手動)
mGBA CLI には起動時 lua auto-load が無いため、 ROM ロードと script ロードだけは GUI で。

1. `Start-Process "C:\Program Files\mGBA\mGBA.exe"`
2. File → Load ROM → `generic_agent/rom/emerald_en.gba`
3. Tools → Scripting → Load script → `generic_agent/scripts/mGBASocketServer_generic.lua`
4. console に `Listening on port 8895` が出れば ready

詳細: [generic_agent/STARTUP.md](generic_agent/STARTUP.md)

### 3. 接続テスト
```bash
poke-rl/Scripts/python.exe -m generic_agent.smoke_test
```

期待出力:
```
[OK] port 8895 reachable
[OK] game title: POKEMON EMER
[OK] game code:  AGB-BPEE
[OK] state: map=(0,0) pos=(0,0)
[ALL OK]
```

### 4. 自動プレイ
```bash
poke-rl/Scripts/python.exe -m generic_agent.auto_loop \
    --turns 2000 \
    --budget 2.0
```

`--budget` は USD の hard cap。 達したら自動停止。

---

## tech stack

- **Vision Model**: Claude Opus 4.8 (primary), Haiku 4.5 (fallback)
- **API**: Anthropic Python SDK (`anthropic` v0.84+)、 OAuth 認証も対応 (`ant auth login`)
- **Emulator**: mGBA 0.10.5 + 自前 lua socket server (port 8895)
- **Language**: Python 3.13 (type hints + dataclass)
- **画像処理**: OpenCV (`cv2`) + numpy で JPG 変換 + frame_hash
- **永続化**: JSON / JSONL (cache、 notes、 run log)
- **dev env**: Windows 11 + VSCode (Claude Code 拡張)

### 依存
```
anthropic    # Claude API
opencv-python  # 画像処理
numpy        # 配列
```

---

## ディレクトリ

```
c:/pokemon-rl/
├── generic_agent/          # 現プロジェクト (VLM agent)
│   ├── auto_loop.py        # メイン loop
│   ├── local_brain.py      # FrameCache + Rules + Recovery
│   ├── rescue_brain.py     # Claude API caller
│   ├── preprocess.py       # 画像処理
│   ├── state.py            # RAM bridge
│   ├── io.py               # mGBA socket
│   ├── memory.py           # 永続ログ
│   ├── prompts.py          # system prompt
│   ├── manual.py           # デバッグ用手動操作
│   ├── smoke_test.py       # 接続確認
│   ├── config.py           # 設定 + API key load
│   ├── rom/                # ROM file (gitignore)
│   ├── scripts/            # lua socket server
│   ├── memory/             # notes.jsonl, run_log.jsonl, frame_cache.json
│   ├── logs/               # screenshots, run logs (gitignore)
│   ├── daily_progress/     # 日次の進捗 + 学び
│   └── STARTUP.md          # user 向け起動手順
├── ant/                    # Anthropic CLI
├── poke-rl/                # Python venv (gitignore)
├── CLAUDE.md               # 全体 rule
└── README.md               # 本ファイル
```

### Phase 1 (rule-based RL) を見る
旧 `pokemon_env.py` (23,000 行)、 `train.py` (SB3 PPO)、 過去 docs / memory / daily_progress 等は GitHub の `old` branch に snapshot 保存。

```bash
git fetch origin
git checkout old
```

---

## ロードマップ

### 短期 (1-2 week)
- [ ] Goal 2: Gym 1 (Rustboro Gym、 ツツジ、 岩 type) 撃破
- [ ] 戦闘専用 RAM bridge (相手 Lv / 技 / type)
- [ ] menu 自動化 (バッグ、 パーティ、 ポケモン交代)
- [ ] cost monitoring dashboard

### 中期 (1-2 month)
- [ ] Goal 3: 全 8 バッジ + ポケモンリーグ
- [ ] 戦闘特化 prompt (type 相性、 効果抜群)
- [ ] cache の自動 prune (古い entry の削除)
- [ ] FireRed / Crystal で同じ codebase テスト

### 長期 / stretch
- [ ] 完全 autonomous: mGBA も Python で起動 (現状 user 手動 1 回)
- [ ] 多 instance 並列でデータ収集
- [ ] portfolio web デモ (走行ログをブラウザで再生)

---

## license

MIT — see [LICENSE](LICENSE).

レガシーコードも MIT。 ROM ファイル (`*.gba`) は著作物のため git に含めない (`.gitignore`)。

---

## 参考

- pokeemerald decomp: <https://github.com/pret/pokeemerald>
- mGBA: <https://github.com/mgba-emu/mgba>
- mGBA-http (lua reference): <https://github.com/nikouu/mGBA-http>
- Anthropic Plays Pokemon (公式 Red クリア事例): Twitch 配信、 2025

---

## author

[tara0kun](https://github.com/tara0kun) — IPUT Tokyo IoT Systems Course
[tiitara0178@gmail.com](mailto:tiitara0178@gmail.com)

Personal IoT project: [iot-life-support](https://github.com/tara0kun/iot-life-support)
