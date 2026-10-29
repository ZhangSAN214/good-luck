"""领域标签：学术 / 日常 / 工作 / 娱乐 / 社交 / 身心（``chat.memory.domains``）。

夜间整理时由模型给每件事标一个；模型没标或标了表外的，用这里的关键词表兜底（程序，不调用模型）。
所有成员用同一张表（中立）。
"""

from __future__ import annotations

from collections.abc import Sequence

DEFAULT = "日常"
WORK = "工作"
# 关键词兜底：按顺序比对，先命中的算
KEYWORDS: dict[str, tuple[str, ...]] = {
    "工作": ("工作", "办公室", "开会", "项目", "交付", "汇报", "加班", "同事", "派活", "上班"),
    "学术": (
        "论文",
        "考试",
        "作业",
        "课程",
        "上课",
        "复习",
        "学习",
        "研究",
        "实验",
        "数学",
        "物理",
    ),
    "身心": (
        "睡",
        "累",
        "病",
        "健身",
        "跑步",
        "心情",
        "焦虑",
        "难过",
        "开心",
        "压力",
        "休息",
        "吃药",
    ),
    "娱乐": (
        "游戏",
        "电影",
        "番",
        "音乐",
        "听歌",
        "小说",
        "看球",
        "综艺",
        "动漫",
        "追剧",
        "演唱会",
    ),
    "社交": ("聊天", "朋友", "约", "聚", "一起", "生日", "礼物", "吵架", "和好", "群里"),
}


def normalize(domain: str | None, domains: Sequence[str]) -> str | None:
    """模型给的标签：在表里就用，不在返回 None。"""
    d = (domain or "").strip()
    return d if d in domains else None


def classify(text: str, domains: Sequence[str]) -> str:
    """关键词兜底；都没命中归「日常」（不在表里时用表的第一个）。"""
    for name, words in KEYWORDS.items():
        if name in domains and any(w in text for w in words):
            return name
    return DEFAULT if DEFAULT in domains else domains[0]


def tag(text: str, given: str | None, domains: Sequence[str]) -> str:
    return normalize(given, domains) or classify(text, domains)
