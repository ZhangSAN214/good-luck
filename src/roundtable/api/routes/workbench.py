"""工作台：dsh、工作台生成器（资料台、周报）、超级电脑。

从 app.py 拆出（代码精简第 2 批）。
"""

from __future__ import annotations

import mimetypes
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from roundtable.api.bodies import (
    BenchAnswerBody,
    BenchAskBody,
    BenchAttachBody,
    BenchBody,
    BenchConflictBody,
    BenchDecideBody,
    BenchEditBody,
    BenchFoundBody,
    BenchInstructBody,
    BenchPluginBody,
    BenchProfileBody,
    BenchRerunBody,
    BenchRevertBody,
    BenchReviewBody,
    BenchSignBody,
    BenchTreeKeyBody,
    SuperLimitBody,
    SuperScheduleBody,
    SuperTaskBody,
    SuperToggleBody,
)
from roundtable.api.common import bench_csp, dsh_out, svc
from roundtable.core.service import ServiceError

router = APIRouter()


@router.get("/api/dsh")
async def dsh_status(request: Request) -> dict[str, Any]:
    return dsh_out(request, svc(request).dsh_status())


@router.post("/api/dsh/start")
async def dsh_start(request: Request) -> dict[str, Any]:
    return dsh_out(request, await svc(request).dsh_start())


@router.post("/api/dsh/stop")
async def dsh_stop(request: Request) -> dict[str, Any]:
    return dsh_out(request, await svc(request).dsh_stop())


@router.post("/api/dsh/check")
async def dsh_check(request: Request) -> dict[str, Any]:
    return dsh_out(request, await svc(request).dsh_check())


# 超级电脑：几位编程助手平级、按分数派活（docs/SUPERCOMPUTER.md）
@router.get("/api/bench")
async def bench_status(request: Request) -> dict[str, Any]:
    return svc(request).bench_status()


@router.post("/api/bench")
async def bench_submit(request: Request, body: BenchBody) -> dict[str, Any]:
    return svc(request).bench_submit(
        body.request, body.materials, from_product=body.from_product, form=body.form
    )


@router.post("/api/bench/materials", status_code=201)
async def bench_material(request: Request, name: str, of: str = "") -> dict[str, Any]:
    """拖进来的一份素材：请求体是原始字节，?name= 文件名，?of= 视频编号（浏览器截的帧）。"""
    service = svc(request)
    limit = service.bench_material_limit
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise ServiceError("文件超过大小上限", 413)
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise ServiceError("文件超过大小上限", 413)
        chunks.append(chunk)
    return service.bench_material(name, b"".join(chunks), of)


@router.post("/api/bench/cancel")
async def bench_cancel(request: Request) -> dict[str, Any]:
    return await svc(request).bench_cancel()


@router.get("/api/bench/net-check")
async def bench_net_check(request: Request) -> dict[str, Any]:
    return await svc(request).bench_net_check()


@router.post("/api/bench/profile")
async def bench_profile_add(request: Request, body: BenchProfileBody) -> dict[str, Any]:
    return svc(request).bench_profile_add(body.text, body.src)


@router.delete("/api/bench/profile/{pid}")
async def bench_profile_remove(request: Request, pid: str) -> dict[str, Any]:
    return svc(request).bench_profile_remove(pid)


@router.get("/api/bench/desk")
async def bench_desk(request: Request) -> dict[str, Any]:
    return svc(request).bench_desk()


@router.post("/api/bench/desk/refresh")
async def bench_desk_refresh(request: Request) -> dict[str, Any]:
    return svc(request).bench_desk(refresh=True)


@router.post("/api/bench/desk/suggest/{sid}")
async def bench_desk_suggest(request: Request, sid: str, accept: bool = True) -> dict[str, Any]:
    return svc(request).bench_desk_suggest(sid, accept)


@router.get("/api/bench/desk/weekly")
async def bench_weekly(request: Request) -> dict[str, Any]:
    return svc(request).bench_weekly()


@router.post("/api/bench/desk/weekly/build")
async def bench_weekly_build(request: Request) -> dict[str, Any]:
    return svc(request).bench_weekly_build()


@router.get("/api/bench/desk/weekly/{week}")
async def bench_weekly_get(request: Request, week: str) -> dict[str, Any]:
    return svc(request).bench_weekly(week)


@router.get("/api/bench/{bench_id}")
async def bench_get(request: Request, bench_id: str) -> dict[str, Any]:
    return svc(request).bench_get(bench_id)


