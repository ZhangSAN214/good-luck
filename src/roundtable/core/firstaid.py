"""「出问题点这里」自检（docs/FIRSTAID.md）：圆桌出了状况时一个入口查清楚，只读，不改任何东西。

四类现场：
- **起不来**：配置读不了、数据库打不开或比程序新、磁盘满了、装后核对没过。
- **卡住 / 没反应**：后台任务停了、渠道全不可用、预算用完、最近调用大量失败、沙箱不可用。
- **想撤销刚才的更新**：上次更新的记录（是否通过冒烟测试、能不能退回）。
- **数据不见了**：和最近一份备份比，关键数据是不是突然少了很多；备份是不是太旧。

服务没开时 ``roundtable doctor`` 跑「离线」那部分（只读打开数据库，不做迁移）；
网页上的自检再加上「在线」那部分（后台任务、渠道、预算、调用记录）。
体检报告是一段纯文本，不含聊天内容和 key，去掉已知密钥和用户目录后可以整段转交。
"""

from __future__ import annotations

import contextlib
import json
import platform
import shutil
import sqlite3
import sys
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from roundtable.core.backup import parse_name
from roundtable.core.providers.secrets import redact
from roundtable.core.storage.migrations import MIGRATIONS

OK, INFO, WARN, BAD = "ok", "info", "warn", "bad"
_RANK = {OK: 0, INFO: 1, WARN: 2, BAD: 3}

START, HANG, UNDO, DATA = "start", "hang", "undo", "data"
SCENES = {
    START: "起不来",
    HANG: "卡住 / 没反应",
    UNDO: "想撤销刚才的更新",
    DATA: "数据不见了",
}

# 和最近一份备份比较的关键数据（表不存在就跳过）
WATCHED_TABLES = {
    "sessions": "作业记录",
    "chat_messages": "聊天消息",
    "member_memories": "成员记忆",
    "memory_items": "成员记忆条目",
    "member_profiles": "成员档案",
    "moments": "朋友圈",
    "life_events": "成员生活记录",
}
# 少了这么多才报：至少少 20 条，而且少了一成以上
DROP_MIN_ROWS = 20
DROP_RATIO = 0.10

DISK_WARN_MB = 1024
DISK_BAD_MB = 200
BACKUP_WARN_DAYS = 3
WAL_WARN_MB = 512

UPDATE_STATE = "update_state.json"  # 安全更新（core/safe_update.py）写的记录
INSTALL_CHECK = "install_check.json"  # 装后核对（core/installcheck.py）写的记录


@dataclass(frozen=True)
class Finding:
    id: str
    scene: str
    level: str  # ok / info / warn / bad
    title: str
    detail: str = ""
    fix: str = ""

    def view(self) -> dict[str, Any]:
        return asdict(self)


def worst(findings: Iterable[Finding]) -> str:
    return max((f.level for f in findings), key=_RANK.__getitem__, default=OK)


def summarize(findings: list[Finding]) -> dict[str, Any]:
    """整体结论 + 按现场分组（每组最严重的级别）。"""
    scenes = []
    for key, name in SCENES.items():
        mine = [f for f in findings if f.scene == key]
        scenes.append({"id": key, "name": name, "level": worst(mine), "count": len(mine)})
    level = worst(findings)
    problems = [f for f in findings if f.level in (WARN, BAD)]
    if level == BAD:
        headline = f"发现 {len(problems)} 个问题，红色的要先处理"
    elif level == WARN:
        headline = f"能用，但有 {len(problems)} 处要留意"
    else:
        headline = "一切正常"
    order = sorted(findings, key=lambda f: (-_RANK[f.level], list(SCENES).index(f.scene)))
    return {
        "level": level,
        "headline": headline,
        "scenes": scenes,
        "findings": [f.view() for f in order],
    }


# ---- 读文件记录 -------------------------------------------------------------


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# ---- 离线检查（服务没开也能做）------------------------------------------------


def open_readonly(db_path: Path) -> sqlite3.Connection | None:
    if not db_path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
    except sqlite3.Error:
        return None
    return conn


