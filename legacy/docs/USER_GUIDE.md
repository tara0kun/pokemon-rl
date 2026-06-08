# ユーザー操作ガイド — ポケモンRL プロジェクト

このファイルは、**ユーザー（人間側）**がセッション開始・管理するための手順書。

---

## 1. 前提環境

### 必要なソフトウェア
- **mGBA** × 3 (ports 8888/8889/8890)
  - 3 つの mGBA インスタンスを別 directory で起動 (lua socket script を各 port 用に分離するため)
- **Python venv**: プロジェクト root に作成 (例: `poke-rl/`)
- **ROM**: Pokemon Emerald (legal copy 各自で用意、 repo には含まない)

### Luaスクリプト（mGBAソケットサーバー）
- Port 8888: `C:\pokemon-rl\mgba_scripts\mGBASocketServer_1.lua`
- Port 8889: `C:\pokemon-rl\mgba_scripts\mGBASocketServer_2.lua`
- Port 8890: `C:\pokemon-rl\mgba_scripts\mGBASocketServer_3.lua`

---

## 2. mGBA起動手順（ユーザー操作）

### 初回起動 / mGBA再起動が必要な場合

1. **mGBA 3台を起動**（ROMを開いた状態で）
2. **各mGBAでLuaスクリプトをロード:**
   - `Tools → Scripting` を開く
   - `File → Load script` で対応するLuaファイルを選択
   - 1台目: `mGBASocketServer_1.lua` (port 8888)
   - 2台目: `mGBASocketServer_2.lua` (port 8889)
   - 3台目: `mGBASocketServer_3.lua` (port 8890)
3. **ゲームが動作中（Pause解除）であることを確認**
   - Luaスクリプトはゲーム実行中のみソケット応答する
   - 停止中なら `Ctrl+P` または `Emulation → Resume`

### セーブステートのロード（必要な場合のみ）
- Claudeはセーブステートをロードできない（CLAUDE.mdルール）
- ユーザーが手動でロードする場合: `File → Load State → Slot 8`（最新ベストセーブ）
- **PC再起動・mGBAクラッシュ後**: 必ず Slot 8 から手動ロード推奨
  - または: `poke-rl/Scripts/python.exe tools/load_slot8_all.py` (3ポート一括)
- monitor.py は10分毎に最高EXPポートを Slot 8 に自動セーブ
- 通常はロード不要（ゲームは前回の状態を保持）

### ★★ 危険コマンド (送信禁止)
mGBA-http の以下のコマンドは送信しないこと:
- `emu.keyPress` の reset combinations (A+B+Start+Select 同時) → mGBA クラッシュ原因
- `core.reset` → 進捗消失
- `core.loadStateSlot` → CLAUDE.md ルール違反 (コードからのロード禁止)

---

## 3. Claudeセッション開始手順

### ステップ1: Claude Codeを起動
VSCode拡張 または CLI で起動。

### ステップ2: 最初の指示（コピペ用）

以下をそのまま送信：

```
学習を開始してください。CLAUDE.mdとMONITORING_GUIDE.mdを読んで、ルールに従って監視・開発を行ってください。
```

これだけでClaudeは以下を自動実行する：
1. MEMORY.md読み込み
2. CLAUDE.md / MONITORING_GUIDE.md読み込み
3. 学習ログ確認
4. 停止していたら学習再起動
5. `/loop 10m` で定期監視cron設定
6. 汎用AI作業を計画

### 環境変数 (v10.9z255で追加)
- `POKEMON_PASSIVE_SWITCH=1` — Ralts PassiveSwitch 有効化 (flag 実装のみ、本番慎重検証要)
  - 起動例: `POKEMON_PASSIVE_SWITCH=1 poke-rl/Scripts/python.exe -u train.py > training_current.log 2>&1 &`
  - Lv10+ 敵でのみ発火 (現R116 Lv5-8 敵では dormant)
  - 目的: Ralts 参加EXP率を向上 (100%KO→50%参加+Blaziken KO)

### ステップ3: 確認
Claudeが以下を報告するのを確認：
- 学習が動いているか（step数、WIN数）
- 各ポートの状態（R116にいるか等）
- cronが設定されたか

---

## 4. セッション中のユーザー操作

### 基本的に放置でOK
- Claudeが10分ごとに自動監視
- 問題検出時は自動でコード修正＋再起動
- WIN/EXP増加を監視し、停滞時は対処

### ユーザーが介入すべき場面

| 状況 | 対処 |
|------|------|
| **mGBAがフリーズ** | mGBAウィンドウを確認。応答なければ再起動+Luaリロード |
| **Claudeが「mGBA接続できません」** | 上記のmGBA再起動手順を実施 |
| **画面を見てエミュが止まっている** | Claudeに「エミュが止まってる」と伝える |
| **Claudeが同じ報告を繰り返す** | 「ちゃんと見れてる？」と確認を促す |
| **長時間WINが出ない** | 「今の状況見て」と詳細確認を促す |

### 有効な指示の例

