"""成员专属表情包：照着成员的形象参考图画 Q 版贴纸，存在本机，审过后成员才会用。

- 每位成员（键为厂商，和聊天成员 id 相同）在 personas.yaml 的 ``sticker_cards`` 里有一张形象卡：
  外观要点、必画细节、参考图，和专属表情清单（表情名、画进图里的配文、动作）。
- 画图只用 ``media.stickers.tier`` 档带 image_edit（能看参考图）的绘图模型，按标签选，不看模型名；
  带的偏好标签（``prefer_tags``，默认 zh、vision）越多越先试。
- 画完用最便宜的识图模型核对配文是否一字不差、人物像不像；不合格换下一个模型重画，
  最多 ``max_attempts`` 次，都不合格时留下最后一张并标出问题，交给你审。
- 花费记在隐藏会话（workflow = 'sticker'）上，role = media：同时计入表情包的每月小上限
  （``media.stickers.monthly_usd``）、创作输出的每月上限和总预算。
- 图片存在数据库旁的 ``stickers/``，索引是同目录的 ``index.json``；不进 git。
  新画的先是「待审」（review 打开时），你点「可以」才进成员的表情清单。

聊天里怎么发、怎么显示由聊天那边接：``MemberStickers.approved(member)`` 给出审过的表情。
"""

from __future__ import annotations

import hashlib
import io
import json
import random
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from roundtable.core.allocation import cheapest
from roundtable.core.attachments.ingest import capable
from roundtable.core.budget.guard import EPS, month_window
from roundtable.core.config import ModelSpec
from roundtable.core.config.schema import StickerCard, StickerSpec
from roundtable.core.jsonout import JSONOutputError, extract_json_object
from roundtable.core.media.draw_order import rank_draw_models
from roundtable.core.media.select import media_group
from roundtable.core.members_art import load_members, mime_of, resolve_file
from roundtable.core.providers import (
    AllChannelsFailed,
    Completion,
    Media,
    Message,
    NoChannelAvailable,
    ProviderError,
)
from roundtable.core.providers.cost import estimate_cost, image_cost

INDEX_FILE = "index.json"
DRAW_STEP = "member_sticker"
CHECK_STEP = "sticker_check"
PHOTO_STEP = "moment_photo"
IMPORTED = "imported"  # 在别处画好、导入进来的（model 字段）
STATUSES = ("pending", "approved", "rejected")
# members.json 里当参考图用的图片，按顺序取（找不到 refs 时）
REF_KEYS = ("standee", "chibi", "avatar")
REF_MAX_SIDE = 1024  # 参考图缩到这个边长以内再发
# 底色刷白：边框上最暗通道 ≥ 这个值、通道差 ≤ 这个值才算底色；刷的时候允许和起点差这么多
BG_MIN_LEVEL = 200
BG_MAX_TINT = 16
BG_THRESH = 36
MARK = (255, 0, 255, 0)  # 刷底时临时用的记号色
CHECK_INPUT_TOKENS = 2500  # 识图核对一次的输入 token 估计（几张图 + 提示词）


class StickerError(Exception):
    """表情名不存在、成员没有形象卡等用户能改的问题。"""


class StickerUnavailable(Exception):
    """没有可用的绘图模型、没有参考图，或画图失败。"""


class StickerBudgetExceeded(Exception):
    """再画就会超过表情包或创作输出的每月上限。"""


@dataclass(frozen=True)
class Check:
    caption_ok: bool
    looks_ok: bool
    problems: str

    @property
    def ok(self) -> bool:
        return self.caption_ok and self.looks_ok


