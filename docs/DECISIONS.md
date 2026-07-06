# DECISIONS — 非自明な設計判断の記録(判断 / 理由 / 棄却した代替案)

> Last verified: 2026-07-06。新しい非自明な判断をしたら 1 エントリ追記する。
> 詳細な経緯は generic_agent/daily_progress/ の該当日に、恒常ルール化されたものは INVARIANTS.md にある。

## プロジェクト方針

- **2026-06-08: rule-based RL → VLM agent へ pivot**。理由: 23,000 行 env が patch-tower 化(2週間で28パッチ)し、「汎用」と言いつつ Emerald 専用 bot になっていた。Anthropic の Claude Plays Pokemon が実証済みパスだった。代替案(patch-tower を refactor して継続)は連鎖崩壊が原理的に再発するため棄却。旧資産は `old` branch に凍結し import 禁止(独立性維持)
- **2026-06-12 頃〜: VLM 常用 → ヒューリスティック+canon hybrid へ再転換**(claude_heuristic.py)。理由: (a) API コストゼロ目標、(b) 行動クローンのデモンストレータにした API モデル自体が Route101 で stuck しており、CNN が stuck パターンを継承した。「会話中の Claude の戦略を Python に直接書く」ことで解決(claude_heuristic.py:1-27)。**全部 LLM は高コスト、全部 rule は柔軟性不足 — hybrid が最適**は 06-08 時点からの一貫した教訓
- **EN ROM 採用**(06-08)。JP ROM は同じモデルでも vision 理解が劇的に弱い。ROM 言語選択はコストより重要

## データ/ナビゲーション

- **canon データはランタイム DL、コード埋め込みは禁止**(map_data.py:8-18)。「ハードコード禁止」ルールと canonical data 活用を両立させる整理。オフライン時は素の heuristic にフォールバック
- **grass/water/ledge は behavior byte で分類**(06-28)。metatile ID 決め打ちは tileset ごとに意味が変わり誤分類した(池を草と判定)
- **水タイルは OUTDOOR のみ BFS 封鎖**(07-06 f136922f7)。INDOOR の暗迷路タイルセットが床を水と誤分類し Brawly 到達不能になったため。「canon の分類より goal 到達可能性を優先する」判断
- **trainer LOS の permanent 封鎖を撤去**(07-01)。Stone Badge 後は勝てる+Route104 では LOS 回避が唯一の南下経路を塞いだ。「戦って進む」が正。将来また封鎖したくなったら理由コメント必須(map_data.py:384-399)
- **目的地以外の warp を BFS 封鎖**(07-03 頃)。Dewford で民家ドアに吸い込まれる事故の対策。protected_warps で goal 自身の target は除外
- **story gate は event flag の実物を使う**(peeko: FLAG_RECOVERED_DEVON_GOODS)。「tunnel を訪れたか」の proxy は入った瞬間に発火して Peeko を置き去りにした(goals.py:205-216)
- **DMA flicker 対策は disk latch**(07-06 bbb634b4b)。読み直し・多数決より「単調 flag は一度 True を見たら永続化」が単純で確実

## エージェント挙動

- **poll 0.6s**(実測主導)。0.05s/0.3s は移動が queue されず stuck。速度より移動確実性(claude_heuristic.py:1555-1564)
- **RUN_CYCLE 先頭 B**、**FIGHT カーソルリセット Up,Up,Left**、**低HP wild は RUN**、**過剰 Lv+ball 無しの wild は RUN**(それぞれ INVARIANTS D 参照)。共通する設計思想: **バトル UI は状態機械として「どこに落ちても自己収束する」ボタン列にする**(1 ステップずれても再収束)
- **rescue prompt は 1 episode 1 回**(06-08)。毎 turn 発火は note 累積で token 肥大 — prompt 側にも patch-tower は起きる
- **anomaly_escape は goal-directed 中は発火させない**(claude_heuristic.py:1365-1370)。escape が mapbfs の進行を壊す方が損
- **explore_target hijack の抑制**(32 fix 06-29): target_pos 付き goal / dewford 系 directed goal 中は探索転換しない。50+ 時間の自律走行で grass に到達しない真因だった

## 開発プロセス

- **dual_dev: Claude=設計/レビュー、Codex=実装、ゲート=決定論**(06-25 頃〜)。LLM 同士の相互レビューではなく、commit 可否は機械判定(diff 上限 / path 制限 / 禁止パターン / py_compile)。Claude は subscription 経路(API key strip)で credit を守る
- **Codex は 4 時間間隔の終日低レート**(07-06)。5 時間ローリング枠の実測に基づく。バースト運用は user の対話利用と衝突する
- **git: main=milestone+tag / dev=日常 / old=凍結**(06-10 改訂)
- **deploy 前チェックリスト制定**(06-29)。8 個の chronic mistake の user 大規模 audit が契機。「真因確定は 1 session 1 回まで」「観察だけの wakeup 禁止」など、**エージェント(Claude)自身の運用規律**を文書で強制する
- **badge_count のバグ修正で「RAM 由来の集計値は必ず実測検証」**(07-01: Stone Badge 取得が 0 と報告されていた)。新しい RAM 由来シグナルを goal 条件に使う前に、実機で値の変化を確認する
