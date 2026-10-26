---
name: hermes-office-live
description: Live-feed the office viewer from real Hermes sessions.
---

# Hermes office — live feed and desktop pane

The 3D office (see `hermes-office-3d`) doubles as a live monitor: it renders the
**focused chat's real subagents** as desks, puts the chat title on the board, and
shows each worker's actual tool calls when you click it. Style comes from the
vaporwave pass (`vaporwave_skill.md`), so composition and lighting stay from Blender.

## Three parts

1. **Viewer** — `C:\Users\Admin\Downloads\hermes-office-vapor.html` (source, ES modules).
2. **Solo build** — `C:\Users\Admin\Downloads\office-solo\hermes-office-solo.html`:
   one 9.7 MB file, three.js bundled by esbuild, GLB meshopt-compressed + base64.
   Served as `office.html` by the feed.
3. **Feed** — `%LOCALAPPDATA%\hermes\office-live\office_live.py` on `127.0.0.1:8797`:
   `/` = the page, `/state?sid=` = JSON, `/health` = ok + request counters.
4. **Desktop plugin** — `%LOCALAPPDATA%\hermes\desktop-plugins\office-hermes\plugin.js`:
   right-zone pane `office_hermes` + statusbar chip.

## Where the real data lives

`$HERMES_HOME/state.db` (SQLite, read-only URI). Everything needed:

- chat: `sessions.title | display_name | model | message_count | tool_call_count`
- subagents: `sessions WHERE parent_session_id = <parent>` — one row per delegate
  child, `title` starts with `Subagent: `, order by `started_at`
- per-worker log: `messages WHERE session_id = <child> ORDER BY id DESC LIMIT 4`
  (covered by `idx_messages_session_id`) → `role`, `tool_name`, `content`, `tool_calls`
- delegation fallback (children not yet created): `async_delegations WHERE
  origin_session = <parent>` → `task_json.tasks[].goal`, `state`

**`sessions.last_activity_at` is NOT live for child sessions** — it stays 0 until the
session ends, so a status derived from it marks everything `done`. Compute the child's
last activity as `max(messages.timestamp)`; keep the parent's `busy` on
`sessions.last_activity_at` (that one does update).

## Status semantics that look right

`ended_at AND last_msg > 90 s ago` → `done`; `last_msg < 120 s` → `running`;
`< 900 s` → `idle`; else `done`. `eff` is decoration derived from real counts
(`62 + tools % 32` while running, 44 idle, 28 done) — never a fabricated progress %.

## Viewer live protocol

- `?live=1` → polls `/state` every 2 s (relative URL, so it must be served over HTTP).
- `?sid=<durable session id>` seeds the chat; the plugin then pushes
  `postMessage({type:'sid'|'busy', value})` so switching chats needs no reload.
- Payload → `applyState(j)`: updates the board (`#btitle`, `#bsub`), rebuilds the
  scene **only when the worker id/label signature changes** (`state.sig`), otherwise
  patches statuses in place; `setWorkers` re-spawns geometry and must not run per tick.
- `window.HermesOffice = { setWorkers, on, select, selectMain, state, count }`.
- Click handling: raycast on worker groups and on the bust; `Escape` deselects.

## Desktop plugin facts

- Atoms that exist (verified in `apps/desktop/src/sdk/host-state.test.ts`):
  `host.state.focusedStoredSessionId` (durable id = `sessions.id`), `focusedSessionId`,
  `busy`, `busyBySession`. Read them with `useValue(...)`.
- Pane: `ctx.register({ id:'pane', area:'panes', title:'office_hermes',
  data:{ placement:'main', dock:{ pane:'workspace', pos:'right', enforce:true },
  width:'560px' }, render })`; the content is just an `<iframe>` on the feed URL, plus a
  statusbar chip.
- **`placement:'right'` puts the pane in the collapsible right sidebar — the user sees
  nothing and reports "the button does nothing".** Use `placement:'main'` + `dock:
  { pane:'workspace', pos:'right', enforce:true }`.
- A docked pane arrives **collapsed as a vertical edge tab**. To have it open itself:
  in `register()` call a `reveal()` that does `host.undismissPane(id)` **then**
  `host.revealPane(id)` (undismiss first — a remembered ✕ keeps the pane out of the
  layout tree, where `revealPane` is a no-op); repeat on a timer and re-check
  `host.paneVisibility(id).get()` in the rendered pane, max ~5 tries.
- **Plugin JS is not hot-reloaded**: after editing `plugin.js` the user must run
  `Ctrl+K` → *Reload desktop plugins*. Page (`office.html`) edits do not need it, but
  version the iframe URL (`'?v=' + Date.now()` captured once per mount) or the old page
  stays on screen.
- **Verification without the app's CDP port:** give the feed a request counter
  (`/health` → `hits`) and watch it grow after the plugin file lands. A mounted chip
  polls `/state`, so a rising counter proves the plugin loaded and mounted — a
  packaged build offers no 9222 port and `focus_pane` cannot reveal plugin panes.
