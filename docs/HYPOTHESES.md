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
  対処案: (a) Grovyle を L27-28 まで grind(HP+攻撃で Makuhita を先に落とせる)、(b) 2体目の主力を育成、(c) 戦闘中の Potion 使用ロジック追加。**最短は (a) の軽い grind**。fighting は grass 等倍なので相性で押せない=レベル差で押す。
  検証: grind 後 savestate から再走行し Grovyle が3体連続で HP を保てるか。

## H2. 徘徊 Briney への確実な話しかけ(帰路の渡し船で再発する)

- **H2a: 「待ち伏せ」方式** — NPC を追わず、徘徊圏の隘路(ドア前など幅1の地点)に立ち、NPC 座標を連続 2 回一致で読めた時だけ顔向け+A。追跡(chase)は mid-step 座標で必ず失敗する。
  検証: house 内で 50 turn 走らせ、成功率を測る。
- **H2b: A 連打圏方式** — NPC の徘徊範囲は狭い(house 内)。隣接判定を捨て、部屋中央で四方向に顔を変えつつ A を押す(dialog 信号が出たら成功)。
  検証: 同上。より単純なのでまず H2b から。

## H3. in-battle での devon flag flicker 再検証(未消化)

_read_saveblock1_ptr の二重読み修正(9998ad652)が**戦闘中でも**有効かは未確認(航海に野生戦がなく未消化; daily 07-06)。
検証: 次の野生戦で FLAG_RECOVERED_DEVON_GOODS を 20 回サンプル。1 回でも False → ptr 修正では不十分で、flag 読み自体の二重読み化が必要。

## H4. Brawly 撃破後の goal 空白(事前に埋められる)

badge_count>=2 になると dewford 系 goal が全て非マッチになり、**次の goal が存在しない**(RESUME.md 未解決#4)。Slateport 方面(帰りの渡し船 → Route109 → Slateport)の chain が必要。
対処案: goals.py に `dewford_gym_brawly` の後続として (a) Dewford 桟橋の Briney に話す(= H2 と同じ徘徊 NPC 問題、桟橋は canon 固定位置なので H2 より簡単な可能性)、(b) Route109 上陸、(c) Slateport 到達、を `badge>=2` 条件で追加。**着手前に canon の warp/NPC を map_data で確認してから座標を書く**(ハードコード禁止ルールに従い canon 参照で)。

## H5. dual_dev の生産性(タスクがゲートに落ちがち)

自動生成タスクの Codex 実装が gate 不合格になりやすい(RESUME.md 未解決#3)。
- **H5a: タスク粒度が大きすぎる** — 対処: task_gen に「単一関数+テスト」粒度を強制、`--allow-path` を 1-2 ファイルに絞る。
- **H5b: ゲートが厳しすぎる**(diff 上限・dirty 判定) — 検証: 直近 fail の GateReport を集計し、落ちた理由の分布を見る。hard_hits でなく diff_lines 超過が多数なら上限調整の余地。

## 記録規則

仮説を検証したら結果をこのファイルに追記(棄却/確定/部分確定 + 日付 + 証拠)。新しい chronic stuck は必ず「decisions.jsonl の src 分布」を証拠にしてから仮説化する。
