"""
CNN Tile Classifier for Pokemon GBA games.

Classifies 16x16 pixel RGB tile images into categories:
  walkable, wall, unknown, door

Architecture: Small CNN with BatchNorm and residual connections for 16x16 input.
v5: Squeeze-and-Excitation (SE) channel attention in residual blocks for better feature focus.
v3: Mixup augmentation, Label Smoothing, Residual blocks, Door confidence threshold.
v2: Focal Loss, BatchNorm, Stratified Split, LR Scheduling, improved augmentation.

Usage:
  python tools/tile_classifier.py train --epochs 50 --batch_size 32
  python tools/tile_classifier.py train --epochs 80 --augment --oversample --mixup
  python tools/tile_classifier.py eval
  python tools/tile_classifier.py eval --door_threshold 0.5
  python tools/tile_classifier.py predict image.png
  python tools/tile_classifier.py predict image1.png image2.png ...

Expected data layout for training:
  tile_data/
    walkable/
      *.png
    wall/
      *.png
    unknown/
      *.png
    door/
      *.png
"""

import argparse
import hashlib
import json
import os
import random
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset, random_split, Subset
from PIL import Image

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CLASSES = ["walkable", "wall", "unknown", "door"]
NUM_CLASSES = len(CLASSES)
IMG_SIZE = 16  # 16x16 tiles
DEFAULT_DATA_DIR = "tile_data"
DEFAULT_CHECKPOINT = "tools/tile_classifier.pth"

# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

