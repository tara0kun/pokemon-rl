"""
ポケモンエメラルド 目標定義 + 達成チェック
==========================================
ゲーム進行順の目標を定義し、達成判定を行う。
ClaudeAdvisor と連携して目標ベースの報酬を提供する。
"""

from dataclasses import dataclass, field


@dataclass
class Goal:
    """ゲーム進行上の目標。"""
    name: str                                    # 目標名
    description: str                             # 詳細説明
    reward: float = 50.0                         # 達成報酬
    target_badges: int | None = None             # 必要バッジ数（None=条件なし）
    target_level: int | None = None              # 必要レベル（None=条件なし）
    target_maps: list[tuple[int, int]] = field(  # 到達判定マップ [(mg, mn)]
        default_factory=list
    )

    def is_achieved(self, info: dict) -> bool:
        """infoからこの目標が達成されたかをチェックする。"""
        # バッジ条件
        if self.target_badges is not None:
            if info.get("badges", 0) < self.target_badges:
                return False

        # レベル条件
        if self.target_level is not None:
            if info.get("level", 0) < self.target_level:
                return False

        # マップ条件（いずれかに到達していればOK）
        if self.target_maps:
            mg = info.get("map_group", -1)
            mn = info.get("map_num", -1)
            if (mg, mn) not in self.target_maps:
                return False

        return True


# ─── ゲーム進行順の目標リスト ─────────────────────────────────────────────
# マップIDは実行時にClaudeが動的に設定可能。
# バッジ数ベースの目標は確実に判定できる。
EMERALD_GOALS = [
    # === 序盤レベリング (Route 101, 推奨Lv8) ===
    Goal(
        name="レベル7到達",
        description="Route 101でLv7まで育てる",
        reward=20.0,
        target_level=7,
    ),
    Goal(
        name="レベル8到達",
        description="Route 101でLv8まで育てる（103番道路ライバル戦準備）",
        reward=15.0,
        target_level=8,
    ),
    # === コトキタウン (推奨Lv8) ===
    Goal(
        name="コトキタウン到達",
        description="Route 101北端のコトキタウン(mn=10)へ。ポケモンセンターで回復",
        reward=30.0,
        target_maps=[(0, 10)],
    ),
    # === 103番道路 (推奨Lv8) ===
    Goal(
        name="103番道路到達",
        description="コトキタウンから北上し103番道路へ。ハルカとバトル",
        reward=40.0,
        target_maps=[(0, 18)],  # Route 103 map num (要確認)
    ),
    # === ミシロタウン → トウカンティ (推奨Lv8後) ===
    Goal(
        name="レベル10到達",
        description="Lv10まで育てる（コンバスケン進化準備）",
        reward=30.0,
        target_level=10,
    ),
    Goal(
        name="トウカンティ到達",
        description="102番道路を通りトウカンティ(Petalburg City)へ",
        reward=50.0,
        target_maps=[(0, 3)],  # Petalburg City map num (要確認)
    ),
    # === トウカの森・カナズミシティ (推奨Lv11-16) ===
    Goal(
        name="レベル12到達",
        description="Lv12到達（トウカの森攻略準備）",
        reward=20.0,
        target_level=12,
    ),
    Goal(
        name="レベル16到達",
        description="Lv16到達（ワカシャモ進化、カナズミジム準備）",
        reward=40.0,
        target_level=16,
    ),
    Goal(
        name="1stバッジ取得",
        description="カナズミジム（ツツジ/岩タイプ）を倒してストーンバッジを入手",
        reward=200.0,
        target_badges=1,
    ),
    # === ムロタウン (推奨Lv16) ===
    Goal(
        name="2ndバッジ取得",
        description="ムロジム（トウキ/格闘タイプ）を倒してナックルバッジを入手",
        reward=200.0,
        target_badges=2,
    ),
    # === キンセツシティ (推奨Lv25) ===
    Goal(
        name="レベル21到達",
        description="Lv21到達（110番道路ライバル戦準備）",
        reward=30.0,
        target_level=21,
    ),
    Goal(
        name="3rdバッジ取得",
        description="キンセツジム（テッセン/電気タイプ）を倒してダイナモバッジを入手",
        reward=200.0,
        target_badges=3,
    ),
    # === フエンタウン (推奨Lv30) ===
    Goal(
        name="レベル30到達",
        description="Lv30到達（えんとつ山攻略準備）",
        reward=40.0,
        target_level=30,
    ),
    Goal(
        name="4thバッジ取得",
        description="フエンジム（アスナ/炎タイプ）を倒してヒートバッジを入手",
        reward=200.0,
        target_badges=4,
    ),
    # === トウカシティ (推奨Lv30+) ===
    Goal(
        name="5thバッジ取得",
        description="トウカジム（センリ/ノーマルタイプ）を倒してバランスバッジを入手",
        reward=200.0,
        target_badges=5,
    ),
    # === ヒワマキシティ ===
    Goal(
        name="6thバッジ取得",
        description="ヒワマキジム（ナギ/飛行タイプ）を倒してフェザーバッジを入手",
        reward=200.0,
        target_badges=6,
    ),
    # === トクサネシティ ===
    Goal(
        name="7thバッジ取得",
        description="トクサネジム（フウとラン/エスパータイプ）を倒してマインドバッジを入手",
        reward=200.0,
        target_badges=7,
    ),
    # === ルネシティ ===
    Goal(
        name="8thバッジ取得",
        description="ルネジム（ミクリ/水タイプ）を倒してレインバッジを入手",
        reward=200.0,
        target_badges=8,
    ),
]


class GoalTracker:
    """目標の進行管理。"""

    def __init__(self):
        self.goals = list(EMERALD_GOALS)
        self.current_index = 0
        self.achieved: list[str] = []  # 達成済み目標名のリスト

    @property
    def current_goal(self) -> Goal | None:
        if self.current_index < len(self.goals):
            return self.goals[self.current_index]
        return None

    def check_achieved(self, info: dict) -> Goal | None:
        """現在の目標が達成されたかチェック。達成ならGoalを返し次へ進む。"""
        goal = self.current_goal
        if goal is None:
            return None
        if goal.is_achieved(info):
            self.achieved.append(goal.name)
            self.current_index += 1
            return goal
        return None

    def get_status(self) -> dict:
        """Claudeに送る進捗情報。"""
        goal = self.current_goal
        return {
            "achieved_goals": self.achieved,
            "current_goal": goal.name if goal else "全目標達成",
            "current_goal_description": goal.description if goal else "",
            "remaining_goals": len(self.goals) - self.current_index,
        }
