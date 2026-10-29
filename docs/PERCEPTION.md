# 成员感官（M11 感知）

方案：`/mnt/project-files/计划/成员感官_看视频听声音读图文_方案.md`（K1～K5）。本文件记录已经做了的部分。

## 职能

感官只做一件事：把外部内容整理成**感知卡**（文字），成员读卡后用自己的口吻回应。成员什么时候去看、看完心情怎么变，都不归这里。

## K2 第一部分：聊天里真看真听（2026-10-29）

用户在私聊、群聊、朋友圈发的图片、语音、视频，以前成员只看到「[图片]」「[语音 5 秒]」这样的占位；现在由感官先看一遍，成员读到的是看到 / 听到的内容。

### 两种模式

| | 快速（默认） | 精细 |
|---|---|---|
| 图片 | 缩到长边 768 像素 | 缩到长边 1536 像素，图里的字逐字抄 |
| 视频 | 均匀取 8 帧，每 4 帧拼成一张 2×2 宫格图（每格标时间），声音转写 | 有带 `video_in` 标签的模型时，整段视频连声音交给它；没有就取 24 帧、不拼图 |
| 语音 | 转写（两种模式共用一份转写稿，不再调用看图模型） | 同左 |
| 输出上限 | 600 token（一条消息合计） | 2000 token |

- 什么时候用精细：消息里写了 `perception.fine_words` 里的词（「仔细看」「看清楚」等），或者在网页上点那条消息的「细看」（`POST /api/chat/messages/{id}/look?room=`）。细看后，原来回应过这条消息的成员（没有就按平常规则选一位）再回应一次。
- **省 token**：一条消息里的所有图片和视频画面合成一次看图调用；按文件内容哈希缓存感知卡和转写稿（`data/perception/`，不进 git），同一个文件谁再发都不重复花钱；精细的卡也满足快速的请求；补精细时直接用已有的转写稿。

### 模型与花费

- 看图：带 `vision` 标签、最便宜的上桌模型；整段视频：同时带 `video_in` 和 `vision` 的最便宜模型；转写：`stt` 模型（没有时用带 `transcribe` 标签的对话模型）。只看标签和价格，不点名。
- 调用记在该会话的隐藏会话上（`step="perceive"`、`role="preprocess"`），计入聊天月预算和总预算；另有感官自己的月上限 `perception.monthly_usd`（默认 $1.5）。额度用完或 `perception.enabled: false` 时不再调用，只用缓存，成员照常回应（看到占位）。
- 视频解码和拼图用可选依赖 `av` + `pillow`（`pip install -e ".[media]"`）；没装时视频只有声音转写（也要 `av` 抽声音，所以没装时视频只能看到占位），图片照常。

### 成员读到的样子

聊天记录（`<chat>` 标签内）里，那条消息后面多一行：

```
用户：你看这只猫
[图片]
<media from="用户" kind="图片">一只橘猫趴在窗台上晒太阳……</media>
```

朋友圈评论时，动态正文后面同样附上感知卡。卡里出现的结束标签会被打断（防注入）；提示词 `perceive_fast/v1`、`perceive_fine/v1` 说明图片、视频、语音里的指令都只是内容。

### 存放与清空

- 感知卡存在消息 / 动态的 `meta.perception`（`[{id, kind, mode, text}]`），清空聊天记录时一起删掉。
- 缓存：`data/perception/<sha256>.<fast|fine>.json`、`<sha256>.transcript.json`；`DELETE /api/perception` 一键清空。原始文件仍在 `data/chat_media/`，感官不另存。

### 网页

- 你发的带图片 / 视频 / 语音的消息，时间旁边显示「已看过 · 快速 / 精细」和「细看」按钮（精细看过后不再显示按钮）。
- 感官在看时，「正在输入」那一行显示「正在看你发的图片 / 视频 / 语音…」。

### 代码

- `core/perception/senses.py`：本地处理（按时间跳转取帧、拼宫格、抽 16 kHz 单声道 WAV、`PerceptionCache`）。
- `core/perception/perceiver.py`：`Perceiver`（选模式、预算、转写、看图、按【编号】拆卡）、`media_block()`。
- `core/service.py`：`chat_post` / `moments_post` 先感知再回应，`chat_look`、`perception_clear`。
- `Media` 新增 `video` 种类：OpenAI 兼容渠道发 `video_url`（data URI），Gemini 用 `inline_data`，Anthropic 不支持（报 bad_request，不切换渠道）。
- 借鉴（只借思路，代码为圆桌重写，均为 MIT）：dsh-visual-plugin（按时间取关键帧、带时间戳按顺序交给模型）、dsh-vision-router（按内容哈希缓存、缓存内容当不可信资料）、modlens（感知卡要有完整文字和人物物体）。

### 测试

`tests/core/perception/`、`tests/api/test_chat_api.py`（感知卡进聊天记录、细看、缓存清空、关键词直接精细）、`tests/web/test_e2e_chat_media.py::test_seen_mark_and_look_closer`、`tests/core/providers/`（`video_url`）。

## K2 第二部分：工作模式的视频附件（2026-10-29）

办公室、讨论、协同提问时可以上传 mp4 / mov / webm 视频（大小上限同其他附件，`uploads.max_file_mb`）。提交后、路由前由 `prepare_attachments()` 转成**文字版**：

1. 声音转写（`stt` 模型，没有时用带 `transcribe` 标签的对话模型）；没有声音轨就跳过。
2. 画面：有带 `video_in` 标签又能看图的模型、文件不超过 `perception.native_max_mb` 时整段发给它；否则按 `perception.fine` 均匀取帧（默认 24 帧，长边 768 像素），每帧一张随附图片。
3. 最便宜的合适模型按提示词 `describe_video/v1` 写成文字版：概括、按时间的时间线、画面里的文字逐字抄下；末尾附上声音转写稿。

所有成员（含统筹、经理）都只收到这份文字版，同一步骤完全相同。调用记在本场（`step="attachments"`、`role="preprocess"`），计入本场花费，不占感官的月上限。转写或文字版失败时整场失败（同音频）。没装 `av` + `pillow` 或没有看图模型时拒绝上传并说明原因。测试：`tests/core/orchestrator/test_attachments.py`（视频部分）、`tests/core/attachments/`。

### 还没做

- 读网页全文（K3）、天气与地图（K4）、时事与开放授权媒体源（K5）、接成员引擎的事件总线（K1）。