class TileAugmentation:
    """Data augmentation for 16x16 tile images.

    Applies random transforms: rotation, flip, color jitter.
    Minority classes (wall, door) get augmented more aggressively.
    """

    def __init__(self, enable: bool = True):
        self.enable = enable

    def __call__(self, img: Image.Image, label: int) -> Image.Image:
        if not self.enable:
            return img
        # ★ v2: Minority classes (wall=1, door=3) get more aggressive augmentation
        is_minority = label in (1, 3)  # wall, door
        # Rotation: 0/90/180/270
        rot = random.choice([0, 90, 180, 270])
        if rot:
            img = img.rotate(rot)
        # Flip: horizontal / vertical
        if random.random() < 0.5:
            img = img.transpose(Image.FLIP_LEFT_RIGHT)
        if random.random() < (0.5 if is_minority else 0.3):
            img = img.transpose(Image.FLIP_TOP_BOTTOM)
        # Color jitter (more aggressive for minority classes)
        jitter_prob = 0.7 if is_minority else 0.4
        if random.random() < jitter_prob:
            arr = np.array(img, dtype=np.float32)
            # Brightness ±20% (minority) / ±15% (majority)
            b_range = 0.20 if is_minority else 0.15
            arr *= random.uniform(1 - b_range, 1 + b_range)
            # Contrast ±15% (minority) / ±10% (majority)
            c_range = 0.15 if is_minority else 0.10
            mean = arr.mean()
            arr = (arr - mean) * random.uniform(1 - c_range, 1 + c_range) + mean
            arr = np.clip(arr, 0, 255).astype(np.uint8)
            img = Image.fromarray(arr)
        # ★ v2: Random pixel noise for minority classes (robustness)
        if is_minority and random.random() < 0.3:
            arr = np.array(img, dtype=np.float32)
            noise = np.random.uniform(-10, 10, arr.shape).astype(np.float32)
            arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
            img = Image.fromarray(arr)
        # ★ v7: Random erasing for door class (partial occlusion robustness)
        if label == 3 and random.random() < 0.4:
            arr = np.array(img)
            h, w = arr.shape[:2]
            eh = random.randint(2, h // 3)
            ew = random.randint(2, w // 3)
            ey = random.randint(0, h - eh)
            ex = random.randint(0, w - ew)
            arr[ey:ey+eh, ex:ex+ew] = random.randint(0, 255)
            img = Image.fromarray(arr)
        return img


class TileDataset(Dataset):
    """Loads 16x16 tile images from tile_data/{label}/*.png.

    v8: Deduplication support — removes duplicate images (same pixel content)
    to prevent data leakage between train/val splits. Each unique image is
    kept only once, with a hash for leak-free splitting.

    With augmentation enabled, minority classes (wall, door) are
    oversampled to balance the dataset.
    """

    def __init__(self, data_dir: str, classes: list[str] = CLASSES,
                 augment: bool = False, oversample_minority: bool = False,
                 binary: bool = False, deduplicate: bool = False):
        self.samples: list[tuple[str, int]] = []
        self.sample_hashes: list[str] = []  # ★ v8: per-sample content hash
        self.binary = binary
        # Binary mode: walkable+door=0, wall+unknown=1
        if binary:
            self.classes = ["walkable", "obstacle"]
            _BINARY_MAP = {"walkable": 0, "door": 0, "wall": 1, "unknown": 1}
        else:
            self.classes = classes
        self.augment = TileAugmentation(enable=augment)
        data_path = Path(data_dir)

        _src_classes = ["walkable", "wall", "unknown", "door"] if binary else classes
        all_samples_raw = []
        for label in _src_classes:
            label_dir = data_path / label
            if not label_dir.is_dir():
                print(f"  Warning: directory not found: {label_dir}")
                continue
            label_idx = _BINARY_MAP[label] if binary else classes.index(label)
            for img_path in sorted(label_dir.glob("*.png")):
                all_samples_raw.append((str(img_path), label_idx))

        if not all_samples_raw:
            raise FileNotFoundError(
                f"No .png images found in {data_dir}/{{{'|'.join(classes)}}}/"
            )

        # ★ v8: Deduplicate by file content hash — prevents data leakage
        # Also resolves cross-class label conflicts via majority vote
        if deduplicate:
            n_before = len(all_samples_raw)
            # Pass 1: collect all labels per hash for majority vote
            hash_info = {}  # hash -> {path, label_votes: Counter}
            from collections import Counter
            for path, label_idx in all_samples_raw:
                h = hashlib.md5(open(path, 'rb').read()).hexdigest()
                if h not in hash_info:
                    hash_info[h] = {"path": path, "votes": Counter()}
                hash_info[h]["votes"][label_idx] += 1
            # Pass 2: resolve conflicts via majority vote
            n_conflicts = 0
            for h, info in hash_info.items():
                if len(info["votes"]) > 1:
                    n_conflicts += 1
                best_label = info["votes"].most_common(1)[0][0]
                self.samples.append((info["path"], best_label))
                self.sample_hashes.append(h)
            n_after = len(self.samples)
            print(f"  Deduplicated: {n_before} → {n_after} unique images "
                  f"(removed {n_before - n_after} duplicates, "
                  f"resolved {n_conflicts} label conflicts via majority vote)")
        else:
            self.samples = all_samples_raw
            # Compute hashes for split purposes even without dedup
            for path, _ in self.samples:
                h = hashlib.md5(open(path, 'rb').read()).hexdigest()
                self.sample_hashes.append(h)

        # Count per class (before oversampling — used for class weights)
        _nc = 2 if binary else len(classes)
        class_counts = [0] * _nc
        for _, l in self.samples:
            class_counts[l] += 1
        max_count = max(class_counts)
        self.original_class_counts = list(class_counts)  # ★ v4: preserve for weight calc

        _cls_names = self.classes if binary else classes
        print(f"  Loaded {len(self.samples)} samples from {data_dir}")
        for label_idx, label in enumerate(_cls_names):
            if label_idx < len(class_counts) and class_counts[label_idx] > 0:
                print(f"    {label}: {class_counts[label_idx]}")

        # Oversample minority classes to match majority
        if oversample_minority and augment:
            extra = []
            for label_idx, count in enumerate(class_counts):
                if 0 < count < max_count:
                    label_samples = [(p, l) for p, l in self.samples if l == label_idx]
                    n_extra = max_count - count
                    for i in range(n_extra):
                        extra.append(label_samples[i % len(label_samples)])
            if extra:
                self.samples.extend(extra)
                # Extend hashes for oversampled entries
                for p, _ in extra:
                    self.sample_hashes.append(
                        hashlib.md5(open(p, 'rb').read()).hexdigest())
                print(f"  Oversampled: {len(extra)} extra → total {len(self.samples)}")

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, int]:
        path, label = self.samples[idx]
        img = Image.open(path).convert("RGB").resize((IMG_SIZE, IMG_SIZE))
        img = self.augment(img, label)
        # HWC uint8 -> CHW float32 [0, 1]
        arr = np.array(img, dtype=np.float32) / 255.0
        tensor = torch.from_numpy(arr).permute(2, 0, 1)  # (3, 16, 16)
        return tensor, label


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class FocalLoss(nn.Module):
    """Focal Loss for class-imbalanced classification.

    Focuses training on hard examples (wall/door) by down-weighting
    easy examples (walkable/unknown). Much better than weighted CE
    for minority class recall.

    FL(p_t) = -alpha_t * (1-p_t)^gamma * log(p_t)
    """

    def __init__(self, alpha: torch.Tensor = None, gamma: float = 2.0):
        super().__init__()
        self.gamma = gamma
        self.alpha = alpha  # per-class weights

    def forward(self, logits: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
        ce_loss = F.cross_entropy(logits, targets, weight=self.alpha, reduction='none')
        p_t = torch.exp(-ce_loss)  # probability of correct class
        focal_factor = (1 - p_t) ** self.gamma
        loss = focal_factor * ce_loss
        return loss.mean()


class SEBlock(nn.Module):
    """Squeeze-and-Excitation block for channel attention.

    v5: Learns per-channel importance weights via global average pooling
    followed by a bottleneck FC layer. This helps the model focus on the
    most discriminative feature channels (e.g., edge detectors for walls,
    color channels for doors) and suppress less useful ones.
    """

    def __init__(self, channels: int, reduction: int = 4):
        super().__init__()
        mid = max(channels // reduction, 4)
        self.fc1 = nn.Linear(channels, mid)
        self.fc2 = nn.Linear(mid, channels)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # Squeeze: global average pool -> (B, C)
        b, c, _, _ = x.size()
        y = x.view(b, c, -1).mean(dim=2)
        # Excitation: bottleneck FC -> sigmoid weights
        y = F.relu(self.fc1(y), inplace=True)
        y = torch.sigmoid(self.fc2(y))
        # Scale: reweight channels
        return x * y.view(b, c, 1, 1)


class ResidualBlock(nn.Module):
    """Residual block with BatchNorm and SE attention for improved gradient flow.

    v3: Skip connection helps preserve fine-grained features (critical for
    distinguishing wall vs walkable textures that differ by subtle patterns).
    v5: Added Squeeze-and-Excitation channel attention to learn which feature
    channels are most important for each tile type.
    """

    def __init__(self, channels: int):
        super().__init__()
        self.conv1 = nn.Conv2d(channels, channels, kernel_size=3, padding=1)
        self.bn1 = nn.BatchNorm2d(channels)
        self.conv2 = nn.Conv2d(channels, channels, kernel_size=3, padding=1)
        self.bn2 = nn.BatchNorm2d(channels)
        self.se = SEBlock(channels, reduction=4)  # ★ v5: channel attention

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        residual = x
        out = F.relu(self.bn1(self.conv1(x)), inplace=True)
        out = self.bn2(self.conv2(out))
        out = self.se(out)  # ★ v5: apply channel attention before skip add
        out = F.relu(out + residual, inplace=True)
        return out


class TileCNN(nn.Module):
    """
    Small CNN for 16x16 RGB tile classification with BatchNorm and residual blocks.

    v3 Architecture (residual blocks for better wall/door discrimination):
      Conv2d(3, 32, 3, pad=1) -> BN -> ReLU -> MaxPool(2)   => (32, 8, 8)
      ResidualBlock(32)                                       => (32, 8, 8)
      Conv2d(32, 64, 3, pad=1) -> BN -> ReLU -> MaxPool(2)  => (64, 4, 4)
      ResidualBlock(64)                                       => (64, 4, 4)
      Conv2d(64, 64, 3, pad=1) -> BN -> ReLU -> MaxPool(2)  => (64, 2, 2)
      Flatten => 256
      Linear(256, 128) -> ReLU -> Dropout(0.4)
      Linear(128, NUM_CLASSES)

    v2 (backward compatible) omits residual blocks.
    """

    def __init__(self, num_classes: int = NUM_CLASSES):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(3, 32, kernel_size=3, padding=1),
            nn.BatchNorm2d(32),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),  # 16 -> 8

            ResidualBlock(32),  # ★ v3: residual for texture discrimination

            nn.Conv2d(32, 64, kernel_size=3, padding=1),
            nn.BatchNorm2d(64),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),  # 8 -> 4

            ResidualBlock(64),  # ★ v3: residual for pattern retention

            nn.Conv2d(64, 64, kernel_size=3, padding=1),
            nn.BatchNorm2d(64),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),  # 4 -> 2
        )
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Linear(64 * 2 * 2, 128),
            nn.ReLU(inplace=True),
            nn.Dropout(0.4),  # ★ v3: increased dropout (0.3→0.4) to reduce overconfidence
            nn.Linear(128, num_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.features(x)
        x = self.classifier(x)
        return x


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def _stratified_split(samples: list[tuple[str, int]], val_ratio: float,
                      seed: int, num_classes: int,
                      sample_hashes: list[str] = None):
    """Stratified train/val split preserving class distribution.

    ★ v8: When sample_hashes is provided, splits by unique hash groups
    to prevent data leakage (identical images always go to same split).
    """
    rng = random.Random(seed)

    if sample_hashes is not None:
        # ★ v8: Group indices by hash → split hash groups, not individual samples
        # This ensures duplicate images (same hash) all go to train OR val, never both
        hash_to_label = {}
        hash_to_indices = {}
        for i, ((_, label), h) in enumerate(zip(samples, sample_hashes)):
            if h not in hash_to_indices:
                hash_to_indices[h] = []
                hash_to_label[h] = label
            hash_to_indices[h].append(i)

        # Group hashes by class
        by_class_hashes = [[] for _ in range(num_classes)]
        for h, label in hash_to_label.items():
            by_class_hashes[label].append(h)

        train_indices, val_indices = [], []
        for cls_hashes in by_class_hashes:
            rng.shuffle(cls_hashes)
            n_val = max(1, int(len(cls_hashes) * val_ratio))
            for h in cls_hashes[:n_val]:
                val_indices.extend(hash_to_indices[h])
            for h in cls_hashes[n_val:]:
                train_indices.extend(hash_to_indices[h])
        return train_indices, val_indices

    # Fallback: original index-based split
    by_class = [[] for _ in range(num_classes)]
    for i, (_, label) in enumerate(samples):
        by_class[label].append(i)

    train_indices, val_indices = [], []
    for cls_indices in by_class:
        rng.shuffle(cls_indices)
        n_val = max(1, int(len(cls_indices) * val_ratio))
        val_indices.extend(cls_indices[:n_val])
        train_indices.extend(cls_indices[n_val:])
    return train_indices, val_indices


def _mixup_data(x: torch.Tensor, y: torch.Tensor, alpha: float = 0.2):
    """Mixup: blend pairs of samples for smoother decision boundaries.

    ★ v3: Reduces overconfident predictions on minority classes (door FP).
    When alpha>0, creates convex combinations of training pairs.
    """
    if alpha <= 0:
        return x, y, y, 1.0
    lam = np.random.beta(alpha, alpha)
    lam = max(lam, 1 - lam)  # ensure lam >= 0.5 so primary sample dominates
    batch_size = x.size(0)
    index = torch.randperm(batch_size, device=x.device)
    mixed_x = lam * x + (1 - lam) * x[index]
    return mixed_x, y, y[index], lam


def train(
    data_dir: str = DEFAULT_DATA_DIR,
    checkpoint_path: str = DEFAULT_CHECKPOINT,
    epochs: int = 50,
    batch_size: int = 32,
    lr: float = 1e-3,
    val_split: float = 0.2,
    seed: int = 42,
    augment: bool = False,
    oversample: bool = False,
    mixup_alpha: float = 0.0,
    label_smoothing: float = 0.0,
    binary: bool = False,
):
    """Train the tile classifier with Focal Loss, BatchNorm, residual blocks,
    optional Mixup, and Label Smoothing (v3)."""
    torch.manual_seed(seed)
    random.seed(seed)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")
    if augment:
        print(f"  Augmentation: ON (oversample={oversample})")
    if mixup_alpha > 0:
        print(f"  Mixup: ON (alpha={mixup_alpha})")
    if label_smoothing > 0:
        print(f"  Label Smoothing: ON (eps={label_smoothing})")

    # Data — train set with augmentation, val set without
    # ★ v8: Deduplicate to prevent data leakage between train/val
    _num_classes = 2 if binary else NUM_CLASSES
    train_dataset = TileDataset(data_dir, augment=augment,
                                oversample_minority=oversample, binary=binary,
                                deduplicate=True)
    val_dataset = TileDataset(data_dir, augment=False, binary=binary,
                              deduplicate=True)

    # ★ v8: Hash-aware stratified split — identical images never leak across splits
    base_train_idx, val_idx = _stratified_split(
        val_dataset.samples, val_split, seed, _num_classes,
        sample_hashes=val_dataset.sample_hashes)
    val_ds = Subset(val_dataset, val_idx)

    # Train on augmented dataset minus val indices
    val_indices_set = set(val_idx)
    train_indices = [i for i in range(len(train_dataset))
                     if (i < len(val_dataset) and i not in val_indices_set)
                     or i >= len(val_dataset)]  # oversampled extras
    train_ds = Subset(train_dataset, train_indices)

    print(f"  Train: {len(train_ds)}, Val: {len(val_ds)}")

    train_loader = DataLoader(
        train_ds, batch_size=batch_size, shuffle=True, num_workers=0,
    )
    val_loader = DataLoader(
        val_ds, batch_size=batch_size, shuffle=False, num_workers=0,
    )

    # Model
    model = TileCNN(_num_classes).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    # ★ v2: CosineAnnealingLR for better convergence
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=epochs, eta_min=lr * 0.01)

    # ★ v4: Use ORIGINAL (pre-oversample) class counts for weights
    # Oversampled counts are all equal → weights=1.0 → Focal Loss ineffective
    # Original counts preserve the true imbalance (door=713 vs walkable=14394)
    # ★ v6b: oversample時はsqrt縮小で二重補正防止
    # 旧(v4実験2): weight=12.00 + oversample併用でdoorが強すぎ val_acc=52%崩壊
    # 新: oversample=Trueならsqrt(weight)に縮小して穏やかに補正
    import math
    class_counts = train_dataset.original_class_counts
    if binary:
        class_counts = class_counts[:_num_classes]  # binary: 2要素に切り詰め
    total_samples = sum(class_counts)
    _raw_weights = [total_samples / (_num_classes * max(c, 1)) for c in class_counts]
    if oversample:
        _raw_weights = [math.sqrt(w) for w in _raw_weights]
    class_weights = torch.tensor(_raw_weights, dtype=torch.float32).to(device)
    print(f"  Class weights: {[f'{w:.2f}' for w in class_weights.tolist()]}")
    criterion = FocalLoss(alpha=class_weights, gamma=2.0)

    # ★ v3: Label Smoothing criterion for Mixup (used during mixup training)
    if label_smoothing > 0:
        criterion_smooth = nn.CrossEntropyLoss(
            weight=class_weights, label_smoothing=label_smoothing)
    else:
        criterion_smooth = None

    best_val_score = 0.0  # ★ v2: Track wall+door F1, not just accuracy
    history: list[dict] = []
    patience = 10  # early stopping patience
    no_improve = 0

    for epoch in range(1, epochs + 1):
        # --- Train ---
        model.train()
        train_loss = 0.0
        train_correct = 0
        train_total = 0
        for images, labels in train_loader:
            images, labels = images.to(device), labels.to(device)
            optimizer.zero_grad()

            # ★ v3: Mixup augmentation — blend samples for smoother boundaries
            if mixup_alpha > 0:
                mixed_x, y_a, y_b, lam = _mixup_data(images, labels, mixup_alpha)
                outputs = model(mixed_x)
                # Mixup loss: weighted combination of losses for both labels
                if criterion_smooth is not None:
                    loss = (lam * criterion_smooth(outputs, y_a)
                            + (1 - lam) * criterion_smooth(outputs, y_b))
                else:
                    loss = (lam * criterion(outputs, y_a)
                            + (1 - lam) * criterion(outputs, y_b))
                # Accuracy: count as correct if prediction matches primary label
                train_correct += (lam * (outputs.argmax(1) == y_a).float().sum().item()
                                  + (1 - lam) * (outputs.argmax(1) == y_b).float().sum().item())
            else:
                outputs = model(images)
                loss = criterion(outputs, labels)
                train_correct += (outputs.argmax(1) == labels).sum().item()

            loss.backward()
            optimizer.step()

            train_loss += loss.item() * images.size(0)
            train_total += images.size(0)

        train_loss /= train_total
        train_acc = train_correct / train_total
        scheduler.step()

        # --- Validate with per-class metrics ---
        model.eval()
        val_loss = 0.0
        val_correct = 0
        val_total = 0
        # Per-class tracking
        class_tp = [0] * NUM_CLASSES
        class_fp = [0] * NUM_CLASSES
        class_fn = [0] * NUM_CLASSES
        with torch.no_grad():
            for images, labels in val_loader:
                images, labels = images.to(device), labels.to(device)
                outputs = model(images)
                loss = criterion(outputs, labels)
                val_loss += loss.item() * images.size(0)
                preds = outputs.argmax(1)
                val_correct += (preds == labels).sum().item()
                val_total += images.size(0)
                for t, p in zip(labels.cpu().numpy(), preds.cpu().numpy()):
                    if t == p:
                        class_tp[t] += 1
                    else:
                        class_fn[t] += 1
                        class_fp[p] += 1

        val_loss /= val_total
        val_acc = val_correct / val_total

        # ★ v2: Compute wall+door macro F1 for checkpoint selection
        minority_f1s = []
        for ci in (1, 3):  # wall=1, door=3
            tp, fp, fn = class_tp[ci], class_fp[ci], class_fn[ci]
            prec = tp / (tp + fp) if (tp + fp) > 0 else 0
            rec = tp / (tp + fn) if (tp + fn) > 0 else 0
            f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0
            minority_f1s.append(f1)
        minority_macro_f1 = sum(minority_f1s) / len(minority_f1s) if minority_f1s else 0
        # Combined score: 60% minority F1 + 40% overall accuracy
        val_score = 0.6 * minority_macro_f1 + 0.4 * val_acc

        record = {
            "epoch": epoch,
            "train_loss": round(train_loss, 4),
            "train_acc": round(train_acc, 4),
            "val_loss": round(val_loss, 4),
            "val_acc": round(val_acc, 4),
            "wall_f1": round(minority_f1s[0], 4) if len(minority_f1s) > 0 else 0,
            "door_f1": round(minority_f1s[1], 4) if len(minority_f1s) > 1 else 0,
            "val_score": round(val_score, 4),
            "lr": round(scheduler.get_last_lr()[0], 6),
        }
        history.append(record)

        saved = ""
        if val_score > best_val_score:
            best_val_score = val_score
            save_checkpoint(model, checkpoint_path)
            saved = " [saved]"
            no_improve = 0
        else:
            no_improve += 1

        wall_rec = class_tp[1] / max(1, class_tp[1] + class_fn[1])
        door_rec = class_tp[3] / max(1, class_tp[3] + class_fn[3])
        print(
            f"  Epoch {epoch:3d}/{epochs}  "
            f"loss={train_loss:.4f}  acc={train_acc:.4f}  "
            f"val_acc={val_acc:.4f}  wall_rec={wall_rec:.3f}  door_rec={door_rec:.3f}  "
            f"score={val_score:.4f}{saved}"
        )

        # Early stopping
        if no_improve >= patience:
            print(f"  Early stopping at epoch {epoch} (no improvement for {patience} epochs)")
            break

    print(f"\nBest val score: {best_val_score:.4f}")
    print(f"Checkpoint saved to: {checkpoint_path}")

    # Save training history
    history_path = checkpoint_path.replace(".pth", "_history.json")
    with open(history_path, "w") as f:
        json.dump(history, f, indent=2)
    print(f"Training history saved to: {history_path}")


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate(
    data_dir: str = DEFAULT_DATA_DIR,
    checkpoint_path: str = DEFAULT_CHECKPOINT,
    batch_size: int = 32,
    door_threshold: float = 0.0,
):
    """Evaluate the trained model on the full dataset and print per-class metrics.

    ★ v3: door_threshold — if >0, door predictions below this confidence
    are reassigned to the next-best class. This dramatically reduces door FP.
    """
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    model = TileCNN(NUM_CLASSES).to(device)
    load_checkpoint(model, checkpoint_path, device)
    model.eval()

    if door_threshold > 0:
        print(f"  Door confidence threshold: {door_threshold:.2f}")

    dataset = TileDataset(data_dir)
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=False, num_workers=0)

    # Confusion matrix
    confusion = np.zeros((NUM_CLASSES, NUM_CLASSES), dtype=np.int64)
    door_idx = CLASSES.index("door")

    with torch.no_grad():
        for images, labels in loader:
            images, labels = images.to(device), labels.to(device)
            logits = model(images)
            probs = F.softmax(logits, dim=1)
            preds = probs.argmax(1)

            # ★ v3: Door threshold — reject low-confidence door predictions
            if door_threshold > 0:
                for i in range(preds.size(0)):
                    if preds[i].item() == door_idx:
                        if probs[i, door_idx].item() < door_threshold:
                            # Reassign to next-best non-door class
                            p = probs[i].clone()
                            p[door_idx] = 0
                            preds[i] = p.argmax()

            for t, p in zip(labels.cpu().numpy(), preds.cpu().numpy()):
                confusion[t][p] += 1

    total = confusion.sum()
    correct = confusion.trace()
    print(f"\nOverall accuracy: {correct}/{total} ({correct / total:.4f})\n")

    # Per-class
    print(f"{'Class':<12} {'Precision':>10} {'Recall':>10} {'F1':>10} {'Support':>10}")
    print("-" * 56)
    for i, cls in enumerate(CLASSES):
        tp = confusion[i][i]
        fp = confusion[:, i].sum() - tp
        fn = confusion[i, :].sum() - tp
        support = confusion[i, :].sum()
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2 * precision * recall / (precision + recall)
              if (precision + recall) > 0 else 0.0)
        print(f"{cls:<12} {precision:>10.4f} {recall:>10.4f} {f1:>10.4f} {support:>10d}")

    print(f"\nConfusion matrix (rows=true, cols=predicted):")
    header = "            " + "".join(f"{c:>10}" for c in CLASSES)
    print(header)
    for i, cls in enumerate(CLASSES):
        row = f"{cls:<12}" + "".join(f"{int(confusion[i][j]):>10}" for j in range(NUM_CLASSES))
        print(row)


