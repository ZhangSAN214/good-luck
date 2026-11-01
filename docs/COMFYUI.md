# 用自己电脑上的 ComfyUI 画朋友圈配图

圆桌可以把成员的朋友圈配图交给你电脑上的 ComfyUI 画：**不花 API 钱**，只用显卡。默认关着，按下面几步打开。
适合的电脑：NVIDIA 显卡 8GB 显存（如 RTX 4060 8GB）、内存 16GB；ComfyUI 和圆桌跑在同一台电脑上。

> 这里只写步骤。仓库里**不放、也不会下载**任何模型文件，模型请你自己从发布页下载，并遵守各模型的许可。

## 它怎么工作

- 渠道 `comfyui`（`config/models.yaml`）：`kind: local`，地址 `http://127.0.0.1:8188`，不需要 key。
- 模型 `comfyui-local`：不上桌、节电档、每张 0 元，默认 `enabled: false`。它吃**标签式提示词**（`image_prompt: tags`），朋友圈配图走它时改用 `prompts/moment_photo_tags`（正向一行、负向一行），走云端绘图模型时照旧用自然语言的 `prompts/moment_photo`。
- 路由的 `model` 写的是**工作流模板**的文件名，模板在 `config/comfyui/`：
  - `moment_photo_sdxl.json`：带 IPAdapter，用成员设定图当参考，让画出来的人像她；
  - `moment_photo_sdxl_noref.json`：没有参考图时用（她不入镜的照片）。
- 价格 0 的绘图模型自然排在付费模型前面；ComfyUI 没开或画失败时，换下一个绘图模型（云端，要花钱）。不想退回付费模型就打开 `local_only`（见第 5 步），这时画不出来只发文字。

## 1. 装 ComfyUI（Windows 便携版）

1. 打开 ComfyUI 官方 GitHub 的发布页（github.com/Comfy-Org/ComfyUI → Releases），下载 Windows 便携版（NVIDIA）压缩包，解压到一个**路径里没有中文和空格**的文件夹，例如 `D:\ComfyUI`。
2. 双击 `run_nvidia_gpu.bat`。第一次会装依赖，等它打印出 `To see the GUI go to: http://127.0.0.1:8188`。
3. 浏览器打开 <http://127.0.0.1:8188>，能看到节点画布就装好了。

8GB 显存一般不需要额外参数；如果画图时报显存不足（out of memory），在 `run_nvidia_gpu.bat` 里给启动命令加上 `--lowvram` 再开。

## 2. 下载一个 SDXL 二次元底模

在 Civitai 或 Hugging Face 上选一个 **SDXL 架构的二次元底模**（`.safetensors`，约 6～7GB），例如 Illustrious-XL、NoobAI-XL、Animagine XL 4.0 这一类。它们吃 Danbooru 风格的标签（`1girl, long white hair, cat ears …`），和 `moment_photo_tags` 的写法对得上。

放到 `ComfyUI\models\checkpoints\`。

## 3. 装 IPAdapter（让画出来的人像成员）

成员是原创角色，底模不认识她们，靠 IPAdapter 照设定图画。

1. 装节点：用 ComfyUI-Manager（新版 ComfyUI 自带；没有就按它的说明装）→ Custom Nodes Manager → 搜 **ComfyUI_IPAdapter_plus** → Install → 重启 ComfyUI。
2. 下载两个模型文件（ComfyUI_IPAdapter_plus 的 README 里有链接），**文件名保持原样**：
   - `ip-adapter-plus_sdxl_vit-h.safetensors` → 放到 `ComfyUI\models\ipadapter\`（没有这个文件夹就新建）
   - `CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors` → 放到 `ComfyUI\models\clip_vision\`

示例模板用的是 `IPAdapterUnifiedLoader` 的 `PLUS (high strength)` 预设，按上面的文件名自动找模型。IPAdapter 对部分二次元底模效果一般，人像不像时可以把模板里 IPAdapter 节点的 `weight` 从 0.7 调到 0.8～0.9 再试。

## 4. 把底模文件名填进工作流模板

**不要改仓库里的模板**（`git pull` 会被卡住）。复制一份，改名成 `.local.json`（已在 `.gitignore` 里，程序会优先用它）：

```bash
copy config\comfyui\moment_photo_sdxl.json config\comfyui\moment_photo_sdxl.local.json
```

```bash
copy config\comfyui\moment_photo_sdxl_noref.json config\comfyui\moment_photo_sdxl_noref.local.json
```

用记事本打开这两个 `.local.json`，把 `"ckpt_name": "your_sdxl_anime_model.safetensors"` 换成你在第 2 步下载的文件名。步数（`steps`）、`cfg`、采样器也可以在这里改。

想用自己在 ComfyUI 里搭的工作流也行：菜单里打开「开发者模式」后「导出（API 格式）」，在要填内容的地方写占位符，存成上面的 `.local.json`：

| 占位符 | 填什么 |
|---|---|
| `"{{prompt}}"` / `"{{negative}}"` | 正向 / 负向标签（可以嵌在一段文字里） |
| `"{{seed}}"` `"{{width}}"` `"{{height}}"` | 随机种子、宽、高（填成数字） |
| `"{{ref_1}}"` `"{{ref_2}}"` `"{{ref_3}}"` | 上传后的参考图文件名（放在 LoadImage 节点里；参考图不够时重复用第一张） |

模板里至少要有一个 SaveImage 节点，程序取它的第一张图。

## 5. 在圆桌里打开

在 `config/models.local.yaml`（没有就新建，已在 `.gitignore` 里）写：

```yaml
model_switches:
  comfyui-local: true
```

只想用本地画、画不出来就只发文字（绝不花钱），再在 `config/roundtable.local.yaml` 写：

```yaml
media:
  moment_photos:
    local_only: true
```

重启圆桌服务。运行下面的命令，能看到 `comfyui-local` 在可用模型里就接上了：

```bash
roundtable models
```

之后成员发朋友圈配图时，会先请 ComfyUI 画（每张 20～60 秒，第一次加载底模更久）。**ComfyUI 要一直开着**；关掉时自动换云端模型，或者在 `local_only` 下只发文字。

## 可以调的参数

都在 `config/models.yaml` 的 `comfyui-local.params`（本机改的话，在 `models.local.yaml` 里整张 `models` 列表都得抄过去，所以一般改 `.local.json` 模板更方便）：

| 参数 | 默认 | 说明 |
|---|---|---|
| `width` / `height` | 832 / 1216 | 竖版，像手机照片；8GB 显存不要超过约 100 万像素 |
| `timeout_s` | 300 | 提交后最多等多久，超时会把排队的任务删掉 |
| `poll_interval_s` | 1.0 | 多久问一次画完没有 |
| `workflow_without_refs` | `moment_photo_sdxl_noref.json` | 没有参考图时用的模板 |

## 常见问题

- **一直换成云端模型**：ComfyUI 没开，或地址不是 `127.0.0.1:8188`。先在浏览器里打开这个地址看看。
- **报「工作流模板里没有 SaveImage 节点」/「找不到工作流模板」**：检查 `.local.json` 的文件名和内容。
- **ComfyUI 那边报节点错误**：多半是底模 / IPAdapter 文件名不对，或没装 ComfyUI_IPAdapter_plus。ComfyUI 的报错会脱敏后写进圆桌的日志。
- **画出来的人不像**：调高 IPAdapter `weight`；长期可以给每位成员训一个 LoRA（在你自己电脑上训，要你决定）。
- **画风变成写实**：换一个更偏二次元的底模；负向标签里已经写了 `photorealistic, realistic, 3d`。
