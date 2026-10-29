"""自我档案：版本与修改记录、分区权限、锁定、撤回、清空。"""

from __future__ import annotations

import pytest

from roundtable.core.member.profile import LOCKED, ProfileError, ProfileStore

from ..orchestrator.conftest import Env


def store() -> ProfileStore:
    return ProfileStore(Env().rt)


def test_empty_profile_is_version_zero():
    s = store()
    p = s.current("V1")
    assert p.version == 0 and p.data == {} and s.history("V1") == []


def test_every_change_is_a_new_version_with_change_log():
    s = store()
    p1 = s.change("V1", {"likes": {"音乐": "后摇"}}, author="user", reason="初始")
    patch = {"likes": {"音乐": "爵士", "电影": "科幻"}}
    p2 = s.change("V1", patch, author="growth", reason="周反思")
    assert (p1.version, p2.version) == (1, 2)
    assert s.current("V1").get("likes.音乐") == "爵士"
    history = s.history("V1")
    assert [h["version"] for h in history] == [2, 1]
    assert history[0]["author"] == "growth" and history[0]["reason"] == "周反思"
    assert history[0]["changes"] == [
        {"path": "likes.电影", "old": None, "new": "科幻"},
        {"path": "likes.音乐", "old": "后摇", "new": "爵士"},
    ]
    # 旧版本原样保留
    assert s.version("V1", 1).data == {"likes": {"音乐": "后摇"}}
    # 没有变化时不加版本
    assert s.change("V1", {"likes": {"音乐": "爵士"}}, author="user").version == 2
    # 值为 None：删掉这一项；分区空了就去掉
    p = s.change("V1", {"likes": {"音乐": None, "电影": None}}, author="user")
    assert "likes" not in p.data and p.version == 3


def test_members_are_independent():
    s = store()
    s.change("V1", {"likes": {"音乐": "后摇"}}, author="user")
    assert s.current("V2").version == 0


def test_section_writers_are_enforced():
    s = store()
    with pytest.raises(ProfileError, match="不能改"):
        s.change("V1", {"identity": {"名字": "x"}}, author="growth")
    with pytest.raises(ProfileError, match="没有「乱写」"):
        s.change("V1", {"乱写": {"a": 1}}, author="user")
    with pytest.raises(ProfileError, match="不能改"):
        s.change("V1", {"likes": {"a": 1}}, author="陌生模块")
    assert s.current("V1").version == 0
    # 用户什么分区都能改；成长机制能改性格
    s.change("V1", {"identity": {"名字": "x"}}, author="user")
    s.change("V1", {"personality": {"外向": 3}}, author="growth")


def test_locked_items_only_user_can_change():
    s = store()
    s.change("V1", {"personality": {"外向": 3}}, author="user")
    s.lock("V1", "personality.外向")
    assert s.current("V1").locked == ["personality.外向"]
    with pytest.raises(ProfileError, match="已被你锁定"):
        s.change("V1", {"personality": {"外向": 4}}, author="growth")
    s.change("V1", {"personality": {"好奇": 5}}, author="growth")  # 别的项照常
    s.change("V1", {"personality": {"外向": 4}}, author="user")
    with pytest.raises(ProfileError, match="锁定用"):
        s.change("V1", {LOCKED: ["x"]}, author="user")
    with pytest.raises(ProfileError, match="不是档案里的一项"):
        s.lock("V1", "外向")
    s.lock("V1", "personality.外向", locked=False)
    assert s.current("V1").locked == []
    s.change("V1", {"personality": {"外向": 2}}, author="growth")


def test_revert_saves_old_content_as_new_version():
    s = store()
    s.change("V1", {"style": {"语气": "平"}}, author="user")
    s.change("V1", {"style": {"语气": "活泼", "口头禅": "好家伙"}}, author="growth")
    p = s.revert("V1", 1)
    assert p.version == 3 and p.data == {"style": {"语气": "平"}}
    assert s.history("V1")[0]["reason"] == "撤回到第 1 版"
    assert [h["version"] for h in s.history("V1")] == [3, 2, 1]
    with pytest.raises(ProfileError, match="没有第 9 版"):
        s.revert("V1", 9)
    # 成长机制撤回时同样受权限约束
    s.lock("V1", "style.语气")
    with pytest.raises(ProfileError):
        s.revert("V1", 2, author="growth")


def test_clear_removes_everything():
    s = store()
    s.change("V1", {"likes": {"a": 1}}, author="user")
    s.change("V1", {"likes": {"a": 2}}, author="user")
    s.change("V2", {"likes": {"a": 1}}, author="user")
    assert s.clear("V1") == 2
    assert s.current("V1").version == 0 and s.history("V1") == []
    assert s.current("V2").version == 1
