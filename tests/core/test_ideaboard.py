"""点子榜：投票排名、黑马、Markdown。"""

from __future__ import annotations

from roundtable.core.ideaboard import (
    Ballot,
    Board,
    Candidate,
    CompareRow,
    Pick,
    Plan,
    pick_dark_horse,
    rank,
    to_markdown,
)

CANDS = [
    Candidate("I1", "a", "甲的点子"),
    Candidate("I2", "b", "乙的点子"),
    Candidate("I3", "c", "丙的点子"),
    Candidate("E1", "a", "杂交点子", "hybrid", ("I2", "I3")),
]


def label(code: str) -> str:
    return f"「{code}」"


def test_rank_sums_scores_ignores_self_votes_and_breaks_ties_by_votes_then_order():
    ballots = {
        "a": Ballot((Pick("I1", 5, 5, 5), Pick("I2", 3, 3, 3), Pick("I3", 2, 2, 2))),  # I1 是自己的
        "b": Ballot((Pick("I3", 1, 1, 1), Pick("E1", 3, 3, 3))),
        "c": Ballot((Pick("I2", 1, 1, 1),), dark_horse="I1"),
    }
    got = rank(CANDS, ballots, label)
    assert [(r.id, r.score, r.votes) for r in got] == [
        ("I2", 12, 2),
        ("I3", 9, 2),  # 与 E1 同分，票数多的在前
        ("E1", 9, 1),
        ("I1", 0, 0),  # 只有黑马票
    ]
    assert got[2].authors == ("「a」",) and got[2].refs == ("I2", "I3")
    assert got[3].dark_votes == 1


def test_rank_is_independent_of_ballot_insertion_order():
    a = {"b": Ballot((Pick("I1", 3, 3, 3),)), "c": Ballot((Pick("I2", 3, 3, 3),))}
    b = dict(reversed(list(a.items())))
    assert rank(CANDS, a, label) == rank(CANDS, b, label)


def test_dark_horse_comes_from_outside_the_top_and_needs_a_vote():
    ballots = {
        "a": Ballot((Pick("I2", 5, 5, 5),), dark_horse="I3"),
        "b": Ballot((Pick("I3", 1, 1, 1),), dark_horse="I3"),
        "c": Ballot((Pick("I1", 2, 2, 2),), dark_horse="I2"),
    }
    ranking = rank(CANDS, ballots, label)
    assert pick_dark_horse(ranking, 1).id == "I3"
    assert pick_dark_horse(ranking, 5) is None
    assert pick_dark_horse(rank(CANDS, {"b": Ballot((Pick("I1", 3, 3, 3),))}, label), 0) is None


def test_board_round_trips_and_renders_markdown_sections():
    ranking = tuple(rank(CANDS, {"b": Ballot((Pick("I1", 4, 3, 5), Pick("E1", 2, 2, 2)))}, label))
    board = Board(
        ranking,
        top=2,
        compare=(CompareRow("I1", "新|奇", "易", "低", "小"),),
        stories=(("I1", "某天早上……"),),
        plan=Plan("I1", "一周小试", ("一", "二")),
        dark_horse="E1",
        dark_horse_why="够大胆",
        lenses={"「a」": "反着来"},
    )
    assert Board.from_dict(board.to_dict()) == board
    md = to_markdown(board)
    for part in (
        "点子榜",
        "前几名对比",
        "新／奇",
        "场景故事",
        "行动方案",
        "最小试验",
        "黑马",
        "够大胆",
    ):
        assert part in md, part
    assert "由 I2 × I3 而来" in md
    assert to_markdown(Board()) == ""


def test_rank_keeps_unvoted_ideas_last_with_average_scores():
    cands = [*CANDS, Candidate("I4", "b", "没人投的点子")]
    ballots = {
        "a": Ballot((Pick("I2", 4, 2, 5),)),
        "c": Ballot((Pick("I2", 2, 4, 3),)),
    }
    got = rank(cands, ballots, label)
    assert [r.id for r in got] == ["I2", "I1", "I3", "E1", "I4"]  # 没票的按编号顺序
    top = got[0]
    assert (top.novelty, top.feasibility, top.value) == (3.0, 3.0, 4.0) and top.voted
    assert not got[-1].voted and got[-1].feasibility == 0.0
    md = to_markdown(Board(tuple(got)))
    assert "I2" in md and "没人投的点子" not in md  # 点子榜只列得票的
    # 没人投的点子不会被当成前几名或黑马
    assert pick_dark_horse(got, 1) is None
