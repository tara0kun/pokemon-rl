# 監視・開発オペレーションガイド

このファイルは、Claudeが学習監視・開発を**滞りなくルール通りに**行うための実践ガイド。
CLAUDE.mdのルールを補完し、過去の失敗パターンと対策を記載する。

---

## セッション開始時チェックリスト（必須）

```
1. memory/MEMORY.md を読む（学習状態・ボトルネック把握）
2. CLAUDE.md を読む（ルール確認）
3. training_current.log の末尾確認（学習稼働チェック）
4. 停止していたら再起動
5. /loop 10m で定期監視cron設定
6. 汎用AI作業を1つ計画（tile_collector / tile_classifier / battle_ai等）
```

---

## 定期監視の正しいやり方

### ★ 絶対にやってはいけないこと

| 禁止事項 | なぜ危険か |
|----------|-----------|
| `grep -c` だけで済ませる | 各ポートの状態（map, nav_target, spc）が見えない |
| 「問題なし」を安易に使う | 実際にはスタックしているポートを見逃す |
| 「次回確認」で先送り | CLAUDE.md★★★違反。問題は即対策 |
| PP不変を「heal中」で片付ける | なぜPPが不変か原因を特定すべき |
| tail -10 で済ませる | tail -40〜80 で各ポートの行動を把握する |

### ★ 毎回の監視で確認する項目

```bash
# ★★★ 最優先: 監視ヘルパーを実行（各ポート状態を自動チェック）
poke-rl/Scripts/python.exe tools/monitor.py

# 補足: ログ詳細確認（ヘルパーで問題検出時）
tail -40 training_current.log
```

**monitor.pyが「!! PROBLEM」を表示したら即対策。「ALL PORTS OK」でも目視でログ確認。**

### ★ ルール順守セルフチェック（3回に1回）
3回に1回の監視で、CLAUDE.mdを実際に読み直して以下を確認:
- 「次回確認」「自動解消待ち」で先送りしていないか
- 「他ポートが生産的だから許容」と免罪符を使っていないか
- 問題を検出してデータで確認したか（推測で「大丈夫」としていないか）
- スクショを撮るべき時に撮っているか
- 汎用AI作業を毎セッション実施しているか
- MEMORY/daily_progressを更新しているか

**違反を見つけたら以下の3点を分析し、このガイドの「教訓」セクションに追記:**
1. **何が**: 具体的にどのルールに違反していたか
2. **なぜ**: なぜそうなったか（思い込み？省略？免罪符？形骸化？）
3. **再発防止**: 仕組みで防止する方法（monitor.py改善、チェック追加等）

```bash
# ★ 問題検出時: スクショでゲーム画面を確認
poke-rl/Scripts/python.exe record_mgba.py 8888 1 1  # Port 8888のスクショ
poke-rl/Scripts/python.exe record_mgba.py all 1 1    # 全ポートのスクショ
# → 画面を見ればバトル中/メニュー/フィールドが一目でわかる
```

### ★ 各ポートで確認すべき5項目

| 項目 | 確認方法 | 異常の判断基準 |
|------|----------|----------------|
| **Map** | HBログのmg,mn / MapChangeログ | R116(0,31)以外に長時間滞在 |
| **Position** | pos=(x,y) | 同一位置で100+step |
| **nav_target** | NavGuardログ | 空、または不適切なターゲット(kanazumi_gym等) |
| **spc** | NavGuardのspc= | 100+は要注意、200+は即対策 |
| **PP変化** | HBのPP値を前回と比較 | 3サイクル連続不変→原因調査 |

### ★ 「なぜ」を考えるチェックポイント

数値を見たら必ず以下を自問：
- **WIN=0 なぜ？** → 各ポートのmap確認。R116にいるか？バトル発生しているか？
- **PP不変 なぜ？** → バトルが発生していない？healループ？staleスタック？
- **FA多発 なぜ？** → staleデータ蓄積？バックオフCDが機能しているか？
- **spc高値 なぜ？** → NPC-DETOUR？OscTrap？MegaStuck？

---

## 問題検出→即対策フロー

```
問題検出
  ↓
原因特定（ログを読む、コードを確認）
  ↓
修正方針決定
  ↓
コード修正 → syntax check → 再起動
  ↓
次回監視で効果確認
```

**★★★ 「次回確認」「自動解消待ち」は絶対禁止**
- 問題を見つけたら、その監視サイクル内で対策を実施する
- コード修正が必要なら即修正する
- 再起動で一時解消する場合でも、根本原因を記録し後で修正する

---

## 既知の問題と対策パターン

### 1. staleデータ蓄積（最大ボトルネック）
- **症状**: PP不変、eHP不変、FA連発、WIN停滞
- **原因**: gBattleMonsのバトル後RAMデータ残存
- **現対策**: FA CDバックオフ(v250v) + 30分EXP停滞で再起動
- **判断**: WIN停滞600step以上 → 原因確認、30分 → 再起動

### 2. カナズミNPCスタック
- **症状**: spc高値、NPC-DETOUR連発、(42,33)付近で停滞
- **対策**: KanazumiForceExit(v250o-p3)、R116-Guard調整(v250t)
- **判断**: カナズミ滞在200step以上でForceExit発動確認

### 3. PC heal永久ループ
- **症状**: PP=[40,0,0,0]でneeds_heal=True永続
- **対策**: v250u(PC内) + v250w(屋外) — 未習得技PP=0を除外
- **判断**: HealDbgでneeds=TrueかつHP/faint正常→PP判定バグ

### 4. R116境界バウンス
- **症状**: R116(7,20)↔カナズミ(46,20)を高速往復
- **対策**: R116-Guard西端閾値を6に縮小(v250t)
- **判断**: MapChangeが2-3stepごとに(0,31)↔(0,3)交互

