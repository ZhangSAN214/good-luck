"""ChatGPT 网页出图：用你自己的 ChatGPT 会员在网页里画，不花 API 的钱（2026-11-02 用户：
「开机时用 ChatGPT，最好全自动，没开机用 2.5」）。

只出图。用一个专用的浏览器资料夹（默认 ``data/chatgpt_web_profile``，不进 git）打开
ChatGPT 网页：新开对话 → 上传参考图 → 输入提示词（逐字输入，不经剪贴板）→ 发送 →
等画完 → 取图。登录只做一次：``roundtable chatgpt-login`` 弹出浏览器，你自己登录，
圆桌不经手账号密码；登录成功后在资料夹里留一个标记，并在 config/models.local.yaml 打开
这个模型。没登录（没有标记）时立刻报 AUTH，调用方换下一个绘图模型（GPT Image 2.5）。

网页上的位置（输入框、发送按钮、画好的图……）写在模型参数里（``selectors``），网页改版时
在 models.local.yaml 的 model_params 里改，不用改代码。

一次只画一张：正在画时再来一张直接报 RATE_LIMIT「网页正在画另一张」，交给下一个模型。
会员额度用完（页面出现上限提示）也报 RATE_LIMIT。

规则同其他适配器：不重试（失败抛 ProviderError，换模型由调用方负责）；错误文本截断、
用 from None 切断异常链；不碰你的其他对话和设置。
"""

from __future__ import annotations

import asyncio
import re
import tempfile
import time
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

from roundtable.core.config.loader import DEFAULT_CONFIG_DIR
from roundtable.core.config.schema import ChannelSpec

from ._http import safe_detail
from .base import ImageOutput, Media, Message, Provider, RawCompletion
from .errors import ErrorKind, ProviderError, UnsupportedCapability
from .registry import register_adapter
from .secrets import Secret

PROJECT_ROOT = DEFAULT_CONFIG_DIR.parent
LOGIN_HINT = "运行 roundtable chatgpt-login 登录"
LOGIN_MARK = ".roundtable_logged_in"  # chatgpt-login 成功后写在资料夹里
MODEL_ID = "chatgpt-web"  # models.yaml 里的模型 id（登录后在 models.local.yaml 打开它）

SELECTORS: dict[str, str] = {
    "prompt": "#prompt-textarea",
    "send": "button[data-testid='send-button']",
    "stop": "button[data-testid='stop-button']",
    "file": "input[type='file']",
    "image": "img[alt^='Generated image'], img[alt^='已生成图片']",
    "login": "button[data-testid='login-button']",
    "limit": "text=/(reached|hit) (the|your) .*limit|已达到.*上限/i",
}

DEFAULTS: dict[str, Any] = {
    "profile_dir": "data/chatgpt_web_profile",  # 相对项目根目录
    "timeout_s": 300.0,  # 发送后最多等多久（网页一张约 1～3 分钟）
    "load_timeout_s": 45.0,  # 打开网页、等输入框出现
    "poll_interval_s": 3.0,
    "max_refs": 10,  # 网页一次能上传的图片数
    "headless": False,  # 无头浏览器容易被网页拦；默认开一个窗口（放到屏幕外，见 offscreen）
    "offscreen": True,  # 窗口挪到屏幕外，不挡你
    # 用本机装的浏览器："msedge" / "chrome"；留空用 Playwright 自带的 Chromium，没装时自动改用 Edge
    "browser": "",
    "executable": "",  # 浏览器程序的完整路径（与 Playwright 版本不匹配时用），一般留空
    "settle_s": 3.0,  # 上传参考图后等缩略图出来
    "selectors": {},  # 覆盖上面的 SELECTORS（网页改版时用）
    # 加在提示词前面，免得网页回一段文字、反问或只给建议
    "prefix": "请直接生成这张图片，只出图，不要提问、不要文字说明：\n\n",
}

Driver = Callable[[str, Sequence[Media], dict[str, Any]], Awaitable[bytes]]
_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}


def options(params: dict[str, Any]) -> dict[str, Any]:
    opts = {**DEFAULTS, **{k: v for k, v in params.items() if k in DEFAULTS}}
    opts["selectors"] = {**SELECTORS, **(opts.get("selectors") or {})}
    return opts


def profile_path(opts: dict[str, Any], root: Path = PROJECT_ROOT) -> Path:
    path = Path(str(opts["profile_dir"]))
    return path if path.is_absolute() else root / path


