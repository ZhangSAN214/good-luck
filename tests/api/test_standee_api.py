"""终端首页大立绘接口：/api/standee 与 /api/standee/files/…。"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.service import RoundtableService

from ..core.members_samples import PNG_1PX
from ..core.orchestrator.conftest import Env


def test_standee_manifest_files_and_persona_lines(tmp_path):
    root = tmp_path / "avatars"
    (root / "s").mkdir(parents=True)
    for name in ("a.png", "a_blink.png"):
        (root / "s" / name).write_bytes(PNG_1PX)
    (tmp_path / "secret.png").write_bytes(PNG_1PX)
    env = Env(standee_file=root / "standee.json")
    vendor = env.config.models.models[0].vendor
    manifest = {
        "name": "立绘一",
        "persona": vendor,
        "variants": [
            {
                "id": "regular",
                "label": "常规",
                "image": "s/a.png",
                "expressions": {"blink": {"image": "s/a_blink.png"}},
            }
        ],
        "lines": {"tap": ["嗯？"]},
    }
    (root / "standee.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    env.rt.repo.set_member_persona(vendor, {"catchphrases": ["我想想", "嗯？"]})
    with TestClient(create_app(RoundtableService(env.rt))) as c:
        data = c.get("/api/standee").json()
        assert data == {
            "available": True,
            "reason": None,
            "name": "立绘一",
            "variants": [
                {
                    "id": "regular",
                    "label": "常规",
                    "image": "/api/standee/files/s/a.png",
                    "head": None,
                    "cutout": True,
                    "expressions": {
                        "blink": {"image": "/api/standee/files/s/a_blink.png", "box": None}
                    },
                    "missing": [],
                }
            ],
            # 人设口头禅加进点击台词，不重复
            "lines": {"tap": ["嗯？", "我想想"]},
        }
        f = c.get(data["variants"][0]["image"])
        assert f.status_code == 200 and f.content == PNG_1PX
        assert f.headers["x-content-type-options"] == "nosniff"
        for bad in ("/api/standee/files/..%2Fsecret.png", "/api/standee/files/s/none.png"):
            assert c.get(bad).status_code == 404


def test_without_standee_reports_reason():
    env = Env()
    with TestClient(create_app(RoundtableService(env.rt))) as c:
        data = c.get("/api/standee").json()
        assert data["available"] is False and "还没有立绘" in data["reason"]
        assert c.get("/api/standee/files/a.png").status_code == 404
