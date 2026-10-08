// Roundtable 代码运行沙箱：在 Deno 中用 Pyodide（WebAssembly 版 CPython）运行 job/main.py。
// 用法：deno run [权限参数] runner.mjs <运行时目录> <工作目录>
// 代码只能看到内存中的文件系统：/work/in（材料）、/work/out（生成的文件）。
// 运行前把工作目录的 in/、out/ 复制进来，运行后把 /work/out 复制回工作目录的 out/（跳过符号链接）。
const [runtimeDir, jobDir] = Deno.args;
const { loadPyodide } = await import(new URL(`file://${runtimeDir}/pyodide/pyodide.mjs`).href);

// 输出边产生边写出（即使解释器异常退出，之前的输出也不会丢）；宿主进程负责截断
const LIMIT = 2_000_000;
const encoder = new TextEncoder();
const written = { out: 0, err: 0 };
function emit(stream, key, text) {
  if (written[key] >= LIMIT) return;
  const bytes = encoder.encode(text + "\n");
  written[key] += bytes.length;
  let offset = 0;
  while (offset < bytes.length) offset += stream.writeSync(bytes.subarray(offset));
}
const py = await loadPyodide({
  indexURL: `${runtimeDir}/pyodide/`,
  stdout: (s) => emit(Deno.stdout, "out", s),
  stderr: (s) => emit(Deno.stderr, "err", s),
  env: { HOME: "/work", MPLBACKEND: "Agg", PYTHONHASHSEED: "0" },
});
const FS = py.FS;

function copyIn(src, dst) {
  FS.mkdirTree(dst);
  let entries = [];
  try { entries = [...Deno.readDirSync(src)]; } catch { return; }
  for (const e of entries) {
    if (e.isSymlink) continue;
    const s = `${src}/${e.name}`;
    const d = `${dst}/${e.name}`;
    if (e.isDirectory) copyIn(s, d);
    else if (e.isFile) FS.writeFile(d, Deno.readFileSync(s));
  }
}

function copyOut(src, dst) {
  for (const name of FS.readdir(src)) {
    if (name === "." || name === "..") continue;
    const s = `${src}/${name}`;
    const d = `${dst}/${name}`;
    const st = FS.lstat(s);
    if (FS.isLink(st.mode)) continue;
    if (FS.isDir(st.mode)) {
      Deno.mkdirSync(d, { recursive: true });
      copyOut(s, d);
    } else if (FS.isFile(st.mode)) {
      Deno.writeFileSync(d, FS.readFile(s));
    }
  }
}

// 纯 Python 的附加包（openpyxl、python-docx 等，由 setup_sandbox.py 放在 wheels/）
try {
  for (const e of Deno.readDirSync(`${runtimeDir}/wheels`)) {
    if (e.isFile && e.name.endsWith(".whl")) {
      py.unpackArchive(Deno.readFileSync(`${runtimeDir}/wheels/${e.name}`), "wheel");
    }
  }
} catch (_) { /* 没有附加包 */ }

copyIn(`${jobDir}/in`, "/work/in");
copyIn(`${jobDir}/out`, "/work/out");
const code = Deno.readTextFileSync(`${jobDir}/main.py`);
// 附加包需要的 Pyodide 包（如 python-docx 需要 lxml）：按代码中的 import 加载
let extras = {};
try { extras = JSON.parse(Deno.readTextFileSync(`${runtimeDir}/extras.json`)); } catch (_) { /* 无 */ }
const imported = [...code.matchAll(/^\s*(?:from|import)\s+([A-Za-z_]\w*)/gm)].map((m) => m[1]);
const deps = [...new Set(imported.flatMap((n) => extras[n] || []))];
// 运行前的准备：工作目录、隐藏一些无用的警告；启动子进程的函数改为直接报错
// （Deno 没有授予子进程权限，即使调用也会失败，但会让解释器整体崩溃）
const SETUP = `
import os, sys, warnings, subprocess
os.chdir('/work')
sys.argv = ['main.py']
warnings.filterwarnings('ignore', message='The [xy] parameter as float')
def _blocked(*args, **kwargs):
    raise PermissionError('沙箱中不能启动子进程')
os.system = os.popen = _blocked
for _name in ('execv', 'execve', 'execvp', 'spawnv', 'fork', 'posix_spawn'):
    if hasattr(os, _name):
        setattr(os, _name, _blocked)
subprocess.Popen = subprocess.run = subprocess.call = subprocess.check_output = _blocked
del _blocked, _name
`;
let status = 0;
try {
  if (deps.length) await py.loadPackage(deps, { messageCallback: () => {} });
  await py.loadPackagesFromImports(code, {
    messageCallback: () => {},
    errorCallback: (m) => emit(Deno.stderr, "err", m),
  });
  py.runPython(SETUP);
  await py.runPythonAsync(code);
} catch (e) {
  emit(Deno.stderr, "err", String(e && e.message ? e.message : e));
  status = 1;
} finally {
  try {
    copyOut("/work/out", `${jobDir}/out`);
  } catch (e) {
    emit(Deno.stderr, "err", `复制生成的文件失败：${e}`);
  }
}
Deno.exit(status);
