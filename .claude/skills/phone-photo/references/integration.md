# 接进代码：现状、推荐做法、测试清单

## 现状（以 moment_photo v4 为准，PR #150）

数据流：
1. `Moments.post()`（`core/moments.py`）发动态时调 `photo(member, event, text)`。
2. `photo()` 判断开关、概率 `chance`、每日上限 `per_day` / `per_member_day`，用 `random.Random(f"photo:{event['id']}:{member.id}")` 按权重从 `rules.shots` 抽一种拍法。
3. 调 `MemberStickers.photo(member_id, scene, post, shot_text, refs=..., seed=...)`（`core/member_stickers.py`）：
   读形象卡 `card(member)`（`look`、`must`、`daily_must`、`cues`），附形象参考图（`her != "none"` 时）和房间图（`room_refs`，放最后一张），
   `rt.prompts.render("moment_photo", version, look=…, must=…, cues=…, shot=…, scene=…, post=…, room=…)` 拼提示词，按 `draw_models()` 顺序调绘图模型，
   成功后 `phone_look()` 加手机味、`_jpeg()` 压到 `max_side`。
4. 花费记在表情包的隐藏会话，受表情包每月小上限和创作输出月上限约束。

配置：`config/roundtable.yaml` → `media.moment_photos`（`MomentPhotoRules`：`enabled`、`chance`、`per_member_day`、`per_day`、`max_side`、`shots`、`phone_look`、`room_refs`）；
`config/personas.yaml` → `sticker_cards.<vendor>`（`StickerCard`）。

## 推荐做法（加摄影维度 + 个人习惯）

1. **schema**：在 `MomentPhotoRules` 加通用维度表，例如：
   ```python
   class PhotoOption(_Strict):
       name: str  # 稳定标识，供个人 boost 引用
       text: str  # 英文短语，直接进提示词
       weight: float = 1.0
       when: list[str] = []  # 可选条件：时间段（morning/day/evening/night）或地点标签；空 = 都可用
   ```
   `angles` / `lights` / `moments` / `lenses` / `filters` 各一张 `list[PhotoOption]`；`MomentShot` 也补一个 `name`。
   `StickerCard` 加可选 `photo: PhotoHabit | None`（`subjects`、`boost`、`quirk`），校验 `boost` 里引用的名字存在于对应表。
2. **抽取**：新建纯函数（建议 `core/moment_shots.py` 或放在 `moments.py`）`pick_photo_spec(rules, habit, event, member_id, her_in_photo) -> dict[str, str]`：
   每个维度用独立种子 `random.Random(f"photo:{dim}:{event_id}:{member_id}")`，权重 = 通用权重 × 个人倍数（拍摄者是她本人时才乘），按 `when` 过滤；
   `her == "none"` 时跳过人像相关维度（机位里只留俯拍 / 平拍物体）。返回值全部是英文短语。
3. **提示词**：新建 `prompts/moment_photo/v5.md`，在 v4 基础上加变量（如 `{{ camera }}`、`{{ light }}`、`{{ moment }}`），删掉与之重复的固定句；
   画风、公寓场景、不扛武器、面部照设定图、cues 这些 v4 的规定全部保留。`python scripts/lock_prompts.py` 登记，`config/roundtable.yaml` 的 `prompts.moment_photo: v5`。
4. **时间段**：如果事件里有时间（生活引擎事件 / 主时区当前时间），映射成 morning/day/evening/night 给 `when` 用；拿不到就不过滤。
5. **默认值**：把 `references/factors.md` 的取值写进 `config/roundtable.yaml`（权重按「手机现实」：1x 主摄、原色、抓拍为主），schema 默认值可以留短表。

## 测试清单（tests/ 结构与 src/ 对应）

- schema：新字段默认值、多余字段报错、`boost` 引用不存在的名字报错。
- 抽取函数：同一事件 + 成员 → 结果可复现；不同维度互不影响；`when` 过滤生效；`her == "none"` 不出人像机位；个人 boost 改变分布（大样本统计或固定种子断言）；朋友帮拍时不用个人 boost。
- 提示词：v5 渲染结果包含抽到的短语；不含任何模型名 / 厂商名 / 昵称（现有提示词测试会查）；`versions.lock` 已登记。
- 端到端（假绘图模型）：`Moments.post()` 产生的提示词里带维度短语；`phone_look` 照旧；预算、上限逻辑不变。
- 中立 / 隔离：工作模式代码路径不读 `photo` 习惯（可加一条 import / 读取守卫测试，参照现有工作 / 生活隔离测试）。

## 不要做

- 不要真调绘图模型试效果（花钱，要用户点头）。
- 不要写 `if member == ...` / `if vendor == ...`；差异全部来自配置。
- 不要改已发布的 `prompts/moment_photo/v1–v4`。
- 不要提交 `data/`、`.env`、生成的样图。