def card_hash(card: StickerCard, spec: StickerSpec) -> str:
    """形象卡里影响这一张的内容：变了就该重画。"""
    parts: list[Any] = [card.look, card.must, card.refs, spec.caption, spec.action]
    if spec.frames:  # 静态贴纸不带这一项，原来画好的不会因此变成「该重画」
        parts.append(spec.frames)
    raw = json.dumps(parts, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class _Index:
    """index.json：{"items": {成员: {表情名: 条目}}}。目录为空时只存在内存里。"""

    def __init__(self, root: Path | None) -> None:
        self.root = root
        self._memory: dict[str, Any] = {"items": {}}
        self._files: dict[str, bytes] = {}

    def load(self) -> dict[str, Any]:
        if self.root is None:
            return self._memory
        try:
            data = json.loads((self.root / INDEX_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"items": {}}
        return data if isinstance(data.get("items"), dict) else {"items": {}}

    def save(self, data: dict[str, Any]) -> None:
        if self.root is None:
            self._memory = data
            return
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / f"{INDEX_FILE}.tmp"
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.root / INDEX_FILE)

    def write(self, name: str, data: bytes) -> None:
        if self.root is None:
            self._files[name] = data
            return
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / name).write_bytes(data)

    def read(self, name: str) -> bytes | None:
        if self.root is None:
            return self._files.get(name)
        if "/" in name or "\\" in name or name.startswith("."):
            return None
        try:
            return (self.root / name).read_bytes()
        except OSError:
            return None


