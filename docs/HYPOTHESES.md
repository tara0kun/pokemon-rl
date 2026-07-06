# HYPOTHESES — 未解決問題と反証実験(上から順に実行)

> Last verified: 2026-07-06。各仮説に「これを実行してこうなれば棄却」を付す。
> 実行前に RESUME.md で現在地を確認。診断の第一手は常に `logs/decisions_*.jsonl` の grep。

## H1. Dewford Town で Gym door 手前振動(現在の最重要 blocker)

**症状**: agent が (6-7,18-19) で振動し、door approach (8,18) → door (8,17) の最終ステップに乗れないことがある。mapbfs の dist が 2→4 に増える(RESUME.md 未解決#1)。Gym 内 BFS は f136922f7 で修復済み。

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
