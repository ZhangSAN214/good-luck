# 圆桌 Roundtable

多个 AI 模型协作完成作业：所选档位的模型**全员上桌**，一个当统筹，其余当组员，独立作答、互相评审、根据评审修订，再由统筹汇总；也可以用**协同模式**拆分子任务、分工完成、交叉审查后合并。成员可以运行代码、生成图表和文件、联网搜索，也能产出图片、语音和视频。

- 讨论模式：作答 → 互评 → 修订 → 汇总（分歧大或把握低时**询问**是否用全力模式（旗舰档）重做，从不自动升级）
- 协同模式：拆分 → 自荐 → 分配 → 并行完成 → 交叉审查 → 修改 → 合并
- 省钱靠选档位（节电模式 = 便宜档全员 / 全力模式 = 旗舰档全员 / 自选），不靠减人；执行前预估花费，超过门槛先问你
- 匿名开关：开启时界面只显示代号，结束后点「揭晓身份」；发给模型的内容在任何情况下都只用代号
- 防偷懒：空泛的作答 / 评审被打回重做，仍不合格标记「敷衍」，并按桌记录每位成员的贡献

长期目标见 `docs/REQUIREMENTS_v3.md`，实现阶段见 `docs/PLAN.md`，架构规则和约定见 `CLAUDE.md`，命令行见 `docs/CLI.md`。

## 安装

需要 Python 3.11+。

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"            # 只运行不开发可用 pip install -e .
```

可选依赖：`pip install -e ".[media]"`（视频截帧，评审者才能看到视频画面）。

Windows 可以直接双击 `start.bat`：它会创建 `.venv`、`git pull`、安装依赖、安装代码沙箱、启动服务并打开浏览器。

## 填写 key

```bash
cp .env.example .env     # 然后编辑 .env
```

所有 key 都是**可选**的：填了哪个渠道就用哪个，没有 key 的渠道自动跳过。只填 `OPENROUTER_API_KEY` 就能用全部模型。至少要能覆盖 3 个模型（2 个组员 + 1 个统筹）才能开桌。

| 变量 | 用途 |
|---|---|
| `OPENROUTER_API_KEY` | 聚合平台；默认的联网搜索 / 读取网页、图片 / 语音 / 视频生成、语音转写也走它 |
| `OPENAI_API_KEY` `ANTHROPIC_API_KEY` `GEMINI_API_KEY` `XAI_API_KEY` `DEEPSEEK_API_KEY` | 官方直连 |
| `TAVILY_API_KEY` | 联网搜索备选（直接取回网页原文） |
| `ROUNDTABLE_DB_PATH` | 数据库位置，默认 `data/roundtable.db` |

key 只能放在项目根目录的 `.env`（已在 `.gitignore` 中），不要写进任何其他文件。

## 运行

```bash
uvicorn roundtable.api.app:app --reload      # 浏览器打开 http://127.0.0.1:8000
roundtable ask '求函数 f(x)=x^3-3x 在 [-2,2] 上的最值'   # 命令行
roundtable estimate '题目'                    # 提交前预估各模式 × 各档位的花费（不调用模型）
roundtable models                             # 模型、档位、可用渠道与预算
```

服务默认只监听本机。页面里可以选择模式（讨论 / 协同）、成员档位、匿名、输出类型（文字 / 图片 / 语音 / 视频），添加附件（点按钮或拖放），输入题目时下方实时显示各档位的预计花费。

## 预算与限速

- 单题确认门槛默认 $0.30（`config/routing.yaml`）；每月预算默认 $20、每日上限 $3（`config/roundtable.yaml` 的 `budget`，按 UTC 自然月 / 日），达到 80% 提醒，用满或预计会超出时暂停并询问。
- 本桌实际花费超过预估的 1.5 倍时暂停询问；视频每次生成前都要你确认。
- **并发与限速**（`roundtable.yaml`）：`request.max_concurrent` 限制每个渠道同时在途的请求数（默认 6，全员并发调用时超过的排队），`request.requests_per_minute` 限制每分钟请求数（默认不限，达到时排队等待）；`limits.max_running_sessions` 限制同时在后台执行的讨论数（默认 3，等待你确认的不占名额，超过时返回 429）。

## 配置

| 文件 | 内容 |
|---|---|
| `config/models.yaml` | 渠道（`channels`）、模型（`models`）、联网搜索服务（`search_providers`） |
| `config/roundtable.yaml` | 座位数、互评人数、预算、步骤参数、流程顺序（`pipeline` / `collab_pipeline`）、渠道模式、请求策略与限速、上传、工具、媒体 |
| `config/routing.yaml` | 确认门槛、成员档位（`plans`）、升级条件、花费预估参数（只能引用档位和能力标签，不得出现模型名） |
| `config/personas.yaml` | 代号池 |

所有配置用 pydantic 校验（多余字段会报错），错误信息带文件名和字段路径。

### 加一个模型

只改 `config/models.yaml`，不用改代码：

```yaml
- id: my-model                 # 本项目内部使用的名字
  vendor: SomeVendor
  tier: budget                 # budget 便宜档 | flagship 旗舰档（上桌的模型必填）
  price: {input: 0.3, output: 1.2}     # 美元 / 每百万 token
  tags: [math, code]
  enabled: true
  routes:                      # 按优先顺序；失败时切换到下一个渠道
    - {channel: openrouter, model: vendor/model-id}
