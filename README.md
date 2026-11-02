# assets/claude-live2d

克劳德 Live2D 拆层素材（只放素材，永不合并）。

**主素材：live2d_src/hd/**（魔搭 1536 拆层 + 4 倍 AI 放大回填，4096x6144，23 层，左右拆分）。

| 目录 | 内容 |
|---|---|
| live2d_src/full.png | 全身样图，插值放大 2 倍（2048x3072） |
| live2d_src/full_hi.png | 全身样图，AI 放大 4 倍（RealESRGAN_x4plus_anime_6B，4096x6144） |
| live2d_src/design.jpg | 设定图 |
| live2d_src/hd/ | **主素材**：layers_hd/*.png、layers.json、layers_hd.psd.zip（解压得 PSD）、overview_hd.jpg、face_compare.jpg。看得见的像素取自 full_hi，被挡住的部分用拆层补的内容 |
| live2d_src/web_run/ | 魔搭 See-through 原始拆层（1536x1536 方形画布，原图左右补白；眼睛层序已修为 眼白→虹膜→睫毛） |
| live2d_src/（根目录 layers*） | 本机 See-through（NF4，768，17 层，未左右拆分），仅作对比 |

坐标：web_run 的 1536 方形画布 = full.png 左右各补 512 像素到 3072x3072 后缩到 1536；hd 画布与 full_hi.png 一致。
