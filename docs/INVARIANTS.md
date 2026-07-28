# INVARIANTS — 壊すと静かに劣化する不変条件

> Last verified: 2026-07-06 (HEAD `3e9e23db6`)
> **training / env / nav コードを変更する前に必読。** ここにある前提を無効化する変更は、同じコミットでこのファイルも更新すること。
> 各項目に根拠(`ファイル:行` または daily_progress 日付)を付す。

## A. プロジェクト原則(repo CLAUDE.md 由来 / gates.py が機械強制)

1. **ROM 操作のみ**: ボタン + screenshot + RAM read。**RAM 直接書込は禁止**(gates.py:13-17 が diff を拒否)
2. **saveStateLoad でストーリー進行をリセットしない**。ただし例外が2つ: (a) セッション再開時の復元(RESUME.md 手順2)、(b) mGBA 異常終了後の emergency restore(io.py:180-193 docstring)。`MGBAClient` が load を「意図的に」別メソッドに隔離しているのはこのため
3. **game 固有座標/map_id をコードにハードコードしない**(prompt と data は OK)。map_data.py は「ランタイムで pokeemerald decomp からデータ取得」でこれを満たす(map_data.py:8-18)。goals.py の GOAL_TABLE 座標は canon データのラベルであり、decision code への埋め込みではない、という整理
4. **old branch (pokemon_env) を import しない**(gates.py:22-25 が拒否)

## B. RAM 読みの不変条件(state.py)

5. **SaveBlock1 は毎フレーム DMA 再配置される**。ポインタは「連続2回一致 + EWRAM 範囲(0x02000000-0x02040000)」のみ採用(state.py:84-95, 07-06 commit 9998ad652)。これを経ない直接 read は戦闘中に過渡アドレスを掴み、flag が間欠的に 0 を返す
6. **単調 story flag は disk latch にする**(goals.py:30-48 の `peeko_done.marker` パターン)。DMA flicker で goal chain が振動した実害あり(07-06)。今後 story gate flag を増やすときは同じパターンを使う
7. **`gs.in_battle` を単独で信用しない**。gBattleTypeFlags(0x02022FEC)は (a) whiteout 後にクリアされず残留する(state.py:58-66 NUANCE)、(b) move-select 画面で 0 を返す個体もある。よって:
   - state.py は gMain.callback2(0x030022C4)が CB2_Overworld(0x08085E5D)のとき in_battle=False に矯正(state.py:70-78, 222-234)
   - heuristic 側は vision の battle_menu 信号で latch し、wild 系分岐は「UI 信号があるときだけ」行動(claude_heuristic.py:116-133, 649-691)
8. **gObjectEvents の NPC 座標は -7 補正が必要**(state.py:190-196)。RAM 座標系は map 座標 +7。また `npcs_on_map` には**プレイヤー自身も含まれる** — 自タイル除外を忘れると自分を障害物扱いする(claude_heuristic.py:362-366)
9. **座標系は canon(map_data)= gs.x/gs.y**。古いメモリの「+7」値と混ぜない(goals.py:267-269)
10. **暗号化された値**: bag 数量は SaveBlock2 security key と XOR(state.py:328-365)、party の species は personality/otid XOR + substruct 順で復号(state.py:272-295)。生値を信用した過去バグあり(35 fix / 06-29 audit)
11. badge_count は FLAG_BADGE01(0x867)〜08 のビット集計(state.py:311-320)。以前は常に 0 を返すバグだった — badge 条件を触るときはここを確認
12. lua からの応答は空文字列があり得る。read 系は `_parse_int` / hex-token 検証で EmulatorError に正規化(io.py:110-157)。`int("")` ValueError で落ちた過去あり(06-08)

## C. ナビゲーション不変条件

