"""一起看的置顶悬浮窗：`python -m roundtable.desktop.overlay --url http://127.0.0.1:8000`。

看视频时不用切回圆桌：成员说的话浮在屏幕最前面（docs/WATCH.md「悬浮窗」）。
- 有 pywebview（桌面版已带）时开一个没有标题栏、总在最前面的应用小窗，载入 ``/overlay.html``：
  和画中画同一个界面（web/js/overlay.js），墨蓝液态玻璃风格；按住顶条拖动、滑块调整个窗口的透明度、
  「－」收成一颗胶囊（有新消息显示条数）、「×」只关小窗不停投屏；点一条消息就回那个人。
- 没有 pywebview 时退回 Python 自带的 tkinter 小窗（同样能拖、调透明度、收起、打字）。
- Windows 上两种小窗都设成「截屏时看不见」，成员看画面时不会读到自己的聊天。
- 投屏停了，小窗说一句「已停止」，几秒后自己关掉。
- 位置、透明度、收起状态记在 ``--state`` 文件里（data/desktop/watch_overlay.json），下次照旧。

和网页一样只通过 HTTP 和本机的圆桌说话（不 import 服务端代码）。由「一起看」面板的「悬浮窗」按钮经
/api/watch/overlay 启动（只在电脑本机）。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import urllib.request
from pathlib import Path
from typing import Any

POLL_MS = 1500
STATUS_MS = 4000
CLOSE_AFTER_STOP_MS = 6000
SHOW_MESSAGES = 6
MIN_ALPHA, MAX_ALPHA = 0.3, 1.0
DEFAULT_STATE = {"x": -1, "y": 80, "alpha": 0.85, "collapsed": False}
WIDTH, HEIGHT, DOT = 340, 300, 52
WEB_WIDTH, WEB_HEIGHT, PILL_WIDTH, PILL_HEIGHT = 360, 400, 170, 44
WEB_BG = "#0e1422"  # 窗口底色（和悬浮窗玻璃的边缘同色，透明不生效时也不突兀）
HINT = "在下面打字，回车发送（@名字 指定谁回）"

# 只连本机：不走系统代理
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Feed:
    """和圆桌的 HTTP 往来、消息缓存与未读数（不依赖 tkinter，测试用）。"""

    def __init__(self, base: str, opener: Any = None, timeout: float = 5.0) -> None:
        self.base = base.rstrip("/")
        self.opener = opener or _OPENER
        self.timeout = timeout
        self.last_id = 0
        self.messages: list[dict[str, Any]] = []
        self.typing: list[str] = []
        self.unread = 0
        self.running = True
        self.note = ""

    def _call(self, path: str, body: dict[str, Any] | None = None) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method="POST" if data else "GET")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        with self.opener.open(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def pull(self) -> list[dict[str, Any]]:
        """拉新消息；返回新来的（成员说的话计入未读）。"""
        data = self._call(f"/api/watch/messages?after={self.last_id}")
        fresh = [m for m in data.get("messages", []) if m.get("id", 0) > self.last_id]
        if fresh:
            self.messages = (self.messages + fresh)[-100:]
            self.last_id = fresh[-1]["id"]
            self.unread += sum(1 for m in fresh if m.get("author") not in ("user", "system"))
        self.typing = list(data.get("typing", []))
        return fresh

    def status(self) -> dict[str, Any]:
        st = self._call("/api/watch")
        self.running = bool(st.get("running"))
        self.note = st.get("paused") or st.get("error") or ""
        return st

    def say(self, text: str) -> None:
        text = text.strip()
        if text:
            self._call("/api/watch/say", {"text": text[:1000]})

    def lines(self) -> list[tuple[str, str, str]]:
        """要显示的最近几条：(类别, 名字, 内容)，类别 me / sys / member。"""
        out = []
        for m in self.messages[-SHOW_MESSAGES:]:
            author = m.get("author")
            kind = "sys" if author == "system" else ("me" if author == "user" else "member")
            out.append((kind, str(m.get("name") or ""), str(m.get("text") or "")))
        return out


def load_state(path: Path | None) -> dict[str, Any]:
    state = dict(DEFAULT_STATE)
    if path is not None and path.is_file():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        if isinstance(saved, dict):
            for k, v in DEFAULT_STATE.items():
                if isinstance(saved.get(k), type(v)):
                    state[k] = saved[k]
    state["alpha"] = min(MAX_ALPHA, max(MIN_ALPHA, float(state["alpha"])))
    return state


def save_state(path: Path | None, state: dict[str, Any]) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


# --- Windows 窗口小工具（其他系统上什么都不做） ---------------------------------------


def exclude_from_capture(hwnd: int) -> bool:
    """截屏时看不见这个窗口（Windows 10 2004+）：成员看画面时不会读到悬浮窗里的聊天。"""
    import sys

    if sys.platform != "win32" or not hwnd:
        return False
    import ctypes

    wda_exclude_from_capture = 0x11
    return bool(ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, wda_exclude_from_capture))


def set_window_alpha(hwnd: int, alpha: float) -> bool:
    """整个窗口的透明度（分层窗口）。"""
    import sys

    if sys.platform != "win32" or not hwnd:
        return False
    import ctypes

    user32 = ctypes.windll.user32
    gwl_exstyle, ws_ex_layered, lwa_alpha = -20, 0x80000, 0x2
    style = user32.GetWindowLongW(hwnd, gwl_exstyle)
    user32.SetWindowLongW(hwnd, gwl_exstyle, style | ws_ex_layered)
    a = int(round(min(MAX_ALPHA, max(MIN_ALPHA, alpha)) * 255))
    return bool(user32.SetLayeredWindowAttributes(hwnd, 0, a, lwa_alpha))


def round_corners(hwnd: int) -> None:
    """Windows 11 的系统圆角（没有标题栏的窗口默认是直角）。"""
    import sys

    if sys.platform != "win32" or not hwnd:
        return
    import ctypes

    pref = ctypes.c_int(2)  # DWMWCP_ROUND
    with contextlib.suppress(Exception):
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))


class Bridge:
    """给 /overlay.html 用的 js_api（pywebview）：读设置、调透明度、收起时改窗口大小、关窗。"""

    def __init__(self, state_path: Path | None, window: Any = None) -> None:
        self.state_path = state_path
        self.state = load_state(state_path)
        self.window = window
        self.hwnd = 0

    def get_state(self) -> dict[str, Any]:
        return {"alpha": self.state["alpha"], "collapsed": self.state["collapsed"]}

    def set_opacity(self, alpha: float) -> None:
        self.state["alpha"] = min(MAX_ALPHA, max(MIN_ALPHA, float(alpha)))
        set_window_alpha(self.hwnd, self.state["alpha"])
        save_state(self.state_path, self.state)

    def set_collapsed(self, collapsed: bool) -> None:
        self.state["collapsed"] = bool(collapsed)
        if self.window is not None:
            w, h = (PILL_WIDTH, PILL_HEIGHT) if collapsed else (WEB_WIDTH, WEB_HEIGHT)
            self.window.resize(w, h)
        save_state(self.state_path, self.state)

    def moved(self, x: int, y: int) -> None:
        self.state["x"], self.state["y"] = int(x), int(y)
        save_state(self.state_path, self.state)

    def close(self) -> None:
        save_state(self.state_path, self.state)
        if self.window is not None:
            self.window.destroy()


def web_overlay(base: str, state_path: Path | None) -> bool:  # pragma: no cover - 需要桌面环境
    """用 pywebview 开应用小窗；没装 pywebview 返回 False（改用 tkinter）。"""
    try:
        import webview
    except ImportError:
        return False
    bridge = Bridge(state_path)
    st = bridge.state
    small = st["collapsed"]
    x = st["x"] if st["x"] >= 0 else None
    window = webview.create_window(
        "一起看",
        f"{base.rstrip('/')}/overlay.html",
        js_api=bridge,
        width=PILL_WIDTH if small else WEB_WIDTH,
        height=PILL_HEIGHT if small else WEB_HEIGHT,
        x=x,
        y=st["y"] if x is not None else None,
        frameless=True,
        easy_drag=False,
        on_top=True,
        transparent=True,
        background_color=WEB_BG,
        min_size=(PILL_WIDTH, PILL_HEIGHT),
    )
    bridge.window = window

    def shown() -> None:
        with contextlib.suppress(Exception):
            bridge.hwnd = int(window.native.Handle.ToInt64())
        exclude_from_capture(bridge.hwnd)
        round_corners(bridge.hwnd)
        set_window_alpha(bridge.hwnd, st["alpha"])

    window.events.shown += shown
    window.events.moved += bridge.moved
    webview.start()
    return True


# --- 没有 pywebview 时：tkinter 小窗 -----------------------------------------------

# 成员名字的颜色（按名字轮换，和是谁无关）
NAME_COLORS = ("#ffd479", "#8fd3ff", "#b6f09c", "#ffadc8", "#d1b3ff", "#ffb38a")
BG, FG, DIM, BAR = "#111827", "#eceff3", "#9ea7b6", "#182c4e"  # 墨蓝玻璃的近似色


class Overlay:  # pragma: no cover - 需要桌面环境，靠手动验收
    def __init__(self, feed: Feed, state_path: Path | None) -> None:
        import tkinter as tk

        self.tk = tk
        self.feed = feed
        self.state_path = state_path
        self.state = load_state(state_path)
        self.colors: dict[str, str] = {}
        root = self.root = tk.Tk()
        root.title("一起看")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", self.state["alpha"])
        root.configure(bg=BG)
        if self.state["x"] < 0:
            self.state["x"] = root.winfo_screenwidth() - WIDTH - 24
        self._drag: tuple[int, int, int, int, bool] = (0, 0, 0, 0, False)
        self._build()
        self.apply_collapsed()
        root.update_idletasks()
        with contextlib.suppress(Exception):
            exclude_from_capture(int(root.wm_frame(), 16))
        root.after(200, self.tick_messages)
        root.after(500, self.tick_status)

    # --- 界面 -------------------------------------------------------------------

    def _build(self) -> None:
        tk = self.tk
        self.full = tk.Frame(self.root, bg=BG, width=WIDTH, height=HEIGHT)
        bar = tk.Frame(self.full, bg=BAR)
        bar.pack(fill="x")
        title = tk.Label(bar, text="● 一起看", bg=BAR, fg="#ff6b6b", font=("Microsoft YaHei", 10))
        title.pack(side="left", padx=8, pady=4)
        for w in (bar, title):
            self._draggable(w)
        tk.Button(bar, text="×", command=self.close, **self._btn()).pack(side="right", padx=2)
        tk.Button(bar, text="－", command=self.collapse, **self._btn()).pack(side="right")
        self.alpha = tk.Scale(
            bar,
            from_=MIN_ALPHA,
            to=MAX_ALPHA,
            resolution=0.05,
            orient="horizontal",
            showvalue=False,
            length=80,
            bg=BAR,
            troughcolor=BG,
            highlightthickness=0,
            bd=0,
            command=self.set_alpha,
        )
        self.alpha.set(self.state["alpha"])
        self.alpha.pack(side="right", padx=4)
        tk.Label(bar, text="透明", bg=BAR, fg=DIM, font=("Microsoft YaHei", 8)).pack(side="right")
        self.text = tk.Text(
            self.full,
            bg=BG,
            fg=FG,
            bd=0,
            wrap="char",
            height=11,
            width=1,
            padx=10,
            pady=6,
            font=("Microsoft YaHei", 11),
            highlightthickness=0,
            cursor="arrow",
        )
        self.text.tag_configure("sys", foreground=DIM, font=("Microsoft YaHei", 9))
        self.text.tag_configure("me", foreground="#c9d1e0")
        self.text.pack(fill="both", expand=True)
        self.text.configure(state="disabled")
        self.note = tk.Label(self.full, text="", bg=BG, fg=DIM, font=("Microsoft YaHei", 9))
        self.note.pack(fill="x")
        self.entry = tk.Entry(
            self.full,
            bg=BAR,
            fg=FG,
            insertbackground=FG,
            bd=0,
            font=("Microsoft YaHei", 10),
            highlightthickness=0,
        )
        self.entry.pack(fill="x", padx=8, pady=(2, 8), ipady=4)
        self.entry.bind("<Return>", self.send)
        self.dot = tk.Label(
            self.root,
            text="看",
            bg="#2f6fed",
            fg="white",
            font=("Microsoft YaHei", 10, "bold"),
            cursor="hand2",
        )
        self._draggable(self.dot, click=self.expand)

    def _btn(self) -> dict[str, Any]:
        return {
            "bg": BAR,
            "fg": FG,
            "bd": 0,
            "relief": "flat",
            "activebackground": BG,
            "width": 2,
            "cursor": "hand2",
        }

    def _draggable(self, widget: Any, click: Any = None) -> None:
        def press(e: Any) -> None:
            self._drag = (e.x_root, e.y_root, self.root.winfo_x(), self.root.winfo_y(), False)

        def move(e: Any) -> None:
            sx, sy, wx, wy, _ = self._drag
            if abs(e.x_root - sx) + abs(e.y_root - sy) > 3:
                self._drag = (sx, sy, wx, wy, True)
                self.root.geometry(f"+{wx + e.x_root - sx}+{wy + e.y_root - sy}")

        def release(e: Any) -> None:
            moved = self._drag[4]
            self.state["x"], self.state["y"] = self.root.winfo_x(), self.root.winfo_y()
            save_state(self.state_path, self.state)
            if click is not None and not moved:
                click()

        widget.bind("<ButtonPress-1>", press)
        widget.bind("<B1-Motion>", move)
        widget.bind("<ButtonRelease-1>", release)

    def set_alpha(self, value: str) -> None:
        self.state["alpha"] = float(value)
        self.root.attributes("-alpha", self.state["alpha"])
        save_state(self.state_path, self.state)

    def collapse(self) -> None:
        self.state["collapsed"] = True
        self.feed.unread = 0
        self.apply_collapsed()

    def expand(self) -> None:
        self.state["collapsed"] = False
        self.feed.unread = 0
        self.apply_collapsed()

    def apply_collapsed(self) -> None:
        x, y = self.state["x"], self.state["y"]
        if self.state["collapsed"]:
            self.full.pack_forget()
            self.dot.pack(fill="both", expand=True)
            self.root.geometry(f"{DOT}x{DOT}+{x}+{y}")
            self.paint_dot()
        else:
            self.dot.pack_forget()
            self.full.pack(fill="both", expand=True)
            self.root.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")
            self.paint()
        save_state(self.state_path, self.state)

    def paint_dot(self) -> None:
        n = self.feed.unread
        self.dot.configure(text=f"看\n{n}" if n else "看", bg="#e5484d" if n else "#2f6fed")

    def paint(self) -> None:
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        for kind, name, text in self.feed.lines():
            if kind == "sys":
                t.insert("end", text + "\n", "sys")
                continue
            if kind == "me":
                t.insert("end", "我：", "me")
                t.insert("end", text + "\n", "me")
                continue
            tag = self.colors.setdefault(name, f"n{len(self.colors)}")
            t.tag_configure(
                tag,
                foreground=NAME_COLORS[int(tag[1:]) % len(NAME_COLORS)],
                font=("Microsoft YaHei", 11, "bold"),
            )
            t.insert("end", f"{name}：", tag)
            t.insert("end", text + "\n")
        if self.feed.typing:
            t.insert("end", "、".join(self.feed.typing) + " 正在输入…\n", "sys")
        t.configure(state="disabled")
        t.see("end")
        self.note.configure(text=self.feed.note[:60] or HINT)

    # --- 轮询 -------------------------------------------------------------------

    def tick_messages(self) -> None:
        with contextlib.suppress(Exception):  # 圆桌没响应时下次再试
            self.feed.pull()
        if self.state["collapsed"]:
            self.paint_dot()
        else:
            self.feed.unread = 0
            self.paint()
        self.root.after(POLL_MS, self.tick_messages)

    def tick_status(self) -> None:
        try:
            self.feed.status()
        except Exception:  # noqa: BLE001
            self.feed.note = "连不上圆桌"
        if not self.feed.running:
            self.feed.note = "投屏已停止，悬浮窗马上关掉"
            if self.state["collapsed"]:
                self.expand()
            self.paint()
            self.root.after(CLOSE_AFTER_STOP_MS, self.close)
            return
        self.root.after(STATUS_MS, self.tick_status)

    def send(self, _e: Any = None) -> None:
        text = self.entry.get()
        self.entry.delete(0, "end")
        try:
            self.feed.say(text)
        except Exception:  # noqa: BLE001
            self.feed.note = "没发出去，再试一次"
        self.paint()

    def close(self) -> None:
        save_state(self.state_path, self.state)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - 需要桌面环境
    ap = argparse.ArgumentParser(description="一起看的置顶悬浮窗")
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--state", default="")
    ap.add_argument("--ui", choices=["auto", "web", "tk"], default="auto")
    args = ap.parse_args(argv)
    state = Path(args.state) if args.state else None
    if args.ui != "tk" and web_overlay(args.url, state):
        return 0
    Overlay(Feed(args.url), state).run()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
