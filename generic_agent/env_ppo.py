"""Gymnasium environment wrapping mGBA for PPO training.

Per-step contract:
    obs = {"image": (3, 84, 84) uint8, "state": (STATE_DIM,) float32}
    reward = sum of PWhiddy v2 reward signals (see reward_state.py)
    done = whiteout OR episode step cap reached

Episodes:
- reset() picks the best saved curriculum savestate (if any) so the
  agent doesn't replay the intro every episode (PWhiddy v1 used a
  similar "load checkpoint" trick).
- step() taps the chosen button on mGBA, sleeps until the next frame
  settles, computes reward delta, returns obs + reward + done.

Single-env design: we have ONE mGBA instance. PPO normally trains on
vector envs (16-32+ parallel) but single-env PPO still learns, just
slower. The mGBA tap latency (~50 ms) is the wall-clock bottleneck.
"""
from __future__ import annotations

import time
from pathlib import Path

import cv2
import gymnasium as gym
import numpy as np
from gymnasium import spaces

from . import (
    brain_cnn,
    config,
    curriculum as curr_mod,
    io as io_mod,
    reward_state as reward_state_mod,
    state as state_mod,
)

ACTIONS = brain_cnn.ACTION_LABELS  # 8 buttons
IMG_SIZE = 64
EPISODE_STEPS = 1024  # PPO rollout cap
FRAME_DELAY_S = 0.03
FRAME_SKIP = 3  # 1 PPO step = 3 mGBA taps -> 3x effective fps


class PokemonEmeraldEnv(gym.Env):
    metadata = {"render_modes": ["rgb_array"]}

    def __init__(
        self,
        episode_steps: int = EPISODE_STEPS,
        use_curriculum: bool = True,
        seed: int | None = None,
    ) -> None:
        super().__init__()
        self.client = io_mod.MGBAClient()
        if not self.client.ping():
            raise RuntimeError("mGBA port 8895 unreachable")
        self.episode_steps = episode_steps
        self.use_curriculum = use_curriculum
        self.rng = np.random.default_rng(seed)

        self.action_space = spaces.Discrete(len(ACTIONS))
        self.observation_space = spaces.Dict({
            "image": spaces.Box(
                low=0, high=255,
                shape=(3, IMG_SIZE, IMG_SIZE), dtype=np.uint8,
            ),
            "state": spaces.Box(
                low=-1.0, high=1.0,
                shape=(brain_cnn.STATE_DIM,), dtype=np.float32,
            ),
        })

        self.reward_state = reward_state_mod.RewardState()
        self.reward_state.load()
        self.curriculum_idx = curr_mod.CurriculumIndex()
        if self.use_curriculum:
            self.curriculum_idx.load()

        self._step_count = 0
        self._prev_state: state_mod.GameState | None = None
        self._screenshot_dir = config.DATASET_DIR / "ppo_screens"
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)

    def _grab_image(self) -> np.ndarray:
        p = self._screenshot_dir / "current.png"
        self.client.screenshot(p)
        time.sleep(0.05)
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if img is None:
            return np.zeros((3, IMG_SIZE, IMG_SIZE), dtype=np.uint8)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = cv2.resize(img, (IMG_SIZE, IMG_SIZE), interpolation=cv2.INTER_AREA)
        return img.transpose(2, 0, 1)  # CHW

    def _build_obs(self) -> dict:
        gs = state_mod.read_state(self.client)
        self._cur_state = gs
        image = self._grab_image()
        state_dict = {
            "in_battle": gs.in_battle,
            "is_trainer": gs.is_trainer_battle,
            "pos": [gs.x, gs.y] if gs.saveblock1_valid else None,
            "party0_hp": gs.party0_hp,
            "party0_max_hp": gs.party0_max_hp,
            "party0_level": gs.party0_level,
            "badge_count": gs.badge_count,
            "total_event_flags": gs.total_event_flags,
            "event_flag_bytes_hex": gs.event_flag_bytes_hex,
        }
        state_vec = brain_cnn.vectorize_state(state_dict)
        return {"image": image, "state": state_vec.astype(np.float32)}

    def _compute_reward(self, prev: state_mod.GameState | None) -> float:
        gs = self._cur_state
        if prev is None or not gs.saveblock1_valid:
            return 0.0
        total = 0.0
        total += self.reward_state.record_event_flag_delta(
            self._step_count, prev.total_event_flags, gs.total_event_flags,
        )
        total += self.reward_state.record_healing(
            self._step_count, prev.party0_hp, gs.party0_hp, gs.party0_max_hp,
        )
        total += self.reward_state.record_badge_delta(
            self._step_count, prev.badge_count, gs.badge_count,
        )
        if gs.saveblock1_valid:
            total += self.reward_state.record_coord_visit(
                self._step_count, gs.map_group, gs.map_num, gs.x, gs.y,
            )
        if (
            prev.party0_hp > 0
            and gs.party0_hp == 0
            and gs.party0_max_hp > 0
            and not gs.in_battle
        ):
            total += self.reward_state.record_death(self._step_count)
        if (prev.map_group, prev.map_num) != (gs.map_group, gs.map_num):
            r = self.reward_state.record_new_map(
                (prev.map_group, prev.map_num),
                (prev.x, prev.y),
                (gs.map_group, gs.map_num),
                (gs.x, gs.y),
                self._step_count,
            )
            total += r
        return float(total)

    def reset(
        self, *, seed: int | None = None, options: dict | None = None,
    ) -> tuple[dict, dict]:
        super().reset(seed=seed)
        self._step_count = 0
        if self.use_curriculum:
            best = self.curriculum_idx.best_milestone()
            if best is not None and Path(best.savestate_path).exists():
                try:
                    r = self.client._send(
                        f"core.loadStateFile,{best.savestate_path},1"
                    )
                    if r.is_error:
                        pass
                except (io_mod.EmulatorError, OSError):
                    pass
        time.sleep(0.2)
        obs = self._build_obs()
        self._prev_state = self._cur_state
        return obs, {}

    def step(
        self, action: int,
    ) -> tuple[dict, float, bool, bool, dict]:
        btn = ACTIONS[int(action)]
        for _ in range(FRAME_SKIP):
            try:
                self.client.tap(btn, frames=10)
            except (io_mod.EmulatorError, ValueError):
                break
            time.sleep(FRAME_DELAY_S)
        obs = self._build_obs()
        reward = self._compute_reward(self._prev_state)
        self._prev_state = self._cur_state
        self._step_count += 1
        terminated = (
            self._cur_state.party0_max_hp > 0
            and self._cur_state.party0_hp == 0
            and not self._cur_state.in_battle
        )
        truncated = self._step_count >= self.episode_steps
        info = {
            "step": self._step_count,
            "map": (self._cur_state.map_group, self._cur_state.map_num)
                   if self._cur_state.saveblock1_valid else None,
            "pos": (self._cur_state.x, self._cur_state.y)
                   if self._cur_state.saveblock1_valid else None,
            "total_event_flags": self._cur_state.total_event_flags,
            "cumulative_reward": self.reward_state.cumulative_reward,
        }
        return obs, reward, terminated, truncated, info

    def close(self) -> None:
        self.reward_state.save()
