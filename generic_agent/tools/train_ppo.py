"""PPO trainer (Stable-Baselines3) over the Pokemon Emerald env.

This is the real-RL leg of the project: rewards (PWhiddy v2 weights)
become gradient signals via PPO, so the policy can in principle exceed
the heuristic ceiling that imitation learning is stuck at.

Single-environment design (we have one mGBA instance). Trains on a
small CNN policy ("CnnPolicy" feature extractor → MLP heads), with the
multi-modal Dict obs handled via MultiInputPolicy.

Usage:
    poke-rl/Scripts/python.exe -m generic_agent.tools.train_ppo \
        --total-timesteps 50000 \
        --n-steps 1024 \
        --save-every 10000

Imitation pretrain (option C): pass --init-from <path> to warm-start
from a CNN behavior-clone weight file. PPO then finetunes.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import torch
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import CheckpointCallback
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize

from .. import config
from ..env_ppo import PokemonEmeraldEnv


PPO_DIR = config.MODEL_DIR / "ppo"
PPO_LATEST = PPO_DIR / "ppo_latest.zip"


def make_env():
    def _init():
        return PokemonEmeraldEnv(episode_steps=1024, use_curriculum=True)
    return _init


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--total-timesteps", type=int, default=50000)
    parser.add_argument("--n-steps", type=int, default=1024)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--gamma", type=float, default=0.99)
    parser.add_argument("--gae-lambda", type=float, default=0.95)
    parser.add_argument("--ent-coef", type=float, default=0.01)
    parser.add_argument("--save-every", type=int, default=10000)
    parser.add_argument("--init-from", type=str, default="",
                        help="Optional: BC CNN weights to warm-start from")
    parser.add_argument("--resume", action="store_true",
                        help="resume from ppo_latest.zip if present")
    args = parser.parse_args()

    PPO_DIR.mkdir(parents=True, exist_ok=True)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[ppo] device={device}")

    env = DummyVecEnv([make_env()])
    env = VecNormalize(
        env, norm_obs=False, norm_reward=True,
        clip_reward=10.0, gamma=0.99,
    )

    if args.resume and PPO_LATEST.exists():
        print(f"[ppo] resuming from {PPO_LATEST}")
        model = PPO.load(str(PPO_LATEST), env=env, device=device)
    else:
        model = PPO(
            "MultiInputPolicy",
            env,
            n_steps=args.n_steps,
            batch_size=args.batch_size,
            learning_rate=args.learning_rate,
            gamma=args.gamma,
            gae_lambda=args.gae_lambda,
            ent_coef=args.ent_coef,
            verbose=1,
            device=device,
            tensorboard_log=str(config.LOG_DIR / "ppo_tb"),
        )

    if args.init_from and Path(args.init_from).exists():
        print(f"[ppo] warm-starting policy weights from BC ckpt {args.init_from}")
        try:
            bc_state = torch.load(args.init_from, map_location=device)
            bc_sd = bc_state.get("state_dict", bc_state)
            pol_sd = model.policy.state_dict()
            loaded = 0
            for k, v in bc_sd.items():
                tgt = f"features_extractor.{k}"
                if tgt in pol_sd and pol_sd[tgt].shape == v.shape:
                    pol_sd[tgt] = v
                    loaded += 1
            model.policy.load_state_dict(pol_sd, strict=False)
            print(f"[ppo] warm-started {loaded} tensors from BC")
        except (OSError, RuntimeError, KeyError) as exc:
            print(f"[ppo] warm-start failed (continuing without): {exc!r}")

    ckpt_cb = CheckpointCallback(
        save_freq=args.save_every,
        save_path=str(PPO_DIR),
        name_prefix="ppo",
    )

    print(f"[ppo] training for {args.total_timesteps} steps")
    started_ts = time.time()
    model.learn(
        total_timesteps=args.total_timesteps,
        callback=ckpt_cb,
        progress_bar=False,
        reset_num_timesteps=not args.resume,
    )
    dur = time.time() - started_ts
    print(f"[ppo] done in {dur:.1f}s")
    model.save(str(PPO_LATEST))
    print(f"[ppo] saved final model -> {PPO_LATEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