### 5. Route030ループ
- **症状**: カナズミ(30,7-8)↔Route030(30,86)を往復
- **対策**: Loop detector発動するが脱出困難。再起動で位置リセット
- **判断**: MapChange (0,3)↔(0,30)の繰り返し

### 6. GymNavバッジ誤読
- **症状**: Badge1取得済みなのにGymNav発動
- **原因**: _cached_badges RAM読み取り失敗（通信タイミング）
- **対策**: R116到達で自然解消。根本修正は未実施

---

## 再起動判断基準

| 条件 | アクション |
|------|-----------|
| EXP停滞30分 | 即再起動（CLAUDE.mdルール） |
| 全ポートPP不変 + WIN停滞600step | 原因調査 → 再起動検討 |
| SLOW-STEP 5s+ 全ポート連続 | pythonプロセス確認 → 余分プロセスkill |
| mGBA接続タイムアウト連発 | mGBAスクリプティングサーバー確認 |

---

## 汎用AI作業（毎セッション必須）

優先順位:
1. `poke-rl/Scripts/python.exe tools/tile_collector.py all 2.0 5` — タイルデータ収集
2. `poke-rl/Scripts/python.exe tools/tile_classifier.py train --epochs 20 --augment --oversample` — 分類器学習
3. battle_ai/ のテスト・改善
4. visual_env.py / train_visual.py の改善

**バックグラウンド実行**して学習を妨げない。

---

## ベストセーブ手順

6時間ごとにSlot 8にセーブ:
```python
# 最も進んだポートを選択（バッジ数→最大Lv→EXP順）
poke-rl/Scripts/python.exe -c "
import socket
s = socket.socket()
s.settimeout(5)
s.connect(('127.0.0.1', PORT))  # 最適ポート
s.sendall(b'core.saveStateSlot,8,1<|END|>')
# レスポンス確認
"
```

---

## 監視サイクル管理

- 5回に1回（~50分ごと）全体確認を実施
- 全体確認の内容:
  - パーティ全員のLv・技構成
  - レベリング方針の妥当性
  - 汎用AI進捗
  - ストーリー進行状況
  - 攻略チャートとの整合性

---

## 過去の失敗から学んだ教訓

1. **数値だけ見て「正常」と判断しない** — 各ポートのmap/nav_target/spcを必ず確認
2. **同じ問題を何度も「次回確認」しない** — 1回検出したら即対策
3. **応急処置（再起動）で満足しない** — 根本原因を特定し、コード修正で解決
4. **監視が形骸化しない** — テンプレ回答をコピペせず、毎回ログを読んで考える
5. **「問題なし」は全ポート個別確認後にのみ使用** — 1ポートでも異常があれば報告+対策
6. **「他ポートが生産的だから許容」は禁止** — 1ポートでもスタックしていたら即対策。生産性を免罪符にしない
7. **同じスタック位置が2回出たら構造的問題** — コード修正やmap直接編集で根本解決。「学習中」「自然解消見込み」は先送りの言い訳
8. **マップデータを確認する** — スタック時はexploration_map.jsonのwall状態を調べる。「壁なし=BFS通過可能」なのに通れない→NPC壁が未記録→即記録
9. **★ 問題発生時は「なぜ」を3回掘る** — 症状(BFS不通)→原因(wallが多い)→根本原因(DeadZoneEscapeが破壊)。表面修正を繰り返さず、データを定量的に確認して根本に到達する。特に**自分のコードが原因の可能性を最初に疑う**
10. **★ マッピングデータの健全性チェック** — 問題時にまずwall数・接続数・到達可能タイル数を確認。異常値（カナズミwall=945、confirmed=0）があれば即座に根本原因を特定。「マップにギャップがある」で終わらず「なぜギャップができたか」まで掘る
11. **★★ 監視 cron 生存確認** (2026-05-22 追加) — session 開始時 + 1h 沈黙時に `CronList` で /loop cron が active か確認。 消失していれば即 `CronCreate` (durable=true) 再作成。 session-only cron は session 過程で消失することがある = 監視機能停止 = 全ルール違反の連鎖元。 user 「定期監視を行えていないように見えていますが」 指摘で発覚 ([[feedback_cron_verify]] 参照)
12. **★★★ 「観察」連発禁止** (2026-05-19 追加) — task-notification 連投に「観察」一語返答を 5 件以上連発したら強制深掘り cycle (monitor.py 12 checklist 全消化 + 自 patch 副作用 tail 確認)。 user 「景色変わらず」 指摘で 100+ event の形骸化発覚。 「同 stuck 5 cycle 以上 = 即 root cause re-investigation」 ([[feedback_no_observation_spam]] 参照)
13. **★★★ canonical map data inject** (2026-05-21 追加) — 新 phase 入りで pokeemerald `data/layouts/<MapName>/map.bin` から passable/wall 抽出 → exploration_map.json に注入。 16-bit block bits 10-11 で collision 判定 (0=passable, 1=wall)。 「random walk で時間かければ突破」 は false hope (Brawly Gym で 2 日進展ゼロ実証)。 `tools/canon_map_inject.py` 汎用化済 ([[feedback_mapping_protocol]] 参照)
14. **★★★ rule audit 抜け穴防止** (2026-05-22 追加 violation #16) — feedback memory 41 件中 半数未確認、 6h ベストセーブ check ゼロ、 5 回 1 回全体確認ゼロ、 UNRESOLVED 14 日 stale 等の audit gap を user 指摘で確認。 対策: session 開始時 + 3 cycle 毎に CLAUDE.md 全文 + memory/feedback_*.md 全件 read + 12 checklist 自問。 「読んだ気」 禁止
