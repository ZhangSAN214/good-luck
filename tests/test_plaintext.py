"""LaTeX → 终端纯文本。"""

from __future__ import annotations

import pytest

from roundtable.plaintext import latex_to_text as t


@pytest.mark.parametrize(
    "src, expected",
    [
        (r"最大值为 \(f(-1)=2\)。", "最大值为 f(-1)=2。"),
        (r"\[ f'(x) = 3x^2 - 3 = 0 \Rightarrow x = \pm 1 \]", "f'(x) = 3x² - 3 = 0 ⇒ x = ± 1"),
        (r"答案 $\boxed{\frac{\sqrt{3}}{2}}$", "答案 √3/2"),
        (r"$\frac{a+b}{c}$", "(a+b)/c"),
        (r"$$\int_0^{\pi} \sin x \, dx = 2$$", "∫₀^π sin x dx = 2"),
        (r"$\sqrt[3]{8} = 2$", "∛8 = 2"),
        (r"$\sqrt{x+1}$", "√(x+1)"),
        (r"$x^{n+1}$ 与 $a_{ij}$", "xⁿ⁺¹ 与 aᵢⱼ"),
        (r"$e^{i\pi}+1=0$", "e^(iπ)+1=0"),
        (r"角度 $30^\circ$，集合 $\mathbb{R}$", "角度 30°，集合 ℝ"),
        (r"\(\text{面积} = \pi r^2\)", "面积 = π r²"),
        (r"$a \leq b$, $a \neq b$, $a \times b$, $a \cdot b$", "a ≤ b, a ≠ b, a × b, a · b"),
        (r"$\alpha + \beta = \gamma$", "α + β = γ"),
    ],
)
def test_math_regions(src, expected):
    assert t(src) == expected


def test_environments_become_lines():
    src = r"解：\[ \begin{cases} x+y=3 \\ x-y=1 \end{cases} \] 得 \(x=2\)"
    assert t(src) == "解：\nx+y=3\nx-y=1\n 得 x=2"
    assert t(r"\begin{aligned} a &= b + c \\ &= 3 \end{aligned}") == "\na = b + c\n= 3\n"


def test_boxed_and_symbols_outside_math():
    assert t(r"最终答案：\boxed{42}") == "最终答案：42"
    assert t(r"3 \times 4 = 12") == "3 × 4 = 12"


@pytest.mark.parametrize(
    "src",
    [
        "价格是 $5 和 $10。",
        r"文件在 C:\Users\me\docs 下",
        "普通文本，没有公式。",
        "**粗体** 与 `code` 保持不变",
    ],
)
def test_non_math_text_untouched(src):
    assert t(src) == src


def test_unbalanced_input_does_not_crash():
    for src in [r"\frac{1}{", r"$\sqrt{$", r"\begin{aligned} x", "$$", r"\\", r"x^"]:
        t(src)
