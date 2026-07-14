# 再開ガイド (RESUME) — 中断: 2026-07-06

前回セッションを安全に中断した時点のスナップショットと、スムーズに再開する手順。
git は `dev` == `origin/dev`(HEAD `dbb0a7328`)、working tree クリーン。全ループ停止済み。
mGBA は **savestate_dewford.ss1 を復元済み**(クリーン Dewford・健全 Grovyle L24・badge 1)。

---

## 現在地スナップショット

### ゲーム (Pokemon Emerald)
- **Dewford Town、Stone Badge (1個) 保持。次の目標 = Brawly (Knuckle Badge, 3個目)**
- 再開ポイント save state: `generic_agent/memory/savestate_dewford.ss1`(Dewford Town (8,11)、クリーン)
- Gym の goal / ナビは実装済み: `dewford_gym_brawly` が Dewford→(8,17)warp→Gym(3,3)→Brawly(4,3) へ誘導
- **H1(door振動)/ nav(gym leader 到達)/ H6a(バトル opening)は ✅ 全て解決** (commit 707d26e58, dbb0a7328)。
  live で **Dewford→ドア→迷路→Brawly 到達→戦闘起動→Grovyle が Machop + Meditite 2体撃破** を確認。詳細 docs/HYPOTHESES.md H1/H6
- **次の blocker = H6b(team強度、grind 必要)**: Brawly は3体(Machop L16 + Meditite L16 + **Makuhita L19 Bulk Up**)。
  Grovyle L24 単騎は2体倒すが消耗して Makuhita に届かず気絶、控え L3-L7 で詰み → whiteout。
  **これは code バグでなく game 進行の問題**。対処 = Grovyle を L27-28 まで軽く grind(または2体目育成 / 戦闘中 Potion)。
  再開は savestate_dewford.ss1 から(setback なし、ストーリー進行ゼロ)。
- **バトルAIの実測**(gBattleMons 固定アドレス)は走行中でも競合に比較的強いが transient 値あり。最終判定はループ停止後 or screenshot で。

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
1. **★最重要 = バトルAI H6**(docs/HYPOTHESES.md H6 に詳細)。Brawly 戦で勝てるはずが落ちる:
   - **H6a**: opening で `party_seq=("A","B","Down","A","A")`(交代画面用)が戦闘メニューに誤発火し
     Grovyle が反撃前に一方的に殴られ気絶。→ trainer 戦で FIGHT メニュー(疑い含む)なら best_move を
     party_seq より優先。party_seq は「lead 0HP かつ交代画面」に限定。
   - **H6b**: 控えが L3-L7 と低レベルで Grovyle 依存。H6a を直せば L24 単騎で Brawly 突破可能。
   - 検証: savestate_dewford.ss1 から再走行し、Machop の HP が opening から単調減少するか
     (decisions ログ + battle_moves.enemy_hp)。**走行中は socket 直読み禁止**(下記メモ参照)。
2. **in-battle flicker 実機確認**(未消化): H6 の Brawly 戦で devon flag が安定か 20回サンプル
3. **dual_dev の生産性**: 自動生成タスクが Codex 実装でゲートに落ちがち。粒度/ゲート調整余地
4. Brawly 撃破後 (badge 2) は journey goal が自動引退。以降 Slateport 方面へ(goal 未実装)

### 運用メモ(重要・今セッションの教訓)
- **走行中の mGBA socket は 1 接続前提**。ループ稼働中に診断で別 `read_state` を張ると読みが衝突し
  corrupt read((0,0)/species 誤り)を誘発する。監視は `logs/decisions_*.jsonl`(ファイル)で行い、
  socket 直読み・screenshot はループ停止後に。
- claude_heuristic は venv ランチャの都合で python.exe が **親+子の 2 プロセス**に見えるが正常
  (socket 競合ではない)。kill は `commandline -match 'claude_heuristic'` で両方まとめて。

## 停止コマンド (ループだけ止める、mGBA は残す)
```
# Git Bash / PowerShell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ?{$_.CommandLine -match 'claude_heuristic|dual_dev'} | %{Stop-Process -Id $_.ProcessId -Force}
```