@router.post("/api/bench/{bench_id}/answer")
async def bench_answer(request: Request, bench_id: str, body: BenchAnswerBody) -> dict[str, Any]:
    return await svc(request).bench_project(bench_id, "answer", answers=body.answers)


@router.post("/api/bench/{bench_id}/decide")
async def bench_decide(request: Request, bench_id: str, body: BenchDecideBody) -> dict[str, Any]:
    reqs = [r.model_dump() for r in body.requirements or []]
    return await svc(request).bench_project(
        bench_id, "decide", choice=body.action, requirements=reqs
    )


@router.post("/api/bench/{bench_id}/sign")
async def bench_sign(request: Request, bench_id: str, body: BenchSignBody) -> dict[str, Any]:
    reqs = [r.model_dump() for r in body.requirements] if body.requirements is not None else None
    design = body.design.model_dump(exclude_none=True) if body.design is not None else None
    return await svc(request).bench_project(
        bench_id, "sign", requirements=reqs, note=body.note, design=design
    )


@router.post("/api/bench/{bench_id}/review")
async def bench_review(request: Request, bench_id: str, body: BenchReviewBody) -> dict[str, Any]:
    return await svc(request).bench_project(
        bench_id,
        "review",
        decision=body.decision,
        comments=[c.model_dump() for c in body.comments],
    )


@router.post("/api/bench/{bench_id}/materials")
async def bench_attach(request: Request, bench_id: str, body: BenchAttachBody) -> dict[str, Any]:
    return await svc(request).bench_project(bench_id, "attach", ids=body.ids)


@router.post("/api/bench/{bench_id}/look")
async def bench_look(request: Request, bench_id: str) -> dict[str, Any]:
    return await svc(request).bench_project(bench_id, "look")


@router.post("/api/bench/{bench_id}/pause")
async def bench_pause(request: Request, bench_id: str, body: BenchTreeKeyBody) -> dict[str, Any]:
    return await svc(request).bench_project(bench_id, "pause", key=body.key or "*")


@router.post("/api/bench/{bench_id}/resume")
async def bench_resume(request: Request, bench_id: str, body: BenchTreeKeyBody) -> dict[str, Any]:
    return await svc(request).bench_project(bench_id, "resume", key=body.key)


@router.post("/api/bench/{bench_id}/rerun")
async def bench_rerun(request: Request, bench_id: str, body: BenchRerunBody) -> dict[str, Any]:
    return await svc(request).bench_project(
        bench_id, "rerun", thread=body.thread, reason=body.reason, worker=body.worker
    )


@router.get("/api/bench/{bench_id}/edit")
async def bench_edit_view(
    request: Request, bench_id: str, key: str, n: int | None = None
) -> dict[str, Any]:
    return svc(request).bench_edit_view(bench_id, key, n)


@router.post("/api/bench/{bench_id}/edit")
async def bench_edit(request: Request, bench_id: str, body: BenchEditBody) -> dict[str, Any]:
    return await svc(request).bench_project(
        bench_id, "edit", key=body.key, value=body.value, why=body.why
    )


@router.post("/api/bench/{bench_id}/revert")
async def bench_revert(request: Request, bench_id: str, body: BenchRevertBody) -> dict[str, Any]:
    return await svc(request).bench_project(bench_id, "revert", key=body.key, n=body.n)


@router.post("/api/bench/{bench_id}/instruct")
async def bench_instruct(
    request: Request, bench_id: str, body: BenchInstructBody
) -> dict[str, Any]:
    return await svc(request).bench_project(
        bench_id, "instruct", window=body.window, text=body.text, worker=body.worker
    )


@router.post("/api/bench/{bench_id}/redo-stale")
async def bench_redo_stale(request: Request, bench_id: str) -> dict[str, Any]:
    return await svc(request).bench_project(bench_id, "redo_stale")


@router.post("/api/bench/{bench_id}/conflict")
async def bench_conflict(
    request: Request, bench_id: str, body: BenchConflictBody
) -> dict[str, Any]:
    return await svc(request).bench_project(bench_id, "conflict", choice=body.choice)


@router.post("/api/bench/{bench_id}/found/swap")
async def bench_found_swap(request: Request, bench_id: str, body: BenchFoundBody) -> dict[str, Any]:
    return await svc(request).bench_project(bench_id, "found_swap", item=body.item)


