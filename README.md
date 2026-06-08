# Pokemon VLM Agent

> Claude (Vision Language Model) で Pokemon Emerald を自動プレイする agent。 ROM 切替だけで他 Pokemon シリーズに転用可能な汎用設計。
>
> **2026-06-08**: 旧 rule-based RL agent から VLM agent に pivot ([詳細](generic_agent/daily_progress/2026-06-08.md))。

---

## ディレクトリ

```
c:/pokemon-rl/
├── generic_agent/    # 現プロジェクト (VLM agent)
├── legacy/           # 旧プロジェクト (rule-based RL、 凍結)
├── ant/              # Anthropic CLI
├── poke-rl/          # Python venv (共有)
├── CLAUDE.md         # 全体 rule
└── README.md         # 本ファイル
```

詳細は [CLAUDE.md](CLAUDE.md) と [generic_agent/STARTUP.md](generic_agent/STARTUP.md)。

---

## Quick start

### 1. 環境変数 (1 回限り)
```powershell
setx ANTHROPIC_API_KEY "sk-ant-api03-..."
```
VSCode 再起動で反映。

### 2. mGBA 起動 (セッション 1 回)
1. `Start-Process "C:\Program Files\mGBA\mGBA.exe"`
2. File → Load ROM → `generic_agent/rom/emerald_en.gba`
3. Tools → Scripting → Load script → `generic_agent/scripts/mGBASocketServer_generic.lua`
4. console に「Listening on port 8895」 確認

### 3. 接続テスト
```bash
poke-rl/Scripts/python.exe -m generic_agent.smoke_test
```

### 4. 自動プレイ
```bash
poke-rl/Scripts/python.exe -m generic_agent.auto_loop --turns 500 --budget 0.5
```

---

## アーキテクチャ

### 3-layer Brain
1. **FrameCache** (`local_brain.FrameCache`): `(frame_hash, map_id)` キーで過去 action を保存。 同じ画面再来訪 = $0
2. **Local rules** (`local_brain.default_rule_for_state`): pos unchanged + last=A → dialog continue 等の deterministic rule
3. **LocalRecovery** (`local_brain.LocalRecovery`): B 連打 → A → random walk の state machine
4. **Haiku Brain** (`rescue_brain.call_navigate` / `call_rescue`): JPG + JSON-strict (max_tokens=120)、 $0.001/call

### RAM bridge
SaveBlock1 pointer (`0x03005D8C`) から map_group / map_num / x / y / saveblock1_valid を毎 turn read。

### 期待性能
- 1 turn 平均: $0.0001-0.0005 (90-95% cost cut vs naive Brain-every-turn)
- 500 turn 走行で $0.20-0.50

---

## 旧プロジェクト (legacy/)

23,000 行のルールベース `pokemon_env.py` + PPO バトル AI を凍結状態で保存。 patch-tower 化 (#1-#28) で連鎖崩壊した教訓ベースで VLM agent に pivot。

詳細: [legacy/README_legacy.md](legacy/README_legacy.md)、 [legacy/CLAUDE_legacy.md](legacy/CLAUDE_legacy.md)

---

## license

MIT (legacy/LICENSE 参照、 同様に generic_agent にも適用)
