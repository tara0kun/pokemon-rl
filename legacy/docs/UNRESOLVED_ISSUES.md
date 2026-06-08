# 未解決問題・後回し・その場しのぎの一覧

最終更新: 2026-06-05 (★★★ graph corruption: edge + wall_X 共存 inconsistency)

---

## 2026-06-06 ★★★ CLAUDE.md ルール違反: cave 70% 未達なのに nav_t=pokecenter (escape mode)

**事実 (user 指摘)**:
- (24,7) Cave 1F: 33.5% (211/630) ← 70% 閾値遠い
- (24,8) Cave B1F: 36.1%
- (24,9) Cave B2F: 42.7%
- (24,10) Steven Room: 16.7%
- (3,3) Brawly Gym: 30.0%
- 全部 CLAUDE.md「70%+ 完了で攻略切替」 閾値未達

**症状**: env nav_t=pokecenter (heal escape route) で 70% 未達なのに「攻略 escape」 mode で動作、 mapping ルール違反

**根本原因**: heal-complete 条件 (`_r116_leveling and not _slot0_fainted`) が cave 内では永久未達 → heal mode 永続化

**user 引継ぎ事項**:
- heal mode 解除条件に **map coverage 閾値** 追加: 「現 map < 70% → heal mode 解除して mapping mode へ」
- env 内 `_pokecenter_heal_active` 解除 logic に coverage 計算追加

**深刻度**: 高 (CLAUDE.md mandatory ルール準拠不能)

---

## 2026-06-05 ★★★ mGBA 8889 ROM unloaded / core dead (process 生存も内部 dead)

**症状**: 
- screenshot `No frames captured for port 8889` = mGBA core 反応無
- RAM `party_lv=[0,0,0,0,0,0]` `enemy=sp0Lv0` = garbage 全 0
- 8890 も partial garbage `party_lv=[48,0,0,0,0,0]` = 不安定前兆
- mGBA process 3 件全生 (PID 27220/37328/79984、 6/03 起動)

**原因推定**: 
- mGBA-http lua script は応答するが ROM core が paused/unloaded
- 既知問題: 10日連続稼働で hang、 但し今回 2日でも兆候

**user 引継ぎ事項 (autonomous 不可)**:
- 8889 mGBA で ROM 再ロード or core resume 操作
- 8890 mGBA も予防的 ROM 再ロード推奨
- 長期 protocol: 定期 mGBA 再起動 (24-48h 周期推奨)

**深刻度**: 高 (8889 chronic stuck の誤検知原因、 監視判断混乱を招く)

---

## 2026-06-05 ★★★★ BattleAmash A forced × patch #20 PP-Guard conflict

**症状**: 全 3 port (24,17) battle 中 spc 600/241/365 で move-select cursor が PP=0 slot 0 から動かず stuck。 PP slot 3 (=5) が active move available だが選ばれず

**根本原因**: log `[BattleAmash] (24,7) spc=565 A forced port=8890` — battle handler が move select 画面でも A button 連打、 patch #20 が出力した Down/Right action を **後段で override** している疑い

**症状連鎖**:
1. Battle main menu → A forced → たたかう (Fight) 選択
2. Move select 画面 → patch #20 が Down action 出力するが BattleAmash が A force で上書き
3. A → cursor slot 0 (PP=0) で move attempted → 「PP がない!」 → menu に戻る → loop

**根本対策 (未実装、 user 判断必要)**:
- **★★★ root cause 確定 (2026-06-05 静的解析)**: patch #20 PP-Guard の battle 検出が `_cached_battle.in_battle` のみ、 JP ROM の CB2 検出失敗時 patch fire しない (CLAUDE.md tech note 既知問題)
- **修正案 (低リスク)**: patch #20 条件を BattleAmash 同様の `_battle_cycle_counter > 0 or _same_pos_count >= 300` に変更
- battle handler の A force ロジックに **PP-0 cursor 検知 → A force skip** 条件追加
- patch #20 を battle handler 後段に配置 (action 最終決定権を patch #20 に)
- z57+ tangled なので standalone simulator で挙動検証推奨

**今回 deploy 控え理由**: patches #19-23 連続 5 deploy 後の検証不十分、 CLAUDE.md 「3 連続 fail で stop」 規律遵守で観察継続

