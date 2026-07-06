# 再開ガイド (RESUME) — 中断: 2026-07-06

前回セッションを安全に中断した時点のスナップショットと、スムーズに再開する手順。
git は `dev` == `origin/dev`(HEAD `f136922f7`)、working tree クリーン。全ループ停止済み。

---

## 現在地スナップショット

### ゲーム (Pokemon Emerald)
- **Dewford Town、Stone Badge (1個) 保持。次の目標 = Brawly (Knuckle Badge, 3個目)**
- 再開ポイント save state: `generic_agent/memory/savestate_dewford.ss1`(Dewford Town (8,11)、クリーン)
- Gym の goal / ナビは実装済み: `dewford_gym_brawly` が Dewford→(8,17)warp→Gym(3,3)→Brawly(4,3) へ誘導
- **直前に直した核心バグ**: 屋内 Gym の暗迷路タイルが water 誤検出され BFS が Brawly に到達不能だった → INDOOR は water-block しない修正 (commit f136922f7)。Gym 内 BFS は復活(38歩)

### dual_dev (Claude + Codex/SakanaAI 二体制)
- run id: **`run_20260706_112639`**(--resume で継続)
- Codex は SakanaAI `fugu`。**5時間ローリング枠**が制約(週間は余裕)。**バーストで100%にしない**
- 作業記録: `generic_agent/dual_dev/SAKANA_LOG.md`(Claude がレビュー/客観データから著述、毎サイクル自動更新。gitignore 済み)
- rate limiter は永続 (`runs/codex_rate_limit.json`)。誤 pause 対策の 429 修正済み

---

## 再開手順

### 1. mGBA 起動 (手動・1回)
`generic_agent/STARTUP.md` 参照。要約:
1. mGBA 起動 → ROM `generic_agent/rom/emerald_en.gba` を Load
2. Tools → Scripting → Load script `generic_agent/scripts/mGBASocketServer_generic.lua`
3. console に `Listening on port 8895` = ready

### 2. ゲーム状態を復元 (Claude が実行可)
```
poke-rl/Scripts/python.exe -c "import sys;sys.path.insert(0,r'C:\pokemon-rl');from generic_agent.io import MGBAClient;from generic_agent import config;MGBAClient().load_state_file(config.MEMORY_DIR/'savestate_dewford.ss1',1)"
```
起動直後は START メニューが開いていることがある → `B` で閉じる。

### 3. ゲームループ起動 (Brawly 攻略)
```
poke-rl/Scripts/python.exe -m generic_agent.claude_heuristic --turns 8000 --poll 0.6
```

### 4. dual_dev ループ再開 (任意・SakanaAI 枠に余裕がある時)
```
poke-rl/Scripts/python.exe -m generic_agent.dual_dev.orchestrate `
  --resume run_20260706_112639 --from-queue --auto-refill 3 `
  --codex-min-interval 3600 --hours 12 --commit --continue-on-fail
```
- 起動時に reaper が停止済み run を自動 abort → 継続。rate limiter リセットで即 Codex 呼び出し可
- 5時間枠に近づけば自動 pause する

---

## 未解決 / 次にやること
1. **Dewford Gym 入口ナビの詰まり**: agent が Dewford Town (6-7,18-19) で Gym door 手前を振動し
   warp (8,17) に乗れないことがある(mapbfs dist が 2→4 と増える)。Gym 内 BFS は直したが、
   Dewford Town 側で door approach (8,18) → door (8,17) への最終ステップを要確認
2. **in-battle flicker 実機確認**(未消化): 次の野生/trainer 戦で devon flag が安定か 20回サンプル
3. **dual_dev の生産性**: 最近の自動生成タスクは Codex 実装がゲートに落ちがち。commit が伸びる
   ようタスク粒度 or ゲート方針の調整余地あり
4. Brawly 撃破後 (badge 2) は journey goal が自動引退。以降 Slateport 方面へ(goal 未実装)

## 停止コマンド (ループだけ止める、mGBA は残す)
```
# Git Bash / PowerShell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ?{$_.CommandLine -match 'claude_heuristic|dual_dev'} | %{Stop-Process -Id $_.ProcessId -Force}
```
