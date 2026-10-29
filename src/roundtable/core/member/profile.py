"""自我档案：每位成员一份，所有模块的数据都在这里（只有一个数据源）。

- 档案按分区存（``chat.engine.profile.sections``：分区 → 谁能改，如 ``identity`` 只有用户能改，
  ``personality`` 用户和成长机制能改）。每个分区是一层 ``键 → 值``，值可以是任意 JSON。
- **每次修改都是一个新版本**（``member_profiles``，迁移 24）：存整份档案、谁改的、为什么、改了哪几项
  （旧值 → 新值）。旧版本不改不删，**撤回**就是把某个旧版本的内容存成一个新版本。
- **锁定**：用户锁住的项（``分区.键``）只有用户能改；锁定清单本身也只有用户能改。
- 用户可以随时查看、修改、撤回、一键清空（§5.1 第 4 条）。只存本地 SQLite。
- 所有成员的规则完全一样（谁能改、锁定、版本上限），与是哪个模型无关。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

USER = "user"
LOCKED = "locked"  # 档案顶层的锁定清单（["personality.核心", …]），不属于任何分区
MISSING = object()


class ProfileError(ValueError):
    """不允许的修改（分区不存在、没有权限、改了锁定的项）。"""


@dataclass(frozen=True)
class Profile:
    member: str
    version: int  # 0：还没有档案
    data: dict[str, Any] = field(default_factory=dict)

    def section(self, name: str) -> dict[str, Any]:
        return dict(self.data.get(name) or {})

    def get(self, path: str, default: Any = None) -> Any:
        sec, _, key = path.partition(".")
        return (self.data.get(sec) or {}).get(key, default)

    @property
    def locked(self) -> list[str]:
        return list(self.data.get(LOCKED) or [])


def _changes(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    """两份档案的差别：[{path, old, new}]（新增时 old 为 None，删除时 new 为 None）。"""
    out = []
    for sec in sorted(set(old) | set(new)):
        if sec == LOCKED:
            if sorted(old.get(LOCKED) or []) != sorted(new.get(LOCKED) or []):
                out.append({"path": LOCKED, "old": old.get(LOCKED), "new": new.get(LOCKED)})
            continue
        a, b = old.get(sec) or {}, new.get(sec) or {}
        for key in sorted(set(a) | set(b)):
            if a.get(key, MISSING) != b.get(key, MISSING):
                out.append({"path": f"{sec}.{key}", "old": a.get(key), "new": b.get(key)})
    return out


class ProfileStore:
    def __init__(self, rt: Any) -> None:
        self.rt = rt

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.profile

    def current(self, member: str) -> Profile:
        row = self.rt.repo.member_profile(member)
        if row is None:
            return Profile(member, 0, {})
        return Profile(member, row["version"], row["data"])

    def history(self, member: str) -> list[dict[str, Any]]:
        """每个版本：版本号、时间、谁改的、为什么、改了哪几项（新的在前，不含整份档案）。"""
        return self.rt.repo.member_profile_history(member)

    def version(self, member: str, version: int) -> Profile:
        row = self.rt.repo.member_profile(member, version)
        if row is None:
            raise ProfileError(f"{member} 没有第 {version} 版档案")
        return Profile(member, row["version"], row["data"])

    def check(self, profile: Profile, new: dict[str, Any], author: str) -> list[dict[str, Any]]:
        """检查从 profile 改成 new 是否允许，返回改动清单。"""
        sections = self.rules.sections
        changes = _changes(profile.data, new)
        locked = set(profile.locked)
        for c in changes:
            path = c["path"]
            if path == LOCKED:
                if author != USER:
                    raise ProfileError("锁定清单只有用户能改")
                continue
            sec = path.split(".", 1)[0]
            if sec not in sections:
                raise ProfileError(f"档案没有「{sec}」这个分区")
            if author != USER and author not in sections[sec]:
                raise ProfileError(f"「{author}」不能改「{sec}」")
            if author != USER and path in locked:
                raise ProfileError(f"「{path}」已被你锁定，只有你能改")
        return changes

    def _save(self, profile: Profile, data: dict[str, Any], author: str, reason: str) -> Profile:
        changes = self.check(profile, data, author)
        if not changes:
            return profile
        version = profile.version + 1
        self.rt.repo.add_member_profile(
            profile.member, version, data, author=author, reason=reason, changes=changes
        )
        return Profile(profile.member, version, data)

    def change(
        self, member: str, patch: dict[str, dict[str, Any]], *, author: str, reason: str = ""
    ) -> Profile:
        """按分区改几项：``{"likes": {"音乐": "后摇", "旧的": None}}``；值为 None 即删掉。"""
        profile = self.current(member)
        data = copy.deepcopy(profile.data)
        for sec, items in patch.items():
            if sec == LOCKED:
                raise ProfileError("锁定用 lock() / unlock()")
            if not isinstance(items, dict):
                raise ProfileError(f"「{sec}」必须是 键 → 值")
            target = data.setdefault(sec, {})
            for key, value in items.items():
                if value is None:
                    target.pop(key, None)
                else:
                    target[key] = value
            if not target:
                data.pop(sec)
        return self._save(profile, data, author, reason)

    def lock(self, member: str, path: str, *, locked: bool = True) -> Profile:
        """用户锁住 / 解锁一项（``分区.键``）。"""
        sec = path.split(".", 1)[0]
        if "." not in path or sec not in self.rules.sections:
            raise ProfileError(f"「{path}」不是档案里的一项（写成 分区.键）")
        profile = self.current(member)
        data = copy.deepcopy(profile.data)
        items = set(data.get(LOCKED) or [])
        items = items | {path} if locked else items - {path}
        if items:
            data[LOCKED] = sorted(items)
        else:
            data.pop(LOCKED, None)
        return self._save(profile, data, USER, f"{'锁定' if locked else '解锁'} {path}")

    def revert(self, member: str, version: int, *, author: str = USER) -> Profile:
        """撤回到某个旧版本：把它的内容存成一个新版本（旧版本都还在）。"""
        old = self.version(member, version)
        reason = f"撤回到第 {version} 版"
        return self._save(self.current(member), copy.deepcopy(old.data), author, reason)

    def clear(self, member: str) -> int:
        """一键清空这位成员的档案和全部修改记录（用户的权利）。返回删掉的版本数。"""
        return self.rt.repo.clear_member_profiles(member)
