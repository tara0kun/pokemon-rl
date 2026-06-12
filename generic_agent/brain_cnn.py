"""Phase 3 vision + RAM model: imitate Brain (Sonnet/Opus) decisions
from BOTH the screenshot and the structured RAM-derived state.

Architectures share the same inference interface (`CNNBrain.predict`):

- `TinyBrainCNN`: 4-conv + 2-FC, ~356K params, 96x96 input. Image-only.
- `ResNetBrainCNN`: torchvision ResNet-18 (ImageNet pre-trained) +
  8-class head, 224x224 input. Image-only.
- `MultiModalBrainCNN` (DEFAULT, the one the user requested):
  ResNet-18 backbone -> 512d visual feature
  +
  Structured state vector (28d: in_battle, is_trainer, blocked_here,
  bfs_to_frontier, GOAL_DIRECTION, oscillating, AVOID, same_pos_streak,
  same_map_streak, pos, map, recent button history)
  → MLP → 64d state feature
  → late fusion (concat 576d) → FC(128) → 8-class softmax
  ~11.3M params, 224x224 image + 28-d state.

The structured state is the same signal stream that the API Brain
prompt receives (blocked_here, bfs_to_frontier, AVOID, etc.). Giving
the CNN access to it lets the model imitate Brain decisions even when
the screenshot alone is ambiguous (e.g. the BFS direction toward
unexplored tiles is information that does not appear in pixels).

Action space: {Up, Down, Left, Right, A, B, Start, Select}.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from PIL import Image
from torchvision import models
from torchvision.models import ResNet18_Weights

ACTION_LABELS = ["Up", "Down", "Left", "Right", "A", "B", "Start", "Select"]
ACTION_TO_IDX = {a: i for i, a in enumerate(ACTION_LABELS)}
DIRECTION_LABELS = ["Up", "Down", "Left", "Right"]
DIR_NONE_IDX = 4

TINY_IMG_SIZE = 96
RESNET_IMG_SIZE = 224

IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)

# Structured state vector layout (28 dims):
#  0     in_battle
#  1     is_trainer
#  2..5  blocked_here  (Up Down Left Right binary)
#  6..10 bfs_to_frontier one-hot (Up Down Left Right None)
# 11..15 GOAL_DIRECTION one-hot
# 16..20 AVOID (suppress_dir) one-hot
# 21     oscillating
# 22     same_pos_streak / 50  (clipped to [0, 1])
# 23     same_map_streak / 200 (clipped to [0, 1])
# 24     pos_x / 32
# 25     pos_y / 32
# 26     map_visit_count / 500 (clipped)
# 27     consecutive_dialog / 20 (clipped)
STATE_DIM = 28


def _dir_index(direction: str | None) -> int:
    if direction in DIRECTION_LABELS:
        return DIRECTION_LABELS.index(direction)
    return DIR_NONE_IDX


def vectorize_state(state: dict[str, Any]) -> np.ndarray:
    """Convert a recorded state dict to the 28-d feature vector.

    Missing keys default to zero/None so old datasets without
    multi-modal fields can still be loaded (their state vectors are
    near-zero, equivalent to image-only behavior cloning).
    """
    v = np.zeros(STATE_DIM, dtype=np.float32)
    v[0] = float(bool(state.get("in_battle", False)))
    v[1] = float(bool(state.get("is_trainer", False)))
    for i, d in enumerate(DIRECTION_LABELS):
        if d in (state.get("blocked_here") or []):
            v[2 + i] = 1.0
    bfs_idx = _dir_index(state.get("bfs_first"))
    v[6 + bfs_idx] = 1.0
    goal_idx = _dir_index(state.get("goal_direction"))
    v[11 + goal_idx] = 1.0
    avoid_idx = _dir_index(state.get("suppress_dir"))
    v[16 + avoid_idx] = 1.0
    v[21] = float(bool(state.get("oscillating", False)))
    v[22] = min(1.0, float(state.get("same_pos_streak", 0)) / 50.0)
    v[23] = min(1.0, float(state.get("same_map_streak", 0)) / 200.0)
    pos = state.get("pos")
    if pos:
        v[24] = float(pos[0]) / 32.0
        v[25] = float(pos[1]) / 32.0
    v[26] = min(1.0, float(state.get("map_visit_count", 0)) / 500.0)
    v[27] = min(1.0, float(state.get("consecutive_dialog", 0)) / 20.0)
    return v


def screenshot_to_tensor(
    path: Path | str,
    img_size: int = TINY_IMG_SIZE,
    normalize_imagenet: bool = False,
) -> torch.Tensor:
    img = Image.open(path).convert("RGB").resize((img_size, img_size))
    arr = np.asarray(img, dtype=np.float32) / 255.0
    tensor = torch.from_numpy(arr).permute(2, 0, 1)
    if normalize_imagenet:
        mean = torch.tensor(IMAGENET_MEAN).view(3, 1, 1)
        std = torch.tensor(IMAGENET_STD).view(3, 1, 1)
        tensor = (tensor - mean) / std
    return tensor


class TinyBrainCNN(nn.Module):
    ARCH = "tiny"
    IMG_SIZE = TINY_IMG_SIZE
    NORMALIZE_IMAGENET = False
    USES_STATE = False

    def __init__(self, num_classes: int = len(ACTION_LABELS)) -> None:
        super().__init__()
        self.conv1 = nn.Conv2d(3, 16, kernel_size=3, stride=2, padding=1)
        self.conv2 = nn.Conv2d(16, 32, kernel_size=3, stride=2, padding=1)
        self.conv3 = nn.Conv2d(32, 64, kernel_size=3, stride=2, padding=1)
        self.conv4 = nn.Conv2d(64, 64, kernel_size=3, stride=2, padding=1)
        self.fc1 = nn.Linear(64 * 6 * 6, 128)
        self.fc2 = nn.Linear(128, num_classes)
        self.dropout = nn.Dropout(p=0.2)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = F.relu(self.conv1(x))
        x = F.relu(self.conv2(x))
        x = F.relu(self.conv3(x))
        x = F.relu(self.conv4(x))
        x = x.flatten(1)
        x = F.relu(self.fc1(x))
        x = self.dropout(x)
        return self.fc2(x)


class ResNetBrainCNN(nn.Module):
    ARCH = "resnet18"
    IMG_SIZE = RESNET_IMG_SIZE
    NORMALIZE_IMAGENET = True
    USES_STATE = False

    def __init__(
        self,
        num_classes: int = len(ACTION_LABELS),
        pretrained: bool = True,
    ) -> None:
        super().__init__()
        weights = ResNet18_Weights.DEFAULT if pretrained else None
        backbone = models.resnet18(weights=weights)
        backbone.fc = nn.Linear(backbone.fc.in_features, num_classes)
        self.backbone = backbone

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.backbone(x)


class MultiModalBrainCNN(nn.Module):
    """ResNet-18 visual + MLP over RAM-derived state, late fusion.

    Visual stream: 224x224 RGB -> ResNet-18 (ImageNet pretrained) ->
        global-avg-pool 512-d.
    State stream: 28-d structured vector -> MLP(64, 64).
    Fusion: concat 576-d -> FC(128, dropout 0.3, ReLU) -> FC(num_classes).
    """

    ARCH = "multimodal"
    IMG_SIZE = RESNET_IMG_SIZE
    NORMALIZE_IMAGENET = True
    USES_STATE = True

    def __init__(
        self,
        num_classes: int = len(ACTION_LABELS),
        pretrained: bool = True,
        state_dim: int = STATE_DIM,
    ) -> None:
        super().__init__()
        weights = ResNet18_Weights.DEFAULT if pretrained else None
        backbone = models.resnet18(weights=weights)
        self.visual_feat_dim = backbone.fc.in_features
        backbone.fc = nn.Identity()
        self.backbone = backbone

        self.state_mlp = nn.Sequential(
            nn.Linear(state_dim, 64),
            nn.ReLU(),
            nn.Linear(64, 64),
            nn.ReLU(),
        )
        self.head = nn.Sequential(
            nn.Linear(self.visual_feat_dim + 64, 128),
            nn.ReLU(),
            nn.Dropout(p=0.3),
            nn.Linear(128, num_classes),
        )

    def forward(
        self, image: torch.Tensor, state: torch.Tensor
    ) -> torch.Tensor:
        v = self.backbone(image)
        s = self.state_mlp(state)
        return self.head(torch.cat([v, s], dim=1))


def build_model(arch: str) -> nn.Module:
    if arch == "tiny":
        return TinyBrainCNN()
    if arch == "resnet18":
        return ResNetBrainCNN(pretrained=True)
    if arch == "multimodal":
        return MultiModalBrainCNN(pretrained=True)
    raise ValueError(f"unknown arch '{arch}' — pick tiny | resnet18 | multimodal")


class CNNBrain:
    def __init__(self, model_path: Path | str) -> None:
        self.model_path = Path(model_path)
        ckpt = torch.load(self.model_path, map_location="cpu")
        if isinstance(ckpt, dict) and "state_dict" in ckpt:
            arch = ckpt.get("arch", "tiny")
            state = ckpt["state_dict"]
        else:
            arch = "tiny"
            state = ckpt
            if any(k.startswith("backbone.") for k in ckpt):
                arch = "resnet18"
            if any(k.startswith("state_mlp.") for k in ckpt):
                arch = "multimodal"
        self.arch = arch
        self.model = build_model(arch)
        self.model.load_state_dict(state)
        self.model.eval()
        self.img_size = type(self.model).IMG_SIZE
        self.normalize_imagenet = type(self.model).NORMALIZE_IMAGENET
        self.uses_state = type(self.model).USES_STATE

    @torch.no_grad()
    def predict(
        self,
        screenshot_path: Path | str,
        state: dict[str, Any] | None = None,
    ) -> tuple[str, float]:
        img = screenshot_to_tensor(
            screenshot_path,
            img_size=self.img_size,
            normalize_imagenet=self.normalize_imagenet,
        ).unsqueeze(0)
        if self.uses_state:
            vec = vectorize_state(state or {})
            s = torch.from_numpy(vec).unsqueeze(0)
            logits = self.model(img, s)
        else:
            logits = self.model(img)
        probs = F.softmax(logits, dim=1).squeeze(0)
        idx = int(torch.argmax(probs).item())
        return ACTION_LABELS[idx], float(probs[idx].item())


def parameter_count(model: nn.Module) -> int:
    return sum(p.numel() for p in model.parameters())


def load_dataset_index(path: Path | str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


if __name__ == "__main__":
    print("=== TinyBrainCNN ===")
    m = TinyBrainCNN()
    print(f"params: {parameter_count(m):,}")
    print()
    print("=== ResNetBrainCNN ===")
    r = ResNetBrainCNN(pretrained=False)
    print(f"params: {parameter_count(r):,}")
    print()
    print("=== MultiModalBrainCNN ===")
    mm = MultiModalBrainCNN(pretrained=False)
    print(f"params: {parameter_count(mm):,}")
    img = torch.randn(1, 3, RESNET_IMG_SIZE, RESNET_IMG_SIZE)
    state = torch.randn(1, STATE_DIM)
    out = mm(img, state)
    print(f"output shape: {out.shape}")
    print()
    print(f"action labels: {ACTION_LABELS}")
    print(f"state dim: {STATE_DIM}")
