"""游戏模式（core/gamemode.py）：忙的判断、连续空闲才放行、自己的显卡活不算、排队补跑、CPU 优先级、
路由对 gpu 渠道让路。全部用假的电脑状态。"""

from __future__ import annotations

import json

import pytest

from roundtable.core.config.schema import GameModeRules, RequestPolicy
from roundtable.core.gamemode import (
    MANUAL_KEY,
    FakeGpu,
    FakeProcesses,
    GameMode,
    StillBusy,
)
from roundtable.core.media.draw_order import resting
from roundtable.core.providers import AllChannelsFailed, ChannelRouter, FakeProvider, Message

from .providers.conftest import models_config

STEAM_GAME = r"D:\SteamLibrary\steamapps\common\Elden Ring\Game\eldenring.exe"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make(rules: GameModeRules | None = None, **kw):
    clock = Clock()
    settings: dict[str, str] = {}
    gpu = kw.pop("gpu", FakeGpu(0.0))
    procs = kw.pop("procs", FakeProcesses())
    priority: list[bool] = []
    gm = GameMode(
        rules or GameModeRules(),
        gpu=gpu,
        procs=procs,
        load=settings.get,
        save=settings.__setitem__,
        clock=clock,
        wall=lambda: 1_800_000_000.0,
        set_priority=priority.append,
        **kw,
    )
    return gm, clock, gpu, procs, settings, priority


def test_idle_computer_is_free_right_away():
    gm, *_ = make()
    assert gm.blocked() is None
    assert gm.view()["gpu_free"] is True


def test_a_steam_game_running_blocks_even_in_the_background():
    gm, _, _, procs, *_ = make()
    procs.running = [r"C:\Windows\explorer.exe", STEAM_GAME]
    assert gm.blocked() == "游戏模式：在玩：Elden Ring"


def test_game_table_from_about_you_and_launchers_are_not_games():
    gm, _, _, procs, *_ = make()
    gm.set_games(lambda: {"Genshin.exe": "原神"})
    procs.running = [r"C:\Program Files\Epic Games\Launcher\Portal\Binaries\Win64\x.exe"]
    assert gm.poll().busy is False  # Epic 启动器不算游戏
    procs.running.append(r"E:\Games\Genshin.exe")
    assert gm.poll().reasons == ["在玩：原神"]


def test_fullscreen_and_gpu_load_and_other_programs_vram():
    gm, _, gpu, procs, *_ = make()
    procs.full = True
    assert "前台是全屏程序" in gm.poll().reasons
    procs.full = False
    gpu.reading = FakeGpu(55.0).reading
    assert gm.poll().reasons == ["显卡占用 55%"]
    gpu.reading = FakeGpu(5.0, others_mb=3000).reading
    assert gm.poll().reasons == ["别的程序占着 3000 MB 显存"]
    gpu.reading = FakeGpu(5.0, others_mb=None).reading  # 驱动不给每个程序的显存：不看这项
    assert gm.poll().busy is False


def test_detectors_can_be_switched_off():
    rules = GameModeRules(detect_games=False, detect_fullscreen=False, busy_util_pct=0)
    gm, _, gpu, procs, *_ = make(rules)
    procs.running, procs.full = [STEAM_GAME], True
    gpu.reading = FakeGpu(99.0).reading
    assert gm.poll().busy is False


def test_auto_off_only_listens_to_the_manual_switch():
    gm, _, _, procs, settings, _ = make(GameModeRules(auto=False))
    procs.running = [STEAM_GAME]
    assert gm.blocked() is None
    view = gm.set_manual(True)
    assert settings[MANUAL_KEY] == "1"
    assert view["manual"] and view["reasons"] == ["你手动开了游戏模式"]
    assert gm.blocked() == "游戏模式：你手动开了游戏模式"


def test_needs_five_quiet_minutes_after_a_game_before_using_the_gpu_again():
    gm, clock, _, procs, *_ = make()
    procs.running = [STEAM_GAME]
    gm.poll()
    procs.running = []
    clock.now += 10
    gm.poll()
    assert gm.blocked() == "游戏模式：显卡刚空下来，再等约 5 分钟"
    clock.now += 4 * 60
    assert "再等约 1 分钟" in (gm.blocked() or "")
    clock.now += 61
    assert gm.blocked() is None


def test_own_gpu_jobs_and_the_grace_after_them_do_not_count_as_busy():
    gm, clock, gpu, *_ = make()
    gpu.reading = FakeGpu(95.0).reading  # 本地出图把显卡跑满
    with gm.hold():
        assert gm.poll().busy is False
    clock.now += 30
    assert gm.poll().busy is False  # 刚做完，余量时间内
    clock.now += 31
    assert gm.poll().busy is True  # 余量过了还这么高：是别人在用


def test_status_is_cached_between_polls():
    gm, clock, gpu, *_ = make()
    assert gm.blocked() is None
    gpu.reading = FakeGpu(90.0).reading
    assert gm.blocked() is None  # 还没到下一次看的时间
    clock.now += gm.rules.poll_seconds
    assert gm.blocked() is not None


def test_cpu_priority_is_lowered_while_busy_and_restored_after():
    gm, clock, _, procs, _, priority = make()
    procs.running = [STEAM_GAME]
    gm.poll()
    assert gm.apply_priority() is True
    procs.running = []
    clock.now += 10
    gm.poll()
    assert gm.apply_priority() is False
    assert priority == [True, False]
    gm2, _, _, procs2, _, priority2 = make(GameModeRules(cpu_when_busy="normal"))
    procs2.running = [STEAM_GAME]
    gm2.poll()
    assert gm2.apply_priority() is False and priority2 == []


