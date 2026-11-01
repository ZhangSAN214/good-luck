"""本地 ComfyUI 适配器：假 HTTP（httpx.MockTransport），不联网、不需要 ComfyUI。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from roundtable.core.config import load_config
from roundtable.core.providers import (
    ErrorKind,
    Media,
    Message,
    ProviderError,
    Secret,
    UnsupportedCapability,
    adapter_names,
)
from roundtable.core.providers.comfyui import (
    DEFAULT_WORKFLOW_DIR,
    ComfyUIProvider,
    fill,
    ref_slots,
    save_nodes,
    split_prompt,
)
from roundtable.core.providers.fake import FAKE_PNG

from .conftest import channel

WORKFLOW = {
    "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{prompt}}"}},
    "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "style, {{negative}}"}},
    "3": {
        "class_type": "KSampler",
        "inputs": {"seed": "{{seed}}", "width": "{{width}}", "height": "{{ height }}"},
    },
    "4": {"class_type": "LoadImage", "inputs": {"image": "{{ref_1}}"}},
    "5": {"class_type": "LoadImage", "inputs": {"image": "{{ref_2}}"}},
    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "rt"}},
}
NOREF = {k: v for k, v in WORKFLOW.items() if k not in ("4", "5")}


class FakeComfy:
    """模拟 ComfyUI 的 /upload/image、/prompt、/history、/view、/queue。"""

    def __init__(self, *, pending: int = 1, history=None, prompt_status=200, prompt_body=None):
        self.pending = pending  # 前几次 /history 返回 {}（还在排队）
        self.history = history
        self.prompt_status = prompt_status
        self.prompt_body = prompt_body
        self.uploads: list[str] = []
        self.graph = None
        self.cancelled: list[str] = []
        self.views: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/upload/image":
            body = request.content
            start = body.index(b'filename="') + 10
            name = body[start : body.index(b'"', start)].decode()
            self.uploads.append(name)
            return httpx.Response(200, json={"name": name, "subfolder": "", "type": "input"})
        if path == "/prompt":
            if self.prompt_status != 200:
                return httpx.Response(self.prompt_status, json=self.prompt_body)
            self.graph = json.loads(request.content)["prompt"]
            return httpx.Response(200, json={"prompt_id": "p1", "number": 1, "node_errors": {}})
        if path == "/history/p1":
            if self.pending:
                self.pending -= 1
                return httpx.Response(200, json={})
            entry = self.history or {
                "outputs": {"9": {"images": [{"filename": "rt_0001.png", "subfolder": ""}]}},
                "status": {"status_str": "success", "completed": True, "messages": []},
            }
            return httpx.Response(200, json={"p1": entry})
        if path == "/view":
            self.views.append(dict(request.url.params))
            return httpx.Response(200, content=FAKE_PNG, headers={"content-type": "image/png"})
        if path == "/queue":
            self.cancelled.extend(json.loads(request.content)["delete"])
            return httpx.Response(200, json={})
        return httpx.Response(404, text="not found")


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += s


def make(tmp_path: Path, handler, clock: Clock | None = None) -> ComfyUIProvider:
    (tmp_path / "wf.json").write_text(json.dumps(WORKFLOW), encoding="utf-8")
    (tmp_path / "noref.json").write_text(json.dumps(NOREF), encoding="utf-8")
    clock = clock or Clock()
    spec = channel("comfyui", "local", base_url="http://127.0.0.1:8188", key_env=None)
    return ComfyUIProvider(
        "comfyui",
        spec,
        None,
        30,
        transport=httpx.MockTransport(handler),
        workflow_dir=tmp_path,
        sleep=clock.sleep,
        clock=clock,
    )


def ref(color: bytes = b"a") -> Media:
    return Media("image", "image/png", FAKE_PNG + color, "ref.png")


PROMPT = "positive: 1girl, white hair\nnegative: weapon, nsfw"


async def test_generates_an_image_and_fills_the_placeholders(tmp_path):
    comfy = FakeComfy(pending=2)
    clock = Clock()
    p = make(tmp_path, comfy, clock)
    params = {"width": 832, "height": 1216, "poll_interval_s": 0.5}
    out = await p.generate_image("wf.json", PROMPT, params, [ref(), ref(b"b")])
    assert out.cost_usd == 0.0
    [image] = out.images
    assert image.kind == "image" and image.mime == "image/png" and image.data == FAKE_PNG
    g = comfy.graph
    assert g["1"]["inputs"]["text"] == "1girl, white hair"
    assert g["2"]["inputs"]["text"] == "style, weapon, nsfw"  # 嵌在字符串里也能填
    seed = g["3"]["inputs"]["seed"]
    assert isinstance(seed, int) and g["3"]["inputs"]["width"] == 832
    assert g["3"]["inputs"]["height"] == 1216  # 占位符里带空格也认
    assert [g["4"]["inputs"]["image"], g["5"]["inputs"]["image"]] == comfy.uploads
    assert len(set(comfy.uploads)) == 2 and all(n.startswith("roundtable_") for n in comfy.uploads)
    assert clock.sleeps == [0.5, 0.5]  # 排队两次后画完
    assert comfy.views == [{"filename": "rt_0001.png", "subfolder": "", "type": "output"}]


async def test_same_prompt_gives_the_same_seed_and_a_param_overrides_it(tmp_path):
    seeds = []
    for params in ({}, {}, {"seed": 7}):
        comfy = FakeComfy(pending=0)
        await make(tmp_path, comfy).generate_image("wf.json", PROMPT, params, [ref()])
        seeds.append(comfy.graph["3"]["inputs"]["seed"])
    assert seeds[0] == seeds[1] != 7 and seeds[2] == 7


async def test_fewer_refs_than_slots_reuse_the_first_one(tmp_path):
    comfy = FakeComfy(pending=0)
    await make(tmp_path, comfy).generate_image("wf.json", PROMPT, {}, [ref()])
    assert len(comfy.uploads) == 1
    assert comfy.graph["4"]["inputs"]["image"] == comfy.graph["5"]["inputs"]["image"]


async def test_no_refs_switch_to_the_template_without_refs(tmp_path):
    comfy = FakeComfy(pending=0)
    await make(tmp_path, comfy).generate_image(
        "wf.json", "1girl", {"workflow_without_refs": "noref.json", "negative": "lowres"}
    )
    assert comfy.uploads == [] and set(comfy.graph) == set(NOREF)
    assert comfy.graph["2"]["inputs"]["text"] == "style, lowres"  # 没有 negative 行时用参数


async def test_no_refs_and_no_fallback_template_is_a_bad_request(tmp_path):
    with pytest.raises(ProviderError) as e:
        await make(tmp_path, FakeComfy()).generate_image("wf.json", PROMPT, {})
    assert e.value.kind is ErrorKind.BAD_REQUEST and not e.value.failover


async def test_comfyui_not_running_is_a_network_error(tmp_path):
    def down(request):
        raise httpx.ConnectError("connection refused")

    with pytest.raises(ProviderError) as e:
        await make(tmp_path, down).generate_image("wf.json", PROMPT, {}, [ref()])
    assert e.value.kind is ErrorKind.NETWORK and e.value.failover
    assert e.value.__cause__ is None and e.value.__suppress_context__


async def test_waiting_too_long_times_out_and_removes_the_job_from_the_queue(tmp_path):
    comfy = FakeComfy(pending=10**6)
    clock = Clock()
    p = make(tmp_path, comfy, clock)
    with pytest.raises(ProviderError) as e:
        await p.generate_image("wf.json", PROMPT, {"timeout_s": 3, "poll_interval_s": 1}, [ref()])
    assert e.value.kind is ErrorKind.TIMEOUT
    assert comfy.cancelled == ["p1"] and clock.sleeps == [1, 1, 1]


async def test_node_errors_are_redacted_and_truncated(tmp_path):
    leaked = Secret("sk-" + "z" * 40)  # 假 key：登记进脱敏表
    body = {
        "error": {"message": "Prompt outputs failed validation", "details": ""},
        "node_errors": {
            "3": {"errors": [{"message": "Value not in list", "details": leaked.reveal() * 3}]}
        },
    }
    comfy = FakeComfy(prompt_status=400, prompt_body=body)
    with pytest.raises(ProviderError) as e:
        await make(tmp_path, comfy).generate_image("wf.json", PROMPT, {}, [ref()])
    assert e.value.kind is ErrorKind.BAD_REQUEST and e.value.status == 400
    text = str(e.value)
    assert "Value not in list" in text and leaked.reveal() not in text
    assert len(e.value.detail) <= 201


async def test_execution_error_in_history_is_a_server_error(tmp_path):
    history = {
        "outputs": {},
        "status": {
            "status_str": "error",
            "completed": False,
            "messages": [
                ["execution_start", {}],
                ["execution_error", {"node_type": "KSampler", "exception_message": "CUDA OOM"}],
            ],
        },
    }
    comfy = FakeComfy(pending=0, history=history)
    with pytest.raises(ProviderError) as e:
        await make(tmp_path, comfy).generate_image("wf.json", PROMPT, {}, [ref()])
    assert e.value.kind is ErrorKind.SERVER and "CUDA OOM" in str(e.value)


async def test_finished_without_images_is_an_invalid_response(tmp_path):
    history = {"outputs": {"9": {"images": []}}, "status": {"status_str": "success"}}
    with pytest.raises(ProviderError) as e:
        await make(tmp_path, FakeComfy(pending=0, history=history)).generate_image(
            "wf.json", PROMPT, {}, [ref()]
        )
    assert e.value.kind is ErrorKind.INVALID_RESPONSE


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("missing.json", None),
        ("../wf.json", None),
        ("bad.json", "{not json"),
        ("nosave.json", json.dumps({"1": {"class_type": "KSampler", "inputs": {}}})),
    ],
)
async def test_bad_templates_are_bad_requests(tmp_path, name, content):
    p = make(tmp_path, FakeComfy())
    if content is not None:
        (tmp_path / name).write_text(content, encoding="utf-8")
    with pytest.raises(ProviderError) as e:
        await p.generate_image(name, PROMPT, {}, [ref()])
    assert e.value.kind is ErrorKind.BAD_REQUEST


async def test_unknown_placeholder_is_a_bad_request(tmp_path):
    p = make(tmp_path, FakeComfy())
    wf = {**NOREF, "8": {"class_type": "X", "inputs": {"v": "{{steps}}"}}}
    (tmp_path / "odd.json").write_text(json.dumps(wf), encoding="utf-8")
    with pytest.raises(ProviderError) as e:
        await p.generate_image("odd.json", PROMPT, {})
    assert e.value.kind is ErrorKind.BAD_REQUEST and "steps" in str(e.value)


async def test_text_completion_is_not_supported(tmp_path):
    with pytest.raises(UnsupportedCapability):
        await make(tmp_path, FakeComfy()).complete("wf.json", [Message("user", "hi")], {})


# --- 纯函数 ----------------------------------------------------------------------


def test_split_prompt():
    assert split_prompt(PROMPT) == ("1girl, white hair", "weapon, nsfw")
    assert split_prompt("just tags", "lowres") == ("just tags", "lowres")
    assert split_prompt("a\nNegative:  \n", "lowres") == ("a", "lowres")


def test_fill_and_ref_slots():
    assert ref_slots(WORKFLOW) == 2 and ref_slots(NOREF) == 0
    values = {"prompt": "p", "negative": "n", "seed": "5", "width": 8, "height": 9}
    values |= {"ref_1": "a.png", "ref_2": "b.png"}
    g = fill(WORKFLOW, values)
    assert g["3"]["inputs"] == {"seed": 5, "width": 8, "height": 9}
    assert g["4"]["inputs"]["image"] == "a.png" and WORKFLOW["4"]["inputs"]["image"] == "{{ref_1}}"
    with pytest.raises(KeyError):
        fill(WORKFLOW, {"prompt": "p"})


# --- 仓库里的配置和示例模板 --------------------------------------------------------------


def test_adapter_is_registered_and_repo_config_wires_the_channel():
    assert "comfyui" in adapter_names()
    models = load_config().models
    spec = models.channels["comfyui"]
    assert spec.adapter == "comfyui" and spec.kind == "local" and spec.key_env is None
    assert spec.base_url == "http://127.0.0.1:8188"
    [model] = [m for m in models.models if any(r.channel == "comfyui" for r in m.routes)]
    assert not model.enabled and not model.seat and model.tier == "budget"
    assert model.price.input == model.price.output == 0
    assert model.media_price is not None and model.media_price.usd == 0
    assert {"image_gen", "image_edit"} <= set(model.tags) and model.image_prompt == "tags"
    assert all(r.channel == "comfyui" for r in model.routes)  # 只走本地，不会悄悄走付费渠道


def test_repo_example_templates_are_valid():
    models = load_config().models
    [model] = [m for m in models.models if any(r.channel == "comfyui" for r in m.routes)]
    names = [model.routes[0].model, model.params["workflow_without_refs"]]
    with_refs, without = (
        json.loads((DEFAULT_WORKFLOW_DIR / n).read_text(encoding="utf-8")) for n in names
    )
    assert ref_slots(with_refs) == 3 and ref_slots(without) == 0
    for wf in (with_refs, without):
        assert save_nodes(wf)
        text = json.dumps(wf)
        keys = ("prompt", "negative", "seed", "width", "height")
        assert all("{{" + k + "}}" in text for k in keys)
        values = {"prompt": "p", "negative": "n", "seed": 1, "width": 8, "height": 8}
        values |= {f"ref_{i}": "r.png" for i in (1, 2, 3)}
        assert "{{" not in json.dumps(fill(wf, values))
    # 不提交模型权重：模板只写文件名占位
    assert not list(DEFAULT_WORKFLOW_DIR.glob("*.safetensors"))


async def test_a_local_copy_of_the_template_wins(tmp_path):
    p = make(tmp_path, comfy := FakeComfy(pending=0))
    mine = {**NOREF, "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "mine {{prompt}}"}}}
    (tmp_path / "noref.local.json").write_text(json.dumps(mine), encoding="utf-8")
    await p.generate_image("noref.json", "1girl", {})
    assert comfy.graph["1"]["inputs"]["text"] == "mine 1girl"
