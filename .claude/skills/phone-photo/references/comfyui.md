# 用 ComfyUI 出图（本地，不花 API 钱）

用户希望配图最好能走 ComfyUI。仓库目前**没有** ComfyUI 接入；下面是接入方案和 ComfyUI 下的提示词写法。
用户电脑：RTX 4060 8GB、内存 16GB（见项目记忆）。ComfyUI 跑在用户电脑上，圆桌服务也跑在那台电脑上，所以走 `http://127.0.0.1:8188`。

## 接入方案（照「加渠道只改配置」）

1. **适配器**：新建 `src/roundtable/core/providers/comfyui.py`，`@register_adapter("comfyui")`，只实现 `generate_image()`（`complete()` 抛 `UnsupportedCapability`）。
   流程：读工作流模板 → 填占位符 → `POST /prompt` → 轮询 `GET /history/{prompt_id}`（间隔、超时走配置）→ `GET /view?filename=…` 取图 → 返回 `ImageOutput`（`cost_usd=0.0`）。
   参考图：先 `POST /upload/image` 上传，把返回的文件名填进模板里的 LoadImage 节点。
   规则：适配器**不重试**（失败抛错，重试 / 换模型由路由负责）；错误文本脱敏截断、`from None`；不需要 key（`key_env: null`）。
2. **渠道**（`config/models.yaml`）：
   ```yaml
   comfyui:
     adapter: comfyui
     kind: local
     base_url: http://127.0.0.1:8188
     key_env: null
   ```
3. **模型条目**：`seat: false`、`tier: budget`、`price` 全 0、`tags: [image_gen, image_edit]`（有 IPAdapter 节点时才写 `image_edit`）、`enabled: false`（默认关，用户装好 ComfyUI 后在 `config/*.local.yaml` 打开）。
   路由里的 `model:` 字段指向**工作流模板文件名**，例如 `moment_photo_sdxl.json`；模板放 `config/comfyui/`（仓库只放示例模板，不放模型权重）。
4. **工作流模板**：用户在 ComfyUI 里搭好后「导出 API 格式」的 JSON。约定占位符字符串（适配器做字符串替换，不认具体节点 id）：
   `"{{prompt}}"`、`"{{negative}}"`、`"{{seed}}"`（数字）、`"{{width}}"`、`"{{height}}"`、`"{{ref_1}}"…"{{ref_3}}"`（上传后的图片文件名；没有参考图时用模板里的默认图或去掉 IPAdapter 分支，由模板变体决定）。
   模板里至少有一个 SaveImage 节点；适配器取它的第一张输出。
5. **可用性**：ComfyUI 没开时连接失败 → 适配器抛错 → 路由切到下一个绘图模型（云端付费模型），或按现有逻辑「画不出来就只发文字」。
   要不要自动退回付费模型由用户定，建议加开关 `media.moment_photos.local_only`（默认 false）。
6. **中立性**：本地模型和其他绘图模型一样只按档位、标签、价格选；价格为 0 时自然排第一，不需要也不允许写「优先 ComfyUI」的分支。

## ComfyUI 下的提示词写法

SDXL 系二次元模型（如 Illustrious / NoobAI / Pony 系）吃**标签式**提示词，自然语言长段落效果差。两条路：

- **简单路**：继续用 `prompts/moment_photo` 的自然语言版本，模板里接一个支持长文本的编码节点。效果不稳定，只做兜底。
- **推荐路**：新增提示词 `prompts/moment_photo_tags/v1.md`，输出两段：正向标签、负向标签（用 `<!-- user -->` 下的固定格式，适配器按分隔行拆成 `{{prompt}}` / `{{negative}}`）。
  维度短语同时准备一份标签版（`PhotoOption` 加可选 `tags` 字段），走 ComfyUI 时用 `tags`，走对话式绘图模型时用 `text`。

标签映射示例（摄影要素 → 标签）：

| 要素 | 标签 |
|---|---|
| 自拍 | `selfie, arm's length, from above` |
| 镜子自拍 | `mirror selfie, holding phone, reflection` |
| 背影 / 偷拍 | `from behind, candid, small figure` |
| 俯拍桌面 | `from above, table, food focus, pov hands` |
| 低机位 | `from below` |
| 隔门框 | `doorway, framed, depth of field` |
| 超广角 | `wide shot, fisheye` |
| 窗光 | `window light, side lighting` |
| 逆光 | `backlighting, rim light` |
| 台灯夜里 | `night, desk lamp, dim lighting` |
| 屏幕光 | `screen light, dark room` |
| 黄金时刻 | `sunset, golden hour, orange sky` |
| 蓝调 | `blue hour, twilight, city lights` |
| 抓拍笑 | `laughing, candid, motion blur` |
| 吃到一半 | `eating, cheek bulge` |

负向标签固定一套：`worst quality, low quality, watermark, text, signature, speech bubble, border, extra fingers, deformed hands, nsfw, cleavage focus, weapon`（日常照禁止武器、卖弄身材，与用户硬反馈一致）。
**角色一致性**：八位成员是原创角色，底模不认识她们。8GB 显存可行的做法是 IPAdapter 用设定图做参考（模板里接），长期可以给每位成员训 LoRA（要用户点头，训练在本机做）。
没有 IPAdapter 时，用形象卡 `look` / `must` 翻成标签（例：`white hair, dragon horns, dragon tail, dragon wings, white dress`），一致性会明显差一些。

## 测试（不联网）

- 用 `httpx.MockTransport`（或项目现有 `_http.py` 的假传输方式）模拟 `/prompt`、`/history`、`/view`、`/upload/image`：成功出图、排队超时、ComfyUI 未启动、返回节点错误。
- 占位符替换：数字型 seed 不带引号、参考图数量少于占位符时的处理。
- 渠道配置校验：多余字段报错；`key_env: null` 允许。
- 路由：本地模型价格 0 时排在付费绘图模型前面；本地失败后切到下一个（`max_retries=0`，不原样重试）。
- 提示词：标签版提示词同样不含模型名 / 厂商名 / 昵称。
