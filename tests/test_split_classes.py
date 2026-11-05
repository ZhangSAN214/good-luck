"""大类拆成几个组成部分（mixin）之后的守卫：同名方法只能定义一次。

拆分前同名方法后定义的覆盖先定义的；拆成多个类后改由继承顺序决定，容易悄悄换掉实现，
所以干脆不许重名。新拆的类加到 CLASSES 里。
"""

import inspect
from collections import defaultdict

import pytest

from roundtable.cli import CLI
from roundtable.core.service import RoundtableService
from roundtable.core.storage.repository import Repository

CLASSES = [Repository, CLI, RoundtableService]


@pytest.mark.parametrize("cls", CLASSES, ids=lambda c: c.__name__)
def test_no_method_defined_in_two_parts(cls):
    owners = defaultdict(list)
    for part in cls.__mro__:
        if part is object:
            continue
        for name, value in vars(part).items():
            if inspect.isfunction(value) and not name.startswith("__"):
                owners[name].append(part.__name__)
    assert {n: o for n, o in owners.items() if len(o) > 1} == {}
