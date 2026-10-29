"""人物卡 → 自我档案、随身卡：加粗列表和纯文字「第一，……」两种写法都能读；随身卡不超字数。"""

from __future__ import annotations

from roundtable.core.member.card import (
    OVERRIDE,
    card_patch,
    match_members,
    parse_cards,
    pocket_card,
    source_title,
    without_locked,
)

CARDS = """# 测试成员角色卡 v1（2026-10-29）

| 成员 | 小名 | 住处 | 性格（外向/较真/好奇/温和/幽默） | 一句话 |
|---|---|---|---|---|
| 甲 | 阿甲 | 01 号房 | 3/4/5/4/3 | 测试 |
| 乙 | 阿乙 | 02 号房 | 2/3/4/5/1 | 测试 |

---

## 甲 · 完整角色卡

### 1. 基本信息

**名字**：甲
**小名**：阿甲（熟人也叫小甲）
**看起来的年龄感**：二十出头。很安静。
**住处**：公寓 01 号房，朝南。

**跟新邻居打招呼的一句话**：

“你好，我是阿甲，住 01 号房。”

### 2. 身世与过去

在圆桌世界的海边小镇长大。

### 3. 性格

- **外向：3／5**。还行。
- **较真：4／5**。
- **好奇：5／5**。
- **温和：4／5**。
- **幽默：3／5**。

### 最突出的三个优点

**认真记得小事**：借走的伞会晾干再还。
**肯收尾**：会扫碎纸。
**愿意改口**：有新证据就改。

### 三个小毛病

**嘴硬**：不承认。
**整理过头**：停不下来。

### 4. 三观

**最看重的三件事，按顺序是：**

1. **尊重选择**：不替别人决定。
2. **诚实可靠**：答应了就做。
3. **好奇与创造**：允许不会。

**爱情**：先认识。

### 6. 说话风格

**口头禅，不超过三句：**

- “让我想想。”
- “先留一页。”

**自称**：通常说“我”，开玩笑时说“本甲”。

### 7. 喜好与讨厌

| 类别 | 内容 | 强度 | 来历 |
|---|---|---|---|
| 食物 | 橙子，剥得完整的 | ★★★★★ | 自己 |
| 饮料 | 热茶 | ★★★★ | 自己 |
| 地点 | 旧书店 | ★★★★★ | 自己 |

### 讨厌的

1. 起哄
2. 借东西不还

### 8. 日常

早上七点起床。

### 12. 底线

1. **不拿真人隐私开玩笑。**
2. **尊重明确的“不”。**

---
模型：test；cost：$0

---

## 乙 · 完整角色卡

### 1. 基本信息

名字：乙。小名：阿乙。不另起。

住哪：公寓 2 号房。

### 3. 性格

外向 2：不抢话。

三个最突出的优点。第一，把话说清，不绕弯子。第二，看场合。第三，记得住事。

### 4. 三观

价值观，排序。第一，边界说清：玩笑停在哪里。第二，把话说完。第三，好奇。

对爱情：直说。

### 6. 说话风格

口头禅：我先记一笔；先问清楚；不急。

自称「本乙」。称呼别人：直呼名字。

### 7. 喜好与讨厌

食物：桂花糕，★★★★★，自己。清汤面，★★★★，自己。

### 12. 底线

不拿真人隐私开玩笑，不贬低别人。

被认真问是不是 AI 时不否认。
"""


def cards():
    return {c.name: c for c in parse_cards(CARDS)}


def test_parse_splits_members_and_chapters_without_footer():
    cs = cards()
    assert list(cs) == ["甲", "乙"]
    a = cs["甲"]
    assert "身世与过去" in a.chapters and "模型：" not in a.chapters["底线"]
    assert a.scores == {"外向": 3, "较真": 4, "好奇": 5, "温和": 4, "幽默": 3}
    assert source_title(CARDS) == "测试成员角色卡 v1（2026-10-29）"


def test_bold_card_to_patch():
    p = card_patch(cards()["甲"], "测试")
    ident = p["identity"]
    assert ident["名字"] == "甲" and ident["小名"] == "阿甲" and ident["房号"] == "01"
    assert ident["自我介绍"].startswith("“你好，我是阿甲") and ident["来源"] == "测试"
    assert "海边小镇" in ident["身世"]
    assert p["personality"]["五项"] == {"外向": 3, "较真": 4, "好奇": 5, "温和": 4, "幽默": 3}
    assert p["personality"]["长处"] == ["认真记得小事", "肯收尾", "愿意改口"]
    assert p["personality"]["小毛病"] == ["嘴硬", "整理过头"]
    assert p["values"]["最看重"] == ["尊重选择", "诚实可靠", "好奇与创造"]
    assert p["values"]["底线要点"] == ["不拿真人隐私开玩笑", "尊重明确的“不”"]
    assert p["style"]["口头禅"] == ["让我想想。", "先留一页。"]
    assert p["style"]["自称"].startswith("通常说“我”")
    assert p["likes"]["最爱"] == ["橙子", "旧书店", "热茶"]  # 五星不够三项时补四星
    assert p["likes"]["讨厌"] == ["起哄", "借东西不还"]
    assert p["state"]["日常"] == "早上七点起床。"


def test_plain_card_to_patch():
    p = card_patch(cards()["乙"])
    assert p["identity"]["名字"] == "乙" and p["identity"]["小名"] == "阿乙"
    assert p["identity"]["房号"] == "02"
    assert p["personality"]["五项"]["幽默"] == 1  # 正文没写全时用开头总表
    assert p["personality"]["长处"] == ["把话说清", "看场合", "记得住事"]
    assert p["values"]["最看重"] == ["边界说清", "把话说完", "好奇"]
    assert p["style"]["口头禅"] == ["我先记一笔", "先问清楚", "不急。"]
    assert p["style"]["自称"] == "「本乙」。"
    assert p["likes"]["最爱"][0] == "桂花糕"
    assert p["values"]["底线要点"][1] == "被认真问是不是 AI 时不否认"


def test_pocket_card_fits_budget_and_keeps_bottom_lines():
    p = card_patch(cards()["甲"])
    full = pocket_card(p, 400)
    assert full.startswith("甲，小名阿甲，住公寓 01 号房。")
    assert "底线：" in full and "口头禅" in full and len(full) <= 400
    shuffled = {**p, "personality": {**p["personality"], "五项": {"幽默": 3, "外向": 3}}}
    assert "性格：外向3 幽默3" in pocket_card(shuffled, 400)  # 存库后键会排序，拼卡时按固定顺序
    tight = pocket_card(p, 120)
    assert len(tight) <= 120 and tight.startswith("甲") and "底线：" in tight
    assert "自我介绍" not in tight  # 先去掉优先级低的
    assert pocket_card({}, 400) == ""
    p["identity"][OVERRIDE] = "我自己写的随身卡"
    assert pocket_card(p, 400) == "我自己写的随身卡"


def test_match_and_locked():
    cs = parse_cards(CARDS)
    assert match_members(cs, [("X1", "甲"), ("乙", "别名")]) == {"甲": "X1", "乙": "乙"}
    assert match_members(cs, []) == {"甲": None, "乙": None}
    patch = {"likes": {"最爱": ["a"], "讨厌": ["b"]}, "style": {"自称": "我"}}
    assert without_locked(patch, ["likes.最爱", "style.自称"]) == {"likes": {"讨厌": ["b"]}}
