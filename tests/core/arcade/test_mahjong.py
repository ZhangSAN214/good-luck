import random

import pytest

from roundtable.core.arcade import mj_bot
from roundtable.core.arcade.mahjong import TILES, Mahjong, is_win, shanten, tile_zh, waits
from roundtable.core.arcade.shedding import Hand, ShedError


def H(s: str) -> list[str]:
    return s.split()


def table(seed: int = 1) -> Mahjong:
    return Mahjong(
        seats=[Hand(id=f"s{k}", name=f"阿{k}") for k in range(4)], rng=random.Random(seed)
    )


def test_win_shapes_and_shanten():
    assert is_win(H("1m 2m 3m 4m 5m 6m 7m 8m 9m 1z 1z 1z 2p 2p"))
    assert is_win(H("1m 1m 2m 2m 3s 3s 4p 4p 5z 5z 6z 6z 7z 7z"))  # 七对
    assert not is_win(H("1m 2m 3m 4m 5m 6m 7m 8m 9m 1z 1z 1z 2p 3p"))
    assert shanten(H("1m 2m 3m 4m 5m 6m 7m 8m 9m 1z 1z 1z 2p")) == 0
    assert shanten(H("1m 2m 3m 4m 5m 6m 7m 8m 9m 1z 1z 1z 2p 2p")) == -1
    assert shanten(H("1m 2m 3m"), melds=3) == 0
    assert waits(H("1m 1m 1m 2m 3m 4m 5m 6m 7m 8m 9m 9m 9m")) == [f"{n}m" for n in range(1, 10)]
    assert tile_zh("5p") == "五筒" and tile_zh("5z") == "中"


def test_shanten_matches_one_step_search():
    for k in range(200):
        h = random.Random(k).sample(TILES * 4, 13)
        s = shanten(h)
        assert (s == 0) == bool(waits(h))
        if s == 1:
            best = min(shanten([*h, t][:i] + [*h, t][i + 1 :]) for t in TILES for i in range(14))
            assert best == 0


def test_bots_play_full_rounds_tiles_and_points_add_up():
    g = table(7)
    for _ in range(4):
        g.start()
        assert sum(len(s.cards) for s in g.seats) == 53  # 庄家多摸一张
        assert len(g.seats[g.dealer].cards) == 14 and g.seats[g.dealer].role == "庄家"
        while not g.done:
            i = g.turn
            g.act(i, mj_bot.decide(g, i, g.rng))
        tiles = (
            sum(len(s.cards) for s in g.seats)
            + sum(len(m["tiles"]) for ms in g.melds for m in ms)
            + sum(len(r) for r in g.rivers)
            + len(g.wall)
        )
        assert tiles == 136
        assert sum(r["points"] for r in g.results) == 0
        assert g.summary is not None
        if g.summary["winner"]:
            assert g.summary["fan"] >= 1 and g.winners()


def test_discard_claim_pong_and_ron():
    g = table()
    g.start()
    d = g.dealer
    nxt, opp = (d + 1) % 4, (d + 2) % 4
    g.seats[d].cards = H("1m 2m 3m 4m 5m 6m 7m 8m 9m 1s 2s 3s 5z 6z")
    g.seats[nxt].cards = H("1m 2m 3m 4m 5m 6m 7m 8m 9m 1p 2p 3p 5z")  # 单吊中
    g.seats[opp].cards = H("5z 5z 2s 3s 4s 6p 7p 8p 1z 2z 3z 4z 9p")
    g.seats[(d + 3) % 4].cards = H("1p 1p 2p 2p 3p 3p 4p 4p 5p 5p 6p 6p 9s")
    g.drawn = "6z"
    with pytest.raises(ShedError):
        g.act(nxt, {"kind": "play", "cards": ["1m"]})  # 没轮到
    g.act(d, {"kind": "play", "cards": ["5z"]})
    assert g.phase == "claim" and g.turn == nxt  # 先问能胡的
    kinds = [o["kind"] for o in g.legal(nxt)]
    assert kinds == ["hu", "pass"]
    assert "胡了" in g.note_for(nxt, g.legal(nxt)[0])
    g.act(nxt, {"kind": "pass"})
    assert g.turn == opp and [o["kind"] for o in g.legal(opp)] == ["pong", "pass"]
    g.act(opp, {"kind": "pong", "cards": ["5z", "5z"]})
    assert g.melds[opp][0]["kind"] == "pong" and g.turn == opp
    assert all(o["kind"] == "play" for o in g.legal(opp))  # 碰完只能打
    assert "5z" not in g.rivers[d]
    g.act(opp, {"kind": "play", "cards": ["1z"]})
    assert g.turn == (opp + 1) % 4 and g.phase == "play"


