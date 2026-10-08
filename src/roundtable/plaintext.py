"""把模型输出中的 LaTeX 数学式转成终端里易读的纯文本（只用于命令行显示，不改动存储的原文）。

    \\[ \\frac{1}{2}x^2 + \\sqrt{3} \\]   →   1/2x² + √3
    \\boxed{f(x)_{\\max} = 2}            →   f(x)ₘₐₓ = 2

数学区域（$…$、$$…$$、\\(…\\)、\\[…\\]）内完整转换；区域外只转换已知命令，
未知的反斜杠内容（如 Windows 路径）原样保留。
"""

from __future__ import annotations

import re

SYMBOLS = {
    # 希腊字母
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "δ",
    "epsilon": "ε",
    "varepsilon": "ε",
    "zeta": "ζ",
    "eta": "η",
    "theta": "θ",
    "vartheta": "θ",
    "iota": "ι",
    "kappa": "κ",
    "lambda": "λ",
    "mu": "μ",
    "nu": "ν",
    "xi": "ξ",
    "pi": "π",
    "rho": "ρ",
    "sigma": "σ",
    "tau": "τ",
    "upsilon": "υ",
    "phi": "φ",
    "varphi": "φ",
    "chi": "χ",
    "psi": "ψ",
    "omega": "ω",
    "Gamma": "Γ",
    "Delta": "Δ",
    "Theta": "Θ",
    "Lambda": "Λ",
    "Xi": "Ξ",
    "Pi": "Π",
    "Sigma": "Σ",
    "Phi": "Φ",
    "Psi": "Ψ",
    "Omega": "Ω",
    # 运算与关系
    "times": "×",
    "cdot": "·",
    "div": "÷",
    "pm": "±",
    "mp": "∓",
    "le": "≤",
    "leq": "≤",
    "ge": "≥",
    "geq": "≥",
    "neq": "≠",
    "ne": "≠",
    "approx": "≈",
    "equiv": "≡",
    "sim": "~",
    "propto": "∝",
    "infty": "∞",
    "to": "→",
    "rightarrow": "→",
    "leftarrow": "←",
    "Rightarrow": "⇒",
    "Leftarrow": "⇐",
    "implies": "⇒",
    "iff": "⇔",
    "Leftrightarrow": "⇔",
    "mapsto": "↦",
    "in": "∈",
    "notin": "∉",
    "subset": "⊂",
    "subseteq": "⊆",
    "supset": "⊃",
    "cup": "∪",
    "cap": "∩",
    "emptyset": "∅",
    "varnothing": "∅",
    "forall": "∀",
    "exists": "∃",
    "neg": "¬",
    "land": "∧",
    "lor": "∨",
    "wedge": "∧",
    "vee": "∨",
    "partial": "∂",
    "nabla": "∇",
    "sum": "Σ",
    "prod": "Π",
    "int": "∫",
    "iint": "∬",
    "oint": "∮",
    "circ": "°",
    "degree": "°",
    "angle": "∠",
    "perp": "⊥",
    "parallel": "∥",
    "triangle": "△",
    "therefore": "∴",
    "because": "∵",
    "ldots": "…",
    "cdots": "…",
    "dots": "…",
    "vdots": "⋮",
    "prime": "′",
    "star": "*",
    "ast": "*",
    "mid": "|",
    "lbrace": "{",
    "rbrace": "}",
    "langle": "⟨",
    "rangle": "⟩",
    "lfloor": "⌊",
    "rfloor": "⌋",
    "lceil": "⌈",
    "rceil": "⌉",
    "percent": "%",
}
NAMED = {
    "sin",
    "cos",
    "tan",
    "cot",
    "sec",
    "csc",
    "arcsin",
    "arccos",
    "arctan",
    "sinh",
    "cosh",
    "tanh",
    "log",
    "ln",
    "lg",
    "exp",
    "lim",
    "max",
    "min",
    "sup",
    "inf",
    "det",
    "gcd",
    "deg",
    "dim",
    "ker",
    "arg",
}
BLACKBOARD = {"R": "ℝ", "N": "ℕ", "Z": "ℤ", "Q": "ℚ", "C": "ℂ"}
SPACES = {",": " ", ";": " ", ":": " ", "!": "", " ": " ", "quad": "  ", "qquad": "   "}
DROP = {
    "left",
    "right",
    "big",
    "Big",
    "bigg",
    "Bigg",
    "bigl",
    "bigr",
    "Bigl",
    "Bigr",
    "displaystyle",
    "textstyle",
    "limits",
    "nolimits",
    "middle",
}
TEXT_ARG = {
    "text",
    "textbf",
    "textit",
    "textrm",
    "mathrm",
    "mathbf",
    "mathit",
    "mathsf",
    "boxed",
    "operatorname",
    "mbox",
    "hbox",
    "emph",
    "underline",
    "overline",
    "bar",
    "hat",
    "vec",
    "tilde",
    "widehat",
    "widetilde",
    "overrightarrow",
}
ACCENTS = {"bar": "̄", "hat": "̂", "vec": "⃗", "tilde": "̃"}

