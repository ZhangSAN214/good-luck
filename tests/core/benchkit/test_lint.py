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
