"""ChatGPT 网页出图适配器：假浏览器驱动，不联网、不开浏览器。"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from roundtable.core.config import load_config
from roundtable.core.config.loader import DEFAULT_CONFIG_DIR, NO_LOCAL_ENV
from roundtable.core.providers import (
    ErrorKind,
    Media,
    Message,
    ProviderError,
    UnsupportedCapability,
    adapter_names,
)
from roundtable.core.providers.chatgpt_web import (
    LOGIN_MARK,
    MODEL_ID,
    ChatGPTWebProvider,
    options,
    switch_on,
)
from roundtable.core.providers.fake import FAKE_PNG

from .conftest import channel

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 20


def spec():
    return channel("chatgpt_web", kind="local", base_url="https://chatgpt.test", key_env=None)


def make(tmp_path: Path, driver, *, login: bool = True) -> ChatGPTWebProvider:
    profile = tmp_path / "data" / "chatgpt_web_profile"
    profile.mkdir(parents=True)
    if login:
        (profile / LOGIN_MARK).write_text("ok", encoding="utf-8")
    return ChatGPTWebProvider("chatgpt_web", spec(), None, 300, driver=driver, root=tmp_path)


async def test_draws_with_refs_and_costs_nothing(tmp_path):
    seen = {}

    async def driver(prompt, refs, opts):
        seen.update(prompt=prompt, refs=refs, opts=opts)
        return JPEG

    prov = make(tmp_path, driver)
    refs = [Media("image", "image/png", FAKE_PNG), Media("audio", "audio/mpeg", b"x")]
    out = await prov.generate_image("chatgpt-web", "two friends laughing", {}, images=refs)
    assert out.cost_usd == 0.0
    [img] = out.images
    assert img.kind == "image" and img.mime == "image/jpeg" and img.data == JPEG
    assert seen["prompt"] == "two friends laughing"
    assert [m.kind for m in seen["refs"]] == ["image"]  # 只传图片
    assert seen["opts"]["url"] == "https://chatgpt.test"
    assert seen["opts"]["profile"] == tmp_path / "data" / "chatgpt_web_profile"


async def test_ref_count_is_capped(tmp_path):
    got = []

    async def driver(prompt, refs, opts):
        got.extend(refs)
        return FAKE_PNG

    prov = make(tmp_path, driver)
    refs = [Media("image", "image/png", FAKE_PNG)] * 7
    out = await prov.generate_image("m", "p", {"max_refs": 3}, images=refs)
    assert len(got) == 3 and out.images[0].mime == "image/png"


async def test_not_logged_in_fails_fast_without_opening_a_browser(tmp_path):
    called = []

    async def driver(prompt, refs, opts):
        called.append(1)
        return FAKE_PNG

    prov = make(tmp_path, driver, login=False)
    with pytest.raises(ProviderError) as err:
        await prov.generate_image("m", "p", {})
    assert err.value.kind is ErrorKind.AUTH and "chatgpt-login" in str(err.value)
    assert not called


async def test_one_drawing_at_a_time_the_second_goes_to_the_next_model(tmp_path):
    gate = asyncio.Event()

    async def driver(prompt, refs, opts):
        await gate.wait()
        return FAKE_PNG

    prov = make(tmp_path, driver)
    first = asyncio.create_task(prov.generate_image("m", "a", {}))
    await asyncio.sleep(0)
    with pytest.raises(ProviderError) as err:
        await prov.generate_image("m", "b", {})
    assert err.value.kind is ErrorKind.RATE_LIMIT
    gate.set()
    assert (await first).images


async def test_empty_result_and_driver_errors(tmp_path):
    async def empty(prompt, refs, opts):
        return b""

    with pytest.raises(ProviderError) as err:
        await make(tmp_path, empty).generate_image("m", "p", {})
    assert err.value.kind is ErrorKind.INVALID_RESPONSE

    async def limited(prompt, refs, opts):
        raise ProviderError(ErrorKind.RATE_LIMIT, "chatgpt_web", "次数用完")

    other = tmp_path / "other"
    with pytest.raises(ProviderError) as err:
        await make(other, limited).generate_image("m", "p", {})
    assert err.value.kind is ErrorKind.RATE_LIMIT


async def test_text_chat_is_unsupported(tmp_path):
    prov = make(tmp_path, None)
    with pytest.raises(UnsupportedCapability):
        await prov.complete("m", [Message("user", "hi")], {})


def test_options_merge_selectors_and_ignore_unknown_params():
    opts = options({"selectors": {"send": "#go"}, "timeout_s": 60, "modalities": ["image"]})
    assert opts["selectors"]["send"] == "#go"
    assert opts["selectors"]["prompt"] == "#prompt-textarea"  # 其余保留默认
    assert opts["timeout_s"] == 60 and "modalities" not in opts
    assert opts["prefix"].strip()


def test_switch_on_keeps_other_settings(tmp_path):
    local = tmp_path / "models.local.yaml"
    switch_on(local)
    assert local.read_text(encoding="utf-8") == f"model_switches:\n  {MODEL_ID}: true\n"

    local.write_text(
        "model_params:\n  x: {a: 1}\nmodel_switches:\n  comfyui-local: false\n", encoding="utf-8"
    )
    switch_on(local)
    switch_on(local)  # 重复运行不加重复行
    text = local.read_text(encoding="utf-8")
    assert text.count(MODEL_ID) == 1 and "comfyui-local: false" in text and "a: 1" in text
    switch_on(local, value=False)
    assert f"{MODEL_ID}: false" in local.read_text(encoding="utf-8")

    local.write_text("model_switches: {}\n", encoding="utf-8")
    switch_on(local)
    assert local.read_text(encoding="utf-8") == f"model_switches:\n  {MODEL_ID}: true\n"

    local.write_text("model_switches: {pc-tts: true}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        switch_on(local)


def test_switch_on_is_picked_up_by_the_config(tmp_path, monkeypatch):
    monkeypatch.delenv(NO_LOCAL_ENV, raising=False)  # 测试默认不读本机设置，这里要读
    folder = tmp_path / "config"
    shutil.copytree(DEFAULT_CONFIG_DIR, folder, ignore=shutil.ignore_patterns("*.local.yaml"))
    switch_on(folder / "models.local.yaml")
    [model] = [m for m in load_config(folder).models.models if m.id == MODEL_ID]
    assert model.enabled


def test_repo_config_wires_the_channel_first_in_draw_order_and_free():
    assert "chatgpt_web" in adapter_names()
    models = load_config().models
    spec_ = models.channels["chatgpt_web"]
    assert spec_.adapter == "chatgpt_web" and spec_.kind == "local" and spec_.key_env is None
    [model] = [m for m in models.models if m.id == MODEL_ID]
    assert not model.enabled and not model.seat  # 登录后才打开，从不上桌
    assert model.price.input == model.price.output == 0
    assert model.media_price is not None and model.media_price.usd == 0
    assert {"image_gen", "image_edit", "feed_photo"} <= set(model.tags)
    assert all(r.channel == "chatgpt_web" for r in model.routes)  # 不会悄悄走付费渠道


# --- 真浏览器走一遍（本机假网页，不联网；没装 playwright 或浏览器时跳过）-------------------

PAGE = """<!doctype html><meta charset="utf-8"><title>fake chat</title>
<div id="prompt-textarea" contenteditable="true"></div>
<input type="file" multiple id="f">
<button data-testid="send-button" id="send">send</button>
<div id="out"></div>
<script>
window.sent = null;
document.getElementById('send').onclick = () => {
  window.sent = {
    text: document.getElementById('prompt-textarea').innerText,
    files: document.getElementById('f').files.length,
  };
  const stop = document.createElement('button');
  stop.dataset.testid = 'stop-button';
  document.body.appendChild(stop);
  setTimeout(() => {
    const img = document.createElement('img');
    img.alt = 'Generated image: a photo';
    const ok = window.sent.files === 2 && window.sent.text.includes('kitchen')
      && window.sent.text.startsWith('请直接生成');
    img.src = location.origin + (ok ? '/out.png' : '/wrong.png');
    document.getElementById('out').appendChild(img);
    stop.remove();
  }, 300);
};
</script>"""


@pytest.fixture
def fake_site():
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path == "/wrong.png":  # 网页没收到两张参考图或提示词不对
                self.send_error(404)
                return
            body, mime = (
                (FAKE_PNG, "image/png")
                if self.path == "/out.png"
                else (
                    PAGE.encode(),
                    "text/html; charset=utf-8",
                )
            )
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()


async def test_real_browser_flow_against_a_fake_page(tmp_path, fake_site):
    pytest.importorskip("playwright")
    prov = ChatGPTWebProvider(
        "chatgpt_web",
        channel("chatgpt_web", kind="local", base_url=fake_site, key_env=None),
        None,
        30,
        root=tmp_path,
    )
    profile = tmp_path / "data" / "chatgpt_web_profile"
    profile.mkdir(parents=True)
    (profile / LOGIN_MARK).write_text("ok", encoding="utf-8")
    params = {"headless": True, "timeout_s": 20, "poll_interval_s": 0.2, "settle_s": 0.1}
    # 与 playwright 版本不匹配的已装 Chromium（同端到端测试的 ROUNDTABLE_CHROMIUM）
    params["executable"] = os.environ.get("ROUNDTABLE_CHROMIUM", "")
    refs = [Media("image", "image/png", FAKE_PNG), Media("image", "image/jpeg", JPEG)]
    try:
        out = await prov.generate_image("chatgpt-web", "a kitchen at night", params, images=refs)
    except ProviderError as exc:
        missing = ("Executable doesn't exist", "is not found at")
        if exc.kind is ErrorKind.NETWORK and any(m in str(exc) for m in missing):
            pytest.skip("没装与 playwright 版本匹配的 Chromium（CI 上会装）")
        raise
    [img] = out.images
    assert img.data == FAKE_PNG and out.cost_usd == 0.0