def logged_in(profile: Path) -> bool:
    return (profile / LOGIN_MARK).is_file()


def _sniff(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


@register_adapter("chatgpt_web")
class ChatGPTWebProvider(Provider):
    def __init__(
        self,
        channel: str,
        spec: ChannelSpec,
        key: Secret | None,  # 不用：网页用你自己登录的会员
        timeout_s: float,
        driver: Driver | None = None,
        root: Path | None = None,
    ) -> None:
        super().__init__(channel)
        self._url = spec.base_url
        self._driver = driver or self._browser
        self._root = root or PROJECT_ROOT
        self._lock = asyncio.Lock()

    def _error(self, kind: ErrorKind, detail: str = "", **kw: Any) -> ProviderError:
        return ProviderError(kind, self.channel, safe_detail(detail), **kw)

    async def complete(
        self, model: str, messages: Sequence[Message], params: dict[str, Any]
    ) -> RawCompletion:
        raise UnsupportedCapability(f"{self.channel} 只能出图")

    async def generate_image(
        self,
        model: str,
        prompt: str,
        params: dict[str, Any],
        images: Sequence[Media] = (),
        api: str = "chat",
    ) -> ImageOutput:
        opts = options(params)
        profile = profile_path(opts, self._root)
        if not logged_in(profile):
            raise self._error(ErrorKind.AUTH, f"还没登录 ChatGPT 网页：{LOGIN_HINT}")
        if self._lock.locked():
            raise self._error(ErrorKind.RATE_LIMIT, "网页正在画另一张")
        refs = [m for m in images if m.kind == "image"][: int(opts["max_refs"])]
        async with self._lock:
            data = await self._driver(prompt, refs, {**opts, "profile": profile, "url": self._url})
        if not data:
            raise self._error(ErrorKind.INVALID_RESPONSE, "网页没有给出图片")
        return ImageOutput((Media("image", _sniff(data), data),), cost_usd=0.0)

    # --- 真浏览器（Playwright）------------------------------------------------------

    async def _browser(self, prompt: str, refs: Sequence[Media], opts: dict[str, Any]) -> bytes:
        try:
            from playwright.async_api import Error as PlaywrightError
            from playwright.async_api import async_playwright
        except ImportError:
            detail = "没装 playwright（pip install playwright）"
            raise self._error(ErrorKind.NOT_FOUND, detail) from None
        sel = opts["selectors"]
        try:
            async with async_playwright() as pw:
                context = await launch(pw, opts)
                try:
                    page = context.pages[0] if context.pages else await context.new_page()
                    await page.goto(str(opts["url"]), wait_until="domcontentloaded")
                    await self._ready(page, opts)
                    if refs:
                        await self._upload(page, refs, opts)
                    box = page.locator(sel["prompt"]).first
                    await box.click()
                    # 逐字输入，不动你的剪贴板
                    await page.keyboard.insert_text(str(opts["prefix"]) + prompt)
                    before = await page.locator(sel["image"]).count()
                    send = page.locator(sel["send"]).first
                    if await send.count():
                        await send.click()
                    else:
                        await page.keyboard.press("Enter")
                    src = await self._wait(page, before, opts)
                    response = await context.request.get(src)
                    if not response.ok:
                        raise self._error(ErrorKind.SERVER, f"取图失败（{response.status}）")
                    return await response.body()
                finally:
                    await context.close()
        except ProviderError:
            raise
        except PlaywrightError as exc:
            raise self._error(ErrorKind.NETWORK, str(exc).splitlines()[0]) from None

    async def _ready(self, page: Any, opts: dict[str, Any]) -> None:
        sel = opts["selectors"]
        deadline = time.monotonic() + float(opts["load_timeout_s"])
        while time.monotonic() < deadline:
            if await page.locator(sel["login"]).count():
                raise self._error(ErrorKind.AUTH, f"ChatGPT 网页登录过期：{LOGIN_HINT}")
            if await page.locator(sel["prompt"]).count():
                return
            await asyncio.sleep(1.0)
        raise self._error(ErrorKind.TIMEOUT, "ChatGPT 网页打不开或改版了（找不到输入框）")

    async def _upload(self, page: Any, refs: Sequence[Media], opts: dict[str, Any]) -> None:
        with tempfile.TemporaryDirectory(prefix="rt_chatgpt_") as tmp:
            paths = []
            for i, media in enumerate(refs, 1):
                path = Path(tmp) / f"ref_{i}.{_EXT.get(media.mime, 'png')}"
                path.write_bytes(media.data)
                paths.append(str(path))
            await page.locator(opts["selectors"]["file"]).first.set_input_files(paths)
            await asyncio.sleep(float(opts["settle_s"]))

    async def _wait(self, page: Any, before: int, opts: dict[str, Any]) -> str:
        """等画完：出现新的生成图、停止按钮消失。返回图片地址。"""
        sel = opts["selectors"]
        deadline = time.monotonic() + float(opts["timeout_s"])
        interval = float(opts["poll_interval_s"])
        while time.monotonic() < deadline:
            await asyncio.sleep(interval)
            if await page.locator(sel["limit"]).count():
                raise self._error(ErrorKind.RATE_LIMIT, "ChatGPT 会员的画图次数用完了")
            images = page.locator(sel["image"])
            if await images.count() > before and not await page.locator(sel["stop"]).count():
                src = await images.last.get_attribute("src")
                if src and re.match(r"^https?://", src):
                    return src
        raise self._error(ErrorKind.TIMEOUT, f"等了 {float(opts['timeout_s']):g} 秒还没画完")


async def launch(pw: Any, opts: dict[str, Any]) -> Any:
    """用专用资料夹打开浏览器（登录状态存在里面）。"""
    profile = Path(opts["profile"])
    profile.mkdir(parents=True, exist_ok=True)
    args = ["--window-position=-2400,0"] if opts.get("offscreen") and not opts["headless"] else []
    kwargs: dict[str, Any] = {"headless": bool(opts["headless"]), "args": args}
    if opts.get("executable"):
        kwargs["executable_path"] = str(opts["executable"])
    elif opts.get("browser"):
        kwargs["channel"] = str(opts["browser"])
    try:
        return await pw.chromium.launch_persistent_context(str(profile), **kwargs)
    except Exception as exc:  # noqa: BLE001 - 没装 Playwright 自带的浏览器时改用本机的 Edge
        if kwargs.get("channel") or kwargs.get("executable_path"):
            raise
        if "Executable doesn't exist" not in str(exc):
            raise
        kwargs["channel"] = "msedge"
        return await pw.chromium.launch_persistent_context(str(profile), **kwargs)


async def login(url: str, params: dict[str, Any], *, wait_s: float = 600.0) -> bool:
    """弹出浏览器让你自己登录 ChatGPT（圆桌不经手账号密码）；看到输入框且没有登录按钮就算成功，
    在资料夹里留下标记。返回是否成功。"""
    from playwright.async_api import async_playwright

    opts = options(params)
    opts.update(headless=False, offscreen=False)
    profile = profile_path(opts)
    opts["profile"] = profile
    sel = opts["selectors"]
    async with async_playwright() as pw:
        context = await launch(pw, opts)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(url, wait_until="domcontentloaded")
            deadline = time.monotonic() + wait_s
            while time.monotonic() < deadline:
                ready = await page.locator(sel["prompt"]).count()
                if ready and not await page.locator(sel["login"]).count():
                    (profile / LOGIN_MARK).write_text("ok", encoding="utf-8")
                    return True
                await asyncio.sleep(2.0)
            return False
        finally:
            await context.close()


def switch_on(local_yaml: Path, model_id: str = MODEL_ID, *, value: bool = True) -> None:
    """在 config/models.local.yaml 的 model_switches 里打开（value=False 关掉）这个模型，
    保留文件里其他内容。"""
    text = local_yaml.read_text(encoding="utf-8") if local_yaml.exists() else ""
    line = f"  {model_id}: {'true' if value else 'false'}"
    lines = [ln for ln in text.splitlines() if not re.match(rf"^\s+{re.escape(model_id)}\s*:", ln)]
    for i, ln in enumerate(lines):
        if re.match(r"^model_switches\s*:\s*$", ln):
            lines.insert(i + 1, line)
            break
        if re.match(r"^model_switches\s*:\s*\{\s*\}\s*$", ln):
            lines[i] = "model_switches:"
            lines.insert(i + 1, line)
            break
        if re.match(r"^model_switches\s*:", ln):
            hint = f"请手动加上 {line.strip()}"
            raise ValueError(f"{local_yaml} 里的 model_switches 不是分行写的，{hint}")
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines += ["model_switches:", line]
    local_yaml.write_text("\n".join(lines) + "\n", encoding="utf-8")
