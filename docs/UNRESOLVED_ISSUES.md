# 未解決問題・後回し・その場しのぎの一覧

最終更新: 2026-05-22 (前回 2026-05-08 から 14 日 stale → user audit 指摘で全面 review)

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
