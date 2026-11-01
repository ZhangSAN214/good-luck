"""拖进来的素材：先收下、开工时拷进工作台、能看图的助手看一遍写进方案，签字时标用处放进成品。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from roundtable.core.supercomputer.runner import RunResult
from tests.core.benchkit.test_builder import ProjectSC, W, project

PNG = b"\x89PNG fake"


class LookSC(ProjectSC):
    """dsh 不会看图（没写 vision），看素材只能派给另外两位。"""

    def __init__(self, **kw):
        super().__init__(workers=("dsh", "codex", "claude"), **kw)
        skills = {"dsh": {}, "codex": {"vision": 0.7}, "claude": {"vision": 0.9}}
        self.rules = SimpleNamespace(
            workers=[SimpleNamespace(id=w, skills=skills[w]) for w in self.workers]
        )

    async def step(self, kind, prompt, **kw):
        p = json.loads(prompt)
        if p["role"] == "bench_look":
            self.prompts.append(prompt)
            self.calls.append({"role": p["role"], "kind": kind, "exclude": kw["exclude"]})
            kw["record"]["worker"] = "claude"
            files = [line[2:].split("（")[0].split("/")[1] for line in p["files"].splitlines()]
            out = {
                "items": [
                    {
                        "file": f,
                        "what": f"第 {n} 张：修图软件",
                        "palette": ["#FF7A00"],
                        "borrow": "左工具栏",
                    }
                    for n, f in enumerate(files, 1)
                ],
                "summary": "暖橙、编辑器式三栏",
                "palette": ["#ff7a00", "#1d1d1f"],
            }
            return W("claude"), RunResult(0, json.dumps(out, ensure_ascii=False))
        return await super().step(kind, prompt, **kw)


def test_stage_material_checks_type_size_and_video_frames(tmp_path):
    b = project(tmp_path, LookSC(), interview=False)
    with pytest.raises(ValueError, match="只收图片"):
        b.stage_material("a.exe", b"x")
    with pytest.raises(ValueError, match="超过"):
        b.stage_material("a.png", b"")
    with pytest.raises(ValueError, match="找不到这个视频"):
        b.stage_material("帧.jpg", PNG, of="0" * 12)
    v = b.stage_material("../../片.mp4", b"video")
    assert v["kind"] == "video" and v["name"] == "片.mp4" and v["file"] == f"{v['id']}.mp4"
    with pytest.raises(ValueError, match="截帧要是图片"):
        b.stage_material("帧.mp4", b"x", of=v["id"])
    f = b.stage_material("片-第1帧.jpg", PNG, of=v["id"])
    assert f["kind"] == "frame" and f["of"] == v["id"]
    assert (tmp_path / "_materials" / f["file"]).read_bytes() == PNG


async def test_materials_are_looked_at_by_vision_workers_and_go_into_the_plan(tmp_path):
    sc = LookSC()
    b = project(tmp_path, sc, interview=False)
    img = b.stage_material("参考.png", PNG)
    video = b.stage_material("演示.mp4", b"video")
    frame = b.stage_material("演示-第1帧.jpg", PNG, of=video["id"])
    b.submit("做一个修图台", [img["id"], video["id"], "没有这个"])  # 截帧跟着视频来
    await b.wait()
    build = b.build
    folder = tmp_path / build["id"]
    assert [m["id"] for m in build["materials"]] == [img["id"], video["id"], frame["id"]]
    assert all((folder / "materials" / m["file"]).is_file() for m in build["materials"])
    look = next(c for c in sc.calls if c["role"] == "bench_look")
    assert look["kind"] == "vision" and look["exclude"] == ("dsh",)
    prompt = next(json.loads(p) for p in sc.prompts if json.loads(p)["role"] == "bench_look")
    assert f"materials/{img['file']}（图片「参考.png」）" in prompt["files"]
    assert "视频「演示.mp4」的截帧" in prompt["files"]
    assert video["file"] not in prompt["files"]  # 视频本身不给看，看截帧
    assert build["look"]["palette"] == ["#ff7a00", "#1d1d1f"]
    assert all(m["seen"] for m in build["materials"] if m["kind"] != "video")
    plan = next(json.loads(p) for p in sc.prompts if json.loads(p)["role"] == "bench_plan")
    assert "你拖进来的参考素材" in plan["request"] and "暖橙、编辑器式三栏" in plan["request"]
    assert any(t["title"] == "看素材" for t in build["timeline"])
    assert any(c["role"] == "看素材" and c["who"] == "claude" for c in build["team"]["cards"])
    d = build["proposal"]["design"]
    assert d["look"]["summary"] == "暖橙、编辑器式三栏" and len(d["materials"]) == 3
    assert d["roles"]["logo"] == "Logo"


async def test_no_vision_worker_just_keeps_files(tmp_path):
    sc = LookSC()
    sc.rules.workers[1].skills = {}
    sc.rules.workers[2].skills = {}

    async def nobody(kind, prompt, **kw):
        if json.loads(prompt)["role"] == "bench_look":
            assert set(kw["exclude"]) == {"dsh", "codex", "claude"}
            return None, None
        return await LookSC.step(sc, kind, prompt, **kw)

    sc.step = nobody
    b = project(tmp_path, sc, interview=False)
    img = b.stage_material("参考.png", PNG)
    b.submit("做一个修图台", [img["id"]])
    await b.wait()
    assert b.build["look"] is None and b.build["gate"]["kind"] == "sign"
    assert any("没有能看图的助手" in x["text"] for x in b.build["log"])


async def test_attach_look_and_sign_put_assets_into_the_product(tmp_path):
    sc = LookSC()
    b = project(tmp_path, sc, interview=False)
    b.submit("做一个修图台")
    await b.wait()
    bid = b.build["id"]
    assert b.build["look"] is None and b.build["gate"]["kind"] == "sign"
    with pytest.raises(ValueError):
        b.look(bid)  # 还没有素材
    logo = b.stage_material("标.png", PNG)
    bg = b.stage_material("底.jpg", PNG)
    b.attach(bid, [logo["id"], bg["id"]])
    with pytest.raises(ValueError):
        b.attach(bid, [logo["id"]])  # 已经有了
    assert len(b.build["proposal"]["design"]["materials"]) == 2
    b.look(bid)
    await b.wait()
    assert b.build["gate"]["kind"] == "sign" and b.build["status"] == "waiting"
    assert b.build["proposal"]["design"]["look"]["items"][0]["what"].startswith("第 1 张")

    b.sign(bid, design={"assets": {logo["id"]: "logo", bg["id"]: "bg"}, "bg": "image"})
    await b.wait()
    folder = tmp_path / bid
    assert (folder / "assets" / logo["file"]).read_bytes() == PNG
    html = (folder / "index.html").read_text(encoding="utf-8")
    assert f'<img src="assets/{logo["file"]}" alt="标志">' in html and 'data-k-bg="image"' in html
    assert f"素材「标.png」当Logo，文件 assets/{logo['file']}" in b.build["brief"]
