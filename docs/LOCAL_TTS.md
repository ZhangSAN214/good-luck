# 用自己电脑上的本地配音服务给成员配音

成员的语音消息、「听 TA 说」、成员设定页的试听，可以交给你电脑上的本地配音服务（例如 IndexTTS2 的本地网页版）来念：**不花 API 钱**，只用显卡。默认关着，按下面几步打开。
适合的电脑：NVIDIA 显卡 8GB 显存（如 RTX 4060 8GB）、内存 16GB；配音服务和圆桌跑在同一台电脑上。

> 这里只写步骤。仓库里**不放、也不会下载**任何模型文件和整合包。IndexTTS2 的模型用的是 bilibili 的模型协议（不是宽松许可证），请你自己从发布页下载并遵守协议；圆桌只通过 HTTP 调它，不复制它的代码。

## 它怎么工作

- 渠道 `local_tts`（`config/models.yaml`）：`kind: local`，地址 `http://127.0.0.1:7860`，不需要 key。
- 模型 `pc-tts`：不上桌、节电档、每个字 0 元，默认 `enabled: false`。
- 每位成员在 `config/personas.yaml` 的 `voices` 里有一个本地预设名（`rt-openai`、`rt-anthropic` …），对应配音服务里存好的「参考音频预设」。
- 成员念一句话时，**不花钱的先试**；本地服务没开或出错，自动换下一个语音模型（云端，按原来的音色，要花钱）。同一句话念过一次会缓存，下次不再花钱、也不再去试本地服务。
- 全力档（高质量）不用本地配音，照旧用全力档的语音模型。

## 1. 装本地配音服务

以 IndexTTS2 的本地网页版为例（例如 GitHub 上的 indextts2-studio，或 B 站工房的整合包）：按它自己的说明装好，启动后浏览器能打开 <http://127.0.0.1:7860>，能在网页上念出一句话就算装好了。8GB 显存一般够用（常驻约 5～6GB）；同时开着 ComfyUI 画图时显存会紧张，两边最好不要同时干活。

端口不是 7860 时，在 `config/models.local.yaml` 里改渠道地址：

```yaml
channels:
  local_tts:
    base_url: http://127.0.0.1:你的端口
```

## 2. 给八位成员各做一个参考音频预设

**参考音频只用合成音色，不克隆真人。** 推荐做法：在圆桌「成员设定」里点每位成员的「试听」，用她现在的云端音色念一段 10～20 秒的话（可以先在 `config/personas.yaml` 把试听句子换长一点），把音频存下来，作为本地服务的参考音频，在配音服务的网页上存成预设，预设名照 `voices` 里写的：

| 成员 | 预设名 |
|---|---|
| OpenAI | rt-openai |
| Anthropic | rt-anthropic |
| xAI | rt-xai |
| DeepSeek | rt-deepseek |
| Google | rt-google |
| Alibaba | rt-alibaba |
| Moonshot AI | rt-moonshot |
| Zhipu AI | rt-zhipu |

想用别的名字，就改 `personas.yaml` 里的 `pc-tts:` 那一行。

## 3. 告诉圆桌这个服务的接口长什么样

不同的本地服务接口不一样，圆桌不写死任何一家，按 `config/models.local.yaml` 里的参数来调：

```yaml
model_switches:
  pc-tts: true          # 打开本地配音

model_params:              # 只改这几项，其余参数照仓库里的
  pc-tts:
    path: api/v1/tts       # 合成接口的路径（相对上面的地址）
    request: form          # json（默认）或 form（表单）
    fields:                # 请求字段；{{text}} = 要念的话，{{voice}} = 预设名
      text: "{{text}}"
      preset: "{{voice}}"
    url_field: result.file     # 回 JSON / SSE 时，音频文件地址在哪个字段
    # audio_field: data.audio  # 或者：音频本身（base64）在哪个字段
    timeout_s: 300
```

（上面的 path / fields / url_field 只是示例，要以你装的服务实际接口为准。装好后我会在你电脑上看一眼它的接口，把这几行填对。）

服务直接回音频文件时，`url_field` 和 `audio_field` 都不用写。

## 4. 试一下

重启圆桌，到「成员设定」点任意一位的「试听」。本地服务开着时，用量面板里这次是 0 元；把本地服务关掉再试一句新的话，会自动换回云端音色。

## 常见问题

- **试听没声音 / 还是走云端**：本地服务没开、端口不对，或接口参数没填对。看圆桌终端里的报错（会写「连不上本地配音服务」或「返回里找不到音频」）。
- **显存不足**：别和 ComfyUI 同时用；或在配音服务里打开半精度（fp16）。
- **念得慢**：第一次加载模型较慢；长句可以把 `timeout_s` 调大。
