"""革新例会·交给开发侧：「做这条」后叫起开发侧（假命令）、记 PR、只有点「合并」才合并。"""

from __future__ import annotations

import pytest

from roundtable.core.config.schema import EvolutionImplementRules, EvolutionRules
from roundtable.core.evolution_dev import branch_name, find_pr, render_brief, resolve
from roundtable.core.service import ServiceError

from .service.test_evolution_service import make as meeting_env

PR = "https://github.com/o/r/pull/42"


class FakeRun:
    """记录调用；按命令决定退出码和输出。"""

    def __init__(self, *, agent_code=0, agent_out=f"做完了\n{PR}\n", merge_code=0):
        self.calls: list[tuple[list[str], str, str]] = []
        self.agent_code, self.agent_out, self.merge_code = agent_code, agent_out, merge_code

    async def __call__(self, argv, cwd, stdin, log, timeout_s):
        argv = list(argv)
        self.calls.append((argv, str(cwd), stdin))
        log.parent.mkdir(parents=True, exist_ok=True)
        if "remote" in argv:
            log.write_text("https://github.com/o/r.git\n", encoding="utf-8")
            return 0
        if "clone" in argv:
            (cwd / argv[-1]).joinpath(".git").mkdir(parents=True, exist_ok=True)
            return 0
        if "merge" in argv:
            return self.merge_code
        with log.open("a", encoding="utf-8") as f:
            f.write(self.agent_out)
        return self.agent_code


async def setup(tmp_path, run=None, enabled=True):
    env, svc = meeting_env(tmp_path)
    rules = env.rt.config.roundtable.evolution.model_copy(
        update={
            "implement": EvolutionImplementRules(
                enabled=enabled, agent=["python", "-c", "pass"], gh=["python"], base_branch="dev"
            )
        }
    )
    env.rt.config = env.config = env.config.model_copy(
        update={"roundtable": env.config.roundtable.model_copy(update={"evolution": rules})}
    )
    svc.dev_run = run or FakeRun()
    sid = svc.evolution_meet()["session_id"]
    await svc.wait(sid)
    idea = svc.evolution()["meetings"][0]["ideas"][0]
    return env, svc, sid, idea


async def finish(svc):
    for task in list(svc._dev_tasks.values()):
        await task


def test_helpers():
    assert find_pr(f"log\n{PR}\nmore https://github.com/o/r/pull/43 end") == (
        "https://github.com/o/r/pull/43"
    )
    assert find_pr("没有开 PR：测试没过") is None
    assert branch_name("2026-W44", "I15") == "evolve/2026-w44-i15"
    assert render_brief("{idea}：{text}", idea="I1", text="加 {x}") == "I1：加 {x}"
    assert resolve("python") and resolve("surely-not-a-command-xyz") is None


async def test_do_starts_dev_in_workspace_and_records_pr(tmp_path):
    run = FakeRun()
    env, svc, sid, idea = await setup(tmp_path, run)
    out = svc.evolution_decide(sid, idea["id"], "do")
    assert out["dev_started"]
    assert svc.evolution()["meetings"][0]["ideas"][0]["job"]["status"] == "running"
    await finish(svc)
    job = svc.evolution()["meetings"][0]["ideas"][0]["job"]
    assert job["status"] == "done" and job["pr"] == PR and not job.get("merged")
    # 先 clone 专用副本，再在副本里叫开发侧；任务说明带着建议、分支、目标分支
    kinds = [c[0] for c in run.calls]
    assert "clone" in kinds[1] and kinds[2][0].lower().endswith(("python", "python.exe"))
    agent_cwd, brief = run.calls[2][1], run.calls[2][2]
    assert agent_cwd.endswith("工作区") and idea["text"] in brief
    week = svc.evolution()["meetings"][0]["week"].lower()  # 按例会那周，不写死周号
    assert f"evolve/{week}-" in brief and "origin/dev" in brief and "不要合并" in brief
    # 再点一次「做这条」不会重复叫
    assert not svc.evolution_decide(sid, idea["id"], "do")["dev_started"]
    assert len(run.calls) == 3


async def test_merge_only_when_clicked_and_only_once(tmp_path):
    run = FakeRun()
    env, svc, sid, idea = await setup(tmp_path, run)
    svc.evolution_decide(sid, idea["id"], "do")
    await finish(svc)
    assert not any("merge" in c[0] for c in run.calls)  # 不会自动合并
    out = await svc.evolution_merge(sid, idea["id"])
    assert out["merged"] and out["pr"] == PR
    merge = next(c[0] for c in run.calls if "merge" in c[0])
    assert merge[-3:] == ["merge", PR, "--merge"]
    assert svc.evolution()["meetings"][0]["ideas"][0]["job"]["merged"]
    with pytest.raises(ServiceError, match="已经合并过"):
        await svc.evolution_merge(sid, idea["id"])


async def test_failed_or_no_pr_cannot_be_merged(tmp_path):
    run = FakeRun(agent_code=1, agent_out="没有开 PR：测试没过\n")
    env, svc, sid, idea = await setup(tmp_path, run)
    svc.evolution_decide(sid, idea["id"], "do")
    await finish(svc)
    assert svc.evolution()["meetings"][0]["ideas"][0]["job"]["status"] == "failed"
    with pytest.raises(ServiceError, match="还没有开好的 PR"):
        await svc.evolution_merge(sid, idea["id"])
    # 失败后再点「做这条」可以重来
    run.agent_code, run.agent_out = 0, f"{PR}\n"
    assert svc.evolution_decide(sid, idea["id"], "do")["dev_started"]
    await finish(svc)
    assert svc.evolution()["meetings"][0]["ideas"][0]["job"]["status"] == "done"


async def test_merge_failure_is_reported(tmp_path):
    env, svc, sid, idea = await setup(tmp_path, FakeRun(merge_code=1))
    svc.evolution_decide(sid, idea["id"], "do")
    await finish(svc)
    with pytest.raises(ServiceError, match="合并没有成功"):
        await svc.evolution_merge(sid, idea["id"])


async def test_off_by_default_and_skip_never_starts(tmp_path):
    assert EvolutionRules().implement.enabled is False
    run = FakeRun()
    env, svc, sid, idea = await setup(tmp_path, run, enabled=False)
    assert not svc.evolution_decide(sid, idea["id"], "do")["dev_started"]
    env, svc, sid, idea = await setup(tmp_path / "b", run)
    assert not svc.evolution_decide(sid, idea["id"], "skip")["dev_started"]
    assert run.calls == []


async def test_missing_agent_command_marks_failed(tmp_path):
    env, svc, sid, idea = await setup(tmp_path)
    rules = env.rt.config.roundtable.evolution
    rules.implement.agent[:] = ["surely-not-a-command-xyz"]
    svc.evolution_decide(sid, idea["id"], "do")
    await finish(svc)
    job = svc.evolution()["meetings"][0]["ideas"][0]["job"]
    assert job["status"] == "failed" and "找不到命令" in job["error"]


async def test_running_job_after_restart_shows_interrupted(tmp_path):
    env, svc, sid, idea = await setup(tmp_path)
    svc.evolution_decide(sid, idea["id"], "do")
    task = svc._dev_tasks.pop(f"{sid}:{idea['id']}")  # 模拟服务重启：任务不在了
    assert svc.evolution()["meetings"][0]["ideas"][0]["job"]["status"] == "interrupted"
    await task
