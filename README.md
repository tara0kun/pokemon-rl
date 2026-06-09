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

### Phase 1 (〜2026-06-07): rule-based RL agent — **凍結** ([legacy/](legacy/))
- `pokemon_env.py` 約 23,000 行のカスタム Gymnasium 環境
- BFS pathfinding + Stable-Baselines3 PPO で戦闘
- mGBA × 3 並列インスタンス、 自前 lua + Python socket bridge
- pokeemerald decomp を直接 parse して canonical map data を BFS graph に注入
- **問題**: patches #1〜#28 で patch-tower 化、 場当たり修正の連鎖、 ROM 切替不可
- **教訓**: 「ハードコードした攻略 script」 は AI ではない、 汎用性ゼロ

### Phase 2 (2026-06-08〜): VLM agent — **本プロジェクト** ([generic_agent/](generic_agent/))
- Claude Opus 4.8 / Haiku 4.5 が screenshot を見て次の 1 ボタンを decide
- ROM 知識 はゼロ (system prompt の Pokemon 一般知識のみ)
- 3-layer Brain で 90%+ 無料判定 + Brain は新画面のみ
- frame_hash + map_id の永続 cache でセッション跨ぎの reuse

なぜ pivot したか の全容: [generic_agent/daily_progress/2026-06-08.md](generic_agent/daily_progress/2026-06-08.md)

---

## ゴール (3 段階)

| 段階 | 達成基準 | 状態 (2026-06-09 朝) |
|------|---------|----------------------|
| **Goal 1: 序盤完了** | starter 受領 + Route 102 到達 | ✅ **達成**: starter 取得 + 11 unique map 訪問 |
| **Goal 2: Gym 1 (ツツジ)** | Rustboro City で Roxanne 撃破 + バッジ 1 | ⏳ 未達 (推定 +$3-5 API 費用) |
| **Goal 3: ポケモンリーグ殿堂入り** | 8 バッジ + チャンピオン撃破 | 🎯 最終目標 |
| **Stretch: 汎用化** | FireRed / Crystal で同じ codebase 動作確認 | 🎯 portfolio 主軸 |

### Goal 2 までの to-do
- Route 102 → Petalburg City → Petalburg Woods → Route 104 → Rustboro City → Gym 戦
- 主な技術課題: 戦闘 RAM bridge (相手 Lv / 技 / type)、 menu 自動化、 マルチ戦闘の継続判断

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
- ✅ 11 unique map 訪問 (Littleroot / Birch Lab / Player house 1F / Route 101 / 短時間 (3,0) (6,0) (0,4))

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
├── legacy/                 # Phase 1 の rule-based RL (凍結)
│   ├── pokemon_env.py      # 23,000 行
│   ├── train.py            # SB3 PPO 訓練
│   ├── docs/               # 過去 docs
│   ├── memory/             # 過去 memory
│   ├── daily_progress/     # 過去 5/29 までの日次
│   └── README_legacy.md    # 旧 README
├── ant/                    # Anthropic CLI
├── poke-rl/                # Python venv (gitignore)
├── CLAUDE.md               # 全体 rule
└── README.md               # 本ファイル
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
