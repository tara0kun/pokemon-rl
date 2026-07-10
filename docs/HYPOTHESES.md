# HYPOTHESES — 未解決問題と反証実験(上から順に実行)

> Last verified: 2026-07-06。各仮説に「これを実行してこうなれば棄却」を付す。
> 実行前に RESUME.md で現在地を確認。診断の第一手は常に `logs/decisions_*.jsonl` の grep。

## H1. Dewford Town で Gym door 手前振動 — ✅ RESOLVED (2026-07-06)

**症状(だった)**: agent が (6-7,18-19) で振動し、door approach (8,18) → door (8,17) の最終ステップに乗れない。mapbfs の dist が 2→4 に増える。

**真因(実測で確定・2 バグ複合)**:
1. **`map_data.warp_step_direction` の中点ヒューリスティックが下半分の建物ドアで誤判定。** door (8,17) は height=20 の下半分にあるため `y*2>height→Down` で **"Down" を返し、door タイルに乗った瞬間に後退**させていた。全 248 キャッシュマップ・490 interior warp 監査 → **127 warp で誤り(曖昧さゼロ)**。中点(マップ上の位置)でなく **collision(歩ける接近側→塞がれたドア側)** から方向を導くルールに置換。
2. **`claude_heuristic.py:394` の walkable ゲートで、非歩行 door タイル (8,17) に乗った瞬間 goal ブロック全体が沈黙。** BFS は非歩行 start で None、(8,17) は target_tiles({(8,18)})に無いため「もう一度 Up」を出すパスが構造的に不在 → wander でドアから降りる。**goal warp タイル上なら warp_step を出す分岐 (`mapbfs_warp_on`)** を追加。

**検証**: (a) 制御タップで (8,19)→Up→(8,18)→Up→(8,17)→Up→**map 3-3 warp** 確認。(b) `warp_step_direction(0,11,8,17)` が "Down"→"Up"。(c) 合成 MapInfo の unit test 6 件追加、計 55 PASS。(d) **live 走行でエージェントが Gym 迷路突破 → Brawly (4,3) 到達・戦闘起動**(振動再現せず)。H1a/H1b/H1c/H1d は主因でなく棄却(warp_tiles_for は正しく (8,18) を返していた)。

> **Fable との比較(user 依頼)**: Opus の診断を伏せて Fable に独立診断させ 2 バグとも完全収束。Fable の全マップ監査提案(当初 62 flip)を Opus が独立再現し 127 flip に拡大確認。collision 由来ルールへの一般化は Fable 案採用。

## H6. バトルAIが勝てる gym 戦を落とす(2026-07-06 表面化 / 07-07 大幅改善)

**当初症状**: Grovyle L24 で Brawly に挑むも Machop 1体倒す前に気絶、控え L3-L7 で詰み。

- **H6a: opening で交代用シーケンスが戦闘メニューに誤発火 — ✅ FIXED (07-07)**
  `battle_move_queue` の補充が `screen_signals["battle_menu"]` vision 依存で、未検出時に `party_seq`(交代画面用の A/B/Down)に落ち、開幕の FIGHT メニューで空打ち → Grovyle が反撃前に削られ気絶。
  **修正 (claude_heuristic.py Part B, "driven by RAM not vision")**: active battler HP(`battle_moves.active_hp`, gBattleMons[0])で「自分の番」と「lead 気絶→交代」を判別。自分の番は best_move を選択(vision 不要で RAM から技を読む)。**`move_select_sequence(0)` は Right/Down nav が無く安全なので vision 無しでも発火**、slot 1-3 のみ vision 確認時に限定(Fable review F1)。敵気絶遷移は enemy_hp==0 かつ FIGHT メニュー非表示時に `B`(SHIFT の「交代する?」を NO、勝利/送り出しテキスト送り、ネスト party メニューを1段戻す)。非 in_battle で queue を flush(F2)。
  **検証 (07-07 live)**: 改善前は Machop 1体で気絶 → 改善後は **Machop + Meditite の2体を撃破**(Grovyle 66→37、敵 HP 単調減少を enemy_hp で確認)。
