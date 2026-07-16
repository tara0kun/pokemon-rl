"""map_data の is_indoor 判定と bfs_to_tile(extra_walkable) の決定的テスト。

なぜ tempfile fixture なのか:
  MapCache.is_indoor は呼び出し時に config.MEMORY_DIR / "map_cache" /
  "<MapName>.map.json" の map_type を読む。この実キャッシュ
  (generic_agent/memory/map_cache/) は .gitignore 済みで、fresh clone / CI
  には少数の実マップしか存在しない ── もしくは全く存在しない。実キャッシュの
  存在ガードで分岐するテストは、そこに無いマップを参照した瞬間に永久に実行
  されなくなり、is_indoor の実効カバレッジがゼロになる (前回 REJECT の真因)。

  そこで本テストは tempfile.TemporaryDirectory に合成 map.json fixture を書き、
  config.MEMORY_DIR (is_indoor が読むパス) と map_data.CACHE_DIR (__init__ の
  mkdir 先) を両方その tmp に差し替える。合成マップ名は実キャッシュの実マップ
  いずれとも一致させないため、実キャッシュの有無に関わらず必ず実行される。
  実行スキップ系デコレータは一切使わない。

ネットワーク禁止も推論ではなく強制する: MapCache._download を fail mock に
差し替え、cold cache のダウンロード経路 (_ensure_index / get) に触れた瞬間に
テストが落ちるようにする。fixture は _loaded_index / _map_groups / _maps を
直接注入して _ensure_index のダウンロード経路自体を殺す。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from generic_agent import config
from generic_agent import map_data as md
from generic_agent.map_data import MapCache, MapInfo


def _forbid_network(tc: unittest.TestCase) -> None:
    """_download が呼ばれたら即 fail。「ネットワーク禁止」をテスト自身に強制させる。"""
    patcher = mock.patch.object(
        md.MapCache, "_download",
        side_effect=AssertionError("network access attempted"),
    )
    patcher.start()
    tc.addCleanup(patcher.stop)


# 合成マップ名リスト。実キャッシュの実マップと意図的に重複させない。
_SYNTHETIC_MAPS = [
    "SyntheticIndoor",
    "SyntheticCave",
    "SyntheticRoute",
    "SyntheticTown",
    "SyntheticMissing",
]


class IsIndoorTests(unittest.TestCase):
    """is_indoor の全分岐を実キャッシュ非依存で検証する (実行スキップ禁止)。"""

    def setUp(self) -> None:
        _forbid_network(self)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        mem = Path(tmp.name)
        cache_dir = mem / "map_cache"
        cache_dir.mkdir(parents=True)
        self.cache_dir = cache_dir

        # is_indoor は config.MEMORY_DIR を、__init__ は md.CACHE_DIR を見る。
        # 両方 tmp に差し替えないと、前者で実キャッシュを読み、後者で実 mkdir が走る。
        p_mem = mock.patch.object(config, "MEMORY_DIR", mem)
        p_cache = mock.patch.object(md, "CACHE_DIR", cache_dir)
        p_mem.start()
        self.addCleanup(p_mem.stop)
        p_cache.start()
        self.addCleanup(p_cache.stop)

        self.cache = MapCache()
        # この 2 行が _ensure_index のダウンロード経路を殺す。
        self.cache._map_groups = [list(_SYNTHETIC_MAPS)]
        self.cache._loaded_index = True

    def _id_for(self, name: str) -> tuple[int, int]:
        return (0, _SYNTHETIC_MAPS.index(name))

    def _write_map_json(self, name: str, map_type: str) -> None:
        path = self.cache_dir / f"{name}.map.json"
        path.write_text(json.dumps({"map_type": map_type}), encoding="utf-8")

    def test_indoor_map_type_returns_true(self) -> None:
        self._write_map_json("SyntheticIndoor", "MAP_TYPE_INDOOR")
        g, n = self._id_for("SyntheticIndoor")
        self.assertTrue(self.cache.is_indoor(g, n))

    def test_underground_is_not_indoor(self) -> None:
        # 洞窟 (MAP_TYPE_UNDERGROUND) は wild encounter があり grind が依存する。
        # INDOOR 扱いにすると grind が壊れる (map_data.py:88-90 の意図の回帰テスト)。
        self._write_map_json("SyntheticCave", "MAP_TYPE_UNDERGROUND")
        g, n = self._id_for("SyntheticCave")
        self.assertFalse(self.cache.is_indoor(g, n))

    def test_route_and_town_are_not_indoor(self) -> None:
        self._write_map_json("SyntheticRoute", "MAP_TYPE_ROUTE")
        self._write_map_json("SyntheticTown", "MAP_TYPE_TOWN")
        rg, rn = self._id_for("SyntheticRoute")
        tg, tn = self._id_for("SyntheticTown")
        self.assertFalse(self.cache.is_indoor(rg, rn))
        self.assertFalse(self.cache.is_indoor(tg, tn))

    def test_missing_map_json_returns_false_without_raising(self) -> None:
        # map.json を書かないマップ → 例外を投げず False。
        g, n = self._id_for("SyntheticMissing")
        self.assertFalse(self.cache.is_indoor(g, n))


class BfsExtraWalkableTests(unittest.TestCase):
    """bfs_to_tile(extra_walkable=) の壁貫通と blocked 優先を検証する。"""

    def setUp(self) -> None:
        _forbid_network(self)

    def _cache_with(self, info: MapInfo) -> MapCache:
        """__init__(mkdir/network) を回避して 1 マップだけ注入した MapCache。"""
        mc = MapCache.__new__(MapCache)
        mc._maps = {(info.map_g, info.map_n): info}
        mc._layouts = {}
        mc._map_groups = []
        mc._loaded_index = True
        return mc

    def _corridor(self) -> MapCache:
        # 高さ 1 の廊下: x=2 が壁で start(0,0) と target(4,0) を分断する。
        info = MapInfo(
            map_g=0, map_n=0, name="SyntheticCorridor", layout_id="L",
            width=5, height=1,
            collision=[[0, 0, 1, 0, 0]],
        )
        return self._cache_with(info)

    def test_wall_blocks_without_extra_walkable(self) -> None:
        mc = self._corridor()
        path = mc.bfs_to_tile(0, 0, (0, 0), {(4, 0)})
        self.assertIsNone(path)

    def test_extra_walkable_penetrates_wall(self) -> None:
        mc = self._corridor()
        path = mc.bfs_to_tile(0, 0, (0, 0), {(4, 0)}, extra_walkable={(2, 0)})
        self.assertEqual(path, ["Right"] * 4)

    def test_blocked_tiles_override_extra_walkable(self) -> None:
        mc = self._corridor()
        path = mc.bfs_to_tile(
            0, 0, (0, 0), {(4, 0)},
            extra_walkable={(2, 0)}, blocked_tiles={(2, 0)},
        )
        self.assertIsNone(path)


if __name__ == "__main__":
    unittest.main()
