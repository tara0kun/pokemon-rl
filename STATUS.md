# STATUS — 現在地

> 50 行以内。区切りごとに更新し、ほかの文書（RESUME・daily・README）からはここを指す。
> **Last verified: 2026-10-30 / `dev` `f056e0d97`**（`main` `df871d9c2`、tag `v0.8.1-ci-green`）。
> 最後にゲームを進めたのは 2026-07-29。それ以降は CI と文書の整備だけ。

## 現在地（ゲーム、2026-07-29 時点）

- **Badge 5（Balance Badge）＋ HM03 なみのり**。in-game の SAVE 済み（Petalburg / Wally の家、map 8,0）
- party: Sceptile L48（Chesto Berry 装備。Rock Smash / Rock Tomb / Pursuit / Quick Attack）＋ 控え 5 体
  （Poochyena L16・L11、Lombre L17、Marill L5、Slakoth L5。Norman 帯では戦力にならない）
- 所持金 ₽75、Super Potion 残り約 3
- 復旧用の savestate: `generic_agent/memory/badge5_rusturf_2026-07-29.ss1`（Rusturf、badges=5。gitignore なので手元だけ）

## 最良の結果

| tag | 日付 | 内容 |
|---|---|---|
| `v0.4-stone-badge` | 07-01 | Badge 1 |
| `v0.6-dynamo-badge3` | 07-14 | Badge 3 |
| `v0.7-heat-badge4` | 07-24 | Badge 4 |
| `v0.8-balance-badge5` | 07-29 | Badge 5（Norman を 5 回目で撃破。勝因は Chesto Berry で Yawn を無効化） |

## 動かしているもの

- 本番のループは `generic_agent/claude_heuristic.py`（`python -m generic_agent.claude_heuristic --turns 8000`、
  1500 turn ずつ回し続けるのは `generic_agent/tools/heur_loop.py`）。ルール＋canon データで動き、
  LLM は既定で OFF（`POKE_RL_USE_LLM=1` で ON）。README が説明している `auto_loop` は今の主力ではない（README は未更新）
- テスト: `poke-rl/Scripts/python.exe -m unittest discover -s generic_agent/tests -t .` → 398 件。CI（`tests.yml`）は緑

## 次の一手

**先に決めること（本人）: 再開するか、Badge 5 でポートフォリオとして完了とするか。** 再開するなら次の順:

1. 再開前に直す（2026-10-29 のレビュー）: `read_state` が例外時に badge=0 を返す欠陥（C-1）、
   バッジ上昇にも 2 回連続の確認（C-3）、周期セーブの無効化、自動スナップショットの退避
2. mGBA の Lua script を読み込み直してから始める（07-29 の終わりに Start の入力だけが全部落ちた）
3. Badge 6（Winona / Fortree、Flying）の目標の連鎖を実装する（稼働ループに Badge 6 の目標は無い）
4. 控えの育成先を Rusturf から高レベル域に移す

## 詳しくは

- 直近の経緯: `generic_agent/daily_progress/2026-07-29.md`
- 起動と再開の手順: `generic_agent/STARTUP.md`、`generic_agent/RESUME.md`（RESUME の現在地は 07-16 のまま。現在地はこのファイルが正）
- 未解決の問題: `docs/HYPOTHESES.md` / 変えてはいけないこと: `docs/INVARIANTS.md`
- レビュー（2026-10-29）: 本文はリポジトリの外（`~/claude-context/reviews/pokemon-rl_review_2026-10-29.md`）
