from __future__ import annotations

import yaml

from roundtable.core.dsh.overlay import overlay_rows, write_overlay


def test_overlay_turns_off_uploads_and_sets_neutral_persona(tmp_path):
    path = write_overlay(tmp_path / "o.yml", "  你是工作台助手。  ")
    rows = {r["id"]: r for r in yaml.safe_load(path.read_text(encoding="utf-8"))}
    # 会话日志上传关掉（只存本机）
    assert rows["session-log-deepseek"] == {
        "id": "session-log-deepseek",
        "config": {"enabled": False},
    }
    # 插件清单上报、遥测整行关掉
    assert rows["plugin-package-inventory-deepseek"]["disabled"] is True
    assert rows["session-telemetry-otel"]["disabled"] is True
    assert rows["system-prompt"]["config"] == {"personaPrefix": "你是工作台助手。"}


def test_overlay_rows_are_complete_configs():
    # dsh 的补丁层整行替换 config：每行要么关掉，要么写全自己的 config
    for row in overlay_rows("x"):
        assert set(row) in ({"id", "config"}, {"id", "disabled"})