def schema_version(conn: sqlite3.Connection) -> int | None:
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    except sqlite3.Error:
        return None
    return int(row[0] or 0) if row else 0


def table_counts(conn: sqlite3.Connection, tables: Iterable[str]) -> dict[str, int]:
    have = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    out = {}
    for t in tables:
        if t in have:
            with contextlib.suppress(sqlite3.Error):
                out[t] = int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])  # noqa: S608
    return out


def check_database(conn: sqlite3.Connection | None, db_path: Path | None) -> list[Finding]:
    """数据库能不能打开、完整性、版本和程序是否对得上、WAL 是否过大。"""
    if db_path is None:
        return [Finding("db", START, INFO, "现在用的是临时数据库", "数据不落盘，不用检查")]
    if conn is None:
        if not db_path.exists():
            return [
                Finding(
                    "db",
                    START,
                    INFO,
                    "还没有数据库文件",
                    "第一次启动时会自动建",
                    "直接启动圆桌即可",
                )
            ]
        return [
            Finding(
                "db",
                START,
                BAD,
                "数据库打不开",
                "文件在，但读不了（可能损坏或被别的程序锁住）",
                "关掉所有圆桌窗口再试；还不行就在「备份」里恢复最近一份",
            )
        ]
    out = []
    try:
        ok = conn.execute("PRAGMA quick_check").fetchone()
        healthy = bool(ok) and ok[0] == "ok"
    except sqlite3.DatabaseError:
        healthy = False
    if healthy:
        out.append(Finding("db", START, OK, "数据库完好", "完整性检查通过"))
    else:
        out.append(
            Finding(
                "db",
                START,
                BAD,
                "数据库有损坏",
                "完整性检查没通过",
                "先在「备份」里存一个存档点，再恢复最近一份每天的备份",
            )
        )
    version = schema_version(conn)
    latest = MIGRATIONS[-1][0] if MIGRATIONS else 0
    if version is None:
        out.append(Finding("db_version", START, BAD, "这不像圆桌的数据库", "没有版本表"))
    elif version > latest:
        out.append(
            Finding(
                "db_version",
                START,
                BAD,
                "数据库比程序新",
                f"数据库版本 {version}，程序只认到 {latest}（多半是退回了旧代码）",
                "重新更新到最新代码；或在「备份」里恢复更新前的存档点",
            )
        )
    elif version < latest:
        out.append(
            Finding(
                "db_version",
                START,
                INFO,
                "数据库待升级",
                f"版本 {version} → {latest}，下次启动时会先存「迁移前」存档点再升级",
            )
        )
    else:
        out.append(Finding("db_version", START, OK, "数据库版本和程序一致", f"版本 {version}"))
    wal = db_path.with_name(db_path.name + "-wal")
    with contextlib.suppress(OSError):
        size = wal.stat().st_size / 1048576
        if size >= WAL_WARN_MB:
            out.append(
                Finding(
                    "db_wal",
                    HANG,
                    WARN,
                    "数据库日志文件很大",
                    f"{size:.0f} MB，可能有没关掉的旧窗口一直占着",
                    "运行 stop.bat 关掉所有圆桌，再重新打开",
                )
            )
    return out


def check_disk(folder: Path) -> list[Finding]:
    probe = folder if folder.exists() else folder.parent
    try:
        free = shutil.disk_usage(probe).free / 1048576
    except OSError:
        return [Finding("disk", START, INFO, "看不到磁盘剩余空间")]
    text = f"数据所在磁盘还剩 {free / 1024:.1f} GB"
    if free < DISK_BAD_MB:
        return [
            Finding(
                "disk",
                START,
                BAD,
                "磁盘快满了",
                text,
                "清理磁盘；空间不够时备份和数据库升级都会失败",
            )
        ]
    if free < DISK_WARN_MB:
        return [Finding("disk", START, WARN, "磁盘空间不多", text, "建议清出几个 GB")]
    return [Finding("disk", START, OK, "磁盘空间够用", text)]


def check_config(error: str | None) -> list[Finding]:
    if error:
        return [
            Finding(
                "config",
                START,
                BAD,
                "配置文件读不了",
                error[:400],
                "按提示改回出错的那一行；改乱了可以用 git checkout -- config/ 还原",
            )
        ]
    return [Finding("config", START, OK, "配置文件正常")]


