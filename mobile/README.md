# mobile/ — 圆桌安卓外壳

使用说明见 [docs/MOBILE.md](../docs/MOBILE.md)。

- `www/`：外壳里唯一的页面（连接页）。`connect.js` 记住电脑地址、依次试「家里」「离家」地址、连上就跳到电脑上的圆桌。
- `capacitor.config.json`：只允许在 WebView 里打开私有地址、`.local`、`.ts.net`；允许明文 HTTP（家里局域网）。
- `scripts/build-android.mjs`：从 Capacitor 模板生成 `android/`（不进 git）、生成图标、改 AndroidManifest（明文 HTTP、关云备份）、打包 debug APK。
- 本地打包需要 Node 20+、JDK 21、Android SDK：`npm ci && npm run android`。平时由 `.github/workflows/android.yml` 打包。