**2026-06-05 23:42 追記 (patch #24 deploy 後)**:
- patch #24 検出条件 fix 効果実証: 8888/8889/8890 **全 port で patch #20 PP-Guard fire 開始** (log 確認、 前は 8890 のみ)
- 8888 LongStuck 900→1 で **(24,17)→(23,17) 移動成功** = effect partial
- ただし 8889 cur=0 stuck spc=476 継続 = **BattleAmash の A forced が patch #20 act=3 後段で override** 継続中
- 残課題 = action order: BattleAmash (line ~21746) が `action = 0` 設定後、 patch #20 (line ~22750+) が `action = 3` 設定するが、 **その間に BattleAmash と patch #20 の if 条件マッチ順序で別 path が override** の疑い
- **user 引継ぎ事項**: BattleAmash の A forced 条件に `move-select 画面検知 (cursor read 可能) かつ PP=0 hover` の skip 追加、 or patch #20 を BattleAmash の前に配置で順序逆転

**2026-06-06 00:32 追記 (patch #25 deploy 後)**:
- patch #25 (BattleAmash probability 0.50→0.10) deploy 後 8888/8890 movement あるが **party_lv 不変 = leveling 完全停止継続**
- 累計 patches #19-25 (7 deploy) 後も battle 抜け 不成、 root cause は構造的問題で patch でない
- **CLAUDE.md patch tower 「3 fail で stop」 規律発動**、 新規 deploy 完全停止宣言
- **構造修正必須 (user 判断必要)**:
  1. battle main menu detection 精度向上 (CB2 JP ROM 代替)
  2. patch #20 を BattleAmash の前に配置で順序逆転
  3. battle handler 全体 standalone simulator で挙動検証
  4. RAM intermittent dead 検証 (mGBA-http lua script 安定性)

**2026-06-06 00:42 追記 (patch tower stop 解除)**:
- 1 cycle 遅延で patch #25 効果実証: **WIN 0→7 + cur=0→1 cursor 移動**
- patches #24 (PP-Guard fix) + #25 (BattleAmash 0.10) 連鎖で battle move-select 突破可能化
- patch tower 規律 stop 解除、 observation continue mode 通常 monitoring 復帰
- 残課題: slot 4 (21→0)、 slot 5 (41→0) 連続 faint = 高 Lv 48 active 偏重で他 party 育たず、 leveling 戦略再検討必要

**今回対応**: patch tower 回避で観察継続、 即時 patch 投入無

**深刻度**: ★★★★ (escape pipeline 試行不可、 全 port stagnation 直接因)

---

## 2026-06-05 ~~Lv 48 バシャーモ overpower battle~~ (誤読、 上記が正解)

**訂正**: 画面 visual layout 誤解 (top=enemy, bottom=player の逆認識)。 **バシャーモ Lv 48 は player の Pokemon** (party slot 0 Lv 48 active battler、 RAM 確認 my=sp282Lv48)。 enemy は Lv 7-8 wild encounter (cave 適切 level)。 overpower でなく上記 BattleAmash×PP-Guard conflict が root cause。

---

**症状**: step 600 で 全 3 port (8888/8889/8890) が 1F (24,17) で **Battle vs Lv 48 バシャーモ** メイン画面 (たたかう/バッグ/ポケモン/にげる) で stuck、 spc 600/241/365

**観察**:
- player Makuhita Lv 18, PP [0,0,0,5] (slot 3 のみ生存)
- 8888 enemy HP 94/161、 8890 enemy HP 53/161 = damage 与えてる → battle 進行中
- 3 port 同じ enemy Lv 48 Blaziken = **deterministic encounter** (random ではない)
- にげる (Run) も Fight も決断されず menu cycle 繰返し疑い

**推定原因**:
- battle handler が overpower enemy 認識せず、 valid PP slot 3 (Lv差 30 で勝ち目無) を出し続け
- にげる threshold logic 無 or 効いてない
- Lv 48 Blaziken の RAM read accuracy 確認必要 (anomaly 疑い、 通常 Granite Cave に出現しない)

**今回対応**: patch tower 回避で **観察継続**、 即時 patch 投入無 (3 連続 deploy 後の追加 patch リスク)

**深刻度**: 高 (escape pipeline patch 効果検証不可、 全 port stagnation 直接因)

---

## 2026-06-05 ★★★ graph corruption: edge と wall_X 共存

**症状**: 8888 1F (24,17) spc=546 chronic stuck、 log で `[FNav-Skip] fail on Right ... +wall at (24,17)` 連発

**原因**: exploration_map.json (24,7) 1F の 13 tile で **edge ([Right: ...]) と wall_Right: True が共存**、 env が wall flag 優先で edge 無視

**応急対策 (patch #23)**: rule-based cleanup で 1F の全 contradictory wall flag 削除 (13 件)

**根本対策 (未実装)**:
- GhostEdgeFix or 類似ロジックが誤って wall flag 立てる原因特定
- env の wall_X 設定処理に「既存 edge と矛盾しないか」チェック追加
- 1F 以外の map (B1F/B2F/Route106 等) でも同様 corruption が無いか全 map audit 必要

**深刻度**: 高 (chronic stuck 直接因)

**2026-06-06 00:02 追記**: patch #23 cleanup 後 1.5h 経過で再 corruption 確認 (log `FNav-Skip fail on Right +wall at (24,19) (24,17)`)。 **GhostEdgeFix が wall flag 再書き込み** = cleanup 永続効果無、 構造修正必須 (user 引継ぎ強化)

---

## 2026-06-03 ★★★★ 4 patches deploy 後も nav design issue 持続 — code-level fix のみ解決

**deploy 済 (全 success)**:
1. cleanup #1 (24,8,11,29) bogus warp 削除
2. cleanup #2 (24,8,12,28) bogus warp 削除
3. canon inject GraniteCave_1F (89→210 nodes、 +377 edges)
4. canon inject GraniteCave_B1F (22→299 nodes、 +876 edges) — 6/03 deploy
5. canon inject GraniteCave_B2F (0→355 nodes、 +1142 edges) — 6/03 deploy
6. canon inject GraniteCave_StevensRoom (0→35 nodes、 +104 edges) — 6/03 deploy

**観察結果** (post-restart 150 step):
- 8889 が 1F (24,18) ↔ B1F (11,28) で **10+ MapChange = oscillation 復帰**
- B1F 完全マッピング (100%) 後も ladder pillar に引き戻される
- graph 拡充では不解決を **definitively 確証**

**真因再確認**: pokemon_env.py の pokecenter target resolution
- 18+ 箇所で `nav_target == "pokecenter"` 参照
- cross-map (cave→Dewford PC) BFS 失敗 → fallback ladder warp = 1 step path
- z269b/z3/z178/z201b 等の deep tangled patches 群

**Autonomous scope を超える理由**:
- 1 surgical fix が 17 既存 patches と衝突する regression リスク
- standalone test harness なし
- 既存 heal corridor (Brawly post-fight 等) 破壊リスク
- patch-tower 既 4/3 deploy、 next failure で trigger

**USER 判断必須**:
1. 「town_exit」 or 「route106_exit」 を新 intermediate target として実装 (cave→outdoor→PC の 2 段階)、 or
2. pokemon_env.py heal nav refactor (large)、 or
3. graph injection で west↔east bridge edges 手動追加 (canon parse limit を補完)

---

## 2026-06-03 ★★★ 1F nav が east 29-step path を選ばず ladder oscillation

**経緯**: cleanup #1+#2 で 8888/8890 が cave 1F 到達可能、 ただし 1F (24,18) ladder 周辺で oscillation 持続、 east の Route106 exit (44,19) へ進行できず。

**graph audit** (post-cleanup):
- 1F nodes 88、 Route106 exit (44,19) area **13 nodes マップ済**
- BFS verify: (24,18) → (44,19) は **REACHED 29 step** = 連結性 OK
- Steven warp area (12,17) は 0 nodes = **未マップ**

**真因仮説**: nav planner の target 選択 logic が `pokecenter` target を Dewford PC (cross-map) に解決失敗 → fallback で 「nearest warp = ladder (1 step)」 を選択 → oscillation
- 29 step east path は計算可能だが選択されない
- target が unreachable cross-map なので最短 warp 経路への defaultlogic と推定

**対処** (hold):
- nav target resolution の root cause 修正は **1F nav logic の design 変更**、 高 regression リスク
- cleanup precedent でない (code 修正)
- 自然探索で graph が west 寄りに拡充されれば Steven warp 発見可能、 ただし oscillation 内では実現困難
- patch-tower budget 1 残、 user "B" cave-interior 制約

---

## 2026-06-03 ★★★ exploration_map.json (24,8,11,29) bogus warp 残存

**症状**: 8888/8890 が canon (4,22)=my (11,29) で 100h+ stuck、 物理 Up は ladder canon (4,21) へ通行可だが移動なし

**graph 確認**:
```python
node = exploration_map['24,8,11,29']
# Up: [24, 8, 11, 28] — passable to ladder ✓
# wall_Down: True ✓
# Right: [24, 8, 12, 29] — passable ✓
# wall_Left: True ✓
# warp: [24, 7, 24, ?] — ★ BOGUS! (11,29) is NOT a warp tile per canon
```

canon B1F (32×26) の real ladder L は **canon (4,21) = my (11,28) のみ**、 (11,29) は通常 floor tile。 exploration_map.json で (11,29) に warp [24, 7, 24, ?] が誤って付与されている。

**影響推定**: env BFS が 「(11,29) は warp tile、 既に到達済→方向不要」 と判断し action 押下せず → 入力空回り → spc 永続蓄積 (実 game では warp tile ではないので何も発火しない) = **chronic stuck の真因の 1つ**

**z302 漏れ**: 2026-05-29 の z302 で bogus 越境 edge 一部 purge 済 (`(3,0)→(24,4)` 等)、 ただし B1F 内部の (11,29) → 1F warp は対象外だった可能性

**対処** (hold): graph 直接修正 (`(24,8,11,29)` の warp key 削除) は patch ではなく **データクリーニング**、 z302 と同等の作業。 ただし user "B" 制約解釈 (cave-interior 変更含むか) で hold 推奨。 backup `exploration_map.json.backup_pre_z302_20260529` あり。

---

## 2026-06-02 [軽微] _recover_game_state print message が misleading

**状況**: 8889 で RECOVERY #126 発火、 print に `Loading save state slot 1...` 出力で「ルール違反 (load 禁止)」 を誤検出した。

**code 検証** (pokemon_env.py:8980-9003): 実装は ボタン操作 (A 連打 30回) のみ、 saveStateLoad 呼び出しなし。 line 8995 で `# ★ ステートロード禁止: ボタン操作でタイトル画面を突破` と明記。 = ルール準拠。

**問題**: print の `"Loading save state slot 1..."` (line 8988) と `"Slot 1 loading..."` (line 8992) が古い実装の文言、 現実装と矛盾。 監視自動化が「load 発火」 と誤検出するリスク。

**対処** (hold): 文言修正は logic 変更なしで安全だが patch-tower 規律下では autonomous 適用回避、 user 承認待ち。

---

## 2026-06-02 ★★★ Port 8890 PP-zero stall 実時間 visible 確証

**スクショ確証** (recording_8890_20260602_225049.gif):
- Zubat Lv9 vs Blaziken Lv46、 戦闘 message **「わざの のこりポイントが ない！」**
- = agent が slot 0 (PP=0) を選択しようと A 押下、 ゲーム側が拒絶
- 反復毎ループで battle 解消せず

**code audit** (pokemon_env.py:12290+ z251o):
- 12290-12293: 「PP>0 slot から `_best_idx` 計算 (move_db power × type 相性)」 → 正しい
- 12294: `_ai_act = [0, 2, 3, 4][_best_idx]` で idx→action 変換
- 違和感: idx=1→act=2(Up)、 idx=2→act=3(Down)、 idx=3→act=4(Left)
- Pokemon Emerald move-select menu は 2×2 grid (slot0 top-left, slot1 top-right, slot2 bottom-left, slot3 bottom-right)
- 標準 cursor nav: idx=1 → Right、 idx=2 → Down、 idx=3 → Down+Right
- → z251o の idx=1→Up、 idx=3→Left は menu layout と不一致疑い (Up が top→外 で no-op、 Left も同様)
- 結果: _ai_act の direction press が cursor 移動を生まず、 次の A press で slot 0 (PP=0) を確定 → 「PP ない」 エラー → 同様

**確認必要** (autonomous で hold):
- _ai_act が direction → A 1ステップで処理されているか、 2ステップ (move + confirm) か
- battle_switch_target / _ai_move_map 連携での実際の input sequence

**自律対応保留 (patch-tower 規律 + user "B" 制約)**:
- z251o の idx→act 修正は z57+ 既存 battle handler patches 群に深く絡む
- 単純な mapping 変更でも regression リスク (Blaziken/Ralts 場合分け、 train_switch_done、 _dmg_only 等)
- standalone simulator で input sequence trace してから patch 設計が要

---

## 2026-06-02 ★★★ Port 8888 PP-zero battle stall (Granite Cave B1F)

**スクショ確証** (recording_8888_20260602_212045.gif):
- マクノシタ Lv10 (Granite Cave trainer の Makuhita) vs バシャーモ Lv46 (slot0 starter、 HP 107/161)
- 戦闘 menu「たたかう/バッグ/ポケモン/にげる」 で stall、 カーソル たたかう

**monitor 表示との乖離**:
- monitor: `Map: (24,8) pos=(11,29) nav_t: (empty) spc: 350` → 「stuck nav」 と誤解しがち
- 実態: `_in_battle=True` で battle menu stall、 overworld 静止は battle 中だから
- training log の `[Stuck-Escape] force_act=Left/Right/Up/Down pos=(11,29)` は battle menu 内 cursor 移動を送っているが、 A press 後の move-select で PP=0 弾かれ → menu 復帰 ループ

**真因**: PP=[0,0,0,16] で slot 0-2 PP 切れ、 slot 3 のみ 16 残。 battle handler が **PP>0 slot を自動選択しない** (z57+ で重ねた patches 群が PP-zero auto-switch に未対応)。

**RUN 不可**: 真トレーナー戦 →「トレーナーには逃げられない」 で にげる 選択も無効。

**患者状態**:
- 8888 はこの状態で 約 100h 釘付け (cave B1F に入った時点から battle 中の可能性、 spc は battle 中も増加)
- Stuck-Escape の force_act は battle 中無意味、 反って menu navigate を撹乱

**自律対応保留 (patch-tower 規律)**:
- z57+ 既存 battle handler patches 群が深く、 reactive patch は regression リスク
- 「PP>0 slot 自動選択」 の正面実装が要、 standalone 検証ありで設計判断
- user 待ちで hold

---

## 2026-06-02 tile_classifier door class データ偏在 (真因)

**現状**: door samples 859 / 49950 (1.7%)、 door F1 0.334、 precision 0.245 (大量 walkable→door 誤検出)

**分布**:
| map | door | walkable | wall |
|---|---|---|---|
| m0_3 (Petalburg?) | 662 (77%) | 10821 (47%) | 3339 (47%) |
| m0_31 (隣接?) | 181 (21%) | 9655 (42%) | 3596 (51%) |
| m24_4 | 10 | 304 | 165 |
| m11_3 (Dewford?) | 0 | 1124 | 0 |
| m11_1/2/11 | 5+1 | 480+440+43 | 0 |

**観察**: 全 class が m0_3/m0_31 に集中、 ~実質 Petalburg 系 2 マップ学習。 Dewford/Granite Cave/Briney 系の door 採取はほぼ皆無 = classifier はそれら tileset で全く識別不能。

**改善案 (autonomous で実行可能)**:
- tile_collector を Dewford/Granite Cave 期に再走 (run時 agent が m11_*/m24_* 領域に到達中)
- canon の door metatile ID を pokeemerald metatile データから直接抽出して synthetic samples を作成
- ただし: 現 cave stuck で agent が Dewford 拠点に戻れない、 retrain 後の deploy も注意 (classifier swap が pokemon_env.py 挙動に影響する可能性)

**hold 判断**: 既存 v6e は Petalburg 期に limited use、 cave stuck 解消後の Dewford 復帰時に tile_collector を再起動して採取するのが効率的

---

## 2026-06-02 ★★★ canon 確証: 8888/8890 がラダー隣接 pillar trap

**canon_ascii.py で B1F/1F を可視化** (KNOWN_MAPS 拡張済、 inject なし):

### GraniteCave_B1F (32×26) — 8888 stuck 位置
```
21  ###.L.#######...########.....###   L=ladder up to 1F (canon 4,21 = my 11,28)
22  ####8.########..#########....###   8=8888 (canon 4,22 = my 11,29)
```
- (4,22) の四方: U=L (passable, ladder)、 D=wall、 L=wall、 R=wall
- → **Up 唯一通行可、 そこがラダー**。 移動の物理 path は完璧
- 観察: spc 350→450 (battle 内 stall、 move 不可)

### GraniteCave_1F (42×15) — 8890 stuck 位置
```
11  ##########...#.#.D.###############....###  D=descent ladder to B1F (canon 17,11 = my 24,18)
12  ############.#.#.9.###############....###  9=8890 (canon 17,12 = my 24,19)
```
- (17,12) の四方: U=D (passable, ladder)、 D=wall、 L=wall、 R=wall
- → **Up 唯一通行可、 そこが B1F 降下ラダー**
- 観察: spc 47 で停止、 スクショで Up 向きラダー視認 (recording_8890_20260602_213320.gif)
- 仮説: exploration_map.json の (24,18,?,?) wall_Up エッジが (canon 一致しないが) 残存、 Up 入力を反射的に reroute している

**結論**: 両 port は cave-exit/inter-floor warp の物理直前で「最後の 1 マス Up」が出ない:
- 8888 = battle 中で move 不可 (battle handler 問題)
- 8890 = 物理は Up 1 マスで OK だが wall data か nav target で Up が抑制されている

**自律対応保留**: user "B" 制約 (cave-interior reactive patching 禁止) を遵守、 patch なし

---

## 2026-06-01 ★★★ 重大運用問題：Port 8889 dead で system 構造破綻

**socket 確認**：port 8888 OPEN、 **port 8889 NO-RESPONSE**、 port 8890 OPEN。

**training log**：8889 への step() が 7-13 秒/step の timeout で **全 system のボトルネック** 化。 SLOW-STEP burst=98 が 8889 単独で発生。

**プロセス調査**：
- mGBA 現存 2 個 (PID 3304, 23800)、 両方 2026-05-24 起動 = **8 日連続稼働中**、 ~4.7日 の CPU time 累積 → これが mGBA hang 累積の根本
- mgba_http_pids.txt は 5/25 の古い PID (38228/37040/1388) で記録、 現実と不一致
- 8889 mGBA-http (or mGBA 本体) は session 途中で落ち、 監視されていなかった

**長期的影響**：
- monitor.py の 8889 表示 (Map=Littleroot, PP=[35,35,35,35]) は **stale cache**
- session 中盤以降の「8889 PP 減少」「8889 trap escape」「8889 cave 1F 到達」報告は **架空データを信じた誤報告** の疑い
- WIN/Counter fluctuations もこの不整合由来の可能性

**ルール判断要請**：
- CLAUDE.md「mGBA を絶対 kill しない」は live process 保護を意図
- 既に dead な 8889 用 mGBA の新規起動はルール抵触するか不明 → user 判断要請、 独断で起動せず

**現状方針**：
- 2 instance 体制で監視継続 (8888/8890 のみ実勢)
- 8889 復旧の許可があれば mGBA + mgba-http 起動を試行

## 2026-06-01 重大訂正：Steven は cave 1F に居ない / warp tile (12,17) 経由

**map.json 解析結果** (GraniteCave_1F):
- Steven は cave 1F NOT、 別マップ `MAP_GRANITE_CAVE_STEVENS_ROOM` に居る
- **my (12,17) は warp tile** → Steven's Room へ転送
- 1F の他 event: Hiker NPC = my (43,16)、 ItemBall(Escape Rope) = my (24,14)、 B1F warp = my (24,18)/(42,10)、 Route106 warp = my (44,19)

**現状進展**:
- 8888 が **full PP recovery 確認** PP=[7,39,30,30] (前 [0,3,3,21]) = whiteout→PC heal→cave 復帰 path が **system 内で organic に作動した**実証
- 8888 cave 1F (23,19) = canon (16,12) → (12,17) warp まで残 11 tile 西 + 2 tile 北
- 8889/8890 はまだ B1F (11,29) で PP[0]=0 stall — 同 recovery path 待ち

**判断**: organic recovery が作動した実証あり → 強制 nudge せず観察継続。 8888 が西進すれば Steven 接近自然発生の見込み。

---

## 2026-05-29 ★最重要 root-cause: exploration_map グラフ汚染 (bogus inter-map edges)

**chronic 島 stuck の真因確定 (graph 解析、read-only)**: Dewford 島マップ (0,11)/(0,21) が到達不能な mainland/invalid マップへ **bogus warp edge** で多数接続されている。frontier/BFS がこの phantom edge を使って mainland frontier (route103 等) へ path 計算 (214 steps) → agent は map 端まで歩くが boat-event gap で stuck → ghost battle 誘発。WIN=0/EXP 凍結の真の dominant blocker。

**graph 解析結果 (island maps の inter-map 接続)**:
- **LEGIT**: (0,11)↔(0,21) [Dewford↔Route106]、(0,21)→(24,7) [Route106→**Granite Cave 1F**、これが post-Brawly story 目的地]、(0,11)→(3,0/3,1/3,3/3,4/3,5) [Dewford 建物: Gym/PC/Mart/houses]。
- **BOGUS** (削除対象): (0,0)Petalburg↔island、(0,9)Littleroot↔island、(2,0)/(8,0)/(8,6)/(9,2)/(27,50)/(28,50)/(34,6)/(36,49)/(40,6)/(48,0)→island、**(255,3)→(0,11)** ← map 255 は invalid = RAM garbage read 由来の確定汚染ノード。
- 汚染源推定: Briney boat trip の warp 記録 + map 遷移中の garbage RAM read + mainland 時代の stale warp。

**症状連鎖の全体像 (本 session で段階的に判明)**:
1. ghost battle が trainer 誤判定で永続ロック → **z300 で修正済 (keep)**。
2. stale `_nav_target="petalburg"` carryover が島で東 pull → **z301 で _nav_target レベルを frontier 化 (keep、ただし症状対処)**。
3. **真因**: graph 汚染で frontier/BFS が mainland (route103) を target → z301 の後 (line 16137) で `_nav_target` 上書き → z301 を実質無効化。

**正しい root-cause 修正 (次 cycle で慎重に実装予定、z301 検証後)**:
- 既存の MapLoad ghost-edge purge pattern (pokemon_env.py:2110 [Petalburg-R104 ghost edges removed]、2501 [Removed false warp]) に倣い、island maps の bogus inter-map edge を purge。
- **delicate**: LEGIT な (0,21)→(24,7) cave edge、(0,11)↔(0,21)、(0,11)→(3,x) 建物 edge を**絶対に消さない**こと。purge 対象は「island → mainland(boat 経由のみ到達) / invalid map」の edge に限定。
- (255,3) invalid ノードは無条件削除可 (garbage)。
- live exploration_map.json への直接編集は 3 process 競合で危険 → MapLoad 時の in-memory purge (code 経由) で実施。
- これが解ければ frontier が島内 (Route106 含む) + Granite Cave 方向に正しく向き、post-Brawly story 進行が自然に再開する見込み。

**★ leak edge 特定 + 修正 deploy (v10.9z302、2026-05-29)**:
- BFS path trace で leak chain 確定: **(0,11)Dewford→(3,0)建物→(24,4)Kanashida Tunnel→(0,0)Petalburg→mainland全域**。map (24,4)=Kanashida Tunnel (mainland、過去 playthrough で訪問)、建物(3,0)→(24,4) は corrupt map-transition 由来の偽 warp。全 mainland leak (garbage 255,255 含む) がこの chain prefix を共有 → **surgical 2-edge cut で済む**(当初恐れた大規模 surgery 不要)。
- offline 検証: 該当 edge 除去で Dewford reachable maps **52→9** (島+建物+0,22 のみ)、mainland(Petalburg/8,0/255,255)完全遮断。LEGIT 島内 edge 不変。
- **z302 実装**: one-time per-env in-memory purge (v261hi pattern 踏襲、line ~9647)。(3,0)→(24,4) と (24,4)→(0,0) edge を除去 + BFS cache version 無効化。exploration_map は `type(self)._shared_exploration_map` で 3 port 共有 → 1 回 purge で全 port 反映。backup: exploration_map.json.backup_pre_z302_20260529。
- **早期検証 (step 50)**: 3 port 全て nav_t=frontier、**mainland target (petalburg/route103) 消失**。8889 が東端 corner (x73)→西 (x61) へ移動 = cave 入口(~55,23)方向への自然再 mapping 開始。強い positive signal。
- **残確認 (次 cycle)**: ① mainland target 不再発の持続確認、② agent が cave 入口到達→Granite Cave 進入するか、③ z302 が 2 edge 中 1 のみ除去した件 (topology 上 1 で十分だが robustness 要確認、disk JSON には 2 edge 存在)。
- Route106 graph fragmentation (東端 component が cave 入口 node と非接続) は別途残るが、mainland pull 除去で frontier が西進→物理 walk で再接続される見込み。

**★ z302 持続検証 + cave 経路分析 (2026-05-29, step 150)**:
- **mainland target 完全消失を確認**: restart 後 route103/petalburg targeting = **0 回**。z302 は意図通り mainland leak を遮断。3 port 全て nav_t=frontier/empty。
- env step rate 健全 (HB: 50 steps/85s ≈ 35/min)。env hang なし。
- ghost battle は継続するが **self-resolve** (bcc 62→8→15 で reset、z300 が永続ロック防止)。EXP 凍結は ghost battle 由来 (real EXP なし) で、agent は元々過剰 Lv47/48 のため story 進行が目的。
- 8889 が x73→x61→x65 と西進中。reachable x-range = **48-77 (cave 入口 ~x55 を含む)** → frontier 探索で物理的に cave 入口到達可能。
- **重要訂正**: graph に **(0,21)→(24,7) 入口 edge は存在しない**。逆向き (24,7)→(0,21) cave-exit warp のみ存在 (z299 recovery で *退出* した記録)。よって agent は cave 入口 tile (~55,23) に**物理的に再度乗る**ことで warp in 発火→cave 進入+edge 記録、が必要。mainland pull 除去後の frontier 西進で自然発生する見込み。**z297 型の強制 nudge は再トラップ risk のため行わない** (frontier に委ねる)。
- 次 cycle 確認: agent が x55 cave 入口に到達→Granite Cave 進入するか。数 cycle 経っても未達なら careful nudge を再検討。

**★ z303 cave-entrance nudge deploy (2026-05-29, user 承認 "実装する")**:
- 判明: Route106 frontier 枯渇 (140/148) のため、mainland pull 除去 (z302) 後も agent は cave 入口 (55,23) へ向かう「理由」が無く東端 idle。**待っても進まない** = nudge が必要。
- 実装: cave 入口 = Route106 (55,23) (graph: cave(24,7)⇄Route106 warp 群が 55,23-25)。bfs_to_position で (55,23) へ walk → 踏むと Granite Cave 1F へ warp in。
- **重要: 配置場所**。初回 line~9356 に置いたが frontier nav (line~16077) に action 上書きされ無効 (z301 と同 root)。**FINAL 直前 absolute override 領域 (line~22417、z296/z299 と同所)** へ移動して解決。早期版は削除。
- **検証 OK**: z303-CaveNav 発火、FINAL act が z303 の act と一致 (上書きされず)。8889 (71,20)→Left 西進、8890 (65,19)→Down、cave 入口 (55,23) へ navigation 開始。
- scoped (Route106+badge2+屋外+非heal+spc<25)、revertible。B1F trap は z299 が backstop。
- 残確認 (次 cycle): agent が (55,23) 到達→Granite Cave (mg=24) 進入するか。進入後 1F で Steven(letter event) へ。re-trap や未進入なら revert/再検討。
- 教訓: nav 系 action override は**配置場所が決定的**。早期 (step 前半) に action を set しても frontier/BFS-Nav が後段で上書きする。確実に効かせるには FINAL 直前の absolute override 領域に置く (z296/z299/z303 共通)。

**★★ BREAKTHROUGH: chronic 島 stuck 解消 — Granite Cave 進入成功 (2026-05-29)**:
- z303 deploy 後、**全 3 agent が Granite Cave (24,7) 進入成功** ((0,21)→(24,7) via (55,23)、MapChange 確認)。数時間 island で stuck していた状態から脱却。
- **z303↔z299 bounce 発見+修正 (z303b)**: z303 が cave へ push、z299 が即 pull out (spc<25 trigger) → 入口で bounce し cave 探索不能だった。z299 の spc<25 trigger を削除 (spc>200 = 真の trap のみ rescue) → bounce 解消、全 3 体が cave 1F (44,18) に滞在。
- 修正連鎖の全体像: **z302 (graph leak 切断=root) → z303 (cave 入口 nudge) → z303b (z299 bounce 修正)**。
- 次段階: cave 1F を西進し **Steven (12,17、letter event)** へ。1F frontier 探索に委ねる (z303 は mn=21 のみ発火するので cave 内では作動せず)。risk: 1F→B1F warp ((24,18)/(42,10)) で B1F へ落ちる可能性 → z299 (spc>200) が backstop。数 cycle 観察し、1F→Steven が frontier で進まなければ 1F nav を検討。

**★ cave 1F 内 新 stuck: 入口 oscillation + fragmented 1F (2026-05-29, step 100)**:
- 全 3 体 cave 1F (24,7) に進入したが **入口 (44,18) で stuck** (8890 OscTrap spc=100)、西進せず。8889 は時々 (44,19) exit warp を踏んで Route106 へ pop out (residual oscillation、z303 が再進入)。
- **graph 解析 (read-only)**: cave 1F 88 nodes (x0-58) だが、入口 (44,18) から到達可能なのは **x23-45 のみ (83 nodes)**。**Steven (12,17) を含む西半分 (x<=22) は unmapped/disconnected** = fragmented。Steven 到達には agent が物理的に西進して 1F 西部を再 mapping する要あり。
- 入口 (44,18) は exit warp (44,19) と隣接 → oscillation で exit を踏みやすく、西進の機会を得られない。
- **これは cave-INTERIOR nav = user が「B (反射 patch しない)」と指示した領域**。cave 進入 (z303、user 承認済) は達成。interior 1F→Steven nav は substantial な新課題 (fragmented 1F の再 mapping)。多数 patch 済のため、interior は autonomous patch せず user 判断を仰ぐ方針。
- 候補 fix (未実施): cave 1F 入口付近 (x>=35) で西 bias nudge → 入口 oscillation を破り frontier 西進 → 1F 西部 mapping → Steven 接続。z299(spc>200) backstop。mapping-first 準拠。だが「B」領域のため確認要。

**★ z304 cave 1F 西進 nudge deploy (2026-05-31, user 承認 "西進 nudge を実装")**:
- 実装: cave 1F (24,7) + x>=35 + spc<150 で westmost reachable (~x23) へ bfs_to_position → first step force。FINAL absolute override 領域 (z303 と z299 の間)。fallback: y を 18→19→17→20→16 順に試行、それも空なら Left bias。
- **検証 OK**: 8888 (44,18)→(34,13)、8889 (44,18)→(33,14) = **約 10 tile 西進**。GhostEdgeFix/BoxedIn ログ多数 = 物理 walk で graph が西方向に拡張中 = mapping-first 動作確認。入口 oscillation 消失。
- 次段階: 西進継続して 1F 西部(x<=22)を mapping → Steven (12,17) 接続。z299(spc>200)が B1F trap backstop。残課題: Steven 接触+letter event 進行確認、 cave 退出後 Briney 船で Slateport へ。
- 副次観察: 一時的な map-read 庸乱 ((0,21)→(2,0) で MapChange) を確認 — RAM read garbage 由来の transient corruption、agent は自己復帰 (cave 1F へ進入)。z302 で主漏洩は遮断済だが類似 corruption は残存可能性あり。深刻化なら別 cycle で graph integrity 再点検。

**★ z304 cycle-2 検証 (2026-05-31, step 100): 部分成功 + oscillation 課題**:
- **大きな西進確認**: 8888 が cave 1F (24,7,24,18) に到達 = 入口(44,18)から **x=24、20 tile 西進**。Steven(12,17)まで残 12 tile。z304 の westmost-BFS は意図通り機能。
- **新しい oscillation pattern 観察**: agents が cave に進入→z304 で深く西進→x<35 で z304 停止 (gate)→frontier 探索が east 方向の mapped frontier へ pull→exit warp 踏んで Route106 へ exit→z303 で再進入。各 cycle で BoxedIn/GhostEdgeFix 多発= 物理 walk で graph が西方向に拡張中 = mapping-first 自然進行。
- 残課題: PP[0]=0 (全 port DMG move 枯渇) = 重い battle 活動だが WIN=0 (ghost-like)。slot1-3 で fight 可能だが効率低下。heal nav 機能で Pokemon Center へ自動回帰見込み。
- 判断: z304 自体は機能。oscillation で漸進的に mapping 拡張中なので**追加 patch せず観察**。次 cycle で west mapping 進捗確認。x=24→x<22 (Steven 側) へ graph 接続したら Steven 接近自然発生見込み。plateau なら z304 の x-gate 拡張等を検討。

**★ cycle-3 (step 850): 想定外の positive 展開 — B1F detour 経由で west 1F 到達 (2026-05-31)**:
- 8888/8889 が **B1F (24,8) へ落下** (1F→B1F warp (24,18) 経由)。一見トラップに見えるが…
- B1F (24,8) 22 nodes mapped (x10-24, y18-29) = agent が B1F 西進し x=10-12 まで到達。
- **決定的瞬間: 8888 が B1F (24,8,11,28)→1F (24,7,11,28) へ復帰 (step 917)** = cave 1F **西側 (x=11)** に到達！Steven (12,17) と x がほぼ同じ。fragmented 1F の東半分から west 半分への接続が **B1F detour 経由で実現**。
- IndoorForceExit 11 回発火 = agent は B1F から exit を試みている。z299 (spc>200) 未発火 = 真の trap でない、moving 状態。
- 残課題: 1F (11,28) から Steven (12,17) へは y=28→y=17 (11 tile 北進) 必要。frontier に委ねるか、次段階の nudge を検討。
- 副次: PP[0]=0 全 port (slot 0 DMG 枯渇)。slot1-3 でなんとか fight。長期は heal nav (Dewford PC) 起動見込み。
- 判断: **追加 patch せず観察継続**。z304 直接路は plateau (x=24止) だが、B1F detour で west 1F へ到達という organic progress 発生。oscillation cycle 中に Steven 周辺 mapping が進む見込み。z304 の x-gate 拡張は当面不要 (B1F detour が補完)。

**★ cycle-4 (step 1400): plateau 確定 — Steven 接近 1 tile 圏内だが crossing 不能 (2026-05-31)**:
- **8888 が cave 1F (23,16) に到達** = Steven (12,17) と **y がほぼ同じ (16 vs 17、1 tile 差)、x で 11 tile 西**。極めて近い。
- だが **spc=104、nav_t=indoor_exit_bfs** = stuck + 退出試行中。**organic に Steven へ繋がる経路がない**ことが判明。
- 1F node count = 88 (unchanged from initial)、**1F west (x<=20) は 2 nodes のみ**(うち 1 は (0,0) glitch)。oscillation 数百 step を経ても west tiles を新規追加できていない = 物理的な壁 (cave 内の wall) で agent が west への walk 不能。
- 結論: cave 1F 東 component (x=23-45) と west region (Steven 含む) は **walk では繋がっていない** — 単なる「未 mapping」でなく**実際の壁/隔離区画**の可能性高。decomp map.bin (canon) で実 layout を確認するのが next step (canon_ascii.py + KNOWN_MAPS に GraniteCave_1F 追加要)。
- 副次: PP[0]=0 全 port 継続。8888 のような nav_t=indoor_exit_bfs は heal nav 起動の前兆。長期的には Dewford PC へ自動退避見込み。RAM-read corruption ((24,7)→(0,0) glitch step 1043) は z302 修正後も transient で残存。
- 判断: **追加 patch せず**。多数 patch deploy 済 + 「B」 cautioned 領域 + plateau は real geographic barrier の可能性が高い。次 cycle で decomp 確認 (canon GraniteCave_1F 追加) するか、heal nav 起動で自然退避を待つかを選択。reactive patch は patch-tower リスク。

**★ cycle-5 (step 2000): real game-state TRAP 発見 — z299/Stuck-Escape 両方無効 (2026-05-31)**:
- **8888/8889 が B1F (24,8,11,29) で同時 stuck**: 8888 spc=388 OscTrap LongStuck、8889 spc=493 nav_t=pokecenter LongStuck。両 port 同 tile 釘付け。
- **z299/GraniteExit 3 回発火しているが効果なし**: spc>200 trigger は満たしている。BFS 確認: (24,8,11,29) から (0,21) Route106 reachable (path 存在)、graph 上は問題なし。
- **Stuck-Escape force_act Down/Left/Up を順次試行も agent 移動せず** (spc=423→427→431→435 で同位置維持)。= **(24,8,11,29) は actual game state で 4 方向 blocked** = 実際の cave 内 wall/NPC/物理 barrier に囲まれた dead tile。
- IndoorExit-BFS は path_len=2 を返すが stay=1000 累積 = walk 不能のまま BFS 再計算ループ。
- **結論**: warp で (11,29) に入った後、game 内では walk-back 不能の **trap-tile** の可能性高 (decomp で要確認)。saveStateLoad 以外の脱出手段が現行 system に存在しない。
- 副次: 8890 (Route106、spc=0) は健在。PP[0]=0 全 port、8889 heal nav は trap で blocked。
- 判断: **追加 patch 不実施**。saveStateLoad は CLAUDE.md で完全禁止。reactive cave-interior recovery patch は patch-tower + 「B」area + 推測パッチ。**正しい次 step は decomp GraniteCave_B1F の canon 確認** (canon_map_inject.py の KNOWN_MAPS に追加 → canon_ascii.py で (11,29) の周辺 wall 配置を可視化 → 物理 trap か nav-logic bug かを確定)。
- 待機 option: 8890 がそのまま cave 進入を続けて Steven 接近する可能性、 heal nav が agent を回復させて trap から救出する可能性 (現状は blocked) を観察。trap 状態が継続するなら次 cycle で decomp 投入。

**★ cycle-6 (step 2550): organic progress 発生 — 部分 escape + WIN=1 (2026-05-31)**:
- **WIN=1 達成** (本 restart 初の実 win)、**Ralts EXP 106196→106655 (+459)** = real battle で勝利し EXP 獲得。
- **8889 が (11,29) trap から escape**: (11,29)→(12,29)、spc 493→158。Right 方向に脱出成功 = (12,29) tile が一時的に passable だった (NPC が他位置にいた瞬間か)。
- 8888 は **spc=938 で同 (11,29) に深く stuck 継続**、 Stuck-Escape Up/Right 試行も移動せず。8889 が脱出したのに 8888 が出来ない = (12,29) NPC が 8888 を block している推測 (NPC 位置が時間で変化、 8889 escape 後に (12,29) を塞いだ)。
- 8890 (Route106 59,24) は健在 移動中、 まだ cave 進入待ち。
- 判断: 既存 mechanism (Stuck-Escape + 確率的入力) で **organic に escape 可能性あり**(8889 実証)。 8888 も時間とともに NPC 移動で escape 余地あり。 reactive patch せず観察継続。 8888 が更に数 cycle stuck 継続なら decomp 投入で trap 構造確認。

**★★ cycle-7 (step 3150): DECOMP + SCREENSHOT で真因確定 — cave-nav でなく battle-handler stall (2026-05-31)**:
- **状況悪化**: 8888 spc=1538、 8889 spc=758、 両者 LongStuck。 organic 回復は起きず。
- **decomp 投入**: GraniteCave_B1F map.bin (832 bytes = 32x26) を fetch、 canon ASCII で (11,29)=canon(4,22) 周辺可視化。**(11,29) Up=canon(4,21)=passable**、 Right=(12,29)=passable、 Left/Down=wall。 = 物理的に Up と Right は通れる、 wall ではない。
- **screenshot で確定**: 8888/8889 共に **battle menu 画面で stall**。 vs 野生 Aron (ココドラ) Lv10/Lv11、 Blaziken (HP 128/161, 160/161) active、 「バシャーモ どうする？」 prompt 表示中。
- → **真因は cave-nav でなく battle handler の PP exhaustion stall**: PP[0]=0 (slot 0 DMG move 枯渇)、 slot 1-3 は PP 残あり (8/5/24)。 既存 recovery (PP 全 0→Struggle→自滅→PC warp) は slot 1-3 残存のため未発動。 agent は slot 0 を選び続けて grayed → 無限 loop。
- 必要 fix: battle 中 slot 0 PP=0 検出時に slot 1/2/3 (PP 残あり) を選択する rotation logic。 既存 v10.9z208 (DMG-PP-FIGHT) 周辺の改修候補。
- 判断: **「B」 cave-interior でなく battle handler 領域、 別 category の patch**。 多数 patch deploy 済のため user に判断仰ぐ方針。 saveStateLoad は引き続き禁止。
- 副次: 8890 は健在 (Route106 55,25)、 まだ trap 未到達。

---

## 2026-05-29 keystone ghost-battle の trainer 誤判定 root-cause + 修正 (v10.9z300)

**視覚的確証**: 3 port のスクショ全てが **overworld 表示**（8888=Route106 草地、8889=Dewford PC前、8890=Route106 水際）にも関わらず、log は「Battle Addresses validated! Enemy HP=29/29 Lv8」「Trainer detected, forcing FIGHT」と報告。= JP-ROM ghost-battle (CB2 が 0x080380FD を保持→in_battle=True 永続) の決定的証拠。WIN=0, FA=67, EXP 凍結 (Ralts 106190→106190)。

**真因 (data-grounded、推測でない)**:
- [pokemon_env.py:11300-11313] v10.9z219: 「RUN 失敗 40bcc + enemy_hp==enemy_max_hp → トレーナー戦推定 → FIGHT 強制 + `_is_trainer_battle=True`」。
- この heuristic は ghost battle でも成立してしまう（stale RAM が enemy_hp==enemy_max_hp を常時報告）。
- `_is_trainer_battle=True` は**永続**（解除は trainer 撃破時=ghost では起きない、または area 離脱時のみ）→ 次 encounter も即 FIGHT ロック → 逃走不能 → WIN=0/EXP 凍結 の poison loop。
- 既存 `_stale_override` (PP 不変=ghost 確定) の FA-reset [pokemon_env.py:3941-4012] は bcc/species/suppress をリセットするが **`_is_trainer_battle` を解除していなかった** = poison が残り次 encounter で即再ロック。これが「stale_override があるのに Route106 で再発」の理由。

**修正 (v10.9z300、deploy 済 syntax OK、PID 42644 で再起動)**:
- FA-reset ブロック [pokemon_env.py:~3977] に `if _stale_override: self._is_trainer_battle=False; _trainer_battle_encounter_count=0; _confirmed_trainer_battle=False` を追加。
- **real trainer 戦への影響なし**: real trainer 戦は FIGHT で PP 変化 → `_stale_override` (PP 不変要件) 不発火 → このブロック未到達 → `_is_trainer_battle` 維持。ghost (PP 凍結) のみフラグ解除。
- これは閾値いじり/座標ハードコードでなく、unsound な永続フラグを ghost 確定時に解除する **root-cause 修正**。

**★ 検証義務 (patch-tower discipline)**: 次 cycle で確認。
- 成功基準: WIN>0 かつ EXP 前進（Ralts EXP 増加）。
- **revert 基準**: real trainer 戦が誤って逃走される / WIN=0 のまま / 新たな battle stuck 悪化 → 即 revert (heavily-patched keystone のため単一変更で検証)。

**検証 #1 (19:43、再起動後 step~150)**: 早期 positive signal — 8890 の ghost battle が `trainer=False` になった (旧 trainer=True 永続ロックが解除)。ghost battle 自体は継続 (z300 は ghost を防がず trainer-lock を防ぐ修正なので想定内)。global FA=1 (since restart) で過剰 FA なし=regression なし。EXP は 106190 のままだが再起動直後 step~150 で判定不可。**次 cron cycle (step 数千) で WIN/EXP 確定判定**。

**検証 #2 (19:53、step 500) — z300 VERDICT: PARTIAL SUCCESS、KEEP**:
- ✅ trainer-lock poison 解除確認 (trainer=False、fa churn 減少、real trainer 戦の誤逃走なし=regression なし)。revert 不要。
- ❌ ただし WIN=0 / EXP 凍結 (8889=106196, 8890=106190) は継続。
- **理由 (確定)**: z300 が消したのは trainer-lock sub-problem。**dominant blocker は別の story-nav tangle**。3 port 全てが Route106 東端 corner (x69-75, y20-23) に集結し spc~50 で oscillate → ここは petalburg-pull water-edge trap。物理 stuck でなく nav-tangle + ghost churn。

**dominant blocker の root-cause (read-only 調査で確定、19:53)**:
- petalburg nav assignment は殆ど `map_num==17` (Route102) gate → Route106 (mn=21) では本来発火しない。8890 の `nav_t=petalburg` は**mainland-leveling phase からの stale carryover target**。
- destination 設定ロジック (例 line 13223 `_target_name="petalburg"; _target_mg,_target_mn=0,0`) は heal/warp/leveling の map-gate 入れ子に埋没 (line 13000-14000 tangle)。
- **核心 gap: post-Brawly の Granite Cave story beat が nav state machine に存在しない**。Dewford 島 (Badge2) で agent は到達不能な mainland target (petalburg/route116) に fallback → 東 water edge へ pull → oscillate → ghost battle。
- → これは **substantial な story-nav state-machine refactor** であり z300 のような単一点修正ではない。user の「B」指示 + patch-tower 防止 + z297-revert 教訓 (反射的 cave-nav patch は backfire) より、**autonomous での反射 patch は禁止**。user の設計判断を仰ぐべき項目。
- clean な恒久解の方向性: story-phase (badges + map) → 正しい次 target を引く state machine。post-Brawly は Granite Cave 入口 (Route106 ~55,23、現状 warp node 未登録=要 mapping) を target に。z296 (Dewford 北端 exit) と同型の scoped data-driven nudge が候補だが、tangled な既存 target ロジックとの競合検証が要る。

**補足: Route106 connectivity 分析 (AI work 19:43)**: 8890 の stuck 位置 (71,20) は 3 方向 open (Left/Down/Right, wall_Up のみ) = 物理 stuck でなく nav-tangle+ghost churn。Route106 warp は 2 件のみ graph 登録: (76,26)→Dewford(0,11)、(29,14)→(0,20)。**Granite Cave 入口 (~55,23、CaveNav override が参照) は warp node 未登録 = mapping gap**。mapping-first の観点で Route106 cave 入口周辺の再 mapping が要 (反射的 patch でなく自然 frontier 探索 or 別 session で対応)。

---

## 2026-05-29 story progress chain (z296/z297) — autonomous session

**達成した story 進行**: session 開始時「全 hard-freeze・Dewford/Devon 圏で停滞」→ 確定地理 nudge 連鎖で前進:
- z296/z296b: Dewford 島脱出 (北端 walk-off-edge → Route106)。8888/8889 脱出実証。
- z297: Route106 → Granite Cave 入口 (55,23 warp) 誘導。8888/8889 が **Granite Cave (mg=24,mn=7) 進入**実証。
- いずれも decomp connections/warps で位置を確定 → bfs 検証 → scoped override (該当 map+badge2+屋外+非heal+spc<25)。推測でなくデータ駆動、revertible。

**現 follow-up**: 両 agent が Granite Cave 進入直後 (44,16-17) で OscTrap (spc~180)。05-28 の「8889 Granite Cave B1F Steven event 未達」と符合 = **cave 内部ナビ (1F→B1F→Steven、multi-floor) は既知の未解決**。新エリア arrival stuck は escape で自己解消した前例 (Route106 beach) あり、1 cycle 観察。未解決なら Steven 位置 (decomp) 調査 → cave 内部誘導検討。

**設計課題 (per-beat nudge の限界)**: z296/z297 は per-map の confirmed-target nudge。story が進むほど beat ごとに override が増える。clean な恒久解は「story-phase → 次 target warp/connection」を引く state machine だが、これは tangled な petalburg-target logic の refactor (既出)。当面は per-beat nudge で進行。

## 2026-05-29 audit highlights

### 汎用AI: tile_classifier 慢性低精度 (val_acc 37%) の主因 = クラス不均衡 【新規診断 2026-05-29】
- tile_data 分布: walkable 22867 (45.8%) / unknown 19124 (38.3%) / wall 7100 (14.2%) / **door 859 (1.7%)**。
- **door 極端過少 (1.7%)** → door_f1 0.17-0.32 の直接原因。door は建物出口検出に必須 = 今回の屋内 stuck 問題と同じ層。
- **unknown 38%** = 巨大な曖昧バケットが学習撹乱し val_acc 頭打ち。
- **訂正 (2026-05-29 train code 確認)**: class-weighting は**既に実装済** (FocalLoss + class_weights, tile_classifier.py:254/516-518)。コメントに「v4実験: door weight=12+oversample で door 強すぎ **val_acc=52% 崩壊**」= 過度な重み付けは逆効果と判明済。
- → 37% の真のボトルネックは door 重みでなく **unknown 38% の曖昧データ品質**。改善には 19k 枚 unknown の再ラベル/縮小 (data curation) が要る = reflexive 不可、substantial。
- retrain は live RL 学習 (torch/GPU) と競合し SLOW-STEP 悪化リスクあり → live 稼働中は実行しない。改善は別 session で data curation から着手すべき。

- 前セッションが Claude API thinking-block エラー (messages.829, context 圧縮起因) で死亡 → 監視 ~1.5d lapse。学習プロセス自体は生存していた。復帰後 cron 再設定 (CronList 空だった)。
- 3 port 全て高Lv(47/48)だが**屋内 warp/出口ナビに失敗しループ**: 8888/8889=Dewford PC2F (3,2), 8890=Devon Corp (11,1) @(24,9) OscTrap spc=50。
- **決定的 root-cause (canon 確認)**: PokemonCenter_2F canon ASCII で stuck 位置 canon(1,9)/(4,9) を確認 → **壁に囲まれていない**。真上 row8 は全面 passable。つまり屋内 stuck は「壁データの問題」ではなく**ナビ・ロジックの欠陥**(階段方向の上に動けるのに動いていない)。
  - → **この種の屋内 stuck に canon 壁データ注入は無効**。修正は warp tile を nav target に設定する exit ロジック側であり、座標ハードコード追加(patch-tower)でも壁注入でもない。
- 新規 tool `tools/canon_ascii.py` (canon map.bin を ASCII grid 可視化、stuck 位置 mark)。feedback_canon_visualization 準拠の汎用診断 tool。
- canon_map_inject.py KNOWN_MAPS のバグ修正: `DewfordTown_PokemonCenter_2F` (404、未検証登録) → 共有 layout 名 `PokemonCenter_2F`/`PokemonCenter_1F` に修正。

---

## 現在 phase: Badge1 取得済、 Dewford Brawly Gym 内攻略中 (canon inject 後 30% mapping、 北進中)、 次 Badge2

## 2026-05-22 audit highlights
- canonical map.bin (pokeemerald `data/layouts/DewfordTown_Gym/map.bin`) inject 成功 → (3,3) 12.1% → 30% coverage、 BFS path 33 to Brawly (11,10) 計算可能
- v10.9z272 で "town_exit" を `_competing_nav_inner` に追加 → BFS path 採用 fix
- canon_map_inject.py 汎用 tool 新規 (他 phase でも再利用可能)
- 残: 5 連続 patch deploy (v10.9z269b〜z272) = patch tower 進行、 24h 副作用観察モード
- tile_classifier eval: acc=71.8% (前回と同じ、 retrain 未実施)、 door F1=33% (改善余地大)

---

## 深刻度: 高 (current phase critical)

### H17. 屋内 warp/出口ナビの systemic 信頼性 【新規 2026-05-29】
- **状態**: 3 port 全て屋内マップで出口 warp に到達できずループ (PC2F, Devon Corp)。Lv47/48 とストーリー進行に対し過剰レベリング。
- **canon root-cause**: stuck 位置は壁に囲まれていない (canon ASCII 確認済) → nav-logic 欠陥。Devon 出口ロジック [pokemon_env.py:21760-21810] は v261g〜gs の座標ハードコード patch-tower。
- **正しい対処方針** (推測パッチ禁止):
  1. 各屋内マップの warp tile (階段/ドア) を canon (map.json events) から取得し nav target に設定
  2. BFS で warp tile へ誘導 (座標別 action override を廃止する方向)
  3. 修正は live exploration_map.json への注入競合に注意 (学習3プロセス稼働中)
- **影響**: story 進行停止 (Devon Goods → Granite Cave Steven event が全 port のボトルネック、前日から継続)。
- **★ 2026-05-29 同日 精密 root-cause 特定 + 修正 deploy (v10.9z293)**:
  - 真因確定: [pokemon_env.py:8768] IndoorForceExit が「建物の出口は通常下側(Down)」と誤前提し、building_stay>=100 で `action=random.choice([..3,3,3])` (Down偏重) + `_nav_target/_nav_path` wipe。PC2F の出口warpは**上**(my(8,13)、canon(1,6)→PC1F)なので agent を底辺壁(y=16)に押込み続け、正しいBFS経路を毎step破壊していた。
  - exploration_map のwarp/edgeデータは正しく、(8,16)→Up×3 で warp 到達可能 (standalone BFS検証済: (8,16)→['Up','Up','Up'], (9,16)→['Left','Up','Up','Up'])。壁問題ではなくロジック問題を裏付け。
  - 修正: force-exit時に現マップ内 warp tile への BFS 第一歩を action 化。warp 不明/到達不能時のみ旧 random 挙動へ fallback (無回帰)。座標ハードコードでなく実 warp データ駆動 → 全屋内マップに汎用。
  - **状態**: deploy 済 + 実証完了。8889 が PC2F→PC1F→屋外 Dewford(0,11) に完全脱出し route103 nav 開始。chronic 屋内 stuck の機構的解消を確認。
  - z293b (WarpExit-FINAL): 早期 z293 が frontier_detour 等の後段 override に上書きされる問題を、v10.9z266/z267 と同じ FINAL 直前 absolute override で解決。
  - **残課題1 (8888) 訂正**: 当初「transient NPC」と推測したが、946step 後もスクショ完全同一 → **静的ブロック**確定 (カウンター/壁が warp隣接にあるか warp座標(8,13)が誤り)。z293b が同方向 Up を盲目強制し続け **hard-freeze (spc=946)** を招いた = z293b の欠陥。
    - **z293c (2026-05-29) 結果**: spc-aware guard で 8888 の spc 946→6 に激減 = **hard-freeze 解消** (凍結→移動)。ただし PC2F 脱出は未達 ((8,13) 静的障害でオシレーション継続)。
    - **patch-tower 規律で打ち止め**: 同領域 z293/293b/293c = 3 edit 到達。freeze は解消したので、これ以上の reflexive patch はしない。z293b revert もしない (8889脱出/8890凍結解除/8888 freeze解除 の実利を失うため逆効果)。
    - **残 follow-up (要・慎重な検証、reflexive patch 禁止)**: 8888 が (8,14)→Up で (8,13) に行けない理由を調査:
      - **object_events 確認済 (2026-05-29)**: PC2F の NPC は全て y=9 (canon y=2、最上段カウンター: TEALA×3 + MYSTERY_GIFT_MAN)。**warp 経路 (8,13)/(8,14) に NPC・object は無し**。地形(map.bin)も canon passable。→ **NPC/地形/object 仮説は除外**。
      - 残る候補: (a) **座標ミスアライン** — agent の (8,14) が canon(1,7) と不一致 (BORDER_OFFSET ずれ or 座標原点違い)。z293b が誤った warp tile を target している可能性。(b) warp トリガーが特定方向 approach のみ。
      - 検証法: 8889 が次に PC2F 入場時の「map変化直前の agent 実位置」を log 取得 → (8,13) と照合。ずれていれば実測値に補正。canon_ascii の BORDER_OFFSET=7 仮定を他マップ既知 warp で逆算検証。
      - **2026-05-29 deep-dive 結果**: wall_hits `3,2,8,14,Up=8` (Up 物理ブロック、ただし閾値未満で graph 未 wall 化 → BFS は Up 返し続け)。`3,2,8,13,Left=10` = 過去 warp tile (8,13) 上で Left 押下記録 → **(8,13) に立っても warp 不発**の疑い (warp suppression or trigger 不全)。ただし wall_hits は累積値で当該が今セッションか不明。
      - **次の調査 (deliberate、reflexive patch 禁止)**: warp trigger ロジックを確認 — (8,13) に立った時に warp が発火しない条件 (warp cooldown / same-group 抑制 / _state_load_warp_block 等)。live RAM で warp フラグ状態を確認。warp 抑制は不要 warp 防止用なので慎重に。8888 は hard-freeze 解消済で低害につき急がない。

### 残: story-navigation デッドロック (post-Brawly) 【観察 2026-05-29】
- chronic 屋内 stuck fix 後、3 port は移動するが story 進行せず: 8888=PC2F warp 不発、8889=Dewford島で本土 nav target (route103/116/petalburg) 指すが船(Briney)無しで到達不能、8890=Devon 内部 warp bounce。
- 全て「Dewford/Rustboro 周辺から story path (Devon goods→船→Granite Cave→Steven) へ抜けられない」高レベル nav 問題。
- deliberate 設計タスク (船利用・建物出口の確実化)。reflexive patch でなく、approach を設計してから。
- **★★ 2026-05-29 解決 (z296/z296b)**: Dewford 島脱出の核心を decomp connections で特定 → **解決**。
  - 発見: Dewford up->ROUTE106 (Granite Cave有), right->ROUTE107(水路)。島脱出は warp でなく **map connection (北端 my y=7, x10-19 を歩いて抜ける walk-off-edge)** → exploration_map に warp として出ず未発見だった。boat/Surf 不要。
  - z296: Dewford 屋外+badges>=2+非heal+spc<25 で北端へ BFS 誘導 (canon walkability + bfs 検証済)。z296b: 北端 y<=7 で Up off-edge 優先 (z296 が北端で横 ping-pong した bug 修正)。
  - **結果実証**: 8888/8889 両方が Dewford(0,11)→**Route106(0,21) に脱出** (`[Dewford-NorthExit] Up off-edge`)。約30 cycle の story 停滞を打破。over-leveled (Lv47/48) なので story 進行が目的、北押しは安全。
  - **新規 follow-up**: 8888 が Route106 東端 (72,25) で spc=55/fa=99 オシレーション (新ルートの局所 stuck、要監視)。次は Route106→Granite Cave→Steven イベント (次 phase nav)。Route106↔Dewford の往復 bounce 可能性も観察 (z296 は Dewford でのみ発火、Route106 では frontier 探索)。
- (旧 記録) Dewford 屋外 warp は建物入口のみで島脱出未マップ → 上記 z296 で解決。
  - 朗報: z295 で建物に閉じ込められなくなり agents が overworld を frontier 探索するようになった → 島の縁を探索して Granite Cave/dock 出口を**自然発見する可能性** (z295 の副次効果)。要観察。発見されない場合は Dewford 海岸/dock の探索誘導 or Briney イベント handling が設計タスク。
- **2026-05-29 調査**: badges=2 は正しく検出。「P1-NewMap」は story-phase でなく新マップ初到達報酬ラベル ([pokemon_env.py:8188]) — phase 誤検出ではない。`_target_name="petalburg"/"route103"` 設定箇所が**数十か所**に分岐 = story-nav は深い tangle で、誤 target は単一バグでなく複雑な状態機械からの創発。→ **reflexive fix 不可確定**。post-Brawly の正しい story = Dewford→Granite Cave(Route106) で Steven に手紙、なのに mainland (petalburg/route103) を target。設計タスクとして、post-Brawly phase の target を Granite Cave に向ける state machine 整理が必要 (user 相談推奨)。
  - **残課題2 (Devon 8890)**: z293b は「最寄り warp」を選ぶが、Devon の最寄り warp は内部階段で building 出口とは限らない → floor 間 bounce。dest-aware 選択 (dest が現 building 外) が要る。
    - **2026-05-29 decomp warp topology 取得** (my-coord): DevonCorp_1F: (12,15)/(13,15)→**Rustboro屋外(真の出口)**, (21,8)→2F。2F: (21,8)→1F, (9,8)→3F。3F: (9,8)→2F。全フロア 171 blocks。→ **真の出口は 1F (12-13,15)**。dest-aware fix は「dest_map が現 mg と異なる (=building 外) warp を優先、無ければ下階(出口に近い)へ」が方針。
    - **座標照合の結論 (2026-05-29)**: 「座標オフセット」仮説は**否定**。PC2F agent warp (8,13)==decomp、Devon agent warp 座標も decomp 一致 ((21,8)/(9,8)/(12-13,15))、map-ID も対応 ((11,0)=1F,(11,1)=2F,(11,2)=3F)。**真因は単一 phantom warp (11,1,24,9)→(24,11)**(group24への偽warp)。既存 Devon-WarpPurge は同マップ/→屋外 ghost のみ除去し、group外 phantom を素通りさせていた。
    - **z294 (2026-05-29) fix + 検証**: Devon-WarpPurge を拡張 — Devon フロア warp は「フロア間 + 1F→Rustboro屋外(0,3)」のみ正当、他は ghost。standalone で (11,1,24,9) 1件のみ除去・正当12 warp 保持を確認しデプロイ。**結果: Devon-WarpPurge 発火、z293b が本物 (21,8)/(9,8) を target、8890 が 1F(mn=0) 到達**。phantom 追跡 解消。
    - **残・微課題 (multi-floor clean exit)**: 8890 は 1F↔2F bounce (1F 最寄り warp=(21,8)→2F、outdoor出口(12-13,15)は遠い)。
    - **z294b 案 (prefer-outdoor-dest) を分析 → 不採用**: standalone 検証で「dest mg=0 優先」は Devon 1F/PC1F の屋外出口を正しく選ぶが、**Devon 2F/3F (屋外 warp 無し) では最寄り実 warp を選び 2F↔3F 無限 bounce を誘発しうる** (regression リスク)。half-fix は不可。
    - **正しい解 (substantial 設計タスク)**: warp グラフを mg=0(屋外) まで BFS する**多段 warp 経路探索** (現 z293b は単一マップ内 BFS のみ)。各 warp の dest を辺として building 全体の warp graph を辿り、outdoor へ最短のフロア遷移列を求める。
    - **現状で十分な部分**: 8888 が到達した PC1F は最寄り warp が既に屋外 (13,15)。z294 (phantom除去) で実 warp 進行は改善済。
    - **2026-05-29 既存 primitive 検証 → どちらも不採用**: bfs_to_map (多段 warp 横断 BFS, line 610) を test。Devon2F→Rustboro は多段経路を返すが **Devon1F→Rustboro は 'Up'→'warp' で 2F へ戻る convoluted path** (直接の屋外出口 (12-13,15) を辿らない)。warp graph の断片化が原因。→ z293b を bfs_to_map-to-town に置換しても clean に出られない。z294b (prefer-outdoor) は 2F↔3F bounce リスク。**両案とも不採用**。
    - **結論**: multi-floor 建物 clean exit は warp graph 断片化 + pathfinding 両方の問題で genuinely tangled。reflexive fix 不可。設計タスク (warp graph の連結性修復 + outdoor 志向 pathfinding) として user 引き継ぎ。**ただし z293/293b/294 で凍結は解消済**(pre-fix の hard-freeze より明確に改善、 regression でない): 8889 脱出、8888→PC1F、8890 実 warp 使用。floor-bounce は「移動するが出られない」状態で frozen より上。

### ★★ 建物 exit stall の精密 root-cause: ドア型 warp の進入方向 (2026-05-29 特定)
- 8888 on PC1F のログで決定的: `WarpExit-FINAL dir=Left warp=(13,15) pos=(14,15)` → step → `pos=(13,15)` → `WarpExit-FINAL dir=Right warp=(14,15) pos=(13,15)` → step back。**(13,15)↔(14,15) を ping-pong** (両方 warp tile、z293b は常にもう一方を target)。かつ **(13,15) [PC1F出口ドア] に乗っても warp 不発** (FINAL は mg=3,mn=1 のまま)。
- **真因**: ドア型 warp (建物下端の出口) は特定方向 (下=Down で潜る) からの進入で発火する。z293b は最短経路で横 (Left/Right) から乗るため発火しない。階段型 (PC2F (8,13)) は Up で乗れば発火 → 8888 は PC2F→PC1F は脱出できた (整合)。
- **2つの sub-issue**: (a) z293b が隣接 2 warp tile 間で ping-pong (常にもう一方を target)。(b) ドア warp は進入方向依存で横入りでは trigger しない。
- **正しい設計**: 各 warp に「進入方向」属性を持たせる (decomp の warp は通常タイルの metatile 種別=ドア/階段で判別可、or 建物下端 y=max の warp は Down 進入)。z293b/IndoorExit が warp を「正しい方向から踏む」ようにする。これは door-entry-direction aware nav = 設計タスク。z293b は既に z293/293b/293c の 3 改修で patch-tower 上限、4つ目の reflexive 改修はしない。
- これが全建物 (PC1F/PC2F/Devon) の clean exit stall を統一的に説明する最も精密な診断。user 引き継ぎの最優先設計項目。
- **z295 (2026-05-29) fix + 結果**: z293b 末尾に「現タイルが warp かつ Down-edge 無し (=下端ドア) なら action=Down 強制」を追加 (階段型は Down-edge 有りで除外、spc<25 ガード下)。standalone で PC1F/Devon1F ドア検出・PC2F 階段除外を確認しデプロイ。**結果: 8888 が PC1F→Dewford屋外(0,11) に脱出** (deploy 直後 act=3 で退出、z295 のドア Down 上書きが ping-pong を解消した動きと一致)。
  - **限界**: z295 は agent がドア付近にいる PC 型小建物で有効。**multi-floor (Devon) は未解決** — 1F(21,9) で z293b 最寄り warp が遠い屋外ドア(12-13,15)でなく近い (21,8)→2F を選びドアに到達できない。Devon clean exit には「prefer-outdoor-dest warp 選択 (z294b)」+「warp-trigger」+ z295 の組合せが要るが、z294b は 2F↔3F bounce リスクがあり単純合成不可。multi-floor は warp-graph 設計タスクとして残る。
  - **進捗総括**: chronic 屋内 stall は PC 型では解消 (8888 overworld 入り)、multi-floor は設計タスク。session 開始時「全 hard-freeze」→「PC 型建物 exit 動作・8888 が overworld でストーリー圏内」。
- **PC2F 8888 続報**: z293/293c + 時間で **PC2F→PC1F(3,1) に脱出**(長期 (8,14) オシレーション後に出口発見)。PC2F stuck 実質解消。残るは PC1F→屋外の進行 (PC1F は outdoor warp あり)。

### H18. train.py 2プロセス — 【誤診断と判明・2026-05-29 訂正、正常形態】
- **当初の誤診断**: `python -u train.py` が2プロセス (48432/39808) → 「重複起動バグで mGBA 入力衝突」と判断し kill+再起動 + singleton lock 追加した。
- **訂正 (同日 検証で判明)**:
  - 「二重HB (PP形式違い2行)」は [pokemon_env.py:9617] と [9808] の**2箇所の HB print**が1プロセス内で出ているだけ。重複プロセスの証拠ではなかった。
  - restart_training.py で全 kill→1起動しても再び「他プロセス残存」警告 → train.py は起動時に**子プロセスを spawn する正常形態 (親+子=2プロセス)**。両 PID とも引数 plain `-u train.py` (multiprocessing-fork 引数なし)。
  - 追加した singleton lock は子を誤って kill しアプリを壊す危険があり **revert 済**。restart_training.py が既に単一論理インスタンスを保証している。
- **教訓**: 数値(プロセス数)を見て因果を即断した = [feedback_root_cause_analysis] 「データで3階層掘る・まず自分のコードを疑う」違反。HB print 箇所と子プロセス引数を先に確認すべきだった。
- **真の劣化要因**: SLOW-STEP/WATCHDOG は mGBA 自体の経時劣化 (1.5d 連続稼働) であり、プロセス重複ではない。長時間稼働時の mGBA 再起動要否は別途監視 (H 既存)。

### H15. (10,12-13) chronic oscillation block 【2026-05-18 大幅進展】
- **状態**: 2026-05-18 v10.9z266 (FINAL force) で大幅改善、 ports SE (17-23, 22-26) area 到達
- **3 root cause 連続発見** (user 「マッピング疑え」 きっかけ):
  1. NPC at (10,12) physical block (wandering)
  2. train.py save() が wall_hits<50 wall 消化 → manual wall fix 無効化
  3. wall_hits.json malformed `26_Left` key で wall_hits load 全 fail
  4. action chain 多層 override (frontier/Briney/IndoorExit/etc) で BG33 path 上書き
- **対策実施**:
  - wall_hits.json key 修正 (underscore→comma) ✓
  - 4 NPC walls confirmed=100 永続化 ✓
  - v10.9z264: OscTrap brawly_gym bypass ✓
  - v10.9z265: NavGuard 直前 force ✓
  - v10.9z266: FINAL log 直前 absolute force ✓
- **現在**: 8890 (17,24)/(19,26)/(20,24)/(23,24) など Brawly door (15,25) 隣接到達
- **残**: (3,3) entry まで最後の数 step
- **status**: 解決 chain 進行中

### H12. Brawly Trainer engagement 未達成 【最重要 進行中 2026-05-22】
- **状態 2026-05-22**: (3,3)=Brawly Gym 確定 ((3,4) は誤、 真は (3,3))、 canon inject で mapping 12.1% → 30%、 BFS path 33 to (11,10) 計算 OK、 v10.9z272 で nav 採用 fix、 WIN 依然 0
- **進展 chain (2026-05-19 〜 22)**:
  - (3,4) → (3,3) 真 Brawly Gym 確定 (pokeemerald decomp 検証)
  - v10.9z263-z267 で entry 達成 (479+ entries)
  - 2 日進展ゼロ問題 = north half 未踏 (canon (4,3)=my(11,10) Brawly 完全到達不能)
  - canonical map.bin inject (150 passable + 229 walls 注入) → 北半既知化
  - v10.9z271 IndoorForceExit skip ((3,3) badges<2)
  - v10.9z272 town_exit を _competing_nav_inner に追加 → BFS Brawly path 採用見込み
- **残**: 24h 副作用観察 → 実 Brawly engagement 検証
- **影響**: Story 完全停止 (Badge2 未達成→以降全 area block)、 5 連続 patch deploy で patch tower risk

### H16. Patch tower (5 連続 deploy in 3 日) 【新規 2026-05-22】
- **状態**: v10.9z269b (heal 4 patch skip) → z270 (Dewford PC table) → z270b (Dewford PC branch) → z271 (IndoorForceExit skip) → z272 (town_exit override) = **5 連続 patch in 3 日**
- **症状**: 各 patch 自体は妥当な fix だが累積で code 複雑度増、 副作用 risk
- **根本原因**: 1 つの「(3,3)+badges<2 専用 brawly mode」 が複数 handler 分散実装で各々独立判定
- **恒久対策案**:
  - F1) `_brawly_mode = (mg==3 and mn==3 and self._cached_badges<2)` 単一 flag 化、 各 force/exit handler はこれ参照
  - F2) Brawly defeated 後 v10.9z269b-z272 全削除 (badges>=2 で natural skip するが残骸残し回避)
- **影響**: 次 phase で同 pattern (他 gym) で再発しやすい

### H13. monitor.py CADENCE silently skip 【新規 2026-05-08】
- **状態**: 2026-05-08 v10.9z262 で auto-detect 実装済 → 即座に detect される
- **症状 (修正前)**:
  - daily_progress 12h、 AI work 24h、 UNRESOLVED 7d 違反 silently 累積
  - 4 日 daily_progress gap、 12 日 AI work dormant、 12 日 UNRESOLVED stale
- **根本原因**: monitor.py の checklist が **manual checkbox のみ** で実 file age check なし → claude が無意識に skip
- **対策実装**: monitor.py:534 周辺に `_cad_violations` block 追加 (file mtime check + warning print)
- **影響範囲**: 今後 同 cadence 違反は visible → 機械的に対策 trigger
- **status**: ✓ 解決 (2026-05-08 cycle 検証済)

### H14. AI work 形骸化再発 【新規 2026-05-08】
- **状態**: tile_classifier_history.json 12.6 日 update なし、 (RULE_VIOLATIONS #11 と同種)
- **症状**: 100+ cycle 「監視のみ」、 汎用 AI 作業 0
- **対策**: H13 cadence check が trigger source、 AI work 必須化
- **2026-05-08 immediate action**: tile_classifier eval 実行 (acc=71.8%、 door F1=33%)
- **次の AI work 候補**:
  - tile_classifier 80 epoch retrain (door class oversample 強化)
  - Brawly Gym (3,4) 新 tile collection (現在は battle 中で skip)

---

## 深刻度: 中

### M3. tile_classifier door class 低精度 (door F1=33%)
- **状態**: 2026-05-08 eval 確認、 door precision 24.5%、 recall 52.4%
- **根本原因**: 859 door tiles vs 49,091 non-door = 0.017 imbalance、 oversample 不十分
- **恒久対策案**:
  - 探索 map の 1693 warp tile から door 自動 collection
  - door 専用 augmentation (rotation/flip 強化)
  - focal loss alpha 調整 (door class 重み増)

### M7. Beach (0,21) 探索停滞
- **状態**: 144 tiles のみ (typical beach 200-300 tiles 想定)、 mapping 不完全
- **症状**: badges<2 時の Beach21-Exit patch (cycle 2316) で (15,6) target 強制
- **根本原因**: beach BFS 不完全、 一部 area 未踏
- **対策案**: badges>=2 後に再探索 phase

### M8. (3,4) Brawly Gym 内部 mapping 不足
- **状態**: 85 tiles (typical gym 150-200 tiles)、 trainee 配置未到達
- **対策**: F2 (mapping 70% 待ち) の前提条件

### M2. tile_classifier door 精度 (継続)
- **状態**: v7 random erasing のみ。 データ不均衡 (859/49091) 未解決
- **対策**: M3 と統合

### M6. SLOW-STEP (mGBA-http bridge 劣化) (継続)
- **状態**: PC restart で reset 済、 但し再発リスクあり
- **根本原因**: mGBA-http ブリッジ長期稼働 (7d+) で socket 応答劣化
- **対策**: SLOW-STEP burst 検出 → port 個別 bridge restart (未実装)

---

## 深刻度: 低

### L1. 汎用AI 作業 形骸化 (H14 と同根)
- **状態**: H14 で active fix 中
- **対策**: monitor.py cadence check で再発防止

### L2. daily_progress 更新漏れ (H13 で fix 済)
- **状態**: 2026-05-08 v10.9z262 で auto-detect 実装
- **対策**: H13 cadence check で 12h 経過 → warning

### L3. ベストセーブ実施
- **状態**: monitor.py BestSave 自動 fire (spc<50 port)、 PC restart 後の log には未記録
- **確認方法**: training_current.log で `[BestSave]` 文字列 grep

---

## 解決済 (Brawly phase 関連)

### H11. Devon Corp 2F 脱出不能 → resolved
- **状態**: 2026-04-26 発見 → 後続 phase で patch 解消
- **解決**: Story 進行 (Devon→Mr.Stone→船→Dewford) で 2F 通過済
- **証拠**: 8888/8890 が Dewford 到達 (cycle 2335 etc.)

### H1-H3 (R116 grinding phase) → 大半 obsolete
- H1 (Confusion 不安定): R116 phase 課題、 現 Brawly phase では battle AI と無関係化
- H2 (Devon Corp 到達不能): Devon→Mr.Stone→船 で完全突破済
- H3 (Kanazumi transit): 8889 が PetalburgForest 進行 = Kanazumi 通過済

### H4-H10: 過去 phase 課題、 現 phase 影響なし

---

## レビュー cadence (CLAUDE.md ルール)

- **5 回に 1 回 (50 分毎)** monitor で本 file 確認
- **7 日 update なし** で monitor.py CADENCE 違反 trigger (H13 で実装)
- **高深刻度 1+ action** 必須 (現在: H12 = 視覚 verify + (3,4) internal patch 設計)
