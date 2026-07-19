"""battle_heal (mid-battle Super Potion) のテスト。

トリガー述語 should_heal の全条件分岐と、run_battle_heal_subtask の
入口ガード/成功判定/禁止ボタンfilter を、mGBA なしの fake client +
patch した VLM で検証する。Flannery 戦 (Overheat out-heal) の前提回路。
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from generic_agent import battle_heal
from generic_agent.state import GameState


def make_gs(**kw) -> GameState:
    base = dict(
        map_group=0, map_num=10, x=5, y=5, saveblock1_valid=True,
        party_count=1, badge_count=0, total_event_flags=0,
        event_flag_bytes_hex="",
        party0_hp=40, party0_max_hp=120, bag_heal_qty=3,
        bag_super_potion_qty=3, in_battle=True,
    )
    base.update(kw)
    return GameState(**base)


class ShouldHealTest(unittest.TestCase):
    """発火条件: our-turn 分岐から呼ばれる純関数。"""

    def test_fires_low_hp_with_potion_enemy_alive(self):
        gs = make_gs(party0_hp=40, party0_max_hp=120)  # 0.33 < 0.40
        self.assertTrue(battle_heal.should_heal(gs, 50, turn=10,
                                                cooldown_until=0))

    def test_no_fire_at_or_above_threshold(self):
        # ちょうど閾値 (0.40) は「まだ戦える」— strict less-than。
        gs = make_gs(party0_hp=48, party0_max_hp=120)
        self.assertFalse(battle_heal.should_heal(gs, 50, 10, 0))
        gs = make_gs(party0_hp=90, party0_max_hp=120)
        self.assertFalse(battle_heal.should_heal(gs, 50, 10, 0))

    def test_no_fire_without_potion(self):
        gs = make_gs(bag_heal_qty=0)
        self.assertFalse(battle_heal.should_heal(gs, 50, 10, 0))

    def test_no_fire_when_enemy_fainted_or_unread(self):
        # enemy 0 = faint transition (B-mash 域)、-1 = RAM 読めず。
        gs = make_gs()
        self.assertFalse(battle_heal.should_heal(gs, 0, 10, 0))
        self.assertFalse(battle_heal.should_heal(gs, -1, 10, 0))

    def test_no_fire_when_lead_fainted_or_unreadable(self):
        self.assertFalse(battle_heal.should_heal(
            make_gs(party0_hp=0), 50, 10, 0))
        self.assertFalse(battle_heal.should_heal(
            make_gs(party0_hp=0, party0_max_hp=0), 50, 10, 0))
        self.assertFalse(battle_heal.should_heal(None, 50, 10, 0))

    def test_cooldown_blocks(self):
        gs = make_gs()
        self.assertFalse(battle_heal.should_heal(gs, 50, turn=5,
                                                 cooldown_until=10))
        self.assertTrue(battle_heal.should_heal(gs, 50, turn=10,
                                                cooldown_until=10))


class _FakeClient:
    """tap を記録するだけの偽 mGBA client。read_state は module patch 側で
    tap 数に応じた GameState を返す (RAM 読みの時系列を再現)。"""

    def __init__(self) -> None:
        self.taps: list[str] = []

    def tap(self, button: str, frames: int = 10) -> None:
        self.taps.append(button)

    def screenshot(self, path) -> None:  # VLM は patch 済みなので中身不要
        pass


def _resp(button: str):
    return SimpleNamespace(content=[SimpleNamespace(
        text=f'{{"button": "{button}", "reason": "test"}}')])


class RunSubtaskTest(unittest.TestCase):
    def _run(self, client, state_fn, vlm_button="A"):
        with mock.patch.object(battle_heal.time, "sleep", lambda s: None), \
             mock.patch.object(battle_heal.state_mod, "read_state",
                               side_effect=lambda c: state_fn(client)), \
             mock.patch.object(battle_heal.rescue_brain, "_call_haiku",
                               return_value=(_resp(vlm_button), 0, "")):
            return battle_heal.run_battle_heal_subtask(client)

    def test_success_on_qty_decrease(self):
        client = _FakeClient()

        def state_fn(c):
            if len(c.taps) >= 8:  # opener 6 + VLM 2 手で potion 消費とする
                return make_gs(party0_hp=80, bag_heal_qty=2,
                               bag_super_potion_qty=2)
            return make_gs(party0_hp=30)

        self.assertTrue(self._run(client, state_fn))
        # 決定論 opener: B,B で submenu 脱出 → FIGHT 左上固定 → Right=BAG → A
        self.assertEqual(client.taps[:6],
                         ["B", "B", "Up", "Left", "Right", "A"])
        self.assertNotIn("Start", client.taps)
        self.assertNotIn("Select", client.taps)

    def test_success_on_hp_increase_only(self):
        # qty read が全 read flicker で減少を見せなくても HP 増で成功扱い。
        client = _FakeClient()

        def state_fn(c):
            if len(c.taps) >= 8:
                return make_gs(party0_hp=80, bag_heal_qty=3)
            return make_gs(party0_hp=30)

        self.assertTrue(self._run(client, state_fn))

    def test_noop_when_already_above_threshold(self):
        client = _FakeClient()
        ok = self._run(client, lambda c: make_gs(party0_hp=100))
        self.assertTrue(ok)          # flicker 誤発火の no-op 成功
        self.assertEqual(client.taps, [])  # ボタンは一切押さない

    def test_gives_up_without_potion(self):
        client = _FakeClient()
        ok = self._run(client, lambda c: make_gs(party0_hp=30,
                                                 bag_heal_qty=0,
                                                 bag_super_potion_qty=0))
        self.assertFalse(ok)
        self.assertEqual(client.taps, [])

    def test_aborts_when_lead_faints_mid_subtask(self):
        client = _FakeClient()

        def state_fn(c):
            if len(c.taps) >= 6:  # opener 完了後に気絶が観測される
                return make_gs(party0_hp=0)
            return make_gs(party0_hp=30)

        self.assertFalse(self._run(client, state_fn))

    def test_start_select_from_vlm_are_filtered(self):
        # VLM が Start を返し続けても実 tap には決して現れず、cap で give up。
        client = _FakeClient()
        ok = self._run(client, lambda c: make_gs(party0_hp=30),
                       vlm_button="Start")
        self.assertFalse(ok)
        self.assertNotIn("Start", client.taps)
        self.assertNotIn("Select", client.taps)


if __name__ == "__main__":
    unittest.main()