# ---------------------------------------------------------------------------
# Prediction
# ---------------------------------------------------------------------------

def predict(
    image_paths: list[str],
    checkpoint_path: str = DEFAULT_CHECKPOINT,
    door_threshold: float = 0.0,
):
    """Predict class for one or more tile images.

    ★ v3: door_threshold — if >0, door predictions below this confidence
    are reassigned to the next-best class.
    """
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    door_idx = CLASSES.index("door")

    model = TileCNN(NUM_CLASSES).to(device)
    load_checkpoint(model, checkpoint_path, device)
    model.eval()

    for path in image_paths:
        img = Image.open(path).convert("RGB").resize((IMG_SIZE, IMG_SIZE))
        arr = np.array(img, dtype=np.float32) / 255.0
        tensor = torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0).to(device)

        with torch.no_grad():
            logits = model(tensor)
            probs = F.softmax(logits, dim=1).squeeze(0)

        pred_idx = probs.argmax().item()

        # ★ v3: Door threshold — reject low-confidence door
        if door_threshold > 0 and pred_idx == door_idx:
            if probs[door_idx].item() < door_threshold:
                p = probs.clone()
                p[door_idx] = 0
                pred_idx = p.argmax().item()

        pred_label = CLASSES[pred_idx]
        confidence = probs[pred_idx].item()

        print(f"{path}: {pred_label} ({confidence:.4f})")
        for i, cls in enumerate(CLASSES):
            bar = "#" * int(probs[i].item() * 30)
            print(f"  {cls:<10} {probs[i].item():.4f} {bar}")


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------