@router.post("/api/bench/{bench_id}/found/drop")
async def bench_found_drop(request: Request, bench_id: str, body: BenchFoundBody) -> dict[str, Any]:
    return await svc(request).bench_project(bench_id, "found_drop", item=body.item)


@router.post("/api/bench/{bench_id}/refind")
async def bench_refind(request: Request, bench_id: str) -> dict[str, Any]:
    return await svc(request).bench_project(bench_id, "refind")


@router.post("/api/bench/{bench_id}/evolve")
async def bench_evolve(request: Request, bench_id: str) -> dict[str, Any]:
    return await svc(request).bench_project(bench_id, "evolve")


@router.post("/api/bench/{bench_id}/plugin")
async def bench_plugin(request: Request, bench_id: str, body: BenchPluginBody) -> dict[str, Any]:
    return await svc(request).bench_project(bench_id, "plugin", name=body.name)


@router.post("/api/bench/{bench_id}/ask")
async def bench_ask(request: Request, bench_id: str, body: BenchAskBody) -> dict[str, Any]:
    return await svc(request).bench_ask(bench_id, body.question, body.as_json)


@router.get("/bench/{bench_id}/{path:path}")
async def bench_file(request: Request, bench_id: str, path: str) -> Response:
    full = svc(request).bench_file(bench_id, path)
    mime = mimetypes.guess_type(full.name)[0] or "application/octet-stream"
    if full.suffix in (".js", ".mjs"):
        mime = "text/javascript"
    csp = bench_csp(svc(request).bench_net_origins())
    headers = {"Cache-Control": "no-cache", "Content-Security-Policy": csp}
    headers["Access-Control-Allow-Origin"] = "*"  # 沙箱页面载入自己的模块和数据
    return Response(content=full.read_bytes(), media_type=mime, headers=headers)


@router.get("/api/super")
async def super_status(request: Request) -> dict[str, Any]:
    return svc(request).super_status()


@router.post("/api/super/tasks")
async def super_submit(request: Request, body: SuperTaskBody) -> dict[str, Any]:
    return svc(request).super_submit(body.text, body.queue)


@router.post("/api/super/cancel")
async def super_cancel(request: Request) -> dict[str, Any]:
    return await svc(request).super_cancel()


@router.get("/api/super/tasks/{task_id}")
async def super_task(request: Request, task_id: str) -> dict[str, Any]:
    return svc(request).super_task(task_id)


@router.put("/api/super/workers/{worker}/limit")
async def super_limit(request: Request, worker: str, body: SuperLimitBody) -> dict[str, Any]:
    return svc(request).super_set_limit(worker, body.limit)


@router.post("/api/super/workers/{worker}/wake")
async def super_wake(request: Request, worker: str) -> dict[str, Any]:
    return svc(request).super_clear_cooldown(worker)


@router.post("/api/super/schedules")
async def super_add_schedule(request: Request, body: SuperScheduleBody) -> dict[str, Any]:
    return svc(request).super_add_schedule(body.text, body.at, body.days, body.once)


@router.put("/api/super/schedules/{item_id}")
async def super_toggle_schedule(
    request: Request, item_id: str, body: SuperToggleBody
) -> dict[str, Any]:
    return svc(request).super_toggle_schedule(item_id, body.enabled)


@router.delete("/api/super/schedules/{item_id}")
async def super_remove_schedule(request: Request, item_id: str) -> dict[str, Any]:
    return svc(request).super_remove_schedule(item_id)


@router.post("/api/super/schedules/{item_id}/run")
async def super_run_schedule(request: Request, item_id: str) -> dict[str, Any]:
    return svc(request).super_run_schedule(item_id)


@router.post("/api/super/pending/{item_id}/approve")
async def super_approve(request: Request, item_id: str) -> dict[str, Any]:
    return svc(request).super_approve(item_id)


@router.post("/api/super/pending/{item_id}/reject")
async def super_reject(request: Request, item_id: str) -> dict[str, Any]:
    return svc(request).super_reject(item_id)


@router.delete("/api/super/queue/{item_id}")
async def super_unqueue(request: Request, item_id: str) -> dict[str, Any]:
    return svc(request).super_unqueue(item_id)


@router.get("/api/super/report")
async def super_report(request: Request, day: str | None = None) -> dict[str, Any]:
    return svc(request).super_report(day)


@router.get("/api/super/audit")
async def super_audit(request: Request) -> dict[str, Any]:
    return svc(request).super_audit()