class MemberStickers:
    def __init__(self, rt: Any, root: Path | None = None) -> None:
        self.rt = rt
        self._index = _Index(root)

    # --- 配置 ------------------------------------------------------------------------

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.media.stickers

    def card(self, member: str) -> StickerCard:
        card = self.rt.config.personas.sticker_cards.get(member)
        if card is None:
            raise StickerError("这位成员还没有形象卡和表情清单")
        return card

    def spec(self, member: str, name: str) -> StickerSpec:
        for s in self.card(member).stickers:
            if s.name == name:
                return s
        raise StickerError(f"没有叫「{name}」的表情")

    def draw_models(self) -> list[ModelSpec]:
        """能看参考图的绘图模型，按出图顺序排（本地 → 不花钱 → 按价，在歇的放最后）。"""
        media = self.rt.config.roundtable.media
        if not media.enabled or not self.rules.enabled:
            return []
        group = [
            m
            for m in media_group(self.rt.router, "image", self.rules.tier)
            if "image_edit" in m.tags
        ]
        # 出图顺序：本地 → 不花钱 → 按价；同档里带的偏好标签越多越先试（见 media/draw_order.py）
        return rank_draw_models(self.rt.router, group, self.rules.prefer_tags)

    def sticker_models(self) -> list[ModelSpec]:
        """画表情包用的：只要吃自然语言提示词的（表情包没有标签版提示词）。"""
        return [m for m in self.draw_models() if m.image_prompt == "text"]

    def local_only(self, models: list[ModelSpec]) -> list[ModelSpec]:
        """只留渠道全是本地（kind: local）的模型。"""
        channels = self.rt.config.models.channels
        return [m for m in models if all(channels[r.channel].kind == "local" for r in m.routes)]

    # --- 查看 ------------------------------------------------------------------------

    def listing(self, member: str) -> list[dict[str, Any]]:
        """每个表情的状态：missing 还没画 / pending 待审 / approved 可以用 / rejected 不要。"""
        card = self.card(member)
        drawn = self._index.load()["items"].get(member, {})
        out = []
        for spec in card.stickers:
            e = drawn.get(spec.name)
            row: dict[str, Any] = {
                "name": spec.name,
                "caption": spec.caption,
                "status": "missing",
                "animated": bool(spec.frames),
            }
            if e:
                row.update(
                    status=e["status"],
                    problems=e.get("problems", ""),
                    stale=e.get("card") != card_hash(card, spec),
                    drawn_at=e.get("at"),
                    version=e["file"][:8],
                )
            out.append(row)
        return out

    def file(self, member: str, name: str) -> bytes | None:
        e = self._index.load()["items"].get(member, {}).get(name)
        return self._index.read(e["file"]) if e else None

    def approved(self, member: str) -> list[dict[str, Any]]:
        """审过可以用的表情：[{name, caption, file}]（file 是 stickers/ 里的文件名）。"""
        drawn = self._index.load()["items"].get(member, {})
        card = self.rt.config.personas.sticker_cards.get(member)
        names = [s.name for s in card.stickers] if card else []
        return [
            {"name": n, "caption": drawn[n]["caption"], "file": drawn[n]["file"]}
            for n in names
            if n in drawn and drawn[n]["status"] == "approved"
        ]

    def uses(self, member: str) -> dict[str, str]:
        """审过可以用的表情各自什么时候发：{表情名: 用法}（personas 里没写用法的不列）。"""
        card = self.rt.config.personas.sticker_cards.get(member)
        ok = {s["name"] for s in self.approved(member)}
        return {s.name: s.use for s in card.stickers if s.name in ok and s.use} if card else {}

    def means(self, member: str) -> dict[str, list[str]]:
        """审过可以用的表情各自的意思等于哪些话：{表情名: [词]}。没写 means 的用配文（去掉标点）。
        发出前去重用：一句话里已经说了这些词，再配这张表情就重复了。"""
        card = self.rt.config.personas.sticker_cards.get(member)
        ok = {s["name"] for s in self.approved(member)}
        if not card:
            return {}
        return {
            s.name: list(s.means) or [w for w in [_letters(s.caption)] if w]
            for s in card.stickers
            if s.name in ok
        }

    def review(self, member: str, name: str, status: str) -> dict[str, Any]:
        if status not in STATUSES:
            raise StickerError("状态只能是 pending / approved / rejected")
        self.spec(member, name)
        data = self._index.load()
        e = data["items"].get(member, {}).get(name)
        if e is None:
            raise StickerError("这个表情还没画")
        e["status"] = status
        self._index.save(data)
        return {"name": name, "status": status}

    def whiten(self, member: str | None = None) -> int:
        """把已画好的表情底色统一刷成纯白（不重画、不花钱、状态不变），返回改了几张。"""
        data = self._index.load()
        changed = 0
        for m, rows in data["items"].items():
            if member and m != member:
                continue
            for e in rows.values():
                old = self._index.read(e["file"])
                if not old or old[:3] == b"GIF":  # 动图画的时候每格已经刷过
                    continue
                image = _square(old, self.rules.size)
                if image == old:
                    continue
                file = hashlib.sha256(image).hexdigest()[:24] + ".png"
                self._index.write(file, image)
                e["file"] = file
                changed += 1
        if changed:
            self._index.save(data)
        return changed

    # --- 花费 ------------------------------------------------------------------------

    def _window(self) -> tuple[datetime, datetime]:
        return month_window(datetime.fromisoformat(self.rt.repo.clock()))

    def month_spent(self) -> float:
        return self.rt.repo.spent_in_workflow("sticker", *self._window())

    def _check_budget(self, estimate: float) -> None:
        limit = self.rules.monthly_usd
        if limit > 0 and self.month_spent() + estimate > limit + EPS:
            raise StickerBudgetExceeded(f"本月表情包的额度（${limit:g}）快用完了，这张先不画")
        creative = self.rt.config.roundtable.media.monthly_usd
        if creative > 0:
            spent = self.rt.repo.spent_in_role("media", *self._window())
            if spent + estimate > creative + EPS:
                raise StickerBudgetExceeded(f"本月创作输出快超过上限 ${creative:g}，这张先不画")

    # --- 画 --------------------------------------------------------------------------

    def references(self, member: str, card: StickerCard) -> list[Media]:
        root = self.rt.members_dir
        paths: list[Path] = []
        for rel in card.refs:
            # 设定图可以放在素材目录里，也可以放在它的上一级（如 data/avatars/设定图/）
            bases = (root, root.parent) if root is not None else ()
            path = next((p for p in (resolve_file(b, rel) for b in bases) if p), None)
            if path and path not in paths:
                paths.append(path)
        # 再补上素材里的立绘 / 小人 / 头像：设定图管配色和细节，小人图管 Q 版画风
        for path in _art_refs(root, member):
            if path not in paths:
                paths.append(path)
        out = []
        for path in paths[:3]:
            try:
                data, mime = _shrink(path.read_bytes(), mime_of(path))
            except OSError:
                continue
            out.append(Media("image", mime, data, path.name))
        return out

    def _prompt(self, card: StickerCard, spec: StickerSpec) -> Any:
        if spec.frames:  # 动图：一次画 2×2 四格，切开后连起来
            frames = "\n".join(f"Panel {i}: {f}" for i, f in enumerate(spec.frames, 1))
            return self.rt.prompts.render(
                "sticker_anim",
                self.rt.config.roundtable.prompts["sticker_anim"],
                look=card.look,
                must="、".join(card.must) or "无",
                caption=spec.caption,
                frames=frames,
            )
        return self.rt.prompts.render(
            "sticker_draw",
            self.rt.config.roundtable.prompts["sticker_draw"],
            look=card.look,
            must="、".join(card.must) or "无",
            caption=spec.caption,
            action=spec.action,
        )

    def prompt_text(self, member: str, name: str) -> str:
        """这张表情的绘图提示词（拿去别处画时用，配上 references 里的参考图）。"""
        return "\n\n".join(
            m.content for m in self._prompt(self.card(member), self.spec(member, name)).messages
        )

    def import_image(self, member: str, name: str, data: bytes) -> dict[str, Any]:
        """把在别处画好的图收进来：和自己画的一样刷白、做成正方形（动图给 2×2 四格图，
        或现成的 GIF），存成待审；不花钱、不核对。"""
        card = self.card(member)
        spec = self.spec(member, name)
        if spec.frames:
            cells = [_square(c, self.rules.size) for c in _frames(data)]
            image = _gif(cells, self.rules.frame_ms)
        else:
            image = _square(data, self.rules.size)
            if image == data and not _is_image(data):
                raise StickerError("读不了这张图")
        self._store(member, card, spec, image, None, IMPORTED, 0.0)
        return next(r for r in self.listing(member) if r["name"] == name)

    def _store(
        self,
        member: str,
        card: StickerCard,
        spec: StickerSpec,
        image: bytes,
        check: Check | None,
        model: str,
        cost: float,
    ) -> None:
        ext = ".gif" if image[:3] == b"GIF" else ".png"
        file = hashlib.sha256(image).hexdigest()[:24] + ext
        self._index.write(file, image)
        data = self._index.load()
        data["items"].setdefault(member, {})[spec.name] = {
            "caption": spec.caption,
            "file": file,
            "status": "pending" if self.rules.review else "approved",
            "problems": self._problems(check),
            "checked": check is not None,
            "card": card_hash(card, spec),
            "model": model,
            "cost_usd": round(cost, 6),
            "at": self.rt.repo.clock(),
        }
        self._index.save(data)

    async def draw(self, member: str, name: str) -> dict[str, Any]:
        """画（或重画）一张。返回这张的状态；花费计入两个每月上限。"""
        card = self.card(member)
        spec = self.spec(member, name)
        models = self.sticker_models()
        if not models:
            raise StickerUnavailable("没有能看参考图的绘图模型")
        refs = self.references(member, card)
        if not refs:
            raise StickerUnavailable("找不到这位成员的形象参考图（素材目录里的设定图、立绘或头像）")
        prompt = self._prompt(card, spec)
        text = "\n\n".join(m.content for m in prompt.messages)
        session = self.rt.repo.sticker_session()
        attempts = [models[i % len(models)] for i in range(self.rules.max_attempts)]
        image: bytes | None = None
        check: Check | None = None
        used: ModelSpec | None = None
        total = 0.0
        for model in attempts:
            self._check_budget(image_cost(model, None, 1) + self._check_estimate())
            try:
                inv = await self.rt.router.invoke(
                    model.id,
                    lambda prov, route, m: prov.generate_image(
                        route.model,
                        text,
                        dict(m.params_for(route)),
                        images=tuple(refs),
                        api=m.image_api_for(route),
                    ),
                )
            except (AllChannelsFailed, NoChannelAvailable) as exc:
                self.rt.repo.record_call(
                    session,
                    step=DRAW_STEP,
                    role="media",
                    model_id=model.id,
                    messages=[Message("user", text)],
                    prompt=prompt,
                    failure=exc,
                )
                continue
            out = inv.result
            cost = (
                out.cost_usd
                if out.cost_usd is not None
                else image_cost(model, inv.route, 1, out.input_tokens, out.output_tokens)
            )
            total += cost
            self._record(session, DRAW_STEP, model, text, inv, cost, out.cost_usd is not None)
            if not out.images:
                continue
            raw, used = out.images[0].data, model
            if spec.frames:
                cells = [_square(c, self.rules.size) for c in _quarters(raw)]
                image, first = _gif(cells, self.rules.frame_ms), cells[0]
            else:
                image = first = _square(raw, self.rules.size)
            check = await self._check(session, first, spec, card, refs)  # 动图核对第一格
            if check is None or check.ok:
                break
        if image is None or used is None:
            raise StickerUnavailable("画图失败（所有模型都没画出来）")
        self._store(member, card, spec, image, check, used.id, total)
        return next(r for r in self.listing(member) if r["name"] == name)

    # --- 朋友圈配图 --------------------------------------------------------------------

    async def photo(
        self,
        member: str,
        scene: str,
        post: str,
        shot: str,
        *,
        refs: bool = True,
        seed: str = "",
        her: str = "",
        shot_tags: str = "",
    ) -> tuple[bytes, str]:
        """照成员的形象卡和参考图画一张朋友圈配图（随手拍，不审、不存进表情索引）。
        她不入镜的拍法传 ``refs=False``：不给参考图，免得模型硬把她画进去。
        吃标签的绘图模型（``image_prompt: tags``）改用标签版提示词：人物标签按拍法的 ``her``
        （none / part / full，不传时按 ``refs`` 当 full / none），拍法标签用 ``shot_tags``。
        返回 (JPEG, mime)；花费和表情包算在同一个隐藏会话和每月上限里。"""
        rules = self.rt.config.roundtable.media.moment_photos
        card = self.card(member)
        models = self.draw_models()
        if rules.local_only:
            models = self.local_only(models)
        if not rules.enabled or not models:
            raise StickerUnavailable("没有能看参考图的绘图模型")
        images = self.references(member, card) if refs else []
        if refs and not images:
            raise StickerUnavailable("找不到这位成员的形象参考图")
        prompt = self.rt.prompts.render(
            "moment_photo",
            self.rt.config.roundtable.prompts["moment_photo"],
            look=card.look,
            must="、".join(card.must) or "无",
            shot=shot,
            scene=scene,
            post=post,
        )
        text = "\n\n".join(m.content for m in prompt.messages)
        subject = rules.tag_subjects.get(her or ("full" if refs else "none"), "")
        tags = self._photo_tags(card, subject, shot_tags, refs)
        session = self.rt.repo.sticker_session()
        for model in models[: self.rules.max_attempts]:  # 画不出来时换下一个模型
            self._check_budget(image_cost(model, None, 1))
            body = tags if model.image_prompt == "tags" else text
            try:
                inv = await self.rt.router.invoke(
                    model.id,
                    lambda prov, route, m, body=body: prov.generate_image(
                        route.model,
                        body,
                        dict(m.params_for(route)),
                        images=tuple(images),
                        api=m.image_api_for(route),
                    ),
                )
            except (AllChannelsFailed, NoChannelAvailable):
                continue
            out = inv.result
            cost = (
                out.cost_usd
                if out.cost_usd is not None
                else image_cost(model, inv.route, 1, out.input_tokens, out.output_tokens)
            )
            self._record(session, PHOTO_STEP, model, body, inv, cost, out.cost_usd is not None)
            if out.images:
                data = out.images[0].data
                if rules.phone_look:
                    data = phone_look(data, random.Random(f"phone:{seed or post}"))
                return _jpeg(data, rules.max_side), "image/jpeg"
        raise StickerUnavailable("画图失败（所有模型都没画出来）")

    def _photo_tags(self, card: StickerCard, subject: str, shot_tags: str, her_in: bool) -> str:
        """标签版配图提示词（只取 user 段：正向一行 + negative 一行）。她不入镜时不放外观标签；
        形象卡没写 look_tags 时也不放（中文外观描述标签模型读不懂，靠参考图认人）。"""
        prompt = self.rt.prompts.render(
            "moment_photo_tags",
            self.rt.config.roundtable.prompts["moment_photo_tags"],
            subject=subject,
            shot=shot_tags,
            look=card.look_tags if her_in else "",
        )
        return tidy_tags(prompt.messages[-1].content)

    # --- 核对 ------------------------------------------------------------------------

    def _problems(self, check: Check | None) -> str:
        if check is None:
            # 有识图模型却没核对上（调用失败或没给出结果）：不挡住，但提醒你自己看
            return "自动核对没给出结果，请自己看一下" if self._check_model() else ""
        return "" if check.ok else check.problems

    def _check_model(self) -> ModelSpec | None:
        if not self.rules.check:
            return None
        pool = capable(self.rt.router, "vision")
        if not pool:
            return None
        import random

        return cheapest(
            pool,
            random.Random(0),
            input_tokens=CHECK_INPUT_TOKENS,
            output_tokens=self.rules.check_max_tokens,
        )

    def _check_estimate(self) -> float:
        model = self._check_model()
        if model is None:
            return 0.0
        return estimate_cost(model.price, CHECK_INPUT_TOKENS, self.rules.check_max_tokens)

    async def _check(
        self, session: str, image: bytes, spec: StickerSpec, card: StickerCard, refs: list[Media]
    ) -> Check | None:
        """核对配文和形象；没有识图模型或核对失败时返回 None（不挡住，交给你审）。"""
        model = self._check_model()
        if model is None:
            return None
        prompt = self.rt.prompts.render(
            "sticker_check",
            self.rt.config.roundtable.prompts["sticker_check"],
            caption=spec.caption,
            look=card.look,
        )
        media = (Media("image", "image/png", image, "sticker.png"), *refs)
        messages = tuple(
            Message(m.role, m.content, media) if m.role == "user" else m for m in prompt.messages
        )
        params = {"max_tokens": self.rules.check_max_tokens, "reasoning": {"effort": "low"}}
        try:
            completion = await self.rt.router.complete(model.id, messages, params)
        except (AllChannelsFailed, NoChannelAvailable, ProviderError) as exc:
            self.rt.repo.record_call(
                session,
                step=CHECK_STEP,
                role="media",
                model_id=model.id,
                messages=messages,
                prompt=prompt,
                failure=exc,
            )
            return None
        self.rt.repo.record_call(
            session,
            step=CHECK_STEP,
            role="media",
            model_id=model.id,
            messages=messages,
            prompt=prompt,
            completion=completion,
        )
        try:
            data = extract_json_object(completion.text)
        except JSONOutputError:
            return None
        problems = str(data.get("problems") or "")[:120]
        caption_ok = data.get("caption_ok") is True
        seen = data.get("text_seen")
        # 模型抄下来的字和配文对不上就算不合格（不全信它自己的判断）
        if isinstance(seen, str) and _letters(seen) != _letters(spec.caption):
            caption_ok = False
            problems = problems or f"图上的字是「{seen[:20]}」，不是「{spec.caption}」"
        return Check(
            caption_ok=caption_ok, looks_ok=data.get("looks_ok") is True, problems=problems
        )

    def _record(
        self,
        session: str,
        step: str,
        model: ModelSpec,
        text: str,
        inv: Any,
        cost: float,
        reported: bool,
    ) -> None:
        completion = Completion(
            text="",
            model_id=model.id,
            channel=inv.channel,
            channel_kind=inv.channel_kind,
            route_model=inv.route.model,
            input_tokens=0,
            output_tokens=0,
            cached_tokens=0,
            cost_usd=cost,
            cost_source="reported" if reported else "estimated",
            latency_s=getattr(inv, "latency_s", 0.0),
            attempts=getattr(inv, "attempts", ()),
        )
        self.rt.repo.record_call(
            session,
            step=step,
            role="media",
            model_id=model.id,
            messages=[Message("user", text)],
            completion=completion,
        )


