"""Пересборка web/office.html — одного файла, который отдаёт сервер.

    npm install            # three, esbuild, @gltf-transform/cli
    python build/build_solo.py [--force]

Что делает:
  1. вытаскивает модуль из build/src/viewer.html (это единственный исходник UI);
  2. вендорит three.js + GLTFLoader/OrbitControls/meshopt из node_modules;
  3. бандлит esbuild'ом в IIFE (иначе file:// и старый Electron ругаются на модули);
  4. жмёт GLB: resize 512 → quantize → meshopt (48 МБ → ~6.7 МБ) и вшивает в base64;
  5. пишет web/office.html (~9.7 МБ) — двойной клик по нему тоже работает.

GLB берётся из assets/office-small.glb, если он есть (иначе из assets/*.glb и
пережимается). Внешних CDN нет: страница работает без интернета.
"""
import base64
import os
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
VIEWER = ROOT / "build" / "src" / "viewer.html"
OUT_HTML = ROOT / "web" / "office.html"
VENDOR = ROOT / "build" / "vendor"
UTILS = ROOT / "build" / "utils"
DEPS = pathlib.Path(os.environ.get("OFFICE_DEPS") or (ROOT / "node_modules"))
SMALL = ROOT / "assets" / "office-small.glb"
FORCE = "--force" in sys.argv


def die(msg):
    raise SystemExit("[build] " + msg)


if not VIEWER.exists():
    die(f"нет {VIEWER}")
for p in (DEPS / "three", DEPS / "esbuild"):
    if not p.exists():
        die("нет node_modules — выполните `npm install` в корне репозитория")

VENDOR.mkdir(parents=True, exist_ok=True)
UTILS.mkdir(parents=True, exist_ok=True)

JSM = DEPS / "three" / "examples" / "jsm"
for src, dst in (
    (JSM / "loaders" / "GLTFLoader.js", VENDOR / "GLTFLoader.js"),
    (JSM / "controls" / "OrbitControls.js", VENDOR / "OrbitControls.js"),
    (JSM / "libs" / "meshopt_decoder.module.js", VENDOR / "meshopt_decoder.module.js"),
    (JSM / "utils" / "BufferGeometryUtils.js", UTILS / "BufferGeometryUtils.js"),
    (DEPS / "three" / "build" / "three.module.js", VENDOR / "three.module.js"),
):
    if not src.exists():
        die(f"нет {src}")
    shutil.copy(src, dst)

# вендоренные аддоны импортируют пакет 'three' — переводим на локальный файл
for f in (VENDOR / "GLTFLoader.js", VENDOR / "OrbitControls.js"):
    t = f.read_text(encoding="utf-8")
    t = t.replace("from 'three';", "from './three.module.js';").replace('from "three";', 'from "./three.module.js";')
    f.write_text(t, encoding="utf-8")

_bu = UTILS / "BufferGeometryUtils.js"
_t = _bu.read_text(encoding="utf-8")
_t = (_t.replace("from 'three';", "from '../vendor/three.module.js';")
        .replace('from "three";', 'from "../vendor/three.module.js";'))
_bu.write_text(_t, encoding="utf-8")

html = VIEWER.read_text(encoding="utf-8")
m = re.search(r'<script type="module">(.*?)</script>', html, re.S)
if not m:
    die("в viewer.html нет <script type=\"module\">")
js = m.group(1)

js = js.replace("from 'three/addons/loaders/GLTFLoader.js'", "from '../vendor/GLTFLoader.js'")
js = js.replace("from 'three/addons/controls/OrbitControls.js'", "from '../vendor/OrbitControls.js'")
js = js.replace("from 'three';", "from '../vendor/three.module.js';")
if "meshopt_decoder" not in js:
    js = "import { MeshoptDecoder } from '../vendor/meshopt_decoder.module.js';\n" + js
js = js.replace("const loader = new GLTFLoader();",
                "const loader = new GLTFLoader();\nloader.setMeshoptDecoder(MeshoptDecoder);")

main_js = ROOT / "build" / "src" / "main.js"
main_js.write_text(js, encoding="utf-8")

esbuild = (DEPS / "@esbuild" / "win32-x64" / "esbuild.exe")
if not esbuild.exists():
    esbuild = DEPS / ".bin" / ("esbuild.cmd" if sys.platform == "win32" else "esbuild")
if not esbuild.exists():
    die("esbuild не найден (npm install esbuild)")

bundle_path = ROOT / "build" / "app.bundle.js"
subprocess.run([str(esbuild), str(main_js), "--bundle", "--minify", "--format=iife",
                "--target=es2020", f"--outfile={bundle_path}"], check=True, cwd=str(ROOT))
bundle = bundle_path.read_text(encoding="utf-8")
print(f"[build] bundle {len(bundle)/1024:.0f} KB")

# --- компактный GLB
if not SMALL.exists() or FORCE:
    src_glb = next((p for p in sorted((ROOT / "assets").glob("*.glb")) if p != SMALL), None)
    if not src_glb:
        die("нет assets/*.glb — положите исходную модель или готовый office-small.glb")
    gt = DEPS / "@gltf-transform" / "cli" / "bin" / "cli.js"
    if not gt.exists():
        die("нет @gltf-transform/cli (npm install)")
    step1 = ROOT / "build" / "office-512.glb"
    subprocess.run(["node", str(gt), "resize", str(src_glb), str(step1),
                    "--width", "512", "--height", "512"], check=True, cwd=str(ROOT))
    subprocess.run(["node", str(gt), "quantize", str(step1), str(SMALL)], check=True, cwd=str(ROOT))
    if src_glb.stat().st_size > 10 * 1048576:      # сырую модель дожимаем meshopt'ом
        subprocess.run(["node", str(gt), "meshopt", str(SMALL), str(SMALL) + ".tmp",
                        "--level", "high"], check=True, cwd=str(ROOT))
        pathlib.Path(str(SMALL) + ".tmp").replace(SMALL)

print(f"[build] glb {SMALL.stat().st_size/1048576:.1f} MB")
b64 = base64.b64encode(SMALL.read_bytes()).decode("ascii")

out = html
out = re.sub(r'<script type="importmap">.*?</script>\s*', "", out, flags=re.S)
out = out.replace(m.group(0),
                  '<script>window.OFFICE_GLB_B64="' + b64 + '";</script>\n'
                  '<script>' + bundle + '</script>')
out = out.replace('Загрузка <b id="pct">0%</b>', "Загрузка офиса…")
OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
OUT_HTML.write_text(out, encoding="utf-8")
print(f"[build] WROTE {OUT_HTML}  {OUT_HTML.stat().st_size/1048576:.1f} MB")