def check_keys(
    present: Iterable[str], missing: Iterable[str], *, channels_ok: bool = False
) -> list[Finding]:
    """只看填没填，不读、不显示 key 的内容。channels_ok：已经有渠道可用（如本地模型）。"""
    have, lack = sorted(set(present)), sorted(set(missing))
    if not have and channels_ok:
        return [Finding("keys", START, INFO, "没填 key", "但已有渠道可用（不需要 key 的渠道）")]
    if not have:
        return [
            Finding(
                "keys",
                START,
                BAD,
                "一个 key 都没填",
                "成员没法调用模型",
                "把 .env.example 复制成 .env，至少填一个 key",
            )
        ]
    detail = f"已填 {len(have)} 个：{'、'.join(have)}"
    if lack:
        detail += f"；没填 {len(lack)} 个：{'、'.join(lack)}（都是可选的）"
    return [Finding("keys", START, OK, "key 已填", detail)]


def check_backups(files: list[Mapping[str, Any]], enabled: bool, now: datetime) -> list[Finding]:
    if not files:
        return [
            Finding(
                "backup",
                DATA,
                WARN,
                "还没有任何备份",
                "成员的记忆和聊天记录只有一份",
                "在「备份」里点「现在存」",
            )
        ]
    latest = max(datetime.fromisoformat(str(f["at"])) for f in files)
    days = (now - latest).total_seconds() / 86400
    text = f"共 {len(files)} 份，最近一份 {latest:%m-%d %H:%M}"
    if days > BACKUP_WARN_DAYS:
        hint = "每天自动备份已关，" if not enabled else ""
        return [
            Finding(
                "backup",
                DATA,
                WARN,
                "备份有点旧了",
                f"{text}（{days:.0f} 天前）",
                f"{hint}在「备份」里点「现在存」",
            )
        ]
    return [Finding("backup", DATA, OK, "有最近的备份", text)]


def check_data_drop(
    now_counts: Mapping[str, int], backup_counts: Mapping[str, int], backup_name: str
) -> list[Finding]:
    """和最近一份备份比，关键数据少了很多就提醒（可能被误删或更新弄乱）。"""
    drops = []
    for table, before in backup_counts.items():
        after = now_counts.get(table)
        if after is None:
            continue
        lost = before - after
        if lost >= DROP_MIN_ROWS and lost >= before * DROP_RATIO:
            drops.append(f"{WATCHED_TABLES.get(table, table)} {before} → {after}")
    if drops:
        return [
            Finding(
                "data_drop",
                DATA,
                BAD,
                "有数据比备份时少了很多",
                "；".join(drops) + f"（对比 {backup_name}）",
                "先在「备份」里存一个存档点保住现状，再恢复这份备份",
            )
        ]
    if backup_counts:
        return [Finding("data_drop", DATA, OK, "数据量和最近的备份对得上", f"对比 {backup_name}")]
    return []