# --- 图片处理 ----------------------------------------------------------------------


def _letters(text: str) -> str:
    """只留文字（去掉标点、空白、省略号），用来比对配文。"""
    return "".join(ch for ch in text if ch.isalnum() or ch == "□")


def _art_refs(root: Path | None, member: str) -> list[Path]:
    """没有 refs 时从 members.json 取：立绘、小人、头像（键名以这些开头，尺寸图取最大的）。"""
    found: list[Path] = []
    for m in load_members(root).members:
        if m.vendor.strip().lower() != member.strip().lower():
            continue
        for prefix in REF_KEYS:
            for key, value in m.images.items():
                if not key.startswith(prefix):
                    continue
                if isinstance(value, dict):
                    value = next((value[s] for s in ("256", "128", "64") if value.get(s)), None)
                path = resolve_file(root, value) if isinstance(value, str) else None
                if path and path not in found:
                    found.append(path)
        break
    return found


def _shrink(data: bytes, mime: str) -> tuple[bytes, str]:
    """参考图太大时缩到 REF_MAX_SIDE 以内（转 JPEG）；读不了就原样返回。"""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            if max(im.size) <= REF_MAX_SIDE:
                return data, mime
            im = im.convert("RGB")
            im.thumbnail((REF_MAX_SIDE, REF_MAX_SIDE))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=88)
            return buf.getvalue(), "image/jpeg"
    except Exception:  # noqa: BLE001 - 坏图、不认识的格式：原样发
        return data, mime


