"""
Tile Label Conflict Resolution Tool

Scans tile_data/ subdirectories (door, wall, walkable, unknown), hashes each
image by MD5, finds images that appear in multiple class folders, and resolves
conflicts via majority vote. Ties go to 'unknown'.

Conflicting copies are moved to the winning folder; losers are backed up to
tile_data/_conflicts_backup/<original_class>/.

Usage:
    python tools/resolve_tile_conflicts.py [--data-dir tile_data] [--dry-run]
"""

import argparse
import hashlib
import os
import shutil
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image

CLASSES = ["door", "wall", "walkable", "unknown"]


def md5_image(path: str) -> str:
    """Compute MD5 hash of an image file's raw bytes."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_tiles(data_dir: str) -> dict:
    """
    Scan all class subdirectories and build a mapping:
        hash -> [(class_name, file_path), ...]
    """
    hash_map = defaultdict(list)
    for cls in CLASSES:
        cls_dir = os.path.join(data_dir, cls)
        if not os.path.isdir(cls_dir):
            print(f"  Warning: directory not found: {cls_dir}")
            continue
        for fname in os.listdir(cls_dir):
            if not fname.lower().endswith(".png"):
                continue
            fpath = os.path.join(cls_dir, fname)
            h = md5_image(fpath)
            hash_map[h].append((cls, fpath))
    return hash_map


def resolve_conflicts(hash_map: dict, data_dir: str, dry_run: bool = False):
    """
    For each hash that appears in multiple classes, pick a winner via majority
    vote (ties -> 'unknown'), move losers to backup, and keep/move winner.
    """
    backup_dir = os.path.join(data_dir, "_conflicts_backup")
    conflicts = {h: entries for h, entries in hash_map.items() if len(set(c for c, _ in entries)) > 1}

    if not conflicts:
        print("No conflicts found. All images have unique class labels.")
        return 0, 0, 0

    total_conflicts = len(conflicts)
    total_resolved = 0
    total_moved = 0

    for h, entries in conflicts.items():
        # Count how many files each class has for this hash
        class_counts = Counter(cls for cls, _ in entries)
        max_count = max(class_counts.values())
        winners = [cls for cls, cnt in class_counts.items() if cnt == max_count]

        # Majority vote; tie -> 'unknown'
        if len(winners) == 1:
            winner = winners[0]
        else:
            winner = "unknown" if "unknown" in winners else winners[0]

        # Group entries by class
        by_class = defaultdict(list)
        for cls, fpath in entries:
            by_class[cls].append(fpath)

        # Process losing classes: back up and remove their copies
        for cls, files in by_class.items():
            if cls == winner:
                continue
            for fpath in files:
                fname = os.path.basename(fpath)
                bak_dir = os.path.join(backup_dir, cls)
                bak_path = os.path.join(bak_dir, fname)

                if dry_run:
                    print(f"  [DRY-RUN] Would move {cls}/{fname} -> _conflicts_backup/{cls}/")
                else:
                    os.makedirs(bak_dir, exist_ok=True)
                    # Avoid overwrite: add hash prefix if name collision
                    if os.path.exists(bak_path):
                        base, ext = os.path.splitext(fname)
                        bak_path = os.path.join(bak_dir, f"{base}_{h[:8]}{ext}")
                    shutil.move(fpath, bak_path)
                total_moved += 1

        # If the winner class doesn't already have a copy, move one from backup
        # (This shouldn't normally happen since winner already has files)

        total_resolved += 1

    return total_conflicts, total_resolved, total_moved


def main():
    parser = argparse.ArgumentParser(description="Resolve tile label conflicts via majority vote.")
    parser.add_argument("--data-dir", default="tile_data", help="Path to tile_data directory")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    args = parser.parse_args()

    data_dir = args.data_dir
    if not os.path.isdir(data_dir):
        print(f"Error: data directory not found: {data_dir}")
        return

    print(f"Scanning tile data in: {os.path.abspath(data_dir)}")
    print(f"Classes: {CLASSES}")
    print()

    # Phase 1: Scan and hash
    print("Phase 1: Hashing all images...")
    hash_map = scan_tiles(data_dir)
    total_images = sum(len(v) for v in hash_map.values())
    unique_hashes = len(hash_map)
    print(f"  Total image files: {total_images}")
    print(f"  Unique images (by MD5): {unique_hashes}")
    print(f"  Duplicate files (same hash, any folder): {total_images - unique_hashes}")
    print()

    # Phase 2: Find conflicts (same image in multiple classes)
    conflicts = {h: entries for h, entries in hash_map.items() if len(set(c for c, _ in entries)) > 1}
    print(f"Phase 2: Found {len(conflicts)} unique images with conflicting labels")

    if conflicts:
        # Show class distribution of conflicts
        conflict_class_counts = Counter()
        for h, entries in conflicts.items():
            for cls, _ in entries:
                conflict_class_counts[cls] += 1
        print("  Conflict involvement by class:")
        for cls in CLASSES:
            print(f"    {cls}: {conflict_class_counts.get(cls, 0)} files")
        print()

    # Phase 3: Resolve
    mode = "[DRY-RUN] " if args.dry_run else ""
    print(f"Phase 3: {mode}Resolving conflicts via majority vote...")
    total_conflicts, total_resolved, total_moved = resolve_conflicts(hash_map, data_dir, args.dry_run)
    print()

    # Summary
    print("=" * 50)
    print("SUMMARY")
    print("=" * 50)
    print(f"  Total image files scanned:  {total_images}")
    print(f"  Unique images (by MD5):     {unique_hashes}")
    print(f"  Conflicts found:            {total_conflicts}")
    print(f"  Conflicts resolved:         {total_resolved}")
    print(f"  Files moved to backup:      {total_moved}")
    if not args.dry_run and total_moved > 0:
        backup_dir = os.path.join(data_dir, "_conflicts_backup")
        print(f"  Backup location:            {os.path.abspath(backup_dir)}")
    print("=" * 50)

    # Post-resolution counts
    if not args.dry_run and total_moved > 0:
        print("\nPost-resolution class sizes:")
        for cls in CLASSES:
            cls_dir = os.path.join(data_dir, cls)
            if os.path.isdir(cls_dir):
                count = len([f for f in os.listdir(cls_dir) if f.lower().endswith(".png")])
                print(f"  {cls}: {count}")


if __name__ == "__main__":
    main()