SUP = dict(zip("0123456789+-=()niaxyk°′", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱᵃˣʸᵏ°′", strict=True))
SUB = dict(zip("0123456789+-=()aeoxijkmnpst", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₒₓᵢⱼₖₘₙₚₛₜ", strict=True))
_SIMPLE = re.compile(r"^[\w.′°√αβγδεθλμπσφωΔΣΩ]+$")

_REGIONS = re.compile(
    r"(?P<env>\\begin\{(?P<name>\w+\*?)\}.+?\\end\{(?P=name)\})"
    r"|\$\$(?P<a>.+?)\$\$"
    r"|\\\[(?P<b>.+?)\\\]"
    r"|\\\((?P<c>.+?)\\\)"
    r"|(?<![\\$\w])\$(?P<d>[^\s$](?:[^$\n]*?[^\s$\\])?)\$(?!\w)",
    re.DOTALL,
)


class _Parser:
    def __init__(self, src: str, strict: bool) -> None:
        self.s = src
        self.i = 0
        self.strict = strict

    def peek(self) -> str:
        return self.s[self.i] if self.i < len(self.s) else ""

    def group(self) -> str:
        """读取一个参数：{…}、单个命令或单个字符。"""
        while self.peek() == " ":
            self.i += 1
        ch = self.peek()
        if ch == "{":
            self.i += 1
            out = self.parse(stop="}")
            self.i += 1  # 跳过 }
            return out
        if ch == "\\":
            return self.command()
        self.i += 1
        return ch

    def optional(self) -> str | None:
        if self.peek() != "[":
            return None
        end = self.s.find("]", self.i)
        if end == -1:
            return None
        inner = _Parser(self.s[self.i + 1 : end], self.strict).parse()
        self.i = end + 1
        return inner

    def command(self) -> str:
        start = self.i
        self.i += 1  # 跳过反斜杠
        m = re.match(r"[A-Za-z]+", self.s[self.i :])
        if not m:  # 单字符命令：\, \; \\ \{ \}
            ch = self.peek()
            self.i += 1
            if ch == "\\":
                return "\n"
            return SPACES.get(ch, ch)
        name = m.group(0)
        self.i += len(name)

        if name in ("frac", "dfrac", "tfrac", "cfrac"):
            num, den = self.group(), self.group()
            return f"{_wrap(num)}/{_wrap(den)}"
        if name == "sqrt":
            index = self.optional()
            body = self.group()
            root = {"2": "", "3": "∛", "4": "∜"}.get(index or "2")
            prefix = root if root is not None else f"{index}√"
            return (prefix or "√") + (body if _SIMPLE.match(body) else f"({body})")
        if name == "mathbb":
            body = self.group()
            return BLACKBOARD.get(body, body)
        if name in ACCENTS and self.strict:
            body = self.group()
            return body + ACCENTS[name] if len(body) == 1 else body
        if name in TEXT_ARG:
            return self.group()
        if name in ("begin", "end"):
            self.group()
            return "" if name == "begin" else ""
        if name in DROP:
            if self.peek() in ".":
                self.i += 1
            return ""
        if name in SPACES:
            return SPACES[name]
        if name in SYMBOLS:
            return SYMBOLS[name]
        if name in NAMED:
            return name
        if self.strict:
            return name
        return self.s[start : self.i]  # 区域外的未知命令原样保留

    def script(self, table: dict[str, str], mark: str) -> str:
        body = self.group()
        if body and all(c in table for c in body):
            return "".join(table[c] for c in body)
        return f"{mark}{body}" if len(body) == 1 else f"{mark}({body})"

    def parse(self, stop: str = "") -> str:
        out: list[str] = []
        while self.i < len(self.s):
            ch = self.peek()
            if stop and ch == stop:
                break
            if ch == "\\":
                out.append(self.command())
            elif ch == "{":
                self.i += 1
                out.append(self.parse(stop="}"))
                self.i += 1
            elif ch == "}" and not stop:
                self.i += 1
            elif ch == "^" and self.strict:
                self.i += 1
                out.append(self.script(SUP, "^"))
            elif ch == "_" and self.strict:
                self.i += 1
                out.append(self.script(SUB, "_"))
            elif ch == "&" and self.strict:
                self.i += 1
            elif ch == "~" and self.strict:
                self.i += 1
                out.append(" ")
            else:
                out.append(ch)
                self.i += 1
        return "".join(out)


def _wrap(part: str) -> str:
    return part if _SIMPLE.match(part) else f"({part})"


def _math(content: str) -> str:
    text = _Parser(content, strict=True).parse()
    lines = [re.sub(r"[ \t]{2,}", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def latex_to_text(text: str) -> str:
    """转换整段文本。"""

    def region(m: re.Match[str]) -> str:
        display = any(m.group(k) is not None for k in ("env", "a", "b"))
        raw = next(m.group(k) for k in ("env", "a", "b", "c", "d") if m.group(k) is not None)
        body = _math(raw)
        return f"\n{body}\n" if display and "\n" in body else body

    converted = _REGIONS.sub(region, text)
    # 区域外：只转换已知命令（如没有包在 $ 里的 \boxed{}、\times）
    if "\\" in converted:
        converted = _Parser(converted, strict=False).parse()
    return re.sub(r"\n{3,}", "\n\n", converted)


# 成对的 **加粗**；两侧紧挨字母 / 数字时不处理（避免误伤代码里的 2**3 这类乘方）
_BOLD = re.compile(r"(?<![A-Za-z0-9*])\*\*(?=\S)(.+?)(?<=\S)\*\*(?![A-Za-z0-9*])")


def strip_bold(text: str) -> str:
    """去掉 Markdown 加粗符号，只保留文字。"""
    return _BOLD.sub(r"\1", text)


def to_terminal(text: str) -> str:
    """命令行显示：数学式转纯文本，并去掉加粗符号。"""
    return strip_bold(latex_to_text(text))
