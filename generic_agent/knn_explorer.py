"""PWhiddy v1 KNN exploration — novelty-based intrinsic motivation.

Original PWhiddy used HNSW (hnswlib) over flattened 4320-dim frame
vectors. hnswlib doesn't build cleanly on Windows + CPython 3.13, so we
use sklearn BallTree as a drop-in: same "approximate nearest neighbor"
interface, similar query latency for our index sizes (<5K entries).

API:
    novel = explorer.query_or_add(vec, threshold=2.0)
        - returns True if vec is far enough from every stored vec
          (distance > threshold), in which case vec is added
        - returns False if vec is within threshold of some stored vec
        - threshold tuned for L2 over uint8 64-d image embeddings

Use case (in claude_heuristic):
    emb = preprocess.frame_embedding(screenshot_path)  # 64-d
    if explorer.query_or_add(emb, threshold=200.0):
        reward = KNN_NOVELTY_REWARD
"""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path

import numpy as np

KNN_NOVELTY_REWARD = 5.0
MAX_ELEMENTS = 5000


class KNNExplorer:
    def __init__(self, dim: int = 64, max_elements: int = MAX_ELEMENTS):
        self.dim = dim
        self.max_elements = max_elements
        self.vecs: deque[np.ndarray] = deque(maxlen=max_elements)
        self._tree = None
        self._tree_dirty = True

    def query_or_add(self, vec: np.ndarray, threshold: float = 200.0) -> bool:
        if vec.ndim != 1 or vec.shape[0] != self.dim:
            return False
        if not self.vecs:
            self.vecs.append(vec.astype(np.float32))
            self._tree_dirty = True
            return True
        if self._tree_dirty or self._tree is None:
            self._rebuild_tree()
        dist, _ = self._tree.query(vec.reshape(1, -1), k=1)
        if dist[0, 0] > threshold:
            self.vecs.append(vec.astype(np.float32))
            self._tree_dirty = True
            return True
        return False

    def _rebuild_tree(self) -> None:
        from sklearn.neighbors import BallTree
        data = np.stack(list(self.vecs)).astype(np.float32)
        self._tree = BallTree(data, leaf_size=40, metric="euclidean")
        self._tree_dirty = False

    def size(self) -> int:
        return len(self.vecs)

    def save(self, path: Path) -> None:
        if not self.vecs:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        arr = np.stack(list(self.vecs)).astype(np.float32)
        np.savez(path, vecs=arr)

    def load(self, path: Path) -> bool:
        if not path.exists():
            return False
        try:
            data = np.load(path)
            self.vecs = deque(data["vecs"], maxlen=self.max_elements)
            self._tree_dirty = True
            return True
        except (OSError, KeyError, ValueError):
            return False
