# 日历底座（2026-10-29）

成员系统的底层插件之一：**只回答「今天是什么日子」**，把结果作为事件发到成员事件总线（`member_events`）。成员怎么过节（发朋友圈、群里祝福、给你发一句）由生活引擎 / 成员系统决定，日历不管；公告栏上面一栏显示今天和未来几天。

代码在 `src/roundtable/core/almanac/`，规则在 `config/roundtable.yaml` 的 `calendar`，成员生日在 `config/personas.yaml` 的 `birthdays`。

## 原则

- **离线、免费、不调模型**：各国法定假日用开源库 [holidays](https://github.com/vacanza/holidays)（MIT，纯 Python，含中国调休补班）；农历和二十四节气查预先算好的表 `lunar_table.json`（2000～2100 年，北京时间，由 `scripts/build_lunar_table.py` 用开源库 lunar_python 生成，运行时不需要它）。
- **只影响生活**：聊天、朋友圈、生活可以用；讨论、协同、办公室、个人作答、头脑风暴不挂载（`tests/core/member/test_mode.py` 静态检查工作代码不引入 `core.almanac`）。
- **中立**：所有成员看到的日历完全一样；只有「某某的生日 / 入职纪念日」这两种条目带成员。
- **你的数据只在本机**：你加的纪念日存在本机 SQLite 的 `app_settings`（`calendar.user_dates`），不进 git、不上传。

## 有哪些日子

| 种类 `kind` | 从哪来 | 例子 |
|---|---|---|
| `holiday` 法定假日 | `calendar.rest`（成员的休息日，默认英格兰）+ `calendar.cultures`（也过但不放假：中国、美国、日本） | 耶稣受难日（成员放假）、国庆节（中国放假）、感恩节（美国放假）、调休放假 |
| `workday` 调休上班 | holidays 的中国补班表 | 2026-02-14 中国调休上班 |
| `lunar` 农历节日 | `calendar.lunar_festivals`（农历 MM-DD 或 12-last） | 春节、元宵、端午、七夕、中秋、重阳、腊八、小年、除夕 |
| `term` 节气 | 二十四节气表（`calendar.solar_terms`） | 立冬、冬至 |
| `observance` 不放假的节日 | `calendar.observances`（规则：`MM-DD`、`easter±N`、`2-sun-05`、`last-sun-10`） | 情人节、英国母亲节、万圣夜、篝火夜、夏令时切换、双十一 |
| `birthday` 成员生日 | `personas.yaml` 的 `birthdays`（默认取各自模型首次公开发布的日子，可改；02-29 在平年按 02-28 过） | 小白龙的生日 |
| `joined` 入职纪念日 | 成员第一次在圆桌聊天里说话的那天（自动算，第 1 年起） | 小K入职 1 周年 |
| `user` 你的纪念日 | 公告栏「我的纪念日」或接口添加；可写年份（算第几周年）、可选农历 | 来曼城的日子（第 1 年） |

同一天名字相同的合并成一条（例如元旦：英国放假，中国、美国、日本也放假；农历除夕 = 除夕）。外文节日名经 `calendar.names` 翻成中文。

**休息日**：周末，或 `rest` 地区的法定假日（`day()["rest"]`、`Almanac.is_rest_day()`）。

## 事件（写进成员事件总线）

生活后台循环每次推进时，日历在**每天第一次**发：

- `calendar.day`：`member = null`，`data = {date, weekday, lunar, rest, items: [...]}`。
- `calendar.upcoming`：`upcoming_days`（默认 3）天后的每个日子一条（节气、补班不发）；成员生日 / 入职纪念日的 `member` 是那位成员，`data` 是条目本身加 `in_days`。

关机期间错过的日子不补发。事件跟其他成员事件一样只存本机，可一键清空。

## 接口

- `GET /api/calendar?days=7&start=YYYY-MM-DD`：从 start（默认今天）起 days 天（默认 `board_days`）的日子，以及你的纪念日列表。
- `POST /api/calendar/dates`：`{name, date: "YYYY-MM-DD" 或 "MM-DD", lunar: false}`，加一个每年都过的纪念日；写法不对返回 400。
- `DELETE /api/calendar/dates/{id}`：删掉一个；没有返回 404。

## 维护

- 中国每年 11～12 月公布下一年的放假和调休安排；holidays 库随后更新，升级即可：`pip install -U holidays`。
- 想多过几个国家的节日：在 `calendar.cultures` 加一行 ISO 国家代码（holidays 支持的都行），外文名可在 `calendar.names` 里翻译。
- 农历表要扩大年份范围时才需要运行 `scripts/build_lunar_table.py`。

## 不归日历管的

课表和 DDL（「关于你」画像，默认关）、时钟和时区（终端多时区时钟）、天气（感官）。
