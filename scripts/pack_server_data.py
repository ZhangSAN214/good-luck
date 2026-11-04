"""把电脑上的圆桌数据打成一个包，搬到云服务器（docs/SERVER.md）。

    python scripts/pack_server_data.py                 # 生成 data/_cloud/server_pack.zip
    python scripts/pack_server_data.py -o D:/pack.zip

数据库用 SQLite 在线备份取快照（圆桌开着也能打包，但建议先关掉，免得打包后又有新聊天没带走）。
不带走的：各种浏览器登录状态（ChatGPT、邮箱、成员上网）、dsh、超级电脑、局域网口令、截图、
日志、缓存和旧备份——这些要么只在电脑上用，要么在服务器上重新生成。
包里不含 .env，key 要在服务器上自己填。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
import zipfile
from collections.abc import Iterator, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_NAME = "roundtable.db"

# data/ 下不搬的子目录（第一层）
SKIP_DIRS = frozenset(
    {
        "_chatgpt",  # ChatGPT 网页登录状态（画图留在电脑上）
        "mail_browser",  # 邮箱登录状态
        "wander",  # 成员上网的浏览器资料夹（含登录状态）
        "dsh",  # dsh 工作台（只在电脑本机）
        "super",  # 超级电脑（调用电脑上的编程助手）
        "desktop",  # 桌面版窗口设置
        "_cloud",  # 打包输出目录本身
        "_check",
        "_comfyui_setup",
        "_logs",
        "logs",
        "_shots",
        "shots",
        "screens",
        "_moment_preview",
        "stickers_preview",
        "roundtable-source",
        "db_backups",  # 旧备份；服务器上每天重新备份
        "voice_cache",  # 缓存，可重新生成
        "video_review",
    }
)
# data/ 下不搬的文件
SKIP_FILES = frozenset({"lan_access.json"})  # 服务器上重新设口令
SKIP_SUFFIXES = (".db-wal", ".db-shm", ".db-journal", ".tmp", ".lock")


def iter_files(data: Path) -> Iterator[Path]:
    """要搬的文件（不含数据库本身，它单独取快照）。"""
    for path in sorted(data.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(data)
        if rel.parts[0] in SKIP_DIRS:
            continue
        if len(rel.parts) == 1 and (rel.name in SKIP_FILES or rel.name == DB_NAME):
            continue
        if path.name.endswith(SKIP_SUFFIXES) or path.name == ".env":
            continue
        yield path


def snapshot_db(src: Path, dest: Path) -> None:
    """SQLite 在线备份：圆桌开着也能拿到一致的快照。"""
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(dest)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def pack(data: Path, out: Path) -> tuple[int, int]:
    """打包，返回（文件数，字节数）。

    包内路径以 data/ 开头，在服务器的 /opt/roundtable 下解压即可。
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        db = data / DB_NAME
        if db.is_file():
            with tempfile.TemporaryDirectory() as tmp:
                snap = Path(tmp) / DB_NAME
                snapshot_db(db, snap)
                zf.write(snap, f"data/{DB_NAME}")
                count += 1
        for path in iter_files(data):
            zf.write(path, "data/" + path.relative_to(data).as_posix())
            count += 1
    return count, out.stat().st_size


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把圆桌数据打包搬到云服务器")
    parser.add_argument("--data", type=Path, default=ROOT / "data", help="数据目录（默认 data/）")
    parser.add_argument(
        "-o", "--out", type=Path, help="输出文件（默认 data/_cloud/server_pack.zip）"
    )
    args = parser.parse_args(argv)
    data: Path = args.data
    if not data.is_dir():
        print(f"找不到数据目录：{data}", file=sys.stderr)
        return 1
    out = args.out or data / "_cloud" / "server_pack.zip"
    count, size = pack(data, out)
    print(f"已打包 {count} 个文件，{size / 1024 / 1024:.1f} MB → {out}")
    print("下一步见 docs/SERVER.md 第 5 步：用 scp 传到服务器再解压。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
