"""音乐卡片的「电脑上正在放的歌」：只跟用户选的播放器、控制转发、封面与本地封面目录。"""

from __future__ import annotations

import pytest

from roundtable.core.nowplaying import (
    FakeMediaSource,
    NowPlaying,
    Playing,
    SmtcSource,
    cover_mime,
    list_covers,
    player_of,
    read_cover,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20


def _src() -> FakeMediaSource:
    return FakeMediaSource(
        [
            Playing(app_id="Chrome", title="私人视频", status="playing", cover=PNG),
            Playing(
                app_id="QQMusic.exe",
                title="夜航星",
                artist="不才",
                status="paused",
                position=10.0,
                duration=200.0,
                can=frozenset({"play", "pause", "next"}),
                cover=PNG,
            ),
        ]
    )


def test_only_the_chosen_player_is_reported():
    np = NowPlaying(_src())
    v = np.view("qqmusic")
    assert v["available"] and v["now"]["title"] == "夜航星"
    assert v["now"]["source"] == "QQ 音乐"
    assert v["now"]["cover"].startswith("/api/nowplaying/cover?player=qqmusic&v=")
    assert v["found"] == ["QQ 音乐"]
    assert "私人视频" not in str(v)  # 别的应用在放什么不报
    # 选「任意播放器」才跟系统当前那一路
    assert np.view("any")["now"]["title"] == "私人视频"
    # 不认识的键当 QQ 音乐
    assert np.view("bogus")["player"] == "qqmusic"
    # 没开 QQ 音乐
    assert NowPlaying(FakeMediaSource([])).view()["now"] is None


def test_controls_go_to_the_player_and_toggle_follows_status():
    src = _src()
    np = NowPlaying(src)
    assert np.control("qqmusic", "toggle")
    assert src.calls[-1] == ("QQMusic.exe", "play", None)
    assert np.view()["now"]["status"] == "playing"  # 控制后马上重读
    assert np.control("qqmusic", "seek", 42.0)
    assert src.calls[-1] == ("QQMusic.exe", "seek", 42.0)
    with pytest.raises(ValueError):
        np.control("qqmusic", "delete")
    assert not NowPlaying(FakeMediaSource([])).control("qqmusic", "play")


def test_unavailable_source_explains_why():
    np = NowPlaying(FakeMediaSource(reason="要在 Windows 上运行圆桌才能连 QQ 音乐"))
    v = np.view()
    assert not v["available"] and "Windows" in v["reason"]
    assert np.cover() is None
    assert not np.control("qqmusic", "play")


def test_smtc_source_degrades_without_pywinrt():
    # 测试环境没有 pywinrt：说明原因，不抛错
    assert SmtcSource().available()


def test_cover_types_and_player_names():
    assert cover_mime(PNG) == "image/png"
    assert cover_mime(b"\xff\xd8\xff") == "image/jpeg"
    assert cover_mime(b"<svg") is None
    assert player_of("C:\\Program Files\\Tencent\\QQMusic\\QQMusic.exe")[0] == "qqmusic"
    assert player_of("Microsoft.ZuneMusic") is None
    np = NowPlaying(_src())
    assert np.cover("qqmusic") == (PNG, "image/png")


def test_local_covers_stay_inside_their_folder(tmp_path):
    d = tmp_path / "music_covers"
    assert list_covers(d) == []
    d.mkdir()
    (d / "cover_claude.png").write_bytes(PNG)
    (d / "notes.txt").write_text("x")
    (tmp_path / "secret.png").write_bytes(PNG)
    assert list_covers(d) == [{"name": "claude", "url": "/api/music/covers/cover_claude.png"}]
    assert read_cover(d, "cover_claude.png") == (PNG, "image/png")
    for bad in ("../secret.png", "notes.txt", "..\\secret.png", ".hidden.png", ""):
        assert read_cover(d, bad) is None