- **付随: gym leader nav — ✅ FIXED (07-07)**。goal target_pos(Brawly (4,3))を常に interact_target 化し歩行可能な隣接タイルへ誘導 + 接近ゾーンを empirical_blocked から免除(face+A の bump が tile_map を汚染し (4,4)/(3,3) が壁化 → 次 run で Brawly 到達不能になっていた)。live で迷路突破 → Brawly 到達 → face+A で戦闘起動を確認。
- **H6b: Grovyle 単騎では Brawly の3体を sweep できない — ⚠️ CONFIRMED (07-07、残 blocker)**
  Brawly = Machop L16 + Meditite L16 + **Makuhita L19(Bulk Up + Vital Throw 70)**。Grovyle L24 は最初の2体を倒せるが、消耗した状態で Makuhita L19 に力尽きる(neutral 相性同士の attrition)。控えは L3-L7 で詰み → whiteout。
  **07-07 追加調査で quick win を全て潰した**:
  - bag に回復アイテム **ゼロ**(唯一の item id=183 は非回復)→ 戦闘中 Potion 案 (c) は不可。
  - `screen_features.battle_menu_open()` は実 FIGHT メニュー(brawly_battle/post_brawly のスクショ)を**正しく検出**、`A@rem0` は dialogue 中の正常なテキスト送り → **vision も battle ロジックも妥当**。play の粗さでなく火力不足が真因。
  - Grovyle L24 の技は [Pound40, Leer0, Pursuit40, QuickAttack98=40] で全て ≤40・格闘に等倍。**Grovyle は L26 で Leaf Blade(70威力 STAB)を習得** → Makuhita を約2撃で落とせる。**grind 目標 = L26(Leaf Blade 獲得)** が明確な payoff。
  - 自律 heal 機構は **未実装**(`record_healing` は HP 上昇の記録のみ、PC で nurse に話す heal アクションは無い)。
  **次セッションの実装計画(focused)**:
  1. **heal-loop**: party0_hp_frac < ~0.4 で最寄り PC(Dewford は (2,10)→DewfordTownPokemonCenter1F)へ route → 内部で nurse(PC 上部)へ walk → 顔向け+A → dialogue を A で確定 → 退出。一般に有用(grind 以外でも whiteout 回避)。
  2. **grind gate**: `dewford_gym_brawly` を `party0_level >= 26` で gate。未満は探索(reward_pick)で野生戦 → leveling。低レベル野生は XP 効率悪いので Granite Cave 等の grind area を明示 target にするとより速い。
  3. 検証: savestate_dewford から走行 → Grovyle L26(Leaf Blade)→ Brawly 3体連続撃破 → Knuckle Badge。

## H2. 徘徊 Briney への確実な話しかけ(帰路の渡し船で再発する)

- **H2a: 「待ち伏せ」方式** — NPC を追わず、徘徊圏の隘路(ドア前など幅1の地点)に立ち、NPC 座標を連続 2 回一致で読めた時だけ顔向け+A。追跡(chase)は mid-step 座標で必ず失敗する。
  検証: house 内で 50 turn 走らせ、成功率を測る。
- **H2b: A 連打圏方式** — NPC の徘徊範囲は狭い(house 内)。隣接判定を捨て、部屋中央で四方向に顔を変えつつ A を押す(dialog 信号が出たら成功)。
  検証: 同上。より単純なのでまず H2b から。

## H3. in-battle での devon flag flicker 再検証(未消化)

_read_saveblock1_ptr の二重読み修正(9998ad652)が**戦闘中でも**有効かは未確認(航海に野生戦がなく未消化; daily 07-06)。
検証: 次の野生戦で FLAG_RECOVERED_DEVON_GOODS を 20 回サンプル。1 回でも False → ptr 修正では不十分で、flag 読み自体の二重読み化が必要。

## H4. Brawly 撃破後の Slateport chain(2026-07-10 研究確定 + nav blocker 発見)