13. **canon 衝突レイヤは「歩ける」の上界でしかない**。深い水(WATER_BEHAVIORS)は collision=walkable だが徒歩不可(map_knowledge.py:58-70)。**ただし INDOOR / UNDERGROUND(洞窟)map では水封鎖をしない** — これらのタイルセットは床を水の behavior byte で誤分類し、Dewford Gym(INDOOR)で BFS が Brawly に到達不能、Granite Cave(UNDERGROUND)で 1F 入口が B1F ラダー(17,11)から分断され Steven letter trek が不能になった。Surf は Granite Cave より遥か後なので、この段階で story が渡らせる「水」は必ず歩ける床(H4a, map_knowledge.py block_water は OUTDOOR のみ True)。**region-aware 経路(H4a、map_data.py `_components`/`region_route_targets`)は raw canon collision で component 分解するので、bfs_to_tile 側の水封鎖と一致していないと「region graph は連結と判断→bfs は水で不達→徘徊」になる。両者の walkability モデルは一致させること。** 2026-07-18 以降、bfs_to_tile は水に加え **elevation-carry**(下記 13b)でも raw collision より狭い。この差は claude_heuristic の relax fallback 連鎖(水 unblock → live-collision → elevation-relax)で「旧挙動を床」として吸収する — fallback を消すと strand が再発する。

13b. **タイル移動は elevation でもゲートされる**(pokeemerald `GetCollisionAtCoords`→`IsElevationMismatchAt`+`ObjectEventUpdateElevation`)。elevation nibble: 0=transition(どこからでも進入可・carry を 0=wildcard に)、15=multi-level/橋(進入可・carry 温存)、それ以外は「プレイヤーが持ち越している elevation と一致」が必要。**stateless な e1==e2 規則はゲーム規則ではない**(0/15 経由の連鎖で過剰/過少ブロック両方を起こす)ので、bfs_to_tile は (x, y, carried_e) 状態で持ち越しを再現する(map_data.py bfs_to_tile)。実害: Route114 (21,57)e3→Down(21,58)e4 は collision=0・behavior=MB_MOUNTAIN_TOP(ledge ではない)で、elevation mismatch だけが塞ぐ — 旧 BFS はここへ 130 turn 突っ込んだ。elevation 未知タイルは 0(wildcard)扱い = elevation を渡さない legacy 呼び出しは従来挙動のまま

