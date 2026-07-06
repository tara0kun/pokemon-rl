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

## H6. バトルAIが勝てる gym 戦を落とす(2026-07-06 新規・H1 の live 走行で表面化)

**症状**: Grovyle L24 で Brawly に挑むも Machop L16(26/50 まで削っただけ)に Grovyle が気絶。控えが L3-L7 と低レベルで詰み、L3(Tackle のみ)vs Machop で 100+ turn 膠着。逃走不可 → whiteout 不可避。

- **H6a: opening で交代用シーケンスが戦闘メニューに誤発火**
  `battle_move_queue` の補充は `screen_signals.get("battle_menu")` True 時のみ。未検出ターンは `party_seq=("A","B","Down","A","A")`(本来 lead 気絶後の交代画面用)に落ち、戦闘開始直後の menu で空打ち → Grovyle が反撃前に一方的に殴られる。decisions 実測: opening turns 8-16 が全て `[trainer]` party_seq、move-select は Grovyle 気絶後に本格化。
  対処案: trainer 戦で in_battle かつ FIGHT メニュー(疑い含む)なら **best_move を party_seq より優先**。party_seq は「lead が 0HP かつ交代画面」条件に限定。
- **H6b: 控えが低レベルで Grovyle 依存**。catch ロジックが弱個体を溜め込む副作用。H6a を直せば L24 単騎で Brawly 突破可能(fighting は grass 等倍、L24>>L16-18)。
  検証: H6a 修正後 savestate_dewford.ss1 から再走行し、Machop の HP が opening から単調減少するか(decisions + enemy_hp)。

- **H1a: tile_map の経験的封鎖が approach 経路を汚染している**
  検証: `python -c` で tile_map.json の map "0-11" の (6-8,17-19) のレコードを dump。blocked/tried≥200 のエッジがあれば、過去の失敗(water 誤検出時代のものを含む)の残骸。
  反証: 封鎖レコードが空なら棄却。
  対処案: 該当タイルの blocked をクリアして再走行(cleanup は 4 方向封鎖しか自動解除しない)。
- **H1b: NPC が (8,18) 近傍に立って BFS 目的地を奪っている**
  検証: 振動発生時の decisions.jsonl で src を確認。`npc_avoid` が出ていれば NPC 干渉。state.read_npcs_on_map の値を同時に dump。
  反証: NPC が近傍にいなければ棄却。
- **H1c: warp_tiles_for が (8,17) を「エッジ warp」と誤分類し approach 変換していない**
  検証: `map_data.get_cache().warp_tiles_for(0,11,"DewfordTownGym")` の返値を確認。(8,18) でなく (8,17) が返るなら、(8,17) は on_edge 判定か walkable 判定に落ちている(map_data.py:441-462)。
  反証: (8,18) が返れば棄却。
- **H1d: mapbfs_perp の stuck 回避(same_pos_streak≥20 で垂直方向)が振動を作っている**
  検証: decisions.jsonl で `mapbfs_perp` の頻度。dist 2→4 は「1歩ずれて遠回り再計算」の症状と整合。
  対処案: door approach タイルへの最終 2 歩は perp 回避を無効化する(goal-directed 最終接近の特例)。

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