def save_checkpoint(model: nn.Module, path: str):
    """Save model weights and class metadata."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    torch.save(
        {
            "model_state_dict": model.state_dict(),
            "classes": CLASSES,
            "img_size": IMG_SIZE,
        },
        path,
    )


def load_checkpoint(model: nn.Module, path: str, device: torch.device):
    """Load model weights from checkpoint."""
    if not os.path.exists(path):
        print(f"Error: checkpoint not found: {path}", file=sys.stderr)
        sys.exit(1)
    ckpt = torch.load(path, map_location=device, weights_only=False)
    model.load_state_dict(ckpt["model_state_dict"])
    print(f"  Loaded checkpoint: {path}")
    if "classes" in ckpt:
        print(f"  Classes: {ckpt['classes']}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="CNN Tile Classifier for Pokemon GBA (16x16 RGB tiles)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # train
    p_train = sub.add_parser("train", help="Train the classifier")
    p_train.add_argument("--data_dir", default=DEFAULT_DATA_DIR)
    p_train.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT)
    p_train.add_argument("--epochs", type=int, default=50)
    p_train.add_argument("--batch_size", type=int, default=32)
    p_train.add_argument("--lr", type=float, default=1e-3)
    p_train.add_argument("--val_split", type=float, default=0.2)
    p_train.add_argument("--seed", type=int, default=42)
    p_train.add_argument("--augment", action="store_true",
                         help="Enable data augmentation (rotation, flip, jitter)")
    p_train.add_argument("--oversample", action="store_true",
                         help="Oversample minority classes (requires --augment)")
    p_train.add_argument("--focal_gamma", type=float, default=2.0,
                         help="Focal loss gamma (0=standard CE, 2=default)")
    p_train.add_argument("--mixup", action="store_true",
                         help="Enable Mixup augmentation (v3, reduces door FP)")
    p_train.add_argument("--mixup_alpha", type=float, default=0.2,
                         help="Mixup interpolation alpha (default 0.2)")
    p_train.add_argument("--label_smoothing", type=float, default=0.0,
                         help="Label smoothing epsilon (v3, 0.05-0.1 recommended)")
    p_train.add_argument("--binary", action="store_true",
                         help="Binary mode: walkable+door vs wall+unknown (2 classes)")

    # eval
    p_eval = sub.add_parser("eval", help="Evaluate on full dataset")
    p_eval.add_argument("--data_dir", default=DEFAULT_DATA_DIR)
    p_eval.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT)
    p_eval.add_argument("--batch_size", type=int, default=32)
    p_eval.add_argument("--door_threshold", type=float, default=0.0,
                         help="Min confidence for door prediction (v3, 0.3-0.6 recommended)")

    # predict
    p_pred = sub.add_parser("predict", help="Predict class for tile image(s)")
    p_pred.add_argument("images", nargs="+", help="Path(s) to 16x16 tile PNG(s)")
    p_pred.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT)
    p_pred.add_argument("--door_threshold", type=float, default=0.0,
                         help="Min confidence for door prediction (v3)")

    args = parser.parse_args()

    if args.command == "train":
        train(
            data_dir=args.data_dir,
            checkpoint_path=args.checkpoint,
            epochs=args.epochs,
            batch_size=args.batch_size,
            lr=args.lr,
            val_split=args.val_split,
            seed=args.seed,
            augment=args.augment,
            oversample=args.oversample,
            mixup_alpha=args.mixup_alpha if args.mixup else 0.0,
            label_smoothing=args.label_smoothing,
            binary=args.binary,
        )
    elif args.command == "eval":
        evaluate(
            data_dir=args.data_dir,
            checkpoint_path=args.checkpoint,
            batch_size=args.batch_size,
            door_threshold=args.door_threshold,
        )
    elif args.command == "predict":
        predict(
            image_paths=args.images,
            checkpoint_path=args.checkpoint,
            door_threshold=args.door_threshold,
        )


if __name__ == "__main__":
    # ★ v8: Default to train with augment+oversample when run without args
    if len(sys.argv) == 1:
        sys.argv.extend(["train", "--augment", "--oversample",
                         "--mixup", "--label_smoothing", "0.05",
                         "--epochs", "60"])
    main()