def check_update(state: Mapping[str, Any] | None, now: datetime) -> list[Finding]:
    """上次更新的结果（scripts/safe_update.py 写的 update_state.json）。"""
    if not state:
        return [
            Finding("update", UNDO, INFO, "还没有安全更新的记录", "用 update_and_start.bat 更新")
        ]
    phase = state.get("phase")
    at = str(state.get("finished_at") or state.get("started_at") or "")[:16].replace("T", " ")
    old = str(state.get("from_commit") or "")[:7]
    new = str(state.get("to_commit") or "")[:7]
    undo = "想退回上一版：双击 undo_update.bat（会先关掉圆桌，数据库变了也会一起退回）"
    if phase == "ok":
        return [
            Finding(
                "update",
                UNDO,
                OK,
                "上次更新通过了冒烟测试",
                f"{at}，{old} → {new}" if old and new and old != new else f"{at}，没有新代码",
                undo if old and new and old != new else "",
            )
        ]
    if phase == "rolled_back":
        return [
            Finding(
                "update",
                UNDO,
                WARN,
                "上次更新没通过冒烟测试，已自动退回",
                f"{at}，退回到 {old}；原因：{str(state.get('reason') or '')[:300]}",
                "数据没动。把体检报告发给我们，修好后再更新",
            )
        ]
    if phase == "undone":
        return [
            Finding(
                "update",
                UNDO,
                INFO,
                "你撤销过一次更新",
                f"{at}，退回到 {old}；撤销的那一版不再自动装，有更新的版本时才会更新",
            )
        ]
    if phase == "begun":
        started = state.get("started_at")
        stale = True
        with contextlib.suppress(TypeError, ValueError):
            stale = now - datetime.fromisoformat(str(started)) > timedelta(minutes=30)
        if stale:
            return [
                Finding(
                    "update",
                    UNDO,
                    WARN,
                    "上次更新中途断了",
                    f"{at} 开始，没走完冒烟测试",
                    "重新双击 update_and_start.bat；不想要这次更新就双击 undo_update.bat",
                )
            ]
        return [Finding("update", UNDO, INFO, "正在更新", f"{at} 开始")]
    if phase == "failed":
        return [
            Finding(
                "update",
                UNDO,
                WARN,
                "上次更新没装上",
                str(state.get("reason") or "")[:300],
                "代码没变，照常用；把体检报告发给我们",
            )
        ]
    return []


def check_install(record: Mapping[str, Any] | None) -> list[Finding]:
    """装后核对（scripts/install_check.py 写的 install_check.json）里没过的项。"""
    if not record:
        return [
            Finding(
                "install",
                START,
                INFO,
                "还没有装后核对的记录",
                "下次用 start.bat 启动时会自动核对",
            )
        ]
    out = []
    for source, block in sorted(record.items()):
        if not isinstance(block, dict):
            continue
        at = str(block.get("at") or "")[:16].replace("T", " ")
        items = [i for i in block.get("items", []) if isinstance(i, dict)]
        bad = [i for i in items if not i.get("ok")]
        name = {"start": "启动", "update": "更新", "dsh": "dsh 安装"}.get(source, source)
        if not bad:
            out.append(
                Finding(
                    f"install_{source}",
                    START,
                    OK,
                    f"{name}后核对全部通过",
                    f"{at}，{len(items)} 项",
                )
            )
            continue
        level = BAD if any(i.get("level") == BAD for i in bad) else WARN
        detail = "；".join(f"{i.get('title')}：{i.get('detail', '')}" for i in bad)
        fix = "；".join(str(i["fix"]) for i in bad if i.get("fix"))
        out.append(
            Finding(
                f"install_{source}",
                START,
                level,
                f"{name}后核对有 {len(bad)} 项没过",
                f"{at}，{detail}"[:600],
                fix[:400],
            )
        )
    return out


# ---- 在线检查（服务开着时）--------------------------------------------------


def check_tasks(tasks: Mapping[str, Any]) -> list[Finding]:
    """后台任务（asyncio.Task）停了就是「假死」的常见原因。"""
    out = []
    for name, task in tasks.items():
        if task is None:
            continue
        if not task.done():
            continue
        if task.cancelled():
            out.append(Finding(f"task_{name}", HANG, WARN, f"后台的{name}停了", "被取消"))
            continue
        exc = task.exception()
        if exc is not None:
            out.append(
                Finding(
                    f"task_{name}",
                    HANG,
                    BAD,
                    f"后台的{name}出错停了",
                    redact(f"{type(exc).__name__}: {exc}")[:300],
                    "关掉圆桌再打开；反复出现就把体检报告发给我们",
                )
            )
        else:
            out.append(Finding(f"task_{name}", HANG, WARN, f"后台的{name}已经结束", ""))
    running = [n for n, t in tasks.items() if t is not None and not t.done()]
    if running and not out:
        out.append(Finding("tasks", HANG, OK, "后台任务都在跑", "、".join(running)))
    return out