def _square(data: bytes, size: int) -> bytes:
    """居中放进 size×size 的白底正方形，存 PNG；读不了时原样返回。"""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            im = _white_background(im.convert("RGBA"))
            im.thumbnail((size, size))
            canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))
            canvas.alpha_composite(im, ((size - im.width) // 2, (size - im.height) // 2))
            buf = io.BytesIO()
            canvas.convert("RGB").save(buf, "PNG", optimize=True)
            return buf.getvalue()
    except Exception:  # noqa: BLE001 - 读不了的图：原样存
        return data


def tidy_tags(text: str) -> str:
    """去掉标签提示词里因变量为空留下的空项（", ,"），每行各自整理。"""
    lines = []
    for line in text.splitlines():
        head, sep, rest = line.partition(":")
        items = [t.strip() for t in (rest if sep else head).split(",")]
        joined = ", ".join(t for t in items if t)
        lines.append(f"{head}: {joined}" if sep else joined)
    return "\n".join(lines)


def phone_look(data: bytes, rng: random.Random) -> bytes:
    """本机后期，让画出来的图更像手机随手拍（不花钱）：裁掉一点让主体偏离中心、轻微歪斜、
    饱和度和对比度压低一点、偏一点冷 / 暖、暗部噪点、中等质量压缩。读不了时原样返回。"""
    try:
        from PIL import Image, ImageEnhance, ImageFilter

        with Image.open(io.BytesIO(data)) as src:
            im = src.convert("RGB")
        w, h = im.size
        left, top = int(w * rng.uniform(0, 0.07)), int(h * rng.uniform(0, 0.07))
        right, bottom = w - int(w * rng.uniform(0, 0.07)), h - int(h * rng.uniform(0, 0.07))
        im = im.crop((left, top, right, bottom))
        angle = rng.uniform(-2.0, 2.0)
        if abs(angle) > 0.3:  # 转了之后再裁掉四角的空白
            cw, ch = im.size
            im = im.rotate(angle, resample=Image.BICUBIC, expand=False)
            m = int(max(cw, ch) * abs(angle) / 57.3) + 1
            im = im.crop((m, m, cw - m, ch - m))
        im = ImageEnhance.Color(im).enhance(rng.uniform(0.78, 0.92))
        im = ImageEnhance.Contrast(im).enhance(rng.uniform(0.9, 0.97))
        r, g, b = im.split()
        warm = rng.uniform(-6, 6)
        r = r.point(lambda v: max(0, min(255, int(v + warm))))
        b = b.point(lambda v: max(0, min(255, int(v - warm))))
        im = Image.merge("RGB", (r, g, b))
        grain = Image.frombytes("L", im.size, rng.randbytes(im.width * im.height))
        noise = Image.merge("RGB", (grain, grain, grain)).filter(ImageFilter.GaussianBlur(0.6))
        im = Image.blend(im, noise, rng.uniform(0.03, 0.05)).filter(
            ImageFilter.UnsharpMask(radius=1, percent=40, threshold=2)
        )
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=rng.randint(74, 84))
        return buf.getvalue()
    except Exception:  # noqa: BLE001 - 读不了的图：原样存
        return data


def _jpeg(data: bytes, max_side: int) -> bytes:
    """缩到长边不超过 max_side，存成 JPEG（朋友圈照片不需要透明）；读不了时原样返回。"""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            im = im.convert("RGB")
            im.thumbnail((max_side, max_side))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=88)
            return buf.getvalue()
    except Exception:  # noqa: BLE001 - 读不了的图：原样存
        return data