- Autostart: `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\hermes-office.vbs`
  running `pythonw.exe office_live.py` (`schtasks /Create ONLOGON` needs elevation).

## Rebuild chain

```bash
cd C:/Users/Admin/Downloads/office-solo && python build_solo.py
cp hermes-office-solo.html "$LOCALAPPDATA/hermes/office-live/office.html"
# feed restarts via: cscript //nologo "<Startup>/hermes-office.vbs"
```

`build_solo.py` vendors `three.module.js`, `GLTFLoader.js`, `OrbitControls.js`,
`meshopt_decoder.module.js` and `utils/BufferGeometryUtils.js` (patching `from 'three'`
inside each), bundles with the **direct** `node_modules/@esbuild/win32-x64/esbuild.exe`
(the `.cmd` wrapper swallows stderr and reports nothing), and reuses
`office-small.glb` unless forced. GLB: `gltf-transform resize 512` → `quantize` →
`meshopt --level high` (38 MB → 6.7 MB; Draco needs a wasm decoder and cannot load
from `file://`).

## Viewer features (palette / active-all / cubes / desk rotation / system load)

- **Palette is driven by three colours** (`PAL = {bg, ink, acc}`, `localStorage`
  `office_palette_v3`): `applyPalette()` sets the CSS vars (`--bg --ink --ink-dim
  --violet --magenta --cyan --line --panel`), pushes `uBg/uInk/uAcc` into the composite
  shader, and recolours screen/face lights and the cube. The dither ramp is derived in
  GLSL (`t0 = uBg*0.55`, `t1/t2 = mix(uBg,uAcc,…)`, `t3 = mix(uAcc,uInk,…)`, `t4 = uInk`).
  Pass colours as raw `byte/255` vectors — `THREE.Color('#…')` applies sRGB→linear and
  shifts the tone.
- **ACTIVE / ALL**: the feed returns `workers` (not done) **and** `history` (every child
  ever); the toggle switches the pool. Cap the stage at `MAX_DESKS = 12` — each worker
  carries 2 PointLights, and 30+ clones (a 193-child session is real) destroy the
  forward renderer. Show `на сцене N / M` in the counter.
- **Cube over the head** (`addCube`): edges `LineSegments` + additive `core`; spins
  `2.4 rad/s` and pulses opacity while `status === 'running'`.
- **Rotate desks**: `↻ столы` animates `state.ringAngle -= π/2`, re-placing each worker
  around the pool centre from `base = {x, z, ry}` recorded at spawn (`applyRing()` runs
  every frame, so newly spawned workers inherit the angle).
- **System load**: the feed embeds `sys = {cpu, ram:{used_mb,total_mb,pct}, procs:[{n,mb}]}`
  from ctypes `GetSystemTimes` / `GlobalMemoryStatusEx` / `EnumProcesses` +
  `GetProcessMemoryInfo` (no psutil on this python), cached 2.5 s. The first `cpu` sample
  is 0 — `GetSystemTimes` needs a previous delta.

## Pitfalls

- **Grep the built bundle for source names and you will think code vanished**: esbuild
  `--format=iife --minify` renames every top-level and local binding, so
  `cube.group.rotation.y` becomes `f.group.rotation.y`. Property accesses survive, plain
  identifiers do not — check `office-solo/src/main.js` (pre-bundle) before concluding a
  patch was dropped.
- **Headless Chrome throttles rAF**: probes that re-read `rotation.y` one second apart
  look frozen. Pump ≥3–5 s and cross-check `compMat.uniforms.time.value` /
  `renderer.info.render.frame` to know if frames actually ran.
- **Name collisions after inlining**: the viewer already had `const logLines = []` for the
  HUD; a helper of the same name kills the esbuild bundle with "symbol already declared".
  Rename helpers (`logHtml`) before bundling.
- `file://` blocks ES modules and any `fetch` of the model — hence the IIFE bundle and
  `loader.parse(ArrayBuffer)` from base64. `?live=1` only works over HTTP.
- Windows lets two `http.server` sockets bind the same port (SO_REUSEADDR), so a restart
  can leave the OLD code answering. Kill by `CommandLine -like '*office_live.py*'` and
  confirm `/health` shows the new payload.
- `frameToContent()` must be idempotent: build the probe matrix **once** from a fixed
  target/direction and derive right/up from `matrixWorld` columns — recomputing from the
  current camera makes each call zoom further in.
- `Object3D` has no `matrixWorldInverse` (that is `Camera`) — use
  `matrixWorld.clone().invert()`.
- ffmpeg is a native binary: pass `C:/...` paths, never `/c/...` MSYS paths.
- Spread workers by columns inside the room bounds (`Box3` over meshes excluding the bust)
  once there are more than the authored 9; keep the authored grid for the default scene.