def check_channels(channels: Mapping[str, Mapping[str, Any]]) -> list[Finding]:
    if not channels:
        return []
    down = {n: c.get("reason") or "不可用" for n, c in channels.items() if not c.get("available")}
    if len(down) == len(channels):
        return [
            Finding(
                "channels",
                HANG,
                BAD,
                "模型渠道全都不可用",
                "；".join(f"{n}：{r}" for n, r in sorted(down.items())),
                "检查 .env 里的 key，改完重启圆桌",
            )
        ]
    if down:
        return [
            Finding(
                "channels",
                HANG,
                INFO,
                f"{len(channels) - len(down)} 个渠道可用，{len(down)} 个没开",
                "；".join(f"{n}：{r}" for n, r in sorted(down.items())),
            )
        ]
    return [Finding("channels", HANG, OK, "模型渠道都可用", f"{len(channels)} 个")]


def check_budget(month: Mapping[str, Any] | None, day: Mapping[str, Any] | None) -> list[Finding]:
    out = []
    for name, s in (("本月", month), ("今天", day)):
        if not s:
            continue
        text = f"{name}已用 ${float(s['spent_usd']):.2f} / ${float(s['limit_usd']):.2f}"
        if s.get("exhausted"):
            out.append(
                Finding(
                    f"budget_{name}",
                    HANG,
                    BAD,
                    f"{name}的预算用完了",
                    text + "，新的作业会被拦下",
                    "等重置，或在 config/roundtable.yaml 的 budget 里调高上限",
                )
            )
        elif s.get("warn"):
            out.append(Finding(f"budget_{name}", HANG, WARN, f"{name}的预算快用完了", text))
    if not out and month:
        out.append(Finding("budget", HANG, OK, "预算还够", f"本月已用 ${month['spent_usd']:.2f}"))
    return out


def recent_call_errors(conn: sqlite3.Connection, since: datetime) -> tuple[int, int]:
    """最近的调用总数和失败数（只数数，不读内容）。"""
    try:
        row = conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN error IS NOT NULL AND error != '' THEN 1 ELSE 0 END) "
            "FROM calls WHERE created_at >= ?",
            (since.isoformat(timespec="seconds"),),
        ).fetchone()
    except sqlite3.Error:
        return 0, 0
    return int(row[0] or 0), int(row[1] or 0)


def check_calls(total: int, failed: int) -> list[Finding]:
    if total == 0:
        return [Finding("calls", HANG, INFO, "最近一天没有调用模型")]
    text = f"最近一天 {total} 次调用，失败 {failed} 次"
    if total >= 5 and failed >= total * 0.5:
        return [
            Finding(
                "calls",
                HANG,
                BAD,
                "最近调用大多失败",
                text,
                "看看 key 是否过期、余额是否用完、网络是否通",
            )
        ]
    if total >= 5 and failed >= total * 0.2:
        return [Finding("calls", HANG, WARN, "最近调用失败偏多", text)]
    return [Finding("calls", HANG, OK, "最近调用正常", text)]


def check_sandbox(reason: str | None) -> list[Finding]:
    if reason:
        return [
            Finding(
                "sandbox",
                HANG,
                WARN,
                "代码沙箱不可用",
                reason[:300],
                "运行 python scripts/setup_sandbox.py；不装也行，成员只是不能跑代码",
            )
        ]
    return [Finding("sandbox", HANG, OK, "代码沙箱可用")]


def check_dsh(status: Mapping[str, Any] | None) -> list[Finding]:
    """dsh 是可拔掉的外设：没带或没启用都不算问题。"""
    if not status or not status.get("enabled"):
        return []
    if not status.get("installed"):
        return [
            Finding(
                "dsh",
                HANG,
                INFO,
                "dsh 工作台还没装",
                str(status.get("unavailable") or ""),
                "需要时运行 python scripts/setup_dsh.py",
            )
        ]
    last = status.get("last_exit")
    if status.get("state") == "stopped" and last:
        return [Finding("dsh", HANG, WARN, "dsh 工作台上次异常退出", str(last)[:300])]
    return [Finding("dsh", HANG, OK, "dsh 工作台正常", str(status.get("state") or ""))]


# ---- 体检报告 ----------------------------------------------------------------

_LEVEL_MARK = {OK: "[正常]", INFO: "[提示]", WARN: "[留意]", BAD: "[问题]"}


