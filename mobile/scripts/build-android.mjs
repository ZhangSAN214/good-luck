// 生成并打包安卓 APK（CI 用，见 mobile/README.md）：
// 1. 没有 android/ 时用 Capacitor 模板生成（android/ 不进 git）
// 2. 用圆桌图标生成启动图标；3. 允许明文 HTTP（家里局域网 / Tailscale 上的电脑是 http://），不做云备份
// 4. cap sync；5. gradlew（--no-build 时跳过） assembleDebug → android/app/build/outputs/apk/debug/app-debug.apk
import { execSync } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';

const run = (cmd, cwd = '.') => execSync(cmd, { stdio: 'inherit', cwd });

if (!existsSync('android')) run('npx cap add android');
run('npx capacitor-assets generate --android --iconBackgroundColor "#0f1724" --splashBackgroundColor "#0f1724"');

const manifest = 'android/app/src/main/AndroidManifest.xml';
let xml = readFileSync(manifest, 'utf8');
if (!xml.includes('usesCleartextTraffic')) {
  xml = xml.replace('<application', '<application\n        android:usesCleartextTraffic="true"');
}
// 登录 cookie 不进云备份
xml = xml.replace('android:allowBackup="true"', 'android:allowBackup="false"');
writeFileSync(manifest, xml);

run('npx cap sync android');
if (process.argv.includes('--no-build')) process.exit(0);
run(process.platform === 'win32' ? 'gradlew.bat assembleDebug' : './gradlew assembleDebug --no-daemon', 'android');