def _quarters(data: bytes) -> list[bytes]:
    """把 2×2 四格图切成四张（左上、右上、左下、右下，PNG）；读不了时四格都是原图。"""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            im = im.convert("RGBA")
            w, h = im.size
            out = []
            for box in ((0, 0, w // 2, h // 2), (w // 2, 0, w, h // 2)) + (
                (0, h // 2, w // 2, h),
                (w // 2, h // 2, w, h),
            ):
                buf = io.BytesIO()
                im.crop(box).save(buf, "PNG")
                out.append(buf.getvalue())
            return out
    except Exception:  # noqa: BLE001 - 坏图：交给后面按一张处理
        return [data] * 4


def _frames(data: bytes) -> list[bytes]:
    """动图的各帧：现成的 GIF 按帧拆开，其他图当 2×2 四格切开。"""
    try:
        from PIL import Image, ImageSequence

        with Image.open(io.BytesIO(data)) as im:
            if getattr(im, "n_frames", 1) > 1:
                out = []
                for frame in ImageSequence.Iterator(im):
                    buf = io.BytesIO()
                    frame.convert("RGBA").save(buf, "PNG")
                    out.append(buf.getvalue())
                return out
    except Exception:  # noqa: BLE001 - 读不了：下面报错
        raise StickerError("读不了这张图") from None
    return _quarters(data)


def _is_image(data: bytes) -> bool:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            im.verify()
        return True
    except Exception:  # noqa: BLE001
        return False


def _gif(frames: list[bytes], ms: int) -> bytes:
    """几张同样大小的 PNG 连成循环播放的 GIF。"""
    from PIL import Image

    images = [Image.open(io.BytesIO(f)).convert("RGB") for f in frames]
    buf = io.BytesIO()
    images[0].save(
        buf, "GIF", save_all=True, append_images=images[1:], duration=ms, loop=0, optimize=True
    )
    return buf.getvalue()


def _white_background(im: Any) -> Any:
    """把和边框连着的浅灰底色刷成纯白；人物身上的白色、浅色不和边框连着，不动。"""
    from PIL import ImageDraw

    w, h = im.size
    step = max(1, min(w, h) // 64)
    edge = (
        [(x, 0) for x in range(0, w, step)]
        + [(x, h - 1) for x in range(0, w, step)]
        + [(0, y) for y in range(0, h, step)]
        + [(w - 1, y) for y in range(0, h, step)]
    )
    for xy in edge:
        r, g, b, a = im.getpixel(xy)
        if (r, g, b) == (255, 255, 255) or a < 255:
            continue
        # 只认浅而不带颜色的底：最暗的通道够亮，三个通道相差不大
        if min(r, g, b) >= BG_MIN_LEVEL and max(r, g, b) - min(r, g, b) <= BG_MAX_TINT:
            # 先填一个图里不会有的颜色（全透明），最后再换成白：直接填白时，
            # 起点和白色相差不到 thresh 会被当成“已经填过”跳过，越接近白的灰越刷不到
            ImageDraw.floodfill(im, xy, MARK, thresh=BG_THRESH)
    mask = im.getchannel("A").point(lambda a: 255 if a == 0 else 0)
    im.paste((255, 255, 255, 255), mask=mask)
    return im