def scrub_paths(text: str, home: str | None = None) -> str:
    """把用户目录换成 ~，报告里不带电脑上的用户名。"""
    home = home if home is not None else str(Path.home())
    if home and len(home) > 3:
        text = text.replace(home, "~").replace(home.replace("\\", "/"), "~")
    return text


def render_report(
    summary: Mapping[str, Any], meta: Mapping[str, Any], home: str | None = None
) -> str:
    """可以整段复制转交的体检报告：不含聊天内容和 key，已去掉已知密钥和用户目录。"""
    lines = [
        "# 圆桌体检报告",
        "",
        f"- 时间：{meta.get('at', '')}",
        f"- 版本：{meta.get('version', '未知')}",
        f"- 系统：{meta.get('os', '')}；Python {meta.get('python', '')}",
        f"- 结论：{summary.get('headline', '')}",
        "",
    ]
    findings = list(summary.get("findings", []))
    for scene in summary.get("scenes", []):
        mine = [f for f in findings if f["scene"] == scene["id"]]
        if not mine:
            continue
        lines.append(f"## {scene['name']}")
        for f in mine:
            line = f"- {_LEVEL_MARK.get(f['level'], '')} {f['title']}"
            if f.get("detail"):
                line += f"：{f['detail']}"
            lines.append(line)
            if f.get("fix") and f["level"] in (WARN, BAD):
                lines.append(f"  - 怎么办：{f['fix']}")
        lines.append("")
    lines.append("（这份报告只有检查结果，不含聊天内容、记忆和 key。）")
    return scrub_paths(redact("\n".join(lines)), home)


def meta_info(version: str | None, now: datetime) -> dict[str, str]:
    return {
        "at": now.isoformat(timespec="seconds"),
        "version": version or "未知",
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version() or sys.version.split()[0],
    }


# ---- 离线自检（命令行 roundtable doctor 用）-------------------------------------


def offline_findings(
    *,
    db_path: Path | None,
    data: Path | None,
    config_error: str | None,
    keys_present: Iterable[str] = (),
    keys_missing: Iterable[str] = (),
    channels_ok: bool = False,
    backup_files: list[Mapping[str, Any]] | None = None,
    backup_folder: Path | None = None,
    backup_enabled: bool = True,
    now: datetime,
) -> list[Finding]:
    """不需要服务、不迁移数据库就能做的检查（网页自检也会先做这些）。"""
    out = check_config(config_error)
    if config_error is None:
        out += check_keys(keys_present, keys_missing, channels_ok=channels_ok)
    conn = open_readonly(db_path) if db_path is not None else None
    try:
        out += check_database(conn, db_path)
        if data is not None:
            out += check_disk(data)
        files = backup_files or []
        if db_path is not None and db_path.exists():
            out += check_backups(files, backup_enabled, now)
        if conn is not None and backup_folder is not None and files:
            out += compare_with_backup(conn, backup_folder, files)
    finally:
        if conn is not None:
            conn.close()
    if data is not None:
        out += check_update(read_json(data / UPDATE_STATE), now)
        out += check_install(read_json(data / INSTALL_CHECK))
    return out


def list_backups(folder: Path | None) -> list[dict[str, Any]]:
    """备份目录里圆桌自己生成的备份（服务没开时用；不打开数据库）。"""
    if folder is None or not folder.is_dir():
        return []
    out = []
    for p in folder.iterdir():
        f = parse_name(p.name, p.stat().st_size) if p.is_file() else None
        if f is not None:
            out.append(f.view())
    return sorted(out, key=lambda f: str(f["at"]), reverse=True)


def compare_with_backup(
    conn: sqlite3.Connection, folder: Path, files: list[Mapping[str, Any]]
) -> list[Finding]:
    """和最近一份备份比关键数据的条数（只读打开备份）。"""
    latest = max(files, key=lambda f: str(f["at"]))
    other = open_readonly(folder / str(latest["name"]))
    if other is None:
        return []
    try:
        before = table_counts(other, WATCHED_TABLES)
    finally:
        other.close()
    return check_data_drop(table_counts(conn, WATCHED_TABLES), before, str(latest["name"]))
