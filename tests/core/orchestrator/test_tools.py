"""工具调用：文字协议、同一步骤所有成员相同的工具说明、额度、文件登记与转交、图像生成。"""

from __future__ import annotations

from pathlib import Path

import pytest

from roundtable.core.providers import Media, RawCompletion
from roundtable.core.routing import Question, UserChoice
from roundtable.core.tools import RunOutput, Sandbox

from ..attachments.samples import PNG
from ..routing.conftest import POOL
from .conftest import MEDIUM, Env

ANSWER = "独立完成同一道题"
REVIEW = "审阅每一份答案"
CUSTOM = UserChoice("custom", ("b1", "b2", "b3", "f1"), "f1")
GUIDE = "这一步你可以使用工具"


class FakeSandbox(Sandbox):
    """不运行代码：按代码内容写出文件并给出输出，记录收到的代码。"""

    name = "fake"

    def __init__(self, rules):
        super().__init__(rules)
        self.codes: list[str] = []

    def unavailable_reason(self):
        return None

    def command(self, job: Path) -> list[str]:
        return []

    async def run(self, job: Path, **kw) -> RunOutput:
        code = (job / "main.py").read_text(encoding="utf-8")
        self.codes.append(code)
        assert (job / "in").is_dir() and (job / "out").is_dir()
        if "boom" in code:
            return RunOutput("", "Traceback: ZeroDivisionError", 1, 0.1)
        if "plot" in code:
            (job / "out" / "plot.png").write_bytes(PNG)
        return RunOutput("42\n", "", 0, 0.2)


def with_tools(env: Env, **updates) -> FakeSandbox:
    rules = env.rt.config.roundtable.tools.model_copy(update=updates)
    rt_cfg = env.rt.config.roundtable.model_copy(update={"tools": rules})
    env.rt.config = env.rt.config.model_copy(update={"roundtable": rt_cfg})
    sandbox = FakeSandbox(rules.python)
    env.rt.tools_sandbox = (sandbox, None)
    return sandbox


def last_user(messages) -> str:
    return [m for m in messages if m.role == "user"][-1].content


def scripted(env: Env, step_marker: str, request: str):
    """该步骤的成员第一次输出 request（申请工具），收到结果后给出正常答案。"""
    original = env.reply

    def reply(model, messages):
        if step_marker in messages[0].content and "<tool_result" not in last_user(messages):
            return request
        return original(model, messages)

    env.fake._default = reply


def answer_calls(env):
    return [c for c in env.fake.calls if ANSWER in c.messages[0].content]


async def test_python_round_trip_files_shared_with_reviewers():
    env = Env(confirm_threshold_usd=100.0)
    sandbox = with_tools(env)
    scripted(
        env, ANSWER, '先画图。\n<tool_call name="python">\nimport plot\nprint(42)\n</tool_call>'
    )
    r = await env.orc.start(Question(MEDIUM), CUSTOM, seed=1)
    assert r.status == "completed"
    assert len(sandbox.codes) == 3 and sandbox.codes[0] == "import plot\nprint(42)"

    calls = answer_calls(env)
    assert len(calls) == 6  # 3 位组员 × 2 轮
    second = [c for c in calls if "<tool_result" in last_user(c.messages)]
    assert len(second) == 3
    result = last_user(second[0].messages)
    assert '<tool_result tool="python" status="成功">' in result and "42" in result
    assert "out/plot.png" in result and "剩余工具调用轮数：5" in result
    # 工具说明对同一步骤所有成员相同（system 只差代号）
    guides = {c.messages[0].content.split(GUIDE, 1)[1] for c in calls}
    assert len(guides) == 1 and "本步骤可用的工具：python、write_file。" in next(iter(guides))

    repo = env.rt.repo
    sid = r.session_id
    assert [t["status"] for t in repo.tool_calls(sid)] == ["ok"] * 3
    files = repo.files(sid)
    assert sorted(f["path"] for f in files) == ["plot.png"] * 3
    assert all(f["tool_call_id"] for f in files) and all(f["step"] == "answer" for f in files)
    answers = [o for o in repo.session_view(sid).outputs if o.kind == "answer"]
    assert all("<tool_call" not in o.content for o in answers)

    reviews = [c for c in env.fake.calls if REVIEW in c.messages[0].content]
    assert all('<file path="out/plot.png" type="image"' in last_user(c.messages) for c in reviews)
    assert all("本步骤可用的工具：python。" in c.messages[0].content for c in reviews)
    synth = [c for c in env.fake.calls if "汇总成一份结论" in c.messages[0].content]
    assert synth and GUIDE not in synth[0].messages[0].content  # 汇总没有配置工具


