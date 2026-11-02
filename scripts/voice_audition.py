"""成员试音：按 scripts/voice_candidates.yaml 给每位成员的候选音色各念一句，存成可以直接听的试音页。

用法（在项目根目录，.env 里要有 OPENROUTER_API_KEY；会产生少量费用，先 --dry-run 看预计花费）：
    python scripts/voice_audition.py --dry-run               # 只列计划和预计花费，不调用
    python scripts/voice_audition.py --max-usd 0.5           # 全部成员，花到 0.5 美元就停
    python scripts/voice_audition.py --member Anthropic      # 只试一位（可写多次）

结果在 --out（默认 data/_check/voices/试音/）：每个候选一个音频文件、index.html（浏览器打开逐个听）
和 results.json（成功 / 失败、实际花费）。音色名不存在时那一条记为失败，接着试下一条。

请求和正式运行走同一段适配器代码（OpenRouter 的 audio/speech）；只用语音模型自带的合成音色，
不克隆真人声音。带 style 的候选按 style_template 把语气描述放在台词前面一起发给语音模型；
带 line 的候选念自己的台词（如加了语气词、笑声叹气标记和停顿的「带感情」版本）。
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CANDIDATES = Path(__file__).resolve().parent / "voice_candidates.yaml"
OUT = ROOT / "data" / "_check" / "voices" / "试音"
CHANNEL = "openrouter"
EXT = {"audio/mpeg": "mp3", "audio/wav": "wav", "audio/ogg": "ogg"}


@dataclass
class Job:
    member: str
    name: str
    index: int
    model: str
    voice: str
    style: str
    note: str
    text: str
    estimate_usd: float
    status: str = "planned"  # planned / ok / failed / skipped
    file: str = ""
    cost_usd: float = 0.0
    error: str = ""


def load_candidates(path: Path) -> dict[str, Any]:
    """读候选文件并做基本检查；有问题抛 ValueError（带字段位置）。"""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    template = data.get("style_template", "")
    if "{style}" not in template or "{line}" not in template:
        raise ValueError(f"{path.name}: style_template 要同时有 {{style}} 和 {{line}}")
    members = data.get("members")
    if not isinstance(members, dict) or not members:
        raise ValueError(f"{path.name}: members 不能为空")
    for vendor, item in members.items():
        where = f"{path.name}: members.{vendor}"
        if not str(item.get("line", "")).strip():
            raise ValueError(f"{where}.line 不能为空")
        cands = item.get("candidates") or []
        if not cands:
            raise ValueError(f"{where}.candidates 不能为空")
        for i, cand in enumerate(cands):
            for key in ("model", "voice"):
                if not str(cand.get(key, "")).strip():
                    raise ValueError(f"{where}.candidates[{i}].{key} 不能为空")
    return data


def spoken_text(data: dict[str, Any], line: str, style: str) -> str:
    return data["style_template"].format(style=style, line=line) if style else line


def plan(data: dict[str, Any], config: Any, only: list[str] | None = None) -> list[Job]:
    """列出要合成的每一条和预计花费；模型不在 models.yaml 或没有 OpenRouter 路由时报错。"""
    from roundtable.core.media.pricing import speech_cost

    jobs: list[Job] = []
    for vendor, item in data["members"].items():
        if only and vendor not in only:
            continue
        for i, cand in enumerate(item["candidates"], 1):
            model = config.models.get(cand["model"])
            route = next((r for r in model.routes if r.channel == CHANNEL), None)
            if route is None:
                raise ValueError(f"{cand['model']} 没有 {CHANNEL} 路由")
            style = str(cand.get("style", "") or "")
            line = str(cand.get("line") or item["line"])  # 候选自己的台词：如加了语气词和停顿的版本
            text = spoken_text(data, line, style)
            jobs.append(
                Job(
                    member=vendor,
                    name=str(item.get("name", vendor)),
                    index=i,
                    model=model.id,
                    voice=str(cand["voice"]),
                    style=style,
                    note=str(cand.get("note", "")),
                    text=text,
                    estimate_usd=speech_cost(model, route, len(text)),
                )
            )
    return jobs


def file_stem(job: Job) -> str:
    voice = re.sub(r"[^0-9A-Za-z-]+", "_", job.voice).strip("_")
    member = re.sub(r"[^0-9A-Za-z-]+", "_", job.member).strip("_")
    return f"{member}_{job.index}_{voice}"


async def synthesize(provider: Any, config: Any, job: Job) -> tuple[bytes, str, float | None]:
    model = config.models.get(job.model)
    route = next(r for r in model.routes if r.channel == CHANNEL)
    params = {
        "response_format": config.roundtable.media.speech.format,
        **model.params_for(route),
        "voice": job.voice,
    }
    out = await provider.synthesize_speech(route.model, job.text, params)
    return out.media.data, out.media.mime, out.cost_usd


async def run_jobs(provider: Any, config: Any, jobs: list[Job], out: Path, max_usd: float) -> float:
    """逐条合成；预计会超过 max_usd 的跳过。返回实际花费合计。"""
    from roundtable.core.providers import ErrorKind, ProviderError

    out.mkdir(parents=True, exist_ok=True)
    spent = 0.0
    for job in jobs:
        if max_usd > 0 and spent + job.estimate_usd > max_usd:
            job.status, job.error = "skipped", f"再念就超过上限 ${max_usd:g}"
            continue
        try:
            data, mime, cost = await synthesize(provider, config, job)
        except ProviderError as exc:
            job.status, job.error = "failed", f"{exc.kind.value} {exc}"[:300]
            print(f"  失败 {job.name} #{job.index} {job.voice}：{job.error}")
            if exc.kind in (ErrorKind.AUTH, ErrorKind.QUOTA):  # key 或余额的问题，后面也不会成功
                break
            continue
        job.cost_usd = job.estimate_usd if cost is None else cost
        spent += job.cost_usd
        name = f"{file_stem(job)}.{EXT.get(mime, 'mp3')}"
        (out / name).write_bytes(data)
        job.status, job.file = "ok", name
        print(f"  好了 {job.name} #{job.index} {job.voice}（${job.cost_usd:.4f}）")
    return spent


def render_html(jobs: list[Job], spent: float) -> str:
    """试音页：按成员分组，每个候选一个播放器；不依赖网络。"""
    rows: list[str] = []
    current = None
    for job in jobs:
        if job.member != current:
            if current is not None:
                rows.append("</section>")
            current = job.member
            rows.append(f"<section><h2>{html.escape(job.name)}</h2>")
        label = f"#{job.index} {html.escape(job.voice)}（{html.escape(job.model)}）"
        note = html.escape(job.note)
        style = f"<div class=s>语气：{html.escape(job.style)}</div>" if job.style else ""
        style += f"<div class=s>念的是：{html.escape(job.text)}</div>"
        if job.status == "ok":
            player = f'<audio controls preload="none" src="{html.escape(job.file)}"></audio>'
        else:
            player = f"<div class=e>{html.escape(job.status)} {html.escape(job.error)}</div>"
        rows.append(f"<div class=c><b>{label}</b> {note}{style}{player}</div>")
    if current is not None:
        rows.append("</section>")
    body = "\n".join(rows)
    return (
        '<!doctype html><meta charset="utf-8"><title>成员试音</title>'
        "<style>body{font-family:sans-serif;max-width:860px;margin:24px auto;padding:0 16px}"
        "section{margin-bottom:28px}.c{margin:10px 0;padding:8px;border:1px solid #ddd;"
        "border-radius:8px}audio{display:block;width:100%;margin-top:6px}"
        ".s{color:#666;font-size:13px}.e{color:#b00}</style>"
        f"<h1>成员试音</h1><p>实际花费合计 ${spent:.4f}。听完告诉我每人选第几号。</p>\n{body}"
    )


def write_report(jobs: list[Job], out: Path, spent: float) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(render_html(jobs, spent), encoding="utf-8")
    report = {"spent_usd": spent, "jobs": [asdict(j) for j in jobs]}
    (out / "results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def run(args: argparse.Namespace) -> int:
    from roundtable.core.config import load_config

    root = Path(args.root)
    config = load_config(root / "config")
    data = load_candidates(Path(args.candidates))
    jobs = plan(data, config, args.member)
    total = sum(j.estimate_usd for j in jobs)
    print(f"共 {len(jobs)} 条，预计 ${total:.3f}")
    if args.dry_run:
        for j in jobs:
            print(f"  {j.name} #{j.index} {j.model} {j.voice} ${j.estimate_usd:.4f}")
        return 0

    from dotenv import load_dotenv

    from roundtable.core.providers import Secret
    from roundtable.core.providers.openai_compat import OpenAICompatProvider

    load_dotenv(root / ".env")
    spec = config.models.channels[CHANNEL]
    key = os.environ.get(spec.key_env or "")
    if not key:
        print(f"缺少 {spec.key_env}（写在 .env 里）", file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else root / "data" / "_check" / "voices" / "试音"
    provider = OpenAICompatProvider(CHANNEL, spec, Secret(key), timeout_s=120)
    try:
        spent = await run_jobs(provider, config, jobs, out, args.max_usd)
    finally:
        await provider.aclose()
    write_report(jobs, out, spent)
    ok = sum(j.status == "ok" for j in jobs)
    print(f"完成 {ok}/{len(jobs)} 条，实际 ${spent:.4f}；打开 {out / 'index.html'} 试听")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--candidates", default=str(CANDIDATES), help="候选文件")
    parser.add_argument(
        "--root", default=str(ROOT), help="项目根目录（读 config/ 和 .env；脚本放在别处时用）"
    )
    parser.add_argument("--member", action="append", help="只试这位成员（vendor，可写多次）")
    parser.add_argument("--out", help="输出目录（默认 <root>/data/_check/voices/试音）")
    parser.add_argument("--max-usd", type=float, default=0.5, help="花费上限（0 = 不限）")
    parser.add_argument("--dry-run", action="store_true", help="只列计划和预计花费")
    return asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
