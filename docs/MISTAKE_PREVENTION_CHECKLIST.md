# Mistake Prevention Checklist (06-29 制定)

user 大規模 audit (06-29) で 8 chronic mistake 判明後、 各 deploy 前 必須 verify。

参照: `~/.claude/projects/c--pokemon-rl/memory/feedback_mistake_prevention.md`

---

## Deploy 前 必須 checklist (1 つでも skip したら deploy 禁止)

### 1. patch tower 反復 check
- [ ] 前 patch deploy 時刻 + 25 min 経過 OR cycle 完走確認?
- [ ] 同じ chronic pattern (e.g. east area stuck) の N 回目 fix か?
- [ ] 連続 3 patch failure → 強制 stop 1 hour 観察期 設定済?

### 2. 真因 単数性 check
- [ ] 「真因確定」 を本 session 中初めて使う? (2 回目以降 = symptom fix)
- [ ] decision tree 全 branch traced (mapbfs / reward / goal_warp / explore_unvisited)?
- [ ] 関連 goals 全 target_pos audit (None = wander risk)?

### 3. verify 完全性 check
- [ ] heur 5 trace 完了?
- [ ] heur process restart 必要なら実施?
- [ ] +25 min 後 cron pos delta 観察予定?

### 4. goal target_pos 必須 check
- [ ] target_map (どの map)
- [ ] **target_pos** (どの tile) ← None 禁止
- [ ] condition
- [ ] desc

---

## Wakeup 必須 fields (cron-fire / ScheduleWakeup 共通)

各 wakeup で 以下 全 fields 報告必須。 1 つでも欠けたら incomplete:

- [ ] **semantic delta** (badges / grass / encounter / map transition / NPC / HM、 数字 +X 禁止)
- [ ] **pattern analysis** (前 cycle との 差分)
- [ ] **deploy 判断 reason** (no-patch なら なぜ観察、 patch なら なぜ今)

### 4 連続 same pattern 強制深掘り
cron 4 連続で agent pos がほぼ同 area (±3 tile)なら、 簡潔 report 禁止。 root cause analysis 強制実行:
1. heur 5 trace
2. tile_map empirical 蓄積 check
3. decision tree branch trace
4. canon parse verify
5. 6 component score honest update

---

## 禁止 phrase (使ったら即 deep dive 強制)

### 諦め system
- 「私の知識限界」
- 「architectural ceiling」
- 「structural limit」
- 「無理」
- 「不可能」
- 「true root cause」 (2 回目以降)

### 形骸化 system
- 「真継続」 evidence なし
- 「遵守継続」 evidence なし
- 「動作中」 だけで終了
- 「微動き」「chronic 継続」 だけで終了
- 「demos +N」 を progress として
- 「action 続行」 を progress として
- 「button send 機能」 を progress として

---

## 実 progress 限定 list (これ以外 「progress」 と呼ばない)

- badges 増加
- grass entry (実 tile に visit)
- wild encounter 発生 (mk.encounters_seen 増加)
- NPC dialog 通過 (story flag set)
- map transition (m_g, m_n 変化)
- HM 取得
- starter / Pokemon 取得
- party0 level up (encounters 経由)

---

## Compliance verification

各 wakeup の最後で:
1. 上記 checklist 全 box check 済?
2. 禁止 phrase 使っていない?
3. 「実 progress」 のいずれか 1 つでも変化あった?
4. semantic delta evidence あり?

NO → 観察スパム判定、 強制深掘り。
