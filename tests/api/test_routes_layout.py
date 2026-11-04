"""接口按部门拆到 api/routes/ 之后的结构守卫。"""

from collections import Counter

from roundtable.api.app import create_app
from roundtable.api.routes import ROUTERS
from roundtable.core.service import LanAccess


def _paths(router):
    for r in router.routes:
        for m in sorted(getattr(r, "methods", None) or ()):
            yield m, r.path


def test_no_route_registered_twice():
    seen = Counter(p for router in ROUTERS for p in _paths(router))
    assert [p for p, n in seen.items() if n > 1] == []


def test_static_mount_comes_after_all_api_routes():
    app = create_app(service=object(), lan=LanAccess(None))
    kinds = [type(r).__name__ for r in app.router.routes]
    if "Mount" in kinds:  # 有 web/ 目录时静态前端挂在最后，接口优先匹配
        assert kinds.index("Mount") == len(kinds) - 1


def test_every_router_has_routes():
    assert all(len(r.routes) > 0 for r in ROUTERS)
    assert sum(len(r.routes) for r in ROUTERS) >= 250
