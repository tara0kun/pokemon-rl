# Pokemon RL → VLM Agent プロジェクト (rule = 唯一のルール定義)

**2026-06-08 大転換**: 旧 rule-based RL agent (現在 `old` branch) は凍結。 現プロジェクトは
**Claude Vision (VLM) Agent** (`generic_agent/`) に pivot。

詳細は [generic_agent/daily_progress/2026-06-08.md](generic_agent/daily_progress/2026-06-08.md) 参照。

---

## ディレクトリ構成 (top level)

| path | 用途 |
|------|------|
| `generic_agent/` | **現プロジェクト**。 VLM agent (Claude Opus/Haiku + screenshot + RAM bridge) |
| `ant/` | Anthropic CLI (OAuth 認証用) |
| `poke-rl/` | Python virtualenv |

旧 rule-based project (23,000 行 `pokemon_env.py` 等) は `old` branch に保存 (GitHub `origin/old`)。 `git fetch origin && git checkout old` で参照可能。

---

## 必読 docs (2026-07-06 整備)

- `docs/ARCHITECTURE.md` — 全体構成 (decision cascade / 知識ストア / dual_dev)
- `docs/INVARIANTS.md` — **training/env/nav コードを変更する前に必読**。不変条件を無効化する変更は同じコミットでこのファイルも更新する
- `docs/GOTCHAS.md` — 罠 / `docs/HYPOTHESES.md` — 未解決問題(次にやること) / `docs/DECISIONS.md` — 設計判断録
- テスト: `poke-rl/Scripts/python.exe -m unittest discover -s generic_agent/tests -t .`(mGBA/API 不要・依存ゼロ)。nav / goal / RAM 系を触ったら必ず実行

## 現プロジェクト (generic_agent) の基本方針

### 開発原則
- **ROM 操作のみ**: ボタン (A/B/Up/Down/Left/Right/Start/Select) + screenshot + RAM read
- **RAM 直接書込 禁止**: 人間がプレイ可能な範囲のみ
- **saveStateLoad 禁止**: ストーリー進行をリセットして突破しない
- **ハードコード禁止**: 座標 / map_id 等の game-specific 数値を コードに埋め込まない (prompt は OK)
- **旧 rule-based コード (old branch) は import 禁止**: 完全独立を維持
- **コスト最優先**: API call は cache + rules で 90% 削減目標

### 起動 (user 手動 1 回 / セッション)
詳細は [generic_agent/STARTUP.md](generic_agent/STARTUP.md) 参照。 要約:
1. mGBA 起動 → ROM load (`generic_agent/rom/emerald_en.gba`)
2. Tools → Scripting → Load script (`generic_agent/scripts/mGBASocketServer_generic.lua`)
3. console に「Listening on port 8895」 表示 = ready

### 起動後の autonomous 動作
```bash
poke-rl/Scripts/python.exe -m generic_agent.smoke_test
poke-rl/Scripts/python.exe -m generic_agent.auto_loop --turns 500 --budget 0.5
```

### auto_loop の 3 段 decision flow
1. **FrameCache** (`(frame_hash, map_id)` キー、 永続) → hit なら $0
2. **default_rule_for_state** (dialog continue 等) → $0
3. **LocalRecovery** (画面 frozen 3-8 turn) → B 連打 + random walk、 $0
4. **`call_navigate` / `call_rescue`** (Haiku 4.5 + JPG、 $0.001/call) → cache に書込

### ファイル責務 (generic_agent)
| file | 責務 |
|------|------|
| `auto_loop.py` | main loop。 budget / cache / state 管理 |
| `local_brain.py` | FrameCache、 LocalRecovery、 default rules |
| `rescue_brain.py` | Haiku 4.5 + JSON-strict (navigate / rescue) |
| `brain.py` | Opus 4.8 + tool use (古い高コスト Brain、 残存) |
| `loop.py` | Opus brain 用 main loop (高品質だが高コスト) |
| `preprocess.py` | JPG 変換、 frame_hash (64x64 MD5)、 frames_differ |
| `state.py` | RAM bridge: SaveBlock1 から map/pos read |
| `io.py` | mGBA socket client |
| `memory.py` | notes.jsonl + run_log.jsonl 永続化 |
| `prompts.py` | Opus brain 用 system prompt |
| `tools_schema.py` | Opus brain 用 tool 定義 |
| `manual.py` | C モード helper (Claude が brain 役の手動操作) |
| `smoke_test.py` | 動作確認 |
| `config.py` | path / model / API key auto-load |

---

## モデル選択
- `claude-opus-4-8` (`MODEL_BRAIN` in config.py): 高品質 Brain、 重要判断用
- `claude-haiku-4-5` (`MODEL_RESCUE` in rescue_brain.py): cost-optimized、 navigate / rescue 用
- 価格 (1M tokens、 2026-06):
  - Opus: $5 input / $25 output / $0.5 cache_read / $6.25 cache_write
  - Haiku: $1 input / $5 output / $0.1 cache_read / $1.25 cache_write

## API key 設定
- 環境変数 `ANTHROPIC_API_KEY` を `setx` で永続化
- なければ `config.load_api_key()` が Windows User scope から自動 fallback
- OAuth (`ant auth login`) も認証可能だが **API credit は同じ pool を消費** (subscription quota は使えない)

## コスト管理
- `auto_loop --budget <USD>` で hard cap
- 1 turn 平均 (実測): cache hit $0、 rule $0、 navigate $0.001、 rescue $0.001
- 500 turn 走行で $0.20-0.50 想定

---

## 旧プロジェクト (`old` branch) 取扱い
- **GitHub `origin/old` branch に snapshot 保存** (`git checkout old` で参照)
- generic_agent 側から import / require しない
- 古い lesson は `old` branch の `docs/`、 `memory/` 参照
- 旧 mGBA instance (port 8888/8889/8890) は基本起動しない (旧 lua は `old` branch の `mgba_scripts/`)
- 旧 `exploration_map.json` 等は信用度低 = 参考程度

---

## git 運用 (2026-06-10 改訂)

### branch 戦略
- **`main` = milestone リリース専用**
  - 通常の cycle 作業では触らない
  - 更新タイミング: session 終了時 / major milestone 達成時 (新マップクラスター、 Gym バッジ、 architecture 大変更 等)
  - 更新時は **必ず tag を切る** (`vX.Y-<short-name>`)
- **`dev` = active development**
  - 各 cycle はここで commit + push
  - WIP・実験・bug fix も全て dev
  - 日々の作業はこのブランチのみで完結
- **`feat/<name>` (任意)**
  - 大規模 feature を独立に進める時のみ、 dev から分岐
  - 完成したら dev に merge

### 各 cycle のワークフロー
```
1. dev で実装 + 検証
2. dev で commit + push
3. daily_progress 更新
4. (main には触らない)
```

### main 更新のワークフロー (milestone 時のみ)
```
1. git checkout main
2. git merge dev --no-ff -m "merge dev: <milestone description>"
3. git push origin main
4. git tag -a vX.Y-<short-name> -m "<milestone summary>"
5. git push origin vX.Y-<short-name>
6. git checkout dev に戻る
```

### 既存 tag
- `v0.1-portfolio-7cycles` (2026-06-10): 初回 portfolio milestone。 7 cycle architecture + Oldale Town reached + daily_progress 完備

---

## 日次進捗
新 daily progress: `generic_agent/daily_progress/YYYY-MM-DD.md`
旧 daily progress: `old` branch の `daily_progress/`
