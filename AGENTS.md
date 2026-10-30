# AGENTS.md — Codex 向けの入口

このリポジトリのルールの正本は **`CLAUDE.md`**。Codex も最初にそれを読み、そのとおりに動くこと。
現在地は **`STATUS.md`**。ここには Codex に固有の差分だけを書く（CLAUDE.md の写しは置かない。写しは古くなる）。

## Codex に固有の差分

- **呼ばれ方**: Claude のセッションから `scratch/ask_codex.py` 経由で、Claude 以外の第三の視点として呼ばれる。
  既定は read-only（レビュー・設計の意見だけ）。ファイルを書いてよいのは `--write`（workspace-write）で呼ばれたときだけ
- **CLAUDE.md の読み替え**: 「Claude」「Claude Code」の機能（subagent、hook、memory など）は Codex には当てはまらない。
  モデル名（`claude-opus-4-8`、`claude-haiku-4-5` など）はエージェントが API で使うモデルで、Codex 自身のことではない
- **使える量に限りがある**（5 時間ごとの枠。`docs/INVARIANTS.md` #34）。1 回の呼び出しで返答を出し切る
