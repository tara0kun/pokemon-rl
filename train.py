"""
Pokemon Emerald PPO Training Script (v4 - Multi Environment)
=============================================================
Usage:
  シングル環境:
    1. mGBA + mGBA-http 起動
    2. python train.py

  マルチ環境 (3並列):
    1. mGBA x3 起動 + 各Luaスクリプトロード
    2. python launch_multi.py  (mGBA-http x3 起動)
    3. python train.py

  設定: PORTS リストでポート数を調整（1個=シングル、3個=3並列）

Output:
  models/       Checkpoints (every 5000 steps)
  logs/         TensorBoard logs -> tensorboard --logdir logs/
  screenshots/  Periodic game screenshots (debug)
"""

import os
import sys
import time
import glob
import faulthandler
faulthandler.enable()
# ★ v10.9z192: faulthandler無効化（WatchdogSessionがHTTPハングを処理するため不要）
# 旧: 120s→600sタイムアウトだが、起動後N秒で無条件kill→正常運転中にクラッシュ
# faulthandler.dump_traceback_later(600, repeat=False, exit=True, file=sys.stderr)

import requests
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.callbacks import BaseCallback

from pokemon_env import PokemonEmeraldEnv, OBS_DIM
from claude_advisor import ClaudeAdvisor, ClaudeGuidance
from emerald_goals import GoalTracker

# ─── マルチ環境設定 ──────────────────────────────────────────────────────
# ポート数 = 環境数。1個ならシングル、複数ならSubprocVecEnvで並列化
PORTS = [8888, 8889, 8890]  # 3並列環境（mGBAデフォルトポート）
N_ENVS = len(PORTS)
MGBA_URL  = f"http://localhost:{PORTS[0]}"  # プライマリ（セーブ/スクリーンショット用）
GAME_SLOT = 4  # Slot 2 = story sync, Slot 4 = training progress


