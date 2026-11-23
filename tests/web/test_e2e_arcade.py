"""游戏区：第六个入口、大厅、开一桌德州（成员用假模型出牌）、你出牌、下一手、离桌退金币；旁观押注；斗地主选牌出牌。"""

from __future__ import annotations

from tests.web.conftest import wait_until


def _fast(srv) -> None:
    """本地电脑代打不用装作在想（不然一局升级要一两分钟）。"""
    rt = srv.env.rt
    cfg = rt.config.roundtable
    arcade = cfg.arcade.model_copy(update={"think_delay_s": 0.05})
    rt.config = rt.config.model_copy(
        update={"roundtable": cfg.model_copy(update={"arcade": arcade})}
    )


def _your_turn_or_done(page) -> str:
    def ready():
        return (
            page.locator(".ac-controls [data-move]").count() > 0
            or page.locator(".ac-controls [data-act=deal]").count() > 0
        )

    wait_until(ready, timeout=20)
    return "turn" if page.locator(".ac-controls [data-move]").count() else "done"


def test_open_holdem_play_a_hand_and_leave(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.click("#entries [data-m=game]")
    page.wait_for_selector(".ac-lobby")
    assert "游戏区" in page.inner_text("#arcade .bn-zh")
    assert page.inner_text("[data-coins]") == "2,000"
    assert page.locator(".ac-game:disabled").count() == 0  # 六个游戏都能玩了
    page.click(".ac-game[data-game=holdem]")
    page.wait_for_selector(".ac-setup")
    assert page.locator(".ac-pick.on").count() >= 1
    page.click("[data-act=open]")
    page.wait_for_selector(".ac-seat.you")
    assert page.locator(".ac-seat.you").count() == 1
    # 带了 1000 上桌（金币数字会跳一下，等它停）
    wait_until(lambda: page.inner_text("[data-coins]") == "1,000", timeout=5)
    # 一直跟到这一手结束
    for _ in range(30):
        if _your_turn_or_done(page) == "done":
            break
        page.evaluate(
            """() => {
              const q = (s) => document.querySelector('.ac-controls ' + s);
              const b = q('[data-move=check]') || q('[data-move=call]')
                || q('[data-move=allin]') || q('[data-move]');
              if (b) b.click();
            }"""
        )
        page.wait_for_timeout(150)
    else:
        raise AssertionError("这一手没打完")
    assert page.locator(".ac-results li").count() >= 1
    # 成员这一手是用模型想的，还说了话
    assert page.locator(".ac-brain:not(.local)").count() >= 1
    page.click("[data-act=deal]")
    _your_turn_or_done(page)
    page.click("[data-act=leave]")
    page.wait_for_selector(".ac-lobby")
    page.wait_for_timeout(900)  # 等金币数字跳完
    coins = int(page.inner_text("[data-coins]").replace(",", ""))
    assert 0 <= coins <= 2000 + 20 * 1000


def test_spectate_and_bet(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url + "#m=game")
    page.wait_for_selector(".ac-lobby")
    page.click(".ac-game[data-game=holdem]")
    page.click("[data-sit='0']")
    page.click("[data-think='0']")  # 本地电脑代打：不调模型
    while page.locator(".ac-pick.on").count() < 2:
        page.locator(".ac-pick:not(.on):not([disabled])").first.click()
    page.click("[data-act=open]")
    page.wait_for_selector("[data-bet-on]")
    value = page.locator("[data-bet-on] option").nth(1).get_attribute("value")
    page.select_option("[data-bet-on]", value)
    page.fill("[data-bet-amount]", "100")
    page.click("[data-act=deal]")
    page.wait_for_selector(".ac-betres", timeout=30000)
    assert "你押" in page.inner_text(".ac-betres")
    assert page.locator(".ac-seat .ac-card.back").count() == 0  # 结束后亮牌


def test_doudizhu_bid_pick_cards_and_finish(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url + "#m=game")
    page.wait_for_selector(".ac-lobby")
    page.click(".ac-game[data-game=doudizhu]")
    page.wait_for_selector(".ac-setup")
    assert "斗地主" in page.inner_text(".ac-setup h3")
    assert page.locator(".ac-pick.on").count() == 2  # 你加两位正好三人
    page.locator(".ac-pick:not(.on):not([disabled])").first.click()
    assert page.locator(".ac-pick.on").count() == 2  # 选满了再点不加
    page.click("[data-act=open]")
    page.wait_for_selector(".ac-shfelt")
    assert page.locator(".ac-hand .ac-hcard").count() == 17

    def ready():
        q = page.locator
        return q("[data-bid]").count() or q("[data-shed]").count() or q(".ac-results").count()

    played = False
    for _ in range(120):
        wait_until(ready, timeout=20)
        if page.locator(".ac-results").count():
            break
        if page.locator("[data-bid]").count():
            page.locator("[data-bid]").last.click()  # 叫最高
        elif page.locator("[data-shed=hint]").count():
            page.click("[data-shed=hint]")
            assert page.locator(".ac-hcard.on").count() >= 1  # 提示帮你选好了牌
            page.click("[data-shed=play]")
            played = True
        else:
            page.click("[data-shed=pass]")
        page.wait_for_timeout(120)
    else:
        raise AssertionError("这一局没打完")
    assert played
    assert page.locator(".ac-results li").count() == 3
    assert "金币" in page.inner_text(".ac-betres")
    page.click("[data-act=deal]")
    wait_until(ready, timeout=20)
    page.click("[data-act=leave]")
    page.wait_for_selector(".ac-lobby")


def test_shengji_declare_bury_play_a_round(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    _fast(srv)
    page.goto(srv.url + "#m=game")
    page.wait_for_selector(".ac-lobby")
    page.click(".ac-game[data-game=shengji]")
    page.wait_for_selector(".ac-setup")
    assert page.locator(".ac-pick.on").count() == 3  # 你加三位正好四人
    page.click("[data-think='0']")  # 本地电脑代打，快一点
    page.click("[data-act=open]")
    page.wait_for_selector(".ac-sjfelt")
    assert page.locator(".ac-sjseat").count() == 4
    assert page.locator(".ac-hand .ac-hcard").count() == 25

    def ready():
        q = page.locator
        return (
            q("#arcade [data-sjopt]").count()
            or q("[data-shed]").count()
            or q(".ac-results").count()
        )

    played = False
    for _ in range(200):
        wait_until(ready, timeout=20)
        if page.locator(".ac-results").count():
            break
        if page.locator("#arcade [data-sjopt]").count():
            page.locator("#arcade [data-sjopt]").first.click()  # 不亮
        elif page.locator("[data-shed=bury]").count():
            page.click("[data-shed=hint]")
            assert page.locator(".ac-hcard.on").count() == 8
            page.click("[data-shed=bury]")
        else:
            page.click("[data-shed=hint]")
            page.click("[data-shed=play]")
            played = True
        page.wait_for_timeout(60)
    else:
        raise AssertionError("这一局没打完")
    assert played
    assert page.locator(".ac-results li").count() == 4
    assert "闲家得分" in page.inner_text(".ac-controls")
    page.click("[data-act=leave]")
    page.wait_for_selector(".ac-lobby")


def test_god_view_when_watching(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    _fast(srv)
    page.goto(srv.url + "#m=game")
    page.wait_for_selector(".ac-lobby")
    page.click(".ac-game[data-game=doudizhu]")
    page.click("[data-sit='0']")
    page.click("[data-think='0']")
    page.click("[data-act=open]")
    page.wait_for_selector("[data-god]")
    assert page.locator(".ac-fan.open").count() == 0
    page.click("[data-act=deal]")
    page.wait_for_selector(".ac-shfelt")
    page.click("[data-god='1']")
    page.wait_for_selector("[data-god='0']")
    wait_until(lambda: page.locator(".ac-fan.open").count() == 3, timeout=20)  # 三家的牌都看得到


def test_deal_animation_card_feel_and_sound_toggle(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    _fast(srv)
    page.goto(srv.url + "#m=game")
    page.wait_for_selector(".ac-lobby")
    page.click(".ac-game[data-game=doudizhu]")
    page.click("[data-think='0']")
    page.click("[data-act=open]")
    page.wait_for_selector(".ac-shfelt")
    page.wait_for_selector(".ac-hcard.ac-deal", timeout=5000)  # 开桌发牌：手牌一张张滑进来
    assert page.locator(".ac-hcard .ac-card em").count() >= 1  # 大牌中间有花色水印（王除外）
    # 音效开关：默认开，点了关，存在这台电脑上，刷新还是关
    assert "开" in page.inner_text("[data-sfx]")
    page.click("[data-sfx]")
    assert "关" in page.inner_text("[data-sfx]")
    assert page.evaluate("localStorage.getItem('roundtable.arcade.sfx')") == "off"
    page.reload()
    page.wait_for_selector("[data-sfx]")
    assert "关" in page.inner_text("[data-sfx]")
    page.click("[data-sfx]")
    assert page.evaluate("localStorage.getItem('roundtable.arcade.sfx')") == "on"


def test_mahjong_discard_claim_and_finish(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    _fast(srv)
    page.goto(srv.url + "#m=game")
    page.wait_for_selector(".ac-lobby")
    page.click(".ac-game[data-game=mahjong]")
    page.wait_for_selector(".ac-setup")
    assert "麻将" in page.inner_text(".ac-setup h3")
    page.click("[data-think='0']")
    page.click("[data-act=open]")
    page.wait_for_selector(".ac-mjfelt")
    assert page.locator(".ac-rivers .ac-river").count() == 4  # 四家的牌河

    def ready():
        q = page.locator
        return (
            q("#arcade [data-sjopt]").count()
            or q(".ac-htile:not([tabindex])").count()
            or q(".ac-results").count()
        )

    discarded = False
    for _ in range(300):
        wait_until(ready, timeout=20)
        if page.locator(".ac-results").count():
            break
        if page.locator(".ac-htile:not([tabindex])").count():
            hu = page.locator("#arcade [data-sjopt]", has_text="自摸")
            if hu.count():
                hu.first.click()
                continue
            tile = page.locator(".ac-htile").last
            tile.click()  # 先选中
            assert page.locator(".ac-htile.on").count() == 1
            tile.click()  # 再点一下就打出去
            discarded = True
        else:
            page.locator("#arcade [data-sjopt]").first.click()  # 碰 / 胡 / 杠，没有就过
        page.wait_for_timeout(80)
    else:
        raise AssertionError("这一局没打完")
    assert discarded
    assert page.locator(".ac-results li").count() == 4
    page.click("[data-act=deal]")
    page.wait_for_selector(".ac-mjfelt")
    page.click("[data-act=leave]")
    page.wait_for_selector(".ac-lobby")


def _short_tanks(srv) -> None:
    """一局只打 3 秒、一条命，结算马上出来（直接改内存里的配置，不走校验）。"""
    rt = srv.env.rt
    cfg = rt.config.roundtable
    tanks = cfg.arcade.tanks.model_copy(update={"minutes": 0.05, "lives": 1, "min_seconds": 0})
    arcade = cfg.arcade.model_copy(update={"tanks": tanks})
    rt.config = rt.config.model_copy(
        update={"roundtable": cfg.model_copy(update={"arcade": arcade})}
    )


def test_tanks_drive_fire_and_settle(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    _short_tanks(srv)
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(srv.url + "#m=game")
    page.wait_for_selector(".ac-lobby")
    page.click(".ac-game[data-game=tanks]")
    page.wait_for_selector(".ac-setup")
    assert "铁甲突围" in page.inner_text(".ac-setup h3")
    page.click("[data-level=hard]")
    assert page.get_attribute("[data-level=hard]", "aria-pressed") == "true"
    page.click("[data-act=open]")
    page.wait_for_selector(".ac-tcanvas")
    assert "你" in page.inner_text("[data-thud]")
    page.keyboard.down("ArrowUp")
    page.wait_for_timeout(300)
    page.keyboard.up("ArrowUp")
    page.keyboard.press("Space")
    # 3 秒到点，结算浮层出来，后台记了这一局
    page.wait_for_selector("[data-tover]:not([hidden])", timeout=15000)
    wait_until(lambda: "金币" in page.inner_text("[data-tover]"), timeout=10)
    assert "名" in page.inner_text("[data-tover]")
    page.click("[data-act=tanks-again]")
    page.wait_for_selector("[data-tover][hidden]", state="attached")
    page.click("[data-act=tanks-quit]")
    page.wait_for_selector(".ac-lobby")
    assert not errors, errors


def test_tanks_spectate_no_coins(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    _short_tanks(srv)
    page.goto(srv.url + "#m=game")
    page.wait_for_selector(".ac-lobby")
    coins = page.inner_text("[data-coins]")
    page.click(".ac-game[data-game=tanks]")
    page.wait_for_selector(".ac-setup")
    page.click("[data-sit='0']")
    assert "旁观不算金币" in page.inner_text(".ac-setup")
    page.click("[data-act=open]")
    page.wait_for_selector(".ac-tcanvas")
    assert page.locator("[data-act=tanks-pause]").count() == 0  # 旁观不用暂停键
    page.wait_for_selector("[data-tover]:not([hidden])", timeout=15000)
    page.click("[data-act=tanks-back]")
    page.wait_for_selector(".ac-lobby")
    assert page.inner_text("[data-coins]") == coins


def test_workshop_make_play_and_delete(serve, page, monkeypatch, tmp_path):
    """游戏工坊：写要求 → 开做（假的编程助手，不跑程序）→ 进度 → 做好的游戏 → 沙箱里玩 → 删除。"""
    import roundtable.core.service as service_mod
    from roundtable.core.arcade.workshop import Workshop
    from roundtable.core.config.schema import SupercomputerRules, SuperWorker
    from roundtable.core.supercomputer import Supercomputer

    from ..core.arcade.test_workshop import Agents
    from ..core.supercomputer.test_engine import Launcher, render

    class Slow(Agents):
        async def __call__(self, argv, **kw):
            import asyncio

            await asyncio.sleep(0.8)
            return await super().__call__(argv, **kw)

    agents = Slow()

    def build(self, runtime, base):
        workers = [
            SuperWorker(id=w, label=lab, argv=[w], skills={"frontend": 0.5, "review": 0.5})
            for w, lab in (("claude", "主做甲"), ("opencode-ds", "辅助乙"), ("codex", "辅助丙"))
        ]
        return Supercomputer(
            SupercomputerRules(workers=workers),
            root=tmp_path / "super",
            render=render,
            launchers={"command": Launcher()},
            runner=agents,
            seed=7,
        )

    monkeypatch.setattr(service_mod.RoundtableService, "_build_super", build)
    real_init = Workshop.__init__

    def init(self, *a, **kw):  # 测试服务用内存数据库，没有本机目录：给工坊一个临时目录
        real_init(self, *a, **{**kw, "root": tmp_path / "ws", "render": render})

    monkeypatch.setattr(Workshop, "__init__", init)
    srv = serve(confirm_threshold_usd=100.0)
    rt = srv.env.rt
    cfg = rt.config.roundtable
    ws = cfg.arcade.workshop.model_copy(update={"browser_check": False})
    arcade = cfg.arcade.model_copy(update={"workshop": ws})
    rt.config = rt.config.model_copy(
        update={"roundtable": cfg.model_copy(update={"arcade": arcade})}
    )
    page.goto(srv.url + "#m=game")
    shop = page.locator(".ac-shop")
    shop.wait_for()
    assert "主做甲 主做" in shop.inner_text() and "辅助乙、辅助丙" in shop.inner_text()
    assert "还没有做好的游戏" in shop.inner_text()
    page.fill("[data-ws-text]", "一个收集星星的原创小游戏")
    page.click("[data-act=ws-make]")
    page.wait_for_selector(".ac-wsjob.live")
    assert page.locator("[data-act=ws-make]").is_disabled()
    page.wait_for_selector(".ac-wsgame", timeout=20000)
    assert "星尘收集者" in page.inner_text(".ac-wsgame")
    assert page.locator(".ac-wsstep").count() == 3  # 主做 + 两位试玩修错
    assert [c[0] for c in agents.calls] == ["claude", "opencode-ds", "codex"]
    page.click("[data-ws-play]")
    frame = page.frame_locator("[data-ws-frame]")
    frame.locator("canvas").wait_for(state="attached")
    assert page.get_attribute("[data-ws-frame]", "sandbox") == "allow-scripts"
    page.click("[data-act=ws-back]")
    page.wait_for_selector(".ac-shop")
    page.click("[data-ws-del]")
    assert page.inner_text("[data-ws-del]") == "再点一下删除"
    page.click("[data-ws-del]")
    page.wait_for_selector(".ac-wsgame", state="detached")


def test_tanks_two_vs_two_mines_and_stats(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    _short_tanks(srv)
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(srv.url + "#m=game")
    page.wait_for_selector(".ac-lobby")
    page.click(".ac-game[data-game=tanks]")
    page.wait_for_selector(".ac-setup")
    page.click("[data-tmode=team]")
    assert page.get_attribute("[data-tmode=team]", "aria-pressed") == "true"
    assert page.locator(".ac-pick.on").count() == 3  # 2 对 2：你 + 3 位
    assert page.locator(".ac-tbadge", has_text="队友").count() == 1
    assert page.locator(".ac-tbadge", has_text="对手").count() == 2
    page.click("[data-act=open]")
    page.wait_for_selector(".ac-tcanvas")
    assert page.locator(".ac-tteam", has_text="左队").count() == 2
    page.keyboard.press("KeyE")  # 没捡到地雷时按了也不出错
    page.wait_for_selector("[data-tover]:not([hidden])", timeout=15000)
    assert "你们队" in page.inner_text("[data-tover]")
    wait_until(lambda: "金币" in page.inner_text("[data-tover]"), timeout=10)
    page.click("[data-act=tanks-back]")
    page.wait_for_selector(".ac-lobby")
    page.click(".ac-game[data-game=tanks]")
    page.wait_for_selector(".ac-tstats")
    assert "你" in page.inner_text(".ac-tstats")  # 战绩记上了
    assert not errors, errors