def test_ron_pays_double_and_dealer_moves():
    g = table()
    g.start()
    d = g.dealer
    nxt = (d + 1) % 4
    g.seats[d].cards = H("1m 2m 3m 4m 5m 6m 7m 8m 9m 1s 2s 3s 5z 6z")
    g.seats[nxt].cards = H("1m 2m 3m 4m 5m 6m 7m 8m 9m 1p 2p 3p 5z")
    g.act(d, {"kind": "play", "cards": ["5z"]})
    g.act(nxt, {"kind": "hu", "cards": ["5z"]})
    assert g.done and g.summary["winner"] == g.seats[nxt].name and not g.summary["self_drawn"]
    pts = {r["seat"]: r["points"] for r in g.results}
    fan = g.summary["fan"]
    assert pts[g.seats[nxt].id] == 2 * fan and pts[g.seats[d].id] == -2 * fan
    assert "点炮" in next(r["role"] for r in g.results if r["seat"] == g.seats[d].id)
    g.start()
    assert g.dealer == nxt  # 闲家胡了，庄家换下一位


def test_self_drawn_concealed_kong_and_fans():
    g = table()
    g.start()
    d = g.dealer
    g.seats[d].cards = H("1m 1m 1m 1m 2m 3m 4m 5m 6m 7m 8m 9m 9m 2m")
    opts = g.legal(d)
    assert any(o["label"] == "暗杠" for o in opts)
    g.act(d, {"kind": "kong", "cards": ["1m"] * 4})
    assert g.melds[d][0]["kind"] == "ankong" and g.kong_draw
    g.seats[d].cards = H("2m 3m 4m 5m 6m 7m 7m 8m 9m 9m 9m")  # 补的牌正好胡（手动摆）
    assert any(o["kind"] == "hu" for o in g.legal(d))
    fans = dict(g.fans(d, self_drawn=True))
    assert fans["清一色"] == 2 and fans["自摸"] == 1 and fans["杠上开花"] == 1
    g.act(d, {"kind": "hu", "cards": []})
    pts = {r["seat"]: r["points"] for r in g.results}
    fan = g.summary["fan"]
    # 自摸三家各付番数，暗杠三家各付 2
    assert pts[g.seats[d].id] == 3 * fan + 6
    assert all(v == -fan - 2 for k, v in pts.items() if k != g.seats[d].id)


def test_view_hides_others_and_tracker_notes():
    g = table(3)
    g.start()
    d = g.dealer
    v = g.view(g.seats[d].id)
    assert v["seats"][d]["cards"] and all(
        s["cards"] == [] for k, s in enumerate(v["seats"]) if k != d
    )
    assert v["options"] and v["wall"] == 136 - 53
    assert len(g.view(None, reveal_all=True)["seats"][(d + 1) % 4]["cards"]) == 13
    o = next(o for o in g.legal(d) if o["kind"] == "play")
    assert "听牌" in g.note_for(d, o)
    assert "牌墙还剩" in g.tracker(d)
    b = g.brief(d)
    assert b["seat"] == "庄家" and "刚摸到" in b["last"]
