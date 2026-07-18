"""Per-tileset behavior tables + elevation-carry BFS の決定的テスト。

ネットワーク / 実 map_cache 不要: tmpdir に合成 layouts.json / map.json /
attr.bin を置き、合成 MapInfo を注入して検証する。

背景 (2026-07-18 Route114 スタック):
  - v1 の _load_behavior_table は「キャッシュにある secondary_*.bin 全部」
    (実質 rustboro のみ) を全マップに適用 → Route114 (secondary =
    gTileset_Fallarbor) の ledge メタタイル 51 個が behavior 0x00 に化けて
    ledge_jumps が空になった。
  - さらに (21,57)e3 -> Down (21,58)e4 のハードブロックは ledge ではなく
    ELEVATION MISMATCH (pokeemerald GetCollisionAtCoords ->
    IsElevationMismatchAt)。BFS は (x, y, carried_elevation) 状態で
    ゲームと同じ持ち越し規則 (ObjectEventUpdateElevation) を再現する。
"""
from __future__ import annotations

import json
import shutil
import struct
import tempfile
import unittest
from pathlib import Path

from generic_agent import config, map_data as md, map_knowledge as mk_mod
from generic_agent.map_data import MapCache, MapInfo


def _grid(width: int, height: int, blocked: set[tuple[int, int]]):
    return [
        [1 if (x, y) in blocked else 0 for x in range(width)]
        for y in range(height)
    ]


def _cache_with(info: MapInfo) -> MapCache:
    mc = MapCache.__new__(MapCache)
    mc._maps = {(info.map_g, info.map_n): info}
    mc._layouts = {}
    mc._map_groups = []
    mc._loaded_index = True
    return mc