async def test_limits_rounds_runs_and_errors():
    env = Env(confirm_threshold_usd=100.0)
    with_tools(env, max_rounds=4, by_step={"answer": ["python"]})
    rules = env.rt.config.roundtable.tools
    rules_py = rules.python.model_copy(update={"max_runs": 2})
    rt_cfg = env.rt.config.roundtable.model_copy(
        update={"tools": rules.model_copy(update={"python": rules_py})}
    )
    env.rt.config = env.rt.config.model_copy(update={"roundtable": rt_cfg})
    original = env.reply

    def greedy(model, messages):  # b1 一直申请运行代码（第一次出错）
        if model == "b1" and ANSWER in messages[0].content:
            rounds = sum("<tool_result" in m.content for m in messages if m.role == "user")
            body = "1/0  # boom" if rounds == 0 else "print(1)"
            return f'答案草稿 {rounds}\n<tool_call name="python">\n{body}\n</tool_call>'
        return original(model, messages)

    env.fake._default = greedy
    r = await env.orc.start(Question(MEDIUM), CUSTOM, seed=2)
    mine = [t for t in env.rt.repo.tool_calls(r.session_id) if t["code"]]
    statuses = [t["status"] for t in mine]
    # 2 次运行额度，4 轮上限；草稿太短被打回重做时，重做也只能拿到"额度已用完"
    assert statuses[:4] == ["error", "ok", "limit", "limit"]
    assert set(statuses[4:]) <= {"limit"}
    b1 = [c for c in answer_calls(env) if c.model == "b1"]
    assert "剩余工具调用轮数：0" in last_user(b1[4].messages)  # 4 轮工具后的最后一次
    view = env.rt.repo.session_view(r.session_id)
    stored = [o.content for o in view.outputs if o.kind == "answer"]
    assert any(s.startswith("答案草稿 4") for s in stored)  # 超过轮数：去掉工具调用后作为结果
    assert all("<tool_call" not in s for s in stored)


async def test_write_file_and_rejections():
    env = Env(confirm_threshold_usd=100.0)
    with_tools(env)
    env.rt.tools_sandbox = (None, "没有沙箱")  # python 不可用：说明里只列 write_file
    request = (
        '<tool_call name="write_file" path="../../.env">KEY=1</tool_call>\n'
        '<tool_call name="write_file" path="notes/solution.md"># 解答\n过程……</tool_call>\n'
        '<tool_call name="write_file" path="run.exe">MZ</tool_call>\n'
        '<tool_call name="python">print(1)</tool_call>'
    )
    scripted(env, ANSWER, request)
    r = await env.orc.start(Question(MEDIUM), CUSTOM, seed=3)
    assert r.status == "completed"
    calls = answer_calls(env)
    assert "本步骤可用的工具：write_file。" in calls[0].messages[0].content
    statuses = [t["status"] for t in env.rt.repo.tool_calls(r.session_id)]
    assert statuses[:4] == ["rejected", "ok", "rejected", "rejected"]
    paths = {f["path"] for f in env.rt.repo.files(r.session_id)}
    assert paths == {"notes/solution.md"}
    note = last_user([c for c in calls if "<tool_result" in last_user(c.messages)][0].messages)
    assert "文件路径不合法" in note and "不能使用工具「python」" in note
    assert not (Path.cwd() / ".env.test-leak").exists()


async def test_review_step_cannot_write_files():
    env = Env(confirm_threshold_usd=100.0)
    with_tools(env)
    original = env.reply

    def reply(model, messages):
        if REVIEW in messages[0].content and "<tool_result" not in last_user(messages):
            return '<tool_call name="write_file" path="x.md">hi</tool_call>'
        return original(model, messages)

    env.fake._default = reply
    r = await env.orc.start(Question(MEDIUM), CUSTOM, seed=4)
    assert r.status == "completed"
    tools = env.rt.repo.tool_calls(r.session_id)
    assert tools and {t["status"] for t in tools} == {"rejected"}
    assert env.rt.repo.files(r.session_id) == []
    assert any(o.kind == "review" for o in env.rt.repo.session_view(r.session_id).outputs)


def image_pool():
    return [*POOL, ("img1", "V9", None, ["image_gen"], 0.5, 2.0)]


