# Lavaridge / Flannery (Badge 4) 到達計画 — canon 検証済み (2026-07-14 workflow w44adnz4i)

**結論: Mauville→Lavaridge は直行不可。Mt.Chimney の Team Magma story sub-arc が hard prerequisite。** map_data の connection graph は `Route112→Lavaridge` を1hop と嘘をつく（実際は一方通行 ledge で分断）。

## 必須の順路（map route）
```
Mauville(0,2) --北--> Route111(0,26) --西(砂漠の手前で分岐)--> Route112(0,27) 南半分に着地
  --warp Route112(11,36) --> FieryPath(24,14) --warp--> Route112(22,10) 北半分
  --北--> Route113 --西--> FallarborTown --西--> Route114 --warp--> MeteorFalls_1F_1R
     ★EVENT A: 隕石強奪カットシーン → FLAG_HIDE_ROUTE_112_TEAM_MAGMA(0x333) SET (cable car の grunt 排除)
  --戻る--> Route112北 --warp(28/29,27)--> Route112_CableCarStation --ケーブルカー--> MtChimney(24,12)
     ★EVENT B: Tabitha → Maxie 撃破 → FLAG_DEFEATED_EVIL_TEAM_MT_CHIMNEY(0x8B) SET
  --warp--> JaggedPass(24,13) 一方通行 ledge 降下 --warp(14/15,40)--> Route112(6,46) SW pocket
  --西--> LavaridgeTown(0,12) --gym door(5,15)--> LavaridgeTown_Gym_1F(4,1) → Flannery → Badge 4
```

## goal chain（Mauville chain と同型・9 goal）
1. `enter_route112` — Mauville/Route111 → Route112。★ここで **Marill 捕獲**(Lv14-16、経路上)
2. `fiery_path_cross` — Route112(11,36)→Fiery→(22,10) 北半分（region-aware nav が処理: Route112 は multi-component True 確認済）
3. `reach_fallarbor` — Route112→Route113→FallarborTown
4. `meteor_falls_theft` — Fallarbor→Route114→MeteorFalls。**event goal**（retire=FLAG 0x333 set、nav は map 到達のみ、cutscene は dialog brain）
5. `ride_cable_car` — Route112 Cable Car(28/29,27)→MtChimney。gate=FLAG 0x333==1
6. `mtchimney_defeat_magma` — MtChimney で Tabitha+Maxie 撃破。**event goal**（retire=FLAG 0x8B set）
7. `descend_jagged_pass` — JaggedPass 降下→Route112 SW。gate=FLAG 0x8B==1
8. `reach_lavaridge` — →LavaridgeTown(0,12)。gate=FLAG 0x8B==1 & badge3 & !badge4
9. `lavaridge_gym_flannery` — Gym1F(4,1) Flannery(13,9)/B1F(4,2)。retire=FLAG_BADGE04_GET(0x86A)

## flags（state.py に追加要）
- FLAG_BADGE03_GET=0x869、**FLAG_BADGE04_GET=0x86A**（Heat Badge、arc retire）
- FLAG_HIDE_ROUTE_112_TEAM_MAGMA=0x333（EVENT A、cable car gate）
- FLAG_DEFEATED_EVIL_TEAM_MT_CHIMNEY=0x8B（EVENT B、Jagged Pass/Lavaridge gate）
- FLAG_DEFEATED_LAVARIDGE_GYM=0x4F3（Flannery beaten）
- (参考)FLAG_HIDE_MT_CHIMNEY_TEAM_MAGMA=0x39F、ITEM_GO_GOGGLES=砂漠 item check(flag なし、経路外)

