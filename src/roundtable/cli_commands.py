"""命令行的各个子命令（ask、bench、models、stats、evolve、stickers、note、history 等）。

从 cli.py 拆出（代码精简）。
"""

from __future__ import annotations

import argparse
import csv
import json
import secrets
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from roundtable.cli_labels import IMAGE_EXTS, KIND_LABELS, KIND_NAMES, STICKER_LABELS
from roundtable.core import ideaboard
from roundtable.core.allocation import display_name
from roundtable.core.attachments import UploadError, ingest
from roundtable.core.bench import (
    BenchError,
    BenchRunner,
    cost_per_correct,
    load_questions,
    parse_modes,
    summarize,
)
from roundtable.core.notes import NoteError, check_can_add, clean_note
from roundtable.core.orchestrator import OrchestratorError, RunResult
from roundtable.core.routing import Question, UserChoice, preview_estimates
from roundtable.core.service import RoundtableService, ServiceError
from roundtable.core.storage import NotFound


class CommandsMixin:
    """命令行的各个子命令（ask、bench、models、stats、evolve、stickers、note、history 等）。"""

    # --- 命令 ----------------------------------------------------------------------

    async def cmd_ask(
        self,
        question: str,
        choice: UserChoice,
        *,
        seed: int | None,
        details: bool,
        reveal: bool | None,
        anonymous: bool = False,
        attach: Sequence[str] = (),
        style_ref: bool = True,
    ) -> int:
        if not self.check_available():
            return 2
        ids = []
        for path in attach:
            try:
                data = Path(path).read_bytes()
                a = ingest(
                    Path(path).name,
                    data,
                    config=self.rt.config,
                    router=self.rt.router,
                    repo=self.rt.repo,
                    store=self.rt.files,
                )
            except OSError as exc:
                self.p(f"无法读取附件 {path}：{exc.strerror}")
                return 2
            except UploadError as exc:
                self.p(f"附件不可用：{exc}")
                return 2
            note = "，".join(a.warnings)
            self.p(
                f"附件：{a.name}（{KIND_LABELS.get(a.kind, a.kind)}）"
                + (f" ⚠ {note}" if note else "")
            )
            ids.append(a.id)
        self.p(f"题目：{question}\n")
        seed = secrets.randbelow(2**31) if seed is None else seed
        self.print_preview(question, choice, ids, seed=seed, anonymous=anonymous, brief=True)
        try:
            result = await self.orc.start(
                Question(question, (), 0, choice.media, choice.media_tier),
                choice,
                seed=seed,
                anonymous=anonymous,
                attachments=ids,
                style_refs=None if style_ref else [],
            )
        except OrchestratorError as exc:
            self.p(f"无法开始：{exc}")
            return 2
        return await self.finish(result, details=details, reveal=reveal)

    async def finish(self, result: RunResult, *, details: bool, reveal: bool | None) -> int:
        result = await self.drive(result)
        for w in result.warnings:
            self.p(f"⚠ {w}")
        if result.error:
            self.p(f"无法开始：{result.error}")
        self.show(result.session_id, details=details)
        if result.status != "completed":
            if result.status in ("paused", "running", "awaiting_confirmation"):
                self.p(f"\n可以用 roundtable resume {result.session_id} 继续。")
            return 1
        if not self.rt.repo.session_row(result.session_id)["anonymous"]:
            self.print_identities(result.session_id, "上桌的模型与每次调用")
            return 0
        if reveal is None:
            raw = self.ask("\n按回车揭晓身份，输入 n 跳过：").strip().lower()
            reveal = raw not in ("n", "no", "否")
        if reveal:
            self.reveal_identities(result.session_id)
        else:
            self.p(f"\n稍后可用 roundtable reveal {result.session_id} 揭晓。")
        return 0

    def check_available(self) -> bool:
        if self.rt.router.available_models():
            return True
        self.p(
            "没有可用的模型：请在项目根目录的 .env 中填写至少一个渠道的 key（参考 .env.example）。"
        )
        for channel, reason in sorted(self.rt.unavailable_channels.items()):
            self.p(f"  {channel}：{reason}")
        return False

    async def cmd_bench(self, args: argparse.Namespace) -> int:
        """对比实验：同一批题用不同模式各跑一遍，比较正确率、花费、耗时。"""
        if args.list:
            batches = self.rt.repo.bench_batches()
            if not batches:
                self.p("还没有做过对比实验。")
            for b in batches:
                self.p(
                    f"  {b['batch']}  {b['runs']} 次运行  ${b['cost_usd']:.4f}  {b['started_at']}"
                )
            return 0
        if args.report:
            return self._bench_report(args.report, args.output)
        if not self.check_available():
            return 2
        try:
            questions = load_questions(args.questions)
            modes = parse_modes(args.modes)
        except BenchError as exc:
            self.p(f"实验设置有误：{exc}")
            return 2
        if args.category:
            questions = [q for q in questions if q.category in args.category]
        if args.limit:
            questions = questions[: args.limit]
        if not questions:
            self.p("没有符合条件的题目。")
            return 2
        seed = args.seed if args.seed is not None else secrets.randbelow(2**31)
        batch = args.batch or datetime.now().strftime("bench-%Y%m%d-%H%M%S")
        self.p(
            f"实验 {batch}：{len(questions)} 道题 × {len(modes)} 种模式，花费上限 ${args.max_usd:g}"
        )
        self.print_bench_estimate(questions, modes, seed)
        runner = BenchRunner(self.orc, self.rt.repo)

        def line(r) -> None:
            mark = {True: "✓", False: "✗", None: "—"}[r.correct]
            self.p(
                f"  {r.question_id:<12} {r.workflow}/{r.tier:<9} {mark}  ${r.cost_usd:.4f}"
                f"  {r.seconds:.1f}s  {r.status}"
            )

        try:
            done = await runner.run(
                questions, modes, batch=batch, seed=seed, max_usd=args.max_usd, on_result=line
            )
        except BenchError as exc:
            self.p(f"实验设置有误：{exc}")
            return 2
        if len(done) < len(questions) * len(modes):
            self.p(f"\n已达到花费上限 ${args.max_usd:g}，提前停止（{len(done)} 次运行已保存）。")
        return self._bench_report(batch, args.output)

    def print_bench_estimate(self, questions, modes, seed: int) -> None:
        totals = {m.label: 0.0 for m in modes}
        for q in questions:
            _, options = preview_estimates(
                Question(q.text),
                config=self.rt.config,
                router=self.rt.router,
                seed=seed,
                workflows=sorted({m.workflow for m in modes}),
            )
            for m in modes:
                for o in options:
                    if o.workflow == m.workflow and o.plan == m.tier and o.estimate:
                        totals[m.label] += o.estimate.total_usd * m.samples
        self.p("预估花费：" + "；".join(f"{k} ${v:.4f}" for k, v in totals.items()))

    def _bench_report(self, batch: str, output: str | None) -> int:
        rows = self.rt.repo.bench_runs(batch)
        if not rows:
            self.p(f"没有找到实验 {batch}。")
            return 2
        summary = summarize(rows)
        self.p(f"\n实验 {batch} 汇总（只统计跑完的运行）：")
        self.p("  模式              类别      题数 答对 正确率  平均花费   平均耗时")
        for r in summary:
            self.p(
                f"  {r['workflow'] + '/' + r['tier']:<16} {r['category']:<9} {r['questions']:>3}"
                f" {r['correct']:>4} {r['accuracy']:>6.0%}  ${r['avg_cost_usd']:<8.4f} "
                f"{r['avg_seconds']:.1f}s"
            )
        for (workflow, tier), value in sorted(cost_per_correct(rows).items()):
            text = f"${value:.4f}" if value is not None else "没有答对的题"
            self.p(f"  {workflow}/{tier}：每答对一题平均花费 {text}")
        if output:
            target = Path(output)
            if target.suffix.lower() == ".json":
                target.write_text(
                    json.dumps({"runs": rows, "summary": summary}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            else:
                with target.open("w", encoding="utf-8-sig", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=list(rows[0]))
                    w.writeheader()
                    w.writerows(rows)
            self.p(f"已导出到 {target.resolve()}")
        return 0

    def cmd_mindmap(self, session_id: str, output: str | None) -> int:
        """保存头脑风暴的思维导图：按后缀 .svg（图）/ .md（点子榜 + 大纲）/ .mmd（Mermaid）。"""
        rows = self.rt.repo.outputs(session_id, kind="mindmap")
        if not rows:
            self.p("这场讨论没有思维导图（只有头脑风暴模式会生成）。")
            return 2
        data = json.loads(rows[-1]["content"])
        target = Path(output or f"roundtable-{session_id[:8]}-mindmap.svg")
        board = ""
        if data.get("board"):
            board = ideaboard.to_markdown(ideaboard.Board.from_dict(data["board"]))
        text = {".md": board + data["markdown"], ".mmd": data["mermaid"]}.get(
            target.suffix.lower(), data["svg"]
        )
        target.write_text(text, encoding="utf-8")
        self.p(f"已保存到 {target.resolve()}")
        return 0

    def cmd_models(self) -> int:
        available = {m.id for m in self.rt.router.available_models()}
        self.p(f"渠道模式：{self.rt.router.mode}")
        for m in self.rt.config.models.models:
            plan = self.rt.router.plan(m.id)
            channels = "、".join(r.channel for r in plan.usable) or "无"
            mark = "✓" if m.id in available else "✗"
            self.p(
                f"  {mark} {m.id:<22} {m.tier or '-':<8} ${m.price.input:g}/${m.price.output:g}"
                f"  可用渠道：{channels}"
            )
        if self.rt.unavailable_channels:
            self.p("缺少 key 的渠道：" + "、".join(sorted(self.rt.unavailable_channels)))
        month, day = self.rt.budget.status()
        self.p(month.describe() + (f"；{day.describe()}" if day else ""))
        return 0

    async def cmd_model_updates(self) -> int:
        catalog = self.rt.catalog
        if catalog is None:
            self.p("没有可用的模型列表来源。")
            return 1
        try:
            data = await catalog.refresh()
        except LookupError as e:
            self.p(str(e))
            return 1
        except Exception as e:  # noqa: BLE001 - 网络错误只报类型
            self.p(f"查询模型列表失败（{type(e).__name__}）。")
            return 1
        self.p(f"来源：{data['source']}（只提示，不会自动修改 config/models.yaml）")
        changes, missing, fresh = data["price_changes"], data["missing"], data["candidates"]
        if changes:
            self.p("价格变动（每百万 token，输入 / 输出）：")
            for c in changes:
                (ci, co), (ai, ao) = c["configured"], c["actual"]
                self.p(f"  {c['model_id']:<22} 配置 ${ci:g}/${co:g} → 实际 ${ai:g}/${ao:g}")
        if missing:
            self.p("找不到（可能已下架或改名）：")
            for m in missing:
                self.p(f"  {m['model_id']:<22} {m['route']}")
        if fresh:
            self.p("可能更合适的新模型：")
            for c in fresh:
                i, o = c["price"]
                tier = c["tier"] or "不在现有档位价格区间"
                swap = f"，可替换 {'、'.join(c['replaces'])}" if c["replaces"] else ""
                self.p(f"  {c['route']:<36} ${i:g}/${o:g}  建议档位：{tier}{swap}")
        if not (changes or missing or fresh):
            self.p("配置的模型都是最新的，价格也一致。")
        else:
            self.p("要采纳请改 config/models.yaml（或告诉我，确认后再改）。")
        return 0

    def cmd_stats(self) -> int:
        """跨会话按模型统计贡献（只统计身份已公开的会话）。"""
        rows = self.rt.repo.contribution_history()
        if not rows:
            self.p("还没有可统计的讨论（匿名讨论揭晓后才计入）。")
            return 0
        by_model: dict[str, dict[str, int]] = {}
        sessions: dict[str, int] = {}
        for r in rows:
            by_model.setdefault(r["model_id"], {})[r["kind"]] = r["amount"]
            sessions[r["model_id"]] = max(sessions.get(r["model_id"], 0), r["sessions"])
        for model, counts in by_model.items():
            parts = [f"{KIND_NAMES[k]} {counts[k]}" for k in KIND_NAMES if k in counts]
            name = f"{display_name(model, self.rt.config)}（{model}）"
            self.p(f"  {name:<28} {sessions[model]} 场  " + "，".join(parts))
        return 0

    async def _evolve_plugins(self, svc: RoundtableService, session_id: str) -> None:
        out = await svc.evolution_plugins(session_id)
        where = f"，保存为 插件清单/{out['file']}" if out["file"] else ""
        self.p(f"插件清单：{len(out['plugins'])} 个，花费 ${out['cost_usd']:.4f}{where}")
        for n, p in enumerate(out["plugins"], 1):
            self.p(f"  {n}. [{p['scene']}] {p['name']}：{p['what']}")

    async def cmd_evolve(
        self,
        action: str,
        session_id: str | None,
        kind: str | None = None,
        theme: str | None = None,
    ) -> int:
        """革新例会：status 看概况；meet 开一场、等它开完、写插件清单并导出采纳单；
        export 导出某场的采纳单；plugins 给某场写插件清单。"""
        svc = RoundtableService(self.rt)
        try:
            if action == "scout":
                found = await svc.evolution_scout()
                self.p(
                    f"找资料：搜了 {found['searched']} 次，{found['candidates']} 个白名单网页，"
                    f"花费 ${found['cost_usd']:.4f}"
                )
                for w in found["written"]:
                    self.p(f"  写进收件箱：{w['file']}  {w['title']}（{w['url']}）")
                for r in found["rejected"]:
                    self.p(f"  没写：{r['url']}（{r['reason']}）")
                return 0
            if action == "meet":
                started = svc.evolution_meet(kind=kind, theme=theme)
                session_id = started["session_id"]
                for note in started["notes"]:
                    self.p(f"  提示：{note}")
                major = started["kind"] == "major"
                label = f"大版本「{started['theme']}」" if major else "小版本（主题不限）"
                self.p(f"  {'' if started['override'] else '自动判断：'}{started['reason']}")
                self.p(
                    f"革新例会 {started['week']} {label} 开始：{len(started['cards'])} 张资料卡，"
                    f"预计 ${started['estimate_usd']:.4f}（会话 {session_id}）"
                )
                await svc.wait(session_id)
                status = self.rt.repo.session_row(session_id)["status"]
                if status != "completed":
                    self.p(
                        f"例会停在「{status}」。可以用 roundtable resume {session_id} 继续，"
                        f"开完后 roundtable evolve export {session_id} 导出采纳单。"
                    )
                    return 1
                if self.rt.config.roundtable.evolution.plugins.enabled:
                    try:
                        await self._evolve_plugins(svc, session_id)
                    except ServiceError as exc:
                        self.p(f"插件清单没有写成：{exc}")
                action = "export"
            if action == "plugins":
                if not session_id:
                    self.p("请给出例会的会话 id：roundtable evolve plugins <id>")
                    return 2
                await self._evolve_plugins(svc, session_id)
                return 0
            if action == "export":
                if not session_id:
                    self.p("请给出例会的会话 id：roundtable evolve export <id>")
                    return 2
                saved = svc.evolution_export(session_id)
                self.p(f"采纳单已保存到 {saved['path']}")
                return 0
        except ServiceError as exc:
            self.p(str(exc))
            return 2
        info = svc.evolution()
        off = "关闭（evolution.enabled，本机设置写在 config/roundtable.local.yaml）"
        state = "开启" if info["enabled"] else off
        self.p(f"革新例会：{state}")
        if info["enabled"] and info["auto"]:
            sch = info["schedule"]
            day = "一二三四五六日"[sch["weekday"] - 1]
            self.p(f"  自动开会：每周{day} {sch['hour']} 点之后（服务开着时）")
        self.p(f"  档位：{info['plan_label']}全员上桌，{'匿名' if info['anonymous'] else '不匿名'}")
        nxt = info["next"]
        theme = f"，主题「{nxt['theme']}」" if nxt.get("theme") else ""
        self.p(f"  下一场：{nxt['label']}（前 {nxt['top']} 个选项{theme}）——{nxt['reason']}")
        self.p(
            f"  本月已花 ${info['month_spent_usd']:.4f} / 上限 ${info['monthly_usd']:.2f}"
            f"（每场 ≤ ${info['meeting_usd']:.2f}）"
        )
        self.p(f"  收件箱：{info['inbox'] or '（没有数据目录）'}")
        for n, card in enumerate(info["cards"], 1):
            self.p(f"    {n}. {card['title']}（{card['file']}，{card['chars']} 字）")
        if not info["cards"]:
            self.p("    （没有资料卡）")
        for note in info["notes"]:
            self.p(f"    提示：{note}")
        for m in info["meetings"]:
            adopted = f"，采纳单 {m['adopted']}" if m["adopted"] else ""
            self.p(
                f"  {m['week']}  {m['session_id']}  {m['status']}  ${m['cost_usd']:.4f}{adopted}"
            )
            for n, idea in enumerate(m.get("ideas") or [], 1):
                decided = {"do": "  【要做】", "skip": "  【不做】"}.get(idea["decision"] or "", "")
                self.p(f"    {n}. {idea['id']} {idea['text'][:60]}（{idea['score']} 分）{decided}")
        return 0

    def cmd_performance(self, do_reset: bool) -> int:
        """办公室模式的员工绩效：按活的类型统计件数、被退回、验收通过率、平均工资与用时。"""
        from roundtable.core.office import collect, reset
        from roundtable.core.office.performance import KIND_LABELS, KINDS

        if do_reset:
            reset(self.rt.repo, self.rt.repo.clock())
            self.p("绩效已清零：之后只统计从现在起的办公室会话（不删除任何记录）。")
            return 0
        perf = collect(self.rt.repo)
        if not perf.by_model:
            self.p("还没有绩效记录：办公室模式办完事后再来看。")
            return 0
        if perf.since:
            self.p(f"从 {perf.since[:19]} 起统计")
        for model, kinds in perf.by_model.items():
            self.p(f"  {display_name(model, self.rt.config)}（{model}）")
            for kind in KINDS:
                st = kinds.get(kind)
                if st is None:
                    continue
                d = st.to_dict()
                rate = "" if st.pass_rate is None else f"，通过率 {st.pass_rate:.0%}"
                back = f"，退回 {st.returned}" if st.returned else ""
                self.p(
                    f"    {KIND_LABELS[kind]} {st.jobs} 件{rate}{back}，"
                    f"平均 ${d['avg_cost_usd']:.4f} / {d['avg_seconds']:.0f} 秒"
                )
        return 0

    def cmd_sticker_packs(
        self,
        action: str,
        target: str | None,
        owner: str | None,
        label: str | None,
        file: str | None,
    ) -> int:
        """外部表情包（dsh-meme 格式）：list 已装 / catalog 目录 / install / assign / remove。"""
        from roundtable.core.chat_actions import sticker_packs
        from roundtable.core.sticker_packs import PackError

        packs = sticker_packs(self.rt)

        def who(owner: str) -> str:
            return owner or "通用"

        try:
            if action == "catalog":
                for p in packs.catalog():
                    size = f"{p['bytes'] / 1048576:.1f} MB" if p["bytes"] else "?"
                    flag = "已装" if p["installed"] else ("" if p["installable"] else "不能直接装")
                    self.p(
                        f"{p['id']:24} {p['name']}（{p['count']} 张，{size}，{p['license']}）{flag}"
                    )
                return 0
            if action == "list":
                rows = packs.installed()
                if not rows:
                    self.p("还没装外部表情包：roundtable sticker-packs catalog 看在线目录")
                for p in rows:
                    s = packs.summary(p)
                    self.p(
                        f"{s['id']:24} 「{s['label']}」{s['count']} 张，给 {who(s['owner'])} 用"
                        f"（{s['license']}）例：{'、'.join(s['sample'][:5])}"
                    )
                return 0
            if not target:
                self.p("要给出包 id（install 也可以用 --file 给压缩包）")
                return 1
            if action == "install":
                data = Path(file).read_bytes() if file else None
                s = packs.install(target, owner, label or "", data)
                self.p(f"装好了「{s['label']}」{s['count']} 张，给 {who(s['owner'])} 用")
                return 0
            if action == "assign":
                s = packs.assign(target, owner, label)
                self.p(f"「{s['label']}」现在给 {who(s['owner'])} 用")
                return 0
            if action == "remove":
                packs.remove(target)
                self.p(f"删掉了 {target}")
                return 0
        except (PackError, OSError) as exc:
            self.p(str(exc))
            return 1
        self.p(f"不认识的操作 {action}")
        return 1

    async def cmd_stickers(
        self,
        member: str | None,
        draw: list[str],
        draw_all: bool,
        output: str | None,
        whiten: bool = False,
        prompts: str | None = None,
        import_dir: str | None = None,
    ) -> int:
        """成员专属表情包：列出状态；--draw / --all 画（新画的待审，到网页成员设定里审）；
        --prompts 导出提示词和参考图拿去别处画，--import 把画好的收进来（待审）。"""
        from roundtable.core.member_stickers import (
            MemberStickers,
            StickerBudgetExceeded,
            StickerError,
            StickerUnavailable,
        )

        stickers = MemberStickers(self.rt, self.rt.sticker_dir)
        cards = self.rt.config.personas.sticker_cards
        members = [member] if member else list(cards)
        if member and member not in cards:
            self.p(f"{member} 还没有形象卡和表情清单（personas.yaml 的 sticker_cards）")
            return 1
        names = (
            {
                m: draw or [r["name"] for r in stickers.listing(m) if r["status"] == "missing"]
                for m in members
            }
            if (draw or draw_all) and not prompts  # 导出提示词时 --draw 只用来挑哪几张
            else {}
        )
        if whiten:
            self.p(f"底色刷白了 {stickers.whiten(member)} 张（没重画，审核状态不变）")
        failed = 0
        if prompts:
            self._sticker_prompts(stickers, members, draw, Path(prompts))
        if import_dir:
            failed += self._sticker_import(stickers, members, Path(import_dir))
        for m, todo in names.items():
            for name in todo:
                try:
                    row = await stickers.draw(m, name)
                except StickerBudgetExceeded as exc:
                    self.p(str(exc))
                    return 1
                except (StickerError, StickerUnavailable) as exc:
                    self.p(f"  {m}·{name}：{exc}")
                    failed += 1
                    continue
                note = f"（{row['problems']}）" if row.get("problems") else ""
                self.p(f"  {m}·{name} 画好了，{STICKER_LABELS[row['status']]}{note}")
        for m in members:
            self.p(f"{m}")
            for row in stickers.listing(m):
                stale = "，形象卡改过，建议重画" if row.get("stale") else ""
                self.p(
                    f"  {row['name']}「{row['caption']}」 {STICKER_LABELS[row['status']]}{stale}"
                )
        self.p(f"本月表情包花费 ${stickers.month_spent():.4f}")
        if output:
            folder = Path(output)
            folder.mkdir(parents=True, exist_ok=True)
            saved = 0
            for m in members:
                for row in stickers.listing(m):
                    data = stickers.file(m, row["name"])
                    if data:
                        ext = "gif" if data[:3] == b"GIF" else "png"
                        (folder / f"{m}_{row['name']}.{ext}").write_bytes(data)
                        saved += 1
            self.p(f"已保存 {saved} 张到 {folder}")
        return 1 if failed else 0

    def _sticker_prompts(
        self, stickers: Any, members: list[str], names: list[str], folder: Path
    ) -> None:
        """每位成员一个子目录：参考图 + 每张一个「名字.txt」提示词（只导出还没画的或指定的）。"""
        count = 0
        for m in members:
            todo = names or [r["name"] for r in stickers.listing(m) if r["status"] == "missing"]
            if not todo:
                continue
            sub = folder / m
            sub.mkdir(parents=True, exist_ok=True)
            for i, ref in enumerate(stickers.references(m, stickers.card(m)), 1):
                (sub / f"参考图{i}_{ref.name}").write_bytes(ref.data)
            for name in todo:
                (sub / f"{name}.txt").write_text(stickers.prompt_text(m, name), encoding="utf-8")
                count += 1
        self.p(
            f"导出了 {count} 张的提示词到 {folder}：把参考图和提示词一起发给画图工具，"
            f"画好的图存成「表情名.png」放回同一个成员目录，再运行 --import {folder}"
        )

    def _sticker_import(self, stickers: Any, members: list[str], folder: Path) -> int:
        """收图：成员目录里（或直接在 folder 里，只有一位成员时）与表情同名的图片。"""
        from roundtable.core.member_stickers import StickerError

        failed = 0
        for m in members:
            sub = folder / m if (folder / m).is_dir() else folder if len(members) == 1 else None
            if sub is None:
                continue
            for row in stickers.listing(m):
                path = next(
                    (
                        sub / f"{row['name']}.{ext}"
                        for ext in IMAGE_EXTS
                        if (sub / f"{row['name']}.{ext}").is_file()
                    ),
                    None,
                )
                if path is None:
                    continue
                try:
                    stickers.import_image(m, row["name"], path.read_bytes())
                except StickerError as exc:
                    self.p(f"  {m}·{row['name']}：{exc}")
                    failed += 1
                    continue
                self.p(f"  {m}·{row['name']} 导入了，待审")
        return failed

    def cmd_note(self, session_id: str, text: str) -> int:
        """讨论进行中补充意见（可在另一个终端运行）：下一个步骤开始时被带上。"""
        repo = self.rt.repo
        try:
            row = repo.session_row(session_id)
            cleaned = clean_note(text)
            check_can_add(row["status"], len(repo.pending_notes(session_id)))
        except NotFound:
            self.p(f"找不到会话 {session_id}")
            return 1
        except NoteError as exc:
            self.p(str(exc))
            return 1
        repo.add_note(session_id, cleaned)
        self.p("已记下，下一个步骤开始时会带给统筹和组员。")
        return 0

    def cmd_history(self, limit: int) -> int:
        for s in self.rt.repo.list_sessions(limit):
            q = s.question if len(s.question) <= 30 else s.question[:30] + "…"
            self.p(f"{s.created_at[:19]}  {s.id}  {s.status:<22} ${s.cost_usd:.4f}  {q}")
        return 0
