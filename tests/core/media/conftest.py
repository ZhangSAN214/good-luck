from __future__ import annotations

import pytest

from roundtable.core.config import load_config
from roundtable.core.media import MediaService
from roundtable.core.providers import ErrorKind, FakeProvider
from roundtable.core.runtime import Runtime


# 下面的用例按这三个画图模型写（一个走对话接口、两个走 /images 接口，分在两档）；
# 配置里其余画图模型默认去掉，让断言不随上桌名单变化。整份名单的检查见 test_repo_image_models.py
CORE_IMAGE_MODELS = {"gpt-image-1-mini", "gpt-image-2", "gemini-3.1-flash-image"}


def core_images_only(config):
    models = [
        m for m in config.models.models if "image_gen" not in m.tags or m.id in CORE_IMAGE_MODELS
    ]
    return config.model_copy(update={"models": config.models.model_copy(update={"models": models})})


class Rig:
    """真实配置（含媒体模型）+ 假渠道：媒体模型都只走 openrouter 渠道。"""

    def __init__(self, no_image_edit=False, all_images=False, **media_updates):
        config = load_config()
        if not all_images:
            config = core_images_only(config)
        if no_image_edit:  # 所有画图模型都不支持参考图
            models = [
                m.model_copy(update={"tags": [t for t in m.tags if t != "image_edit"]})
                for m in config.models.models
            ]
            config = config.model_copy(
                update={"models": config.models.model_copy(update={"models": models})}
            )
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
        kw.setdefault("scrubber", self.rt.scrubber)
        return MediaService(
            session_id=self.sid,
            config=self.config,
            router=self.rt.router,
            repo=self.rt.repo,
            store=self.rt.files,
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