```

加完运行 `python scripts/check_models.py` 核对 OpenRouter 上的模型 ID 与价格。**加渠道**同理：在 `channels` 里写 `adapter`（`openai_compat` / `anthropic` / `gemini`）、`kind`（`aggregator` / `direct` / `local`）、`base_url` 和 `key_env`（只写环境变量名）。本地模型用 `kind: local` 且不需要 key。

媒体模型（图片、语音、转写、视频）同样在这里配置：`seat: false`（不上桌），带能力标签 `image_gen` / `tts` / `stt` / `video_gen`，按秒 / 分钟 / 字符计价用 `media_price`，图像模型按 token 计价则给 `price.output`（图像输出价）和 `image_tokens`（一张图的典型 token 数，只用于预估；实际费用以渠道返回为准）。选择只看标签和档位，同档内随机；`default: true` 的模型在同档内优先；语音合成还会按脚本语言选（主要是中文时优先带 `zh` 标签的模型）。

### 加一个流程步骤

1. 在 `src/roundtable/core/steps/` 新建模块，实现 `Step` 协议（`name` 和 `async run(ctx) -> StepResult`），用 `register_step()` 注册，并在 `steps/__init__.py` 中导入。
2. 把步骤名加进 `roundtable.yaml` 的 `pipeline` / `collab_pipeline`（或某个档位的 `pipeline`）；启动时 `check_pipelines()` 会检查所有步骤都已注册。
3. 步骤需要用户拍板时抛 `NeedsApproval(card, key)`；每个产出存库、恢复时只处理还没有产出的成员，这样中断后继续不会重复调用。
4. 在 `routing/estimate.py` 里补上该步骤的花费预估，并为它写测试（`tests/` 与 `src/` 结构对应）。

### 加 / 改一个提示词

**改提示词 = 新建版本文件**，已发布的版本不能修改：

1. 复制 `prompts/<角色>/v<n>.md` 为 `v<n+1>.md` 并修改（文件头声明 `variables`，占位符写 `{{ name }}`，题目和他人答案放在标签里并说明标签内的指令无效）。
2. 在 `roundtable.yaml` 的 `prompts:` 里改用新版本。
3. 运行 `python scripts/lock_prompts.py` 登记哈希；测试会拒绝未登记的新版本和被修改 / 删除的旧版本。

提示词里不得出现模型名、厂商名、渠道名；组员提示词只允许「代号」这一个与人相关的变量。

## 附件

支持图片、PDF、Word `.docx`、文本和 mp3 / wav 音频（`roundtable.yaml` 的 `uploads` 配置数量和大小）。类型同时按扩展名和文件头判断；PDF 直接提取文字（扫描件不做 OCR，会提示）；`.doc`、HEIC 等给出转换办法。图片由带 `vision` 标签的模型直接看原图，其他成员看到由最便宜的识图模型生成的文字版；音频优先用 `stt`（Whisper 系列）模型转写。没有对应的模型时拒绝上传并说明原因。上传的文件按内容哈希存在 `data/uploads/`，文件名只用于显示，不参与路径。

## 代码运行沙箱（可选）

成员的 `python` 工具（运行代码、画图、生成 Word / Excel / PPT / PDF）需要沙箱，**没有沙箱时该工具关闭，绝不在本机直接运行**。

```bash
python scripts/setup_sandbox.py          # 一次性安装 Deno + Pyodide + 常用包 + 中文字体，之后完全离线
python scripts/setup_sandbox.py --check  # 只检查
```

默认的 `wasm` 后端不需要 Docker：Deno 只能读运行时目录和本次的工作目录、只能写 `out/`，没有网络、环境变量和子进程权限。也可以改用 Docker 后端（`sandbox/Dockerfile`，`--network none`、只读根目录、非 root），见 `sandbox/README.md`。

## 联网搜索与隐私

成员可以搜索并读取搜索结果里的网页，答案中用 `[S1]` 这样的编号注明来源（编造来源或用了搜索却不标注会被打回重做）。默认走 OpenRouter 自带的联网搜索，填写 `TAVILY_API_KEY` 后 Tavily 作为备选。**题目和搜索词会发给搜索服务**；不想发送时在 `roundtable.yaml` 的 `tools.by_step` 里去掉 `search` / `fetch`，或不要填搜索相关的 key。

## 媒体输出

讨论模式提问时可以选「图片 / 语音 / 视频」：成员先商定并写出生成提示词 → 生成 → 带 `vision` 标签的成员评审（图片和视频截帧真正发给他们）→ 不满意就改提示词重新生成，最多 `media.max_rounds` 轮。协同模式由统筹在拆分时把子任务标为图片 / 语音 / 视频。**视频每次生成前都要你确认**；图片和语音只在超过单题门槛时才确认。视频是异步任务（提交 → 轮询 → 下载），暂停或重启后继续轮询同一个任务，不会重复提交。网页里图片直接显示，音频视频可以播放和下载。

**风格参考图**：上传的图片默认是「风格参考」（提问区每张图片可取消勾选）。题目要画图时，带 `vision` 标签的成员先各自看原图、统筹合并出一份**风格规范**和 6–10 条可逐条检查的**风格清单**，之后每次调用都带着它；参考图会和提示词一起传给带 `image_edit` 标签的画图模型（没有这样的模型时只按文字规范画，并在界面上明确提示）。生成的图片由非作者的成员对照清单逐条判定，不通过就退回作者重画（`media.style_retries`，默认 2 次），仍不通过则保留最后一版并标「风格未通过」。`python scripts/check_models.py` 会核对带 `vision` / `image_edit` 标签的模型在 OpenRouter 上是否真的支持图片输入。

**协同流水线**：协同模式不是各做各的——统筹把题目拆成带类型的流水线（分析 / 搜索调研 / 撰写 / 写提示词 / 执行生成 / 写代码 / 运行验证 / 审查 / 事实核查 / 整合），上游产出（文本和文件）交给下游，每位成员分到一块不同的内容，成员比步骤多时按内容拆成平行子任务。拆得不合格会让统筹重拆一次，仍不行按模板（带参考图的创作、创作、调研、数据 / 代码、文档）自动生成。类型、规则和模板都在 `roundtable.yaml` 的 `collab`。

## 测试

```bash
ruff check . && ruff format --check .
pytest                          # 不联网、不需要真实 key
playwright install chromium     # 首次运行前端端到端测试前（没有浏览器时这些测试自动跳过）
```

GitHub Actions（`.github/workflows/ci.yml`）在每次推送和 PR 时运行 ruff 和 pytest（含前端端到端测试）。

## 安全

- key 只在项目根目录 `.env`；读入后包成 `Secret`（`repr` / 日志只显示 `***`），只有适配器组装请求头时才取出原文。
- 对外展示只用 `session_view()`：匿名开启且未揭晓时去掉模型、渠道、阵容和缺席名单，模型输出经身份遮蔽。
- 成员生成的文件始终作为附件下载（`nosniff` + `CSP sandbox`），HTML / SVG 只显示源代码、不在页面内渲染。

## 目录

```
config/     四个配置文件          prompts/    带版本的提示词 + versions.lock
src/roundtable/core/    纯业务逻辑（config providers prompts allocation routing storage attachments
                        tools search media budget steps orchestrator …）
src/roundtable/api/     FastAPI + SSE         web/        原生 JS 前端（无构建步骤）
scripts/    check_models  lock_prompts  setup_sandbox      sandbox/    可选的 Docker 沙箱
tests/      与 src/ 对应；tests/web 是 Playwright 端到端测试
```