```
# 状態確認（monitor.py実行を促す）
今の状況見て
monitor.pyの結果見せて

# 問題を指摘（具体的に）
ずっとポケセンにいるけど
なんでバトルしてないの？
一つのエミュが街でスタックしてる

# ルール遵守チェック
ちゃんとルール通りにやれてる？
ルールに抜けはないですか
スクショ撮って確認して

# 学習再開
学習を再開して

# レベリング方針変更
Raltsを優先してレベリングして

# 汎用AI作業
tile_classifierを学習して
```

### ユーザーが直接確認できること

```
# monitor.pyをユーザー自身で実行
cd C:\pokemon-rl
poke-rl\Scripts\python.exe tools\monitor.py

# スクショを撮ってゲーム画面確認
poke-rl\Scripts\python.exe record_mgba.py all 1 1
# → screenshots/ フォルダにPNG保存

# 学習ログのリアルタイム確認
type training_current.log | more
# または VSCode で training_current.log を開く
```

### Claudeの監視品質を上げるコツ

1. **monitor.pyの結果を要求する** — 「monitor.pyの結果見せて」と言えば各ポートの詳細が出る
2. **エミュ画面と照合する** — 画面で見えている状態とClaudeの報告が一致しているか確認
3. **「問題なし」を鵜呑みにしない** — 特にWIN停滞中は「各ポートの状態詳しく」と要求
4. **具体的に指摘する** — 「ポート8889が街にいる」など具体的に言うとClaudeが見落としに気づく
5. **定期的にルール確認を促す** — 「ルール通りにやれてる？」で自己チェックを促す

---

## 5. トラブルシューティング

### mGBAが応答しない
```
症状: Claudeが「Connection refused」「timed out」を報告
原因: mGBAのLuaスクリプトが停止
対処:
  1. 各mGBAウィンドウで Tools → Scripting を開く
  2. File → Load script で対応するLuaファイルを再ロード
  3. ゲームがPause状態でないか確認（Ctrl+P）
  4. Claudeに「スクリプトリロードした」と伝える
```

### 学習が進まない（WINが出ない）
```
症状: 30分以上WINが0
原因: staleデータ蓄積 / カナズミスタック / healループ
対処:
  - Claudeが自動で再起動するはず
  - しない場合は「学習を再起動して」と指示
  - 改善しない場合は「コードを修正して」と指示
```

### Claudeの監視が形骸化している
```
症状: 毎回同じ「問題なし。安定稼働継続。」ばかり
対処:
  - 「monitor.pyの結果見せて」と具体的に要求
  - 「ちゃんと監視してる？今の状況見れてる？」と質問
  - 「ルールに抜けはないですか」と確認を促す
  - 「各ポートの状態を詳しく教えて」と具体的に要求
  - 「スクショ撮って確認して」でゲーム画面を見させる
  - エミュ画面を見て異常があれば「○○がおかしい」と具体的に伝える
```

### ユーザー側で定期的にやると良いこと
```
1. エミュ画面をたまにチェック（街でスタックしていないか）
2. Claudeの報告が短い（1-2行）時は注意 — 深く見ていない可能性
3. WIN数が長時間変わらない時は「なぜ？」と質問
4. monitor.py を自分で実行して結果を確認（Claudeの報告と照合）
```

### pythonプロセスが増殖してSLOW-STEP
```
症状: step()が2-5秒かかる（通常0.3秒）
原因: 前のtrain.pyプロセスが残存
対処: Claudeに「SLOW-STEPが出てる」と伝える
  → Claudeが余分なプロセスをkillして再起動する
```

---

## 6. ドキュメント一覧

| ファイル | 対象 | 内容 |
|----------|------|------|
| `CLAUDE.md` | Claude | ルール定義（唯一の公式ルール） |
| `MONITORING_GUIDE.md` | Claude | 監視の実践ガイド（失敗パターンと対策） |
| `KNOWN_RISKS.md` | Claude/User | 今後予想される問題と事前対策 |
| `USER_GUIDE.md` | User | このファイル。ユーザー操作手順 |

## 7. プロジェクト構成（参考）

```
C:\pokemon-rl\
├── CLAUDE.md              ← Claudeのルール定義（最重要）
├── MONITORING_GUIDE.md    ← 監視の実践ガイド
├── USER_GUIDE.md          ← このファイル（ユーザー向け）
├── pokemon_env.py         ← メイン環境（~14000行）
├── train.py               ← 学習スクリプト
├── training_current.log   ← 学習ログ（リアルタイム）
├── exploration_map.json   ← 永続マップデータ
├── mgba_scripts/          ← mGBAソケットサーバーLua
├── battle_ai/             ← 汎用バトルAI（開発中）
├── tools/                 ← タイルデータ収集・分類ツール
├── daily_progress/        ← 日次進捗ログ
└── poke-rl/               ← Python仮想環境
```

---

## 7. 現在の攻略状況（参考）

- **Badge**: 1（カナズミジム済み）
- **パーティ**: Lv7(Ralts)/17-19/20-21/14-16/19/47(Blaziken)
- **現在地**: Route 116 レベリング中
- **次の目標**: 全員Lv16到達 → カナシダトンネル → Badge2
- **ベストセーブ**: Slot 8（定期的にClaudeが自動保存）