## TOP RISKS
1. **connection-lie（最大）**: `Route112→Lavaridge` の直接 edge を nav が使うと一方通行 ledge で dead-end（Route104/Dewford と同型の失敗）。→ **FLAG 0x8B が set されるまで Route112→Lavaridge の直接 edge を nav で無効化**し、SW pocket（Jagged Pass 経由）のみを Lavaridge approach とする。Mt.Chimney sub-chain(goal 2-7)は prerequisite として注入必須。
2. **battle/cutscene 依存**: goal 4(Meteor Falls trigger)と 6(Tabitha Lv? + Maxie Lv24-26)は battle/event brain で完了。team が弱いと goal 6 で stall。
3. **Lavaridge Gym の hole puzzle**: 24個の温泉穴タイルは warp edge（B1F へ落下）。collision BFS が穴を壁扱いすると Flannery に届かない → Mauville 同様 **live-collision** 対応要。

## Flannery team & 対策
- Flannery: Numel L24 / Slugma L24 / Camerupt L26(Fire/**Ground**) / Torkoal L29。全員 Sunny Day + Overheat、Hyper Potion×2。
- **Grovyle(草)は不可**（Fire 2x 被弾、Grass STAB 0.5x で通らない、Sunny Overheat で一撃圏）。
- **推奨: Route112 で Marill 捕獲(Lv14-16) → Azumarill(L18)**。Water は Fire 全員に有効、Numel/Camerupt は Fire/Ground=**Water 4x**。Water Gun/Pulse で sweep。手持ちの Lotad→Ludicolo(Water/Grass)も可だが Fire 2x 弱点は残る。

## ★ Segment 0（NEW・最優先）: Rock Smash 能力チェーン（architect Fable 診断 2026-07-15）
**Route111 の (20,104) stall の真因は砂漠でなく BREAKABLE_ROCK ゲート**（砂漠 trigger は全 y≤61、stuck は 40 タイル南で反証）。Mauville 側から北へ抜ける唯一の通路 = (19,100)/(18,101) の岩 + (19,101) FatMan。static BFS は貫通するが live は read_npcs_on_map が gObjectEvents から岩/NPC を拾い bfs_blocked→None→wander/escape で袋小路 stuck。**HM06 Rock Smash（+ Dynamo Badge、取得済）で砕く必要。generic_agent に rock smash/field move/HM コードは 0 行。**
- **HM06 give NPC = MauvilleCityHouse1(10,2) の RockSmashDude (4,4)**（house 入口 = Mauville (3,7)/(4,7)）。
- Rock Smash チェーン（**完全自動・ユーザーは操作しない**方針）:
  1. `get_rock_smash` goal: House1(10,2)→RockSmashDude(4,4) interact で HM06 受領（既存 nav+interact）
  2. **HM を教える**: bag/party UI = 前例ゼロの最難所 → **VLM brain 委譲**（screenshot 見てメニュー操作、1回のみ）。Rock Smash は dead-weight の Poochyena に教えて HM slave 化（Grovyle の技を潰さない）
  3. 岩砕き: 既存 interact_target 機構で岩隣接→face+A。砕けた岩は flag→gObjectEvents から自然消滅
- **Part B（砂漠ガード、岩突破後の予防）**: map_knowledge の coord_triggers から ViciousSandstorm tile（(11,61)(12,61)(13,61)(14,61)/(12,44)(13,43)(14,42)(16,40)(17,39)(18,38)）を bfs_blocked に追加（canon script-name 由来、座標ハードコードでない）。これで南 BFS は y=28-31(砂漠経由)を諦め y=66-71 に正しくルート。

## latent bug（記録）
`MapInfo.connections` が direction-key dict のため、Route111 の2つの left 接続（Route113 offset0 / Route112 offset20）の片方が clobber され **Route113 が消失**。現チェーン（Route112北→up端→Route113）では非ブロッキングだが要修正。docs/HYPOTHESES 参照。

## 実装方針（incremental・realistically 複数セッション）
**Segment 0（Rock Smash、最優先・下記が無いと1歩も進めない）** → Segment 1: reach_fallarbor(実装済) → Marill 捕獲 → Segment 2: Meteor Falls event + cable car + Mt.Chimney battles → Segment 3: Jagged Pass + reach_lavaridge(connection-lie) → Segment 4: gym hole puzzle(live-collision) + Flannery。各 segment で offline test + live 検証。
