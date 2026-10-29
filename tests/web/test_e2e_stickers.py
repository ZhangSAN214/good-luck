"""成员专属表情包：「成员设定」里看到表情网格，待审的点「可以」后变成可以用。"""

from __future__ import annotations

import io

from PIL import Image

from roundtable.core.config.schema import StickerCard, StickerSpec
from roundtable.core.member_stickers import MemberStickers, card_hash

from .test_e2e_chat import open_chat


def _png(color: str) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (360, 360), color).save(buf, "PNG")
    return buf.getvalue()


def test_people_dialog_reviews_member_stickers(serve, page, tmp_path):
    def setup(env):
        rt = env.rt
        card = StickerCard(
            look="测试成员",
            stickers=[
                StickerSpec(name="晚安", caption="晚安", action="挥手"),
                StickerSpec(name="收到", caption="收到！", action="敬礼"),
                StickerSpec(name="好耶", caption="好耶！", action="跳起来"),
            ],
        )
        personas = rt.config.personas.model_copy(update={"sticker_cards": {"V2": card}})
        rt.config = rt.config.model_copy(update={"personas": personas})
        rt.sticker_dir = tmp_path / "stickers"
        stickers = MemberStickers(rt, rt.sticker_dir)
        data = {"items": {"V2": {}}}
        for spec, color, status in (
            (card.stickers[0], "#f4d0e0", "pending"),
            (card.stickers[1], "#d0e4f4", "approved"),
        ):
            file = f"{spec.name.encode().hex()}.png"
            stickers._index.write(file, _png(color))
            data["items"]["V2"][spec.name] = {
                "caption": spec.caption,
                "file": file,
                "status": status,
                "problems": "",
                "card": card_hash(card, spec),
                "model": "x",
                "cost_usd": 0.02,
                "at": rt.repo.clock(),
            }
        stickers._index.save(data)

    srv = serve(confirm_threshold_usd=100.0, setup=setup)
    open_chat(srv, page)
    page.click(".gc-room[data-room='dm:V2']")
    page.wait_for_function("document.querySelector('#gc-title')?.textContent === '与 阿二 私聊'")
    page.click("#gc-people")
    page.wait_for_selector("#people[open] .pp-stickers .pp-stk")
    grid = page.locator("#people .pp-stk")
    assert grid.count() == 3
    assert "待审" in grid.nth(0).inner_text() and "可以用" in grid.nth(1).inner_text()
    assert grid.nth(2).locator(".pp-stk-empty").count() == 1
    page.wait_for_function(
        "[...document.querySelectorAll('#people .pp-stk img')].every(i => i.naturalWidth === 360)"
    )
    page.click("#people .pp-stk[data-sname='晚安'] [data-sok]")
    page.wait_for_selector("#people .pp-stk.approved[data-sname='晚安']")
    approved = MemberStickers(srv.env.rt, tmp_path / "stickers").approved("V2")
    assert [s["name"] for s in approved] == ["晚安", "收到"]
