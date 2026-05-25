# Ralts PassiveSwitch 有効化計画

## 現状 (2026-04-12, v10.9z254)

`_train_switch_passive` フラグは実装済だが、コード内で True に設定される場所がない (ドーマント状態)。

SafeSwitch 現行条件:
```python
if _slot0_lv < _enemy_lv - 1 and _slot0_lv < 16:
    # Blaziken に交代 (passive=False, 通常交代)
```

R116野生 Lv5-8 に対しては Ralts Lv7-8 なら直接戦闘が優先される設計。
Ralts Lv7→8 到達 (複数ポート) は direct battle EXP の証拠。

## PassiveSwitch の設計

Gen3 のEXP参加者フラグ:
- 1度でも場に出た Pokemon → 参加者 → EXPの 50% を獲得
- 戦闘開始時のリードは自動的に参加者

PassiveSwitch フロー (未実装):
1. 戦闘開始 → Ralts (slot0) がリード
2. bcc=15まで B×N で待機 (Ralts が場に出たフラグ確定)
3. `_train_switch_passive = True` + switch to Blaziken
4. Blaziken が KO
5. WIN後: Ralts + Blaziken 両方が EXP 獲得
6. 次戦闘: Ralts に戻す (PassiveReturn 既存)

## 有効化条件 (案)

```python
# SafeSwitch ブロック内に追加:
if _slot0_lv < _enemy_lv - 1 and _slot0_lv < 16:
    # 強敵 → 通常 SafeSwitch (現状)
    self._train_switch_passive = False
elif _slot0_lv < 16 and _enemy_lv <= _slot0_lv:
    # 弱敵 → Ralts 1ターン攻撃後 PassiveSwitch
    # (Raltsで倒せる敵だが、安全のためBlazikenで完全KO)
    self._train_switch_passive = True  # ★新規有効化
```

## 検証必要項目

1. **参加者フラグ設定タイミング**: bcc=15 で Ralts 参加者扱いになるか
2. **EXP 分配量**: 参加EXP 50% で Ralts Lv7→16 に要する戦闘数
3. **PassiveReturn 安定性**: 既存 _passive_return_seq が正しく動くか
4. **全滅リスク**: 1ターン待機中にRaltsが瀕死→その後のフロー

## 期待効果

- Ralts の EXP 獲得率を約 50% に向上 (現状: 倒した時のみ 100%、失敗時 0%)
- 瀕死リスクを低減 (Blazikenが仕上げ)
- WIN率維持 (既存のSafeSwitch性能を保持)

## リスク

- 参加者フラグが未設定でEXP=0 (方式A2の再現)
- PassiveReturn失敗で slot0 が Blaziken のまま戦闘継続 (stale状態)
- bcc増加によるバトル時間長期化

## 実施タイミング

- 現セッションでは実装せず (検証リスク大)
- Ralts Lv10+ 到達後、別ブランチで検証
- 最終判断: 実装しても WIN率維持かつ Ralts EXP+50% を達成したら本流に採用

## 参考

- `ralts_leveling_experiments.md`: 過去の方式A/A2実験結果
- `pokemon_env.py:5717`: 現行 SafeSwitch コード
- `pokemon_env.py:5966`: `_get_training_switch_seq` passive branch