def mgba_save_game(slot: int = GAME_SLOT, port: int = None) -> bool:
    url = f"http://localhost:{port}" if port else MGBA_URL
    try:
        r = requests.post(f"{url}/core/savestateslot", params={"slot": slot}, timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def mgba_load_game(slot: int = GAME_SLOT, port: int = None) -> bool:
    url = f"http://localhost:{port}" if port else MGBA_URL
    try:
        r = requests.post(f"{url}/core/loadstateslot", params={"slot": slot}, timeout=5)
        if r.status_code == 200:
            time.sleep(0.5)
            return True
        return False
    except Exception:
        return False


def mgba_screenshot(filepath: str) -> bool:
    """Save mGBA screenshot to file (primary instance only)."""
    try:
        r = requests.post(
            f"{MGBA_URL}/core/screenshot",
            params={"path": filepath},
            timeout=5,
        )
        return r.status_code == 200
    except Exception:
        return False


# --- Directory setup ---
LOG_DIR        = "./logs/"
SAVE_DIR       = "./models/"
SCREENSHOT_DIR = "./screenshots/"
os.makedirs(LOG_DIR,        exist_ok=True)
os.makedirs(SAVE_DIR,       exist_ok=True)
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


# --- RND Predictor update callback ---
class CuriosityUpdateCallback(BaseCallback):
    def _on_step(self) -> bool:
        return True

    def _on_rollout_end(self) -> None:
        # env_method: 全環境のRNDを更新、env0のlossをログ
        losses = self.training_env.env_method("rnd_update")
        loss = losses[0] if losses else 0
        if loss > 0:
            self.logger.record("curiosity/rnd_loss", loss)


# --- Checkpoint + game state save callback ---
class CheckpointWithGameStateCallback(BaseCallback):
    def __init__(self, save_freq: int, save_path: str, name_prefix: str = "pokemon"):
        super().__init__()
        self.save_freq   = save_freq
        self.save_path   = save_path
        self.name_prefix = name_prefix

    def _on_step(self) -> bool:
        if self.n_calls % self.save_freq == 0:
            path = os.path.join(
                self.save_path, f"{self.name_prefix}_{self.num_timesteps}_steps"
            )
            self.model.save(path)
            ok = mgba_save_game(GAME_SLOT)
            status = "OK" if ok else "FAIL"
            print(f"  [Save] {path}.zip | slot{GAME_SLOT}: {status}")
        return True


# --- Screenshot callback ---
class ScreenshotCallback(BaseCallback):
    """Periodically save mGBA screenshots for debugging (primary only)."""

    def __init__(self, save_freq: int = 5000):
        super().__init__()
        self.save_freq = save_freq
        self._available = True

    def _on_step(self) -> bool:
        if not self._available:
            return True
        if self.n_calls % self.save_freq == 0:
            filepath = os.path.abspath(os.path.join(
                SCREENSHOT_DIR, f"step_{self.num_timesteps}.png"
            ))
            ok = mgba_screenshot(filepath)
            if not ok and self.n_calls <= self.save_freq:
                print("  [Screenshot] API not available, disabling")
                self._available = False
        return True


# --- Progress display callback ---
class ProgressCallback(BaseCallback):
    def __init__(self, print_freq: int = 500, report_freq: int = 10):
        super().__init__()
        self.print_freq      = print_freq
        self.report_freq     = report_freq  # N エピソードごとにレポート更新
        self.start_time      = time.time()
        self.start_timesteps = 0
        self.ep_count        = 0
        self.best_badges     = 0
        self.best_tiles      = 0
        self.best_level      = 0
        self.best_maps       = 0
        self.best_exp        = 0
        self.total_battles   = 0
        # 経過レポート用
        self._ep_history: list[dict] = []
        self._best_reward   = -float("inf")
        self._total_blackouts = 0
        self._total_loops     = 0
        self._repel_checked   = False  # Repel診断（1回のみ）
        # ★ v10.9z251g: EXP停滞自動検出+環境リセット（自立稼働用）
        self._last_exp_change_step = 0
        self._last_exp_value = 0
        self._stale_reset_count = 0

    def _on_training_start(self) -> None:
        self.start_timesteps = self.num_timesteps

    def _on_step(self) -> bool:
        infos = self.locals.get("infos", [{}])

        # 全環境のinfoを処理（マルチ環境対応）
        for info in infos:
            cur_level  = info.get("level", 0)
            cur_tiles  = info.get("visited_tiles", 0)
            cur_maps   = info.get("visited_maps", 0)
            cur_badges = info.get("badges", 0)
            cur_exp    = info.get("exp", 0) or 0
            if cur_level  and cur_level  > self.best_level:  self.best_level  = cur_level
            if cur_tiles  and cur_tiles  > self.best_tiles:  self.best_tiles  = cur_tiles
            if cur_maps   and cur_maps   > self.best_maps:   self.best_maps   = cur_maps
            if cur_badges and cur_badges > self.best_badges: self.best_badges = cur_badges
            if cur_exp    and cur_exp    > self.best_exp:    self.best_exp    = cur_exp

            # Episode end display
            if "episode" in info:
                self.ep_count += 1
                ep_r   = info["episode"]["r"]
                ep_l   = info["episode"]["l"]
                level  = info.get("level", 0)
                party  = info.get("party", 0)
                exp    = info.get("exp", 0)
                mg     = info.get("map_group", 0)
                mn     = info.get("map_num", 0)
                badges = info.get("badges", 0)
                tiles  = info.get("visited_tiles", 0)
                maps   = info.get("visited_maps", 0)
                flags  = info.get("flags", 0)
                stuck  = info.get("stuck_count", 0)
                battles = info.get("battle_wins", 0)
                self.total_battles += battles
                hp_cur = info.get("hp_cur", 0)
                hp_max = info.get("hp_max", 0)
                blackouts = info.get("blackouts", 0)
                loops = info.get("loops", 0)

                print(
                    f"  EP {self.ep_count:4d} | "
                    f"R:{ep_r:8.1f} | "
                    f"L:{ep_l:5d} | "
                    f"Lv{level} PT{party} EXP{exp} | "
                    f"HP:{hp_cur}/{hp_max} | "
                    f"Map({mg},{mn}) | "
                    f"Badge{badges} | "
                    f"Tile{tiles} Map{maps} | "
                    f"Btl{battles} BO{blackouts} Lp{loops} | "
                    f"Fl{flags} Stk{stuck}"
                )

                # レポート用にエピソード結果を蓄積
                self._ep_history.append({
                    "ep": self.ep_count, "step": self.num_timesteps,
                    "reward": ep_r, "level": level, "party": party,
                    "exp": exp, "badges": badges, "tiles": tiles,
                    "maps": maps, "battles": battles, "blackouts": blackouts,
                    "loops": loops, "stuck": stuck,
                })
                if ep_r > self._best_reward:
                    self._best_reward = ep_r
                self._total_blackouts += blackouts
                self._total_loops += loops

                # N エピソードごとにレポートを更新
                if self.ep_count % self.report_freq == 0:
                    self._update_report()

        # Periodic progress
        if self.n_calls % self.print_freq == 0:
            elapsed   = time.time() - self.start_time
            new_steps = self.num_timesteps - self.start_timesteps
            sps       = new_steps / elapsed if elapsed > 0 else 0

            # 最新のマップ座標を取得
            latest_mg = infos[0].get("map_group", 0) if infos else 0
            latest_mn = infos[0].get("map_num", 0) if infos else 0
            battle_starts = max(info.get("battle_starts", 0) for info in infos) if infos else 0
            print(
                f"[{self.num_timesteps:>9,} steps] "
                f"{sps:.1f} sps | "
                f"Lv{self.best_level} | "
                f"Badge{self.best_badges} | "
                f"Tile{self.best_tiles} | "
                f"Map{self.best_maps}({latest_mg},{latest_mn}) | "
                f"EXP{self.best_exp} | "
                f"Btl{self.total_battles}({battle_starts}) | "
                f"Env{N_ENVS}"
            )

            # ★ v10.9z251g: EXP停滞自動検出+stale状態リセット
            # EXPが3000step(=6回のprint_freq)変化なし→各環境のstale状態をリセット
            _total_exp = sum(info.get("exp", 0) or 0 for info in infos)
            if _total_exp != self._last_exp_value and _total_exp > 0:
                self._last_exp_change_step = self.num_timesteps
                self._last_exp_value = _total_exp
            _exp_stale_steps = self.num_timesteps - self._last_exp_change_step
            if _exp_stale_steps >= 3000 and self._last_exp_change_step > 0:
                # 各環境のstale状態をリセット
                self._stale_reset_count += 1
                print(f"  [AUTO-RESET] EXP stagnation {_exp_stale_steps} steps. "
                      f"Resetting stale state (#{self._stale_reset_count})")
                try:
                    envs = self.training_env.envs if hasattr(self.training_env, 'envs') else []
                    for env in envs:
                        _inner = env
                        while hasattr(_inner, 'env'):
                            _inner = _inner.env
                        if hasattr(_inner, '_stale_battle_suppress'):
                            _inner._stale_battle_suppress = False
                            _inner._battle_reentry_cd = 0
                            _inner._fa_backoff_cd = 100
                            _inner._battle_cycle_counter = 0
                            _inner._same_pos_count = 0
                except Exception as e:
                    print(f"  [AUTO-RESET] Error: {e}")
                self._last_exp_change_step = self.num_timesteps

            # Repel診断: 最初の3回だけ直接APIで確認
            if not self._repel_checked or self.n_calls % 5000 == 0:
                try:
                    import requests as _req
                    for port in PORTS[:1]:
                        s = _req.Session()
                        # SB1ポインタ読み取り
                        r = s.get(f"http://localhost:{port}/core/read32",
                                  params={"address": hex(0x03005AEC)}, timeout=1)
                        sb1 = int(r.json()["value"]) if isinstance(r.json(), dict) else int(r.json())
                        if sb1 and sb1 > 0x02000000:
                            # VAR_REPEL_STEP_COUNT (SB1+0x13DE)
                            r = s.get(f"http://localhost:{port}/core/read16",
                                      params={"address": hex(sb1 + 0x13DE)}, timeout=1)
                            repel = int(r.json()["value"]) if isinstance(r.json(), dict) else int(r.json())
                            # 周辺vars
                            r2 = s.get(f"http://localhost:{port}/core/read16",
                                       params={"address": hex(sb1 + 0x13DC)}, timeout=1)
                            v1 = int(r2.json()["value"]) if isinstance(r2.json(), dict) else int(r2.json())
                            r3 = s.get(f"http://localhost:{port}/core/read16",
                                       params={"address": hex(sb1 + 0x13E0)}, timeout=1)
                            v2 = int(r3.json()["value"]) if isinstance(r3.json(), dict) else int(r3.json())
                            print(f"  [Repel-Diag] Port{port} SB1=0x{sb1:08X} "
                                  f"VAR_REPEL(+13DE)={repel} "
                                  f"+13DC={v1} +13E0={v2}")
                        s.close()
                    self._repel_checked = True
                except Exception as e:
                    print(f"  [Repel-Diag] Error: {e}")

        return True

    def _update_report(self) -> None:
        """PROGRESS.mdに学習経過レポートを書き出す。"""
        import datetime
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        elapsed = time.time() - self.start_time
        hours = elapsed / 3600

        # 直近10エピソードの統計
        recent = self._ep_history[-10:]
        avg_r = sum(e["reward"] for e in recent) / len(recent)
        avg_lv = sum(e["level"] for e in recent) / len(recent)
        avg_btl = sum(e["battles"] for e in recent) / len(recent)
        avg_tiles = sum(e["tiles"] for e in recent) / len(recent)
        avg_bo = sum(e["blackouts"] for e in recent) / len(recent)
        avg_loops = sum(e["loops"] for e in recent) / len(recent)
        avg_stuck = sum(e["stuck"] for e in recent) / len(recent)
        max_r = max(e["reward"] for e in recent)
        max_lv = max(e["level"] for e in recent)
        max_tiles = max(e["tiles"] for e in recent)

        # 全期間統計
        all_eps = self._ep_history
        total_avg_r = sum(e["reward"] for e in all_eps) / len(all_eps) if all_eps else 0

        lines = [
            f"# Pokemon RL 学習経過レポート",
            f"",
            f"最終更新: {now} | 総ステップ: {self.num_timesteps:,} | "
            f"エピソード: {self.ep_count} | 稼働: {hours:.1f}h",
            f"",
            f"## ベスト記録（全期間）",
            f"| 項目 | 値 |",
            f"|------|-----|",
            f"| レベル | {self.best_level} |",
            f"| バッジ | {self.best_badges} |",
            f"| 探索タイル | {self.best_tiles} |",
            f"| 探索マップ | {self.best_maps} |",
            f"| 経験値 | {self.best_exp} |",
            f"| 最高報酬 | {self._best_reward:.1f} |",
            f"| 累計勝利 | {self.total_battles} |",
            f"",
            f"## 直近10エピソード平均",
            f"| 項目 | 平均 | 最大 |",
            f"|------|------|------|",
            f"| 報酬 | {avg_r:.1f} | {max_r:.1f} |",
            f"| レベル | {avg_lv:.1f} | {max_lv} |",
            f"| タイル | {avg_tiles:.1f} | {max_tiles} |",
            f"| 戦闘勝利 | {avg_btl:.1f} | - |",
            f"| ブラックアウト | {avg_bo:.1f} | - |",
            f"| ループ検知 | {avg_loops:.0f} | - |",
            f"| 最大スタック | {avg_stuck:.0f} | - |",
            f"",
            f"## 直近エピソード一覧",
            f"| EP | Steps | 報酬 | Lv | PT | Btl | BO | Tile | Map | Lp | Stk |",
            f"|----|-------|------|----|----|-----|-----|------|-----|----|-----|",
        ]
        for e in self._ep_history[-20:]:
            lines.append(
                f"| {e['ep']} | {e['step']:,} | {e['reward']:.0f} | "
                f"{e['level']} | {e['party']} | {e['battles']} | "
                f"{e['blackouts']} | {e['tiles']} | {e['maps']} | "
                f"{e['loops']} | {e['stuck']} |"
            )

        lines.append("")
        lines.append(f"## 問題分析")
        if avg_bo > 1.0:
            lines.append(f"- **ブラックアウト多発**: 平均{avg_bo:.1f}回/EP → HP管理改善が必要")
        if avg_stuck > 1000:
            lines.append(f"- **長時間スタック**: 平均{avg_stuck:.0f}ステップ → メニュートラップの可能性")
        if avg_btl < 1.0:
            lines.append(f"- **戦闘不足**: 平均{avg_btl:.1f}勝/EP → 草むらへの誘導が必要")
        if avg_loops > 100:
            lines.append(f"- **ループ多発**: 平均{avg_loops:.0f}回/EP → 行動多様性の改善が必要")
        if avg_bo <= 1.0 and avg_stuck <= 1000 and avg_btl >= 1.0 and avg_loops <= 100:
            lines.append(f"- 特に大きな問題は検出されていません")

        try:
            with open("PROGRESS.md", "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception:
            pass


# --- Claude strategy advisor + milestone save callback ---
class ClaudeAdvisorCallback(BaseCallback):
    """
    Per-episode Claude consultation + milestone saves on badge acquisition.
    """

    def __init__(self):
        super().__init__()
        self.advisor: ClaudeAdvisor | None = None
        self.goal_tracker = GoalTracker()
        self._init_failed = False
        self._last_badges = 0

    def _on_training_start(self) -> None:
        try:
            self.advisor = ClaudeAdvisor()
            print("  [Claude] Strategy advisor OK")
            goal = self.goal_tracker.current_goal
            if goal:
                print(f"  [Claude] First goal: {goal.name} - {goal.description}")
        except Exception as e:
            print(f"  [Claude] Init failed: {e}")
            print("  [Claude] Continuing without Claude")
            self._init_failed = True

    def _on_step(self) -> bool:
        if self._init_failed:
            return True

        infos = self.locals.get("infos", [{}])
        # env0のinfoのみ使用（Claude相談+マイルストーン）
        info = infos[0] if infos else {}

        # Goal check every step
        achieved = self.goal_tracker.check_achieved(info)
        if achieved:
            print(f"  [Goal] Achieved: {achieved.name} (+{achieved.reward})")
            next_goal = self.goal_tracker.current_goal
            if next_goal:
                print(f"  [Goal] Next: {next_goal.name} - {next_goal.description}")
            else:
                print("  [Goal] All goals achieved!")

        # Milestone save on badge acquisition (slots 3-10)
        cur_badges = info.get("badges", 0)
        if cur_badges > self._last_badges:
            milestone_slot = 2 + cur_badges
            ok = mgba_save_game(milestone_slot)
            status = "OK" if ok else "FAIL"
            print(f"  [Milestone] Badge {cur_badges}! Saved slot {milestone_slot}: {status}")
            mgba_save_game(GAME_SLOT)
            self._last_badges = cur_badges

        # Consult Claude at episode end (env0 only)
        if "episode" in info and self.advisor is not None:
            self._consult_claude(info)

        return True

    def _consult_claude(self, info: dict) -> None:
        summary = {
            "total_steps":   self.num_timesteps,
            "episode_reward": info.get("episode", {}).get("r", 0),
            "x":             info.get("x", 0),
            "y":             info.get("y", 0),
            "map_group":     info.get("map_group", 0),
            "map_num":       info.get("map_num", 0),
            "level":         info.get("level", 0),
            "party":         info.get("party", 0),
            "badges":        info.get("badges", 0),
            "hp_cur":        info.get("hp_cur", 0),
            "hp_max":        info.get("hp_max", 0),
            "flags":         info.get("flags", 0),
            "exp":           info.get("exp", 0),
            "visited_tiles": info.get("visited_tiles", 0),
            "visited_maps":  info.get("visited_maps", 0),
            "stuck_count":   info.get("stuck_count", 0),
            "goal_status":   self.goal_tracker.get_status(),
        }

        print("  [Claude] Consulting...")
        guidance = self.advisor.consult(summary)

        # 全環境にガイダンスを設定
        self.training_env.env_method("set_claude_guidance", guidance)

        # TensorBoard logging
        self.logger.record("claude/battle_stance", guidance.battle_stance)
        self.logger.record("claude/direction_north", guidance.direction_bonus.get("north", 0))
        self.logger.record("claude/direction_east", guidance.direction_bonus.get("east", 0))
        self.logger.record("claude/goals_achieved", len(self.goal_tracker.achieved))

        # Console output
        print(f"  [Claude] {guidance.strategy_text}")
        print(f"  [Claude] Stance: {guidance.battle_stance} | "
              f"Dir: N={guidance.direction_bonus.get('north', 0):+.3f} "
              f"S={guidance.direction_bonus.get('south', 0):+.3f} "
              f"E={guidance.direction_bonus.get('east', 0):+.3f} "
              f"W={guidance.direction_bonus.get('west', 0):+.3f}")
        if guidance.priority_maps:
            maps_str = ", ".join(f"({m[0]},{m[1]})+{m[2]}" for m in guidance.priority_maps)
            print(f"  [Claude] Priority: {maps_str}")


# --- Per-env comparison + best state sharing callback ---
class EnvComparisonCallback(BaseCallback):
    """
    環境間パフォーマンス比較 + ベストステート共有。
    - 定期的に各環境のメトリクスを比較し、TensorBoard+コンソールに出力
    - 大きく遅れている環境がある場合、最優秀環境のゲーム状態をコピー
    """

    def __init__(self, compare_freq: int = 2500, share_cooldown: int = 15000):
        super().__init__()
        self.compare_freq = compare_freq
        self.share_cooldown = share_cooldown      # 共有後のクールダウン（ステップ）
        self._last_shared_step = 0
        self._share_count = 0

    def _on_step(self) -> bool:
        if N_ENVS < 2:
            return True
        if self.n_calls % self.compare_freq != 0:
            return True

        metrics_list = self.training_env.env_method("get_metrics")
        self._log_comparison(metrics_list)

        # クールダウン経過後のみ状態共有を検討
        if self.num_timesteps - self._last_shared_step >= self.share_cooldown:
            self._maybe_share_state(metrics_list)

        return True

    def _compute_score(self, m: dict) -> float:
        """複合パフォーマンススコア。"""
        return (
            m.get("level", 0) * 10
            + m.get("badges", 0) * 100
            + m.get("exp", 0) * 0.01
            + m.get("visited_tiles", 0) * 0.5
            + m.get("battle_wins", 0) * 5
        )

    def _log_comparison(self, metrics_list: list[dict]) -> None:
        """各環境のメトリクスをTensorBoard+コンソールに出力。"""
        scores = []
        for i, m in enumerate(metrics_list):
            score = self._compute_score(m)
            scores.append(score)
            self.logger.record(f"env{i}/level", m.get("level", 0))
            self.logger.record(f"env{i}/exp", m.get("exp", 0))
            self.logger.record(f"env{i}/tiles", m.get("visited_tiles", 0))
            self.logger.record(f"env{i}/battles", m.get("battle_wins", 0))
            self.logger.record(f"env{i}/score", score)

        best_idx = max(range(len(scores)), key=lambda i: scores[i])
        self.logger.record("compare/best_env", best_idx)
        self.logger.record("compare/score_spread",
                           max(scores) - min(scores) if scores else 0)

        # コンソール出力
        parts = []
        for i, (m, s) in enumerate(zip(metrics_list, scores)):
            marker = "*" if i == best_idx else " "
            parts.append(
                f"E{i}{marker}(Lv{m.get('level',0)} "
                f"T{m.get('visited_tiles',0)} "
                f"B{m.get('battle_wins',0)} "
                f"S{s:.0f})"
            )
        print(f"  [Compare] {' | '.join(parts)}")

    def _maybe_share_state(self, metrics_list: list[dict]) -> None:
        """最優秀環境の状態を大きく遅れている環境にコピー。"""
        scores = [self._compute_score(m) for m in metrics_list]
        best_idx = max(range(len(scores)), key=lambda i: scores[i])
        best_score = scores[best_idx]

        if best_score <= 0:
            return

        # ベストの50%未満の環境を「遅れている」と判定
        behind = [
            i for i, s in enumerate(scores)
            if i != best_idx and s < best_score * 0.5
        ]

        if not behind:
            return

        # ベスト環境のみ slot 9 に保存
        save_ok = self.training_env.env_method(
            "save_best_state", indices=[best_idx]
        )
        if not save_ok[0]:
            return

        time.sleep(0.5)  # ファイル書き込み完了待ち

        # 遅れている環境のみ slot 9 からロード
        sync_ok = self.training_env.env_method(
            "sync_from_best", indices=behind
        )
        for j, idx in enumerate(behind):
            if sync_ok[j]:
                self._share_count += 1
                print(
                    f"  [Share #{self._share_count}] "
                    f"E{best_idx}→E{idx} "
                    f"(score {best_score:.0f}→{scores[idx]:.0f})"
                )

        self._last_shared_step = self.num_timesteps


# --- Find latest checkpoint ---
def find_latest_checkpoint() -> str | None:
    pattern = os.path.join(SAVE_DIR, "pokemon_*_steps.zip")
    files   = glob.glob(pattern)
    if not files:
        return None
    def extract_steps(path):
        base = os.path.basename(path)
        try:
            return int(base.split("_")[1])
        except (IndexError, ValueError):
            return 0
    return max(files, key=extract_steps)


# --- Environment factory ---
def make_env(port: int):
    """指定ポートのmGBA-httpに接続するenv生成関数を返す。"""
    def _init():
        env = PokemonEmeraldEnv(port=port, max_steps=30000, step_delay=0.0)
        env = Monitor(env)
        return env
    return _init


# --- New model factory ---
def _create_new_model(env) -> PPO:
    return PPO(
        policy          = "MlpPolicy",
        env             = env,
        verbose         = 0,
        learning_rate   = 3e-4,
        n_steps         = 2048,
        batch_size      = 64,
        n_epochs        = 10,
        gamma           = 0.999,
        gae_lambda      = 0.95,
        clip_range      = 0.2,
        ent_coef        = 0.05,
        vf_coef         = 0.5,
        max_grad_norm   = 0.5,
        tensorboard_log = LOG_DIR,
    )


# --- Stdout → file tee ---
class TeeOutput:
    """stdout/stderrをファイルにも書き出す。"""
    def __init__(self, filepath, original):
        self._original = original
        self._file = open(filepath, "a", encoding="utf-8", buffering=1)

    def write(self, text):
        self._original.write(text)
        try:
            self._file.write(text)
        except Exception:
            pass

    def flush(self):
        self._original.flush()
        try:
            self._file.flush()
        except Exception:
            pass


LOG_FILE = "training_current.log"
LOG_ARCHIVE_DIR = "logs/archive"
LOG_ROTATE_BYTES = 200 * 1024  # 200KB超えたらアーカイブ


def _rotate_log_if_needed():
    """起動時に現在のログが大きければ logs/archive/ へ移動する。ルートには1ファイルのみ保持。"""
    os.makedirs(LOG_ARCHIVE_DIR, exist_ok=True)
    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > LOG_ROTATE_BYTES:
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(LOG_ARCHIVE_DIR, f"training_{ts}.log")
        try:
            os.rename(LOG_FILE, dest)
            print(f"[LogRotate] Archived old log → {dest}", file=sys.__stdout__)
        except PermissionError:
            # Windows: file still held open by previous process → skip archive
            print(f"[LogRotate] Skipped (file in use): {LOG_FILE}", file=sys.__stdout__)


# --- Main training loop ---
def train():
    _rotate_log_if_needed()
    sys.stdout = TeeOutput(LOG_FILE, sys.stdout)
    sys.stderr = TeeOutput(LOG_FILE, sys.stderr)

    print("=" * 60)
    print(f"  Pokemon Emerald PPO Training (v4 - {N_ENVS} envs)")
    print(f"  Obs dim: {OBS_DIM} | Episode: 15000 steps")
    print(f"  Ports: {PORTS}")
    print("=" * 60)

    # DummyVecEnv（安定性優先、SubprocVecEnvはWindowsでパイプデッドロック発生）
    env_fns = [make_env(p) for p in PORTS]
    env = DummyVecEnv(env_fns)
    print(f"  DummyVecEnv: {N_ENVS} environments (sequential)")

    # Checkpoint loading with obs_dim compatibility check
    checkpoint = find_latest_checkpoint()
    if checkpoint:
        try:
            model = PPO.load(checkpoint, env=env)
            obs_shape = model.observation_space.shape[0]
            if obs_shape != OBS_DIM:
                print(f"  Checkpoint obs_dim={obs_shape} != current {OBS_DIM}")
                print(f"  Starting fresh (new observation space)")
                raise ValueError("obs_dim mismatch")
            print(f"Resuming: {checkpoint}")
            # ゲーム状態は env.reset() で slot 1 からロードされる
            # (メモリ書き換え禁止のため、毎エピソード初期状態から開始)
            reset_timesteps = False
        except Exception:
            print("Starting fresh training...")
            model = _create_new_model(env)
            reset_timesteps = True
    else:
        print("Starting fresh training...")
        # Fresh start: load slot 1 on all mGBA instances
        for port in PORTS:
            if mgba_load_game(1, port=port):
                print(f"  Port {port}: slot 1 loaded")
            else:
                print(f"  Port {port}: using current state")
        model = _create_new_model(env)
        reset_timesteps = True

    # Callbacks
    checkpoint_cb  = CheckpointWithGameStateCallback(save_freq=5000, save_path=SAVE_DIR)
    progress_cb    = ProgressCallback(print_freq=500)
    curiosity_cb   = CuriosityUpdateCallback()
    claude_cb      = ClaudeAdvisorCallback()
    screenshot_cb  = ScreenshotCallback(save_freq=5000)
    comparison_cb  = EnvComparisonCallback(compare_freq=2500, share_cooldown=15000)

    print(f"\nTensorBoard: tensorboard --logdir {LOG_DIR}")
    print("Ctrl+C to stop (auto-save).\n")
    print("-" * 60)

    try:
        model.learn(
            total_timesteps     = 100_000_000,
            callback            = [
                checkpoint_cb, progress_cb, curiosity_cb,
                claude_cb, screenshot_cb, comparison_cb,
            ],
            reset_num_timesteps = reset_timesteps,
            tb_log_name         = "PPO",
        )
    except KeyboardInterrupt:
        print("\nStopping... saving model...")

    final_path = os.path.join(SAVE_DIR, "pokemon_latest")
    model.save(final_path)
    mgba_save_game(GAME_SLOT)
    print(f"Saved: {final_path}.zip | slot {GAME_SLOT}")
    env.close()


if __name__ == "__main__":
    train()
