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
STATUSES = ("pending", "approved", "rejected")
# members.json 里当参考图用的图片，按顺序取（找不到 refs 时）
REF_KEYS = ("standee", "chibi", "avatar")
REF_MAX_SIDE = 1024  # 参考图缩到这个边长以内再发
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
    raw = json.dumps(
        [card.look, card.must, card.refs, spec.caption, spec.action], ensure_ascii=False
    )
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
        """能看参考图的绘图模型：带的偏好标签（prefer_tags）越多越先试，同样多时保持配置顺序。"""
        media = self.rt.config.roundtable.media
        if not media.enabled or not self.rules.enabled:
            return []
        group = [
            m
            for m in media_group(self.rt.router, "image", self.rules.tier)
            if "image_edit" in m.tags
        ]
        prefer = self.rules.prefer_tags
        return sorted(group, key=lambda m: -sum(t in m.tags for t in prefer))

    # --- 查看 ------------------------------------------------------------------------

    def listing(self, member: str) -> list[dict[str, Any]]:
        """每个表情的状态：missing 还没画 / pending 待审 / approved 可以用 / rejected 不要。"""
        card = self.card(member)
        drawn = self._index.load()["items"].get(member, {})
        out = []
        for spec in card.stickers:
            e = drawn.get(spec.name)
            row: dict[str, Any] = {"name": spec.name, "caption": spec.caption, "status": "missing"}
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

    async def draw(self, member: str, name: str) -> dict[str, Any]:
        """画（或重画）一张。返回这张的状态；花费计入两个每月上限。"""
        card = self.card(member)
        spec = self.spec(member, name)
        models = self.draw_models()
        if not models:
            raise StickerUnavailable("没有能看参考图的绘图模型")
        refs = self.references(member, card)
        if not refs:
            raise StickerUnavailable("找不到这位成员的形象参考图（素材目录里的设定图、立绘或头像）")
        prompt = self.rt.prompts.render(
            "sticker_draw",
            self.rt.config.roundtable.prompts["sticker_draw"],
            look=card.look,
            must="、".join(card.must) or "无",
            caption=spec.caption,
            action=spec.action,
        )
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
            image, used = _square(out.images[0].data, self.rules.size), model
            check = await self._check(session, image, spec, card, refs)
            if check is None or check.ok:
                break
        if image is None or used is None:
            raise StickerUnavailable("画图失败（所有模型都没画出来）")
        file = hashlib.sha256(image).hexdigest()[:24] + ".png"
        self._index.write(file, image)
        data = self._index.load()
        data["items"].setdefault(member, {})[name] = {
            "caption": spec.caption,
            "file": file,
            "status": "pending" if self.rules.review else "approved",
            "problems": self._problems(check),
            "checked": check is not None,
            "card": card_hash(card, spec),
            "model": used.id,
            "cost_usd": round(total, 6),
            "at": self.rt.repo.clock(),
        }
        self._index.save(data)
        return next(r for r in self.listing(member) if r["name"] == name)

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
            im = im.convert("RGBA")
            im.thumbnail((size, size))
            canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))
            canvas.alpha_composite(im, ((size - im.width) // 2, (size - im.height) // 2))
            buf = io.BytesIO()
            canvas.convert("RGB").save(buf, "PNG", optimize=True)
            return buf.getvalue()
    except Exception:  # noqa: BLE001 - 读不了的图：原样存
        return data
