"""
Claude 戦略アドバイザー
=======================
エピソード終了ごとにClaudeに現在のゲーム進行状況を送り、
戦略アドバイス（方向ボーナス・優先マップ・戦闘スタンス）を返してもらう。

必要: ANTHROPIC_API_KEY 環境変数
"""

import json
import logging
from dataclasses import dataclass, field

import anthropic

from emerald_goals import Goal, GoalTracker

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """\
あなたはポケモンエメラルド（日本語版GBA）を強化学習で攻略する際の戦略アドバイザーです。

## あなたの役割
エピソード（10000ステップ）ごとにゲームの進行状況を受け取り、次のエピソードでAIエージェントが取るべき戦略を JSON で返してください。

## ゲームの仕組み
- AIは8つのボタン（A, B, 上, 下, 左, 右, Start, Select）を押すことしかできません
- 観測はRAMから読み取った9次元ベクトルです（X座標, Y座標, マップグループ, マップ番号, バッジ数, HP割合, レベル, パーティ数, イベントフラグ）
- AIには「方向ボーナス」「優先マップ」「戦闘スタンス」を報酬として与えることで間接的に行動を誘導できます

## ポケモンエメラルドの攻略順序
ミシロタウン → Route 101 → コトキタウン → Route 103 → トウカシティ → Route 104 → トウカの森 → カナズミシティ（1stジム：ツツジ）→ カナシダトンネル → ムロタウン（2ndジム：トウキ）→ ...

## 応答フォーマット（必ずこのJSON形式で返す）
```json
{
  "strategy": "今のエピソードの分析と次の戦略の説明（日本語、1-2文）",
  "direction_bonus": {
    "north": 0.0,
    "south": 0.0,
    "east": 0.0,
    "west": 0.0
  },
  "priority_maps": [],
  "battle_stance": "balanced"
}
```

### フィールド説明
- `strategy`: 戦略の概要テキスト
- `direction_bonus`: 各方向への移動にかかるステップごとのボーナス報酬。範囲: -0.005 〜 +0.005。ゲーム進行方向を正に設定する
- `priority_maps`: 次に向かうべきマップの `[map_group, map_num, bonus]` のリスト。bonusは到達時の追加報酬（5〜30）。マップIDが不明なら空リストで良い
- `battle_stance`: 戦闘方針。"aggressive"（レベル上げ優先、レベルアップ報酬1.5倍）、"cautious"（HP維持優先、HP減少ペナルティ1.5倍）、"balanced"（通常）のいずれか

### 注意事項
- direction_bonusは小さい値にすること（大きすぎると壁に向かって歩き続ける）
- 序盤はexploration（探索）を重視し、aggressiveにレベル上げを促す
- AIの行動はまだランダムに近いことを念頭に置く
- JSONのみを返すこと（コードブロックの```json ```で囲まないこと）
"""


@dataclass
class ClaudeGuidance:
    """Claudeからの戦略指示を保持するデータクラス。"""
    direction_bonus: dict[str, float] = field(default_factory=lambda: {
        "north": 0.0, "south": 0.0, "east": 0.0, "west": 0.0
    })
    priority_maps: list[tuple[int, int, float]] = field(default_factory=list)
    battle_stance: str = "balanced"
    strategy_text: str = ""


class ClaudeAdvisor:
    """Claude API を使った戦略アドバイザー。"""

    def __init__(self):
        self.client = anthropic.Anthropic(
            timeout=30.0,  # 30秒タイムアウト（ハング防止）
        )
        self.history: list[dict] = []        # 会話履歴（直近10ターン）
        self.last_guidance = ClaudeGuidance()

    def consult(self, game_summary: dict) -> ClaudeGuidance:
        """
        ゲーム状態サマリーをClaudeに送り、戦略ガイダンスを取得する。
        API障害時は前回のガイダンスを維持する。
        """
        user_msg = self._build_user_message(game_summary)
        self.history.append({"role": "user", "content": user_msg})

        try:
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=512,
                system=SYSTEM_PROMPT,
                messages=self.history,
            )
            assistant_text = response.content[0].text
            self.history.append({"role": "assistant", "content": assistant_text})

            # 履歴を直近10ターン（20メッセージ）に制限
            if len(self.history) > 20:
                self.history = self.history[-20:]

            guidance = self._parse_response(assistant_text)
            self.last_guidance = guidance
            return guidance

        except Exception as e:
            logger.warning(f"Claude API エラー: {e}")
            # 失敗したuser messageを取り除く
            self.history.pop()
            return self.last_guidance

    def _build_user_message(self, summary: dict) -> str:
        """ゲーム状態サマリーからClaudeへのメッセージを構築する。"""
        goal_status = summary.get("goal_status", {})
        lines = [
            "## エピソード終了レポート",
            f"- 総ステップ数: {summary.get('total_steps', 0):,}",
            f"- エピソード報酬: {summary.get('episode_reward', 0):.1f}",
            f"- 現在位置: マップ({summary.get('map_group', 0)}, {summary.get('map_num', 0)}) "
            f"座標({summary.get('x', 0)}, {summary.get('y', 0)})",
            f"- レベル: {summary.get('level', 0)}",
            f"- パーティ数: {summary.get('party', 0)}",
            f"- バッジ: {summary.get('badges', 0)}",
            f"- HP: {summary.get('hp_cur', 0)}/{summary.get('hp_max', 0)}",
            f"- イベントフラグ: {summary.get('flags', 0)}",
            f"- 探索タイル数: {summary.get('visited_tiles', 0)}",
            f"- 到達マップ数: {summary.get('visited_maps', 0)}",
            "",
            "## 目標進捗",
            f"- 達成済み: {goal_status.get('achieved_goals', [])}",
            f"- 現在の目標: {goal_status.get('current_goal', '不明')}",
            f"  → {goal_status.get('current_goal_description', '')}",
            f"- 残り目標数: {goal_status.get('remaining_goals', '不明')}",
        ]
        return "\n".join(lines)

    def _parse_response(self, text: str) -> ClaudeGuidance:
        """Claudeの応答JSONをパースしてClaudeGuidanceに変換する。"""
        # コードブロックで囲まれている場合に対応
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = lines[1:]  # ```json を除去
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning(f"Claude応答のJSONパース失敗: {text[:200]}")
            return self.last_guidance

        guidance = ClaudeGuidance()

        # strategy
        guidance.strategy_text = data.get("strategy", "")

        # direction_bonus（値をクランプ）
        db = data.get("direction_bonus", {})
        for direction in ["north", "south", "east", "west"]:
            val = db.get(direction, 0.0)
            guidance.direction_bonus[direction] = max(-0.005, min(0.005, float(val)))

        # priority_maps
        pm = data.get("priority_maps", [])
        for entry in pm:
            if isinstance(entry, list) and len(entry) >= 3:
                guidance.priority_maps.append(
                    (int(entry[0]), int(entry[1]), float(entry[2]))
                )

        # battle_stance
        stance = data.get("battle_stance", "balanced")
        if stance in ("aggressive", "cautious", "balanced"):
            guidance.battle_stance = stance
        else:
            guidance.battle_stance = "balanced"

        return guidance