Emerald ストーリーゲート(pokeemerald decomp 検証済み):
1. ✅ Brawly 撃破(badge=2)
2. **Steven に Letter 配達**(GraniteCave_StevensRoom (24,10)、Steven NPC (7,8))→ `FLAG_DELIVERED_STEVEN_LETTER`(0xBD)。**Dewford→Slateport 渡し船の hard gate**(`DewfordTown/scripts.inc` goto_if_unset)。TM47 入手。
3. Briney (Dewford (12,9)) に話す → **multichoice** Petalburg(case0)/Slateport(case1)。**face+A だけだと default=Petalburg を選び誤航海する危険** — Down+A で Slateport 選択が要る(未実装)。
4. 渡し船は **Route109 (0,24) に上陸**(Slateport 直行でない)→ 北上して SlateportCity (0,1)。
5-6. Dock (SternsShipyard_1F) → Oceanic Museum 2F で Devon Goods 配達 → `FLAG_DELIVERED_DEVON_GOODS`(0x95)+ `FLAG_HIDE_ROUTE_110_TEAM_AQUA`(**Route110 の hard gate**)。
7-8. Route110 (0,25) → MauvilleCity → Wattson(Dynamo Badge)。

実装済み: state.py が 0xBD/0x95 を読取、goals.py に `deliver_steven_letter`(target StevensRoom (24,10) pos (7,9))+ heal を badge>=2 拡張。85→93 tests。

### ⚠ H4a: letter 配達が region-aware nav で block(2026-07-10、live で発覚)
`map_path(24,7→24,10)` は 1F を1ノード扱いで「1F→StevensRoom 直通」と返すが、**タイルレベルでは (5,10) warp は 1F 入口から到達不能**。canon collision で Granite Cave 1F は 3 つの非連結領域:
- size 85(入口): (37,12)→Route106、(17,11)→B1F
- size 117(Steven側): (35,3)→B1F、**(5,10)→StevensRoom**
- 入口→Steven warp は **1F入口 →(17,11)→ B1F(dark, requires_flash)→(35,3)→ 1F-Steven領域 →(5,10)→ StevensRoom** と B1F 経由必須。
現 nav は到達不能な (5,10) を BFS→None→reward_pick で徘徊(live 実測: 428 turn 入口領域を出られず)。
**修正案**: nav を **region-aware** 化 — ノードを (map, connected-component) にし、warp を「どの領域からどの領域へ」で張った graph を map_path の代わりに使う。mapbfs 自体は canon collision + RAM 駆動で vision 非依存なので、正しい map-level waypoint 列を与えれば暗い B1F も通れる(暗所 thrash は goal 無し wander 時のみ)。要 architect 設計 + verifier 検証。
- 反証/検証: region graph で (1F,入口領域)→StevensRoom の経路が出るか。B1F の warp 着地タイル(dest_warp_id→着地 warp index)を map_data で解決し、(17,11) 着地と (35,3) 着地が同一 B1F 領域かを確認。

### H4b: Briney sail multichoice
face+A の interact では Petalburg default を選ぶ危険。sail goal は multichoice 検出 → Slateport(case1)へ Down+A する処理が要る。誤ると Petalburg へ飛び progress 巻き戻し。letter(H4a)解決後に着手。

## H5. dual_dev の生産性(タスクがゲートに落ちがち)

自動生成タスクの Codex 実装が gate 不合格になりやすい(RESUME.md 未解決#3)。
- **H5a: タスク粒度が大きすぎる** — 対処: task_gen に「単一関数+テスト」粒度を強制、`--allow-path` を 1-2 ファイルに絞る。
- **H5b: ゲートが厳しすぎる**(diff 上限・dirty 判定) — 検証: 直近 fail の GateReport を集計し、落ちた理由の分布を見る。hard_hits でなく diff_lines 超過が多数なら上限調整の余地。

## 記録規則

仮説を検証したら結果をこのファイルに追記(棄却/確定/部分確定 + 日付 + 証拠)。新しい chronic stuck は必ず「decisions.jsonl の src 分布」を証拠にしてから仮説化する。