async def test_generate_image_uses_tool_model_that_never_sits():
    env = Env(pool=image_pool(), confirm_threshold_usd=100.0)
    with_tools(env)
    env.fake.queue("img1", *[RawCompletion("", images=(Media("image", "image/png", PNG),))] * 3)
    scripted(
        env, ANSWER, '<tool_call name="generate_image" path="figure.jpg">函数图像示意图</tool_call>'
    )
    r = await env.orc.start(Question(MEDIUM), seed=5)  # 便宜档全员：工具模型不上桌
    assert r.status == "completed"
    table = env.rt.repo.tables(r.session_id)[0]
    assert "img1" not in table["members"].values() and table["coordinator"] != "img1"
    image_calls = [c for c in env.fake.calls if c.model == "img1"]
    assert (
        image_calls
        and "<description>\n函数图像示意图\n</description>" in image_calls[0].messages[1].content
    )
    files = env.rt.repo.files(r.session_id)
    assert {f["path"] for f in files} == {"figure.png"}  # 扩展名按返回的图片类型
    tool_rows = env.rt.repo.conn.execute(
        "SELECT role, code, step FROM calls WHERE model_id = 'img1'"
    ).fetchall()
    assert {tuple(r) for r in tool_rows} <= {("tool", c, "answer") for c in table["members"]}
    guide = answer_calls(env)[0].messages[0].content
    assert "本步骤可用的工具：python、write_file、generate_image。" in guide


async def test_tools_disabled_means_no_guide():
    env = Env(confirm_threshold_usd=100.0)
    with_tools(env, enabled=False)
    r = await env.orc.start(Question(MEDIUM), CUSTOM, seed=6)
    assert r.status == "completed"
    assert all(GUIDE not in c.messages[0].content for c in env.fake.calls)


def test_untiered_model_must_be_tool_only():
    from roundtable.core.config import AppConfig

    from ..routing.conftest import app_config

    cfg = app_config(pool=image_pool())
    data = cfg.model_dump()
    data["models"]["models"][-1]["seat"] = True
    with pytest.raises(ValueError, match="缺少 tier"):
        AppConfig.model_validate(data)


async def test_budget_exhausted_stops_tools():
    from roundtable.core.attachments import FileStore
    from roundtable.core.tools import ToolBox, ToolRequest

    env = Env()
    sid = env.rt.repo.create_session("q", seed=1)
    box = ToolBox(
        session_id=sid,
        table_no=0,
        config=env.config,
        router=env.rt.router,
        prompts=env.rt.prompts,
        repo=env.rt.repo,
        store=FileStore(None),
        scrubber=env.rt.scrubber,
        sandbox=FakeSandbox(env.config.roundtable.tools.python),
        budget_ok=lambda: False,
    )
    try:
        result = await box.execute(
            ToolRequest("python", {}, "print(1)"),
            step="answer",
            code="甲",
            round_no=1,
            call_id=None,
            allowed=["python"],
        )
        assert result.status == "limit" and box.sandbox.codes == []
    finally:
        box.close()


async def test_real_wasm_sandbox_end_to_end():
    """装了 wasm 沙箱时：成员真的运行代码画图，图片作为文件登记。"""
    from roundtable.core.tools import WasmSandbox

    env = Env(confirm_threshold_usd=100.0)
    with_tools(env, by_step={"answer": ["python"]})
    real = WasmSandbox(env.rt.config.roundtable.tools.python)
    if real.unavailable_reason():
        pytest.skip(real.unavailable_reason())
    env.rt.tools_sandbox = (real, None)
    code = (
        "import matplotlib.pyplot as plt, numpy as np\n"
        "x = np.linspace(-2, 2, 50)\nplt.plot(x, x**3 - 3*x)\nplt.savefig('out/f.png')\n"
        "print('max', max(x**3 - 3*x))"
    )
    scripted(env, ANSWER, f'<tool_call name="python">\n{code}\n</tool_call>')
    r = await env.orc.start(
        Question(MEDIUM), UserChoice("custom", ("b1", "b2", "f1"), "f1"), seed=7
    )
    assert r.status == "completed"
    tools = env.rt.repo.tool_calls(r.session_id)
    assert [t["status"] for t in tools] == ["ok", "ok"] and "max 2.0" in tools[0]["output"]
    files = env.rt.repo.files(r.session_id)
    assert {f["path"] for f in files} == {"f.png"}
    assert env.rt.files.load(files[0]["storage_key"]).startswith(b"\x89PNG")
