"""Train CNN to imitate Brain (Sonnet/Opus) decisions.

Reads `dataset/demonstrations.jsonl` and pairs each entry with its
screenshot file. Trains a vision model (TinyBrainCNN or ResNet-18) by
behavior cloning on the expert's (screen, button) pairs.

Quality-first defaults match the user's directive that inference cost
is zero so the model should be the best practical choice:
- arch = resnet18 (ImageNet-pretrained backbone)
- 224x224 input + ImageNet normalization
- training-time augmentation (random crop, color jitter, horizontal flip OFF
  because Pokemon Emerald is direction-sensitive)
- cosine LR decay, AdamW optimizer, weight decay
- early stopping on best validation accuracy
- class-balanced cross-entropy (rare buttons like Start / Select get more weight)
- saved checkpoint stores `arch` so CNNBrain can re-instantiate the
  matching architecture at inference time

Run:
    poke-rl/Scripts/python.exe -m generic_agent.tools.train_imitation
    poke-rl/Scripts/python.exe -m generic_agent.tools.train_imitation \
        --arch resnet18 --epochs 30 --batch 32 --lr 1e-4
    poke-rl/Scripts/python.exe -m generic_agent.tools.train_imitation \
        --arch tiny --epochs 15
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from sklearn.model_selection import train_test_split
from torch.utils.data import DataLoader, Dataset

from .. import config
from ..brain_cnn import (
    ACTION_LABELS,
    ACTION_TO_IDX,
    IMAGENET_MEAN,
    IMAGENET_STD,
    STATE_DIM,
    build_model,
    load_dataset_index,
    parameter_count,
    screenshot_to_tensor,
    vectorize_state,
)


class DemonstrationDataset(Dataset):
    def __init__(
        self,
        rows: list[dict],
        root: Path,
        img_size: int,
        normalize_imagenet: bool,
        augment: bool = False,
        uses_state: bool = False,
    ) -> None:
        self.root = root
        self.img_size = img_size
        self.normalize_imagenet = normalize_imagenet
        self.augment = augment
        self.uses_state = uses_state
        self.items: list[tuple[Path, int, np.ndarray | None]] = []
        skipped = 0
        for r in rows:
            btn = r.get("button")
            if btn not in ACTION_TO_IDX:
                skipped += 1
                continue
            shot_rel = r.get("screenshot")
            if not shot_rel:
                skipped += 1
                continue
            shot = root / shot_rel
            if not shot.exists():
                skipped += 1
                continue
            state_vec = vectorize_state(r) if uses_state else None
            self.items.append((shot, ACTION_TO_IDX[btn], state_vec))
        if skipped:
            print(
                f"[dataset] skipped {skipped} rows "
                f"(missing image / unknown button)"
            )

    def __len__(self) -> int:
        return len(self.items)

    def _maybe_augment(self, x: torch.Tensor) -> torch.Tensor:
        if not self.augment:
            return x
        # Random crop with 8-pixel jitter (helps small translation
        # invariance without confusing directional decisions).
        pad = 8
        x = nn.functional.pad(x.unsqueeze(0), (pad, pad, pad, pad)).squeeze(0)
        top = int(torch.randint(0, 2 * pad + 1, (1,)).item())
        left = int(torch.randint(0, 2 * pad + 1, (1,)).item())
        x = x[:, top:top + self.img_size, left:left + self.img_size]
        # Color jitter — small contrast/brightness perturbation (GBA
        # rendering is fixed-palette so changes are subtle).
        brightness = 1.0 + (torch.rand(1).item() - 0.5) * 0.2
        contrast = 1.0 + (torch.rand(1).item() - 0.5) * 0.2
        if self.normalize_imagenet:
            mean = torch.tensor(IMAGENET_MEAN).view(3, 1, 1)
            std = torch.tensor(IMAGENET_STD).view(3, 1, 1)
            x_raw = x * std + mean
            x_raw = torch.clamp(
                ((x_raw - 0.5) * contrast + 0.5) * brightness, 0, 1
            )
            x = (x_raw - mean) / std
        else:
            x = torch.clamp(((x - 0.5) * contrast + 0.5) * brightness, 0, 1)
        return x

    def __getitem__(
        self, i: int
    ) -> tuple[torch.Tensor, int] | tuple[torch.Tensor, torch.Tensor, int]:
        shot, label, state_vec = self.items[i]
        x = screenshot_to_tensor(
            shot,
            img_size=self.img_size,
            normalize_imagenet=self.normalize_imagenet,
        )
        x = self._maybe_augment(x)
        if self.uses_state and state_vec is not None:
            return x, torch.from_numpy(state_vec), label
        return x, label


def evaluate(
    model: nn.Module,
    loader: DataLoader,
    device: torch.device,
    uses_state: bool,
) -> tuple[float, float, np.ndarray]:
    model.eval()
    total = 0
    correct = 0
    total_loss = 0.0
    per_class_correct = np.zeros(len(ACTION_LABELS), dtype=np.int64)
    per_class_total = np.zeros(len(ACTION_LABELS), dtype=np.int64)
    crit = nn.CrossEntropyLoss(reduction="sum")
    with torch.no_grad():
        for batch in loader:
            if uses_state:
                x, s, y = batch
                x = x.to(device); s = s.to(device); y = y.to(device)
                logits = model(x, s)
            else:
                x, y = batch
                x = x.to(device); y = y.to(device)
                logits = model(x)
            total_loss += float(crit(logits, y).item())
            pred = torch.argmax(logits, dim=1)
            correct += int((pred == y).sum().item())
            total += int(y.numel())
            for cls in range(len(ACTION_LABELS)):
                mask = y == cls
                per_class_total[cls] += int(mask.sum().item())
                per_class_correct[cls] += int(
                    (pred[mask] == cls).sum().item()
                )
    per_class_acc = per_class_correct / np.maximum(per_class_total, 1)
    return (
        correct / max(1, total),
        total_loss / max(1, total),
        per_class_acc,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--arch",
        default="multimodal",
        choices=["tiny", "resnet18", "multimodal"],
        help="architecture (default: multimodal — image + RAM state)",
    )
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch", type=int, default=32)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--val-split", type=float, default=0.15)
    parser.add_argument(
        "--no-augment", action="store_true",
        help="disable training-time augmentation",
    )
    parser.add_argument(
        "--max-samples", type=int, default=0,
        help="cap dataset size (0 = all)",
    )
    parser.add_argument(
        "--exclude-sources",
        nargs="+",
        default=[],
        help="substrings of `source` to exclude (e.g. 'dialog_continue' "
             "to drop the A-heavy heuristic rows that bias the model).",
    )
    args = parser.parse_args()

    index = config.DATASET_INDEX
    if not index.exists():
        print(
            f"[ERROR] {index} not found. "
            f"Run `auto_loop --dataset` first."
        )
        return 1

    print(f"[dataset] loading index {index}")
    rows = load_dataset_index(index)
    if args.exclude_sources:
        before = len(rows)
        rows = [
            r for r in rows
            if not any(
                s in r.get("source", "") for s in args.exclude_sources
            )
        ]
        print(
            f"[dataset] excluded {before - len(rows)} rows whose "
            f"source contains any of {args.exclude_sources}"
        )
    if args.max_samples > 0:
        rows = rows[: args.max_samples]
    print(f"[dataset] {len(rows)} raw rows")

    print(f"[model] building arch={args.arch}")
    model = build_model(args.arch)
    img_size = type(model).IMG_SIZE
    normalize_imagenet = type(model).NORMALIZE_IMAGENET
    uses_state = type(model).USES_STATE
    print(
        f"[model] params={parameter_count(model):,}  img={img_size}x{img_size}  "
        f"uses_state={uses_state}  state_dim={STATE_DIM if uses_state else 0}"
    )

    full = DemonstrationDataset(
        rows, root=config.ROOT,
        img_size=img_size, normalize_imagenet=normalize_imagenet,
        augment=False, uses_state=uses_state,
    )
    if len(full) == 0:
        print("[ERROR] no usable (image, button) pairs")
        return 1

    labels = [lbl for _, lbl, _ in full.items]
    label_counts = np.bincount(labels, minlength=len(ACTION_LABELS))
    print("[dataset] label distribution:")
    for i, c in enumerate(label_counts):
        pct = c / len(full) * 100 if len(full) else 0
        print(f"  {ACTION_LABELS[i]:6s}: {c:5d}  ({pct:5.1f}%)")

    train_idx, val_idx = train_test_split(
        list(range(len(full))),
        test_size=args.val_split,
        stratify=labels if int(min(label_counts)) >= 2 else None,
        random_state=0,
    )
    train_ds = DemonstrationDataset(
        rows, root=config.ROOT,
        img_size=img_size, normalize_imagenet=normalize_imagenet,
        augment=not args.no_augment, uses_state=uses_state,
    )
    train_ds.items = [full.items[i] for i in train_idx]
    val_ds = DemonstrationDataset(
        rows, root=config.ROOT,
        img_size=img_size, normalize_imagenet=normalize_imagenet,
        augment=False, uses_state=uses_state,
    )
    val_ds.items = [full.items[i] for i in val_idx]
    print(f"[dataset] train={len(train_ds)} val={len(val_ds)}")

    train_loader = DataLoader(
        train_ds, batch_size=args.batch, shuffle=True,
        num_workers=0, pin_memory=False,
    )
    val_loader = DataLoader(
        val_ds, batch_size=args.batch, shuffle=False,
        num_workers=0, pin_memory=False,
    )

    device = torch.device("cpu")
    model = model.to(device)

    weights = np.maximum(label_counts, 1)
    inv = 1.0 / weights
    inv = inv / inv.sum() * len(ACTION_LABELS)
    class_weight = torch.tensor(inv, dtype=torch.float32, device=device)
    criterion = nn.CrossEntropyLoss(weight=class_weight)
    optimizer = optim.AdamW(
        model.parameters(), lr=args.lr, weight_decay=args.weight_decay
    )
    scheduler = optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=args.epochs
    )

    best_val_acc = 0.0
    best_state: dict | None = None
    best_per_class: np.ndarray | None = None
    best_epoch = 0

    for epoch in range(1, args.epochs + 1):
        model.train()
        epoch_loss = 0.0
        seen = 0
        t0 = time.time()
        for batch in train_loader:
            if uses_state:
                x, s, y = batch
                x = x.to(device); s = s.to(device); y = y.to(device)
                logits = model(x, s)
            else:
                x, y = batch
                x = x.to(device); y = y.to(device)
                logits = model(x)
            optimizer.zero_grad()
            loss = criterion(logits, y)
            loss.backward()
            optimizer.step()
            epoch_loss += float(loss.item()) * int(y.numel())
            seen += int(y.numel())
        scheduler.step()
        train_loss = epoch_loss / max(1, seen)
        val_acc, val_loss, per_class_acc = evaluate(
            model, val_loader, device, uses_state
        )
        dt = time.time() - t0
        marker = ""
        if val_acc > best_val_acc:
            best_val_acc = val_acc
            best_state = {
                k: v.detach().cpu().clone()
                for k, v in model.state_dict().items()
            }
            best_per_class = per_class_acc.copy()
            best_epoch = epoch
            marker = "  ← best"
        lr_now = optimizer.param_groups[0]["lr"]
        print(
            f"epoch {epoch:3d}/{args.epochs}  "
            f"train_loss={train_loss:.4f}  "
            f"val_loss={val_loss:.4f}  "
            f"val_acc={val_acc:.4f}  "
            f"lr={lr_now:.2e}  "
            f"({dt:.1f}s){marker}"
        )

    if best_state is None:
        print("[ERROR] training produced no model")
        return 1

    print()
    print(f"[best] epoch {best_epoch}  val_acc={best_val_acc:.4f}")
    if best_per_class is not None:
        print("[best] per-class accuracy:")
        for i, acc in enumerate(best_per_class):
            print(f"  {ACTION_LABELS[i]:6s}: {acc:.3f}")

    config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    tag = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    ckpt_path = config.MODEL_DIR / f"brain_cnn_{args.arch}_{tag}.pt"
    torch.save(
        {
            "arch": args.arch,
            "state_dict": best_state,
            "val_acc": best_val_acc,
            "epochs_trained": args.epochs,
            "best_epoch": best_epoch,
            "dataset_size": len(full),
            "action_labels": ACTION_LABELS,
        },
        ckpt_path,
    )
    latest = config.MODEL_DIR / "brain_cnn_latest.pt"
    try:
        if latest.exists() or latest.is_symlink():
            latest.unlink()
        shutil.copy2(ckpt_path, latest)
    except OSError as exc:
        print(f"[WARN] could not update latest pointer: {exc}")
    print(
        f"[done] saved {ckpt_path.name} "
        f"(copied to brain_cnn_latest.pt)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
