"""命令行显示用的中文名称表和小工具（从 cli.py 拆出，代码精简）。"""

from __future__ import annotations

from typing import Any

MODE_LABELS = {"single": "单人办", "meeting": "开会", "project": "项目分工", "bench": "交给工作台"}


def _group_line(mode: str, group: dict[str, Any]) -> str:
    """提交前预估里的一段：开会（2～3 人）约 $a～$b。"""
    (low_n, high_n), (low, high) = group["people"], group["usd"]
    people = f"{low_n}" if low_n == high_n else f"{low_n}～{high_n}"
    span = f"${low:.4f}" if low_n == high_n else f"${low:.4f}～${high:.4f}"
    return f"{MODE_LABELS[mode]}（{people} 人）约 {span}"


IMAGE_EXTS = ("png", "jpg", "jpeg", "webp", "gif")


STICKER_LABELS = {"missing": "还没画", "pending": "待审", "approved": "可以用", "rejected": "不要"}


MEDIA_NAMES = {"image": "图片", "speech": "语音", "video": "视频", "music": "音乐"}


JOB_STATE = {
    "submitted": "已提交",
    "pending": "排队中",
    "running": "生成中",
    "completed": "完成",
    "failed": "失败",
    "timeout": "超时",
}


STANCE_NAMES = {"want": "想做", "can": "可以做", "unfit": "不适合"}


LEVEL_NAMES = {"full": "全部采用", "partial": "部分采用", "none": "未采用"}


SOURCE_NAMES = {"rule": "规则判断", "model": "规划员判断", "default": "默认"}


KIND_NAMES = {
    "answered": "作答",
    "adopted": "被采纳的要点",
    "volunteer_accepted": "自荐被采纳",
    "valid_review": "有效评审",
    "valid_issue": "指出的有效问题",
    "issue_accepted": "被作者采纳的问题",
    "redo": "被打回重做",
    "lazy": "敷衍",
    "dropped": "退出",
}


TOOL_NAMES = {
    "python": "运行代码",
    "write_file": "写文件",
    "generate_image": "生成图片",
    "search": "联网搜索",
    "fetch": "读取网页",
}


TOOL_STATUS = {
    "ok": "成功",
    "error": "出错",
    "timeout": "超时",
    "rejected": "被拒绝",
    "limit": "额度已用完",
}


FILE_KINDS = {
    "image": "图片",
    "audio": "音频",
    "video": "视频",
    "code": "代码",
    "text": "文本",
    "table": "表格",
    "document": "文档",
}


KIND_LABELS = {"image": "图片", "pdf": "PDF", "docx": "Word", "text": "文本", "audio": "音频"}


LENGTH_NAMES = {"simple": "短", "medium": "中等", "hard": "长", None: "未判断"}


def _indent(text: str) -> str:
    return "\n".join("    " + line for line in text.splitlines())
