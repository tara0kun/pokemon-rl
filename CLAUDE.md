# Pokemon RL → VLM Agent プロジェクト (rule = 唯一のルール定義)

**2026-06-08 大転換**: 旧 rule-based RL agent (`legacy/`) は凍結。 現プロジェクトは
**Claude Vision (VLM) Agent** (`generic_agent/`) に pivot。

詳細は [generic_agent/daily_progress/2026-06-08.md](generic_agent/daily_progress/2026-06-08.md) 参照。

---

## ディレクトリ構成 (top level)

| path | 用途 |
|------|------|
| `generic_agent/` | **現プロジェクト**。 VLM agent (Claude Opus/Haiku + screenshot + RAM bridge) |
| `legacy/` | 旧プロジェクト (rule-based RL、 23,000 行 `pokemon_env.py` 等)。 凍結、 参照のみ |
| `ant/` | Anthropic CLI (OAuth 認証用) |
| `poke-rl/` | Python virtualenv (両プロジェクト共有) |

---

## 現プロジェクト (generic_agent) の基本方針

### 開発原則
- **ROM 操作のみ**: ボタン (A/B/Up/Down/Left/Right/Start/Select) + screenshot + RAM read
- **RAM 直接書込 禁止**: 人間がプレイ可能な範囲のみ
- **saveStateLoad 禁止**: ストーリー進行をリセットして突破しない
- **ハードコード禁止**: 座標 / map_id 等の game-specific 数値を コードに埋め込まない (prompt は OK)
- **既存 legacy/ コードは import 禁止**: 完全独立を維持
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

## 旧プロジェクト (legacy/) 取扱い
- **読み取り専用 reference として残す**
- generic_agent 側から import / require しない
- 古い lesson は `legacy/docs/`、 `legacy/memory/` 参照
- 旧 mGBA instance (port 8888/8889/8890) は基本起動しない (旧 lua は `legacy/mgba_scripts/`)
- `legacy/exploration_map.json` 等は信用度低 = 参考程度

---

## git 運用
- `main`: 安定 (deploy 可能状態)
- `dev`: 開発中の作業
- feature branch: `feat/<name>` から dev に PR

---

## 日次進捗
新 daily progress: `generic_agent/daily_progress/YYYY-MM-DD.md`
旧 daily progress: `legacy/daily_progress/`