13c. **bike 専用 metatile は徒歩の壁**(2026-07-22)。`FOOT_IMPASSABLE_BEHAVIORS`(map_data.py: 0xD0 MB_MUDDY_SLOPE / 0xD1 MB_BUMPY_SLOPE / 0xD3-D6 MB_*_RAIL)は collision=0 だが徒歩では絶対に入れない(pokeemerald `CheckAcroBikeCollision` が非0 collision に変換 / muddy は `ForcedMovement_MuddySlope` で南へ強制滑落)。agent は bike を持たないので bfs_to_tile はこれらを**常時** wall 扱いし、water/elevation-relax fallback や `extra_walkable`(live grid も同じ collision=0 を読むので嘘をつく)でも再開放しない。実害: JaggedPass の登り経路は全て 0xD1 strip 経由で、phantom Up-path → bump → 一方通行 ledge で下端漏斗 → warp pad → Jagged↔Route112 pocket 無限バウンド(grind 不成立)。⚠ raw collision の component 分解(`_components`)はこれを見ない — 「同一 component = 徒歩到達可能」は bumpy を跨いで嘘をつく(JaggedPass 草地は**上の Mt.Chimney 側からのみ**進入可、test_jagged_nav.py が機械検証)。影響監査: cache 済み 277 map 中 8 map のみ・全て橋にならない行き止まりタイル(開いた隣接<2)で、既踏破ルートへの影響ゼロ
14. **BFS の blocked 合成順**(claude_heuristic.py:394-477): NPC タイル + 経験的封鎖(3方向 blocked のタイル / 200回試行失敗の方向エッジ)+ permanent + 水 + **目的地以外の warp タイル**。最後のは「BFS 経路が他人の家のドアを踏んで別 map に飛ぶ」事故の防止(Dewford で実害: gym door と民家 door が同じ x 列)
14b. **run() の探索系 post-override(anomaly_escape / forward_force)は goal-directed な button を上書きしない**(`GOAL_DIRECTED_SRC_PREFIXES`、claude_heuristic.py `forward_force_override`)。BFS が経路を返している間は follow が絶対 — 特に map 進入後 30 turn の forward_force が entry_dir を強制すると、一方通行 JUMP ledge 連鎖では**bump が起きないため tile_map の blocked 学習による自己回復が効かず**、分岐を素通りして不可逆に落ちる(2026-07-22 実害: Jagged Pass grind 降下が毎 cycle y=29/31 の草分岐を飛び越え bottom warp pad → pocket → cable car 無限ループ、grind ゼロ。test_jagged_nav.py TestJaggedDescentReplay が実 heuristic_button + 実 override で降下全体を機械検証)
15. **interior door warp は non-walkable タイルに乗っている**。BFS の目的地はドアの1つ下の approach タイルにし、到着後 `warp_step_direction()` が Up を返す(map_data.py:441-463, 481-518)。ドアタイル自体を target にすると BFS が None を返し徘徊する(旧 Rustboro 東縁振動の真因)
16. **tile_map の封鎖は 3 回失敗で確定、ただし 4 方向封鎖は記録バグ**(そのタイルに立てた以上、最低1方向は通れる)— 起動時 `cleanup_phantom_walls()` で自動解除(tile_map.py:126-135, 159-171)。dialog/battle 中の方向キーは `overworld=False` で記録スキップ(tile_map.py:104-124)— これを怠ると封鎖リストが汚染される
17. **ledge は 1 方向エッジ**: JUMP behavior タイルへ「ジャンプ方向と同じ向きで」踏み込むと 2 タイル先に着地(map_knowledge.py LEDGE_JUMP_BEHAVIORS, map_data.py bfs_to_tile)。ジャンプはタイル自身の collision より優先され(pokeemerald `CheckForObjectEventCollision` は `ShouldJumpLedge` を collision 結果に上書き)、**それ以外の方向から JUMP タイルに乗ることは決してない**(BFS も skip する)。また **behavior 分類はマップの layout が指す primary/secondary tileset ペアで引くこと**(map_knowledge.py `_load_behavior_table(map_name)` + SEED_VERSION)。単一 secondary テーブルの流用は Route114(Fallarbor secondary)の ledge 51 個を behavior 0x00 に化けさせ、ledge_jumps 空 → BFS が game-blocked エッジへ突入する v1 バグの真因だった(2026-07-18)。persisted knowledge は seed_version < SEED_VERSION で自動再導出(empirical データは温存)
17b. **warp_events は「tile の metatile behavior が warp 系」のときだけ発火する**(pokeemerald `TryStartWarpEventScript` → `IsWarpMetatileBehavior`)。behavior 0(MB_NORMAL)の warp_event は発火しない inert データ — Lavaridge Gym の hole/geyser ペアの受け側 landing pad、イベント解禁前の隠し入口(Terra/Altering Cave、Steven's Cave)、script で開く閉扉(Petalburg Gym 部屋、Trick House、E4 hall)がこれ(2026-07-19 監査: cache 861 warps 中 behavior-0 は 65 件、全て上記 3 分類)。region graph(`_warps_in_component`)と `warp_tiles_for` は `_warp_active` でこれを除外する。behavior 不明(attr 未取得)は従来通り有効扱い。**Petalburg Gym の部屋ドアは static map では behavior 0(閉)なので、Badge5 時に live metatile 読みが必要になる見込み**(既知の限界)。
17c. **hole/geyser puzzle(Lavaridge Gym)は「同一 map ペア間の step-on warp + 一方通行 ledge」の合成で解く**。Flannery の部屋(1F comp6)へは B1F (12,12) geyser のみ、その geyser 室(B1F comp6)へは B1F の jump 行(10-12,10)のみが入口 — raw collision の component 分解だけでは循環閉鎖に見える。region graph は ledge の component 間有向 edge(`_ledge_component_edges`、behavior 由来)を持ち、`region_route_targets(target_tile=)` が goal tile の component を狙う。同一 map で tile-BFS が全滅したときの heuristic fallback(claude_heuristic の region_interact fallback)がこの経路に乗せる。canonical solve は 8 warp ride(test_region_route.py LavaridgeGymIntegrationTests が機械検証)。**mh_chain fallback はチェーン末尾(goal 側)から試す**: 中間フロアでは chain[0]=「いま来た map」で、any-component 一致が「戻りの geyser」で満たされ hole↔geyser 無限バウンドになる(2026-07-21 実害、ChainBacktrackTests が pin)。また **Part B の hop-ban probe は canon ledge を渡し、多 component map では ban 前に region ルートの有無を確認する** — exit warp が別 component の warp maze で town hop を誤 ban すると map_path が崩壊し、汚染 path_memory chain((0,0) 経由)に落ちて徒歩振動する(claude_heuristic.py Part B (iii))。
18. Route104 北と南浜は同一 map で徒歩非接続。横断は Petalburg Woods 経由(goals.py:199-213)。Route104→Rustboro の実働 exit は x=19 のみ(canon-walkable でも game-blocked の縁がある: map_data.py:369-377 `_EMPIRICAL_EXIT_TILES`)

18b. **connection の exit tile は「着地が離脱可能」なものだけ有効**(2026-07-28, map_data.py `_exit_strip_for`)。canon-walkable でも dest 側の着地タイルに「dest 内の walkable 隣接が 0」のものがある(1タイル孤島)。渡り自体は成功するが唯一の手が「引き返し再横断」で、nav は対岸に立てないため blocked-edge 学習が働かず、nearest-exit-tile BFS が同じタイルを選び続けて境界振動する。実害: Verdanturf (19,6)→Route117 (0,6)(size-1 component)が Rusturf トンネル着地からの最近傍で、東進 Verdanturf→Mauville が必ず吸い込まれる。監査(cache 全 518 map / exit 2004 タイル、verifier が独立再計数): 除外は 15 タイルのみ・全て縮退孤島(Verdanturf (19,6) / Mauville (28,19)→Route110 (28,0) / 海縁コーナー類)で、**全該当 connection は実横断帯 ≥2 タイルを保持**(band が 0/1 に潰れる connection はゼロ。test_connections.py UnleavableLandingTest が synthetic + 実 cache で pin)。exit band を狭める変更なので、band が空になる新ケースを cache 追加時に見たらここを再監査すること
18c. **party_grind marker ON 中は current_goal が `goals.PGRIND_GOAL_NAMES` 以外を絶対に返さない**(2026-07-28, goals.py PGRIND_GOAL_NAMES / current_goal の name filter)。真因: badge_count は saveblock1_valid=True でも ~0.7% のフレームで 0 に DMA flicker し、era gate が全滅した隙に cur-ungated な旧世代 goal(実測: reach_mauville。sweep 再現: enter_rustboro_gym / ride_cable_car)が scan を勝ち取り往復振動を起こす。filter は「badge latch を直す」のではなく「訓練モード中の選択肢を名前で閉じる」一般解。flicker フレームは None を返し、loop の goal-carry(claude_heuristic の `carry_allowed` gate)が直前の pgrind goal を運ぶ — **carry も marker ON 中は pgrind goal 以外を運ばない**(marker OFF 時は current_goal / carry とも従来と byte-identical、sweep 576 state で機械検証)。クリーンフレームで subset が全滅する位置(回廊外)は `pgrind_fallback`(Rusturf pin 行き catch-all)が受けるので goal-less にならない。pgrind goal を追加したら PGRIND_GOAL_NAMES にも足すこと(テストが GOAL_TABLE との整合を pin)。smash_rusturf_rock / smash_route111_rock は「target==cur + 岩実在」でしか発火しない facilitator なので意図的に許可(Route111 の 255-turn wedge 再発防止)。

19. trainer LOS は**封鎖しない**(Stone Badge 後は勝てる+Route104 では LOS 回避が Woods を到達不能にした)。победа後に回避が必要になったら map_data._PERMANENT_BLOCKED_TILES に理由コメント付きで追加(map_data.py:384-399 の削除履歴参照)

## D. バトル不変条件

20. **trainer 戦から RUN は不可**。wild 判定なしに RUN を強制すると "No running from a TRAINER battle!" dialog が無限ループ(claude_heuristic.py:141-176)
21. FIGHT 選択は必ずカーソルリセット付き(`Up,Up,Left` → A,A)。カーソル位置は前ターン依存で、盲目 A 連打は RUN/POKEMON を誤確定する(claude_heuristic.py:179-191, Roxanne 戦 06-24 実証)
22. RUN_CYCLE は先頭 B(`B,A,Down,Right,A,A`)。B なしの旧シーケンスは 1 phase ずれると party メニュー内を永久航行した(claude_heuristic.py:66-72, Route116 で 40+ turn 実害)
23. wild 戦の低HP(<26%)は FIGHT/catch せず RUN — whiteout(全滅→強制 warp+所持金半減)の方が損失大(claude_heuristic.py:134-148, GameState.party0_critical state.py:143-145)
24. battle_menu の vision 検出は「battle 疑いがある turn は毎 turn」実行。turn%5 スロットルに戻すと FIGHT 枝が 4/5 turn 死ぬ(07-01 の全セッション Roxanne 失敗の真因; claude_heuristic.py:1097-1114)

## E. タイミング・プロセス・環境

25. **poll は 0.6s 未満にしない**。タイル歩行 ≈16 frame + button hold 15 frame。poll が短いと移動が正しく queue されず chronic stuck(claude_heuristic.py:1555-1564 の実測表)
26. socket は 1 コマンド = 1 接続(使い回し不安定; io.py:35-39)。lua script の多重 load は listener 重複で router が壊れる — mGBA ごと再起動が復旧手順(06-08)
27. **python プロセスの一括 kill 禁止**。dual_dev orchestrate を巻き添えにすると run-state が固着し Codex が無限待機(07-06)。`claude_heuristic|dual_dev` でコマンドラインを絞って kill(RESUME.md 停止コマンド)
28. ROM は EN 版(`emerald_en.gba`)。JP ROM は vision 理解が著しく弱い(06-08 実測)。ROM ファイルは repo に含めない(著作権)
29. mGBA 起動 + lua load だけが人間の仕事(1回/セッション)。それ以外の操作をコード外で行ったら daily_progress に記録する(再現性)
30. 定期 in-game save(500 turn 毎, Start メニューシーケンス)+ savestate 自動スナップ(150 turn 毎)。クラッシュ時は autosnap から emergency restore(claude_heuristic.py:1492-1526)

## F. 開発プロセス

31. git: `dev` = 日常、`main` = milestone のみ(必ず tag)、`old` = 凍結(repo CLAUDE.md)
32. deploy 前に docs/MISTAKE_PREVENTION_CHECKLIST.md を通す(真因単数性 / patch tower 反復 / verify 完全性 / goal target_pos 必須)
33. dual_dev の Claude 呼び出しは API key を strip した subscription 経路(dual_dev/README.md:11-13)— API credit を dev loop で燃やさない
34. SakanaAI/Codex は **5時間ローリング枠**が拘束。バーストさせず `--codex-min-interval` で終日低レート運用(07-06 実測)
