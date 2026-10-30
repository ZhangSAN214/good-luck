"""工作台生成器：读窗口代码的检查（没用零件库、写死颜色、代码太大）。"""

from __future__ import annotations

from roundtable.core.benchkit.lint import lint_windows


def write(folder, wid, text):
    (folder / "windows").mkdir(exist_ok=True)
    (folder / "windows" / f"{wid}.js").write_text(text, encoding="utf-8")


def test_lint_windows(tmp_path):
    write(tmp_path, "good", "export default (el, ctx) => ctx.kit.timeline(el, { items: [] });")
    write(
        tmp_path,
        "imp",
        "import { table } from '/kit/kit.js';\nexport default (el) => table(el, {});",
    )
    write(
        tmp_path,
        "hand",
        "export default (el) => { el.innerHTML = '<div style=\"color:#123\">x</div>'; };",
    )
    write(
        tmp_path, "colors", "ctx.kit.esc('x'); const c = ['#fff', '#000', 'rgb(1,2,3)', '#abcdef'];"
    )
    write(tmp_path, "big", "ctx.kit.esc('x');" + "//" + "x" * 70 * 1024)
    found = {
        f.check: f for f in lint_windows(tmp_path, ["good", "imp", "hand", "colors", "big", "gone"])
    }
    assert found["窗口没用零件库"].severity == "block"
    assert found["窗口没用零件库"].windows == ["hand"]
    assert found["写死颜色"].windows == ["colors"] and found["写死颜色"].severity == "warn"
    assert found["窗口代码太大"].windows == ["big"]
    assert lint_windows(tmp_path, ["good", "imp"]) == []


def test_destructured_kit_counts_and_polling_is_flagged(tmp_path):
    # 第 2 次真跑的误报：先从 ctx 解构出 kit、再从 kit 解构零件，也是在用零件库
    write(
        tmp_path,
        "pack",
        "export default (el, ctx) => { const { kit, store } = ctx;"
        " const { seg, ring } = kit; ring(el, 3); };",
    )
    write(tmp_path, "arg", "export default (el, { kit, bus }) => { const { table } = kit; };")
    write(
        tmp_path,
        "poll",
        "export default (el, ctx) => { setInterval(() => ctx.kit.esc('x'), 2000); };",
    )
    found = {f.check: f for f in lint_windows(tmp_path, ["pack", "arg", "poll"])}
    assert "窗口没用零件库" not in found
    assert found["轮询定时器"].windows == ["poll"] and found["轮询定时器"].severity == "warn"


def test_hand_drawn_svg_is_blocked(tmp_path):
    shapes = "".join(f"<circle cx='{i}' cy='1' r='2'/>" for i in range(13))
    write(
        tmp_path,
        "drawn",
        "export default (el, ctx) => { ctx.kit.esc('');"
        f" el.innerHTML = `<svg>{shapes}</svg>`; }};",
    )
    write(
        tmp_path,
        "few",
        "export default (el, ctx) => { ctx.kit.esc('');"
        " el.innerHTML = '<svg><path d=\"M0 0\"/></svg>'; };",
    )
    found = {f.check: f for f in lint_windows(tmp_path, ["drawn", "few"])}
    assert found["自己画插图"].severity == "block" and found["自己画插图"].windows == ["drawn"]
