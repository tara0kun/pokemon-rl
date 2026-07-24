# GOTCHAS — 直感に反する挙動・一度ハマった罠

> Last verified: 2026-07-06。「一見動くが間違い」「知らないと数時間溶かす」ものだけを列挙。恒常的な設計前提は INVARIANTS.md、経緯は DECISIONS.md へ。

## ゲーム/エミュレータ

- **Dewford 到着トラップ**: 到着時 player は桟橋の Briney に隣接配置される。そこで A を押すと帰りの渡し船が起動して Route104 に戻される。到着後はまず桟橋から離れる(daily 07-06)
- **Peeko 救出後の Briney は徘徊 NPC**: canon の固定 (5,3)/(6,3) にいない。RAM の NPC 位置読みも間欠的に mid-step 座標を返す。「隣接して A」の自動化が3回失敗した既知ギャップ(daily 07-06)
- **savestate ロード直後は START メニューが開いていることがある** → B で閉じてからループ起動(RESUME.md)
- **whiteout は gBattleTypeFlags をクリアしない**(0xc が残留)。post-whiteout の overworld で in_battle=True に見える(state.py:58-66)
- ledge は逆方向から踏むと壁扱い。ジャンプ方向と押下方向が一致したときだけ通過(map_knowledge.py:77-87)
- 民家ドアと gym ドアが同じ x 列にあると、素朴な BFS 経路が民家に吸い込まれる(Dewford: gym (15,24) と House2 (15,15))→ 目的地以外の warp は BFS で封鎖(claude_heuristic.py:458-473)

## RAM/データ

- **grass 判定は metatile ID ではなく behavior byte**。旧 `GRASS_METATILES={0x208,0x209}` は Rustboro タイルセットでは池の水だった(灯台下暗し fix 06-28; map_knowledge.py:45-56)
- secondary tileset の behavior は 0x200+ にオフセットして引く。抽出済み secondary_*.bin がある map cluster しか正しくない(map_knowledge.py:288-299)
- bag 数量・species は暗号化されている(INVARIANTS B-10)。「生で読めた気がする」値はノイズ
- map.bin の寸法が layouts.json と合わないことがある → 幅候補から因数分解で推定するフォールバックあり(map_data.py:171-180)
- pokeemerald からの map データは**初回アクセス時にネットワーク DL**(map_data.py:106-111)。オフラインだと BFS 系が None を返し、heuristic は素の探索に落ちる(設計上のフォールバック)
- **battler slot は敵味方交互(0/2=自分、1/3=敵)**。`active_hp()` は slot 0 のみ = **ダブルでは自分の2体目(slot 2)のひんしが構造的に見えない**。Route111 Twins で 900 turn 停止した真因(07-16 fix)。ダブルの自分側は `battle_moves.player_battler_hps(double=True)` で両方読む
- **connection は direction 毎に複数あり得る**(全 518 map 中 Route111 left / Route124 right の 2 件)。`connections` は `dict[direction] -> list[conn]`。単一 dict にすると clobber(Route111→Route113 消失で Fallarbor 直通不可、07-17 fix)。exit_tiles_toward は必ず `dest_name` を渡して1接続の strip だけ取る(union は誤 map の edge を狙う)
- **connection の存在 ≠ 交差可能**。Route112→Route113 up は static 全壁の connection-lie(exit_tiles=∅)、Route112→Lavaridge left は別 walkable component のみ。map_path は最短の嘘 hop を選ぶので、exit_tiles ∅ / BFS 不達の first-hop は Part B が ban して再計画する(claude_heuristic の hop-probe)
- **Route112 は Mt.Chimney で2 blob に分断**(Fiery Path のみで接続)。map graph は map を1 node に潰すので Route112→FieryPath→Route112 の再入を表現できず ping-pong する。`fiery_path_cross` goal(南 blob = 高y Fiery warp と同 component の時だけ発火)で横断を明示。同型の Lavaridge SW pocket(cid27)は Segment3 で要対応
- **タマゴは hp>0 だが出せない**。HP だけで「控えがいる」と数えると、開いていないパーティリストを操作し続ける。`is_egg`(暗号化 M substruct の IV word bit30)で除外する。Lavaridge の Wynaut タマゴは NPC の YES/NO を dialog の A 連打が YES と答えて受け取るので、放っておくと必ず踏む(07-16、verifier 指摘)

## vision(screen_features)

- 検出は 240x160 固定レイアウト前提の白比率ヒューリスティック。**battle_menu は「右下だけ白、左下は白くない」**で dialog と区別(screen_features.py:110-130)。UI が変わる場面(進化・図鑑など)で誤検出があり得る
- post-faint の「Choose a POKEMON」party list 画面は dialog/menu/battle_menu **全部 False** を返す。UI 信号ゲートに頼ると party 画面で overworld ナビを押し続ける(claude_heuristic.py:622-641 のコメント)
- front_blocked は Canny エッジ密度の粗い判定。信号としては弱く、same_pos_streak>=3 のときだけ使う(claude_heuristic.py:196-206)

## コスト/クォータ(この repo 特有の事情)

- **VSCode 拡張の claude.exe は subscription quota を使うが 1 call ≈ $0.15 相当**。長時間の C モード連用は危険(daily 06-08)
- **OAuth (`ant auth login`) は API credit 問題を解決しない** — subscription と API billing は完全分離(daily 06-08)。逆に dual_dev は意図的に API key を strip して subscription 経路を使う(dual_dev/README.md)
- SakanaAI/Codex は 5時間ローリング枠が拘束。朝バーストすると昼まで死ぬ(daily 07-06)

## 開発時

- decisions の per-turn トレースは `logs/decisions_<session>.jsonl`(1行1JSON: turn/map/pos/goal/button/src)。**stuck 調査はまずこれを grep**(claude_heuristic.py:1256-1274)。100-turn stdout サマリだけでは「なぜ動いたか」が分からない
- dataset/demonstrations.jsonl は追記型で肥大する。ディスク注意
- tile_map の decay/cleanup は起動時に走る。「昨日封鎖されてたのに今日通れる判定」はバグではなく仕様(tile_map.py:137-171)
- `--poll` を短くして速度を稼ごうとすると逆に進まなくなる(INVARIANTS E-25 の実測表)