def test_pause_mode_pauses_background_loops_only_while_busy():
    gm, _, _, procs, *_ = make(GameModeRules(cpu_when_busy="pause"))
    assert gm.background_paused() is False
    procs.running = [STEAM_GAME]
    gm.poll()
    assert gm.background_paused() is True
    low, _, _, procs3, *_ = make()
    procs3.running = [STEAM_GAME]
    low.poll()
    assert low.background_paused() is False  # 默认只降优先级，不停


async def test_queue_waits_for_a_free_gpu_then_runs_one_job_at_a_time(tmp_path):
    file = tmp_path / "gpu_queue.json"
    gm, clock, _, procs, *_ = make(queue_file=file)
    ran: list[int] = []

    async def handler(payload):
        ran.append(payload["n"])

    gm.register("photo", handler)
    procs.running = [STEAM_GAME]
    gm.poll()
    gm.defer("photo", "配图一", {"n": 1})
    gm.defer("photo", "配图二", {"n": 2})
    assert await gm.drain_one() is False and ran == []
    saved = json.loads(file.read_text(encoding="utf-8"))
    assert [j["label"] for j in saved] == ["配图一", "配图二"]
    # 重启后排队还在
    again, *_ = make(queue_file=file)
    assert [j.label for j in again.queue()] == ["配图一", "配图二"]

    procs.running = []
    clock.now += 10
    gm.poll()
    assert await gm.drain_one() is False  # 还没连续空闲 5 分钟
    clock.now += 5 * 60
    assert await gm.drain_one() is True and ran == [1]
    assert await gm.drain_one() is True and ran == [1, 2]
    assert await gm.drain_one() is False
    assert json.loads(file.read_text(encoding="utf-8")) == []


async def test_still_busy_goes_back_to_the_front_and_errors_are_dropped():
    gm, *_ = make()
    calls: list[str] = []

    async def busy(payload):
        calls.append("busy")
        raise StillBusy

    async def broken(payload):
        calls.append("broken")
        raise RuntimeError("坏了")

    gm.register("a", busy)
    gm.defer("a", "甲", {})
    assert await gm.drain_one() is False
    assert [j.label for j in gm.queue()] == ["甲"]
    gm.register("a", broken)
    assert await gm.drain_one() is True
    assert gm.queue() == [] and calls == ["busy", "broken"]


async def test_jobs_without_a_handler_wait_and_the_queue_is_capped():
    gm, *_ = make(GameModeRules(queue_max=2))
    for n in range(3):
        gm.defer("unknown", f"第{n}件", {})
    assert [j.label for j in gm.queue()] == ["第1件", "第2件"]
    assert await gm.drain_one() is False


def test_broken_queue_file_starts_empty(tmp_path):
    file = tmp_path / "gpu_queue.json"
    file.write_text("{不是 JSON", encoding="utf-8")
    gm, *_ = make(queue_file=file)
    assert gm.queue() == []


# --- 路由 ------------------------------------------------------------------------------


def gpu_router():
    cfg = models_config({"pc": "local", "cloud": "aggregator"}, {"draw": ["pc", "cloud"]})
    data = cfg.model_dump()
    data["channels"]["pc"]["gpu"] = True
    data["models"].append(
        {**data["models"][0], "id": "pcdraw", "routes": [{"channel": "pc", "model": "x"}]}
    )
    cfg = type(cfg).model_validate(data)
    providers = {"pc": FakeProvider("pc"), "cloud": FakeProvider("cloud")}
    router = ChannelRouter(cfg, providers, policy=RequestPolicy(failover_rounds=2, backoff_s=0))
    return router, providers


async def test_router_skips_gpu_channels_while_gaming_and_uses_the_next_one():
    router, providers = gpu_router()
    gm, clock, gpu, procs, *_ = make()
    router.set_gpu_guard(gm)
    procs.running = [STEAM_GAME]
    c = await router.complete("draw", [Message("user", "hi")])
    assert c.channel == "cloud" and providers["pc"].calls == []
    assert router.gpu_blocked("pc") == "游戏模式：在玩：Elden Ring"
    assert router.gpu_blocked("cloud") is None
    assert router.cooling_s("pc") == 0  # 不算出错，不冷却
    skip = router.last_skip("pc")
    assert skip is not None and "游戏模式" in skip.reason
    with pytest.raises(AllChannelsFailed):
        await router.complete("pcdraw", [Message("user", "hi")])
    assert router.model_gpu_blocked("pcdraw") and not router.model_gpu_blocked("draw")
    assert resting(router, router.plan("pcdraw").model)


async def test_router_holds_the_gpu_while_calling_a_gpu_channel():
    router, providers = gpu_router()
    gm, clock, gpu, *_ = make()
    router.set_gpu_guard(gm)
    seen: list[int] = []

    async def call(provider, route, model):
        seen.append(gm._own_active)
        return "ok"

    await router.invoke("draw", call)
    assert seen == [1] and gm._own_active == 0
    gpu.reading = FakeGpu(99.0).reading
    clock.now += gm.rules.poll_seconds
    assert gm.poll().busy is False  # 刚画完的余量时间里，满占用是自己的


def test_repo_config_marks_the_local_gpu_channels():
    from roundtable.core.config import load_config

    channels = load_config().models.channels
    assert channels["comfyui"].gpu and channels["local_tts"].gpu
    assert not any(spec.gpu for spec in channels.values() if spec.kind != "local")
