"""版本化提示词：加载 prompts/<role>/v<n>.md，校验变量，渲染为消息列表。

文件格式：
    ---
    description: ...
    output: text | json
    variables: [a, b]
    ---
    <!-- system -->
    系统提示，可用 {{ a }}
    <!-- user -->
    用户消息，可用 {{ b }}

占位符用 {{ name }} 而不是 $name，避免和数学公式中的 $ 冲突。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal

import yaml

from roundtable.core.config import ConfigError, RoundtableConfig
from roundtable.core.providers import Message

DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parents[4] / "prompts"

_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_SECTION = re.compile(r"^<!-- (system|user) -->$", re.MULTILINE)
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_VERSION = re.compile(r"^v(\d+)$")


class PromptError(ValueError):
    """提示词文件缺失、格式错误或渲染参数不匹配。"""


@dataclass(frozen=True)
class RenderedPrompt:
    role: str
    version: str
    sha256: str  # 模板文件内容的哈希（换行统一为 LF），随调用入库
    messages: tuple[Message, ...]


@dataclass(frozen=True)
class PromptTemplate:
    role: str
    version: str
    sha256: str
    description: str
    output: Literal["text", "json"]
    variables: frozenset[str]
    system: str
    user: str

    def render(self, **values: str) -> RenderedPrompt:
        given = set(values)
        missing = sorted(self.variables - given)
        extra = sorted(given - self.variables)
        if missing or extra:
            raise PromptError(
                f"{self.role}/{self.version} 渲染参数不匹配：缺少 {missing}，多余 {extra}"
            )
        texts = {k: str(v) for k, v in values.items()}

        def fill(template: str) -> str:
            # 单次替换：值里即使出现 {{ x }} 也不会被再次展开
            return _PLACEHOLDER.sub(lambda m: texts[m.group(1)], template)

        return RenderedPrompt(
            role=self.role,
            version=self.version,
            sha256=self.sha256,
            messages=(Message("system", fill(self.system)), Message("user", fill(self.user))),
        )


def parse_template(role: str, version: str, raw: bytes) -> PromptTemplate:
    name = f"{role}/{version}"
    text = raw.decode("utf-8").replace("\r\n", "\n")
    match = _FRONT_MATTER.match(text)
    if not match:
        raise PromptError(f"{name}：缺少 --- 包围的文件头")
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise PromptError(f"{name}：文件头不是合法的 YAML：{exc}") from None
    if not isinstance(meta, dict):
        raise PromptError(f"{name}：文件头必须是映射")

    output = meta.get("output", "text")
    if output not in ("text", "json"):
        raise PromptError(f"{name}：output 只能是 text 或 json")
    declared = meta.get("variables") or []
    if not isinstance(declared, list) or not all(isinstance(v, str) for v in declared):
        raise PromptError(f"{name}：variables 必须是字符串列表")

    body = text[match.end() :]
    parts = _SECTION.split(body)
    # split 结果：[前导文本, 'system', 内容, 'user', 内容]
    if len(parts) != 5 or parts[1] != "system" or parts[3] != "user" or parts[0].strip():
        raise PromptError(f"{name}：正文必须依次包含且仅包含 <!-- system --> 与 <!-- user --> 两段")
    system, user = parts[2].strip(), parts[4].strip()
    if not system or not user:
        raise PromptError(f"{name}：system 和 user 段都不能为空")

    used = set(_PLACEHOLDER.findall(system + "\n" + user))
    undeclared, unused = sorted(used - set(declared)), sorted(set(declared) - used)
    if undeclared or unused:
        raise PromptError(f"{name}：占位符与声明不一致：未声明 {undeclared}，未使用 {unused}")

    return PromptTemplate(
        role=role,
        version=version,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        description=str(meta.get("description", "")),
        output=output,
        variables=frozenset(declared),
        system=system,
        user=user,
    )


class PromptLibrary:
    def __init__(self, root: Path | str = DEFAULT_PROMPTS_DIR) -> None:
        self.root = Path(root)
        self._load = cache(self._load_uncached)

    def path(self, role: str, version: str) -> Path:
        if not _VERSION.match(version):
            raise PromptError(f"版本号必须形如 v1、v2，收到 {version!r}")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", role):
            raise PromptError(f"角色名不合法：{role!r}")
        return self.root / role / f"{version}.md"

    def _load_uncached(self, role: str, version: str) -> PromptTemplate:
        path = self.path(role, version)
        if not path.is_file():
            raise PromptError(f"找不到提示词：{path.relative_to(self.root.parent)}")
        return parse_template(role, version, path.read_bytes())

    def get(self, role: str, version: str) -> PromptTemplate:
        return self._load(role, version)

    def render(self, role: str, version: str, **values: str) -> RenderedPrompt:
        return self.get(role, version).render(**values)

    def roles(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if p.is_dir() and self.versions(p.name))

    def versions(self, role: str) -> list[str]:
        directory = self.root / role
        if not directory.is_dir():
            return []
        found = [p.stem for p in directory.glob("v*.md") if _VERSION.match(p.stem)]
        return sorted(found, key=lambda v: int(v[1:]))

    def latest(self, role: str) -> str:
        versions = self.versions(role)
        if not versions:
            raise PromptError(f"角色 {role!r} 没有任何提示词版本")
        return versions[-1]

    def check_config(self, roundtable: RoundtableConfig) -> None:
        """确认配置里引用的每个提示词版本都存在且格式正确。"""
        problems = []
        for role, version in roundtable.prompts.items():
            try:
                self.get(role, version)
            except PromptError as exc:
                problems.append(str(exc))
        if problems:
            raise ConfigError(
                "roundtable.yaml 引用的提示词有问题：\n  - " + "\n  - ".join(problems)
            )
