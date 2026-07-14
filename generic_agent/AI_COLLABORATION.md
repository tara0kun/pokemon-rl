# AI Collaboration 運用メモ (Claude + SakanaAI/Codex)

**目的**: 1 つの開発サイクルを Claude と SakanaAI/Codex の 2 系統で回す。
役割を分け、同一ファイルの同時編集を避け、引き継ぎを明示することで事故を防ぐ。

このメモは「運用ルール」の定義。プロジェクト固有の制約は必ず repo 側 (AGENTS.md /
STARTUP.md) を優先する。

---

## 役割分担 (原則)

| 系統 | 主担当 | 具体タスク |
|------|--------|-----------|
| **Claude** | 設計・分析・レビュー | 方針整理 / 長文設計 / prompt 改善 / 失敗ログ分析 / 次サイクル計画 |
| **SakanaAI / Codex** | 実装・検証 | ローカルコード編集 / test 実行 / 差分確認 / daily_progress 更新 |

- **1 タスク 1 担当**。同じファイルを両者が同時に触らない。
- Claude の提案は「案」であり、そのまま反映しない。Codex 側が repo 制約に照合してから実装。
- どちらかの成果を渡すときは **差分 / 目的 / 未検証点** を必ず添える。

---

## 守るべき repo 制約 (両系統で厳守)

- **ROM 操作のみ**: ボタン + screenshot + RAM read
- **RAM 直接書込 禁止**
- **saveStateLoad 禁止** (ストーリー進行のリセットで突破しない)
- **ハードコード禁止** (座標 / map_id 等の game-specific 数値をコードに埋め込まない。prompt は OK)
- **old branch import 禁止** (generic_agent は完全独立)
- **コスト最優先** (API call は cache + rules で削減)

---

## プロンプトテンプレ

### Claude に投げる (設計・分析)
```
[役割] 設計/分析のみ。コードは最小の擬似コードまで。実装は Codex が行う。
[制約] RAM書込禁止 / saveStateLoad禁止 / 座標・map_idハードコード禁止 / old branch import禁止
[入力] <ログ抜粋 or 対象ファイル or 課題>
[依頼] <分析 or 方針 or prompt改善案>
[出力形式] 1) 結論 2) 根拠 3) Codexへの実装指示 4) 未検証点/リスク
```

### SakanaAI / Codex に投げる (実装・検証)
```
[役割] 実装/検証。最小変更で。
[制約] 上記 repo 制約を厳守。同時編集回避のため担当ファイルを限定。
[担当ファイル] <path のみ>
[入力] <Claude案 or 課題>
[依頼] <実装 or test実行 or daily_progress更新>
[完了条件] 差分提示 + 検証結果 + 未検証点
```

---

## 引き継ぎフォーマット (ハンドオフ)

Claude → Codex、または Codex → Claude で渡すとき:

```
## Handoff (<from> -> <to>) <date>
- 目的:
- 変更/提案の要点:
- 触ったファイル (担当境界):
- 検証済み:
- 未検証 / リスク:
- 次にやること:
```

---

## Git 運用 (repo ルール準拠)

- 通常サイクルは `dev` で commit + push。
- 大きめ実験は `dev` から `feat/<name>` を分岐、完成後 `dev` に merge。
- `main` は milestone 専用 (触らない)。
- 両系統が別々に commit する場合、commit message 冒頭に系統名を付けると追跡しやすい:
  - 例: `[claude-plan] ...` / `[codex-impl] ...`

---

## 記録場所

- 日次: `generic_agent/daily_progress/YYYY-MM-DD.md`
- 各サイクルで「Claude案 / SakanaAI実装 / 検証結果」を 1 セットで残す。
- テンプレ:

```
## <date> cycle
### Claude案
### SakanaAI/Codex 実装
### 検証結果
### 未解決 / 次サイクル
```