class TilesetDirnameTest(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(md._tileset_dirname("gTileset_General"), "general")
        self.assertEqual(md._tileset_dirname("gTileset_Fallarbor"), "fallarbor")

    def test_camel_case_split(self):
        self.assertEqual(
            md._tileset_dirname("gTileset_MeteorFalls"), "meteor_falls",
        )
        self.assertEqual(
            md._tileset_dirname("gTileset_EverGrande"), "ever_grande",
        )


class PerMapBehaviorTableTest(unittest.TestCase):
    """_load_behavior_table はマップの layout が指す tileset pair を使う。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        cache = self.tmp / "map_cache"
        cache.mkdir(parents=True)
        (cache / "layouts.json").write_text(json.dumps({
            "layouts": [
                {"id": "LAYOUT_X",
                 "primary_tileset": "gTileset_General",
                 "secondary_tileset": "gTileset_Fallarbor"},
                {"id": "LAYOUT_Y",
                 "primary_tileset": "gTileset_General",
                 "secondary_tileset": "gTileset_Rustboro"},
            ]
        }), encoding="utf-8")
        (cache / "MapX.map.json").write_text(
            json.dumps({"layout": "LAYOUT_X"}), encoding="utf-8")
        (cache / "MapY.map.json").write_text(
            json.dumps({"layout": "LAYOUT_Y"}), encoding="utf-8")
        # primary: metatile 1 = TALL_GRASS(0x02)
        prim = bytearray(0x200 * 2)
        struct.pack_into("<H", prim, 1 * 2, 0x0002)
        (cache / "primary_general_attr.bin").write_bytes(bytes(prim))
        # fallarbor secondary: entry 0 (= metatile 0x200) = JUMP_SOUTH(0x3B)
        (cache / "secondary_fallarbor_attr.bin").write_bytes(
            struct.pack("<H", 0x103B))
        # rustboro secondary: entry 0 = POND_WATER(0x10)
        (cache / "secondary_rustboro_attr.bin").write_bytes(
            struct.pack("<H", 0x0010))
        self._old_memory_dir = config.MEMORY_DIR
        self._old_cache_dir = md.CACHE_DIR
        config.MEMORY_DIR = self.tmp
        md.CACHE_DIR = self.tmp / "map_cache"

    def tearDown(self):
        config.MEMORY_DIR = self._old_memory_dir
        md.CACHE_DIR = self._old_cache_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _store(self) -> mk_mod.MapKnowledgeStore:
        st = mk_mod.MapKnowledgeStore.__new__(mk_mod.MapKnowledgeStore)
        st._cache = {}
        st._mc = None
        st._beh_cache = {}
        return st

    def test_secondary_resolved_per_map(self):
        st = self._store()
        tx = st._load_behavior_table("MapX")
        ty = st._load_behavior_table("MapY")
        # primary range identical
        self.assertEqual(tx[1], 0x02)
        self.assertEqual(ty[1], 0x02)
        # secondary range differs per map: 0x200 is a ledge on X, water on Y.
        # v1 applied one fixed secondary to both — the Route114 bug.
        self.assertEqual(tx[0x200], 0x3B)
        self.assertEqual(ty[0x200], 0x10)

    def test_unresolvable_map_falls_back_to_primary_only(self):
        st = self._store()
        t = st._load_behavior_table("NoSuchMap")
        self.assertEqual(t[1], 0x02)       # primary still classified
        self.assertNotIn(0x200, t)         # no wrong secondary applied

    def test_behavior_is_low_byte_of_u16(self):
        # attr 0x103B (layer bits set) must classify as behavior 0x3B.
        st = self._store()
        self.assertEqual(st._load_behavior_table("MapX")[0x200], 0x3B)


class SeedVersionMigrationTest(unittest.TestCase):
    """v1 の persisted knowledge は get() で再導出され、empirical を温存する。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        cache = self.tmp / "map_cache"
        cache.mkdir(parents=True)
        (cache / "layouts.json").write_text(json.dumps({
            "layouts": [{"id": "LAYOUT_X",
                         "primary_tileset": "gTileset_General",
                         "secondary_tileset": "gTileset_Fallarbor"}]
        }), encoding="utf-8")
        (cache / "MapX.map.json").write_text(
            json.dumps({"layout": "LAYOUT_X"}), encoding="utf-8")
        prim = bytearray(0x200 * 2)
        (cache / "primary_general_attr.bin").write_bytes(bytes(prim))
        # secondary entry 0 -> metatile 0x200 = JUMP_SOUTH(0x3B)
        (cache / "secondary_fallarbor_attr.bin").write_bytes(
            struct.pack("<H", 0x3B))
        # 2x1 map.bin: (0,0)=primary metatile 0 e3, (1,0)=secondary 0x200 e4
        (cache / "MapX.map.bin").write_bytes(
            struct.pack("<HH", 0x3000 | 0x000, 0x4000 | 0x200))
        kd = self.tmp / "knowledge"
        kd.mkdir()
        # v1 file (no seed_version): wrong table -> no ledges; has empirical
        (kd / "0-0.json").write_text(json.dumps({
            "map_g": 0, "map_n": 0, "name": "MapX",
            "width": 2, "height": 1,
            "grass_tiles": [[1, 0]],          # v1 誤分類 (捨てられるべき)
            "ledge_jumps": {},                # v1 は ledge を見落とした
            "encounters_seen": [{"x": 0, "y": 0, "species": 1, "level": 5}],
            "trainer_los": [[0, 0]],
            "canon_loaded": True,
        }), encoding="utf-8")
        self._old = (config.MEMORY_DIR, md.CACHE_DIR, mk_mod.KNOWLEDGE_DIR)
        config.MEMORY_DIR = self.tmp
        md.CACHE_DIR = self.tmp / "map_cache"
        mk_mod.KNOWLEDGE_DIR = kd

    def tearDown(self):
        config.MEMORY_DIR, md.CACHE_DIR, mk_mod.KNOWLEDGE_DIR = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_v1_file_reseeds_and_keeps_empirical(self):
        info = MapInfo(
            map_g=0, map_n=0, name="MapX", layout_id="LAYOUT_X",
            width=2, height=1, collision=[[0, 0]],
        )
        st = mk_mod.MapKnowledgeStore.__new__(mk_mod.MapKnowledgeStore)
        st._cache = {}
        st._beh_cache = {}

        class _FakeMC:
            def get(self, g, n):
                return info
        st._mc = _FakeMC()
        mk = st.get(0, 0)
        self.assertEqual(mk.seed_version, mk_mod.SEED_VERSION)
        # ledge が正しい secondary テーブルで出現
        self.assertEqual(mk.ledge_jumps.get((1, 0)), (0, 1))
        # empirical: encounters と、その座標の grass は温存
        self.assertEqual(len(mk.encounters_seen), 1)
        self.assertIn((0, 0), mk.grass_tiles)
        self.assertIn((0, 0), mk.trainer_los)
        # v1 の誤分類 grass (ledge タイル) は canon 再導出で消える
        self.assertNotIn((1, 0), mk.grass_tiles)
        # elevation も再導出される
        self.assertEqual(mk.tile_elevation[(0, 0)], 3)
        self.assertEqual(mk.tile_elevation[(1, 0)], 4)
        # persisted file が sv=2 で書き戻されている
        saved = json.loads(
            (mk_mod.KNOWLEDGE_DIR / "0-0.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["seed_version"], mk_mod.SEED_VERSION)


class ElevationCarryBfsTest(unittest.TestCase):
    """bfs_to_tile のゲーム準拠 elevation 規則。

    corridor: (0,0)..(4,0) 全部 collision 歩行可。elevation を張り替えて
    エッジ許否を検証する。"""

    def _mc(self, width=5, height=3, blocked=frozenset()):
        info = MapInfo(
            map_g=0, map_n=0, name="Synth", layout_id="L",
            width=width, height=height,
            collision=_grid(width, height, set(blocked)),
        )
        return _cache_with(info)

    def test_direct_elevation_mismatch_blocked(self):
        # e3 -> e4 隣接はゲームでは COLLISION_ELEVATION_MISMATCH。
        # Route114 (21,57)->(21,58) の最小再現。
        mc = self._mc()
        elev = {(0, 0): 3, (1, 0): 3, (2, 0): 4, (3, 0): 4, (4, 0): 4}
        # y=1,2 行は elevation 未知(=0 wildcard)だが壁にして一本道にする
        mc._maps[(0, 0)].collision = _grid(
            5, 3, {(x, y) for x in range(5) for y in (1, 2)})
        path = mc.bfs_to_tile(0, 0, (0, 0), {(4, 0)}, tile_elevation=elev)
        self.assertIsNone(path)

    def test_transition_tile_bridges_elevations(self):
        # e3 -> e0(transition) -> e4 は通れる(0 は wildcard + carry=0)。
        mc = self._mc()
        mc._maps[(0, 0)].collision = _grid(
            5, 3, {(x, y) for x in range(5) for y in (1, 2)})
        elev = {(0, 0): 3, (1, 0): 3, (2, 0): 0, (3, 0): 4, (4, 0): 4}
        path = mc.bfs_to_tile(0, 0, (0, 0), {(4, 0)}, tile_elevation=elev)
        self.assertEqual(path, ["Right"] * 4)

    def test_multi_level_preserves_carry(self):
        # e4 -> e15(bridge) -> e3 は降り口で mismatch(carry は 4 のまま)。
        # e4 -> e15 -> e4 は通れる。pokeemerald ObjectEventUpdateElevation
        # は cur/prev いずれか 15 のとき currentElevation を更新しない。
        mc = self._mc(width=5)
        mc._maps[(0, 0)].collision = _grid(
            5, 3, {(x, y) for x in range(5) for y in (1, 2)})
        elev = {(0, 0): 4, (1, 0): 15, (2, 0): 15, (3, 0): 3, (4, 0): 3}
        self.assertIsNone(
            mc.bfs_to_tile(0, 0, (0, 0), {(4, 0)}, tile_elevation=elev))
        elev[(3, 0)] = 4
        elev[(4, 0)] = 4
        self.assertEqual(
            mc.bfs_to_tile(0, 0, (0, 0), {(4, 0)}, tile_elevation=elev),
            ["Right"] * 4)

    def test_missing_elevation_is_wildcard(self):
        # tile_elevation を渡さない legacy 呼び出しは collision-only のまま。
        mc = self._mc()
        path = mc.bfs_to_tile(0, 0, (0, 0), {(4, 0)})
        self.assertEqual(path, ["Right"] * 4)

    def test_ledge_jump_edge_and_entry_block(self):
        # (2,0) が JUMP_EAST の ledge: Right で踏むと (3,0) を飛び越えて
        # (3,0)? いや着地は ledge の 1 つ先 = (3,0)。ジャンプ方向一致のみ
        # 通過でき、逆方向 (Left で (2,0) に入る) は塞がる。
        mc = self._mc()
        mc._maps[(0, 0)].collision = _grid(
            5, 3, {(x, y) for x in range(5) for y in (1, 2)})
        ledges = {(2, 0): (1, 0)}  # JUMP_EAST
        p = mc.bfs_to_tile(0, 0, (0, 0), {(4, 0)}, ledge_jumps=ledges)
        # Right(1,0) -> Right jump lands (3,0) -> Right (4,0)
        self.assertEqual(p, ["Right", "Right", "Right"])
        # 逆走: (4,0) から (0,0) へは ledge が壁 → None
        self.assertIsNone(
            mc.bfs_to_tile(0, 0, (4, 0), {(0, 0)}, ledge_jumps=ledges))

    def test_ledge_jump_ignores_ledge_tile_collision(self):
        # ledge メタタイルは collision=1 で置かれる (Route114 実測 51/51)。
        # ジャンプはそれを飛び越える (CheckForObjectEventCollision は
        # collision 結果より先に ShouldJumpLedge を返す)。
        mc = self._mc()
        blocked = {(x, y) for x in range(5) for y in (1, 2)} | {(2, 0)}
        mc._maps[(0, 0)].collision = _grid(5, 3, blocked)
        ledges = {(2, 0): (1, 0)}
        p = mc.bfs_to_tile(0, 0, (0, 0), {(4, 0)}, ledge_jumps=ledges)
        self.assertEqual(p, ["Right", "Right", "Right"])


if __name__ == "__main__":
    unittest.main()
