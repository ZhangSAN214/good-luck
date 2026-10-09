from __future__ import annotations

import pytest

from roundtable.core.config import load_config
from roundtable.core.media import MediaService
from roundtable.core.providers import ErrorKind, FakeProvider
from roundtable.core.runtime import Runtime


class Rig:
    """真实配置（含媒体模型）+ 假渠道：媒体模型都只走 openrouter 渠道。"""

    def __init__(self, **media_updates):
        config = load_config()
        policy = config.roundtable.request.model_copy(update={"backoff_s": 0, "failover_rounds": 1})
        rules = config.roundtable.media
        if media_updates:
            video = rules.video.model_copy(
                update={k: media_updates.pop(k) for k in list(media_updates) if k in VIDEO_KEYS}
            )
            rules = rules.model_copy(update={"video": video, **media_updates})
        rt_cfg = config.roundtable.model_copy(update={"request": policy, "media": rules})
        self.config = config.model_copy(update={"roundtable": rt_cfg})
        self.fake = FakeProvider("openrouter")
        self.rt = Runtime.build(
            config=self.config, providers={"openrouter": self.fake}, db_path=":memory:"
        )
        self.sid = self.rt.repo.create_session("画一只猫", seed=7, anonymous=False)
        self.now = 1000.0
        self.sleeps: list[float] = []
        self.events: list[tuple[str, dict]] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def service(self, **kw) -> MediaService:
        return MediaService(
            session_id=self.sid,
            config=self.config,
            router=self.rt.router,
            repo=self.rt.repo,
            store=self.rt.files,
            scrubber=self.rt.scrubber,
            seed=7,
            sleep=self.sleep,
            wall_clock=lambda: self.now,
            emit=lambda t, step, code, **d: self.events.append(
                (t, {"step": step, "code": code, **d})
            ),
            **kw,
        )

    def calls(self, kind: str):
        return [c for c in self.fake.media_calls if c[0] == kind]


VIDEO_KEYS = {"duration_s", "poll_interval_s", "timeout_s", "submit_retries", "frames", "max_mb"}


@pytest.fixture
def rig() -> Rig:
    return Rig()


__all__ = ["ErrorKind", "Rig"]
