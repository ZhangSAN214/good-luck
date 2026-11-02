# 克劳德 Live2D 成品样机（2026-11-02）

素材：`live2d_src/hd_v3/`（提交 daaa18a）。两版都由开发分支的 `scripts/live2d_import.py` 生成，可以重跑。
**只给老板个人看**：两个工具的 .moc3 是逆向工程写出来的，商用前要先确认 Live2D 的条款。

| 文件 | 内容 |
|---|---|
| `compare.jpg` | 并排对比图：两版在静止、看左、看右、说话、身体摆动、笑六个时刻的全身和头部特写 |
| `compare_side_by_side.mp4` | 两版演示并排（左 PSD2Live，右 image2live2d），18 秒 |
| `psd2live/` | PSD2Live 3.7.0，「完整」预设（五官位移、前发 / 后发模拟、衣服模拟） |
| `psd2live/psd2live.model3.json` | Cubism 5.0 模型入口（.moc3、`psd2live.4096/` 三张 4096 贴图、物理、动作、7 个表情） |
| `psd2live/psd2live.cmo3` | Cubism Editor 工程（可在官方编辑器里精修） |
| `psd2live/model.psd2live` | PSD2Live 自己的工程（含全部编辑历史，用 PSD2Live 打开继续改） |
| `psd2live/demo_psd2live.mp4` | 演示，PSD2Live 自带的视频导出（带物理） |
| `image2live2d/model/model.model3.json` | image2live2d 0.2.0，Cubism 3.0 模型入口（.moc3、一张 4096 图集、41 组物理、22 个动作） |
| `image2live2d/model.cmo3` | Cubism Editor 工程（工具自己标为实验性） |
| `image2live2d/demo_image2live2d.mp4` | 演示，image2live2d 自带的网页预览逐帧录制（它的近似软件渲染器） |

两段演示用的是同一条参数时间线（视线先动、头再跟；看左 → 看右 → 点头 → 说话 → 身体摆动 → 笑）。

## 在家里电脑上看

1. 把这个 `live2d_out` 文件夹整个拷到开发分支的 `web\live2d\models\` 下面（该目录不进 git）。
2. 双击 `web\live2d\serve.bat`（默认看 PSD2Live 版；命令行 `serve.bat image2live2d` 看另一版）。
3. 浏览器打开后：鼠标移动她会看过去；下方按钮切换情绪；勾「说话」随机对口型；选一个音频文件按音量对口型。
   需要联网（Cubism Core 从 Live2D 官方地址加载）。

## 已知情况

- 效果层（阴影 / 高光 / 边缘光 / 腮红）都已按原混合模式合并进所属部件：Cubism 5.0 以前没有滤色，网页播放库
  也不支持新混合模式，合并最稳；代价是阴影跟着部件走，转头时投在脖子上的阴影不会移动。
- 张嘴：PSD2Live 用「啊」口型当最大张口、闭嘴线另一层，「哦」等口型靠它的口型参数变形；image2live2d 自己合成
  口腔，没用表情层。泪痣在 image2live2d 版并进了脸（单独一层会被它当成第二张脸）。
- image2live2d 的网页预览不处理透明度，录演示时把只在眨眼时出现的闭眼图层藏了，眨眼是眼皮压扁的效果；
  它的预览三角形之间有细缝，是预览器的问题。
