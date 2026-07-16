# 再開ガイド (RESUME) — 更新: 2026-07-16

セッション再開手順。git は `dev` == `origin/dev`、working tree クリーン。

---

## 現在地スナップショット (2026-07-16)

### ゲーム (Pokemon Emerald)
- **Badge 3 (Dynamo Badge) 保持。Route111。Grovyle L35。次の目標 = Badge 4 (Flannery @ Lavaridge)**
- パーティ: slot0 Grovyle L35(主力) / slot1-3 Poochyena L10,L9,L3 / slot4 Lotad L10 (弱小 dead weight)
- **現 blocker = Route111 の Rock Smash 岩** ((19,100)/(18,101))。HM06 未取得。
  実装済みチェーン: `get_rock_smash`(Mauville House1(10,2) の RockSmashDude(4,4)) → `teach_rock_smash`(hm_teach.py = Haiku VLM が bag/party UI を操作し **Poochyena slot1** に教える) → `smash_route111_rock`((19,100) を interact)
- その先の全体計画は **`docs/PLAN_lavaridge_flannery.md`**(canon 検証済み: Meteor Falls → Mt.Chimney Team Magma 撃破 → Jagged Pass → Lavaridge。Flannery は炎で Grovyle 不利 → **Route112 で Marill 捕獲→Azumarill** 推奨)

### ★ 復元ポイント (最重要)
- **`generic_agent/memory/savestate_autosnap.ss1`** = 150 turn ごとの自動スナップ。**再開時はこれを load する**
- ⚠ **`rom/emerald_en.sav`(in-game セーブ)は当てにならない**: agent は in-game SAVE をしないため .sav は古いまま(2026-07-13 = Route109/Badge2)。
  **mGBA を再起動すると .sav から起動して進行が巻き戻って見える** → 必ず下記手順2で autosnap を復元すること
  (2026-07-16 に実際に発生。badge2/Route109 に見えたが autosnap 復元で Badge3/Route111/L35 に完全復旧)
- ルール根拠: `docs/INVARIANTS.md:10` の例外(a)「セッション再開時の復元」= 正規手順。ストーリー短絡ではない

---

## 再開手順

### 1. mGBA 起動 (手動・1回/セッション)
`generic_agent/STARTUP.md` 参照。要約:
1. mGBA 起動 → ROM `generic_agent/rom/emerald_en.gba` を Load
2. Tools → Scripting → Load script `generic_agent/scripts/mGBASocketServer_generic.lua`
3. console に `Listening on port 8895` = ready

### 2. ゲーム状態を復元 (Claude が実行可・**必須**)
```
poke-rl/Scripts/python.exe -c "from generic_agent.io import MGBAClient;from generic_agent import config;print(MGBAClient().load_state_file(config.MEMORY_DIR/'savestate_autosnap.ss1',1))"
```
- 直後は START メニューが開いていることがある → `B` を数回タップして閉じる
- 復元確認: badge_count / map / party0_level を**複数回**読み(DMA flicker 対策)、Badge 3 / Route111 / L35 なら成功

### 3. ゲームループ起動
```
poke-rl/Scripts/python.exe -m generic_agent.claude_heuristic --turns 8000
```
- ⚠ venv python は launcher+子プロセスの **2 プロセス**になる(正常。二重ループではない)
- 停止: `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*claude_heuristic*' } | Stop-Process -Force`

### 4. dual_dev (SakanaAI) — 任意
`generic_agent/dual_dev/` に実装あり。**並行 production commit と gate が競合する**ため、回すなら隔離 worktree / quiescent tree で。

---

## 未解決 / 次にやること
1. **★ Rock Smash チェーンの live 検証**(実装済・未検証): House1 で HM06 → VLM が Poochyena に teach → Route111 の岩砕き → Fallarbor 到達
2. Segment 2-4(`docs/PLAN_lavaridge_flannery.md`): Meteor Falls イベント → Cable Car → Mt.Chimney(Tabitha+Maxie) → Jagged Pass → Lavaridge → **gym の溶岩穴パズル(live-collision 要)** → Flannery
3. **Flannery 対策**: Grovyle(草)は炎に不利(2x 被弾・草技 0.5x) → Route112 で **Marill 捕獲 → Azumarill**(Numel/Camerupt は Fire/Ground = 水 4x)
4. latent bug: `MapInfo.connections` が direction-key dict のため Route111 の 2 つの left 接続(Route113/Route112)の片方が clobber され **Route113 が消失**(現状非ブロッキング)
5. (低優先) puzzle-gym 再入場時の tile_map 自動クリアのコード化 / vision battle_menu false-positive の根治 / briney_sail menu 到達不能 / badge_count flicker で旧 goal が一瞬発火

## 運用メモ
- **走行中の mGBA socket は 1 接続前提**。ループ稼働中に診断で別 `read_state` を張ると読みが衝突する(EmulatorError)。最終判定はループ停止後に
- **難所は `architect` / `verifier` subagent を明示起用**。2026-07-15 に architect(Fable)が「Route111 の詰まりは砂漠でなく Rock Smash 岩」と仮説を反証し、無駄な実装を防いだ実績あり
- daily は `generic_agent/daily_progress/YYYY-MM-DD.md`。git は dev に随時 commit、Badge 等の milestone で main merge + tag(最新 `v0.6-dynamo-badge3`)
