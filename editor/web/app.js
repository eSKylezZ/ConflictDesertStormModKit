// Conflict editor (one game at a time: GAME from the server) - viewport and panels. Every change goes through the server's command API (editor/commands.py),
// the same commands an MCP client uses. Game coordinates: cm, left-handed, Y up -> three.js: m, (x, y, -z).
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { TransformControls } from 'three/addons/controls/TransformControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { clone as cloneSkinned } from 'three/addons/utils/SkeletonUtils.js';

// Model URLs carry the page-load time: the server caches converted models on disk, but the browser kept stale copies
// for an hour (a reload didn't refetch models this script loads) - e.g. after a reader fix.
const MODEL_V = Date.now();

// the game the server has open: {id, title, short, exe, plugin} - filled from game_info before the first level opens
let GAME = { id: '', title: 'Conflict', short: 'Conflict', exe: 'the game', plugin: 'the plugin' };

const $ = (id) => document.getElementById(id);
// Hooks for editor add-on scripts (worldtools.js: Creation-Kit-style tools) - filled at the end of this file.
const hostHooks = { afterBuild: [], pickBlocked: () => false, pasteInPlace: null };
const toThree = (p) => new THREE.Vector3(p[0] / 100, p[1] / 100, -p[2] / 100);
const toGame = (v) => [+(v.x * 100).toFixed(2), +(v.y * 100).toFixed(2), +(-v.z * 100).toFixed(2)];
const deg = THREE.MathUtils.degToRad;

// per-launch API token (server.py writes it into index.html; /api/* refuses calls without it)
const editorToken = document.querySelector('meta[name="editor-token"]')?.content || '';
async function api(cmd, args = {}) {
  // the server runs one command at a time: a quick one can wait behind a slow save, so the default isn't too tight
  const ms = SLOW_COMMANDS.has(cmd) ? 300000 : 30000;
  const abort = new AbortController(), timer = setTimeout(() => abort.abort(), ms);
  let r, j;
  try {
    r = await fetch('/api/' + cmd, { method: 'POST', body: JSON.stringify(args), signal: abort.signal,
                                     headers: { 'Content-Type': 'application/json', 'X-Editor-Token': editorToken } });
    if (!(r.headers.get('Content-Type') || '').includes('application/json'))
      throw new Error(`HTTP ${r.status} ${r.statusText} - not a JSON answer: ${(await r.text()).slice(0, 120)}`);
    j = await r.json();
  } catch (e) {
    if (e.name === 'AbortError') throw new Error(`no answer after ${ms / 1000} s (the editor may still be working on it)`);
    if (e instanceof TypeError) throw new Error(`can't reach the editor server - is server.py still running? (${e.message})`);
    throw e;
  } finally { clearTimeout(timer); }
  if (!POLL_COMMANDS.has(cmd)) requestRender();   // its result usually changes what's drawn (the caller does that next)
  if (!j.ok) throw new Error(j.error || `HTTP ${r.status} ${r.statusText}`);
  return j.result;
}

const POLL_COMMANDS = new Set(['history', 'game_console', 'ui_poll', 'ui_reply', 'status']);
// commands that convert models / textures, build the walk map or write files: up to 5 minutes
const SLOW_COMMANDS = new Set(['open_level', 'open_mission', 'game_mission_open', 'open_dll_mission', 'list_dll_missions', 'new_level', 'save_mod', 'save_mission', 'play', 'export_mod_zip',
  'save_minimap', 'briefing_burn_map', 'briefing_map_image', 'kit_preview', 'kit_pieces', 'kit_models', 'library',
  'asset_options', 'upload_file', 'create_weapon', 'create_skin', 'walkability_grid', 'scene_budget', 'texture_png',
  'terrain_new', 'terrain_noise', 'terrain_set', 'road_new', 'road_set', 'save_script', 'campaign_save',
  'batch', 'check_mission', 'mission_report', 'recovery_restore']);
function status(text, bad = false, detail = '') {
  const s = $('status'); s.textContent = text; s.classList.toggle('bad', bad);
  s.title = detail ? `${text}\n(${detail})` : text;            // the line is cut off with … - the full text on hover
}
async function run(cmd, args, what) {
  try { const r = await api(cmd, args); if (what) status(what); return r; }
  catch (e) { status(e.message, true, `command ${cmd}`); throw e; }
}

// ---- header menus (File / Mission / World / Data): click opens, a click on an item, outside or Esc closes; while one
// is open, pointing at another menu switches to it ----
function closeMenus(except = null) {
  for (const m of document.querySelectorAll('.menu')) if (m !== except) { m.classList.remove('open'); m.querySelector('.menu-list').hidden = true; }
}
for (const m of document.querySelectorAll('.menu')) {
  const btn = m.querySelector('.menu-btn'), list = m.querySelector('.menu-list');
  btn.onclick = () => { const open = list.hidden; closeMenus(); m.classList.toggle('open', open); list.hidden = !open; };
  btn.onmouseenter = () => { if (document.querySelector('.menu.open') && !m.classList.contains('open')) btn.click(); };
  list.addEventListener('click', (e) => { if (e.target.closest('button')) closeMenus(); });
}
document.addEventListener('pointerdown', (e) => { if (!e.target.closest('.menu')) closeMenus(); }, true);
window.addEventListener('keydown', (e) => { if (e.key === 'Escape' && document.querySelector('.menu.open')) { closeMenus(); e.stopImmediatePropagation(); } }, true);

// ---- three.js set-up ----
const view = $('view');
// logarithmic depth: close-ups inside buildings and kilometre views without z-fighting
const renderer = new THREE.WebGLRenderer({ antialias: true, logarithmicDepthBuffer: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.shadowMap.enabled = true;                  // sun shadows (view toolbar: Shadows)
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
view.appendChild(renderer.domElement);
const scene3 = new THREE.Scene();
scene3.background = new THREE.Color(0x8fa6b8);
const perspCam = new THREE.PerspectiveCamera(60, 1, 0.02, 6000);
perspCam.position.set(30, 30, 30);
// Top view (T): an orthographic camera straight down, north (game +z) up; `camera` is whichever one is in use
const topCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 30000);
topCam.up.set(0, 0, -1);
let camera = perspCam, topView = null, topHalf = 50;   // topHalf: half the top view's height (m) at zoom 1     // topView = {offset: the perspective view's offset to come back to}
const orbit = new OrbitControls(camera, renderer.domElement);
orbit.enableDamping = true;
orbit.zoomToCursor = true;      // the wheel zooms toward what is under the cursor
orbit.minDistance = 0.05;
orbit.maxDistance = 3000;
const hemi = new THREE.HemisphereLight(0xdfe8f0, 0x6b5a40, 1.6);
scene3.add(hemi);
const sun = new THREE.DirectionalLight(0xfff1d6, 1.4);
// sunDir points at the sun; the light and its shadow box follow the view (updateSunShadow), so shadows stay sharp
// near what you look at whether the view is 20 m or 1 km across.
const sunDir = new THREE.Vector3(50, 120, 30).normalize();
sun.position.copy(sunDir).multiplyScalar(150);
sun.castShadow = true;
sun.shadow.mapSize.width = 2048; sun.shadow.mapSize.height = 2048;
sun.shadow.bias = -0.0004; sun.shadow.normalBias = 0.04;
scene3.add(sun, sun.target);
const gizmo = new TransformControls(camera, renderer.domElement);
gizmo.setSize(0.8);
scene3.add(gizmo);
gizmo.addEventListener('dragging-changed', (e) => { orbit.enabled = !e.value && !camView; if (!e.value) commitMove().then(gizmoMode); });

// Render on demand: a frame is drawn when something on screen changed (requestRender) or while something moves -
// WASD flying, orbit damping (its 'change' events), the sandstorm, NVG grain, vision cones following their soldiers.
let needsRender = true;
function requestRender() { needsRender = true; }
orbit.addEventListener('change', requestRender);
gizmo.addEventListener('change', requestRender);          // hover highlight of an axis, drags
// Catch-all for the many UI paths that change the view: any input (window capture runs before the canvas handlers
// that stop propagation). Mouse moves count over the view or while a button is held.
for (const ev of ['pointerdown', 'pointerup', 'wheel', 'keydown', 'keyup', 'click', 'input', 'change', 'dragover', 'drop'])
  window.addEventListener(ev, requestRender, { capture: true, passive: true });
window.addEventListener('pointermove', (e) => { if (e.buttons || e.target === renderer.domElement) requestRender(); },
                        { capture: true, passive: true });
document.addEventListener('visibilitychange', requestRender);

new ResizeObserver(() => {
  const w = view.clientWidth, h = view.clientHeight;
  renderer.setSize(w, h); perspCam.aspect = w / h; perspCam.updateProjectionMatrix(); fitTopFrustum();
  requestRender();
}).observe(view);
// WASD fly: W/S along the view, A/D sideways, Q/E down/up; Shift faster. Camera and orbit centre move
// together, so mouse orbiting keeps working around the new spot.
const held = new Set();
const flyKeys = new Set(['w', 'a', 's', 'd', 'q', 'e']);
window.addEventListener('keydown', (e) => {
  if (['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName) || e.ctrlKey || document.querySelector('dialog[open]')) return;
  const k = e.key.toLowerCase();
  if (flyKeys.has(k)) { held.add(k); e.preventDefault(); }
  if (e.key === 'Shift') held.add('shift');
});
window.addEventListener('keyup', (e) => {
  held.delete(e.key.toLowerCase()); if (e.key === 'Shift') held.delete('shift');
});
window.addEventListener('blur', () => held.clear());
const clock = new THREE.Clock();
const fwd = new THREE.Vector3(), side = new THREE.Vector3(), move = new THREE.Vector3();
function fly(dt) {              // true when the camera moved
  if (camView || ![...held].some((k) => flyKeys.has(k))) return false;
  if (topView) { fwd.set(0, 0, -1); side.set(1, 0, 0); }       // top view: W = north (up on screen)
  else { camera.getWorldDirection(fwd); side.crossVectors(fwd, camera.up).normalize(); }
  move.set(0, 0, 0);
  if (held.has('w')) move.add(fwd);
  if (held.has('s')) move.sub(fwd);
  if (held.has('d')) move.add(side);
  if (held.has('a')) move.sub(side);
  if (held.has('e')) move.y += 1;
  if (held.has('q')) move.y -= 1;
  if (!move.lengthSq()) return false;
  const speed = 12 * (held.has('shift') ? 5 : 1) * (topView ? Math.max(1, topHalf / (topCam.zoom * 30)) : 1);   // m/s
  move.normalize().multiplyScalar(speed * dt);
  camera.position.add(move); orbit.target.add(move);
  return true;
}
renderer.setAnimationLoop(() => {
  const dt = Math.min(clock.getDelta(), 0.1);
  if (fly(dt)) needsRender = true;
  orbit.update();                                   // damping: 'change' (requestRender) while the view still moves
  if (followVision()) needsRender = true;
  if (dust.visible || envMode === 'nvg') needsRender = true;   // blowing dust, phosphor grain
  if (!needsRender) return;
  needsRender = false;
  updateSunShadow(); blowDust(dt); renderFrame();
});

// ---- layers ----
const LAYERS = [
  ['world', 'World', '#8d8a6a'], ['props', 'Props', '#b0a27a'], ['objects', 'Objects', '#e05050'],
  ['waypoints', 'Waypoints', '#38c7d6'], ['regions', 'Regions', '#e0b030'],
  ['routes', 'AI routes', '#5fd16b', false],      // thousands of markers: off until wanted (built only while shown)
  ['cameras', 'Cameras', '#b67be8'], ['sounds', 'Sounds', '#6aa0ff'], ['lights', 'Lights', '#ffd36a'],
  ['aipoints', 'AI points', '#7fe0a0', false],   // Global Storm: AI position markers + cover points (facing / arc)
  ['switches', 'Switches', '#ff8c3a'], ['emitters', 'Emitters', '#c9c4b8'],
  ['vision', 'Vision cones', '#4fd16b', false], ['hazards', 'Minefields', '#ff4040'],   // tactics (editor only)
];
// which layers are shown is remembered per browser (a convenience, not part of the mission)
let shownLayers = {};
try { shownLayers = JSON.parse(localStorage.getItem('layers') || '{}'); } catch { /* storage blocked */ }
function rememberLayer(key, on) {
  shownLayers[key] = on;
  try { localStorage.setItem('layers', JSON.stringify(shownLayers)); } catch { /* ignore */ }
}
const groups = {};
// ?show=aipoints,lights turns layers on for this visit (links to a view)
const urlShow = new Set((new URLSearchParams(location.search).get('show') || '').toLowerCase().split(',').filter(Boolean));
for (const [key, label, color, dflt = true] of LAYERS) {
  const on = urlShow.has(key) || (shownLayers[key] ?? dflt);
  const g = new THREE.Group(); g.name = key; g.visible = on; scene3.add(g); groups[key] = g;
  const row = document.createElement('label');
  row.innerHTML = `<input type="checkbox" ${on ? 'checked' : ''}> <span class="swatch" style="background:${color}"></span>${label}` +
                  `<span class="count" id="count-${key}"></span>`;
  row.querySelector('input').onchange = (e) => {
    g.visible = e.target.checked; rememberLayer(key, g.visible); requestRender();
    if (key === 'vision' && g.visible) buildVision().catch(() => {});
    if (key === 'routes' && g.visible && routesStale) buildRoutes();
  };
  $('layers').appendChild(row);
}
const pickable = [];

// ---- models ----
const loader = new GLTFLoader();
const modelCache = new Map();
let level = null;
function model(name, skin = '') {
  // skin: a texture that replaces a skinned model's own (Global Storm soldiers: the HLOI's body texture)
  const key = name.toUpperCase() + (skin ? '@' + skin.toUpperCase() : '');
  const q = skin ? `&skin=${encodeURIComponent(skin)}` : '';
  if (!modelCache.has(key))
    modelCache.set(key, loader.loadAsync(`/model/${level}/${encodeURIComponent(name.toUpperCase())}.glb?v=${MODEL_V}${q}`).then((g) => keepTree(g.scene))
                                .catch(() => null).finally(requestRender));   // its clones are added in the callers' .then
  return modelCache.get(key);
}
// Level switch: the cached originals are freed by the next buildScene(true), once their clones have left the scene
// (freed at once, a frame drawn in between would upload them again).
const staleModels = [];
function clearModelCache() { staleModels.push(...modelCache.values()); modelCache.clear(); }
function disposeStaleModels() {
  for (const p of staleModels.splice(0)) p.then((m) => { if (m) disposeObject(m, true); });
}

// ---- GPU memory: objects leaving the scene free their geometries, materials and textures (clearGroup) - except
// resources that outlive one build: module-level geometries / materials (keep) and everything of a cached model
// (keepTree: clones share its geometry and materials) or of texCache.
const kept = new WeakSet();
function keep(x) { kept.add(x); return x; }
function keepTree(root) {
  root.traverse((x) => {
    if (x.geometry) kept.add(x.geometry);
    for (const m of [].concat(x.material || [])) { kept.add(m); for (const v of Object.values(m)) if (v?.isTexture) kept.add(v); }
  });
  return root;
}
function disposeObject(root, all = false) {       // all: kept resources too (a cached model being dropped)
  root.traverse((x) => {
    if (x.geometry && (all || !kept.has(x.geometry))) x.geometry.dispose();
    if (x.isSkinnedMesh) x.skeleton.dispose();      // its bone texture; every clone has its own skeleton
    if (x.isInstancedMesh) x.dispose();             // instance matrix / colour buffers (not part of the geometry)
    for (const m of [].concat(x.material || [])) {
      if (!all && kept.has(m)) continue;
      for (const v of Object.values(m)) if (v?.isTexture && (all || !kept.has(v))) v.dispose();
      m.dispose();
    }
  });
}

// ---- building the overlays from the scene command ----
let data = null;
const byLine = new Map();       // record line -> Object3D
const objectColor = (o) => /^HERO/i.test(o.model) ? 0x4fd16b : /^IRAQ|Iraqi|^RU_|Russian/i.test(o.model + o.unit) ? 0xe05050
  : /VEH|TRUCK|TANK|BMP|T62|T72|HELI|HIND|BLACKHAWK|UH60|A10|MIG/i.test(o.model + o.unit) ? 0x5b8ff0 : 0xe0c040;
const coneGeo = keep(new THREE.ConeGeometry(0.35, 1.4, 10).translate(0, 0.7, 0));
const ringGeo = keep(new THREE.RingGeometry(0.45, 0.6, 24).rotateX(-Math.PI / 2).translate(0, 0.04, 0));
const facingGeo = keep(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0.05, 0.6), new THREE.Vector3(0, 0.05, 1.3)]));
const ballGeo = keep(new THREE.SphereGeometry(0.25, 10, 8));

function clearGroup(g, except = null) {
  for (const c of [...g.children]) { if (c === except) continue; g.remove(c); disposeObject(c); }
}
const markerMats = new Map();   // one material per marker colour (kept: shared by every build)
function markerMat(color) {
  if (!markerMats.has(color)) markerMats.set(color, keep(new THREE.MeshBasicMaterial({ color })));
  return markerMats.get(color);
}
const cameraGeo = keep(new THREE.OctahedronGeometry(0.5)), soundGeo = keep(new THREE.IcosahedronGeometry(0.35));
const lightGeo = keep(new THREE.SphereGeometry(0.22, 12, 8));
const aiPointGeo = keep(new THREE.SphereGeometry(0.12, 8, 6)), coverGeo = keep(new THREE.BoxGeometry(0.3, 0.3, 0.3));
function marker(geo, color, rec, cat) {
  const m = new THREE.Mesh(geo, markerMat(color));
  m.position.copy(toThree(rec.pos));
  m.userData = { line: rec.line, cat };
  pickable.push(m); byLine.set(rec.line, m);
  return m;
}
function placeProp(obj, rec) {
  obj.position.copy(toThree(rec.pos));
  obj.rotation.set(-deg(rec.rot[0]), -deg(rec.rot[1]), deg(rec.rot[2]), 'YXZ');
  obj.scale.set(rec.scale[0], rec.scale[1], rec.scale[2]);
}

// Zone chunks: the scene gives every object of a zone model (ground tiles, cliff and building pieces) its own record,
// and the game creates each object from that record - so they are drawn one by one from the records (objects without
// a record are never in the game) and, with "Edit world" on, picked, moved, turned and deleted like props. Holders are
// kept by record name across rebuilds (names are unique; lines shift) - a refresh only moves / adds / drops what changed.
const chunkHolders = new Map();
let worldEdit = false;
try { worldEdit = localStorage.getItem('worldEdit') === '1'; } catch { /* storage blocked */ }
function buildChunks(fresh) {
  if (fresh) chunkHolders.clear();          // the world group was emptied by the caller
  const seen = new Set();
  for (const c of data.chunks || []) {
    seen.add(c.name);
    let h = chunkHolders.get(c.name);
    if (!h) {
      h = new THREE.Group(); h.userData = { cat: 'world', chunk: true };
      chunkHolders.set(c.name, h); groups.world.add(h);
      model(c.zone).then((m) => {
        const node = m && h.parent && m.getObjectByName(c.model);
        if (!node) return;
        const o = node.clone(); o.matrix.identity(); o.position.set(0, 0, 0); o.rotation.set(0, 0, 0); o.scale.set(1, 1, 1);
        o.traverse((x) => { if (x.isMesh) { x.userData = { line: h.userData.line, cat: 'world', chunk: true, proxy: h }; pickable.push(x); } });
        shadowed(o); h.add(o); requestRender();
      });
    }
    h.userData.line = c.line; h.userData.name = c.name;
    placeProp(h, c); byLine.set(c.line, h);
    h.traverse((x) => { if (x.isMesh) { x.userData.line = c.line; pickable.push(x); } });
  }
  for (const [name, h] of chunkHolders) if (!seen.has(name)) { chunkHolders.delete(name); groups.world.remove(h); disposeObject(h); }
}
// picking skips the level's own geometry unless "Edit world" is on (a click on the ground would select it otherwise)
const pickOk = (o) => groups[o.userData.cat]?.visible && (!o.userData.chunk || worldEdit) && !hostHooks.pickBlocked(o.userData);

async function buildScene(fresh) {
  data = await run('scene');
  if (fresh) { exitCamView(); logicInfo = null; if (!$('atmos').hidden) openAtmos().catch(() => {}); }
  pickable.length = 0; byLine.clear();
  for (const k of Object.keys(groups)) if (k !== 'world' || fresh) clearGroup(groups[k]);
  if (fresh) disposeStaleModels();
  for (const [key] of LAYERS) if (data[key] || ['world', 'routes'].includes(key)) $('count-' + key).textContent = key === 'world' ? data.zones.length
    : key === 'routes' ? data.routes.length : (data[key] || []).length;
  // World pieces go where their scene record puts them (all at 0 in the campaign; MP_MISSION1's is offset 160 m).
  if (fresh) for (const z of data.zones) if (!z.chunked) model(z.model).then((m) => { if (m) { const w = m.clone(); placeProp(w, z); shadowed(w); groups.world.add(w); } });
  buildChunks(fresh);
  for (const p of data.props) {
    const holder = new THREE.Group(); placeProp(holder, p);
    holder.userData = { line: p.line, cat: 'props' }; byLine.set(p.line, holder);
    groups.props.add(holder);
    if (p.terrain) { addTerrainMesh(holder, p); continue; }
    if (p.road) { addRoadMesh(holder, p); continue; }
    model(p.model, p.skin || '').then((m) => {
      const obj = m ? m.clone() : new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshBasicMaterial({ color: 0xb0a27a, wireframe: true }));
      obj.traverse((c) => { if (c.isMesh) { c.userData = { line: p.line, cat: 'props', proxy: holder }; pickable.push(c); } });
      if (m) shadowed(obj);
      holder.add(obj);
    });
  }
  // Objects (soldiers, vehicles, items, doors): their model (skinned ones cloned with their skeleton), a ring in the
  // squad / enemy / vehicle / item colour and a facing tick; a cone when the model isn't in the extracted files.
  for (const o of data.objects) {
    const holder = new THREE.Group(); placeProp(holder, { pos: o.pos, rot: o.rot, scale: [1, 1, 1] });
    holder.userData = { line: o.line, cat: 'objects' }; byLine.set(o.line, holder);
    const color = objectColor(o);
    const ring = new THREE.Mesh(ringGeo, new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide }));
    ring.userData = { line: o.line, cat: 'objects' }; pickable.push(ring);
    holder.add(ring, new THREE.Line(facingGeo, new THREE.LineBasicMaterial({ color })));
    groups.objects.add(holder);
    holder.userData.layer = groupOf(o);
    // Global Storm soldiers: the body and a separate head model (UNITS.TXT), both skinned to the same skeleton
    const parts = [[o.model, o.skin || '', true]];
    if (o.head) parts.push([o.head.model, o.head.skin || '', false]);
    const style = (obj) => obj.traverse((c) => {
      if (!c.isMesh) return;
      c.userData = { line: o.line, cat: 'objects' }; pickable.push(c);
      if (!o.active) {             // waits for the script: drawn see-through
        c.material = (Array.isArray(c.material) ? c.material : [c.material]).map((mt) => {
          const x = mt.clone(); x.transparent = true; x.opacity = 0.35; x.depthWrite = false; return x; });
        if (c.material.length === 1) c.material = c.material[0];
      }
    });
    for (const [partModel, partSkin, main] of parts) model(partModel, partSkin).then((m) => {
      if (!m && !main) return;
      const obj = m ? cloneSkinned(m) : new THREE.Mesh(coneGeo, new THREE.MeshBasicMaterial({ color }));
      style(obj);
      if (m) shadowed(obj);
      holder.add(obj);
      applyLayerFilter();
      // Global Storm: hats, radios, pouches - rigid models in a bone's space, parented to that bone of the body
      if (main && m && o.attach) for (const a of o.attach) model(a.model).then((am) => {
        const bone = am && obj.getObjectByName(THREE.PropertyBinding.sanitizeNodeName(a.bone));
        if (!bone) return;
        const c = am.clone(); style(c); shadowed(c); bone.add(c); requestRender();
      });
    });
  }
  for (const w of data.waypoints) groups.waypoints.add(marker(ballGeo, 0x38c7d6, w, 'waypoints'));
  drawPathLinks();
  for (const o of data.objects) if (o.patrol) drawPatrol(o);
  camTargets.clear();
  for (const c of data.cameras) {
    const m = marker(cameraGeo, 0xb67be8, c, 'cameras'); groups.cameras.add(m);
    addCameraGuides(c);
  }
  for (const s of data.sounds) {
    const m = marker(soundGeo, 0x6aa0ff, s, 'sounds');
    const ring = new THREE.Mesh(new THREE.SphereGeometry(s.max_distance / 100, 16, 8),
                                new THREE.MeshBasicMaterial({ color: 0x6aa0ff, wireframe: true, transparent: true, opacity: 0.08 }));
    ring.raycast = () => {}; m.add(ring); groups.sounds.add(m);
    if (s.min_distance > 0) {        // inside: full volume; between the spheres it fades out
      const inner = new THREE.Mesh(new THREE.SphereGeometry(s.min_distance / 100, 16, 8),
                                   new THREE.MeshBasicMaterial({ color: 0x9cc0ff, wireframe: true, transparent: true, opacity: 0.22 }));
      inner.raycast = () => {}; m.add(inner);
    }
  }
  // Lights: a bulb in the light's colour; point lights a faint sphere of their reach, spots a cone outline along their
  // direction, suns (DLGT) an arrow
  for (const l of data.lights || []) {
    const col = new THREE.Color(...l.colour.map((c) => Math.min(1, Math.max(0, c))));
    const m = marker(lightGeo, col.getHex(), l, 'lights'); groups.lights.add(m);
    if (l.kind === 'PLGT' && l.radius > 0) {
      const reach = new THREE.Mesh(new THREE.SphereGeometry(l.radius / 100, 16, 8),
                                   new THREE.MeshBasicMaterial({ color: col, wireframe: true, transparent: true, opacity: 0.06 }));
      reach.raycast = () => {}; m.add(reach);
    } else if (l.dir) {
      const dir = gameForward(l.dir[0], l.dir[1]);
      const len = l.kind === 'SLGT' ? Math.min(l.range || 1000, 3000) / 100 : 3;
      const tip = dir.clone().multiplyScalar(len);
      const pts = [new THREE.Vector3(), tip];
      if (l.kind === 'SLGT' && l.cone > 0) {       // outer cone: 4 edges to a ring at the end
        const r = Math.tan(deg(Math.min(l.cone, 80) / 2)) * len;
        const side = new THREE.Vector3(0, 1, 0).cross(dir).normalize();
        if (side.lengthSq() < 1e-6) side.set(1, 0, 0);
        const up = dir.clone().cross(side).normalize();
        const ring = [];
        for (let i = 0; i <= 24; i++) {
          const a = (i / 24) * Math.PI * 2;
          ring.push(tip.clone().addScaledVector(side, Math.cos(a) * r).addScaledVector(up, Math.sin(a) * r));
        }
        for (let i = 0; i < 24; i += 6) pts.push(new THREE.Vector3(), ring[i]);
        for (let i = 0; i < 24; i++) pts.push(ring[i], ring[i + 1]);
      }
      const lines = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(pts),
                                           new THREE.LineBasicMaterial({ color: col, transparent: true, opacity: 0.6 }));
      lines.raycast = () => {}; m.add(lines);
    }
  }
  for (const p of data.aipoints || []) {
    const cover = p.kind === 'cover';
    const m = marker(cover ? coverGeo : aiPointGeo, cover ? 0x3fd08a : 0x9fe8c0, p, 'aipoints'); groups.aipoints.add(m);
    if (cover && p.facing != null) {   // the arc it covers, 1.5 m out, around its facing (Global Storm)
      const pts = [], n = 12, half = (p.arc || 90) / 2;
      for (let i = 0; i <= n; i++) {
        const d = gameForward(0, p.facing - half + (i / n) * 2 * half).multiplyScalar(1.5);
        if (i === 0) pts.push(new THREE.Vector3(), d); else pts.push(pts[pts.length - 1], d);
      }
      pts.push(pts[pts.length - 1], new THREE.Vector3());
      const fan = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(pts),
                                         new THREE.LineBasicMaterial({ color: 0x3fd08a, transparent: true, opacity: 0.7 }));
      fan.raycast = () => {}; m.add(fan);
    }
  }
  for (const r of data.regions) {
    const m = marker(ballGeo, 0xe0b030, r, 'regions');
    let shape;
    if (r.type === 1) shape = new THREE.Mesh(new THREE.SphereGeometry(r.radius / 100, 20, 10),
                                             new THREE.MeshBasicMaterial({ color: 0xe0b030, wireframe: true }));
    else {
      const size = r.max.map((v, i) => Math.abs(v - r.min[i]) / 100);
      shape = new THREE.Mesh(new THREE.BoxGeometry(...size), new THREE.MeshBasicMaterial({ color: 0xe0b030, wireframe: true }));
      const c = toThree(r.max.map((v, i) => (v + r.min[i]) / 2));
      shape.position.set(c.x, c.y, c.z);
      m.rotation.set(-deg(r.rot[0]), -deg(r.rot[1]), deg(r.rot[2]), 'YXZ');
    }
    shape.raycast = () => {}; m.add(shape); groups.regions.add(m);
  }
  drawInteractive();
  buildRoutes();
  buildOutliner();
  buildScriptLayers();
  if (fresh) { missionFlow = null; obBaseTargets = null; buildMissionGroups().catch(() => {}); }
  if ($('walk-toggle')?.checked) buildWalkMap(fresh).catch(() => {});
  productionAfterScene(fresh);
  buildTactics().catch(() => {});
  applyIsolate();
  for (const f of hostHooks.afterBuild) { try { f(fresh); } catch (err) { console.error(err); } }
  requestRender();
}

// Switches: an orange keypad box, a dashed line to each door it drives (red box when a door / its region is gone).
// Emitters: a ball in the particle colour with an arrow along the mean particle velocity; faint when off at the start.
const switchGeo = keep(new THREE.BoxGeometry(0.3, 0.45, 0.12).translate(0, 1.2, 0));
const emitterGeo = keep(new THREE.SphereGeometry(0.3, 12, 8));
function drawInteractive() {
  for (const w of data.switches || []) {
    const m = marker(switchGeo, w.missing.length ? 0xff3030 : 0xff8c3a, w, 'switches');
    m.rotation.set(0, -deg(w.rot[1]), 0, 'YXZ');
    groups.switches.add(m);
    for (const tp of w.target_pos) {
      const a = toThree(w.pos).add(new THREE.Vector3(0, 1.2, 0)), b = toThree(tp).add(new THREE.Vector3(0, 1, 0));
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([a, b]),
                                  new THREE.LineDashedMaterial({ color: 0xff8c3a, dashSize: 0.4, gapSize: 0.25 }));
      line.computeLineDistances(); line.raycast = () => {}; groups.switches.add(line);
    }
  }
  for (const e of data.emitters || []) {
    const [r, g, b, a] = e.colour.map((v) => Math.min(1, Math.max(0, v)));
    const col = new THREE.Color(r, g, b);
    const m = new THREE.Mesh(emitterGeo, new THREE.MeshBasicMaterial({ color: col, transparent: true,
      opacity: e.on ? Math.max(0.35, a) : 0.2, depthWrite: false }));
    m.position.copy(toThree(e.pos)); m.userData = { line: e.line, cat: 'emitters' };
    pickable.push(m); byLine.set(e.line, m);
    const v = new THREE.Vector3(e.velocity[0], e.velocity[1], -e.velocity[2]);
    if (v.lengthSq() > 1e-6) {
      const arrow = new THREE.ArrowHelper(v.clone().normalize(), new THREE.Vector3(), Math.min(3, 0.5 + v.length() / 150),
                                          col.getHex(), 0.3, 0.2);
      arrow.traverse((c) => { c.raycast = () => {}; }); m.add(arrow);
    }
    groups.emitters.add(m);
  }
}

// ---- the base mission's own logic (editor/missionlogic.py): events -> readable steps; mission groups ----
let missionFlow = null;
const hiddenGroupLines = new Set();
async function loadFlow() { missionFlow ||= await api('mission_flow').catch(() => null); return missionFlow; }
function lineOfScript(id) { return data.objects.find((o) => o.script_id === id)?.line; }
function segHtml(seg) {
  if (typeof seg === 'string') return escHtml(seg);
  return `<a class="ref ${seg.ref}" data-ref="${seg.ref}" data-id="${seg.id}" title="${seg.ref}">${escHtml(seg.text)}</a>`;
}
function flowNode(n, open) {
  const html = n.text.map(segHtml).join('');
  if (!n.children.length) { const d = document.createElement('div'); d.className = `leaf k-${n.kind}`; d.innerHTML = html; return d; }
  const det = document.createElement('details'); det.className = `k-${n.kind}`; det.open = open && n.open;
  const sum = document.createElement('summary'); sum.innerHTML = html; det.appendChild(sum);
  for (const c of n.children) det.appendChild(flowNode(c, open));
  return det;
}
function onFlowClick(e) {
  const a = e.target.closest('a.ref'); if (!a) return;
  e.preventDefault();
  const id = +a.dataset.id;
  if (a.dataset.ref === 'object') { const l = lineOfScript(id); if (l) select({ line: l }, true); }
  else if (a.dataset.ref === 'region') { const r = data.regions[id]; if (r) select({ line: r.line }, true); }
  else if (a.dataset.ref === 'waypoint') { const w = data.waypoints[id]; if (w) select({ line: w.line }, true); }
  else if (a.dataset.ref === 'objective') showObTab('obj');
}
async function renderFlow() {
  const box = $('ob-flow'); box.innerHTML = '<span class="hint">reading the mission DLL…</span>';
  const f = await loadFlow();
  box.innerHTML = '';
  const has = !!f?.dll;
  $('ob-tab-flow').hidden = !has;
  $('ob-rules-base').innerHTML = has ? `${escHtml(f.dll)} runs the original mission alongside your rules - see the <b>Mission logic</b> tab.` : '';
  if (!has) return;
  $('ob-flow-title').textContent = `How ${f.dll} runs this mission, read from your own copy of it: the set-up, every game ` +
    'event it reacts to and what it checks during play. Read-only - the DLL keeps doing all of this; your Event rules run ' +
    'as well. Click a name to select it in the scene.';
  const q = $('ob-flow-search').value.trim().toLowerCase();
  const plain = (n) => n.text.map((s) => typeof s === 'string' ? s : s.text).join('') + ' ' + n.children.map(plain).join(' ');
  const count = (ns) => ns.reduce((a, n) => a + 1 + count(n.children), 0);
  const part = (ev) => ['init', 'msg_3b', 'msg_12'].includes(ev.id) ? 0 : ev.id === 'update' ? 1 : 2;
  const order = { init: 0, msg_3b: 1, msg_12: 2 };
  const events = [...f.events].sort((a, b) => part(a) - part(b) || (order[a.id] ?? 0) - (order[b.id] ?? 0));
  const heads = ['Before play: loading, briefing, start', 'During play: checked every frame', 'Reactions to game events'];
  let shown = -1;
  for (const ev of events) {
    const nodes = ev.nodes.filter((n) => !q || plain(n).toLowerCase().includes(q) || ev.title.toLowerCase().includes(q));
    if (!nodes.length) continue;
    if (part(ev) !== shown) { shown = part(ev); box.insertAdjacentHTML('beforeend', `<h4>${heads[shown]}</h4>`); }
    const det = document.createElement('details'); det.className = 'ev'; det.open = !!q;
    det.innerHTML = `<summary title="${escHtml(ev.note || '')}">${escHtml(ev.title)}<span class="n">${count(ev.nodes)} steps</span></summary>`;
    for (const n of nodes) det.appendChild(flowNode(n, !!q));
    box.appendChild(det);
  }
  if (q) for (const el of box.querySelectorAll('summary, .leaf'))
    if (el.textContent.toLowerCase().includes(q) && !el.querySelector('summary')) el.classList.add('hit');
  if (!box.children.length) box.innerHTML = '<span class="hint">nothing matches</span>';
}
function openFlowFor(text) {
  openObjectives('flow').then(() => { $('ob-flow-search').value = text; renderFlow(); }).catch(() => {});
}
$('ob-flow-collapse').onclick = () => { for (const d of $('ob-flow').querySelectorAll('details')) d.open = false; };
$('ob-flow').onclick = onFlowClick;
$('logic-list').onclick = onFlowClick;
$('ob-flow-search').oninput = () => { clearTimeout(renderFlow.t); renderFlow.t = setTimeout(() => renderFlow().catch(() => {}), 250); };
$('ob-flow-expand').onclick = () => { for (const d of $('ob-flow').querySelectorAll('details')) d.open = true; };
async function showObjectLogic(o) {
  const r = await api('object_logic', { script_id: o.script_id }).catch(() => null);
  if (!r?.dll || selection?.line !== o.line) return;
  const box = $('logic-list'); box.innerHTML = '';
  const by = {};
  for (const u of r.uses) (by[u.event] ||= []).push(u);
  const parts = Object.keys(by).length;
  $('logic-sum').innerHTML = r.uses.length
    ? `${escHtml(r.dll)} uses #${o.script_id} in ${r.uses.length} step${r.uses.length > 1 ? 's' : ''} across ${parts} part${parts > 1 ? 's' : ''} of the mission. <a href="#" id="logic-open">Show in Mission logic</a>`
    : `${escHtml(r.dll)} doesn't name this object (#${o.script_id}) directly.`;
  $('logic-open')?.addEventListener('click', (e) => { e.preventDefault(); openFlowFor(`#${o.script_id}`); });
  for (const [ev, uses] of Object.entries(by)) {
    const det = document.createElement('details'); det.className = 'ev';
    det.innerHTML = `<summary>${escHtml(ev)} <span class="hint">(${uses.length})</span></summary>`;
    let last = null;
    for (const u of uses.slice(0, 20)) {
      if (u.when && u.when !== last) det.insertAdjacentHTML('beforeend', `<div class="when">${escHtml(u.when)}</div>`);
      last = u.when;
      det.insertAdjacentHTML('beforeend', `<div class="leaf">${escHtml(u.text)}</div>`);
    }
    box.appendChild(det);
  }
  $('logic-row').hidden = false;
}
// Mission groups (left panel): what the base mission brings into / takes out of play together, and when - in
// sections, two lines per group (trigger, effect), a click shows every trigger and the objects.
const MG_SECTIONS = [['start', 'At the start'], ['triggered', 'Triggered during play'], ['events', 'On game events'],
                     ['reinforcements', 'Reinforcements (spawn points)'], ['waiting', 'Never activated by id']];
async function buildMissionGroups() {
  const box = $('mg-list'); box.innerHTML = '';
  hiddenGroupLines.clear();
  const f = await loadFlow();
  $('mission-groups').hidden = !(f?.dll && f.groups.length);
  if ($('mission-groups').hidden) return;
  const named = new Set(f.groups.flatMap((g) => g.objects.map((o) => o.id)));
  const rest = data.objects.filter((o) => !o.active && !o.cutscene && !named.has(o.script_id));
  const all = [...f.groups];
  if (rest.length) all.push({ category: 'waiting', title: 'Waiting, but not brought in by script id',
    effect: `${rest.length} wait: spawned by a cutscene / table, or never`, action: 'none',
    objects: rest.map((o) => ({ id: o.script_id, text: `${o.unit} #${o.script_id}` })), triggers: [], how: [] });
  $('mg-count').textContent = f.groups.length;
  $('mg-hint').textContent = `What ${f.dll} brings into play, and when (the game has no layers of its own).`;
  $('mg-hint').title = `${f.dll} brings objects into play by script id when something happens - these are those moments. ` +
    'Read-only. Untick a row to hide its objects in the view; click it for the details.';
  for (const [cat, label] of MG_SECTIONS) {
    const list = all.filter((g) => g.category === cat);
    if (!list.length) continue;
    const sec = document.createElement('details'); sec.className = 'mg-sec'; sec.open = cat !== 'reinforcements' && cat !== 'waiting';
    sec.innerHTML = `<summary>${label}<span class="count">${list.length}</span></summary>`;
    for (const g of list) sec.appendChild(groupRow(g));
    box.appendChild(sec);
  }
}
function groupRow(g) {
  const lines = g.objects.map((o) => lineOfScript(o.id)).filter(Boolean);
  const title = g.title.replace(/^When /, '');
  const up = /appear|respawn|visible/.test(g.effect);
  const row = document.createElement('div'); row.className = 'mg';
  row.innerHTML = `<input type="checkbox" checked title="show these objects in the view">` +
    `<div class="t" title="${escHtml(g.title + ' → ' + g.effect)}"><div class="title">${escHtml(title[0].toUpperCase() + title.slice(1))}</div>` +
    `<div class="effect"><span class="${g.action === 'none' ? '' : up ? 'up' : 'down'}">${g.action === 'none' ? '…' : up ? '▲' : '▼'}</span> ${escHtml(g.effect)}</div></div>` +
    `<button title="select these objects">⊙</button>`;
  row.querySelector('input').onchange = (e) => {
    for (const l of lines) { if (e.target.checked) hiddenGroupLines.delete(l); else hiddenGroupLines.add(l); }
    applyLayerFilter();
  };
  row.querySelector('button').onclick = () => selectMany(lines);
  row.querySelector('.t').onclick = () => {
    const open = row.querySelector('.more');
    if (open) { open.remove(); return; }
    const more = document.createElement('div'); more.className = 'more';
    if (g.triggers.length > 1 || (g.triggers[0] && g.triggers[0].text !== g.title))
      more.innerHTML = g.triggers.map((t) => `<div>• ${escHtml(t.text)}</div>`).join('');
    more.insertAdjacentHTML('beforeend', '<div>' + g.objects.map((o) => `<a data-id="${o.id}">${escHtml(o.text)}</a>`).join('') + '</div>');
    for (const a of more.querySelectorAll('a')) a.onclick = () => { const l = lineOfScript(+a.dataset.id); if (l) select({ line: l }, true); };
    row.appendChild(more);
  };
  return row;
}

// Script layers: named groups of objects the mission script shows (game.show_layer) or hides (game.hide_layer)
// together. Each row: view checkbox, start state (hidden = waits for the script, shown = in play from the start),
// select all, rename, delete. "(cutscene actors)" and "(waiting for the script)" are the scene's own inactive objects.
const hiddenLayers = new Set();
let layerInfo = {};
const groupOf = (o) => o.layer || (o.active ? '' : o.cutscene ? '(cutscene actors)' : '(waiting for the script)');
async function buildScriptLayers() {
  layerInfo = await api('list_layers');
  const members = new Map();
  for (const o of data.objects) {
    const name = groupOf(o);
    if (name) { if (!members.has(name)) members.set(name, []); members.get(name).push(o.line); }
  }
  const box = $('script-layers'); box.innerHTML = '';
  if (!members.size) box.innerHTML = '<span class="hint">none - select objects, type a layer name in their properties</span>';
  const names = [...members.keys()].sort((a, b) => (a.startsWith('(') - b.startsWith('(')) || a.localeCompare(b));
  for (const name of names) {
    const own = !name.startsWith('(');
    const start = layerInfo[name]?.start || 'hidden';
    const row = document.createElement('div'); row.className = 'layer';
    row.innerHTML = `<input type="checkbox" ${hiddenLayers.has(name) ? '' : 'checked'} title="show in the editor">` +
      `<span class="name" title="${name}">${name}</span><span class="count">${members.get(name).length}</span>` +
      (own ? `<button class="start" title="start state in the game">${start}</button>` : '') +
      `<button class="sel" title="select its objects">⊙</button>` +
      (own ? `<button class="ren" title="rename">✎</button><button class="del" title="delete the layer (objects back in play)">×</button>` : '');
    row.querySelector('input').onchange = (e) => {
      if (e.target.checked) hiddenLayers.delete(name); else hiddenLayers.add(name);
      applyLayerFilter();
    };
    row.querySelector('.sel').onclick = () => selectMany(members.get(name));
    if (own) {
      row.querySelector('.start').onclick = async () => {
        await run('set_layer_start', { layer: name, start: start === 'hidden' ? 'shown' : 'hidden' },
                  `${name} starts ${start === 'hidden' ? 'shown (game.hide_layer removes it)' : 'hidden (game.show_layer brings it)'}`);
        await refresh();
      };
      row.querySelector('.ren').onclick = async () => {
        const to = prompt(`Rename layer "${name}" to:`, name);
        if (to && to.trim() && to.trim() !== name) { await run('rename_layer', { layer: name, to: to.trim() }, `renamed to ${to.trim()}`); await refresh(); }
      };
      row.querySelector('.del').onclick = async () => {
        await run('delete_layer', { layer: name }, `layer ${name} deleted - its objects are in play from the start`); await refresh();
      };
    }
    box.appendChild(row);
  }
  $('layer-names').innerHTML = names.filter((n) => !n.startsWith('(')).map((n) => `<option value="${n}">`).join('');
  applyLayerFilter();
}
function applyLayerFilter() {
  for (const h of groups.objects.children)
    h.visible = !hiddenLayers.has(h.userData.layer || '') && !hiddenGroupLines.has(h.userData.line)
      && (!isolated || isolated.has(h.userData.line));
  markSelected();
}

// ---- terrain patches: a heightfield mesh (game local x, height, z -> three x, y, -z in metres), the game texture tiled ----
const texCache = new Map();
function gameTexture(name) {
  if (!texCache.has(name)) {
    texCache.set(name, api('texture_png', { name }).then((url) => {
      const t = keep(new THREE.TextureLoader().load(url, requestRender));
      t.wrapS = t.wrapT = THREE.RepeatWrapping; t.colorSpace = THREE.SRGBColorSpace;
      return t;
    }).catch(() => null));
  }
  return texCache.get(name);
}
// Same split as the exported model (terrain.py parts): flat faces mapped from above with the ground texture, faces
// steeper than t.slope with the steep texture mapped from the side they face (no vertical streaks). Group 0 / 1.
function terrainGeometry(t) {
  const n = t.n, half = (n - 1) * t.cell / 2, h = t.heights, cosLimit = Math.cos(t.slope * Math.PI / 180);
  const P = (k) => [(k % n) * t.cell - half, h[k], Math.floor(k / n) * t.cell - half];   // game local cm
  const flat = [], steep = [];
  for (let j = 0; j < n - 1; j++) for (let i = 0; i < n - 1; i++) {
    const a = j * n + i, b = (j + 1) * n + i, c = a + 1, d = b + 1;
    for (const tri of [[a, b, c], [b, d, c]]) {
      const [p0, p1, p2] = tri.map(P);
      const e1 = [p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]], e2 = [p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2]];
      const cr = [e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0]];
      const len = Math.hypot(...cr) || 1;
      (t.steep_texture && cr[1] / len < cosLimit ? steep : flat).push({ tri, ps: [p0, p1, p2], cr });
    }
  }
  const pos = [], uv = [];
  const put = (p, u, v) => { pos.push(p[0] / 100, p[1] / 100, -p[2] / 100); uv.push(u, v); };
  // ground texture by distance along the surface (terrain.py surface_uv): slopes aren't stretched
  const su = new Float32Array(n * n), sv = new Float32Array(n * n);
  for (let j = 0; j < n; j++) { let acc = -half; for (let i = 0; i < n; i++) { if (i) acc += Math.hypot(t.cell, h[j * n + i] - h[j * n + i - 1]); su[j * n + i] = acc; } }
  for (let i = 0; i < n; i++) { let acc = -half; for (let j = 0; j < n; j++) { if (j) acc += Math.hypot(t.cell, h[j * n + i] - h[(j - 1) * n + i]); sv[j * n + i] = acc; } }
  for (const f of flat) for (const k of [f.tri[0], f.tri[2], f.tri[1]]) put(P(k), su[k] / t.tile, sv[k] / t.tile);
  for (const f of steep) {
    const across = Math.abs(f.cr[0]) >= Math.abs(f.cr[2]) ? 2 : 0;
    for (const p of [f.ps[0], f.ps[2], f.ps[1]]) put(p, p[across] / t.tile, -p[1] / t.tile);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
  g.addGroup(0, flat.length * 3, 0); g.addGroup(flat.length * 3, steep.length * 3, 1);
  g.computeVertexNormals();
  return g;
}
function addTerrainMesh(holder, p) {
  const mats = [p.terrain.texture, p.terrain.steep_texture || p.terrain.texture].map((name) => {
    const mat = new THREE.MeshLambertMaterial({ color: 0xffffff, side: THREE.DoubleSide });
    gameTexture(name).then((tex) => { if (tex) { mat.map = tex; mat.needsUpdate = true; } else mat.color.set(0xc8b48a); });
    return mat;
  });
  const mesh = new THREE.Mesh(terrainGeometry(p.terrain), mats);
  mesh.castShadow = mesh.receiveShadow = true;
  mesh.userData = { line: p.line, cat: 'props', proxy: holder, terrain: p.model };
  holder.userData.terrain = { model: p.model, data: p.terrain, mesh };
  pickable.push(mesh); holder.add(mesh);
}

// ---- roads: the strip roads.py builds (local cm), the road texture along it ----
function addRoadMesh(holder, p) {
  const m = p.road.mesh, pos = [], uv = [], idx = [];
  for (const v of m.pos) pos.push(v[0] / 100, v[1] / 100, -v[2] / 100);
  for (const u of m.uv) uv.push(u[0], u[1]);
  for (const t of m.tris) idx.push(t[0], t[2], t[1]);
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
  g.setIndex(idx); g.computeVertexNormals();
  // drawn in front of the ground and of lower roads (each road sits a little higher, like in the game)
  const mat = new THREE.MeshLambertMaterial({ color: 0xffffff, side: THREE.DoubleSide, polygonOffset: true,
                                              polygonOffsetFactor: -2 - (p.road.lift || 6) / 2, polygonOffsetUnits: -4 });
  gameTexture(p.road.texture).then((tex) => { if (tex) { mat.map = tex; mat.needsUpdate = true; } else mat.color.set(0x555555); });
  const mesh = new THREE.Mesh(g, mat);
  mesh.userData = { line: p.line, cat: 'props', proxy: holder, road: p.model };
  holder.userData.road = { model: p.model, data: p.road };
  pickable.push(mesh); holder.add(mesh);
}

// Road panel: click points on the ground, Finish (Enter) -> a smoothed centreline every 2 m, each sample dropped onto
// the ground (rays down onto the world, props and terrain - not other roads) -> road_new (dense).
let roadOpts = null, roadClicks = [];
const roadPreview = new THREE.Group(); scene3.add(roadPreview);
function drawRoadPreview() {
  clearGroup(roadPreview);
  const dot = new THREE.SphereGeometry(0.4, 10, 8), mat = new THREE.MeshBasicMaterial({ color: 0xffd23f, depthTest: false });
  for (const p of roadClicks) { const s = new THREE.Mesh(dot, mat); s.position.copy(p); s.renderOrder = 11; roadPreview.add(s); }
  if (roadClicks.length > 1) {
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(catmull(roadClicks, 1.0)),
                                new THREE.LineBasicMaterial({ color: 0xffd23f, depthTest: false }));
    line.renderOrder = 11; roadPreview.add(line);
  }
}
function catmull(pts, spacing) {                 // three-space points (m), resampled every `spacing` m
  const out = [pts[0].clone()];
  for (let k = 0; k < pts.length - 1; k++) {
    const p0 = pts[Math.max(0, k - 1)], p1 = pts[k], p2 = pts[k + 1], p3 = pts[Math.min(pts.length - 1, k + 2)];
    const steps = Math.max(1, Math.round(p1.distanceTo(p2) / spacing));
    for (let s = 1; s <= steps; s++) {
      const t = s / steps, t2 = t * t, t3 = t2 * t;
      out.push(new THREE.Vector3(...['x', 'y', 'z'].map((c) => 0.5 * (2 * p1[c] + (-p0[c] + p2[c]) * t
        + (2 * p0[c] - 5 * p1[c] + 4 * p2[c] - p3[c]) * t2 + (-p0[c] + 3 * p1[c] - 3 * p2[c] + p3[c]) * t3))));
    }
  }
  return out;
}
function groundBelow(p) {
  const caster = new THREE.Raycaster(new THREE.Vector3(p.x, p.y + 50, p.z), new THREE.Vector3(0, -1, 0));
  const hit = caster.intersectObjects([groups.world, groups.props], true)
    .find((h) => h.object.isMesh && !h.object.userData.road && !h.object.userData.ghost);
  return hit ? hit.point : p;
}
function roadDrawing() { return !$('road').hidden && $('r-draw').checked; }
renderer.domElement.addEventListener('pointerdown', (e) => {
  if (e.button !== 0 || !roadDrawing()) return;
  const r = renderer.domElement.getBoundingClientRect();
  ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
  const hit = ray.intersectObjects([groups.world, groups.props], true).find((h) => h.object.isMesh && !h.object.userData.road);
  if (!hit) return;
  e.stopImmediatePropagation(); e.preventDefault();
  roadClicks.push(hit.point.clone()); drawRoadPreview();
  status(`${roadClicks.length} point(s) - click more, Finish (Enter) makes the road, Esc clears`);
}, { capture: true });
async function finishRoad() {
  if (roadClicks.length < 2) return status('click at least two points on the ground', true);
  const centre = catmull(roadClicks, 2.0).map(groundBelow).map(toGame);
  const r = await run('road_new', { points: centre, dense: true, width: +$('r-width').value, texture: $('r-texture').value,
                                    surface: $('r-surface').value }, 'road made');
  roadClicks = []; drawRoadPreview();
  await refresh(); await select({ line: r.line });
  status(`${r.model}: ${r.points} points - select it to change width / texture`);
}
window.addEventListener('keydown', (e) => {
  if (!roadDrawing() || e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
  if (e.key === 'Enter') { e.preventDefault(); finishRoad().catch(() => {}); }
  if (e.key === 'Escape') { roadClicks = []; drawRoadPreview(); status('road points cleared'); }
}, { capture: true });
async function openRoads() {
  roadOpts = roadOpts || await run('road_options');
  for (const [id, list] of [['r-texture', roadOpts.textures], ['r-surface', roadOpts.surfaces]]) {
    const sel = $(id); if (sel.options.length) continue;
    for (const v of list) sel.add(new Option(roadOpts.variants?.includes(v) ? `${v} (one-off: scorch / end / junction)` : v, v));
  }
  $('road').hidden = false; updateRoadPanel();
}
function currentRoad() {
  const h = selection?.line !== undefined ? byLine.get(selection.line) : null;
  return h?.userData?.road ? h : null;
}
function updateRoadPanel() {
  const h = currentRoad();
  $('r-current').textContent = h ? `${h.userData.road.model} - ${h.userData.road.data.points.length} points` : 'Selected road (none)';
  for (const id of ['r-apply', 'r-across']) $(id).disabled = !h;
  if (h) { const d = h.userData.road.data; $('r-across').checked = d.across; }
}
$('road-btn').onclick = () => { if ($('road').hidden) openRoads().catch(() => {}); else $('road').hidden = true; };
$('r-close').onclick = () => { $('road').hidden = true; roadClicks = []; drawRoadPreview(); };
$('r-finish').onclick = () => finishRoad().catch(() => {});
$('r-clear').onclick = () => { roadClicks = []; drawRoadPreview(); };
$('r-apply').onclick = async () => {
  const h = currentRoad(); if (!h) return;
  await run('road_set', { model: h.userData.road.model, width: +$('r-width').value, texture: $('r-texture').value,
                          surface: $('r-surface').value, across: $('r-across').checked }, 'road changed');
  const line = h.userData.line; await refresh(); await select({ line });
};

// Terrain panel: new patch, brushes (drag on a patch), hills, texture. A stroke is sculpted here and sent as one
// terrain_set (one undo step); the same brushes are commands for MCP (terrain_brush).
let terrainOpts = null, sculpt = null;   // sculpt = {holder, heights} during a stroke
function currentTerrain() {
  const h = selection?.line !== undefined ? byLine.get(selection.line) : null;
  return h?.userData?.terrain ? h : null;
}
function brushLocal(holder, point) {           // three world point -> patch local game x / z (cm)
  const l = holder.userData.terrain.mesh.worldToLocal(point.clone());
  return [l.x * 100, -l.z * 100];
}
function applyBrush(t, heights, x, z, op, radius, strength) {
  const n = t.n, half = (n - 1) * t.cell / 2;
  const target = op === 'flatten' ? (sculpt.flatten ??= sampleHeight(t, heights, x, z)) : 0;
  const changed = [];
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
    const d = Math.hypot(i * t.cell - half - x, j * t.cell - half - z);
    if (d >= radius) continue;
    // falloff (World Tools adds the slider): the inner part of the radius at full strength, a cosine edge outside it
    const inner = radius * (1 - Math.min(100, Math.max(1, +($('t-falloff')?.value ?? 100))) / 100);
    const w = d <= inner ? 1 : 0.5 * (1 + Math.cos(Math.PI * (d - inner) / (radius - inner))), k = j * n + i;
    if (op === 'raise') heights[k] += strength * w;
    else if (op === 'lower') heights[k] -= strength * w;
    else if (op === 'flatten') heights[k] += (target - heights[k]) * Math.min(1, strength / 100) * w;
    else if (op === 'smooth') {
      const nb = [[i - 1, j], [i + 1, j], [i, j - 1], [i, j + 1]].filter(([a, b]) => a >= 0 && b >= 0 && a < n && b < n).map(([a, b]) => heights[b * n + a]);
      changed.push([k, heights[k] + (nb.reduce((s, v) => s + v, 0) / nb.length - heights[k]) * Math.min(1, strength / 100) * w]);
    }
  }
  for (const [k, v] of changed) heights[k] = v;
}
function sampleHeight(t, h, x, z) {
  const half = (t.n - 1) * t.cell / 2, i = Math.round((x + half) / t.cell), j = Math.round((z + half) / t.cell);
  return h[Math.min(t.n - 1, Math.max(0, j)) * t.n + Math.min(t.n - 1, Math.max(0, i))];
}
const brushRing = new THREE.Mesh(new THREE.RingGeometry(0.96, 1, 48).rotateX(-Math.PI / 2), new THREE.MeshBasicMaterial({ color: 0xffd23f, depthTest: false, transparent: true }));
brushRing.renderOrder = 10; brushRing.visible = false; scene3.add(brushRing);
function terrainHit(e) {
  const holder = currentTerrain();
  if (!holder || $('terrain').hidden || !$('t-sculpt').checked) return null;
  const r = renderer.domElement.getBoundingClientRect();
  ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
  const hit = ray.intersectObject(holder.userData.terrain.mesh, false)[0];
  return hit ? { holder, point: hit.point } : null;
}
function brushParams() {
  return { op: document.querySelector('input[name=t-op]:checked').value, radius: +$('t-radius').value * 100, strength: +$('t-strength').value };
}
function strokeAt(hit) {
  const t = hit.holder.userData.terrain, [x, z] = brushLocal(hit.holder, hit.point), b = brushParams();
  applyBrush(t.data, sculpt.heights, x, z, b.op, b.radius, b.strength * (b.op === 'smooth' || b.op === 'flatten' ? 1 : 0.15));
  t.mesh.geometry.dispose(); t.mesh.geometry = terrainGeometry({ ...t.data, heights: sculpt.heights });
}
renderer.domElement.addEventListener('pointerdown', (e) => {
  const hit = e.button === 0 && terrainHit(e);
  if (!hit) return;
  e.stopImmediatePropagation(); e.preventDefault();
  orbit.enabled = false;
  sculpt = { holder: hit.holder, heights: [...hit.holder.userData.terrain.data.heights], flatten: undefined };
  renderer.domElement.setPointerCapture(e.pointerId);
  strokeAt(hit);
}, { capture: true });
renderer.domElement.addEventListener('pointermove', (e) => {
  const hit = terrainHit(e);
  brushRing.visible = !!hit;
  if (!hit) return;
  brushRing.position.copy(hit.point).add(new THREE.Vector3(0, 0.05, 0));
  brushRing.scale.setScalar(+$('t-radius').value);
  if (sculpt && e.buttons & 1) { e.stopImmediatePropagation(); strokeAt(hit); }
}, { capture: true });
renderer.domElement.addEventListener('pointerup', async (e) => {
  if (!sculpt) return;
  e.stopImmediatePropagation();
  const s = sculpt; sculpt = null; orbit.enabled = true;
  const model = s.holder.userData.terrain.model, line = s.holder.userData.line;
  await run('terrain_set', { model, heights: s.heights.map((h) => +h.toFixed(1)) }, 'sculpted');
  await refresh(); await select({ line });
}, { capture: true });

async function openTerrain() {
  terrainOpts = terrainOpts || await run('terrain_options');
  for (const [id, list] of [['t-texture', terrainOpts.textures], ['t-new-texture', terrainOpts.textures],
                              ['t-steep', ['', ...terrainOpts.textures]], ['t-new-steep', ['', ...terrainOpts.textures]],
                              ['t-surface', terrainOpts.surfaces]]) {
    const sel = $(id); if (sel.options.length) continue;
    for (const v of list) sel.add(new Option(v || '(none)', v));
  }
  if (terrainOpts.steep) $('t-new-steep').value = terrainOpts.steep;
  $('t-surface').value = 'Sand';
  $('terrain').hidden = false; updateTerrainPanel();
}
function updateTerrainPanel() {
  const h = currentTerrain();
  $('t-current').textContent = h ? `${h.userData.terrain.model} - ${Math.round((h.userData.terrain.data.n - 1) * h.userData.terrain.data.cell / 100)} m, ${h.userData.terrain.data.n}x${h.userData.terrain.data.n}` : 'select a terrain patch (or make one)';
  for (const id of ['t-hills', 't-texture', 't-steep', 't-slope', 't-apply-tex', 't-fit']) $(id).disabled = !h;
  if (h) {
    const d = h.userData.terrain.data;
    $('t-texture').value = d.texture; $('t-steep').value = d.steep_texture || '';
    $('t-slope').value = d.slope; $('t-tile').value = Math.round(d.tile / 100);
  }
}
$('terrain-btn').onclick = () => { if ($('terrain').hidden) openTerrain().catch(() => {}); else $('terrain').hidden = true; };
$('t-close').onclick = () => { $('terrain').hidden = true; brushRing.visible = false; };
$('t-new').onclick = async () => {
  const at = toGame(pastePoint());
  const r = await run('terrain_new', { pos: at, size: +$('t-size').value, vertices: +$('t-verts').value,
                                       texture: $('t-new-texture').value, steep_texture: $('t-new-steep').value,
                                       surface: $('t-surface').value }, 'terrain patch made');
  await refresh(); await select({ line: r.line }); updateTerrainPanel();
  framePatch(r.line, +$('t-size').value);
  status(`terrain patch ${r.model} made (${$('t-size').value} m) - drag on it with Sculpt on: Raise / Lower / Smooth / Flatten. Ctrl+Z undoes it`);
};
function framePatch(line, sizeM) {     // the whole patch in view, from above at an angle
  const h = byLine.get(line); if (!h) return;
  const c = h.position.clone(), d = Math.max(20, sizeM * 1.1);
  const dir = camera.position.clone().sub(orbit.target).setY(0);
  if (dir.lengthSq() < 1e-6) dir.set(0, 0, 1);
  dir.normalize().multiplyScalar(d * 0.7).setY(d * 0.7);
  orbit.target.copy(c); camera.position.copy(c).add(dir);
  if (topView) placeTopCamera(c, sizeM * 0.75);
  requestRender();
}
$('t-hills').onclick = async () => {
  const h = currentTerrain(); if (!h) return;
  await run('terrain_noise', { model: h.userData.terrain.model, amplitude: +$('t-hill-height').value * 100,
                               scale: +$('t-hill-size').value * 100, seed: Math.floor(Math.random() * 1e6) }, 'hills added');
  const line = h.userData.line; await refresh(); await select({ line });
};
$('t-apply-tex').onclick = async () => {
  const h = currentTerrain(); if (!h) return;
  await run('terrain_set', { model: h.userData.terrain.model, texture: $('t-texture').value, tile: +$('t-tile').value,
                             steep_texture: $('t-steep').value, slope: +$('t-slope').value }, 'textures set');
  const line = h.userData.line; await refresh(); await select({ line });
};
// Fit edges: rays straight down onto the level (world pieces and props, not this patch) under every vertex of the
// outer band; those vertices blend from the ground height at the rim to their own height inside the band.
$('t-fit').onclick = async () => {
  const h = currentTerrain(); if (!h) return;
  const t = h.userData.terrain, d = t.data, n = d.n, half = (n - 1) * d.cell / 2, band = Math.max(1, Math.round((n - 1) * 0.15));
  const heights = [...d.heights], down = new THREE.Vector3(0, -1, 0), caster = new THREE.Raycaster();
  const targets = [groups.world, groups.props];
  let fitted = 0;
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
    const edge = Math.min(i, j, n - 1 - i, n - 1 - j);
    if (edge > band) continue;
    const w = 1 - edge / (band + 1);
    const local = new THREE.Vector3((i * d.cell - half) / 100, 0, -(j * d.cell - half) / 100);
    const world = t.mesh.localToWorld(local.clone());
    caster.set(new THREE.Vector3(world.x, world.y + 200, world.z), down);
    const hit = caster.intersectObjects(targets, true).find((x) => x.object.isMesh && !x.object.userData.terrain && !x.object.userData.ghost);
    if (!hit) continue;
    const ground = t.mesh.worldToLocal(hit.point.clone()).y * 100;
    heights[j * n + i] = heights[j * n + i] * (1 - w) + ground * w;
    fitted++;
  }
  if (!fitted) return status('no ground found under the patch edges', true);
  await run('terrain_set', { model: t.model, heights: heights.map((v) => +v.toFixed(1)) }, `edges fitted to the ground (${fitted} points)`);
  const line = h.userData.line; await refresh(); await select({ line });
};

// AI routes: one instanced mesh of markers, link lines (two-way green, one-way orange).
let routeMesh = null, routesStale = true;
function buildRoutes() {
  clearGroup(groups.routes);
  routesStale = !groups.routes.visible;          // hidden: built when the layer is switched on
  if (routesStale) return;
  const ms = data.routes;
  if (!ms.length) return;
  // Hand-placed markers (flags bit 0 clear) bright; generated ones (one per walkable cell, rebuilt by the game from the
  // scene for missions - read-only here) dim.
  routeMesh = new THREE.InstancedMesh(new THREE.SphereGeometry(0.18, 6, 4), new THREE.MeshBasicMaterial({ color: 0xffffff }), ms.length);
  const mtx = new THREE.Matrix4(), two = [], one = [];
  const hand = new THREE.Color(0xffd23f), made = new THREE.Color(0x3f8f4f);
  ms.forEach((m, i) => {
    routeMesh.setMatrixAt(i, mtx.makeTranslation(toThree(m.pos)));
    routeMesh.setColorAt(i, m.flags & 1 ? made : hand);
    for (const j of m.links) {
      if (j >= ms.length) continue;
      const back = ms[j].links.includes(i);
      if (back && j < i) continue;
      (back ? two : one).push(toThree(m.pos).setY(toThree(m.pos).y + 0.1), toThree(ms[j].pos).setY(toThree(ms[j].pos).y + 0.1));
    }
  });
  routeMesh.userData = { cat: 'routes' };
  pickable.push(routeMesh);
  groups.routes.add(routeMesh);
  groups.routes.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(two), new THREE.LineBasicMaterial({ color: 0x5fd16b })));
  groups.routes.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(one), new THREE.LineBasicMaterial({ color: 0xf0903a })));
}

// ---- outliner ----
// Objects (soldiers, vehicles, items) always; while searching also the rest of the scene. Each object row carries a tag
// telling same-named ones apart: in play at the start, waiting for the script, a cutscene actor, or its script layer.
function objectTag(o) {
  if (o.layer) return ['t-layer', o.layer, `In script layer "${o.layer}"`];
  if (o.cutscene) return ['t-cut', 'cutscene', `A cutscene actor (flag 0x20000) - the films use it, not play; ${o.active ? 'in play from the start' : 'waits for the script'}`];
  if (!o.active) return ['t-wait', 'waits for script', 'Not in play at the start: the mission script activates it'];
  return ['t-start', 'at start', 'In play from the mission start'];
}
const SEARCHABLE = [            // [data list, label, text to search, row text]
  ['props', 'prop', (p) => p.model, (p) => p.model],
  ['waypoints', 'waypoint', (w) => `${w.name} ${w.id}`, (w) => `${w.name} <small>id ${w.id}</small>`],
  ['regions', 'region', (r) => r.name, (r) => r.name],
  ['cameras', 'camera', (c) => `${c.name} ${c.kind}`, (c) => `${c.name} <small>${c.kind}</small>`],
  ['sounds', 'sound', (s) => `${s.name || ''} ${s.sound || ''}`, (s) => `${s.name || s.sound} <small>${s.sound || ''}</small>`],
  ['aipoints', 'AI point', (p) => p.name, (p) => `${p.name} <small>${p.kind}</small>`],
  ['lights', 'light', (l) => `${l.name} ${l.kind}`, (l) => `${l.name} <small>${{ PLGT: 'point', SLGT: 'spot', DLGT: 'sun' }[l.kind] || l.kind}</small>`],
  ['emitters', 'emitter', (e) => e.name || '', (e) => e.name || 'emitter'],
  ['switches', 'switch', (w) => w.name || '', (w) => w.name || 'switch'],
];
let outlinerAnchor = null;      // the last plainly / Ctrl-clicked object row: Shift+click selects from there
function buildOutliner() {
  const q = $('search').value.trim().toLowerCase();
  const ul = $('outliner'); ul.innerHTML = '';
  for (const o of data.objects) {
    const [cls, tag, tip] = objectTag(o);
    const text = `${o.unit} ${o.model} ${o.stat} ${o.script_id} ${tag}`.toLowerCase();
    if (q && !text.includes(q)) continue;
    const li = document.createElement('li');
    li.dataset.line = o.line; li.dataset.obj = '1';
    li.innerHTML = `${o.lineage === 'modified' || o.lineage === 'custom' ? `<span class="lin ${o.lineage}" title="${o.lineage === 'modified' ? 'Modified from the shipped level' : 'Added in the editor'}">●</span>` : ''}${o.is_base_target ? `<span class="shield" title="DLL Critical: ${escHtml(o.base_dll || 'the base mission')} uses script id ${o.script_id} - hide it in a layer rather than delete it">🛡</span>` : ''}${escHtml(o.unit)}<span class="tag ${cls}" title="${escHtml(tip)}">${escHtml(tag)}</span> <small>${escHtml(o.model)} · id ${o.script_id}</small>`;
    li.onclick = (e) => outlinerClick(e, o.line);
    ul.appendChild(li);
  }
  if (q) for (const [key, label, hay, row] of SEARCHABLE) {
    for (const r of data[key] || []) {
      if (!String(hay(r)).toLowerCase().includes(q)) continue;
      const li = document.createElement('li'); li.className = 'kind-row';
      li.dataset.line = r.line;
      li.innerHTML = `<span class="k">${label}</span>${row(r)}`;
      li.onclick = () => select({ line: r.line }, true);
      ul.appendChild(li);
    }
  }
  if (!ul.children.length && q) ul.innerHTML = '<li class="hint">nothing in the scene matches</li>';
  markOutliner();
}
function outlinerClick(e, line) {
  if (e.shiftKey && outlinerAnchor !== null) {          // a range of the listed object rows
    const rows = [...$('outliner').querySelectorAll('li[data-obj]')].map((li) => +li.dataset.line);
    const a = rows.indexOf(outlinerAnchor), b = rows.indexOf(line);
    if (a >= 0 && b >= 0) { selectMany(rows.slice(Math.min(a, b), Math.max(a, b) + 1)); return; }
  }
  outlinerAnchor = line;
  if (e.ctrlKey || e.metaKey) toggleMany(line); else select({ line }, true);
}
$('search').oninput = () => data && buildOutliner();
function markOutliner() {
  for (const li of $('outliner').children)
    li.classList.toggle('sel', (selection && +li.dataset.line === selection.line) || many.has(+li.dataset.line));
}

// ---- library: pick a template, then click in the world to place it ----
let placing = null;             // library entry waiting for its spot
const TABS = [['tab-objects', 'pane-objects'], ['tab-library', 'pane-library'], ['tab-kits', 'pane-kits'], ['tab-prefabs', 'pane-prefabs']];
for (const [tab, pane] of TABS) {
  $(tab).onclick = () => {
    for (const [t, p] of TABS) { $(t).classList.toggle('on', t === tab); $(p).hidden = p !== pane; }
    if (pane === 'pane-library') loadLibrary();
    if (pane === 'pane-kits') loadKits();
    if (pane === 'pane-prefabs') loadPrefabs().catch(() => {});
  };
}

// ---- thumbnails for the Library and Kits lists: each model rendered once (off screen, 96 px, a three-quarter view),
// only for rows scrolled into view, one at a time; kept for the session ----
const thumbs = new Map();
let thumbRenderer = null, thumbBusy = false;
const thumbQueue = [];
const thumbScene = new THREE.Scene();
thumbScene.add(new THREE.HemisphereLight(0xdfe8f0, 0x6b5a40, 1.8));
const thumbSun = new THREE.DirectionalLight(0xfff1d6, 1.4); thumbSun.position.set(2, 3, 1.5); thumbScene.add(thumbSun);
const thumbCam = new THREE.PerspectiveCamera(30, 1, 0.01, 10000);
async function renderThumb(job) {
  if (!thumbRenderer) {
    thumbRenderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    thumbRenderer.setSize(96, 96); thumbRenderer.outputColorSpace = THREE.SRGBColorSpace;
  }
  const url = await job.url();
  const src = url && await loader.loadAsync(url).then((g) => g.scene).catch(() => null);
  if (!src) return null;
  const obj = cloneSkinned(src);
  const box = new THREE.Box3().setFromObject(obj), size = box.getSize(new THREE.Vector3()), centre = box.getCenter(new THREE.Vector3());
  obj.position.sub(centre);
  const r = Math.max(size.x, size.y, size.z) || 1;
  thumbCam.position.set(r * 1.25, r * 0.95, r * 1.25); thumbCam.near = r / 100; thumbCam.far = r * 20;
  thumbCam.lookAt(0, 0, 0); thumbCam.updateProjectionMatrix();
  thumbScene.add(obj);
  thumbRenderer.render(thumbScene, thumbCam);
  thumbScene.remove(obj);
  return thumbRenderer.domElement.toDataURL('image/png');
}
async function pumpThumbs() {
  if (thumbBusy) return;
  thumbBusy = true;
  while (thumbQueue.length) {
    const job = thumbQueue.shift();
    if (!job.img.isConnected) continue;
    if (!thumbs.has(job.key)) thumbs.set(job.key, await renderThumb(job).catch(() => null));
    const data = thumbs.get(job.key);
    if (data) job.img.src = data; else job.img.classList.add('none');
  }
  thumbBusy = false;
}
const thumbWatch = new IntersectionObserver((entries) => {
  for (const e of entries) {
    if (!e.isIntersecting) continue;
    thumbWatch.unobserve(e.target);
    const job = e.target._thumb;
    if (thumbs.has(job.key)) { const d = thumbs.get(job.key); if (d) job.img.src = d; continue; }
    thumbQueue.push(job);
  }
  pumpThumbs();
});
function withThumb(li, key, url) {           // url: async () => model url (or null)
  const img = document.createElement('img'); img.className = 'thumb'; img.alt = '';
  li.prepend(img);
  li._thumb = { key, url, img };
  thumbWatch.observe(li);
}

// ---- kits: pieces of the levels' ground models (cliff faces, wall runs, building shells) placed like props ----
async function loadKits() {
  const lv = $('kit-level');
  if (!lv.options.length) for (const o of $('level').options) lv.add(new Option(o.text, o.value));
  if (!lv.value || lv.dataset.set !== '1') { lv.value = level; lv.dataset.set = '1'; await loadKitModels(); }
}
async function loadKitModels() {
  const models = await run('kit_models', { level: $('kit-level').value });
  const sel = $('kit-model'); sel.innerHTML = '';
  for (const m of models) sel.add(new Option(m, m));
  await loadKitPieces();
}
let kitTimer = null;
async function loadKitPieces() {
  const model = $('kit-model').value, ul = $('kitlist');
  ul.innerHTML = '';
  if (!model) return;
  const items = await run('kit_pieces', { level: $('kit-level').value, model, search: $('kit-search').value.trim() });
  for (const p of items.slice(0, 500)) {
    const li = document.createElement('li');
    li.innerHTML = `<span>#${p.index}${p.count > 1 ? ` <b>×${p.count}</b>` : ''} ${p.textures.slice(0, 2).join(', ')}<br><small>${p.size[0]}×${p.size[2]}×${p.size[1]} m${p.collision ? '' : ' · no collision'}</small></span>`;
    li.title = p.textures.join(', ');
    const entry = { kit: true, level: $('kit-level').value, model, index: p.index, centre: p.centre, label: `${model} #${p.index}`, name: p.name };
    withThumb(li, `kit/${entry.level}/${model}/${p.index}`, async () => {
      const pv = await api('kit_preview', { level: entry.level, model, index: p.index });
      return `/model/${pv.level}/${encodeURIComponent(pv.model)}.glb?v=${MODEL_V}`;
    });
    li.onclick = () => { placing = entry; for (const x of ul.children) x.classList.toggle('sel', x === li); status(`click in the world to place ${entry.label} - or drag it in`); };
    li.draggable = true;
    li.ondragstart = (ev) => { dragEntry = entry; ev.dataTransfer.effectAllowed = 'copy'; ev.dataTransfer.setData('text/plain', entry.label); };
    li.ondragend = dropPreview;
    ul.appendChild(li);
  }
  if (!items.length) ul.innerHTML = '<li class="hint">no pieces match</li>';
}
$('kit-level').onchange = () => loadKitModels().catch(() => {});
$('kit-model').onchange = () => loadKitPieces().catch(() => {});
$('kit-search').oninput = () => { clearTimeout(kitTimer); kitTimer = setTimeout(() => loadKitPieces().catch(() => {}), 300); };
let libTimer = null;
// The library by source: the open game's shipped levels, the converted libraries of the other games (Desert Storm:
// <game>\PSE\Library\<game>) and every mod - a chip per source (click = show / hide), a collapsible section per
// source with its badge and count, and on every item its badge + where it comes from (commands.py library()).
const LIB_PER_SECTION = 300;
const libPref = (k, d) => { try { return JSON.parse(localStorage.getItem('lib.' + k)) ?? d; } catch { return d; } };
const libSave = (k, v) => { try { localStorage.setItem('lib.' + k, JSON.stringify(v)); } catch { /* private window */ } };
const libTag = (e) => e.source_kind === 'mod' ? 'MOD' : e.source_kind === 'converted' ? e.source_label.split(' ')[0] : 'GAME';
function libItem(e, ul) {
  const li = document.createElement('li');
  li.className = (e.source_kind === 'game' && !e.in_level) ? 'away' : '';
  const note = e.source_kind === 'game'
    ? (e.in_level ? '' : " - not in this level's archive (packed into the mod)")
    : e.source_kind === 'mod' ? ' - its files are copied into your mod when you save'
      : ' - the game reads it from the converted library; a soldier type / vehicle definition is copied into your mod';
  li.title = `${e.source_label} · ${e.from}${note}`;
  const icon = document.createElement('span'); icon.textContent = e.kind === 'prop' ? '▣ ' : '● ';
  const badge = document.createElement('b'); badge.className = `lib-badge k-${e.source_kind}`; badge.textContent = libTag(e);
  const name = document.createElement('span'); name.className = 'lib-name'; name.textContent = e.label;
  const from = document.createElement('small'); from.textContent = e.from;
  const text = document.createElement('span'); text.className = 'lib-text';
  text.append(icon, badge, name, from);
  li.append(text);
  withThumb(li, `lib/${e.thumb}/${e.model.toUpperCase()}`, async () => `/model/${encodeURIComponent(e.thumb)}/${encodeURIComponent(e.model.toUpperCase())}.glb?v=${MODEL_V}`);
  li.onclick = () => {
    placing = e;
    for (const x of ul.querySelectorAll('li')) x.classList.toggle('sel', x === li);
    status(`click in the world to place ${e.label} (${e.source_label}) - or drag it in (Esc cancels)`);
  };
  li.draggable = true;
  li.ondragstart = (ev) => {
    dragEntry = e;
    ev.dataTransfer.effectAllowed = 'copy';
    ev.dataTransfer.setData('text/plain', e.label);
    ev.dataTransfer.setDragImage(li, 10, 10);
  };
  li.ondragend = dropPreview;
  return li;
}
async function loadLibrary() {
  if (!level) return;
  loadInteractive().catch(() => {});
  const items = await run('library', { search: $('lib-search').value.trim(), kind: $('lib-kind').value });
  const here = $('lib-here').checked;
  const hidden = new Set(libPref('hidden', []));
  const closed = new Set(libPref('closed', []));
  // sources in order: the game, converted libraries, mods
  const order = { game: 0, converted: 1, mod: 2 };
  const groups = new Map();
  for (const e of items) {
    if (here && e.source_kind === 'game' && !e.in_level) continue;
    if (!groups.has(e.source)) groups.set(e.source, { id: e.source, label: e.source_label, kind: e.source_kind, items: [] });
    groups.get(e.source).items.push(e);
  }
  const list = [...groups.values()].sort((a, b) => (order[a.kind] - order[b.kind]) || a.label.localeCompare(b.label));
  const chips = $('lib-chips'); chips.innerHTML = '';
  for (const g of list) {
    const c = document.createElement('button');
    c.className = `lib-chip k-${g.kind}` + (hidden.has(g.id) ? ' off' : '');
    c.textContent = `${g.label} ${g.items.length}`;
    c.title = hidden.has(g.id) ? 'hidden - click to show' : 'click to hide';
    c.onclick = () => { if (hidden.has(g.id)) hidden.delete(g.id); else hidden.add(g.id); libSave('hidden', [...hidden]); loadLibrary(); };
    chips.append(c);
  }
  const ul = $('library'); ul.innerHTML = '';
  for (const g of list) {
    if (hidden.has(g.id)) continue;
    const head = document.createElement('li');
    head.className = `lib-sec k-${g.kind}` + (closed.has(g.id) ? ' closed' : '');
    head.innerHTML = '<span class="lib-arrow"></span>';
    const t = document.createElement('span'); t.className = 'lib-sec-label';
    t.textContent = g.label + (g.kind === 'converted' ? ' · converted' : '');
    const n = document.createElement('small'); n.textContent = g.items.length;
    head.append(t, n);
    head.onclick = () => { if (closed.has(g.id)) closed.delete(g.id); else closed.add(g.id); libSave('closed', [...closed]); loadLibrary(); };
    ul.append(head);
    if (closed.has(g.id)) continue;
    for (const e of g.items.slice(0, LIB_PER_SECTION)) ul.append(libItem(e, ul));
    if (g.items.length > LIB_PER_SECTION) {
      const more = document.createElement('li'); more.className = 'hint';
      more.textContent = `${g.items.length - LIB_PER_SECTION} more in ${g.label} - search to narrow it down`;
      ul.append(more);
    }
  }
  if (!list.length) ul.innerHTML = '<li class="hint">nothing matches</li>';
}
$('lib-search').oninput = () => { clearTimeout(libTimer); libTimer = setTimeout(loadLibrary, 250); };
// ---- switches & emitters (editor/interactive.py) ----
let interOpts = null, interLevel = null;
async function loadInteractive(force = false) {
  if (!level) return null;
  if (!force && interOpts && interLevel === level) return interOpts;
  interOpts = await api('interactive_options').catch(() => null); interLevel = level;
  if (!interOpts) return null;
  const st = $('sw-type'), keepT = st.value; st.innerHTML = '';
  for (const t of interOpts.switch_types) st.add(new Option(`${t.model_off} / ${t.model_on}`, t.type));
  if (keepT) st.value = keepT;
  const ep = $('em-preset'), keepP = ep.value; ep.innerHTML = '';
  const byType = {};
  for (const p of interOpts.emitter_presets) (byType[p.type_name] ||= []).push(p);
  for (const [tn, list] of Object.entries(byType)) {
    const og = document.createElement('optgroup'); og.label = tn;
    for (const p of list) og.appendChild(new Option(`${p.name} (${p.level.replace('MISSION', 'M')})`, p.id));
    ep.appendChild(og);
  }
  const smoke = interOpts.emitter_presets.find((p) => p.name.toUpperCase() === 'CAMP_FIRE_SMOKE');
  ep.value = keepP || (smoke ? smoke.id : '');
  return interOpts;
}
$('sw-place').onclick = () => { placing = { interactive: 'switch', label: 'switch' }; status('click in the world to place the switch (Esc cancels)'); };
$('em-place').onclick = () => { placing = { interactive: 'emitter', label: 'emitter' }; status('click in the world to place the emitter (Esc cancels)'); };
$('lib-kind').onchange = loadLibrary;
$('lib-here').onchange = loadLibrary;
function cancelPlacing() {
  placing = null;
  for (const x of [...$('library').querySelectorAll('li'), ...$('prefabs').children]) x.classList.remove('sel');
}
async function placeAt(point, entry = placing) {
  const e = entry; cancelPlacing();
  if (e.prefab) return stampPrefab(e, point);
  if (e.interactive === 'switch') {
    const r = await run('switch_add', { pos: toGame(point), type: $('sw-type').value }, 'switch placed');
    await refresh(); await select({ line: r.line });
    status(r.doors.length ? `switch ${r.name} drives door${r.doors.length > 1 ? 's' : ''} ${r.doors.join(' + ')} - change them in the inspector`
      : `switch ${r.name} placed - no door in the scene yet: place an IR_DOOR_… object, then pick it in the inspector`);
    return;
  }
  if (e.interactive === 'emitter') {
    const r = await run('emitter_add', { pos: toGame(point), preset: +$('em-preset').value }, 'emitter placed');
    await refresh(); await select({ line: r.line });
    return;
  }
  if (e.kit) {
    const r = await run('kit_place', { level: e.level, model: e.model, index: e.index, pos: toGame(point) }, `placed ${e.label}`);
    await refresh(); await select({ line: r.line });
    return;
  }
  const jitter = $('jitter').checked;     // variety for repeated props: any facing, size ±10 % (props only)
  const args = { template: e.id, pos: toGame(point), yaw: jitter ? +(Math.random() * 360).toFixed(1) : 0 };
  if (jitter && e.kind === 'prop') args.scale = +(0.9 + Math.random() * 0.2).toFixed(3);
  const r = await run('place', args, `placed ${e.label}`);
  await refresh(); await select({ line: r.line });
}

// ---- drag from the library: a see-through preview follows the surface under the cursor, drop places it ----
let dragEntry = null, ghost = null, ghostFor = null;
const ghostMat = keep(new THREE.MeshBasicMaterial({ color: 0x9fd0ff, transparent: true, opacity: 0.45, depthWrite: false }));
const ghostCone = new THREE.Mesh(coneGeo, ghostMat);
function surfaceAt(clientX, clientY) {
  const r = renderer.domElement.getBoundingClientRect();
  ray.setFromCamera(new THREE.Vector2(((clientX - r.left) / r.width) * 2 - 1, -((clientY - r.top) / r.height) * 2 + 1), camera);
  const hit = ray.intersectObjects([groups.world, groups.props], true).find((h) => h.object.isMesh && !h.object.userData.ghost);
  return hit ? hit.point : null;
}
async function ghostModel(e) {             // the template's model from its own level, see-through
  const key = e.prefab ? `prefab/${e.prefab}` : e.kit ? `kit/${e.name}` : `${e.level}/${e.model.toUpperCase()}`;
  if (ghostFor === key) return ghost;
  ghostFor = key;
  if (ghost) { scene3.remove(ghost); disposeObject(ghost); }   // its model was loaded for it alone (not cached)
  ghost = new THREE.Group(); ghost.add(ghostCone.clone()); scene3.add(ghost); ghost.visible = false;
  if (e.prefab) return ghost;              // a group: the cone marks where its centre goes
  const want = ghost;
  let url = `/model/${e.level}/${encodeURIComponent(e.model.toUpperCase())}.glb?v=${MODEL_V}`;
  if (e.kit) { const pv = await api('kit_preview', { level: e.level, model: e.model, index: e.index }); url = `/model/${pv.level}/${encodeURIComponent(pv.model)}.glb?v=${MODEL_V}`; }
  const src = await loader.loadAsync(url).then((g) => g.scene).catch(() => null);
  if (src && want === ghost) {
    const m = cloneSkinned(src);
    if (e.kit) m.position.copy(toThree(e.centre)).multiplyScalar(-1);   // world-space piece: centre on the cursor
    m.traverse((o) => { if (o.isMesh) { o.material = ghostMat; o.userData.ghost = true; } });
    ghost.clear(); ghost.add(m); requestRender();
  }
  return ghost;
}
function dropPreview() {          // drag ended (dropped or cancelled): hide the preview
  dragEntry = null;
  if (ghost) ghost.visible = false;
}
renderer.domElement.addEventListener('dragover', async (ev) => {
  if (!dragEntry) return;
  ev.preventDefault(); ev.dataTransfer.dropEffect = 'copy';
  await ghostModel(dragEntry);
  const p = surfaceAt(ev.clientX, ev.clientY);
  if (p) { ghost.position.copy(p); ghost.visible = true; } else ghost.visible = false;
});
renderer.domElement.addEventListener('dragleave', () => { if (ghost) ghost.visible = false; });
renderer.domElement.addEventListener('drop', async (ev) => {
  if (!dragEntry) return;
  ev.preventDefault();
  const e = dragEntry, p = surfaceAt(ev.clientX, ev.clientY);
  dragEntry = null; if (ghost) ghost.visible = false;
  if (!p) return status('no surface there - drop it on the ground or a building', true);
  await placeAt(p, e);
});

// ---- selection + properties ----
let selection = null;          // {line} or {route: index}
const many = new Set();        // objects selected together (Shift+click): the Layer box applies to all of them
const selMark = keep(new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.9, side: THREE.DoubleSide }));
const multiBoxes = new THREE.Group(); scene3.add(multiBoxes);
function markSelected() {      // a white ring under every object of a multi-selection, a white box round every prop
  requestRender();
  for (const h of groups.objects.children) {
    let ring = h.getObjectByName('multi');
    const on = many.has(h.userData.line);
    if (on && !ring) { ring = new THREE.Mesh(new THREE.RingGeometry(0.65, 0.8, 24).rotateX(-Math.PI / 2).translate(0, 0.05, 0), selMark); ring.name = 'multi'; h.add(ring); }
    if (ring) ring.visible = on;
  }
  clearGroup(multiBoxes);
  for (const line of many) {
    const h = data?.props.some((p) => p.line === line) && byLine.get(line);
    if (h) multiBoxes.add(new THREE.BoxHelper(h, 0xffffff));
  }
}
const multiOk = (line) => data.objects.some((o) => o.line === line) || data.props.some((p) => p.line === line && !p.ground);
function showMany() {
  gizmo.detach();
  selection = null;
  const allObjects = [...many].every((l) => data.objects.some((o) => o.line === l));
  $('sel-title').textContent = `${many.size} ${allObjects ? 'objects' : 'records'} selected`;
  $('fields').innerHTML = ''; $('fields-adv').innerHTML = ''; $('adv-box').hidden = true;
  showSharedFields([...many]).catch((e) => console.warn('shared fields:', e));
  $('layer-row').hidden = !allObjects;
  stopSound();
  const layers = new Set([...many].map((l) => data.objects.find((o) => o.line === l)?.layer || ''));
  $('layer-input').value = layers.size === 1 ? [...layers][0] : '';
  $('layer-input').placeholder = layers.size === 1 ? '(in play from the start)' : '(different layers)';
  for (const id of INSPECTOR_BOXES) $(id).hidden = true;
  const soldiers = [...many].map((l) => data.objects.find((o) => o.line === l)).filter((o) => o?.ai_profile);
  if (soldiers.length === many.size) {            // all soldiers: one AI profile for all of them
    const kinds = new Set(soldiers.map((o) => o.ai_profile));
    (aiProfiles ? Promise.resolve(aiProfiles) : api('ai_profiles')).then((p) => {
      aiProfiles = p; fillProfiles(kinds.size === 1 ? [...kinds][0] : '');
      $('patrol-info').textContent = 'Patrols: select one soldier.'; $('patrol-btn').disabled = $('patrol-clear').disabled = true;
      $('ai-row').hidden = false;
    }).catch(() => {});
  }
  markOutliner(); markSelected();
}
// Several records of one kind selected: the fields they all have, a value where they agree; a change goes to all of
// them as one undo step (batch). Script ids, lines and counted lists stay per record.
const SHARED_SKIP = /^(line|kind|script_id|id|name)$|_count$|^(kit|link|group|lod|ai_list)\d/;
async function showSharedFields(lines) {
  const recs = await Promise.all(lines.slice(0, 80).map((line) => api('get_record', { line })));
  if (selection || many.size !== lines.length || lines.some((l) => !many.has(l))) return;    // the selection moved on
  const kinds = new Set(recs.map((r) => r.kind));
  if (kinds.size !== 1 || lines.length > 80) {
    $('fields').innerHTML = `<tr><td colspan="2" class="ro">${kinds.size !== 1 ? 'Different kinds of record: select records of one kind to edit a field on all of them.' : 'More than 80 selected - shared fields are edited for up to 80.'}</td></tr>`;
    return;
  }
  const keys = Object.keys(recs[0].fields).filter((k) => !SHARED_SKIP.test(k) && recs.every((r) => k in r.fields));
  const t = $('fields'); t.innerHTML = `<tr><td colspan="2" class="ro">Fields all ${recs.length} have - a change applies to every one (one undo step).</td></tr>`;
  for (const k of keys) {
    const vals = recs.map((r) => r.fields[k]), first = vals[0];
    const same = vals.every((v) => JSON.stringify(v) === JSON.stringify(first));
    const tr = document.createElement('tr'); tr.innerHTML = `<td>${escHtml(k)}</td><td></td>`;
    const inp = document.createElement('input');
    inp.value = same ? (Array.isArray(first) ? first.join(', ') : String(first)) : '';
    inp.placeholder = same ? '' : '(different)';
    inp.oninput = () => inp.classList.add('changed');
    inp.onkeydown = (e) => { if (e.key === 'Enter') inp.blur(); };
    inp.onchange = async () => {
      if (!inp.value.trim()) return;
      let value = inp.value;
      if (Array.isArray(first)) value = value.split(',').map(Number);
      else if (typeof first === 'number') value = Number(value);
      const keep = [...lines];
      try {
        await run('batch', { steps: lines.map((line) => ({ command: 'set_field', args: { line, field: k, value } })) },
                  `${k} set on ${lines.length} records`);
        await refresh(); selectMany(keep);
      } catch { inp.classList.remove('changed'); }
    };
    tr.children[1].appendChild(inp); t.appendChild(tr);
  }
}
async function toggleMany(line) {
  if (!multiOk(line)) return select({ line });
  if (selection?.line !== undefined && !selection.target && multiOk(selection.line)) many.add(selection.line);
  if (many.has(line)) many.delete(line); else many.add(line);
  if (many.size === 1) { const only = [...many][0]; many.clear(); return select({ line: only }); }
  if (!many.size) return select(null);
  showMany();
}
function selectMany(lines) {
  many.clear(); for (const l of lines) many.add(l);
  if (many.size === 1) { const only = lines[0]; many.clear(); return select({ line: only }, true); }
  showMany();
}
const proxy = new THREE.Object3D();
scene3.add(proxy);
gizmo.addEventListener('objectChange', () => {     // the selected visual follows the gizmo while dragging
  if (selection?.target) { const m = camTargets.get(selection.line); if (m) { m.position.copy(proxy.position); m.userData.update?.(); } return; }
  const obj = selection?.line !== undefined ? byLine.get(selection.line) : null;
  if (!obj) return;
  obj.position.copy(proxy.position);
  if (kitOffset) {     // kit piece / zone chunk: the gizmo sits on its centre, the record at the level origin
    const off = kitOffset.clone();
    if (gizmo.mode === 'rotate' && kitQuat0) off.applyQuaternion(proxy.quaternion.clone().multiply(kitQuat0.clone().invert()));
    obj.position.sub(off);
  }
  if (gizmo.mode === 'rotate') obj.rotation.copy(proxy.rotation);
  chunkBox?.update();
});
let kitOffset = null, kitQuat0 = null;
// Alt held: the gizmo turns the selection; Alt+X / Alt+Y / Alt+Z pick the axis - Y = facing (yaw, the default),
// X = tilt forward / back (pitch), Z = lean sideways (roll), in the object's own frame. Released: back to moving.
// The game's rot = (pitch, yaw, roll) in degrees = three.js Euler (-x, -y, z) in 'YXZ' order. A mode change waits
// until a drag ends; the chosen axis is kept for the next Alt.
proxy.rotation.order = 'YXZ';
let selRot = null, wantRotate = false, rotAxis = 'Y';
const AXIS_NAME = { X: 'tilt (pitch)', Y: 'facing (yaw)', Z: 'lean (roll)' };
function gizmoMode() {
  if (gizmo.dragging) return;
  const rotate = wantRotate && selRot !== null;
  gizmo.setMode(rotate ? 'rotate' : 'translate');
  gizmo.setSpace(rotate ? 'local' : 'world');
  gizmo.showX = !rotate || rotAxis === 'X';
  gizmo.showY = !rotate || rotAxis === 'Y';
  gizmo.showZ = !rotate || rotAxis === 'Z';
  if (rotate) status(`rotate: ${AXIS_NAME[rotAxis]} - Alt+X / Alt+Y / Alt+Z switch the axis`);
}
window.addEventListener('keydown', (e) => {
  if (e.key === 'Alt') { e.preventDefault(); wantRotate = true; gizmoMode(); return; }
  const k = e.key.toUpperCase();
  if (e.altKey && (k === 'X' || k === 'Y' || k === 'Z')) { e.preventDefault(); rotAxis = k; wantRotate = true; gizmoMode(); }
});
window.addEventListener('keyup', (e) => { if (e.key === 'Alt') { e.preventDefault(); wantRotate = false; gizmoMode(); } });
window.addEventListener('blur', () => { wantRotate = false; gizmoMode(); });

let chunkBox = null;            // outline of a selected zone chunk (it sits flush with the ground round it)
function outlineChunk(h) {
  if (chunkBox) { scene3.remove(chunkBox); chunkBox.geometry.dispose(); chunkBox = null; }
  if (h) { chunkBox = new THREE.BoxHelper(h, 0xffd040); chunkBox.renderOrder = 12; chunkBox.material.depthTest = false; scene3.add(chunkBox); }
}
async function select(sel, focus = false) {
  selection = sel; requestRender();
  outlineChunk(null);
  many.clear(); markSelected();
  $('layer-input').placeholder = '(in play from the start)';
  gizmo.detach();
  for (const id of INSPECTOR_BOXES) $(id).hidden = true;
  stopSound();
  clearVanillaGhost();
  $('fields-adv').innerHTML = ''; $('adv-box').hidden = true;
  if (!sel) { $('sel-title').textContent = 'Nothing selected'; $('fields').innerHTML = ''; $('layer-row').hidden = true; markOutliner(); return; }
  $('layer-row').hidden = true;
  if (sel.route !== undefined) {
    selRot = null;   // markers have no rotation
    const m = data.routes[sel.route];
    proxy.position.copy(toThree(m.pos)); gizmo.attach(proxy);
    $('sel-title').textContent = `Route marker ${sel.route}`;
    showFields({ index: sel.route, pos: m.pos, links: m.links, flags: m.flags }, true, 'route');
  } else if (sel.target) {             // a target camera's look-at point (CAMTARGPOS)
    selRot = null; kitOffset = null;
    const cam = data.cameras.find((c) => c.line === sel.line);
    proxy.position.copy(toThree(cam.target)); proxy.rotation.set(0, 0, 0, 'YXZ'); gizmo.attach(proxy);
    $('sel-title').textContent = `${cam.kind} · line ${sel.line} · look-at point`;
    showFields({ line: sel.line, target: cam.target }, true);
    showInspector({ kind: cam.kind, line: sel.line });
  } else {
    const obj = byLine.get(sel.line);
    const rec = await run('get_record', { line: sel.line });
    selRot = Array.isArray(rec.fields.rot) ? rec.fields.rot : null;
    if (obj) {
      const piv = (data.props.find((p) => p.line === sel.line) || data.chunks?.find((p) => p.line === sel.line))?.pivot;
      proxy.position.copy(piv ? toThree(piv) : obj.position);
      kitOffset = piv ? toThree(piv).sub(obj.position) : null;
      if (selRot) proxy.rotation.set(-deg(selRot[0]), -deg(selRot[1]), deg(selRot[2]), 'YXZ');
      else proxy.rotation.set(0, 0, 0, 'YXZ');
      kitQuat0 = proxy.quaternion.clone();
      gizmo.attach(proxy);
    }
    $('sel-title').textContent = `${rec.kind} · line ${rec.line}`;
    const chunk = data.chunks?.find((c) => c.line === rec.line);
    if (chunk) {
      $('sel-title').textContent = `World piece ${chunk.model} · ${chunk.zone} #${chunk.index}${chunk.root ? ' (zone root)' : ''} · line ${rec.line}`;
      outlineChunk(obj);
      if (chunk.root) { gizmo.detach(); status(`${chunk.zone}: the zone's root record - its other chunks hang from it, so it stays put`); }
    }
    const crit = rec.kind === 'HLOI' ? data.objects.find((o) => o.line === rec.line) : null;
    if (crit?.is_base_target) {
      const b = document.createElement('span'); b.className = 'badge critical';
      b.textContent = '🛡 DLL Critical';
      b.title = `${crit.base_dll || 'The base mission'} uses script id ${crit.script_id} - hide it in a layer instead of deleting it`;
      $('sel-title').append(' ', b);
    }
    const item = sceneItem(rec.line);
    if (item?.lineage) {
      const b = document.createElement('span'); b.className = 'badge lin-' + item.lineage;
      b.textContent = { vanilla: 'Vanilla', modified: 'Vanilla · Modified', custom: 'Custom' }[item.lineage];
      b.title = { vanilla: 'As the game ships it', modified: 'A shipped record you changed - see Vanilla lineage below',
                  custom: 'Added in the editor (or renamed / renumbered): not in the shipped level' }[item.lineage];
      $('sel-title').append(' ', b);
    }
    const isObject = rec.kind === 'HLOI';
    $('layer-row').hidden = !isObject;
    if (isObject) $('layer-input').value = data.objects.find((o) => o.line === rec.line)?.layer || '';
    showFields(rec.fields, false, rec.kind);
    showInspector(rec);
    showVanilla(rec, item).catch((e) => console.warn('vanilla lineage:', e));
  }
  applyMoveSnap();
  if (focus) frameSelection(sel);
  markOutliner();
  if (!$('terrain').hidden) updateTerrainPanel();
  if (!$('road').hidden) updateRoadPanel();
}

$('layer-input').onchange = async () => {
  const lines = many.size ? [...many] : selection?.line ? [selection.line] : [];
  if (!lines.length) return;
  const layer = $('layer-input').value.trim();
  const keep = new Set(lines);
  await run('set_layer', { lines, layer }, layer ? `${lines.length} object(s) in layer "${layer}"` : `${lines.length} object(s) in play from the start`);
  await refresh();
  if (keep.size > 1) selectMany([...keep]);
};
$('layer-input').onkeydown = (e) => { if (e.key === 'Enter') $('layer-input').blur(); };

// The field list: the record's named values first; flags, unnamed (u_*), AI tuning, vectors and links in the collapsed
// "Advanced (raw)" section, flags with a checkbox per known bit. A soldier's kit has its own editor (Kit section).
const RAW_FIELD = /^(flags$|u_[a-z]+$|u\d+$|ai_|v\d$|link|group|lod|shard_|number$|p\d+$)/;
const KIT_FIELD = /^kit(_count$|\d+\.)/;
// known bits per record kind: [bit, label, editable] - layout bits (they decide which values follow) are read-only
const FLAG_BITS = {
  HLOI: [[0x20000, 'cutscene actor', true]],
  ENVI: [[0x400000, 'breakable (shatters)', false]],
  EMITTER: [[0x1, 'on at the start', true], [0x2, 'limited duration', true], [0x8, 'tint', true], [0x10, 'grows', true],
            [0x20, 'growth from life', true], [0x40, 'tint varies', true], [0x200, 'world-space velocity', true]],
  NEWENVMARKER: [[0x80, 'named', false]],
  route: [[0x1, 'generated from the walk map', false]],
};
function showFields(fields, readOnly, kind = '') {
  const t = $('fields'), adv = $('fields-adv'); t.innerHTML = ''; adv.innerHTML = '';
  const o = kind === 'HLOI' ? data.objects.find((x) => x.line === fields.line) : null;
  const kitEditor = !readOnly && o && !/_VEH_/i.test(o.unit) && 'kit_count' in fields;
  let nAdv = 0;
  for (const [k, v] of Object.entries(fields)) {
    if (kitEditor && KIT_FIELD.test(k)) continue;
    const raw = RAW_FIELD.test(k) || KIT_FIELD.test(k);
    const tr = document.createElement('tr');
    const editable = !readOnly && !['line', 'kind'].includes(k);
    tr.innerHTML = `<td>${escHtml(k)}</td><td></td>`;
    const cell = tr.children[1];
    const shown = Array.isArray(v) ? v.join(', ') : String(v);
    const commit = async (value) => {
      try { await run('set_field', { line: fields.line, field: k, value }, `${k} set`); await refresh(); return true; }
      catch { return false; }
    };
    if (editable) {
      const inp = document.createElement('input'); inp.value = shown;
      inp.oninput = () => inp.classList.add('changed');
      inp.onkeydown = (e) => { if (e.key === 'Enter') inp.blur(); };
      inp.onchange = async () => {
        let value = inp.value;
        if (Array.isArray(v)) value = value.split(',').map(Number);
        else if (typeof v === 'number') value = Number(value);
        if (!await commit(value)) { inp.value = shown; inp.classList.remove('changed'); }
      };
      cell.appendChild(inp);
    } else { cell.className = 'ro'; cell.textContent = shown; }
    if (k === 'flags' && typeof v === 'number' && FLAG_BITS[kind]) {
      const bits = document.createElement('div'); bits.className = 'flag-bits';
      for (const [bit, label, canEdit] of FLAG_BITS[kind]) {
        const l = document.createElement('label');
        l.title = `bit 0x${bit.toString(16).toUpperCase()}${canEdit ? '' : ' - fixed: it decides which values follow in the record'}`;
        const cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = !!(v & bit);
        cb.disabled = readOnly || !canEdit;
        cb.onchange = () => commit(cb.checked ? v | bit : v & ~bit).then((ok) => { if (!ok) cb.checked = !cb.checked; });
        l.append(cb, ` ${label}`); bits.appendChild(l);
      }
      cell.appendChild(bits);
    }
    (raw ? adv : t).appendChild(tr);
    if (raw) nAdv++;
  }
  $('adv-box').hidden = !nAdv;
  $('adv-box').querySelector('summary').textContent = `Advanced (raw) · ${nAdv} values`;
  if (kitEditor) showKit(fields).catch((e) => console.warn('kit:', e));
}

// ---- the Kit section: one row per item (weapon / inventory item, amount, remove) and "+ Add item" ----
let kitOpts = null;
async function showKit(fields) {
  kitOpts ||= await api('kit_options');
  if (selection?.line !== fields.line) return;
  const known = new Map([...kitOpts.weapons, ...kitOpts.items].map((x) => [x.id, x.category]));
  if (!$('kit-items').children.length) {
    $('kit-items').innerHTML = [...kitOpts.weapons.map((w) => `<option value="${escHtml(w.id)}">weapon</option>`),
      ...kitOpts.items.map((i) => `<option value="${escHtml(i.id)}">item</option>`)].join('');
  }
  const items = [];
  for (let k = 0; k < fields.kit_count; k++)
    items.push({ item: fields[`kit${k}.item`], category: fields[`kit${k}.category`], amount: fields[`kit${k}.amount`] });
  const box = $('kit-list'); box.innerHTML = '';
  const save = async (list, what) => {
    try {
      await run('set_kit', { line: fields.line, items: list }, what);
      const line = fields.line; await refresh(); if (selection?.line !== line) await select({ line });
    } catch { /* the status line says why; the rows show the kit as it is */ }
  };
  const catOf = (id, fallback) => (known.get(id) ?? fallback ?? null);
  const row = (it, i) => {
    const div = document.createElement('div');
    const needCat = catOf(it.item, it.category) === null;
    div.className = 'kit' + (needCat ? ' cat' : '');
    const name = document.createElement('input'); name.setAttribute('list', 'kit-items'); name.value = it.item;
    name.placeholder = 'weapon or item'; name.title = `${it.item} - a Weaps.txt weapon or an Inven.txt item (type or pick)`;
    const amt = document.createElement('input'); amt.type = 'number'; amt.min = 0; amt.value = it.amount;
    amt.title = 'Amount: rounds for a gun, count for grenades and items';
    const cat = document.createElement('input'); cat.type = 'number'; cat.min = 0; cat.value = it.category ?? '';
    cat.title = 'Kit category: this item is in no shipped kit, so give its number (weapons 5, keys 20, goggles 17)';
    const del = document.createElement('button'); del.type = 'button'; del.textContent = '×'; del.title = 'Remove from the kit';
    const send = () => {
      const id = name.value.trim(); if (!id) return;
      save(items.map((x, j) => (j !== i ? x
        : { item: id, amount: +amt.value, category: needCat ? (cat.value === '' ? null : +cat.value) : catOf(id, id === x.item ? x.category : null) })),
        `kit: ${id} × ${amt.value}`);
    };
    name.onchange = send; amt.onchange = send; cat.onchange = send;
    del.onclick = () => save(items.filter((_, j) => j !== i), `kit: ${it.item} removed`);
    div.append(name, ...(needCat ? [cat] : []), amt, del);
    return div;
  };
  items.forEach((it, i) => box.appendChild(row(it, i)));
  if (!items.length) box.innerHTML = '<p class="hint">Empty kit.</p>';
  $('kit-add').onclick = () => {                 // a new row; it is saved once an item is typed or picked
    if (box.querySelector('.kit.new')) return box.querySelector('.kit.new input').focus();
    if (!items.length) box.innerHTML = '';
    const div = document.createElement('div'); div.className = 'kit new';
    const name = document.createElement('input'); name.setAttribute('list', 'kit-items'); name.placeholder = 'weapon or item';
    const amt = document.createElement('input'); amt.type = 'number'; amt.min = 0; amt.value = 1;
    const del = document.createElement('button'); del.type = 'button'; del.textContent = '×'; del.title = 'Cancel';
    name.onchange = () => {
      const id = name.value.trim(); if (!id) return;
      if (kitOpts.weapons.some((w) => w.id === id) && +amt.value === 1) amt.value = 30;
      save([...items, { item: id, amount: +amt.value, category: catOf(id) }], `kit: ${id} added`);
    };
    del.onclick = () => { div.remove(); if (!items.length) box.innerHTML = '<p class="hint">Empty kit.</p>'; };
    div.append(name, amt, del); box.appendChild(div); name.focus();
  };
  $('kit-row').hidden = false;
}

function focusOn(p, dist = 6) {
  const off = camera.position.clone().sub(orbit.target).normalize().multiplyScalar(dist);
  orbit.target.copy(p); camera.position.copy(p).add(off);
  if (topView) placeTopCamera(p);
  requestRender();
}
// Frame a selection: far enough back to see all of it, from the current direction if nothing is in the way - else
// the first clear direction around it (turning, then from higher up); with none clear, in front of what blocks it.
const blockRay = new THREE.Raycaster();
function frameSelection(sel) {
  const obj = sel?.line !== undefined && !sel.target ? byLine.get(sel.line) : null;
  if (!obj) return focusOn(proxy.position);
  const box = new THREE.Box3().setFromObject(obj);
  const centre = box.isEmpty() ? obj.getWorldPosition(new THREE.Vector3()) : box.getCenter(new THREE.Vector3());
  const size = box.isEmpty() ? 1 : box.getSize(new THREE.Vector3()).length();
  const dist = THREE.MathUtils.clamp(size * 1.3, 4, 600);
  if (topView) return focusOn(centre);
  const self = new Set(); obj.traverse((c) => self.add(c));
  const solids = [groups.world, groups.props, groups.objects].filter((g) => g.visible);
  const clearFor = (dir) => {                    // how far the camera can go back along dir before a surface
    blockRay.set(centre, dir); blockRay.far = dist;
    const hit = blockRay.intersectObjects(solids, true).find((h) => h.object.isMesh && !self.has(h.object)
      && !h.object.userData.ghost && shownInView(h.object));
    return hit ? hit.distance : Infinity;
  };
  const cur = camera.position.clone().sub(orbit.target).normalize();
  const yaw0 = Math.hypot(cur.x, cur.z) > 1e-3 ? Math.atan2(cur.x, cur.z) : 0;
  let best = cur, bestFree = -1;
  search: for (const pitch of [Math.asin(THREE.MathUtils.clamp(cur.y, 0.15, 0.9)), 0.6, 1.0, 1.4]) {
    for (let k = 0; k < 12; k++) {
      const yaw = yaw0 + (k % 2 ? -1 : 1) * Math.ceil(k / 2) * (Math.PI / 6);
      const dir = new THREE.Vector3(Math.sin(yaw) * Math.cos(pitch), Math.sin(pitch), Math.cos(yaw) * Math.cos(pitch));
      const free = clearFor(dir);
      if (free > bestFree) { best = dir; bestFree = free; }
      if (free === Infinity) break search;
    }
  }
  orbit.target.copy(centre);
  camera.position.copy(centre).addScaledVector(best, bestFree === Infinity ? dist : Math.max(0.6, bestFree - 0.4));
  requestRender();
}
// drawn in the view: the object and all its parents visible (layer filters, isolation)
function shownInView(o) { for (let x = o; x; x = x.parent) if (!x.visible) return false; return true; }

// Snapping: turns through the gizmo (live), moves when the drag ends - the record's x / z on the grid, a kit piece's
// corner (centre - half its size, turned 90-degree sizes swapped) so 8 m tiles meet edge to edge.
function snapped(pos) {
  const g = +$('snap-move').value;
  if (!g) return pos;
  const p = data.props.find((x) => x.line === selection?.line);
  let hx = 0, hz = 0;
  if (p?.kit?.size) {
    const turned = Math.round(((p.rot?.[1] || 0) % 180 + 180) % 180 / 90) === 1;
    [hx, hz] = turned ? [p.kit.size[2] / 2, p.kit.size[0] / 2] : [p.kit.size[0] / 2, p.kit.size[2] / 2];
  }
  return [Math.round((pos[0] - hx) / g) * g + hx, pos[1], Math.round((pos[2] - hz) / g) * g + hz];
}
function applyTurnSnap() {
  const t = +$('snap-turn').value;
  gizmo.setRotationSnap(t ? THREE.MathUtils.degToRad(t) : null);
}
function applyMoveSnap() {          // live grid while dragging; kit pieces and route markers snap on release / not at all
  const g = +$('snap-move').value;
  const kit = data?.props?.find((p) => p.line === selection?.line)?.kit;
  gizmo.setTranslationSnap(g && !kit && selection && selection.route === undefined ? g / 100 : null);
}
$('snap-turn').onchange = applyTurnSnap;
$('snap-move').onchange = applyMoveSnap;
applyTurnSnap();
try {                                        // remembered per browser (a convenience, not part of the mission)
  $('snap-move').value = localStorage.getItem('snap-move') || '0';
  $('snap-turn').value = localStorage.getItem('snap-turn') || '0'; applyTurnSnap();
  $('jitter').checked = localStorage.getItem('jitter') === '1';
} catch { /* storage blocked */ }
for (const id of ['snap-move', 'snap-turn']) $(id).addEventListener('change', () => { try { localStorage.setItem(id, $(id).value); } catch { /* ignore */ } });
$('jitter').addEventListener('change', () => { try { localStorage.setItem('jitter', $('jitter').checked ? '1' : '0'); } catch { /* ignore */ } });

async function commitMove() {
  if (!selection) return;
  if (selection.target) {               // a target camera's look-at point
    await run('set_camera', { line: selection.line, target: toGame(proxy.position) }, `camera ${selection.line}: target moved`);
    await refresh();
    return;
  }
  if (gizmo.mode === 'rotate' && selection.line !== undefined && selRot) {
    const r = proxy.rotation;   // order 'YXZ'; TransformControls turns the quaternion and the Euler follows it
    const norm = (d) => +((((d % 360) + 360) % 360).toFixed(2));
    const rot = [norm(-THREE.MathUtils.radToDeg(r.x)), norm(-THREE.MathUtils.radToDeg(r.y)), norm(THREE.MathUtils.radToDeg(r.z))];
    await run('set_field', { line: selection.line, field: 'rot', value: rot }, `line ${selection.line} rotated to ${rot.join(', ')}°`);
    await refresh();
    return;
  }
  const pos = selection.route !== undefined ? toGame(proxy.position) : snapped(toGame(proxy.position));
  if (selection.route !== undefined) await run('route_move', { index: selection.route, pos }, `route marker ${selection.route} moved`);
  else await run('move', { line: selection.line, pos }, `line ${selection.line} moved`);
  await refresh();
}

// After a change: rebuild the overlays (line numbers may shift) and keep the selection when it still exists.
async function refresh() {
  const keep = selection;
  const hist = updateHistory();              // Undo / the unsaved mark at once - the scene rebuild can take a while
  await buildScene(false);
  await hist;
  if (keep && (keep.route !== undefined ? keep.route < data.routes.length : byLine.has(keep.line))) await select(keep);
  else await select(null);
}
// Changes made from outside the page (the MCP server, another tab): the session's revision moves -> redraw; a different
// level or mission -> reload it.
let knownRev = null, viewKey = null, polling = false;
setInterval(async () => {
  if (polling || document.hidden || gizmo.dragging) return;
  polling = true;
  try {
    const h = await api('history');
    dirty = !!h.dirty;
    if (knownRev !== null && h.revision !== knownRev) {
      const key = `${h.level}|${h.mission ? h.mission.join('/') : ''}`;
      if (key !== viewKey && h.level) {
        level = h.level; clearModelCache(); clearGroup(groups.world); hiddenLayers.clear();
        if ($('level').value !== h.level) $('level').value = h.level;
        await buildScene(true); await select(null);
      } else await refresh();
      status('updated from outside the editor (MCP)');
    }
    knownRev = h.revision; viewKey = `${h.level}|${h.mission ? h.mission.join('/') : ''}`;
  } catch { /* the server is restarting */ } finally { polling = false; }
}, 1500);

// The MCP link to this window (workflow.py): screenshot / focus requests from an assistant, polled here and answered
// with ui_reply. Runs while the tab is hidden too (browsers slow the timer to once a second there).
let uiPolling = false;
setInterval(async () => {
  if (uiPolling) return;
  uiPolling = true;
  try {
    for (const q of await api('ui_poll')) {
      try {
        const result = q.type === 'screenshot' ? await uiScreenshot(q.args) : q.type === 'focus' ? await uiFocus(q.args) : null;
        if (q.type === 'screenshot') await api('ui_reply', { id: q.id, result });
      } catch (e) {
        if (q.type === 'screenshot') await api('ui_reply', { id: q.id, error: String(e.message || e) }).catch(() => {});
      }
    }
  } catch { /* the server is restarting */ } finally { uiPolling = false; }
}, 500);

async function uiFocus(a) {
  if (a.route !== null && a.route !== undefined) await select({ route: a.route }, true);
  else await select({ line: a.line }, true);
  status(a.route != null ? `route marker ${a.route} shown by the assistant` : `line ${a.line} shown by the assistant`);
  requestRender();
  return { ok: true };
}

// One picture of the 3D view, aimed as asked (game units, yaw = compass direction looked along: 0 = +z), drawn and
// read back in the same task (no preserveDrawingBuffer needed), scaled to the asked width; the view goes back after.
async function uiScreenshot(a) {
  const saved = { pos: camera.position.clone(), target: orbit.target.clone() };
  let target = null, dist = a.distance ? a.distance / 100 : null;
  if (a.line !== null && a.line !== undefined) {
    const o = byLine.get(a.line);
    if (!o) throw new Error(`line ${a.line} isn't drawn in the view (no position, or its layer is hidden)`);
    const box = new THREE.Box3().setFromObject(o);
    target = box.isEmpty() ? o.getWorldPosition(new THREE.Vector3()) : box.getCenter(new THREE.Vector3());
    if (!dist) dist = Math.max(4, box.isEmpty() ? 6 : box.getSize(new THREE.Vector3()).length() * 1.4);
  } else if (a.route !== null && a.route !== undefined) {
    const m = data.routes[a.route];
    if (!m) throw new Error(`no route marker ${a.route}`);
    target = toThree(m.pos);
  } else if (a.target) target = toThree(a.target);
  if (a.position) {
    camera.position.copy(toThree(a.position));
    orbit.target.copy(target || toThree(a.position).add(new THREE.Vector3(0, 0, -1)));
  } else if (target || a.top || a.yaw != null || a.pitch != null) {
    target = target || orbit.target.clone();
    dist = dist || (a.top ? 60 : 30);
    if (a.top) camera.position.copy(target).add(new THREE.Vector3(0, dist, 0.001));
    else {
      const yaw = deg(a.yaw ?? 45), pitch = deg(a.pitch ?? 35);
      // game forward (sin yaw, cos yaw) in x / z; three's z = -game z; the camera sits behind and above the target
      camera.position.copy(target).add(new THREE.Vector3(-Math.sin(yaw) * Math.cos(pitch), Math.sin(pitch),
                                                          Math.cos(yaw) * Math.cos(pitch)).multiplyScalar(dist));
    }
    orbit.target.copy(target);
  }
  orbit.update(); camera.updateMatrixWorld();
  const gizmoShown = gizmo.visible;
  gizmo.visible = false;
  let url, w, h;
  try {
    renderFrame();
    const src = renderer.domElement;
    w = Math.min(a.width || 1024, src.width); h = Math.round(src.height * w / src.width);
    const c = document.createElement('canvas'); c.width = w; c.height = h;
    c.getContext('2d').drawImage(src, 0, 0, w, h);
    url = c.toDataURL(a.format === 'png' ? 'image/png' : 'image/jpeg', 0.85);
  } finally {
    gizmo.visible = gizmoShown;
    const cam = { position: toGame(camera.position), target: toGame(orbit.target) };
    if (!a.keep_camera) { camera.position.copy(saved.pos); orbit.target.copy(saved.target); orbit.update(); }
    requestRender();
    uiScreenshot.last = cam;
  }
  return { image: url.split(',')[1], mime: a.format === 'png' ? 'image/png' : 'image/jpeg', size: [w, h],
           camera: uiScreenshot.last };
}

// Unsaved changes: the session's 'dirty' (updateHistory, the poll). Opening another level / mission or leaving the page
// asks first.
let dirty = false;
async function confirmDiscard(what) {
  try { dirty = !!(await api('history')).dirty; } catch { /* the last known state */ }
  return !dirty || confirm(`The current level has unsaved changes (Save as mod / Save as mission keeps them).\n\n${what} anyway and lose them?`);
}
window.addEventListener('beforeunload', (e) => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
// desktop app (editor/app.py): back to its game picker
function appReady() { for (const el of document.querySelectorAll('.app-only')) el.hidden = false; }
if (window.pywebview?.api) appReady(); else window.addEventListener('pywebviewready', appReady);
$('switch-game').onclick = async () => {
  if (dirty && !confirm('Unsaved changes in this level will be lost. Switch game anyway?')) return;
  dirty = false;
  await window.pywebview.api.home();
};

async function updateHistory() {
  const h = await api('history');
  dirty = !!h.dirty;
  knownRev = h.revision; viewKey = `${h.level}|${h.mission ? h.mission.join('/') : ''}`;
  $('undo').disabled = !h.undo.length; $('redo').disabled = !h.redo.length;
  $('undo').title = h.undo.length ? `Undo: ${h.undo.at(-1)} (Ctrl+Z)` : 'Undo (Ctrl+Z)';
  $('redo').title = h.redo.length ? `Redo: ${h.redo.at(-1)} (Ctrl+Y)` : 'Redo (Ctrl+Y)';
  renderHistory(h);
  const doc = $('doc');
  currentMission = h.mission;
  if (h.mission) {
    doc.className = 'mission';
    doc.innerHTML = `${h.dirty ? '● ' : ''}Mission <b>${escHtml(h.mission[1])}</b> · Mods\\${escHtml(h.mission[0])} · base ${h.level}`;
    doc.title = `Editing mission ${h.mission[1]} in Mods\\${h.mission[0]} (base level ${h.level})${h.dirty ? ' - unsaved changes' : ' - saved'}`;
  } else {
    doc.className = '';
    doc.textContent = h.level ? `${h.dirty ? '● ' : ''}${h.level} (game level)` : '';
    doc.title = h.level ? `Editing the game level ${h.level}${h.dirty ? ' - unsaved changes' : ''}` : '';
  }
  // Play needs a mission: on a game level it opens Save as mission first, then plays
  $('play').title = h.mission ? `Save mission ${h.mission[1]} and start the game straight into it`
    : 'A game level can\'t be played directly: Play opens Save as mission… first, then starts the game in the new mission';
  document.title = (h.dirty ? '● ' : '') + `${GAME.short} Editor - ${h.mission ? h.mission[1] : level || ''}`;
}
let currentMission = null;          // [mod, NAME] once saved / opened as a mission (updateHistory)

// ---- picking ----
const ray = new THREE.Raycaster();
let downAt = null;
renderer.domElement.addEventListener('pointerdown', (e) => { downAt = [e.clientX, e.clientY]; });
renderer.domElement.addEventListener('pointerup', (e) => {
  if (!downAt || Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) > 4 || gizmo.dragging) return;
  const r = renderer.domElement.getBoundingClientRect();
  ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
  if (groundPick) {           // a production tool (spawn pad) waiting for a spot on the ground
    const hit = ray.intersectObjects([groups.world, groups.props], true).find((h) => h.object.isMesh);
    if (hit) groundPick.pick(toGame(hit.point)); else status('no ground there - click on the ground', true);
    return;
  }
  if (placing) {              // placing from the library: the surface under the cursor
    const hit = ray.intersectObjects([groups.world, groups.props], true).find((h) => h.object.isMesh);
    if (hit) placeAt(hit.point); else status('no surface there - click on the ground or a building', true);
    return;
  }
  if (pickMode) {               // picking for a form (objective target, patrol waypoints): nothing gets selected
    const h = ray.intersectObjects(pickable.filter(pickOk), false)
      .find((x) => shownInView(x.object) && pickMode.accept(x.object.userData));
    if (h) pickMode.pick(h.object.userData); else status(pickMode.hint, true);
    return;
  }
  const hits = ray.intersectObjects(pickable.filter(pickOk), false).filter((x) => shownInView(x.object));
  if (!hits.length) return select(null);
  const h = hits[0];
  if (h.object.userData.cat === 'routes') return select({ route: h.instanceId });
  if (h.object.userData.camTarget) return select({ line: h.object.userData.line, target: true });
  if (e.shiftKey && ['objects', 'props'].includes(h.object.userData.cat)) return toggleMany(h.object.userData.line);
  select({ line: h.object.userData.line });
});

// Double-click: the orbit centre jumps to the surface under the cursor (world, props, markers) and the camera
// comes in close - the quick way into buildings.
renderer.domElement.addEventListener('dblclick', (e) => {
  const r = renderer.domElement.getBoundingClientRect();
  ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
  const targets = Object.values(groups).filter((g) => g.visible);
  const hit = ray.intersectObjects(targets, true).find((h) => h.object.isMesh && shownInView(h.object));
  if (!hit) return;
  if (topView) { placeTopCamera(hit.point); return; }
  const dir = camera.position.clone().sub(hit.point).normalize();
  orbit.target.copy(hit.point);
  camera.position.copy(hit.point).addScaledVector(dir, 3);
});

// ---- commands from the UI ----
async function openLevel(name, ask = true) {
  if (ask && !await confirmDiscard(`Open ${name}`)) return status('kept the unsaved changes');
  status(`opening ${name}…`);
  await run('open_level', { level: name });
  level = name; shownLevel = name; clearModelCache(); clearGroup(groups.world); hiddenLayers.clear(); endIsolate();
  await buildScene(true);
  await updateHistory();
  const want = (urlArgs.get('focus') || '').toLowerCase();
  const hero = (want && data.objects.find((o) => String(o.unit).toLowerCase() === want))
    || data.objects.find((o) => /^HERO/i.test(o.model) || /^(Bradley|Foley|Connors|Conners|Jones|Sherman)$/i.test(o.unit)) || data.objects[0];
  if (hero) focusOn(toThree(hero.pos));
  status(`${name}: ${data.objects.length} objects, ${data.props.length} props, ${data.routes.length} route markers`);
  if (location.hash === '#objectives') openObjectives().catch(() => {});   // link straight to the dialog
}
$('open').onclick = () => openLevel($('level').value);
let shownLevel = null;              // the picker's value while that level is open (put back when opening is cancelled)
$('level').onchange = async () => {
  const want = $('level').value;
  if (!await confirmDiscard(`Open ${want}`)) { if (shownLevel) $('level').value = shownLevel; return status('kept the unsaved changes'); }
  openLevel(want, false).catch(() => {});
};
$('undo').onclick = async () => { const r = await run('undo', {}); status(`undone: ${r.undone}`); await refresh(); };
$('redo').onclick = async () => { const r = await run('redo', {}); status(`redone: ${r.redone}`); await refresh(); };
function needModName() {           // Save as mod without a name: point at the box
  const box = $('mod');
  box.classList.add('invalid'); box.focus();
  status('type a mod name in the Mod name box first (the folder under Mods\\ it is saved to)', true);
}
$('mod').addEventListener('input', () => $('mod').classList.remove('invalid'));
$('save').onclick = async () => {
  const mod = $('mod').value.trim();
  if (!mod) return needModName();
  let r;
  try { r = await api('save_mod', { mod }); }
  catch (e) {
    if (!/^no changes/.test(e.message)) return status(e.message, true, 'command save_mod');
    if (!confirm(`Nothing differs from the shipped ${level}: saving would only copy the game's own ${level}.ENV / .RMD `
      + `into Mods\\${mod} (the game reads them either way, and Export refuses copies of game files).\n\nSave the copies anyway?`))
      return status('not saved - nothing changed yet');
    try { r = await api('save_mod', { mod, allow_unchanged: true }); } catch (e2) { return status(e2.message, true, 'command save_mod'); }
  }
  status(`saved ${r.files.join(', ')} to ${r.folder}`); await updateHistory();
};
// New level: new_level builds an empty scene with a ground zone and saves it as a mission, then it opens here.
$('new-level').onclick = () => {
  const sel = $('nl-env');
  if (!sel.options.length) for (const o of $('level').options) if (/^MISSION/i.test(o.value)) sel.add(new Option(o.text, o.value));
  $('nl-mod').value ||= $('mod').value; $('nl-result').textContent = '';
  $('newlevel-dialog').showModal();
};
$('nl-cancel').onclick = () => $('newlevel-dialog').close();
$('nl-make').onclick = async (e) => {
  e.preventDefault();
  const form = $('newlevel-dialog').querySelector('form');
  if (!form.reportValidity()) return;
  if (!await confirmDiscard('Create a new level')) return status('kept the unsaved changes');
  try {
    const r = await run('new_level', { environment: $('nl-env').value, mod: $('nl-mod').value.trim(),
                                       name: $('nl-name').value.trim(), size: +$('nl-size').value }, 'creating the level…');
    $('newlevel-dialog').close();
    level = $('nl-env').value; clearModelCache(); clearGroup(groups.world); hiddenLayers.clear();
    await buildScene(true); await updateHistory();
    focusOn(toThree(r.start)); await select(null);
    status(`new level ${r.mission[1]} (Mods\\${r.mission[0]}): Terrain… to shape the ground, Library to add props`);
  } catch (err) { $('nl-result').textContent = err.message; }
};

// Open mission: missions saved with save_mission (they remember their base level and layers).
$('open-mission').onclick = async () => {
  const list = await run('list_missions');
  const sel = $('o-list'); sel.innerHTML = '';
  for (const m of list) sel.add(new Option(`${m.name}  (Mods\\${m.mod})`, JSON.stringify(m)));
  // mission DLLs someone built (a saved mission made from one is listed above and keeps its DLL)
  const saved = new Set(list.map((m) => `${m.mod}/${m.name}`.toUpperCase()));
  let dlls = [];
  if (GAME.script_host) { try { dlls = await run('list_dll_missions'); } catch { dlls = []; } }
  for (const d of dlls) {
    if (!d.enabled || saved.has(`${d.mod}/${d.dll.replace(/\.dll$/i, '')}`.toUpperCase())) continue;
    sel.add(new Option(`${d.dll}  (Mods\\${d.mod}, mission DLL${d.shipped ? ' - changed game DLL' : ''})`,
                       JSON.stringify({ mod: d.mod, dll: d.dll, name: d.dll })));
  }
  // the game's own missions, opened as readable Lua in a mod (editor/game_missions.py; DesertStormFix 3.3)
  if (GAME.id === 'ds1') {
    let gm = [];
    try { gm = await run('game_missions'); } catch { gm = []; }
    for (const g of gm) {
      sel.add(new Option(`${g.mission}  (game mission as Lua${g.mods.length ? ` - Mods\\${g.mods.join(', ')}` : ''})`,
                         JSON.stringify({ game: g.mission, mods: g.mods, name: g.mission })));
    }
  }
  if (!sel.options.length) sel.add(new Option('no saved missions yet', ''));
  $('open-dialog').showModal();
};
$('o-open').onclick = async (e) => {
  e.preventDefault();
  const v = $('o-list').value;
  if (!v) return;
  const m = JSON.parse(v);
  if (!await confirmDiscard(`Open ${m.name}`)) return status('kept the unsaved changes');
  $('open-dialog').close();
  status(`opening ${m.name}…`);
  let r;
  if (m.dll) {
    try { r = await run('open_dll_mission', { mod: m.mod, dll: m.dll }); }
    catch (err) {
      if (!/\(base\)$/.test(err.message)) throw err;
      const base = prompt(`${err.message}\nLevel (e.g. MISSION1):`);
      if (!base) return status('not opened');
      r = await run('open_dll_mission', { mod: m.mod, dll: m.dll, base });
    }
  } else if (m.game) {
    const mod = m.mods[0] || prompt(`Mod folder for the Lua version of ${m.name}
(the game runs it instead of ${m.name}.dll):`,
                                    `My${m.name}`);
    if (!mod) return status('not opened');
    r = await run('game_mission_open', { mission: m.game, mod });
    m.mod = mod;
    if (r.made) status(`${m.name}: Lua made from your game's ${m.name}.dll (Mods\\${mod}\\mission_src)`);
  } else r = await run('open_mission', m);
  level = r.base; shownLevel = r.base; $('level').value = r.base; clearModelCache(); clearGroup(groups.world); hiddenLayers.clear();
  await buildScene(true); await updateHistory(); await select(null);
  const hero = data.objects.find((o) => /^HERO/i.test(o.model) || /^(Bradley|Foley|Connors|Conners|Jones|Sherman)$/i.test(o.unit)) || data.objects[0];
  if (hero) focusOn(toThree(hero.pos));
  status(`${m.name} (Mods\\${m.mod}, base ${r.base})`);
};

// ---- script writer: the mission's Lua in CodeMirror, snippets, scene names, the game's error report ----
const SNIPPETS = [
  ['When an object is killed', `if id == game.EVENT.KILLED and a == 0 --[[victim's script id]] then\n    -- b = killer\nend\n`],
  ['When a player enters a region', `if id == game.EVENT.ENTERED and a == "REGION" then\n    -- b = player (0-3)\nend\n`],
  ['When a player leaves a region', `if id == game.EVENT.LEFT and a == "REGION" then\nend\n`],
  ['When an enemy spots someone', `if id == game.EVENT.SPOTTED then\n    -- a = who saw, b = who was seen\nend\n`],
  ['Start a timer (seconds)', `game.timer(1, 30)   -- on_message(game.EVENT.TIMER, 1) in 30 s\n`],
  ['When a timer fires', `if id == game.EVENT.TIMER and a == 1 then\nend\n`],
  ['Show a layer', `game.show_layer("LAYER")\n`],
  ['Hide a layer', `game.hide_layer("LAYER")\n`],
  ['Show an objective', `game.show_objective(0)\n`],
  ['Complete an objective', `game.complete_objective(0)\n`],
  ['Mission success', `game.end_mission(2)\n`],
  ['Mission failed', `game.end_mission(1)\n`],
  ['Remember a value in saves', `game.state.done = true   -- game.state is stored in save games\n`],
  ['Log a message', `print("something happened")   -- ${GAME.plugin}.log\n`],
];
// DesertStormFix 3.2+ only (Desert Storm)
const SNIPPETS_DS1 = [
  ['Kill a soldier (cutscene)', `game.kill(0 --[[soldier's script id]])   -- death animation, blood pool\n`],
  ['Blow up a soldier (cutscene)', `game.explode(0 --[[soldier's script id]])   -- thrown, gibs, large blood pool\n`],
  ['If the difficulty is VERY HARD', `if game.difficulty and game.difficulty() == 3 then   -- 0 EASY 1 MEDIUM 2 HARD 3 VERY HARD 4 VERY EASY\nend\n`],
];
let cm = null;
function ensureEditor() {
  if (cm) return cm;
  cm = CodeMirror($('code'), { mode: 'lua', lineNumbers: true, indentUnit: 4, tabSize: 4, matchBrackets: true,
                               autoCloseBrackets: true, extraKeys: { 'Ctrl-S': () => saveScript(), Tab: (c) => c.replaceSelection('    ') } });
  cm.on('change', () => { $('script-state').textContent = 'unsaved changes'; });
  for (const [label, text] of GAME.id === 'ds1' ? SNIPPETS.concat(SNIPPETS_DS1) : SNIPPETS) {
    const b = document.createElement('button'); b.textContent = label;
    b.onclick = () => insertCode(text); $('snippets').appendChild(b);
  }
  return cm;
}
function insertCode(text) {
  const line = cm.getCursor().line, indent = (cm.getLine(line).match(/^\s*/) || [''])[0];
  const body = text.split('\n').map((l, i) => (i && l ? indent + l : l)).join('\n');
  cm.replaceSelection(body); cm.focus();
}
async function openScript() {
  try {
    const r = await run('get_script');
    ensureEditor();
    $('script-title').textContent = r.path.split(/[\\/]/).slice(-2).join(' › ');
    $('script-dialog').showModal();
    cm.setValue(r.text); cm.clearHistory(); $('script-state').textContent = 'saved';
    setTimeout(() => cm.refresh(), 0);
    $('sn-region').innerHTML = data.regions.map((g) => `<option>${g.name}</option>`).join('') || '<option disabled>no regions</option>';
    $('sn-layer').innerHTML = Object.keys(layerInfo).map((n) => `<option>${n}</option>`).join('') || '<option disabled>no layers</option>';
    $('script-report').textContent = '';
  } catch (e) { status(e.message, true); }
}
async function saveScript() {
  const cur = cm.getCursor();
  const r = await run('save_script', { text: cm.getValue() }, 'script saved');
  if (r.text !== cm.getValue()) { cm.setValue(r.text); cm.setCursor(cur); }
  $('script-state').textContent = r.lua_error ? `saved - Lua error: ${r.lua_error}` : 'saved';
  if (r.lua_error) status(`the script doesn't load: ${r.lua_error}`, true);
}
$('script-btn').onclick = openScript;
async function play() {
  const r = await run('play', {}, 'starting the game…');
  cOffset = 0; $('c-log').innerHTML = ''; showCrash(null); cWasRunning = true; cMods = r.mods || null;
  $('console').hidden = false; startConsole();
  status(`game starting in ${r.level} (Mods\\${r.mod}) - it goes straight into the mission; if it stops at PLEASE PRESS `
    + 'ANY KEY TO BEGIN, click the game window and press a key. It quits after the mission\'s end screen');
  await updateHistory();
}
let playAfterSave = false;          // Play on a game level: Save as mission first, then play
$('play').onclick = () => {
  if (!currentMission) { playAfterSave = true; openSaveMission(); return; }
  play().catch(() => showProblems());
};

// ---- game console: the plugin's log while the game runs, and a crash report when it stops with an error ----
let cOffset = 0, cTimer = null, cWasRunning = false, cMods = null;
const esc = (t) => t.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
function logClass(l) {
  if (/\[fail\]|error|crash|couldn't|can't|not found/i.test(l)) return 'fail';
  if (/warning|\[warn\]/i.test(l)) return 'warn';
  if (/\[ok\]/.test(l)) return 'ok';
  return '';
}
function showCrash(c) {
  const box = $('c-crash');
  if (!c) { box.hidden = true; return; }
  const rows = [['Error', `${c.exception} (${c.exit_code})`]];
  if (c.address) rows.push(['Where', `${c.module || GAME.exe} at ${c.address}`]);
  if (c.cause) rows.push(['Likely cause', c.cause]);
  if (c.last_mod_file) rows.push(['Last mod file loaded', c.last_mod_file]);
  if (c.last_log_line) rows.push(['Last log line', c.last_log_line]);
  let html = `<h3>The game crashed</h3><dl>${rows.map(([k, v]) => `<dt>${k}</dt><dd>${esc(String(v))}</dd>`).join('')}</dl>`;
  html += problemsHtml(c.problems);
  box.innerHTML = html; box.hidden = false;
  wireProblems(box);
}
function problemsHtml(problems) {
  if (!problems?.length) return '';
  const fixable = problems.some((p) => p.fix);
  return '<h3 style="margin-top:8px">Problems in this scene</h3>' + problems.map((p) =>
    `<div data-fix="${p.fix || ''}"><a data-line="${p.line}">line ${p.line}</a> - ${esc(p.problem)}</div>`).join('') +
    (fixable ? '<button id="c-fix" style="margin-top:6px">Fix these</button>' : '');
}
function wireProblems(box) {
  for (const a of box.querySelectorAll('a[data-line]')) a.onclick = () => select({ line: +a.dataset.line }, true);
  const fix = box.querySelector('#c-fix');
  if (fix) fix.onclick = async () => {
    const kinds = new Set([...box.querySelectorAll('[data-fix]')].map((d) => d.dataset.fix));
    let n = 0;
    if (kinds.has('zones')) n += (await run('fix_zones', {}, 'zones fixed')).fixed.length;
    if (kinds.has('crews')) n += (await run('fix_crews', {}, 'crews fixed')).fixed.length;
    if (kinds.has('hidden')) n += (await run('fix_hidden', {}, 'objects put in play')).fixed.length;
    if (kinds.has('rinfo')) n += (await run('fix_rinfo', {}, 'prop flags fixed')).fixed.length;
    await refresh(); status(`${n} record(s) fixed`); showProblems();
  };
}
// Problems found before playing (Play refuses known crashers): same box, without a crash.
async function showProblems() {
  const problems = await api('scene_problems').catch(() => []);
  const box = $('c-crash');
  if (!problems.length) { box.hidden = true; return false; }
  box.innerHTML = problemsHtml(problems); box.hidden = false; wireProblems(box);
  $('console').hidden = false;
  return true;
}
async function pollConsole() {
  let r;
  try { r = await api('game_console', { offset: cOffset, all_lines: $('c-all').checked }); } catch { return; }
  if (r.offset < cOffset) $('c-log').innerHTML = '';
  cOffset = r.offset;
  if (r.lines.length) {
    const log = $('c-log'), atEnd = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
    log.insertAdjacentHTML('beforeend', r.lines.map((l) => `<span class="${logClass(l)}">${esc(l)}</span>\n`).join(''));
    while (log.childNodes.length > 6000) log.removeChild(log.firstChild);
    if (atEnd) log.scrollTop = log.scrollHeight;
  }
  let state = r.running ? `running${r.level ? ' - ' + r.level : ''}` : 'not running';
  if (r.exit_code !== null && r.exit_code !== undefined) state = r.crash ? 'crashed' : `exited (${r.exit_code})`;
  if (r.waiting_for_key) state += ' · waiting at PLEASE PRESS ANY KEY TO BEGIN - click the game window and press a key';
  if (!r.log) state += ` · no ${GAME.plugin}.log (only the dev build of the plugin writes one)`;
  // the game loads every enabled mod (name order, a later one wins a file both have) - not only the one being played
  const mods = r.mods || cMods || [];
  if (mods.length) state += ` · mods loaded: ${mods.join(', ')}`;
  $('c-state').textContent = state;
  $('c-state').title = mods.length ? `The game loads every enabled mod in Mods\\, in name order (a later one wins a file `
    + `both have): ${mods.join(', ')}. Rename a folder with a leading _ (or MODS in the game's main menu) to switch one off.` : '';
  showCrash(r.crash);
  $('console-btn').classList.toggle('alert', !!r.crash);
  if (r.crash && cWasRunning) { $('console').hidden = false; status('the game crashed - see the console', true); }
  cWasRunning = r.running;
  if (!r.running && $('console').hidden) stopConsole();
}
function startConsole() { if (!cTimer) { cTimer = setInterval(pollConsole, 1000); pollConsole(); } }
function stopConsole() { clearInterval(cTimer); cTimer = null; }
$('console-btn').onclick = () => { const c = $('console'); c.hidden = !c.hidden; if (!c.hidden) startConsole(); };
$('c-close').onclick = () => { $('console').hidden = true; };
$('c-clear').onclick = () => { $('c-log').innerHTML = ''; };
$('c-all').onchange = () => { cOffset = 0; $('c-log').innerHTML = ''; pollConsole(); };
$('script-play').onclick = async () => { try { await saveScript(); await play(); } catch { /* status shows it */ } };
$('script-save').onclick = saveScript;
$('script-close').onclick = () => {
  if ($('script-state').textContent === 'unsaved changes' && !confirm('Close without saving the script?')) return;
  $('script-dialog').close();
};
$('sn-region-go').onclick = () => $('sn-region').value && insertCode(`"${$('sn-region').value}"`);
$('sn-layer-go').onclick = () => $('sn-layer').value && insertCode(`"${$('sn-layer').value}"`);
$('sn-object').onclick = () => {
  const o = selection?.line !== undefined ? data.objects.find((x) => x.line === selection.line) : null;
  if (o) insertCode(String(o.script_id)); else status('select an object in the 3D view first', true);
};
$('sn-report').onclick = async () => {
  const r = await run('script_report');
  $('script-report').textContent = r.lines.length ? r.lines.join('\n') : 'no errors reported for this script';
};

// ---- asset forms: .weapon / .skin files (+ uploads) written into a mod ----
// [setting, label, kind, hint]: kind text / number / select:<list> / file:<accept>[:dds[:fmt]]
const WEAPON_FORM = [
  ['id', 'Weapon id', 'text', 'letters, digits, _ - e.g. US_WPN_HandCannon'],
  ['based on', 'Based on', 'select:weapons', 'the game weapon it starts as'],
  ['display name', 'Display name', 'text', 'inventory and loadout screen'],
  ['faction', 'Faction', 'select:factions', 'listed first for those uniforms'],
  ['damage', 'Damage', 'number', 'per bullet'], ['time between shots', 'Time between shots (s)', 'number', ''],
  ['magazine size', 'Magazine size', 'number', ''], ['magazines', 'Magazines', 'number', 'handed out, incl. the one in the gun'],
  ['reload time', 'Reload time (s)', 'number', ''], ['range', 'Range', 'number', ''],
  ['hearing range', 'Hearing range', 'number', 'normal 80, silenced 8'], ['silenced', 'Silenced', 'select:yesno', 'no flash, quiet shot'],
  ['texture', 'Texture', 'file:.dds,.png:dds', 'same layout as the original; PNG is converted'],
  ['model', 'Model (.evo)', 'file:.evo', 'from the Blender exporter'],
  ['hud icon', 'HUD picture (.png)', 'file:.png', 'transparent, 1 px = 1 px of 800 x 600'],
  ['sound', 'Borrow a gunshot', 'select:weapons', 'any weapon\'s sound'],
  ['shot sound', 'Own gunshot (.wav)', 'file:.wav', '16-bit WAV'], ['tail sound', 'Distant tail (.wav)', 'file:.wav', 'optional'],
];
const SKIN_FORM = [
  ['_file', 'File name', 'text', 'the .skin file, e.g. JungleSAS'],
  ['uniform', 'Side', 'select:uniforms', ''], ['soldier', 'Soldier', 'select:soldiers', ''],
  ['uniform name', 'Uniform name', 'text', 'a new uniform: what the UNIFORM row shows'],
  ['first name', 'First name', 'text', 'or a new recruit: first + last name'], ['last name', 'Last name', 'text', ''],
  ['texture', 'Texture', 'file:.dds,.png:dds', 'the soldier\'s layout; PNG is converted'],
  ['portrait', 'Portrait (64 x 64)', 'file:.dds,.png:dds:DXT3', 'soldier panel picture'],
  ['body', 'Body (.evo)', 'file:.evo', 'optional own body mesh'], ['normal map', 'Normal map (.tga)', 'file:.tga', 'optional'],
  ['voice', 'Voice', 'select:voices', 'SAS or Delta accent'],
];
let assetOpts = null, assetKind = 'weapon';
function assetList(name) {
  if (name === 'yesno') return ['yes', 'no'];
  if (name === 'weapons') return assetOpts.weapons.map((w) => w.id);
  return assetOpts[name] || [];
}
function buildAssetForm() {
  const box = $('a-fields'); box.innerHTML = '';
  for (const [key, label, kind, hint] of assetKind === 'weapon' ? WEAPON_FORM : SKIN_FORM) {
    const wrap = document.createElement('label');
    wrap.innerHTML = `${label}`;
    let input;
    if (kind.startsWith('select:')) {
      input = document.createElement('select');
      input.add(new Option(key === 'based on' || key === 'uniform' || key === 'soldier' ? '(choose)' : '(unchanged)', ''));
      for (const v of assetList(kind.slice(7))) input.add(new Option(v, v));
    } else if (kind.startsWith('file:')) {
      const [, accept, conv, fmt] = kind.split(':');
      input = document.createElement('input'); input.type = 'text'; input.placeholder = 'pick a file …';
      const pick = document.createElement('input'); pick.type = 'file'; pick.accept = accept;
      pick.onchange = async () => {
        const f = pick.files[0]; if (!f) return;
        const mod = $('a-mod').value.trim(); if (!mod) { status('enter the mod folder first', true); return; }
        const buf = new Uint8Array(await f.arrayBuffer());
        let bin = ''; for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
        const r = await run('upload_file', { mod, filename: f.name, data_base64: btoa(bin), as_dds: conv === 'dds', dds_format: fmt || null },
                            `${f.name} uploaded`);
        input.value = r.name;
      };
      const row = document.createElement('div'); row.className = 'file'; row.append(input, pick);
      wrap.append(row); input.dataset.key = key;
      if (hint) wrap.insertAdjacentHTML('beforeend', `<small>${hint}</small>`);
      box.appendChild(wrap); continue;
    } else {
      input = document.createElement('input'); input.type = kind === 'number' ? 'number' : 'text';
      if (kind === 'number') input.step = 'any';
    }
    input.dataset.key = key; wrap.appendChild(input);
    if (hint) wrap.insertAdjacentHTML('beforeend', `<small>${hint}</small>`);
    box.appendChild(wrap);
  }
}
for (const [tab, kind] of [['a-tab-weapon', 'weapon'], ['a-tab-skin', 'skin']]) {
  $(tab).onclick = () => {
    assetKind = kind;
    for (const t of ['a-tab-weapon', 'a-tab-skin']) $(t).classList.toggle('on', t === tab);
    $('a-result').textContent = ''; buildAssetForm();
  };
}
$('assets-btn').onclick = async () => {
  assetOpts = await run('asset_options');
  $('a-mods').innerHTML = assetOpts.mods.map((m) => `<option value="${m}">`).join('');
  $('a-result').textContent = ''; buildAssetForm();
  $('assets-dialog').showModal();
};
$('a-create').onclick = async (e) => {
  e.preventDefault();
  const mod = $('a-mod').value.trim();
  if (!mod) return status('enter the mod folder', true);
  const fields = {}; let file = '';
  for (const el of $('a-fields').querySelectorAll('[data-key]')) {
    const v = el.value.trim(); if (!v) continue;
    if (el.dataset.key === '_file') file = v; else fields[el.dataset.key] = v;
  }
  try {
    const r = assetKind === 'weapon' ? await run('create_weapon', { mod, fields }) : await run('create_skin', { mod, name: file, fields });
    $('a-result').textContent = `Written: ${r.path}`; status('asset written');
  } catch (err) { $('a-result').textContent = err.message; }
};

// Save as mission: a dialog for mod / mission / campaign names -> save_mission (stays open with the result).
$('save-mission').onclick = () => { playAfterSave = false; openSaveMission(); };
function openSaveMission() {
  if (!level) return;
  if (data?.mission) { $('m-mod').value = data.mission[0]; $('m-name').value = data.mission[1].toLowerCase(); }
  $('m-mod').value ||= $('mod').value; $('m-result').textContent = '';
  $('m-close').textContent = 'Cancel'; $('m-save').textContent = playAfterSave ? 'Save mission and play' : 'Save mission';
  $('m-play-note').hidden = !playAfterSave;
  $('mission-dialog').showModal();
}
$('m-close').onclick = () => { playAfterSave = false; $('mission-dialog').close(); };
$('m-save').onclick = async (e) => {
  e.preventDefault();
  const form = $('mission-dialog').querySelector('form');
  if (!form.reportValidity()) return;
  try {
    const r = await run('save_mission', { mod: $('m-mod').value.trim(), name: $('m-name').value.trim(),
                                           campaign: $('m-campaign').value.trim() || null });
    $('m-result').textContent = `Saved to ${r.folder}
${r.files.join(', ')}` +
      (r.script_kept ? `
${r.level} already existed - kept as it is` : '') +
      (r.packed_models.length ? `
packed models: ${r.packed_models.join(', ')}` : '') +
      `
Play: SINGLE PLAYER › CUSTOM CAMPAIGNS${$('m-campaign').value.trim() ? '' : ' (add a campaign name to list it there)'}`;
    status(`mission ${r.level} saved - you are now editing it`); await updateHistory();
    $('m-close').textContent = 'Close'; $('m-save').textContent = 'Save again'; $('m-close').focus();
    if (playAfterSave) { playAfterSave = false; $('mission-dialog').close(); play().catch(() => showProblems()); }
  } catch (err) { $('m-result').textContent = err.message; }
};

// ---- copy / paste: Ctrl+C the selection, Ctrl+V at the surface under the mouse (or the view centre) ----
let mouseNdc = null;
renderer.domElement.addEventListener('pointermove', (e) => {
  const r = renderer.domElement.getBoundingClientRect();
  mouseNdc = new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
});
renderer.domElement.addEventListener('pointerleave', () => { mouseNdc = null; });
function pastePoint() {
  if (mouseNdc) {
    ray.setFromCamera(mouseNdc, camera);
    const hit = ray.intersectObjects([groups.world, groups.props], true).find((h) => h.object.isMesh);
    if (hit) return hit.point;
  }
  return orbit.target.clone();
}
async function copySelection() {
  const lines = many.size ? [...many] : selection?.line !== undefined ? [selection.line] : [];
  if (!lines.length) return status('select something to copy', true);
  const r = await run('copy', { lines });
  status(`copied ${r.copied} (${r.kinds.join(', ')}) - Ctrl+V pastes under the mouse`);
}
async function pasteHere() {
  const r = await run('paste', { pos: toGame(pastePoint()) }, 'pasted');
  await refresh();
  if (r.lines.length > 1) selectMany(r.lines); else await select({ line: r.lines[0] });
  status(`pasted ${r.lines.length}`);
}

window.addEventListener('keydown', async (e) => {
  if (['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName) || document.querySelector('dialog[open]')) return;
  if (e.key === 'Escape' && pickMode) { endPick(true); return; }
  if (e.key === 'Escape' && groundPick) { endGroundPick(); status('placing cancelled'); return; }
  if (e.key === 'Escape' && camView) { exitCamView(); return; }
  if (e.key === 'Enter' && pickMode?.finish) { e.preventDefault(); pickMode.finish(); return; }
  const k = e.key.toLowerCase();
  if (e.ctrlKey && k === 'c') { e.preventDefault(); return copySelection().catch(() => {}); }
  if (e.ctrlKey && e.shiftKey && k === 'v') { e.preventDefault(); return hostHooks.pasteInPlace?.().catch(() => {}); }
  if (e.ctrlKey && k === 'v') { e.preventDefault(); return pasteHere().catch(() => {}); }
  if (k === 'escape' && placing) { cancelPlacing(); status('placing cancelled'); return; }
  if (e.ctrlKey && k === 'z') { e.preventDefault(); $('undo').click(); }
  else if (e.ctrlKey && k === 'y') { e.preventDefault(); $('redo').click(); }
  else if (e.ctrlKey && k === 'd' && selection?.line) {
    e.preventDefault();
    const r = await run('duplicate', { line: selection.line }, 'duplicated');
    await refresh(); await select({ line: r.line });
  } else if (k === 'delete' && selection) {
    if (selection.route !== undefined) await run('route_delete', { index: selection.route }, 'route marker deleted');
    else {
      const o = data.objects.find((x) => x.line === selection.line);
      if (o?.is_base_target && !confirm(`Object #${o.script_id} is required by ${o.base_dll || 'the base mission'}. Deleting it will break the `
        + 'original campaign logic. Hide it in a layer instead of deleting.\n\nDelete it anyway?')) return;
      try { await run('delete', { line: selection.line, force: !!o?.is_base_target }, `line ${selection.line} deleted`); }
      catch { return; }          // refused (a zone's root record ...): run() shows why in the status line
    }
    selection = null; await refresh();
  } else if (k === 'f' && !e.ctrlKey && !e.altKey) { e.preventDefault(); dropToGround().catch(() => {}); }
  else if (k === 'c' && !e.ctrlKey && selection) focusOn(proxy.position);
});

// ==== Creation-Kit-style tools: drop to ground, picking for forms, camera guides / view-through, soldier behaviour,
// events & actions, objectives, atmosphere. All changes go through commands (logic.py) like everything else. ====

// Game angles in degrees (pitch > 0 looks down; yaw: forward = (sin, cos) in game x / z) -> a three.js direction.
function gameForward(pitch, yaw) {
  const p = deg(pitch), y = deg(yaw);
  return new THREE.Vector3(Math.sin(y) * Math.cos(p), -Math.sin(p), -Math.cos(y) * Math.cos(p));
}
const SCENE_LISTS = ['props', 'objects', 'waypoints', 'regions', 'cameras', 'sounds', 'lights', 'aipoints', 'chunks'];
const findRec = (line) => SCENE_LISTS.map((k) => data?.[k]?.find((r) => r.line === line)).find(Boolean);
const lift = (v, m = 0.15) => v.add(new THREE.Vector3(0, m, 0));

// ---- Drop to ground (F): a ray straight down from each selected thing onto the visible world and props (never
// itself); the bottom of its bounding box (markers: their position) goes onto the hit point. One move per record. ----
async function dropToGround() {
  const lines = many.size ? [...many] : selection?.line !== undefined && !selection.target ? [selection.line] : [];
  if (!lines.length) return status('select something to drop to the ground (F)', true);
  const targets = [groups.world, groups.props].filter((g) => g.visible);
  const moves = [];
  for (const line of lines) {
    const obj = byLine.get(line), rec = findRec(line);
    if (!obj || !rec || rec.ground || rec.road) continue;   // a level's ground / a road follows the ground already
    const self = new Set(); obj.traverse((c) => self.add(c));
    const box = new THREE.Box3().setFromObject(obj);
    const solid = ['props', 'objects', 'world'].includes(obj.userData.cat) && !box.isEmpty();
    const bottom = solid ? box.min.y : obj.position.y;
    const centre = solid ? box.getCenter(new THREE.Vector3()) : obj.position.clone();
    // from just above its feet first (a roof or bridge over it isn't the ground), from its top when it is sunk deeper
    const down = (y) => new THREE.Raycaster(new THREE.Vector3(centre.x, y, centre.z), new THREE.Vector3(0, -1, 0))
      .intersectObjects(targets, true).find((h) => h.object.isMesh && !self.has(h.object) && !h.object.userData.ghost);
    const hit = down(bottom + 0.5) || (solid ? down(Math.max(box.max.y, obj.position.y) + 0.05) : null);
    if (!hit) continue;
    const dy = hit.point.y - bottom;
    if (Math.abs(dy) < 0.005) continue;
    const base = rec.pivot || rec.pos;        // kit pieces move by their centre (move command)
    moves.push({ line, pos: [base[0], +(base[1] + dy * 100).toFixed(2), base[2]] });
  }
  if (!moves.length) return status('nothing to drop: no ground under the selection, or it already stands on it');
  for (const m of moves) await run('move', m);
  await refresh();
  if (lines.length > 1) selectMany(lines);
  status(`dropped ${moves.length} to the ground (Ctrl+Z undoes each)`);
}
$('drop-btn').onclick = () => dropToGround().catch(() => {});
$('world-edit').checked = worldEdit;
$('world-edit').onchange = (e) => {
  worldEdit = e.target.checked;
  try { localStorage.setItem('worldEdit', worldEdit ? '1' : '0'); } catch { /* ignore */ }
  if (!worldEdit && selection?.line !== undefined && data.chunks?.some((c) => c.line === selection.line)) select(null);
  status(worldEdit ? 'Edit world: click the ground, cliffs or buildings to pick that chunk (Del deletes, Alt turns, Ctrl+D copies)'
                   : "Edit world off: the level's own geometry can't be picked");
};

// ---- Paths & Regions panel (paths.py): draw a path (click points, Enter), place a region or a waypoint ----
let ppMode = 'path', ppClicks = [];
const ppPreview = new THREE.Group(); scene3.add(ppPreview);
const ppDotGeo = keep(new THREE.SphereGeometry(0.35, 10, 8));
const ppMat = keep(new THREE.MeshBasicMaterial({ color: 0x38c7d6, depthTest: false }));
const ppLineMat = keep(new THREE.LineBasicMaterial({ color: 0x38c7d6, depthTest: false }));
function ppDraw() {
  clearGroup(ppPreview);
  for (const p of ppClicks) { const s = new THREE.Mesh(ppDotGeo, ppMat); s.position.copy(p); s.renderOrder = 11; ppPreview.add(s); }
  if (ppClicks.length > 1) {
    const pts = [...ppClicks]; if ($('pp-loop').checked) pts.push(ppClicks[0]);
    const l = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), ppLineMat); l.renderOrder = 11; ppPreview.add(l);
  }
  requestRender();
}
function ppTab(mode) {
  ppMode = mode;
  for (const [m, id] of [['path', 'pp-path'], ['region', 'pp-region'], ['wp', 'pp-wp'], ['cam', 'pp-cam']]) {
    $(`pp-tab-${m}`).classList.toggle('on', m === mode); $(id).hidden = m !== mode;
  }
  if (mode !== 'path') { ppClicks = []; ppDraw(); }
}
for (const m of ['path', 'region', 'wp', 'cam']) $(`pp-tab-${m}`).onclick = () => ppTab(m);
$('pp-cam-add').onclick = async () => {        // the editor view -> a CAMFREE (pitch down +, yaw: forward = sin / cos)
  const f = new THREE.Vector3(); camera.getWorldDirection(f);
  const pitch = THREE.MathUtils.radToDeg(Math.asin(Math.max(-1, Math.min(1, -f.y))));
  const yaw = THREE.MathUtils.radToDeg(Math.atan2(f.x, -f.z));
  const r = await run('camera_add', { pos: toGame(camera.position), rot: [+pitch.toFixed(3), +yaw.toFixed(3), 0],
                                      name: $('pp-cname').value, fov: +$('pp-fov').value }, 'camera saved');
  await refresh(); showLayer('cameras');
  status(`camera ${r.name} saved (camera ${r.index}) - use it in a rule's "Camera shot"`);
};
$('pp-shape').onchange = () => { $('pp-boxsize').hidden = $('pp-shape').value !== 'box'; $('pp-radius').hidden = $('pp-shape').value === 'box'; };
async function fillPathSelect(keep) {
  await loadPaths();
  const sel = $('pp-path-sel'), cur = keep ?? sel.value; sel.innerHTML = '';
  for (const c of pathList) sel.add(new Option(`${c.name} - ${c.ids.length} points${c.loop ? ', loop' : ''}`, c.start));
  if (cur !== undefined && cur !== '') sel.value = String(cur);
  const n = selectedScriptIds().length;
  $('pp-info').textContent = `${pathList.length} path(s) in this level. ${n ? n + ' object(s) selected.' : 'Select soldiers (Shift-click for several) to give them the path.'}`;
}
async function openPathsPanel(mode = 'path') {
  if (!level) return;
  $('paths-panel').hidden = false; ppTab(mode); showLayer('waypoints');
  await fillPathSelect();
}
function ppActive() { return !$('paths-panel').hidden; }
renderer.domElement.addEventListener('pointerdown', async (e) => {
  if (e.button !== 0 || !ppActive() || pickMode) return;
  const r = renderer.domElement.getBoundingClientRect();
  ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
  const hit = ray.intersectObjects([groups.world, groups.props], true).find((h) => h.object.isMesh && !h.object.userData.ghost);
  if (!hit || e.shiftKey || ppMode === 'cam') return;   // Shift-click still selects (soldiers to give a path to)
  e.stopImmediatePropagation(); e.preventDefault();
  downAt = null;                         // the click-up must not select what is under the cursor
  if (ppMode === 'path') {
    ppClicks.push(hit.point.clone()); ppDraw();
    status(`${ppClicks.length} point(s) - click more, Enter makes the path, Esc clears (Shift-click selects soldiers)`);
  } else if (ppMode === 'region') {
    const box = $('pp-shape').value === 'box';
    const res = await run('region_add', { pos: toGame(hit.point), name: $('pp-rname').value, shape: $('pp-shape').value,
                                          size: [+$('pp-sx').value, +$('pp-sy').value, +$('pp-sz').value], radius: +$('pp-r').value },
                          `region placed (${box ? 'box' : 'sphere'})`);
    await refresh(); await select({ line: res.line });
  } else {
    const res = await run('waypoint_add', { pos: toGame(hit.point), name: $('pp-wname').value, wait: +$('pp-wwait').value,
                                            snap: $('pp-snap').checked }, 'waypoint placed');
    if (res.warning) status(res.warning, true);
    await refresh(); await select({ line: res.line });
  }
}, { capture: true });
async function ppFinish() {
  if (ppClicks.length < 2) return status('click at least two points on the ground', true);
  const res = await run('path_create', { points: ppClicks.map(toGame), name: $('pp-name').value, loop: $('pp-loop').checked,
                                         wait: +$('pp-wait').value, snap: $('pp-snap').checked }, 'path made');
  if (res.warning) status(res.warning, true);
  ppClicks = []; ppDraw();
  await refresh(); await fillPathSelect(res.start);
  status(`path ${res.name}: ${res.ids.length} points - select soldiers and "Assign" to have them walk it`);
}
window.addEventListener('keydown', (e) => {
  if (!ppActive() || ppMode !== 'path' || ['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName)) return;
  if (e.key === 'Enter' && ppClicks.length) { e.preventDefault(); e.stopImmediatePropagation(); ppFinish().catch(() => {}); }
  if (e.key === 'Escape' && ppClicks.length) { e.stopImmediatePropagation(); ppClicks = []; ppDraw(); status('path points cleared'); }
}, { capture: true });
$('pp-finish').onclick = () => ppFinish().catch(() => {});
$('pp-clear').onclick = () => { ppClicks = []; ppDraw(); };
$('pp-loop').onchange = ppDraw;
$('pp-close').onclick = () => { $('paths-panel').hidden = true; ppClicks = []; ppDraw(); };
$('paths-btn').onclick = () => { if ($('paths-panel').hidden) openPathsPanel().catch(() => {}); else $('pp-close').click(); };
$('pp-assign').onclick = async () => {
  const start = +$('pp-path-sel').value;
  const c = pathList.find((x) => x.start === start);
  const lines = many.size ? [...many] : selection?.line !== undefined ? [selection.line] : [];
  const soldiers = lines.filter((l) => data.objects.some((o) => o.line === l));
  if (!c) return status('make or pick a path first', true);
  if (!soldiers.length) return status('select the soldiers first (click, Shift-click for several)', true);
  await run('set_patrol', { lines: soldiers, waypoints: c.ids, loop: c.loop, movement: $('pp-move').value, start: $('pp-start').value,
                            unseen: $('pp-unseen').checked },
            `${soldiers.length} soldier(s) walk ${c.name}`);
  // a soldier standing on a blocked walk-map cell may never get onto his route
  const stand = soldiers.map((l) => data.objects.find((o) => o.line === l)).filter(Boolean).map((o) => o.pos);
  const chk = await api('walk_check', { points: stand }).catch(() => []);
  const bad = chk.filter((p) => p.walkable === false).length;
  if (bad) status(`${bad} of the soldiers stand on blocked ground (AI walk map) - move them onto open ground or they may not reach the path`, true);
  await refresh(); await fillPathSelect(start);
};
$('pp-del').onclick = async () => {
  const start = +$('pp-path-sel').value; const c = pathList.find((x) => x.start === start);
  if (!c || !confirm(`Delete path ${c.name} (${c.ids.length} waypoints)?`)) return;
  await run('path_delete', { start }, `path ${c.name} deleted`); await refresh(); await fillPathSelect();
};

// ---- picking for a form: the next click(s) in the 3D view pick a record instead of selecting it ----
let pickMode = null;            // {hint, accept(userData), pick(userData), finish?(), done?(cancelled)}
const patrolPreview = new THREE.Group(); scene3.add(patrolPreview);
function startPick(mode) { pickMode = mode; renderer.domElement.style.cursor = 'crosshair'; status(mode.hint); }
function endPick(cancelled) {
  const m = pickMode; pickMode = null; renderer.domElement.style.cursor = ''; clearGroup(patrolPreview);
  if (cancelled) status('picking cancelled');
  m?.done?.(cancelled);
}
function showLayer(key) {        // make sure a pickable kind is drawn
  if (groups[key].visible) return;
  const row = $('layers').children[LAYERS.findIndex(([k]) => k === key)];
  if (row) { row.querySelector('input').checked = true; row.querySelector('input').onchange({ target: { checked: true } }); }
  else groups[key].visible = true;
}
const objectByName = (name) => data.objects.find((o) => (o.model + o.script_id).toUpperCase() === String(name).toUpperCase());

// ---- cameras: a dashed line to where each one looks; a target camera's look-at point is a box you can drag ----
const camTargets = new Map();   // line -> look-at marker (CAMTARGPOS)
function camLook(c) {
  if (c.target) return toThree(c.target);
  if (c.target_object) {          // CAMTARGOBJ / CAMCHASEOBJ: "<model><script id>" + an offset
    const o = objectByName(c.target_object);
    if (o) return toThree(o.pos.map((v, i) => v + (c.offset?.[i] || 0)));
  }
  if (c.rot) return toThree(c.pos).add(gameForward(c.rot[0], c.rot[1]).multiplyScalar(5));
  return null;
}
function addCameraGuides(c) {
  const eye = toThree(c.pos), look = camLook(c);
  if (!look) return;
  const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([eye, look]),
                              new THREE.LineDashedMaterial({ color: 0xb67be8, dashSize: 0.4, gapSize: 0.25 }));
  line.computeLineDistances(); line.raycast = () => {};
  groups.cameras.add(line);
  if (c.target) {
    const t = new THREE.Mesh(new THREE.BoxGeometry(0.45, 0.45, 0.45), new THREE.MeshBasicMaterial({ color: 0xd9a8ff, wireframe: true }));
    t.position.copy(look);
    t.userData = { line: c.line, cat: 'cameras', camTarget: true,
                   update: () => { line.geometry.dispose(); line.geometry = new THREE.BufferGeometry().setFromPoints([eye, t.position]); line.computeLineDistances(); } };
    pickable.push(t); groups.cameras.add(t); camTargets.set(c.line, t);
  }
}
let camView = null;             // {pos, target, fov, line} of the editor view while looking through a game camera
function enterCamView(c) {
  const look = camLook(c);
  if (!look) return status('the editor can\'t tell where this camera looks', true);
  if (topView) setTopView(false);
  if (!camView) camView = { pos: camera.position.clone(), target: orbit.target.clone(), fov: camera.fov };
  camView.line = c.line;
  camera.position.copy(toThree(c.pos)); orbit.target.copy(look); camera.lookAt(look);
  camera.fov = c.fov || 60; camera.updateProjectionMatrix();   // the game's FOV is vertical, like three.js
  orbit.enabled = false;
  $('camview-name').textContent = `Looking through ${c.kind} "${c.name}" · FOV ${c.fov ?? '?'}°`;
  $('camview').hidden = false; $('cam-look').textContent = '👁 Back to the editor view';
}
function exitCamView() {
  if (!camView) return;
  camera.position.copy(camView.pos); orbit.target.copy(camView.target);
  camera.fov = camView.fov; camera.updateProjectionMatrix();
  camView = null; orbit.enabled = true;
  $('camview').hidden = true; $('cam-look').textContent = '👁 View through camera';
}
$('camview-exit').onclick = exitCamView;
$('cam-look').onclick = () => {
  const line = selection?.line; const c = data.cameras.find((x) => x.line === line);
  if (camView && camView.line === line) exitCamView(); else if (c) enterCamView(c);
};
$('cam-fov').onchange = async () => {
  const line = selection?.line; if (line === undefined) return;
  await run('set_camera', { line, fov: +$('cam-fov').value }, `camera ${line}: FOV ${$('cam-fov').value}°`);
  await refresh();
  const c = data.cameras.find((x) => x.line === line);
  if (camView && c) enterCamView(c);
};

// ---- the inspector's extra sections: Behaviour (soldiers), Camera, Events & Actions (regions, objects) ----
let aiProfiles = null, logicInfo = null;
async function loadLogic() { logicInfo = await api('mission_logic').catch(() => null); return logicInfo; }
async function showInspector(rec) {
  const line = rec.line;
  const o = rec.kind === 'HLOI' ? data.objects.find((x) => x.line === line) : null;
  if (o?.ai_profile) {
    aiProfiles ||= await api('ai_profiles');
    fillProfiles(o.ai_profile);
    $('patrol-info').textContent = o.patrol
      ? `Walks ${o.patrol.waypoints[0].replace(/_\d+$/, '')}: ${o.patrol.waypoints.length} points${o.patrol.loop ? ', loop' : ''}, ${o.patrol.movement || 'walk'}, ${o.patrol.start === 'rule' ? 'when a rule starts it' : 'from the mission start'}`
      : 'No path - "Give a path…" draws or picks one.';
    $('patrol-btn').disabled = false; $('patrol-clear').disabled = !o.patrol;
    $('unit-info').textContent = '';
    $('ai-row').hidden = false;
  }
  const cam = rec.kind?.startsWith('CAM') ? data.cameras.find((c) => c.line === line) : null;
  if (cam) {
    $('cam-fov').value = cam.fov ?? ''; $('cam-fov').disabled = cam.fov === undefined;
    $('cam-info').textContent = cam.target ? 'Looks at a point: select the wireframe box at its end to move it.'
      : cam.target_object ? `Looks at object ${cam.target_object}.` : cam.rot ? `Pitch ${cam.rot[0]}°, yaw ${cam.rot[1]}° (fields p0 / p1).` : '';
    $('cam-look').textContent = camView?.line === line ? '👁 Back to the editor view' : '👁 View through camera';
    $('cam-row').hidden = false;
  }
  if (o && /_VEH_/i.test(o.unit)) showCrew(line).catch(() => {});
  if (rec.kind === 'ENVSND') showSound(rec).catch(() => {});
  if (o && HERO_BANK[String(o.model).slice(0, 6).toUpperCase()]) showVoices(o).catch(() => {});
  if (o) showObjectLogic(o).catch(() => {});
  if (rec.kind === 'SWITCH') await showSwitch(rec);
  if (rec.kind === 'EMITTER') await showEmitter(rec);
  if (['REGN', 'SWITCH', 'NEWENVWAYPT'].includes(rec.kind) || o) await showEvents(rec, o);
}
function interRow(label, el) {
  const row = document.createElement('div'); row.className = 'row';
  const l = document.createElement('label'); l.className = 'inline'; l.textContent = label + ' '; l.appendChild(el);
  row.appendChild(l); $('inter-body').appendChild(row); return el;
}
const dist2 = (p, q) => (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2;
async function refreshKeep() { const line = selection?.line; await refresh(); if (line) await select({ line }); }
async function showSwitch(rec) {
  const opts = await loadInteractive(true); if (!opts) return;
  const f = rec.fields, w = data.switches.find((x) => x.line === rec.line);
  $('inter-title').textContent = 'Switch'; $('inter-body').innerHTML = '';
  const near = [...opts.doors].sort((a, b) => dist2(a.pos, f.pos) - dist2(b.pos, f.pos));
  const doorSel = (cur) => {
    const sel = document.createElement('select'); sel.add(new Option('(none)', -1));
    for (const d of near) sel.add(new Option(`${d.script_id} · ${d.unit} · ${(Math.sqrt(dist2(d.pos, f.pos)) / 100).toFixed(0)} m`, d.script_id));
    if (cur !== -1 && !near.some((d) => d.script_id === cur)) sel.add(new Option(`${cur} (not a door / gone)`, cur));
    sel.value = cur; return sel;
  };
  const a = interRow('Door', doorSel(f.target_a)), b = interRow('Second door', doorSel(f.target_b));
  a.onchange = () => run('switch_set', { line: rec.line, door_a: +a.value }, 'switch door changed').then(refreshKeep);
  b.onchange = () => run('switch_set', { line: rec.line, door_b: +b.value }, 'switch door changed').then(refreshKeep);
  const t = document.createElement('select');
  for (const x of opts.switch_types) t.add(new Option(`${x.model_off} / ${x.model_on}`, x.type));
  t.value = opts.switch_types.find((x) => x.type.toUpperCase() === String(f.type).toUpperCase())?.type || '';
  interRow('Model', t).onchange = () => run('switch_set', { line: rec.line, type: t.value }, 'switch model changed').then(refreshKeep);
  const reg = data.regions.find((r) => r.name === f.region);
  if (reg?.type === 0) {
    const size = document.createElement('input'); size.type = 'number'; size.min = 100; size.max = 4000; size.step = 50; size.style.width = '6em';
    size.value = Math.round(Math.abs(reg.max[0] - reg.min[0]));
    interRow('Use area (cm)', size).onchange = () => run('switch_set', { line: rec.line, use_size: +size.value }, 'use area changed').then(refreshKeep);
  }
  $('inter-info').textContent = (w?.missing.length ? `Missing in this scene: ${w.missing.join(', ')} - the game won't find them. ` : '')
    + `Used inside region ${f.region}. Each use opens / closes the door${f.target_b !== -1 ? 's' : ''} and sends`
    + ` game.EVENT.SWITCH with a = ${w?.message_a} (rules: "When a player uses switch"). The game has no lock or key for these doors.`;
  $('inter-row').hidden = false;
}
async function showEmitter(rec) {
  const opts = await loadInteractive(); if (!opts) return;
  const f = rec.fields, e = data.emitters.find((x) => x.line === rec.line);
  $('inter-title').textContent = 'Particle emitter'; $('inter-body').innerHTML = '';
  const t = document.createElement('select');
  for (const [k, v] of Object.entries(opts.particle_types)) t.add(new Option(`${v} (${k})`, k));
  if (!opts.particle_types[f.type]) t.add(new Option(`type ${f.type}`, f.type));
  t.value = String(f.type);
  interRow('Particles', t).onchange = () => run('emitter_set', { line: rec.line, type: +t.value }, 'particle type changed').then(refreshKeep);
  const on = document.createElement('input'); on.type = 'checkbox'; on.checked = !!(f.flags & 1);
  interRow('On from the start', on).onchange = () => run('emitter_set', { line: rec.line, on: on.checked }, on.checked ? 'emitter on' : 'emitter off').then(refreshKeep);
  const col = document.createElement('input'); col.type = 'color';
  const hex = (v) => Math.round(Math.min(1, Math.max(0, v)) * 255).toString(16).padStart(2, '0');
  col.value = `#${hex(f.colour_r)}${hex(f.colour_g)}${hex(f.colour_b)}`;
  const alpha = document.createElement('input'); alpha.type = 'number'; alpha.min = 0; alpha.max = 1; alpha.step = 0.05;
  alpha.value = f.colour_a; alpha.style.width = '4.5em';
  const setCol = () => {
    const n = parseInt(col.value.slice(1), 16);
    run('emitter_set', { line: rec.line, colour: [(n >> 16 & 255) / 255, (n >> 8 & 255) / 255, (n & 255) / 255, +alpha.value] },
        'emitter colour changed').then(refreshKeep);
  };
  interRow('Colour', col).onchange = setCol; interRow('Opacity', alpha).onchange = setCol;
  if ('region' in f) {
    const r = document.createElement('select'); r.add(new Option('(at its position)', ''));
    for (const n of opts.regions) r.add(new Option(n, n));
    r.value = e?.region || '';
    interRow('Spawn inside region', r).onchange = () => run('emitter_set', { line: rec.line, region: r.value }, 'emitter region changed').then(refreshKeep);
  }
  $('inter-info').textContent = (e?.parent ? `Follows ${e.parent} (its position is relative to it). ` : '')
    + 'Fields above: times in seconds, sizes and speeds in cm - delay_min / max = time between particles, life_min / max, '
    + 'size → end_size_min / max, velocity_min / max. An emitter that is off waits for the mission script.';
  $('inter-row').hidden = false;
}
function fillProfiles(current) {
  const sel = $('ai-profile'); sel.innerHTML = '';
  if (current === 'custom' || !current) sel.add(new Option(current ? 'Custom (its own values)' : '(different)', current || ''));
  for (const [k, p] of Object.entries(aiProfiles)) sel.add(new Option(p.label, k));
  sel.value = current || '';
  $('ai-source').textContent = !current ? 'Pick a profile for all of them.'
    : current === 'custom' ? 'Its AI values match no profile - picking one replaces them.'
      : `Values copied from ${aiProfiles[current].source}.`;
}
$('ai-profile').onchange = async () => {
  const v = $('ai-profile').value;
  if (!aiProfiles[v]) return;
  const lines = many.size ? [...many] : [selection.line];
  const keep = new Set(lines);
  await run('set_ai_profile', { lines, profile: v }, `${lines.length} soldier(s): ${aiProfiles[v].label}`);
  await refresh();
  if (keep.size > 1) selectMany([...keep]);
};
// Paths: each waypoint's link to its next one (the game's patrol routes) as a cyan line with an arrowhead.
const pathArrowGeo = keep(new THREE.ConeGeometry(0.22, 0.6, 8).rotateX(Math.PI / 2));
const pathLineMat = keep(new THREE.LineBasicMaterial({ color: 0x38c7d6, transparent: true, opacity: 0.8 }));
const pathArrowMat = keep(new THREE.MeshBasicMaterial({ color: 0x38c7d6 }));
function drawPathLinks() {
  const byId = new Map(data.waypoints.map((w) => [w.id, w]));
  for (const w of data.waypoints) {
    const n = byId.get(w.next);
    if (!n) continue;
    const a = lift(toThree(w.pos), 0.3), b = lift(toThree(n.pos), 0.3);
    const l = new THREE.Line(new THREE.BufferGeometry().setFromPoints([a, b]), pathLineMat); l.raycast = () => {};
    const tip = new THREE.Mesh(pathArrowGeo, pathArrowMat); tip.raycast = () => {};
    tip.position.copy(a.clone().lerp(b, 0.62)); tip.lookAt(b);
    groups.waypoints.add(l, tip);
  }
}
// Patrol linker: click waypoints in order, Enter stores the route (set_patrol); drawn as a dashed orange loop.
function drawPatrol(o) {
  const pts = o.patrol.waypoints.map((n) => data.waypoints.find((w) => w.name === n)).filter(Boolean).map((w) => lift(toThree(w.pos)));
  if (pts.length < 2) return;
  if (o.patrol.loop) pts.push(pts[0].clone());
  const l = new THREE.Line(new THREE.BufferGeometry().setFromPoints([lift(toThree(o.pos)), ...pts]),
                           new THREE.LineDashedMaterial({ color: 0xffa040, dashSize: 0.6, gapSize: 0.35 }));
  l.computeLineDistances(); l.raycast = () => {};
  groups.waypoints.add(l);
}
$('patrol-btn').onclick = () => openPathsPanel('path').catch(() => {});
$('patrol-clear').onclick = async () => {
  const line = selection?.line; if (line === undefined) return;
  await run('set_patrol', { line, waypoints: [] }, 'patrol removed'); await refresh();
};

// ---- rules (missionrules.py): "when <trigger>, if <conditions>, do <actions>" - cards + an editor ----
const escHtml = (t) => String(t).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
function objTitle(id) { const o = logicInfo?.objectives.find((x) => x.id === id); return o ? `${id} "${o.title}"` : String(id); }
let ruleDraft = null;           // {index: n | null, rule}
let pathList = [];
let apiFns = null;
async function loadPaths() { pathList = (await api('path_list').catch(() => ({ paths: [] }))).paths; return pathList; }
function objLabel(id) { const o = data.objects.find((x) => x.script_id === id); return o ? `${o.unit} #${id}` : `#${id}`; }
function fillDatalists() {
  $('dl-objects').innerHTML = data.objects.map((o) => `<option value="${escHtml(`${o.unit} #${o.script_id}`)}">`).join('');
  const names = new Set();
  for (const r of logicInfo?.rules || []) for (const it of [r.trigger, ...r.conditions, ...r.actions])
    if (it && typeof it.name === 'string') names.add(it.name);
  $('dl-names').innerHTML = [...names].map((n) => `<option value="${escHtml(n)}">`).join('');
}
const idOf = (txt) => { const m = String(txt).match(/#(-?\d+)\s*$/) || String(txt).match(/^\s*(-?\d+)\s*$/); return m ? +m[1] : null; };
function pickObjects(multi, done) {       // close the dialog, click objects in the 3D view, come back
  const dlg = $('objectives-dialog'); dlg.close(); showLayer('objects');
  const picked = [];
  startPick({
    hint: multi ? 'click objects - Enter finishes, Esc cancels' : 'click an object - Esc cancels',
    accept: (u) => u.cat === 'objects',
    pick: (u) => {
      const sid = data.objects.find((x) => x.line === u.line)?.script_id;
      if (sid === undefined) return;
      if (!picked.includes(sid)) picked.push(sid);
      if (!multi) endPick(false); else status(`${picked.map(objLabel).join(', ')} - click more, Enter finishes`);
    },
    finish: multi ? () => endPick(false) : undefined,
    done: (cancelled) => { if (!cancelled) done(picked); openObjectives('rules').then(() => renderRuleEditor()); },
  });
}
function paramEl(p, value, set) {
  const wrap = document.createElement('span'); wrap.className = 'param';
  wrap.append(document.createTextNode(p.label + ' '));
  const t = p.type;
  const sel = (opts, cur) => { const s = document.createElement('select');
    for (const [v, l] of opts) s.add(new Option(l, v)); s.value = cur ?? ''; s.onchange = () => set(s.value); return s; };
  if (t === 'object') {
    const inp = document.createElement('input'); inp.className = 'obj'; inp.setAttribute('list', 'dl-objects');
    inp.placeholder = p.default === -1 ? 'any (leave empty)' : 'type or ◎ pick';
    inp.value = value !== undefined && value !== null && value !== -1 ? objLabel(value) : '';
    inp.onchange = () => { const v = idOf(inp.value); set(v === null ? -1 : v); };
    const pick = document.createElement('button'); pick.type = 'button'; pick.textContent = '◎'; pick.title = 'pick in the 3D view';
    pick.onclick = () => pickObjects(false, (ids) => { if (ids.length) set(ids[0]); });
    wrap.append(inp, pick);
  } else if (t === 'objects') {
    const ids = Array.isArray(value) ? value : [];
    const chips = document.createElement('span'); chips.className = 'chips';
    for (const id of ids) {
      const c = document.createElement('span'); c.className = 'chip'; c.innerHTML = `${escHtml(objLabel(id))}<b title="remove">×</b>`;
      c.querySelector('b').onclick = () => set(ids.filter((x) => x !== id)); chips.append(c);
    }
    const inp = document.createElement('input'); inp.className = 'obj'; inp.setAttribute('list', 'dl-objects'); inp.placeholder = 'add: type or ◎ pick';
    inp.onchange = () => { const v = idOf(inp.value); if (v !== null && !ids.includes(v)) set([...ids, v]); };
    const pick = document.createElement('button'); pick.type = 'button'; pick.textContent = '◎'; pick.title = 'pick several in the 3D view';
    pick.onclick = () => pickObjects(true, (got) => set([...new Set([...ids, ...got])]));
    const fromSel = document.createElement('button'); fromSel.type = 'button'; fromSel.textContent = '+ selected';
    fromSel.title = 'add the objects selected in the 3D view'; fromSel.onclick = () => set([...new Set([...ids, ...selectedScriptIds()])]);
    wrap.append(chips, inp, pick, fromSel);
  } else if (t === 'region') wrap.append(sel([['', '(pick a region)'], ...data.regions.map((r) => [r.name, r.name])], value));
  else if (t === 'switch') wrap.append(sel([['', '(pick a switch)'], ...(data.switches || []).map((w) => [w.name, w.name])], value));
  else if (t === 'waypoint') wrap.append(sel([['', '(pick a waypoint)'], ...data.waypoints.map((w) => [w.id, `${w.name} (${w.id})`])], value));
  else if (t === 'path') wrap.append(sel([['', '(pick a path)'], ...pathList.map((c) => [c.start, `${c.name} - ${c.ids.length} points${c.loop ? ', loop' : ''}`])], value));
  else if (t === 'objective') wrap.append(sel([['', '(pick an objective)'], ...(logicInfo?.objectives || []).map((o) => [o.id, `${o.id}: ${o.title}`])], value));
  else if (t === 'layer') wrap.append(sel([['', '(pick a layer)'], ...(logicInfo?.layers || []).map((l) => [l, l])], value));
  else if (t === 'team') wrap.append(sel(Object.entries(logicInfo.catalog.teams), value));
  else if (t === 'camera') wrap.append(sel([['', '(pick a camera)'], ...data.cameras.map((c) => [c.name, `${c.name} (${c.kind.replace('CAM', '').toLowerCase()})`])], value));
  else if (t === 'wav') { const inp = document.createElement('input'); inp.value = value ?? ''; inp.placeholder = 'radio1.wav (in the mod folder)';
    inp.onchange = () => set(inp.value.trim()); wrap.append(inp); }
  else if (t === 'choice') wrap.append(sel(Object.entries(p.choices), value));
  else if (t === 'rule') wrap.append(sel((logicInfo?.rules || []).map((r, i) => [i + 1, `${i + 1}: ${r.name || logicInfo.rule_texts[i]}`.slice(0, 70)]), value));
  else if (t === 'bool') { const cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = !!value; cb.onchange = () => set(cb.checked); wrap.append(cb); }
  else if (['seconds', 'metres', 'percent', 'number'].includes(t)) {
    const n = document.createElement('input'); n.type = 'number'; n.step = 'any'; n.value = value ?? p.default ?? 0;
    n.onchange = () => set(+n.value); wrap.append(n,
      document.createTextNode({ seconds: ' s', metres: ' m', percent: ' %', number: '' }[t]));
  } else if (t === 'flag' || t === 'counter') {
    const inp = document.createElement('input'); inp.setAttribute('list', 'dl-names'); inp.value = value ?? ''; inp.placeholder = `${t} name`;
    inp.onchange = () => set(inp.value.trim()); wrap.append(inp);
  } else if (t === 'text') { const inp = document.createElement('input'); inp.value = value ?? ''; inp.style.width = '24em'; inp.onchange = () => set(inp.value); wrap.append(inp); }
  else if (t === 'slot') {
    const inp = document.createElement('input'); inp.setAttribute('list', 'dl-slots'); inp.style.width = '22em';
    const f = apiFns?.find((x) => x.slot === value); inp.value = f ? `${f.name} (0x${f.slot.toString(16).toUpperCase()})` : '';
    inp.onchange = () => { const m = inp.value.match(/0x([0-9a-f]+)\)?\s*$/i); if (m) set(parseInt(m[1], 16)); };
    wrap.append(inp);
    if (f) wrap.append(Object.assign(document.createElement('span'), { className: 'hint', textContent: ` ${f.args.map((a) => a.name + ':' + a.type).join(', ')} - ${f.desc.slice(0, 120)}` }));
  } else if (t === 'args') {
    const inp = document.createElement('input'); inp.style.width = '22em'; inp.placeholder = 'e.g. 164, 2.5, "text"';
    inp.value = (value || []).map((v) => typeof v === 'string' ? JSON.stringify(v) : v).join(', ');
    inp.onchange = () => { try { set(JSON.parse(`[${inp.value}]`)); } catch { status('arguments: numbers and "texts", separated by commas', true); } };
    wrap.append(inp);
  } else if (t === 'lua') { const ta = document.createElement('textarea'); ta.value = value ?? ''; ta.onchange = () => set(ta.value); wrap.append(ta); }
  else { const inp = document.createElement('input'); inp.value = value ?? ''; inp.onchange = () => set(inp.value); wrap.append(inp); }
  return wrap;
}
function defaultsFor(spec, type) {
  const out = { type };
  for (const p of spec.params) out[p.name] = p.default === null || p.default === undefined ? (p.type === 'objects' ? [] : undefined) : JSON.parse(JSON.stringify(p.default));
  return out;
}
function itemRow(table, item, onChange, onRemove, extra) {
  const row = document.createElement('div'); row.className = 'item';
  const typeSel = document.createElement('select');
  const groups = {};
  for (const [k, spec] of Object.entries(table)) (groups[spec.group || ''] ||= []).push([k, spec.label]);
  for (const [g, list] of Object.entries(groups)) {
    const parent = g ? Object.assign(document.createElement('optgroup'), { label: g }) : typeSel;
    for (const [k, l] of list) parent.appendChild(new Option(l, k));
    if (g) typeSel.appendChild(parent);
  }
  typeSel.value = item.type;
  typeSel.onchange = () => onChange(defaultsFor(table[typeSel.value], typeSel.value));
  row.append(typeSel);
  if (extra) row.append(extra);
  for (const p of table[item.type].params) {
    if (p.show_if && Object.entries(p.show_if).some(([k, v]) => item[k] !== v)) continue;   // e.g. object only for "this object"
    row.append(paramEl(p, item[p.name], (v) => { item[p.name] = v; onChange(item, true); }));
  }
  if (table[item.type].help) row.append(Object.assign(document.createElement('span'), { className: 'hint', textContent: table[item.type].help }));
  if (onRemove) { const x = document.createElement('button'); x.type = 'button'; x.className = 'x'; x.textContent = '×'; x.title = 'remove'; x.onclick = onRemove; row.append(x); }
  return row;
}
function renderRuleEditor() {
  const box = $('ob-rule-form');
  if (!ruleDraft) { box.hidden = true; box.innerHTML = ''; return; }
  const cat = logicInfo.catalog, r = ruleDraft.rule;
  fillDatalists();
  box.hidden = false; box.innerHTML = '';
  const head = document.createElement('div'); head.className = 'row';
  const name = Object.assign(document.createElement('input'), { value: r.name || '', placeholder: 'Rule name (optional)' });
  name.style.width = '20em'; name.onchange = () => { r.name = name.value; };
  const once = document.createElement('label'); once.className = 'check';
  once.innerHTML = `<input type="checkbox" ${r.once ? 'checked' : ''}> only the first time`;
  once.querySelector('input').onchange = (e) => { r.once = e.target.checked; };
  head.append(Object.assign(document.createElement('strong'), { textContent: ruleDraft.index === null ? 'New rule ' : `Rule ${ruleDraft.index + 1} ` }), name, once);
  box.append(head);
  const redraw = () => renderRuleEditor();
  box.append(Object.assign(document.createElement('h3'), { textContent: 'When' }));
  box.append(itemRow(cat.triggers, r.trigger, (it, inPlace) => { if (!inPlace) r.trigger = it; redraw(); }, null));
  box.append(Object.assign(document.createElement('h3'), { textContent: 'Only if (all of these)' }));
  r.conditions.forEach((c, k) => {
    const neg = document.createElement('label'); neg.className = 'check'; neg.innerHTML = `<input type="checkbox" ${c.not ? 'checked' : ''}> not`;
    neg.querySelector('input').onchange = (e) => { c.not = e.target.checked; };
    box.append(itemRow(cat.conditions, c, (it, inPlace) => { if (!inPlace) r.conditions[k] = it; redraw(); },
                       () => { r.conditions.splice(k, 1); redraw(); }, neg));
  });
  const addC = Object.assign(document.createElement('button'), { type: 'button', textContent: '+ condition' });
  addC.onclick = () => { r.conditions.push(defaultsFor(cat.conditions.alive, 'alive')); redraw(); };
  box.append(addC);
  box.append(Object.assign(document.createElement('h3'), { textContent: 'Do (in order)' }));
  r.actions.forEach((a, k) => {
    const mv = document.createElement('span');
    for (const [txt, d] of [['↑', -1], ['↓', 1]]) {
      const b = Object.assign(document.createElement('button'), { type: 'button', textContent: txt });
      b.onclick = () => { const n = k + d; if (n < 0 || n >= r.actions.length) return; r.actions.splice(n, 0, r.actions.splice(k, 1)[0]); redraw(); };
      mv.append(b);
    }
    box.append(itemRow(cat.actions, a, (it, inPlace) => { if (!inPlace) r.actions[k] = it; redraw(); },
                       () => { r.actions.splice(k, 1); redraw(); }, mv));
  });
  const addA = Object.assign(document.createElement('button'), { type: 'button', textContent: '+ action' });
  addA.onclick = () => { r.actions.push(defaultsFor(cat.actions.message, 'message')); redraw(); };
  const addW = Object.assign(document.createElement('button'), { type: 'button', textContent: '+ wait' });
  addW.onclick = () => { r.actions.push({ type: 'wait', seconds: 2 }); redraw(); };
  box.append(addA, addW);
  const foot = document.createElement('div'); foot.className = 'foot';
  const ok = Object.assign(document.createElement('button'), { type: 'button', textContent: ruleDraft.index === null ? 'Add rule' : 'Save rule' });
  const cancel = Object.assign(document.createElement('button'), { type: 'button', textContent: 'Cancel' });
  const err = Object.assign(document.createElement('span'), { className: 'err' });
  ok.onclick = async () => {
    try {
      if (ruleDraft.index === null) await api('add_rule', { rule: r });
      else await api('update_rule', { index: ruleDraft.index, rule: r });
      status(ruleDraft.index === null ? 'rule added' : 'rule saved');
      ruleDraft = null; await loadLogic(); refreshLogicViews();
    } catch (e) { err.textContent = e.message; }
  };
  cancel.onclick = () => { ruleDraft = null; renderRuleEditor(); renderRuleCards(); };
  foot.append(ok, cancel, err);
  box.append(foot);
  box.scrollIntoView({ block: 'nearest' });
}
function newRule(trigger) {
  const cat = logicInfo.catalog;
  return { name: '', enabled: true, once: true, trigger: trigger || defaultsFor(cat.triggers.region_enter, 'region_enter'),
           conditions: [], actions: [defaultsFor(cat.actions.message, 'message')] };
}
function ruleRefs(r, rec) {             // does the rule name this record?
  const items = [r.trigger, ...r.conditions, ...r.actions];
  if (rec.kind === 'REGN') return items.some((it) => it.region === rec.fields.name);
  if (rec.kind === 'SWITCH') return items.some((it) => it.switch === rec.fields.name);
  if (rec.kind === 'NEWENVWAYPT') return items.some((it) => it.waypoint === rec.fields.id || it.path === rec.fields.id);
  const sid = rec.fields.script_id;
  return items.some((it) => Object.values(it).some((v) => v === sid || (Array.isArray(v) && v.includes(sid))));
}
function ruleCard(r, i, compact) {
  const card = document.createElement('div'); card.className = 'rule-card' + (r.enabled === false ? ' off' : '') +
    (ruleDraft?.index === i ? ' sel' : '');
  const text = logicInfo.rule_texts?.[i] || '';
  if (!compact) {
    const cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = r.enabled !== false; cb.title = 'rule on / off';
    cb.onchange = async () => { await run('update_rule', { index: i, rule: { ...r, enabled: cb.checked } }, cb.checked ? 'rule on' : 'rule off'); await loadLogic(); refreshLogicViews(); };
    card.append(cb);
  }
  const body = document.createElement('div');
  body.innerHTML = `<div class="rn">${escHtml(r.name || `Rule ${i + 1}`)}${r.once ? '' : ' <span class="hint">(every time)</span>'}</div><div class="rt">${escHtml(text)}</div>`;
  card.append(body);
  const btns = document.createElement('div'); btns.className = 'rb';
  const b = (txt, title, fn) => { const x = Object.assign(document.createElement('button'), { type: 'button', textContent: txt, title }); x.onclick = fn; btns.append(x); };
  b('Edit', 'edit this rule', () => { openObjectives('rules').then(() => { ruleDraft = { index: i, rule: JSON.parse(JSON.stringify(r)) }; renderRuleCards(); renderRuleEditor(); }); });
  if (!compact) {
    b('⧉', 'duplicate', async () => { await run('add_rule', { rule: { ...r, name: (r.name || `Rule ${i + 1}`) + ' copy' } }, 'rule copied'); await loadLogic(); refreshLogicViews(); });
    b('↑', 'move up', async () => { if (i > 0) { await run('move_rule', { index: i, to: i - 1 }); await loadLogic(); refreshLogicViews(); } });
    b('↓', 'move down', async () => { if (i < logicInfo.rules.length - 1) { await run('move_rule', { index: i, to: i + 1 }); await loadLogic(); refreshLogicViews(); } });
  }
  b('×', 'delete', async () => { if (!confirm(`Delete "${r.name || `rule ${i + 1}`}"?`)) return; await run('delete_rule', { index: i }, 'rule deleted'); ruleDraft = null; await loadLogic(); refreshLogicViews(); });
  card.append(btns);
  return card;
}
function renderRuleCards() {
  const box = $('ob-rules'); box.innerHTML = '';
  const q = $('rule-filter').value.trim().toLowerCase();
  (logicInfo?.rules || []).forEach((r, i) => {
    if (q && !`${r.name} ${logicInfo.rule_texts[i]}`.toLowerCase().includes(q)) return;
    box.append(ruleCard(r, i, false));
  });
  if (!box.children.length) box.innerHTML = '<p class="hint">No rules yet - "+ New rule" makes one: pick when it fires, optional conditions, and what it does.</p>';
}
$('rule-filter').oninput = () => renderRuleCards();
$('rule-new').onclick = () => { ruleDraft = { index: null, rule: newRule() }; renderRuleCards(); renderRuleEditor(); };
async function prepareRules() {
  await loadPaths();
  if (!apiFns) { apiFns = (await api('api_functions').catch(() => ({ functions: [] }))).functions;
    $('dl-slots').innerHTML = apiFns.map((f) => `<option value="${escHtml(`${f.name} (0x${f.slot.toString(16).toUpperCase()})`)}">`).join(''); }
}
async function showEvents(rec, o) {
  const info = await loadLogic();         // fresh: layers / objectives / regions may have changed since
  if (!info) return;
  const box = $('ev-list'); box.innerHTML = '';
  info.rules.forEach((r, i) => { if (ruleRefs(r, rec)) box.append(ruleCard(r, i, true)); });
  const goals = info.objectives.filter((g) => rec.kind === 'REGN' ? g.type === 'region' && g.target === rec.fields.name
    : rec.kind === 'HLOI' && g.type === 'kill' && [].concat(g.target).includes(rec.fields.script_id));
  if (goals.length) box.insertAdjacentHTML('afterbegin', goals.map((g) =>
    `<p class="hint">Objective ${g.id} "${escHtml(g.title)}" completes ${rec.kind === 'REGN' ? 'when a player gets here' : 'when this is killed / destroyed'}.</p>`).join(''));
  if (!box.children.length) box.innerHTML = '<p class="hint">No rule uses this yet.</p>';
  $('ev-new').onclick = () => {
    const cat = info.catalog;
    const trig = rec.kind === 'REGN' ? { ...defaultsFor(cat.triggers.region_enter, 'region_enter'), region: rec.fields.name }
      : rec.kind === 'SWITCH' ? { type: 'switch', switch: rec.fields.name }
        : rec.kind === 'NEWENVWAYPT' ? { ...defaultsFor(cat.triggers.waypoint, 'waypoint'), waypoint: rec.fields.id }
          : { type: 'killed', objects: [rec.fields.script_id], mode: 'any' };
    openObjectives('rules').then(() => { ruleDraft = { index: null, rule: newRule(trig) }; renderRuleCards(); renderRuleEditor(); });
  };
  $('events-row').hidden = false;
}
function refreshLogicViews() {            // after a rule / objective change: the inspector and the dialog
  if (selection?.line !== undefined && !selection.target && !$('events-row').hidden)
    api('get_record', { line: selection.line }).then((r) => showEvents(r, null)).catch(() => {});
  if ($('objectives-dialog').open) { renderRules(); renderLuaTab(); }
  updateHistory();
}

// ---- Objectives dialog: objectives (add / edit / reorder / delete, saved together), all rules, the generated Lua ----
const OB_TYPES = { kill: 'Kill / destroy', region: 'Reach a region', timer: 'Survive (seconds)', manual: 'A rule or script says so' };
let obState = null;
function showObTab(tab) {
  for (const t of ['obj', 'flow', 'rules', 'lua']) {
    $(`ob-tab-${t}`).classList.toggle('on', t === tab); $(`ob-pane-${t}`).hidden = t !== tab;
  }
}
for (const t of ['obj', 'flow', 'rules', 'lua']) $(`ob-tab-${t}`).onclick = () => showObTab(t);
async function openObjectives(tab = 'obj') {
  if (!level) return;
  const info = await loadLogic();
  if (!info) return status('open a level first', true);
  obState = { objectives: info.objectives.map((o) => ({ ...o })), win: info.win_when_all_done,
              base: (info.base_objectives || []).map((o) => ({ ...o })) };
  $('ob-win').checked = obState.win;
  renderObjectives(); renderRules(); renderLuaTab(); showObTab(tab);
  renderFlow().catch(() => {});
  if (!$('objectives-dialog').open) $('objectives-dialog').showModal();
}
$('objectives-btn').onclick = () => openObjectives().catch(() => {});
$('ob-close').onclick = () => $('objectives-dialog').close();
function selectedScriptIds() {
  const lines = many.size ? [...many] : selection?.line !== undefined ? [selection.line] : [];
  return lines.map((l) => data.objects.find((o) => o.line === l)?.script_id).filter((x) => x !== undefined);
}
function pickForObjective(o, kind) {      // close the dialog, pick in the 3D view, come back
  $('objectives-dialog').close();
  if (kind !== 'region') showLayer('objects');
  if (kind !== 'kill') showLayer('regions');
  const picked = kind === 'kill' ? [].concat(o.target ?? []).filter((x) => x !== '' && x !== null) : [];
  startPick({
    hint: kind === 'kill' ? 'click the objects to kill / destroy - Enter finishes, Esc cancels'
      : kind === 'region' ? 'click a region (yellow) - Esc cancels'
        : 'click an object (done when it is killed / destroyed) or a region (done when a player gets there) - Esc cancels',
    accept: (u) => (kind === 'kill' ? u.cat === 'objects' : kind === 'region' ? u.cat === 'regions' : ['objects', 'regions'].includes(u.cat)),
    pick: (u) => {
      if (kind === 'any') {                      // the pick decides the objective's type
        if (u.cat === 'regions') { o.type = 'region'; o.target = data.regions.find((x) => x.line === u.line)?.name; }
        else { o.type = 'kill'; o.target = data.objects.find((x) => x.line === u.line)?.script_id; }
        endPick(false);
      } else if (kind === 'kill') {
        const sid = data.objects.find((x) => x.line === u.line)?.script_id;
        if (sid !== undefined && !picked.includes(sid)) picked.push(sid);
        status(`targets: ${picked.join(', ')} - click more, Enter finishes`);
      } else { o.target = data.regions.find((x) => x.line === u.line)?.name; endPick(false); }
    },
    finish: kind === 'kill' ? () => { o.target = picked.length === 1 ? picked[0] : picked; endPick(false); } : undefined,
    done: () => { renderObjectives(); $('objectives-dialog').showModal(); },
  });
}
// The campaign objectives' targets, read from the base mission's logic (mission_flow): every "Complete objective" step
// with the game event it runs in and the objects / regions named on the way to it. Read-only - the DLL decides.
let obBaseTargets = null;          // {title: {events: Set, refs: Map(key -> ref)}}
async function loadBaseTargets() {
  const f = await loadFlow();
  const out = {};
  const walk = (n, anc, ev) => {
    if (n.text[0] === 'Complete objective') {
      const g = n.text.find((x) => typeof x === 'object');
      const title = g?.text.match(/“(.*)”/)?.[1];
      if (title) {
        const t = (out[title] ||= { events: new Set(), refs: new Map() });
        t.events.add(ev);
        for (const a of anc) for (const x of a.text)
          if (typeof x === 'object' && ['object', 'region', 'waypoint'].includes(x.ref)) t.refs.set(`${x.ref}:${x.id}`, x);
      }
    }
    for (const c of n.children) walk(c, [...anc, n], ev);
  };
  for (const ev of f?.events || []) for (const n of ev.nodes) walk(n, [], ev.title);
  obBaseTargets = out;
  return out;
}
function baseTargetWhen(o) {
  if (o.si) {                    // Global Storm: the objective is data in the level's .SI
    const pr = { primary: 'primary', secondary: 'bonus', script: 'script helper (never shown)' }[o.si.priority] || o.si.priority;
    return `<span class="when-done" title="${escHtml(o.si.name)} in the level's .SI">Completed when ${escHtml(o.si.when)}`
      + ` · ${escHtml(pr)}${o.si.status === 'active' ? ' · shown from the start' : ''}${o.si.xp ? ` · ${o.si.xp} XP` : ''}</span>`;
  }
  const t = obBaseTargets?.[o.original_title || o.title];
  return t ? `<span class="when-done" title="where the base mission completes it">Completed: ${escHtml([...t.events].join('; ').toLowerCase())}</span>` : '';
}
function baseTargetHtml(o) {
  if (o.si) {
    if (!o.si.refs.length) return '<span class="hint">—</span>';
    return o.si.refs.map((r) => r.ref === 'missing'
      ? `<span class="hint" title="not in this scene">${escHtml(r.text)}</span>`
      : `<a class="ref ${r.ref}" data-ref="${r.ref}" data-id="${r.id}" title="select it in the view">${escHtml(r.text)}</a>`).join('')
      + '<br><span class="hint">(level .SI, read-only)</span>';
  }
  if (!obBaseTargets) return '<span class="hint">reading the mission logic…</span>';
  const t = obBaseTargets[o.original_title || o.title];
  const refs = t ? [...t.refs.values()] : [];
  if (!refs.length) return '<span class="hint" title="Set and completed by the base mission\'s own script - not editable here">— (campaign script)</span>';
  return refs.slice(0, 6).map((r) => `<a class="ref ${r.ref}" data-ref="${r.ref}" data-id="${r.id}" title="select it in the view">${escHtml(r.text)}</a>`).join('')
    + (refs.length > 6 ? `<span class="hint">+${refs.length - 6} more</span>` : '')
    + '<br><span class="hint">(campaign script, read-only)</span>';
}
function renderObjectives() {
  if (!obBaseTargets && obState.base.length) loadBaseTargets().then(() => { if ($('objectives-dialog').open) renderObjectives(); }).catch(() => { obBaseTargets = {}; });
  const tb = $('ob-table').querySelector('tbody'); tb.innerHTML = '';
  for (const o of obState.base) {         // the base mission's own: its DLL adds, shows and completes them
    const tr = document.createElement('tr'); tr.className = 'base-ob' + (o.hidden ? ' off' : '');
    tr.innerHTML = `<td>${o.id}</td>
      <td class="title-cell"><span class="badge campaign" title="Added and completed by the base mission (${escHtml(o.key || 'its DLL')})">Campaign Objective</span>
        <input value="${escHtml(o.title)}" maxlength="120"${o.title_editable ? '' : ' disabled title="not one of the level&#39;s own texts - it can&#39;t be renamed"'}></td>
      <td style="min-width:360px"><span class="desc">${escHtml(o.description || '')}</span> ${baseTargetWhen(o)}</td>
      <td class="tgt">${baseTargetHtml(o)}</td>
      <td><label class="inline" title="off = the script hides it whenever the base mission shows it"><input type="checkbox"${o.hidden ? '' : ' checked'}> on</label></td>
      <td><button title="back to the game's title, on">↺</button></td>`;
    for (const a of tr.querySelectorAll('.tgt a.ref')) a.onclick = () => {
      const id = +a.dataset.id;
      const line = a.dataset.ref === 'region' ? data.regions[id]?.line : a.dataset.ref === 'waypoint' ? data.waypoints[id]?.line : lineOfScript(id);
      if (line !== undefined) { $('objectives-dialog').close(); select({ line }, true); }
    };
    const [titleIn, onIn] = tr.querySelectorAll('input');
    titleIn.oninput = () => { o.title = titleIn.value; };
    onIn.onchange = () => { o.hidden = !onIn.checked; tr.classList.toggle('off', o.hidden); };
    tr.querySelector('button').onclick = () => { o.title = o.original_title; o.hidden = false; renderObjectives(); };
    tb.appendChild(tr);
  }
  if (!obState.objectives.length) {
    tb.insertAdjacentHTML('beforeend', `<tr><td colspan="6" class="hint">${obState.base.length
      ? 'No objectives of your own yet - + Add objective adds secondary ones after the campaign objectives.'
      : 'No objectives yet - + Add objective.'}</td></tr>`);
  }
  obState.objectives.forEach((o, i) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td><input type="number" min="0" max="63" value="${o.id}" title="the editor's number for this objective (rules use it); the game hands out its own index when the mission runs"></td>
      <td><input value="${escHtml(o.title)}" maxlength="120" placeholder="e.g. Destroy the SCUD launcher"></td>
      <td><select>${Object.entries(OB_TYPES).map(([k, v]) => `<option value="${k}"${k === o.type ? ' selected' : ''}>${v}</option>`).join('')}</select></td>
      <td class="tgt"></td>
      <td><select><option value="active"${o.start === 'active' ? ' selected' : ''}>shown</option><option value="hidden"${o.start === 'hidden' ? ' selected' : ''}>hidden</option></select></td>
      <td><button title="up">↑</button><button title="down">↓</button><button title="delete">×</button></td>`;
    const [idIn, titleIn] = tr.querySelectorAll('input');
    const typeSel = tr.children[2].querySelector('select'), startSel = tr.children[4].querySelector('select');
    idIn.onchange = () => { o.id = +idIn.value; };
    titleIn.oninput = () => { o.title = titleIn.value; };
    typeSel.onchange = () => { o.type = typeSel.value; o.target = o.type === 'timer' ? 60 : o.type === 'region' ? logicInfo.regions[0] : null; renderObjectives(); };
    startSel.onchange = () => { o.start = startSel.value; };
    const [up, down, del] = tr.children[5].querySelectorAll('button');
    up.onclick = () => { if (i) { obState.objectives.splice(i - 1, 0, obState.objectives.splice(i, 1)[0]); renderObjectives(); } };
    down.onclick = () => { if (i < obState.objectives.length - 1) { obState.objectives.splice(i + 1, 0, obState.objectives.splice(i, 1)[0]); renderObjectives(); } };
    del.onclick = () => { obState.objectives.splice(i, 1); renderObjectives(); };
    const cell = tr.querySelector('.tgt');
    if (o.type === 'kill') {
      const inp = document.createElement('input'); inp.placeholder = 'script ids, e.g. 120, 121';
      inp.value = [].concat(o.target ?? []).join(', ');
      inp.onchange = () => { const ids = inp.value.split(/[\s,]+/).filter(Boolean).map(Number); o.target = ids.length === 1 ? ids[0] : ids; };
      const pick = document.createElement('button'); pick.textContent = '◎ Pick'; pick.title = 'Pick in the 3D view: click the targets';
      pick.onclick = () => pickForObjective(o, 'kill');
      cell.append(inp, pick);
    } else if (o.type === 'region') {
      const sel = document.createElement('select');
      for (const n of logicInfo.regions) sel.add(new Option(n, n));
      sel.value = o.target ?? ''; sel.onchange = () => { o.target = sel.value; };
      const pick = document.createElement('button'); pick.textContent = '◎ Pick'; pick.title = 'Pick in the 3D view: click a region';
      pick.onclick = () => pickForObjective(o, 'region');
      cell.append(sel, pick);
    } else if (o.type === 'timer') {
      const inp = document.createElement('input'); inp.type = 'number'; inp.min = 1; inp.value = o.target ?? 60;
      inp.onchange = () => { o.target = +inp.value; };
      cell.append(inp, Object.assign(document.createElement('span'), { className: 'hint', textContent: 's' }));
    } else {
      cell.innerHTML = '<span class="hint">a rule completes it</span>';
      const pick = document.createElement('button'); pick.textContent = '◎ Pick';
      pick.title = 'Pick its target in the 3D view: an object (kill / destroy) or a region (reach it)';
      pick.onclick = () => pickForObjective(o, 'any');
      cell.append(' ', pick);
    }
    tb.appendChild(tr);
  });
  $('ob-warn').textContent = (logicInfo?.warnings || []).join('\n');
}
$('ob-add').onclick = () => {
  const used = new Set(obState.objectives.map((o) => o.id));
  let id = logicInfo.first_free_index; while (used.has(id)) id++;
  const ids = selectedScriptIds();
  obState.objectives.push(ids.length ? { id, title: '', type: 'kill', target: ids.length === 1 ? ids[0] : ids, start: 'active' }
                                     : { id, title: '', type: 'manual', target: null, start: 'active' });
  renderObjectives();
  $('ob-table').querySelector('tbody tr:last-child td:nth-child(2) input')?.focus();
};
$('ob-win').onchange = () => { obState.win = $('ob-win').checked; };
$('ob-save').onclick = async () => {
  try {
    if (obState.base.length) {
      await run('set_base_objectives', { objectives: obState.base.map((o) => ({
        id: o.id, title: o.title === o.original_title ? '' : o.title, hidden: !!o.hidden })) });
    }
    await run('set_objectives', { objectives: obState.objectives, win_when_all_done: obState.win },
              `${obState.objectives.length} objective(s) saved - written into the script with the mission`);
    await loadLogic(); obState.objectives = logicInfo.objectives.map((o) => ({ ...o }));
    obState.base = (logicInfo.base_objectives || []).map((o) => ({ ...o }));
    renderObjectives(); renderRules(); renderLuaTab(); updateHistory();
  } catch (e) { $('ob-warn').textContent = e.message; }
};
function renderRules() {
  prepareRules().then(() => { renderRuleCards(); renderRuleEditor(); }).catch(() => {});
}
function renderLuaTab() {
  $('ob-lua').textContent = logicInfo?.lua || '';
  $('ob-warn').textContent = (logicInfo?.warnings || []).join('\n');
}

// ---- Atmosphere: the scene's time of day, colour keys, fog and sun (get_atmosphere / set_atmosphere), previewed
// in the 3D view while the panel is open ----
let atmos = null, atmosDefault = null;
const toHex = (rgba) => '#' + rgba.slice(0, 3).map((v) => Math.round(Math.min(1, Math.max(0, v)) * 255).toString(16).padStart(2, '0')).join('');
const fromHex = (h) => [1, 3, 5].map((i) => +(parseInt(h.slice(i, i + 2), 16) / 255).toFixed(6)).concat(1);
const keyForHour = (h) => (h < 5 || h >= 20 ? 'night' : h < 9 ? 'dawn' : h < 16.5 ? 'noon' : 'dusk');
const ATMOS_COLOURS = ['SUNDIFCOL', 'SUNAMBCOL', 'FOGCOL', 'SKYCOL'];
async function openAtmos() {
  atmos = await run('get_atmosphere');
  atmosDefault ||= { bg: scene3.background.clone(), fog: scene3.fog, sky: hemi.color.clone(), ground: hemi.groundColor.clone(),
                     hemiI: hemi.intensity, sun: sun.color.clone(), sunDir: sunDir.clone() };
  $('at-hour').value = atmos.time_of_day ?? 12;
  $('at-key').value = keyForHour(+$('at-hour').value);
  $('at-fog-start').value = Math.round((atmos.fog_start ?? 5000) / 100);
  $('at-fog-stop').value = Math.round((atmos.fog_stop ?? 8000) / 100);
  const s = atmos.sun;
  for (const id of ['at-sun-colour', 'at-sun-tilt', 'at-sun-heading']) $(id).disabled = !s;
  $('at-sun-name').textContent = s ? `Sun record "${s.name}" (line ${s.line})` : 'No sun record in this scene';
  if (s) { $('at-sun-colour').value = toHex(s.colour); $('at-sun-tilt').value = s.tilt; $('at-sun-heading').value = s.heading; }
  fillAtmosColours(); atmosLabels(); fillSky();
  $('atmos').hidden = false; previewAtmos();
  loadMusic().catch(() => {});
}
function fillAtmosColours() {
  for (const k of ATMOS_COLOURS) {
    const v = atmos.colours?.[k]?.[$('at-key').value];
    $('at-' + k).disabled = !v; if (v) $('at-' + k).value = toHex(v);
  }
}
function atmosLabels() {
  const h = +$('at-hour').value;
  $('at-hour-v').textContent = `${String(Math.floor(h)).padStart(2, '0')}:${String(Math.round((h % 1) * 60)).padStart(2, '0')}`;
  $('at-fog-start-v').textContent = `${$('at-fog-start').value} m`; $('at-fog-stop-v').textContent = `${$('at-fog-stop').value} m`;
}
function restoreAtmos() {
  if (!atmosDefault) return;
  scene3.background = atmosDefault.bg.clone(); scene3.fog = atmosDefault.fog;
  hemi.color.copy(atmosDefault.sky); hemi.groundColor.copy(atmosDefault.ground); hemi.intensity = atmosDefault.hemiI;
  sun.color.copy(atmosDefault.sun); sunDir.copy(atmosDefault.sunDir);
}
function previewAtmos() {
  if ($('atmos').hidden || !$('at-preview').checked) return restoreAtmos();
  const col = (id) => new THREE.Color($(id).value);
  const start = +$('at-fog-start').value, stop = Math.max(start + 1, +$('at-fog-stop').value);
  scene3.fog = new THREE.Fog(col('at-FOGCOL'), start, stop);         // metres, like the scene's cm / 100
  scene3.background = col('at-SKYCOL');
  hemi.color.copy(col('at-SUNAMBCOL')); hemi.groundColor.copy(col('at-SUNAMBCOL')).multiplyScalar(0.55); hemi.intensity = 2.4;
  sun.color.copy(col('at-SUNDIFCOL'));
  if (atmos?.sun) sunDir.copy(gameForward(+$('at-sun-tilt').value, +$('at-sun-heading').value)).multiplyScalar(-1).normalize();
}
async function commitAtmos(args, what) {
  try { atmos = await run('set_atmosphere', args, what); updateHistory(); buildVision().catch(() => {}); }
  catch { await openAtmos(); }                                       // refused: back to the scene's values
}
$('atmos-btn').onclick = () => { if ($('atmos').hidden) openAtmos().catch(() => {}); else { $('atmos').hidden = true; restoreAtmos(); } };
$('at-close').onclick = () => { $('atmos').hidden = true; restoreAtmos(); };
$('at-preview').onchange = previewAtmos;
$('at-key').onchange = () => { fillAtmosColours(); previewAtmos(); };
$('at-hour').oninput = atmosLabels;
$('at-hour').onchange = () => {
  $('at-key').value = keyForHour(+$('at-hour').value); fillAtmosColours(); previewAtmos();
  commitAtmos({ time_of_day: +$('at-hour').value }, `time of day ${$('at-hour-v').textContent}`);
};
for (const k of ATMOS_COLOURS) {
  $('at-' + k).oninput = previewAtmos;
  $('at-' + k).onchange = () => commitAtmos({ colours: { [k]: { [$('at-key').value]: fromHex($('at-' + k).value) } } },
                                            `${$('at-' + k).parentElement.textContent.trim()} at ${$('at-key').value} changed`);
}
for (const id of ['at-fog-start', 'at-fog-stop']) {
  $(id).oninput = () => { atmosLabels(); previewAtmos(); };
  $(id).onchange = () => {
    let a = +$('at-fog-start').value, b = +$('at-fog-stop').value;
    if (a >= b) { if (id === 'at-fog-start') b = a + 1; else a = Math.max(0, b - 1); $('at-fog-start').value = a; $('at-fog-stop').value = b; atmosLabels(); }
    commitAtmos({ fog_start: a * 100, fog_stop: b * 100 }, `fog ${a}-${b} m`);
  };
}
$('at-sun-colour').oninput = previewAtmos;
$('at-sun-colour').onchange = () => commitAtmos({ sun_colour: fromHex($('at-sun-colour').value) }, 'sun colour changed');
for (const id of ['at-sun-tilt', 'at-sun-heading']) {
  $(id).oninput = previewAtmos;
  $(id).onchange = () => commitAtmos({ sun_tilt: +$('at-sun-tilt').value, sun_heading: +$('at-sun-heading').value }, 'sun turned');
}

// ==== Level dressing and checking (editor/studio.py): shadows, the AI walk map, the tactical map picture, prefabs,
// vehicle crews, sound audition. ====
const INSPECTOR_BOXES = ['vanilla-row', 'kit-row', 'ai-row', 'cam-row', 'events-row', 'crew-row', 'sound-row', 'inter-row', 'logic-row'];

// ---- shadows: every solid model casts and receives; the sun's shadow box is centred on the orbit target ----
function shadowed(obj) {
  obj.traverse((c) => {
    if (!c.isMesh) return;
    const mats = Array.isArray(c.material) ? c.material : [c.material];
    if (mats.some((m) => !m || m.isMeshBasicMaterial)) return;
    c.castShadow = !mats.some((m) => m.transparent && m.opacity < 1);    // see-through "waits for the script" models don't
    c.receiveShadow = true;
  });
  return obj;
}
const shadowCentre = new THREE.Vector3();
let shadowExtent = 0;
function updateSunShadow(centre = orbit.target, extent = null) {
  if (!renderer.shadowMap.enabled) return;
  const e = extent ?? THREE.MathUtils.clamp(camera.position.distanceTo(orbit.target) * 1.5, 30, 700);   // metres
  const step = (2 * e) / sun.shadow.mapSize.width;            // one shadow texel: snapping stops shimmer while flying
  shadowCentre.set(Math.round(centre.x / step) * step, centre.y, Math.round(centre.z / step) * step);
  sun.target.position.copy(shadowCentre);
  sun.position.copy(shadowCentre).addScaledVector(sunDir, e * 2 + 300);
  sun.target.updateMatrixWorld();
  if (extent !== null || Math.abs(e - shadowExtent) > e * 0.05) {
    shadowExtent = extent !== null ? 0 : e;                    // a forced extent (minimap) is re-fitted next frame
    const cam = sun.shadow.camera;
    cam.left = cam.bottom = -e; cam.right = cam.top = e; cam.near = 1; cam.far = e * 4 + 900;
    cam.updateProjectionMatrix();
  }
}
function setShadows(on) {
  renderer.shadowMap.enabled = on; sun.castShadow = on; requestRender();
  scene3.traverse((o) => { if (o.material) for (const m of [].concat(o.material)) m.needsUpdate = true; });
}
try { $('shadows').checked = localStorage.getItem('shadows') !== '0'; } catch { /* storage blocked */ }
setShadows($('shadows').checked);
$('shadows').onchange = () => {
  setShadows($('shadows').checked);
  try { localStorage.setItem('shadows', $('shadows').checked ? '1' : '0'); } catch { /* ignore */ }
};

// ---- AI walk map: the level's quadtree (walkable green, blocked red; cells over 20 m as red outlines) and the
// footprints of props / kit pieces / vehicles added since - the game rebuilds the map from the scene and cuts those out ----
const walkGroup = new THREE.Group(); walkGroup.visible = false; scene3.add(walkGroup);
const walkFoot = new THREE.Group(); walkGroup.add(walkFoot);
{
  const row = document.createElement('label');
  row.title = "The game's AI walk map: green = soldiers can walk there, red = blocked. Red boxes: what you added that the game's rebuild will cut out.";
  row.innerHTML = '<input type="checkbox" id="walk-toggle"> <span class="swatch" style="background:#3fd06b"></span>AI walk map<span class="count" id="count-walk"></span>';
  $('layers').appendChild(row);
}
let walkLevel = null;
const flatPlane = keep(new THREE.PlaneGeometry(1, 1).rotateX(-Math.PI / 2));
const overlayMat = (color, opacity) => new THREE.MeshBasicMaterial({ color, transparent: true, opacity, depthWrite: false,
  side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 });
async function buildWalkMap(fresh = false) {
  if (!$('walk-toggle').checked || !level) return;
  const key = `${level}|${viewKey}`;
  const w = await run('walkability_grid', { cells: fresh || walkLevel !== key });
  if (w.cells) {
    walkLevel = key;
    clearGroup(walkGroup, walkFoot);
    const mtx = new THREE.Matrix4(), q = new THREE.Quaternion(), sc = new THREE.Vector3();
    const walk = w.cells.filter((c) => c.walkable), small = w.cells.filter((c) => !c.walkable && c.size <= 2000);
    const big = w.cells.filter((c) => !c.walkable && c.size > 2000);
    const cellsMesh = (list, color, opacity) => {
      const m = new THREE.InstancedMesh(flatPlane, overlayMat(color, opacity), Math.max(1, list.length));
      m.count = list.length;
      list.forEach((c, i) => {
        const p = toThree(c.pos); p.y += 0.3;
        const s = (c.size / 100) * 0.94;                      // a small gap shows the cell size
        m.setMatrixAt(i, mtx.compose(p, q, sc.set(s, 1, s)));
      });
      m.raycast = () => {}; m.renderOrder = 3;
      return m;
    };
    walkGroup.add(cellsMesh(walk, 0x3fd06b, 0.32), cellsMesh(small, 0xe04040, 0.3));
    const pts = [];
    for (const c of big) {
      const p = toThree(c.pos), h = c.size / 200, y = p.y + 0.3;
      const k = [[-h, -h], [h, -h], [h, h], [-h, h]].map(([dx, dz]) => new THREE.Vector3(p.x + dx, y, p.z + dz));
      for (let i = 0; i < 4; i++) pts.push(k[i], k[(i + 1) % 4]);
    }
    const lines = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: 0xe04040 }));
    lines.raycast = () => {};
    walkGroup.add(lines);
    $('count-walk').textContent = `${w.walkable} / ${w.blocked}`;
    status(`AI walk map: ${w.source} - ${w.walkable} walkable, ${w.blocked} blocked cells, ${w.footprints.length} added obstacle(s)`);
  }
  buildFootprints(w.footprints);
}
function buildFootprints(lines) {
  clearGroup(walkFoot);
  for (const line of lines) {
    const rec = findRec(line);
    if (!rec) continue;
    model(rec.model).then(() => requestAnimationFrame(() => {   // after buildScene's own .then added the model
      const h = byLine.get(line);
      if (!h || !walkGroup.visible) return;
      const box = new THREE.Box3().setFromObject(h);
      if (box.isEmpty()) return;
      const helper = new THREE.Box3Helper(box, 0xff4040); helper.raycast = () => {};
      const size = box.getSize(new THREE.Vector3()), c = box.getCenter(new THREE.Vector3());
      const quad = new THREE.Mesh(flatPlane, overlayMat(0xff4040, 0.45));
      quad.position.set(c.x, box.min.y + 0.35, c.z); quad.scale.set(size.x, 1, size.z); quad.raycast = () => {};
      walkFoot.add(helper, quad); requestRender();
    }));
  }
}
$('walk-toggle').onchange = () => {
  walkGroup.visible = $('walk-toggle').checked;
  if (walkGroup.visible) buildWalkMap(true).catch(() => {});
};

// ---- tactical map (in-game MISSION tab / pause screen): a top-down orthographic shot of the ground and props over
// the level bounds the game stretches the map across (minimap_info), 1024 px scaled down to the game's 256 ----
let minimapUrl = null;
async function renderMinimap() {
  const info = await run('minimap_info');
  const [x0, z0] = info.min, [x1, z1] = info.max;
  const W = (x1 - x0) / 100, D = (z1 - z0) / 100, cx = (x0 + x1) / 200, cz = -(z0 + z1) / 200;
  const top = 4000;
  const cam = new THREE.OrthographicCamera(-W / 2, W / 2, D / 2, -D / 2, 1, top + 4000);
  cam.up.set(0, 0, -1);                    // screen up = game +z (north on the shipped maps), right = game +x
  cam.position.set(cx, top, cz); cam.lookAt(cx, 0, cz); cam.updateMatrixWorld();
  const envWas = envMode;
  if (envWas !== 'day') setEnvMode('day');
  const keep = [groups.world, groups.props, sun, sun.target, hemi];
  const hidden = scene3.children.filter((o) => o.visible && !keep.includes(o));
  for (const o of hidden) o.visible = false;
  const fog = scene3.fog, bg = scene3.background;
  scene3.fog = null; scene3.background = new THREE.Color(0xc9b994);
  const N = 1024, size = renderer.getSize(new THREE.Vector2()), ratio = renderer.getPixelRatio();
  let url;
  try {
    updateSunShadow(new THREE.Vector3(cx, 0, cz), Math.max(W, D) * 0.75);
    renderer.setPixelRatio(1); renderer.setSize(N, N, false);
    renderer.render(scene3, cam);
    url = renderer.domElement.toDataURL('image/png');     // same task as the render: the buffer is still there
  } finally {
    renderer.setPixelRatio(ratio); renderer.setSize(size.x, size.y, false);
    for (const o of hidden) o.visible = true;
    scene3.fog = fog; scene3.background = bg;
    if (envWas !== 'day') setEnvMode(envWas);
    requestRender();
  }
  const img = await new Promise((ok, bad) => { const i = new Image(); i.onload = () => ok(i); i.onerror = bad; i.src = url; });
  let src = img;
  for (const s of [512, info.size]) {                    // halve twice: smoother than one big step
    const c = document.createElement('canvas'); c.width = c.height = s;
    const ctx = c.getContext('2d'); ctx.imageSmoothingQuality = 'high'; ctx.drawImage(src, 0, 0, s, s);
    src = c;
  }
  return { info, png: src.toDataURL('image/png') };
}
$('minimap-btn').onclick = async () => {
  if (!level) return;
  status('rendering the tactical map…');
  try {
    const { info, png } = await renderMinimap();
    minimapUrl = png;
    $('mm-img').src = png;
    $('mm-info').textContent = `Covers game x ${info.min[0].toFixed(0)} to ${info.max[0].toFixed(0)}, z ${info.min[1].toFixed(0)} to ${info.max[1].toFixed(0)} (the level bounds) - ${info.size} × ${info.size} px like the game's own maps.`;
    $('mm-save').disabled = !info.mission;
    $('mm-result').textContent = info.mission ? `Saves ${info.files.join(' + ')} into Mods\\${info.mission[0]} and loads it from the mission script.`
      : 'Save the level as a mission first (Save as mission…) - the map belongs to the mission.';
    if (info.base_map) $('mm-base').textContent = `Replaces ${info.base_map}.`;
    $('minimap-dialog').showModal();
    status('tactical map rendered');
  } catch (e) { status(`minimap: ${e.message}`, true); }
};
$('mm-save').onclick = async () => {
  try {
    const r = await run('save_minimap', { image_data_base64: minimapUrl }, 'tactical map saved');
    $('mm-result').textContent = `Saved:\n${r.files.join('\n')}\nScript: ${r.script}`;
    updateHistory();
  } catch (e) { $('mm-result').textContent = e.message; }
};
$('mm-close').onclick = () => $('minimap-dialog').close();

// ---- prefabs: Shift+click objects and props, save them as a group; stamp the group anywhere (any level / mission) ----
async function loadPrefabs() {
  const list = await run('list_prefabs');
  const ul = $('prefabs'); ul.innerHTML = '';
  for (const p of list) {
    const li = document.createElement('li');
    li.innerHTML = `<span>▦ ${escHtml(p.name)}<br><small>${p.records} record(s)${p.crew ? ` + ${p.crew} crew` : ''} · from ${escHtml(p.level || '?')}</small></span>`;
    li.title = (p.description ? p.description + '\n' : '') + p.contents.join('\n');
    const del = document.createElement('button'); del.textContent = '×'; del.title = 'Delete this prefab';
    del.onclick = async (ev) => {
      ev.stopPropagation();
      if (!confirm(`Delete the prefab "${p.name}"?`)) return;
      await run('delete_prefab', { name: p.name }, `prefab ${p.name} deleted`); loadPrefabs();
    };
    li.appendChild(del);
    const entry = { prefab: p.name, label: `prefab ${p.name}` };
    li.onclick = () => {
      placing = entry;
      for (const x of ul.children) x.classList.toggle('sel', x === li);
      status(`click in the world to stamp ${p.name} - or drag it in (Esc cancels)`);
    };
    li.draggable = true;
    li.ondragstart = (ev) => { dragEntry = entry; ev.dataTransfer.effectAllowed = 'copy'; ev.dataTransfer.setData('text/plain', entry.label); };
    li.ondragend = dropPreview;
    ul.appendChild(li);
  }
  if (!list.length) ul.innerHTML = '<li class="hint">no prefabs yet - Shift+click objects and props, then save them</li>';
}
async function stampPrefab(e, point) {
  const v = $('pf-yaw').value;
  const yaw = v === 'r' ? +(Math.random() * 360).toFixed(1) : +v;
  const r = await run('paste_prefab', { name: e.prefab, pos: toGame(point), yaw }, `stamped ${e.prefab}`);
  await refresh();
  if (r.lines.length > 1) selectMany(r.lines); else await select({ line: r.lines[0] });
  status(`stamped ${e.prefab}: ${r.lines.length} record(s)${yaw ? `, turned ${yaw}°` : ''} - Ctrl+Z undoes it`);
}
$('pf-save').onclick = async () => {
  const lines = many.size ? [...many] : selection?.line !== undefined && !selection.target ? [selection.line] : [];
  if (!lines.length) return status('select objects or props first (Shift+click several)', true);
  const name = $('pf-name').value.trim();
  if (!name) { $('pf-name').focus(); return status('give the prefab a name', true); }
  const r = await run('save_prefab', { name, lines });
  status(`prefab ${r.name}: ${r.records} record(s)${r.crew ? ` + ${r.crew} crew` : ''} saved`);
  $('pf-name').value = '';
  loadPrefabs();
};
$('pf-name').onkeydown = (e) => { if (e.key === 'Enter') $('pf-save').click(); };

// ---- vehicle crews: a seat per VEHICLE.TXT connector (driver, gunner, commander, passengers) ----
async function showCrew(line) {
  const card = await api('vehicle_crew', { line });
  if (selection?.line !== line) return;
  const t = $('crew-table'); t.innerHTML = '';
  for (const seat of card.seats) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${escHtml(seat.name)}</td><td></td><td></td>`;
    const sel = document.createElement('select');
    sel.add(new Option('(empty)', ''));
    const cur = seat.soldier;
    if (cur) sel.add(new Option(`${cur.unit} · id ${cur.script_id}${cur.ok ? '' : ' - not in the scene!'}`, cur.line ?? 'x'));
    for (const c of card.candidates) {
      if (cur && c.line === cur.line) continue;
      const where = c.in_vehicle ? (c.in_vehicle.line === line ? ' (other seat)' : ` (in vehicle, line ${c.in_vehicle.line})`) : '';
      sel.add(new Option(`${c.unit} · id ${c.script_id} · ${c.distance_m} m${where}`, c.line));
    }
    sel.value = cur ? String(cur.line ?? 'x') : '';
    sel.onchange = () => {
      if (sel.value === 'x') return;
      assignCrew(line, seat.seat, sel.value ? { soldier_line: +sel.value } : {},
                 sel.value ? `${seat.name}: ${sel.selectedOptions[0].text}` : `${seat.name} emptied`);
    };
    tr.children[1].appendChild(sel);
    const b = document.createElement('button');
    if (cur?.line) { b.textContent = '→'; b.title = 'Select this soldier'; b.onclick = () => select({ line: cur.line }, true); }
    else {
      b.textContent = 'Spawn'; b.disabled = !card.spawn_unit;
      b.title = card.spawn_unit ? `A new ${card.spawn_unit} in the ${seat.name.toLowerCase()} seat` : 'no crewman of this side to copy';
      b.onclick = () => assignCrew(line, seat.seat, { spawn: true }, `${seat.name}: new ${card.spawn_unit}`);
    }
    tr.children[2].appendChild(b);
    t.appendChild(tr);
  }
  $('crew-info').textContent = (card.known_seats ? '' : 'Seats unknown for this vehicle (not in VEHICLE.TXT) - numbered. ')
    + 'Crew mount at level start and start in play with their vehicle.';
  $('crew-row').hidden = false;
}
async function assignCrew(vehicleLine, seat, extra, what) {
  const r = await run('assign_crew', { vehicle_line: vehicleLine, seat, ...extra }, what);
  await refresh(); await select({ line: r.line });
}

// ---- sound audition: ambient sounds (ENVSND) and the squad's voice lines, decoded by the server as WAV ----
const audio = new Audio();
let audioUrl = null;
const HERO_BANK = { HERO01: 'Bradley', HERO02: 'Ramirez', HERO03: 'Jones', HERO04: 'Foley' };
const voiceCache = new Map();
function stopSound() { audio.pause(); $('snd-play').textContent = '▶ Audition'; }
function playSound(url) {
  if (audioUrl === url && !audio.paused) return stopSound();
  audioUrl = url; audio.src = url; audio.loop = $('snd-loop').checked; audio.currentTime = 0;
  audio.play().then(() => { $('snd-play').textContent = '⏸ Stop'; })
    .catch((e) => status(`can't play it: ${e.message}`, true));
}
audio.onended = () => { $('snd-play').textContent = '▶ Audition'; };
$('snd-loop').onchange = () => { audio.loop = $('snd-loop').checked; };
async function showSound(rec) {
  const f = rec.fields;
  $('snd-line').hidden = true;
  $('snd-play').disabled = true;
  $('snd-loop').checked = !!f.loop;
  $('sound-row').hidden = false;
  const range = `full volume within ${(f.min_distance / 100).toFixed(0)} m, silent past ${(f.max_distance / 100).toFixed(0)} m (inner / outer sphere)`;
  try {
    const info = await api('sound_info', { name: f.sound });
    $('snd-info').textContent = `${f.sound}: ${info.seconds} s, ${info.rate} Hz${f.loop ? ', loops' : ''} - ${range}`;
    $('snd-play').disabled = !info.url;
    $('snd-play').onclick = () => playSound(info.url);
  } catch (e) { $('snd-info').textContent = `${f.sound}: ${e.message} - ${range}`; }
}
async function showVoices(o) {
  const bank = HERO_BANK[String(o.model).slice(0, 6).toUpperCase()];
  if (!voiceCache.has(level)) voiceCache.set(level, api('voice_banks').catch(() => []));
  const b = (await voiceCache.get(level)).find((x) => x.bank === bank);
  if (selection?.line !== o.line) return;
  const sel = $('snd-line'); sel.innerHTML = '';
  $('snd-loop').checked = false;
  $('sound-row').hidden = false;
  if (!b) { $('snd-info').textContent = `${bank}'s radio lines aren't in this level's sound cache.`; $('snd-play').disabled = true; sel.hidden = true; return; }
  for (const l of b.lines) sel.add(new Option(`${l.line}. ${l.name.replace(/_/g, ' ')}`, l.url));
  sel.hidden = false; $('snd-play').disabled = false;
  sel.onchange = () => { stopSound(); playSound(sel.value); };
  $('snd-play').onclick = () => playSound(sel.value);
  $('snd-info').textContent = `${bank}'s radio lines (${b.lines.length}) - "loud" ones in combat, "quiet" ones when the squad is calm.`;
}

// ==== production: resource budget, campaigns, briefing, soundtrack, spawns, release (editor/production.py) ====
function productionAfterScene(fresh) {
  scheduleBudget();
  if (!$('spawns').hidden || spawnGroup.children.length) loadSpawns(false).catch(() => {});
  if (fresh) musicOpts = null;
}

// ---- resource budget bar: polygons / objects / texture memory against the largest shipped level ----
let budgetTimer = null, budgetInfo = null;
function scheduleBudget() {
  clearTimeout(budgetTimer);
  budgetTimer = setTimeout(updateBudget, 900);
}
async function updateBudget() {
  const box = $('budget');
  box.classList.add('busy');
  try { budgetInfo = await api('scene_budget'); }
  catch (e) { box.title = `budget: ${e.message}`; return; }
  finally { box.classList.remove('busy'); }
  const b = budgetInfo;
  const show = { triangles: [b.triangles, (v) => v >= 1e5 ? `${(v / 1e3).toFixed(0)}k` : v.toLocaleString()],
                 objects: [b.objects, (v) => v.toLocaleString()],
                 texture_mb: [b.textures.mb, (v) => `${v.toFixed(0)} MB`] };
  for (const [k, [v, fmt]] of Object.entries(show)) {
    const m = $('bud-' + k), r = b.ratio[k], lim = b.limits[k];
    m.querySelector('.bar').style.width = `${Math.min(100, r * 100)}%`;
    m.querySelector('b').textContent = `${fmt(v)} · ${Math.round(r * 100)}%`;
    m.classList.toggle('warn', r > 1 && r <= 1.25);
    m.classList.toggle('over', r > 1.25);
    m.title = `${fmt(v)} of ${fmt(lim.guide)} (${lim.largest_shipped}, the largest shipped level by this measure)`
      + (r > 1.25 ? ' - well past anything the game shipped with: test on low-end settings' : r > 1 ? ' - past the largest shipped level' : '');
  }
  const t = b.triangles_by, o = b.objects_by;
  box.title = `Triangles: world ${t.world.toLocaleString()}, props ${t.props.toLocaleString()}, objects ${t.objects.toLocaleString()}, `
    + `terrain/roads/kits ${t.terrain.toLocaleString()}\nRecords: ${o.objects} objects, ${o.props} props, ${o.world_pieces} world pieces\n`
    + `Textures: ${b.textures.count} (${b.textures.mb} MB)\nHeaviest: ${b.heaviest.slice(0, 4).map((h) => `${h.model} ${h.triangles}`).join(', ')}\n`
    + b.notes.join('\n') + '\n(click to re-measure)';
}
$('budget').onclick = () => { status('measuring the scene…'); updateBudget().then(() => status('budget measured')); };

// ---- campaigns ----
let cpInfo = null, cpSeq = [];
async function openCampaigns(selectMod) {
  cpInfo = await run('campaign_list');
  const sel = $('cp-mod'); sel.innerHTML = '';
  for (const m of cpInfo.mods) sel.add(new Option(m.mod + (m.enabled ? '' : ' (off)') + (m.campaign ? ' ★' : ''), m.mod));
  const want = selectMod || (data?.mission ? data.mission[0] : null);
  if (want && cpInfo.mods.some((m) => m.mod === want)) sel.value = want;
  $('cp-diff').innerHTML = '';
  for (const d of cpInfo.difficulties) $('cp-diff').add(new Option(d || '(not rated)', d));
  showCampaignMod();
  if (!$('campaign-dialog').open) $('campaign-dialog').showModal();
}
function cpMod() { return cpInfo.mods.find((m) => m.mod === $('cp-mod').value); }
function showCampaignMod() {
  const m = cpMod();
  if (!m) { $('cp-avail').innerHTML = '<li class="hint">no mod folders yet - save a mission first</li>'; return; }
  const c = m.campaign;
  $('cp-name').value = c ? c.name : m.mod;
  $('cp-diff').value = c ? c.difficulty : '';
  cpSeq = c ? c.missions.map((x) => x.file) : [];
  const ul = $('cp-avail'); ul.innerHTML = '';
  for (const a of m.available) {
    const li = document.createElement('li');
    li.innerHTML = `<span class="grow">${escHtml(a.mission)}${a.title ? ` <small>${escHtml(a.title)}</small>` : ''}</span><small>${escHtml(a.base || '')}</small>`;
    li.title = 'Add to the campaign';
    li.onclick = () => { if (!cpSeq.includes(a.file)) { cpSeq.push(a.file); drawSeq(); } };
    ul.appendChild(li);
  }
  if (!m.available.length) ul.innerHTML = '<li class="hint">no mission scripts in this mod</li>';
  $('cp-result').textContent = '';
  drawSeq();
}
function drawSeq() {
  const m = cpMod(), own = new Map((m?.available || []).map((a) => [a.file, a]));
  const ol = $('cp-seq'); ol.innerHTML = '';
  cpSeq.forEach((f, i) => {
    const a = own.get(f.toLowerCase());
    const li = document.createElement('li'); li.draggable = true;
    if (!a) li.classList.add('missing');
    li.innerHTML = `<span class="grow">${escHtml(a?.title || f.replace(/\.lua$/i, ''))} <small>${escHtml(f)}${a ? '' : ' - not in this mod'}</small></span>`;
    const btn = (txt, fn, tip) => { const b = document.createElement('button'); b.textContent = txt; b.title = tip; b.onclick = fn; li.appendChild(b); };
    btn('▲', () => { if (i) { [cpSeq[i - 1], cpSeq[i]] = [cpSeq[i], cpSeq[i - 1]]; drawSeq(); } }, 'earlier');
    btn('▼', () => { if (i < cpSeq.length - 1) { [cpSeq[i + 1], cpSeq[i]] = [cpSeq[i], cpSeq[i + 1]]; drawSeq(); } }, 'later');
    btn('✕', () => { cpSeq.splice(i, 1); drawSeq(); }, 'remove from the campaign');
    li.ondragstart = (e) => { e.dataTransfer.setData('text/plain', String(i)); li.classList.add('drag'); };
    li.ondragend = () => li.classList.remove('drag');
    li.ondragover = (e) => e.preventDefault();
    li.ondrop = (e) => {
      e.preventDefault();
      const from = +e.dataTransfer.getData('text/plain');
      if (Number.isNaN(from) || from === i) return;
      const [x] = cpSeq.splice(from, 1); cpSeq.splice(i, 0, x); drawSeq();
    };
    ol.appendChild(li);
  });
  if (!cpSeq.length) ol.innerHTML = '<li class="hint">click missions on the left to add them</li>';
  $('cp-count').textContent = `${cpSeq.length} mission${cpSeq.length === 1 ? '' : 's'} · ${cpInfo.active_campaigns} of ${cpInfo.max_campaigns} campaign slots used`;
}
$('cp-mod').onchange = showCampaignMod;
$('cp-extra-add').onclick = () => {
  let f = $('cp-extra').value.trim(); if (!f) return;
  if (!/\.lua$/i.test(f)) f += '.lua';
  f = f.toLowerCase();
  if (!cpSeq.includes(f)) cpSeq.push(f);
  $('cp-extra').value = ''; drawSeq();
};
$('cp-save').onclick = async () => {
  try {
    const mod = $('cp-mod').value;
    const r = await run('campaign_save', { mod, name: $('cp-name').value, missions: cpSeq, difficulty: $('cp-diff').value }, 'campaign saved');
    $('cp-result').textContent = `Saved ${r.path}` + (r.not_in_this_mod.length ? `\n${r.note}: ${r.not_in_this_mod.join(', ')}` : '');
    await openCampaigns(mod);
  } catch (e) { $('cp-result').textContent = e.message; }
};
$('campaign-btn').onclick = () => openCampaigns().catch(() => {});
$('cp-close').onclick = () => $('campaign-dialog').close();

// ---- briefing designer: dossier texts + tactical map pins (game x / z), burnt into the map picture ----
const PIN_COLOUR = { objective: '#ff5040', insertion: '#5fd16b', extraction: '#5fa8ff' };
let bf = null;              // {map: {min, max}, pins, img}
async function openBriefing() {
  const info = await run('briefing_get');
  let img = null;
  try { const r = await api('briefing_map_image'); if (r.image) { img = new Image(); img.src = r.image; await img.decode(); } }
  catch (e) { /* not saved as a mission yet */ }
  bf = { map: info.map, pins: info.pins.map((p) => ({ ...p })), img };
  $('bf-title').value = info.title; $('bf-date').value = info.date; $('bf-location').value = info.location; $('bf-text').value = info.text;
  const base = info.base, fresh = !info.title && !info.date && !info.location && !info.text;
  if (fresh && base?.base) {             // nothing written yet: start from the base mission's own briefing
    $('bf-title').value = base.title; $('bf-date').value = base.intro_date || base.date;
    $('bf-location').value = base.intro_location || base.location; $('bf-text').value = base.briefing_text;
  }
  $('bf-burn').disabled = !img || !data?.mission;
  $('bf-result').textContent = !info.map ? `No map bounds: ${info.map_problem || ''}`
    : !img ? 'No tactical map picture yet - Generate Minimap and save it to the mission, then reopen this.' : '';
  if (fresh && base?.base) $('bf-result').textContent = `Filled from the base mission's own briefing - nothing changes until you Save. ${$('bf-result').textContent}`;
  drawBriefing();
  $('briefing-dialog').showModal();
}
function bfToCanvas(pos, size = 512) {
  const { min, max } = bf.map;
  return [(pos[0] - min[0]) / (max[0] - min[0]) * size, (1 - (pos[1] - min[1]) / (max[1] - min[1])) * size];
}
function bfToGame(x, y, size = 512) {
  const { min, max } = bf.map;
  return [min[0] + x / size * (max[0] - min[0]), min[1] + (1 - y / size) * (max[1] - min[1])];
}
function drawPin(ctx, p, size, scale = 1) {
  const [x, y] = bfToCanvas(p.pos, size), c = PIN_COLOUR[p.type], r = 9 * scale;
  ctx.save(); ctx.lineWidth = 2.2 * scale; ctx.strokeStyle = c; ctx.fillStyle = c;
  ctx.shadowColor = '#000'; ctx.shadowBlur = 3 * scale;
  if (p.type === 'objective') {
    ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(x - r * 1.6, y); ctx.lineTo(x - r * 0.4, y); ctx.moveTo(x + r * 0.4, y); ctx.lineTo(x + r * 1.6, y);
    ctx.moveTo(x, y - r * 1.6); ctx.lineTo(x, y - r * 0.4); ctx.moveTo(x, y + r * 0.4); ctx.lineTo(x, y + r * 1.6); ctx.stroke();
  } else if (p.type === 'insertion') {
    const a = deg(p.heading || 0);               // game heading: forward = (sin, cos) in x / z; canvas y runs down
    const dx = Math.sin(a), dy = -Math.cos(a), L = r * 2.4;
    ctx.beginPath(); ctx.moveTo(x - dx * L, y - dy * L); ctx.lineTo(x + dx * L * 0.4, y + dy * L * 0.4); ctx.stroke();
    ctx.beginPath(); const tx = x + dx * L, ty = y + dy * L;
    ctx.moveTo(tx, ty); ctx.lineTo(tx - dx * r * 1.4 - dy * r * 0.9, ty - dy * r * 1.4 + dx * r * 0.9);
    ctx.lineTo(tx - dx * r * 1.4 + dy * r * 0.9, ty - dy * r * 1.4 - dx * r * 0.9); ctx.closePath(); ctx.fill();
  } else {
    ctx.beginPath(); ctx.arc(x, y, r * 1.2, 0, Math.PI * 2); ctx.stroke();
    ctx.font = `bold ${Math.round(13 * scale)}px sans-serif`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText('H', x, y + scale);
  }
  if (p.label) {
    ctx.font = `bold ${Math.round(11 * scale)}px sans-serif`; ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
    ctx.fillStyle = '#fff'; ctx.fillText(p.label, x + r * 1.7, y - r * 1.2);
  }
  ctx.restore();
}
function drawBriefing() {
  const cv = $('bf-map'), ctx = cv.getContext('2d');
  ctx.fillStyle = '#10120f'; ctx.fillRect(0, 0, cv.width, cv.height);
  if (bf.img) ctx.drawImage(bf.img, 0, 0, cv.width, cv.height);
  else { ctx.fillStyle = '#9a9b8c'; ctx.font = '13px sans-serif'; ctx.textAlign = 'center'; ctx.fillText('(no tactical map picture yet)', 256, 256); }
  if (bf.map) for (const p of bf.pins) drawPin(ctx, p, cv.width);
  const ul = $('bf-pins'); ul.innerHTML = '';
  bf.pins.forEach((p, i) => {
    const li = document.createElement('li');
    li.innerHTML = `<span style="color:${PIN_COLOUR[p.type]}">●</span><span class="grow">${p.type}${p.label ? ` "${escHtml(p.label)}"` : ''} <small>x ${p.pos[0].toFixed(0)}, z ${p.pos[1].toFixed(0)}${p.type === 'insertion' ? `, heading ${p.heading.toFixed(0)}°` : ''}</small></span>`;
    const b = document.createElement('button'); b.textContent = '✕'; b.onclick = () => { bf.pins.splice(i, 1); drawBriefing(); };
    li.appendChild(b); ul.appendChild(li);
  });
}
let bfDown = null;
const bfPoint = (e) => { const r = $('bf-map').getBoundingClientRect(); return [(e.clientX - r.left) * 512 / r.width, (e.clientY - r.top) * 512 / r.height]; };
$('bf-map').onpointerdown = (e) => { if (e.button === 0 && bf?.map) bfDown = bfPoint(e); };
$('bf-map').onpointerup = (e) => {
  if (!bfDown || !bf?.map) return;
  const [x0, y0] = bfDown, [x1, y1] = bfPoint(e); bfDown = null;
  if (bf.pins.length >= 24) { $('bf-result').textContent = 'up to 24 pins'; return; }
  const type = document.querySelector('input[name="bf-pin"]:checked').value;
  const heading = Math.hypot(x1 - x0, y1 - y0) > 6 ? (THREE.MathUtils.radToDeg(Math.atan2(x1 - x0, -(y1 - y0))) + 360) % 360 : 0;
  bf.pins.push({ type, pos: bfToGame(x0, y0).map((v) => +v.toFixed(1)), label: $('bf-label').value.trim(), heading: +heading.toFixed(1) });
  drawBriefing();
};
$('bf-map').oncontextmenu = (e) => {
  e.preventDefault();
  if (!bf?.map) return;
  const [x, y] = bfPoint(e);
  let best = -1, d = 14;
  bf.pins.forEach((p, i) => { const [px, py] = bfToCanvas(p.pos); const dd = Math.hypot(px - x, py - y); if (dd < d) { d = dd; best = i; } });
  if (best >= 0) { bf.pins.splice(best, 1); drawBriefing(); }
};
async function saveBriefing() {
  const r = await run('briefing_set', { title: $('bf-title').value, date: $('bf-date').value, location: $('bf-location').value,
                                        text: $('bf-text').value, pins: bf.pins }, 'briefing saved (Save as mission writes it into the script)');
  await updateHistory();
  return r;
}
$('bf-save').onclick = async () => {
  try { await saveBriefing(); $('bf-result').textContent = 'Saved. Save the mission (or Play) to write it into the script.'; }
  catch (e) { $('bf-result').textContent = e.message; }
};
$('bf-burn').onclick = async () => {
  try {
    await saveBriefing();
    const cv = document.createElement('canvas'); cv.width = cv.height = 512;
    const ctx = cv.getContext('2d'); ctx.drawImage(bf.img, 0, 0, 512, 512);
    for (const p of bf.pins) drawPin(ctx, p, 512, 1.6);          // the game shows it at 256 px: bolder strokes
    const r = await run('briefing_burn_map', { image_data_base64: cv.toDataURL('image/png') }, 'pins drawn into the tactical map');
    $('bf-result').textContent = `Map saved with ${bf.pins.length} pin(s): ${r.files.join(', ')}\nThe pin-less picture is kept for the next burn.`;
  } catch (e) { $('bf-result').textContent = e.message; }
};
$('briefing-btn').onclick = () => openBriefing().catch(() => {});
$('bf-close').onclick = () => $('briefing-dialog').close();

// ---- soundtrack (Atmosphere panel) ----
let musicOpts = null;
const musicAudio = new Audio();
async function loadMusic() {
  musicOpts = await api('music_options');
  for (const [id, cur] of [['mu-ambient', musicOpts.ambient_track], ['mu-combat', musicOpts.combat_track]]) {
    const sel = $(id); sel.innerHTML = '';
    const sc = musicOpts.base?.score?.length ? musicOpts.base.score.map((x) => x.replace(/\.psf$/i, '')).join(', ') : '';
    sel.add(new Option(sc ? `Level score: ${sc} (base mission)` : '(none - level score only)', ''));
    for (const t of musicOpts.tracks) sel.add(new Option(`${t.file}${t.seconds ? ` (${t.seconds} s)` : ''}`, t.file));
    sel.value = cur || '';
  }
  $('mu-calm').value = musicOpts.calm_seconds;
  const score = musicOpts.level_score.filter((x) => x.kind === 'score').map((x) => x.stream).join(', ') || 'none';
  const b = musicOpts.base;
  $('mu-info').textContent = (musicOpts.mission ? '' : 'Save as a mission first - tracks live in its mod folder. ')
    + `Level score: ${score} (one interactive stream - it plays under both ambient and combat)`
    + (b?.stings?.length ? `, stings: ${b.stings.join(', ')}` : '') + (b?.sound_bank ? `, sound bank: ${b.sound_bank}` : '')
    + `. ${musicOpts.note}`;
  for (const id of ['mu-upload', 'mu-apply']) $(id).disabled = !musicOpts.mission;
}
function auditionTrack(selId, btn) {
  const t = musicOpts?.tracks.find((x) => x.file === $(selId).value);
  if (!t) { status('pick a .wav track (the level\'s own score can\'t be auditioned here)', true); return; }
  if (!musicAudio.paused && musicAudio.dataset.url === t.url) { musicAudio.pause(); btn.textContent = '▶'; return; }
  for (const b of ['mu-ambient-play', 'mu-combat-play']) $(b).textContent = '▶';
  musicAudio.dataset.url = t.url; musicAudio.src = t.url; musicAudio.currentTime = 0;
  musicAudio.play().then(() => { btn.textContent = '⏸'; }).catch((e) => status(`can't play it: ${e.message}`, true));
}
musicAudio.onended = () => { for (const b of ['mu-ambient-play', 'mu-combat-play']) $(b).textContent = '▶'; };
$('mu-ambient-play').onclick = () => auditionTrack('mu-ambient', $('mu-ambient-play'));
$('mu-combat-play').onclick = () => auditionTrack('mu-combat', $('mu-combat-play'));
$('mu-upload').onclick = () => $('mu-file').click();
$('mu-file').onchange = async () => {
  const f = $('mu-file').files[0]; if (!f || !musicOpts?.mission) return;
  const buf = new Uint8Array(await f.arrayBuffer());
  let bin = ''; for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
  try { await run('upload_file', { mod: musicOpts.mission[0], filename: f.name, data_base64: btoa(bin) }, `${f.name} added to the mod`); await loadMusic(); }
  catch { /* shown in the status line */ }
  $('mu-file').value = '';
};
$('mu-apply').onclick = async () => {
  try {
    await run('music_set', { ambient_track: $('mu-ambient').value, combat_track: $('mu-combat').value, calm_seconds: +$('mu-calm').value },
              'music set (saved into the script with the mission)');
    await updateHistory();
  } catch { await loadMusic(); }
};

// ---- multiplayer spawns: squad soldiers players start on (co-op), faction spawn areas (versus, data only) ----
const spawnGroup = new THREE.Group(); scene3.add(spawnGroup);
const PLAYER_COLOUR = ['#4fa0ff', '#ff5a4a', '#5fd16b', '#ff7ad9'];   // the plugin's player colours (light bar)
const FACTION_COLOUR = { allied: '#4fa0ff', iraqi: '#e0b030' };
let spawnInfo = null, groundPick = null;
function endGroundPick() {
  groundPick = null; renderer.domElement.style.cursor = '';
  for (const b of $('sp-pads').children) b.classList.remove('on');
  for (const b of document.querySelectorAll('#emp-list .on')) b.classList.remove('on');
}
function labelSprite(text, colour) {
  const cv = document.createElement('canvas'); cv.width = 128; cv.height = 64;
  const ctx = cv.getContext('2d');
  ctx.fillStyle = '#000a'; ctx.beginPath(); ctx.roundRect(4, 8, 120, 48, 10); ctx.fill();
  ctx.strokeStyle = colour; ctx.lineWidth = 4; ctx.stroke();
  ctx.fillStyle = colour; ctx.font = 'bold 30px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillText(text, 64, 33);
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(cv), depthTest: false, transparent: true }));
  s.scale.set(1.6, 0.8, 1); s.renderOrder = 10;
  return s;
}
const silhouetteGeo = keep(new THREE.CapsuleGeometry(0.28, 1.1, 4, 10).translate(0, 0.83, 0));
const padGeo = keep(new THREE.CylinderGeometry(0.75, 0.75, 0.06, 28).translate(0, 0.03, 0));
function spawnMarker(pos, colour, text) {
  const g = new THREE.Group(); g.position.copy(toThree(pos));
  const mat = new THREE.MeshBasicMaterial({ color: colour, transparent: true, opacity: 0.55, depthWrite: false });
  g.add(new THREE.Mesh(padGeo, mat), new THREE.Mesh(silhouetteGeo, mat));
  const l = labelSprite(text, colour); l.position.y = 2.4; g.add(l);
  g.traverse((c) => { c.raycast = () => {}; });
  spawnGroup.add(g);
}
async function loadSpawns(openPanel = true) {
  spawnInfo = await api('multiplayer_info');
  clearGroup(spawnGroup);
  const n = spawnInfo.max_players;
  for (const p of spawnInfo.coop) spawnMarker(p.pos, PLAYER_COLOUR[p.slot], `P${p.slot + 1}`);
  for (const [side, pts] of Object.entries(spawnInfo.deathmatch || {}))
    pts.forEach((pt, i) => spawnMarker(pt, FACTION_COLOUR[side], `${side === 'allied' ? 'US' : 'IQ'}${i + 1}`));
  if (!openPanel && $('spawns').hidden) return;
  $('sp-mode').value = spawnInfo.mode || 'coop';
  showSpawnMode();
  const pads = $('sp-pads'); pads.innerHTML = '';
  for (const p of spawnInfo.coop) {
    const b = document.createElement('button'); b.textContent = `P${p.slot + 1}`;
    b.style.borderBottom = `3px solid ${PLAYER_COLOUR[p.slot]}`; b.title = `${p.soldier} (line ${p.line}) - click, then click the ground`;
    b.onclick = () => {
      endGroundPick(); b.classList.add('on'); renderer.domElement.style.cursor = 'crosshair';
      status(`click the ground: P${p.slot + 1} (${p.soldier}) starts there - Esc cancels`);
      groundPick = { pick: async (pos) => {
        endGroundPick();
        try {
          const r = await run('multiplayer_setup', { mode: 'coop', spawns: { [`P${p.slot + 1}`]: pos } }, `P${p.slot + 1} spawn moved`);
          if (r.warnings.length) status(r.warnings.join('; '), true);
          await refresh();
        } catch { /* status shows it */ }
      } };
    };
    pads.appendChild(b);
  }
  const t = $('sp-table');
  t.innerHTML = '<tr><th>Soldier</th><th>2 players</th><th>3 players</th><th>4 players</th></tr>'
    + spawnInfo.coop.map((p) => `<tr><td>${p.soldier}</td>${['2', '3', '4'].map((k) => `<td style="color:${PLAYER_COLOUR[p.players[k] - 1]}">P${p.players[k]}</td>`).join('')}</tr>`).join('');
  $('sp-info').textContent = spawnInfo.coop.length
    ? `${spawnInfo.coop.length} squad soldier(s) here: co-op for up to ${n} player(s). ${spawnInfo.note}`
    : 'No squad soldiers in this scene.';
  const ul = $('sp-dm-list'); ul.innerHTML = '';
  for (const [side, pts] of Object.entries(spawnInfo.deathmatch || {})) pts.forEach((pt, i) => {
    const li = document.createElement('li');
    li.innerHTML = `<span style="color:${FACTION_COLOUR[side]}">●</span><span class="grow">${spawnInfo.factions[side]} ${i + 1} <small>${pt.map((v) => v.toFixed(0)).join(', ')}</small></span>`;
    const b = document.createElement('button'); b.textContent = '✕';
    b.onclick = () => setDmSpawns(side, pts.filter((_, j) => j !== i));
    li.appendChild(b); ul.appendChild(li);
  });
  $('spawns').hidden = false;
}
async function setDmSpawns(side, pts) {
  try { await run('multiplayer_setup', { mode: 'deathmatch', spawns: { [side]: pts } }, 'versus spawns updated'); await updateHistory(); await loadSpawns(); }
  catch { /* status */ }
}
function addDmSpawn(side) {
  endGroundPick(); renderer.domElement.style.cursor = 'crosshair';
  status(`click the ground for a ${spawnInfo.factions[side]} spawn - Esc cancels`);
  groundPick = { pick: (pos) => { endGroundPick(); setDmSpawns(side, [...(spawnInfo.deathmatch?.[side] || []), pos]); } };
}
function showSpawnMode() { const dm = $('sp-mode').value === 'deathmatch'; $('sp-coop').hidden = dm; $('sp-dm').hidden = !dm; }
$('sp-mode').onchange = showSpawnMode;
$('sp-add-allied').onclick = () => addDmSpawn('allied');
$('sp-add-iraqi').onclick = () => addDmSpawn('iraqi');
$('spawns-btn').onclick = () => { if ($('spawns').hidden) loadSpawns().catch((e) => status(e.message, true)); else { $('spawns').hidden = true; endGroundPick(); } };
$('sp-close').onclick = () => { $('spawns').hidden = true; endGroundPick(); clearGroup(spawnGroup); };

// ---- export mod package ----
$('export-btn').onclick = async () => {
  try {
    const opts = await run('asset_options');
    const sel = $('ex-mod'); sel.innerHTML = '';
    for (const m of opts.mods) sel.add(new Option(m, m));
    // the mod being worked on: the open mission's, else the Mod name box
    const want = [currentMission?.[0], $('mod').value.trim()].find((m) => m && opts.mods.includes(m));
    if (want) sel.value = want;
    $('ex-result').textContent = ''; $('ex-download').hidden = true; $('ex-fix').hidden = true;
    $('export-dialog').showModal();
  } catch { /* status */ }
};
$('ex-close').onclick = () => $('export-dialog').close();
$('ex-go').onclick = async () => {
  $('ex-result').textContent = 'Checking the missions and packing…'; $('ex-download').hidden = true; $('ex-go').disabled = true;
  $('ex-fix').hidden = true;
  try {
    const r = await run('export_mod_zip', { mod: $('ex-mod').value, version: $('ex-version').value.trim(),
                                            creator: $('ex-creator').value.trim(), include_sources: $('ex-sources').checked }, 'mod package written');
    const out = [`${r.zip}  (${(r.bytes / 1024).toFixed(0)} KB)`, `Missions checked: ${r.missions.join(', ') || 'none'}`, '', 'Packed:', ...r.files.map((f) => `  ${f}`)];
    if (r.replaces_game_files.length) out.push('', 'Replaces game files (your own versions):', ...r.replaces_game_files.map((f) => `  ${f}`));
    if (r.left_out.length) out.push('', 'Left out:', ...r.left_out.map((x) => `  ${x.file} - ${x.why}`));
    $('ex-result').textContent = out.join('\n');
    const a = $('ex-download'); a.href = r.url; a.download = r.url.split('/').pop(); a.hidden = false;
  } catch (e) {
    $('ex-result').textContent = e.message;
    if (/copies of game files/.test(e.message)) {         // offer the fixes the message names
      $('ex-fix').hidden = false;
      $('ex-resave').hidden = !(currentMission && currentMission[0] === $('ex-mod').value);
    }
  } finally { $('ex-go').disabled = false; }
};
$('ex-remove-copies').onclick = async () => {
  const mod = $('ex-mod').value;
  if (!confirm(`Move the files in Mods\\${mod} that are exact copies of game files out of the mod? They go into the editor's `
    + 'backups (nothing is lost) - the game keeps reading its own copies.')) return;
  try {
    const r = await run('remove_game_copies', { mod }, null);
    status(`${r.removed.length} game cop${r.removed.length === 1 ? 'y' : 'ies'} moved out of Mods\\${mod}${r.backup ? ` (backup: ${r.backup})` : ''}`);
    $('ex-go').click();
  } catch (e) { $('ex-result').textContent = e.message; }
};
$('ex-resave').onclick = async () => {
  if (!currentMission) return;
  try {
    await run('save_mission', { mod: currentMission[0], name: currentMission[1] }, `mission ${currentMission[1]} saved`);
    await updateHistory(); $('ex-go').click();
  } catch (e) { $('ex-result').textContent = e.message; }
};

// ==== Tactics (editor/tactics.py): vision cones, minefields, emplacements, air support; viewport environment modes;
// the history drawer. ====

// ---- vision cones: each enemy's sight from the game's rules (soldier_detection_profiles), drawn from head height.
// Solid cone = unaware range (the normal state); amber outline = alerted range. Width = editor guidance per AI profile
// (the game's view angle isn't decoded). The cones follow their soldier every frame, so turning one shows at once.
const visionCones = [];          // {cone, line}
let visionInfo = null;
const VISION_COLOUR = { unaware: 0x4fd16b, waiting: 0x8a8f86 };
function visionCone(p) {
  const g = new THREE.Group();
  const len = Math.max(0.5, p.range_meters), half = deg(p.fov_angle / 2);
  const geo = new THREE.ConeGeometry(len * Math.tan(half), len, 32, 1, true)
    .translate(0, -len / 2, 0).rotateX(Math.PI / 2).scale(1, 0.45, 1);     // apex at the eyes, opening forward (-z)
  const waiting = p.state !== 'unaware';
  const mat = new THREE.MeshBasicMaterial({ color: waiting ? VISION_COLOUR.waiting : VISION_COLOUR.unaware, transparent: true,
                                            opacity: waiting ? 0.08 : 0.16, depthWrite: false, side: THREE.DoubleSide });
  const cone = new THREE.Mesh(geo, mat);
  const edge = new THREE.LineSegments(new THREE.EdgesGeometry(geo, 30), new THREE.LineBasicMaterial({ color: mat.color, transparent: true, opacity: 0.45 }));
  const alert = Math.max(len, p.alert_range_meters), pts = [new THREE.Vector3()];   // alerted range: a flat fan outline
  for (let i = 0; i <= 24; i++) { const a = -half + (2 * half * i) / 24; pts.push(new THREE.Vector3(Math.sin(a) * alert, 0, -Math.cos(a) * alert)); }
  pts.push(new THREE.Vector3());
  const fan = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineDashedMaterial({ color: 0xe0b030, dashSize: 0.6, gapSize: 0.4, transparent: true, opacity: 0.7 }));
  fan.computeLineDistances();
  g.add(cone, edge, fan);
  g.traverse((c) => { c.raycast = () => {}; });
  g.userData = { line: p.line, eye: (p.eye[1] - p.pos[1]) / 100 };
  return g;
}
async function buildVision() {
  clearGroup(groups.vision); visionCones.length = 0;
  if (!groups.vision.visible || !data) { $('count-vision').textContent = ''; return; }
  try { visionInfo = await api('soldier_detection_profiles'); } catch { return; }
  for (const p of visionInfo.soldiers) { const c = visionCone(p); groups.vision.add(c); visionCones.push(c); }
  $('count-vision').textContent = visionInfo.soldiers.length;
  followVision();
}
function followVision() {        // each frame: on its soldier (also while the gizmo drags or turns it); true if one moved
  let moved = false;
  for (const c of visionCones) {
    const h = byLine.get(c.userData.line);
    if (c.visible !== !!h) { c.visible = !!h; moved = true; }
    if (!h) continue;
    const y = h.position.y + c.userData.eye;
    if (c.position.x !== h.position.x || c.position.y !== y || c.position.z !== h.position.z || c.rotation.y !== h.rotation.y) {
      c.position.set(h.position.x, y, h.position.z);
      c.rotation.set(0, h.rotation.y, 0);
      moved = true;
    }
  }
  return moved;
}

// ---- minefields: red hazard circles with a skull (editor only), the brush and the list ----
let tacticsInfo = null;
function hazardLabel(text) {
  const cv = document.createElement('canvas'); cv.width = 192; cv.height = 64;
  const ctx = cv.getContext('2d');
  ctx.fillStyle = '#000b'; ctx.beginPath(); ctx.roundRect(4, 6, 184, 52, 10); ctx.fill();
  ctx.strokeStyle = '#ff4040'; ctx.lineWidth = 4; ctx.stroke();
  ctx.fillStyle = '#ff5050'; ctx.font = 'bold 30px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillText(`☠ ${text}`, 96, 33);
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(cv), depthTest: false, transparent: true }));
  s.scale.set(2.4, 0.8, 1); s.renderOrder = 10;
  return s;
}
function hazardCircle(f) {
  const g = new THREE.Group(); g.position.copy(toThree(f.center));
  const r = f.radius / 100, pts = [];
  for (let i = 0; i < 96; i++) { const a = (i / 96) * Math.PI * 2; pts.push(new THREE.Vector3(Math.cos(a) * r, 0.15, Math.sin(a) * r)); }
  const ring = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineDashedMaterial({ color: 0xff3030, dashSize: 1, gapSize: 0.5 }));
  ring.computeLineDistances();
  const disc = new THREE.Mesh(new THREE.CircleGeometry(r, 64).rotateX(-Math.PI / 2).translate(0, 0.1, 0),
                              new THREE.MeshBasicMaterial({ color: 0xff2020, transparent: true, opacity: 0.08, depthWrite: false }));
  const label = hazardLabel(`${f.type} ×${f.remaining.length}`); label.position.y = 2.2;
  g.add(ring, disc, label);
  g.traverse((c) => { c.raycast = () => {}; });
  groups.hazards.add(g);
}
async function buildTactics() {
  try { tacticsInfo = await api('tactics_info'); } catch { tacticsInfo = null; }
  clearGroup(groups.hazards);
  for (const f of tacticsInfo?.minefields || []) hazardCircle(f);
  $('count-hazards').textContent = tacticsInfo ? tacticsInfo.minefields.length : '';
  renderMinefieldList();
  buildVision().catch(() => {});
  if (!$('mission-settings').hidden) fillAirSupport();
}
function renderMinefieldList() {
  const ul = $('mf-list'); ul.innerHTML = '';
  for (const f of tacticsInfo?.minefields || []) {
    const li = document.createElement('li');
    li.innerHTML = `<span class="grow">☠ Field ${f.id}: ${f.remaining.length}/${f.script_ids.length} ${f.type} mines, ${(f.radius / 100).toFixed(0)} m</span>`;
    const go = document.createElement('button'); go.textContent = '◎'; go.title = 'Look at it';
    go.onclick = () => focusOn(toThree(f.center));
    const del = document.createElement('button'); del.textContent = '✕'; del.title = 'Remove the field and its mines';
    del.onclick = async () => { try { await run('minefield_delete', { id: f.id }, `minefield ${f.id} removed`); await refresh(); } catch { /* status */ } };
    li.append(go, del); ul.appendChild(li);
  }
}
// The brush: drag over the ground (sand, roads, terrain). Every few metres of the stroke drops a stamp of mines; the
// spots are scattered here, each one dropped onto the surface under it, and the whole stroke becomes one minefield
// (one undo step).
let mineStroke = null;
const mineRing = new THREE.Mesh(new THREE.RingGeometry(0.94, 1, 64).rotateX(-Math.PI / 2),
                                new THREE.MeshBasicMaterial({ color: 0xff3030, depthTest: false, transparent: true, opacity: 0.9 }));
mineRing.visible = false; mineRing.renderOrder = 11; scene3.add(mineRing);
const minePreview = new THREE.Group(); scene3.add(minePreview);
const mineDotGeo = keep(new THREE.CylinderGeometry(0.22, 0.22, 0.08, 12));
const mineDotMat = keep(new THREE.MeshBasicMaterial({ color: 0xff3030 }));
function mineBrushOn() { return $('mf-brush').classList.contains('on'); }
function groundHit(e) {
  const r = renderer.domElement.getBoundingClientRect();
  ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
  return ray.intersectObjects([groups.world, groups.props], true).find((h) => h.object.isMesh && !h.object.userData.ghost);
}
function mineStamp(centre) {
  const radius = +$('mf-radius').value, n = +$('mf-count').value, spacing = 1.5;
  for (let k = 0, tries = 0; k < n && tries < n * 60; tries++) {
    const a = Math.random() * Math.PI * 2, d = radius * Math.sqrt(Math.random());
    const p = new THREE.Vector3(centre.x + Math.cos(a) * d, centre.y, centre.z + Math.sin(a) * d);
    if (mineStroke.spots.some((q) => Math.hypot(q.x - p.x, q.z - p.z) < spacing)) continue;
    const probe = new THREE.Vector3(p.x, centre.y + 30, p.z), g = groundBelow(probe);
    if (g === probe || Math.abs(g.y - centre.y) > 25) continue;        // no ground there (or a roof / cliff): skip
    mineStroke.spots.push(g.clone()); k++;
    const dot = new THREE.Mesh(mineDotGeo, mineDotMat); dot.position.copy(g); minePreview.add(dot);
  }
  mineStroke.stamps.push(centre.clone());
}
renderer.domElement.addEventListener('pointerdown', (e) => {
  if (e.button !== 0 || !mineBrushOn()) return;
  const hit = groundHit(e);
  if (!hit) return;
  e.stopImmediatePropagation(); e.preventDefault();
  orbit.enabled = false; downAt = null;          // the stroke isn't a click: nothing gets selected when it ends
  mineStroke = { spots: [], stamps: [] };
  mineStamp(hit.point);
}, { capture: true });
renderer.domElement.addEventListener('pointermove', (e) => {
  if (!mineBrushOn()) { mineRing.visible = false; return; }
  const hit = groundHit(e);
  mineRing.visible = !!hit;
  if (!hit) return;
  const r = +$('mf-radius').value;
  mineRing.position.copy(hit.point).add(new THREE.Vector3(0, 0.12, 0)); mineRing.scale.set(r, 1, r);
  if (mineStroke && hit.point.distanceTo(mineStroke.stamps.at(-1)) > r * 1.2) mineStamp(hit.point);
});
window.addEventListener('pointerup', async () => {
  if (!mineStroke) return;
  const stroke = mineStroke; mineStroke = null; orbit.enabled = !camView;
  minePreview.clear();
  if (!stroke.spots.length) return status('no ground under the brush', true);
  const c = stroke.stamps.reduce((a, p) => a.add(p), new THREE.Vector3()).divideScalar(stroke.stamps.length);
  const radius = Math.max(...stroke.stamps.map((p) => Math.hypot(p.x - c.x, p.z - c.z))) + +$('mf-radius').value;
  c.y = stroke.stamps[0].y;
  try {
    const r = await run('minefield_paint', { center_pos: toGame(c), radius: Math.min(20000, radius * 100), mine_type: $('mf-type').value,
                                             positions: stroke.spots.slice(0, 60).map(toGame) }, null);
    status(`${r.action} - field ${r.minefield.id}`);
    await refresh();
  } catch { /* status */ }
});
$('mf-brush').onclick = () => {
  const on = !mineBrushOn();
  $('mf-brush').classList.toggle('on', on);
  if (on) { cancelPlacing(); status('minefield brush: drag over the ground - Esc stops'); } else mineRing.visible = false;
};
window.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && mineBrushOn()) { $('mf-brush').classList.remove('on'); mineRing.visible = false; status('minefield brush off'); }
});

// ---- emplacements: one click stamps revetment + gun (+ gunner) on the ground ----
const EMPLACEMENT_ICON = { M2_50Cal: '⌖', NSV_127: '⌖', NSV_Rooftop: '⌂' };
async function loadEmplacements() {
  const box = $('emp-list'); if (box.dataset.loaded) return;
  let presets;
  try { presets = await api('emplacement_presets'); } catch { return; }
  box.dataset.loaded = '1'; box.innerHTML = '';
  for (const [key, p] of Object.entries(presets)) {
    const b = document.createElement('button');
    b.className = 'emp'; b.title = `${p.source}\n${p.prop ? p.prop + ' + ' : ''}${p.gun}`;
    b.textContent = `${EMPLACEMENT_ICON[key] || '+'} ${p.label}`;
    b.onclick = () => {
      endGroundPick(); $('mf-brush').classList.remove('on'); b.classList.add('on');
      renderer.domElement.style.cursor = 'crosshair';
      status(`click the ground (or a roof) for the ${p.label} - Esc cancels`);
      groundPick = { pick: async (pos) => {
        endGroundPick(); b.classList.remove('on');
        const yaw = $('jitter').checked ? Math.random() * 360 : +$('emp-yaw').value;
        try {
          const r = await run('place_emplacement', { preset: key, pos, yaw, with_gunner: $('emp-gunner').checked }, null);
          status(r.action); await refresh(); await select({ line: r.gun_line });
        } catch { /* status */ }
      } };
    };
    box.appendChild(b);
  }
  const note = document.createElement('p'); note.className = 'hint';
  note.textContent = 'DShK: the game has the NSV (Soviet 12.7 mm) instead. No mortar in the game data.';
  box.appendChild(note);
}

// ---- Mission Settings: air support ----
const PAYLOADS = { a10: [['A10_Maverick', 'AGM-65 Maverick missiles'], ['A10_GunRun', 'GAU-8 30 mm gun run']],
                   apache: [['Apache_Hellfire', 'AGM-114 Hellfire missiles']] };
function fillPayloads(selected) {
  const sel = $('as-payload'); sel.innerHTML = '';
  for (const [k, label] of PAYLOADS[$('as-aircraft').value]) sel.add(new Option(label, k));
  if (selected && [...sel.options].some((o) => o.value === selected)) sel.value = selected;
}
function fillAirSupport() {
  const a = tacticsInfo?.air_support || {};
  $('as-enabled').checked = !!a.enabled;
  $('as-aircraft').value = String(a.strike_type || 'A10_Maverick').startsWith('Apache') ? 'apache' : 'a10';
  fillPayloads(a.strike_type);
  $('as-strikes').value = a.strikes || 2; $('as-cooldown').value = a.cooldown ?? 60;
  $('as-designator').checked = a.enabled ? a.designator_id != null : true;
  $('as-lua').textContent = tacticsInfo?.lua || '';
  $('as-state').textContent = a.enabled ? `On: ${a.aircraft}, ${a.payload} - ${a.strikes} strike(s), re-arm ${a.cooldown} s`
    + (a.designator_id != null ? `, designator pick-up = script id ${a.designator_id}` : '') : 'Off';
}
$('as-aircraft').onchange = () => fillPayloads();
$('settings-btn').onclick = async () => {
  if (!$('mission-settings').hidden) { $('mission-settings').hidden = true; return; }
  if (!tacticsInfo) await buildTactics();
  fillAirSupport(); $('mission-settings').hidden = false;
};
$('ms-close').onclick = () => { $('mission-settings').hidden = true; };
$('as-apply').onclick = async () => {
  try {
    const r = await run('set_air_support', { enabled: $('as-enabled').checked, strike_type: $('as-payload').value,
                                             strikes: +$('as-strikes').value, cooldown: +$('as-cooldown').value,
                                             give_designator: $('as-designator').checked }, null);
    status(`${r.action} - compiled into the mission script when it is saved`);
    await refresh();
  } catch { /* status */ }
};

// ---- viewport environment: Day / Sandstorm / Night Ops / NVG (editor preview only - the scene isn't changed) ----
let envMode = 'day', envSaved = null;
const dust = (() => {
  const n = 9000, pos = new Float32Array(n * 3);
  for (let i = 0; i < n * 3; i++) pos[i] = (Math.random() - 0.5) * 60;
  const geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  const cv = document.createElement('canvas'); cv.width = cv.height = 32;          // a soft round speck
  const ctx = cv.getContext('2d'), grad = ctx.createRadialGradient(16, 16, 0, 16, 16, 16);
  grad.addColorStop(0, '#fff'); grad.addColorStop(0.4, '#fffa'); grad.addColorStop(1, '#fff0');
  ctx.fillStyle = grad; ctx.fillRect(0, 0, 32, 32);
  const pts = new THREE.Points(geo, new THREE.PointsMaterial({ color: 0xd9bf8c, size: 0.16, map: new THREE.CanvasTexture(cv),
                                                               transparent: true, opacity: 0.8, depthWrite: false }));
  pts.visible = false; pts.frustumCulled = false; pts.raycast = () => {};
  scene3.add(pts);
  return pts;
})();
const DUST_BOX = 60, wind = new THREE.Vector3(9, -0.6, 3.5);    // m/s
function blowDust(dt) {
  if (!dust.visible) return;
  const a = dust.geometry.attributes.position, c = camera.position, h = DUST_BOX / 2;
  for (let i = 0; i < a.count; i++) {
    let x = a.getX(i) + (wind.x + Math.sin(i * 1.7 + clock.elapsedTime) * 2) * dt, y = a.getY(i) + wind.y * dt, z = a.getZ(i) + wind.z * dt;
    if (x - c.x > h) x -= DUST_BOX; else if (x - c.x < -h) x += DUST_BOX;      // wrap round the camera
    if (y - c.y > h) y -= DUST_BOX; else if (y - c.y < -h) y += DUST_BOX;
    if (z - c.z > h) z -= DUST_BOX; else if (z - c.z < -h) z += DUST_BOX;
    a.setXYZ(i, x, y, z);
  }
  a.needsUpdate = true;
}
// NVG: the scene is rendered into a target, then shown through a green-phosphor shader (amplified luminance, grain,
// scan lines, round tube vignette).
const nvgTarget = new THREE.WebGLRenderTarget(1, 1, { samples: 4 });
const nvgMat = new THREE.ShaderMaterial({
  uniforms: { tScene: { value: nvgTarget.texture }, uTime: { value: 0 }, uGain: { value: 9.0 }, uAspect: { value: 1 } },
  vertexShader: 'varying vec2 vUv; void main() { vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }',
  fragmentShader: `uniform sampler2D tScene; uniform float uTime, uGain, uAspect; varying vec2 vUv;
    float rnd(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233)) + uTime * 37.0) * 43758.5453); }
    void main() {
      vec3 c = texture2D(tScene, vUv).rgb;
      float l = dot(c, vec3(0.299, 0.587, 0.114));
      l = 1.0 - exp(-l * uGain);                                   // amplified, soft highlights like an intensifier tube
      l += (rnd(vUv * 900.0) - 0.5) * 0.12;                        // grain
      l *= 0.9 + 0.1 * sin(vUv.y * 900.0);                         // scan lines
      vec2 d = (vUv - 0.5) * vec2(uAspect, 1.0);
      l *= smoothstep(0.62, 0.42, length(d));                      // the tube
      gl_FragColor = vec4(l * vec3(0.35, 1.0, 0.45) + vec3(0.0, 0.02, 0.0), 1.0);
    }`,
  depthTest: false, depthWrite: false,
});
const nvgScene = new THREE.Scene();
nvgScene.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), nvgMat));
const nvgCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
function renderFrame() {
  if (envMode !== 'nvg') { renderer.render(scene3, camera); return; }
  const size = renderer.getDrawingBufferSize(new THREE.Vector2());
  if (nvgTarget.width !== size.x || nvgTarget.height !== size.y) nvgTarget.setSize(size.x, size.y);
  renderer.setRenderTarget(nvgTarget); renderer.render(scene3, camera); renderer.setRenderTarget(null);
  nvgMat.uniforms.uTime.value = clock.elapsedTime % 100; nvgMat.uniforms.uAspect.value = size.x / size.y;
  renderer.render(nvgScene, nvgCam);
}
const ENV = {
  sandstorm: { bg: 0xc4a678, fog: [0xc4a678, 0.055], sky: 0xe8d2a8, ground: 0x8a6f48, hemi: 1.1, sun: 0xffe2b0, sunI: 0.35, dust: true },
  night: { bg: 0x04060c, fog: [0x04060c, 0.01], sky: 0x2a3a5c, ground: 0x0c0f16, hemi: 0.35, sun: 0x8fa8ff, sunI: 0.3 },
  nvg: { bg: 0x04060c, fog: [0x04060c, 0.006], sky: 0x2a3a5c, ground: 0x10141c, hemi: 1.1, sun: 0x8fa8ff, sunI: 0.7 },   // the moonlit night, amplified by the tube
};
function setEnvMode(mode) {
  if (mode !== 'day' && !envSaved) {
    envSaved = { bg: scene3.background.clone(), fog: scene3.fog, sky: hemi.color.clone(), ground: hemi.groundColor.clone(),
                 hemi: hemi.intensity, sun: sun.color.clone(), sunI: sun.intensity };
    atmosDefault ||= { bg: envSaved.bg.clone(), fog: envSaved.fog, sky: envSaved.sky.clone(), ground: envSaved.ground.clone(),
                       hemiI: envSaved.hemi, sun: envSaved.sun.clone(), sunDir: sunDir.clone() };
  }
  envMode = mode; requestRender();
  for (const b of document.querySelectorAll('#env-modes button')) b.classList.toggle('on', b.dataset.mode === mode);
  const e = ENV[mode];
  dust.visible = !!e?.dust;
  if (!e) {                          // day: the editor's lighting (or the Atmosphere panel's preview)
    if (envSaved) {
      scene3.background = envSaved.bg; scene3.fog = envSaved.fog; hemi.color.copy(envSaved.sky); hemi.groundColor.copy(envSaved.ground);
      hemi.intensity = envSaved.hemi; sun.color.copy(envSaved.sun); sun.intensity = envSaved.sunI; envSaved = null;
    }
    if (!$('atmos').hidden) previewAtmos();
    return status('view: day');
  }
  scene3.background = new THREE.Color(e.bg); scene3.fog = new THREE.FogExp2(e.fog[0], e.fog[1]);
  hemi.color.set(e.sky); hemi.groundColor.set(e.ground); hemi.intensity = e.hemi;
  sun.color.set(e.sun); sun.intensity = e.sunI;
  status({ sandstorm: 'view: sandstorm (editor preview - the scene is unchanged)', night: 'view: night ops (moonlight)',
           nvg: 'view: night vision goggles (green phosphor)' }[mode]);
}
for (const b of document.querySelectorAll('#env-modes button')) b.onclick = () => setEnvMode(b.dataset.mode);

// ---- history drawer: every change in order; click one to jump back (or forward) to it ----
let historyData = null;
function renderHistory(h = historyData) {
  historyData = h;
  if (!h || $('history-panel').hidden) return;
  const ul = $('hist-list'); ul.innerHTML = '';
  const current = h.undo.length;
  const rows = [{ index: 0, title: 'Opened', command: h.mission ? `mission ${h.mission[1]}` : h.level || '', done: true }, ...h.timeline];
  for (const t of rows) {
    const li = document.createElement('li');
    li.className = (t.index === current ? 'current ' : '') + (t.index > current ? 'undone' : '');
    li.innerHTML = `<span class="hn">${t.index}</span><span class="grow">${escHtml(t.title)}</span><small>${escHtml(t.command || '')}</small>`;
    li.title = t.index === current ? 'The scene now' : t.index > current ? 'Undone - click to redo up to here' : 'Click to go back to just after this';
    li.onclick = async () => {
      if (t.index === current) return;
      try { const r = await run('history_jump', { index: t.index }, null); status(`history: ${r.title} (${r.steps > 0 ? '+' : ''}${r.steps})`); await refresh(); }
      catch { /* status */ }
    };
    ul.appendChild(li);
  }
  ul.querySelector('.current')?.scrollIntoView({ block: 'nearest' });
  $('hist-count').textContent = `${current} done${h.redo.length ? `, ${h.redo.length} undone` : ''}`;
}
async function toggleHistory(open = $('history-panel').hidden) {
  $('history-panel').hidden = !open;
  $('history-btn').classList.toggle('on', open);
  if (open) renderHistory(await api('history'));
}
$('history-btn').onclick = () => toggleHistory();
$('hist-close').onclick = () => toggleHistory(false);
window.addEventListener('keydown', (e) => {
  if (e.ctrlKey && e.key.toLowerCase() === 'h' && !['INPUT', 'TEXTAREA'].includes(e.target.tagName)) { e.preventDefault(); toggleHistory(); }
});
$('tab-library').addEventListener('click', () => loadEmplacements());

// ---- tool panels (Terrain, Road, Paths & Regions, Atmosphere, Mission Settings, Spawns, History) start in the right
// column's dock; panels.js then floats each one (or keeps it docked) the way the user left it ----
for (const id of ['terrain', 'road', 'paths-panel', 'atmos', 'mission-settings', 'spawns', 'history-panel']) $('tool-dock').appendChild($(id));

// ---- top view (T): orthographic, straight down, north up; left / right drag pans, the wheel zooms ----
function fitTopFrustum() {
  const aspect = view.clientWidth / Math.max(1, view.clientHeight);
  topCam.top = topHalf; topCam.bottom = -topHalf; topCam.left = -topHalf * aspect; topCam.right = topHalf * aspect;
  topCam.updateProjectionMatrix();
}
function placeTopCamera(target, half = null) {
  if (half) { topHalf = Math.max(5, half); topCam.zoom = 1; fitTopFrustum(); }
  orbit.target.copy(target);
  topCam.position.set(target.x, target.y + 3000, target.z);
  topCam.lookAt(target); orbit.update(); requestRender();
}
const ORBIT_BUTTONS = { ...orbit.mouseButtons };
function setTopView(on) {
  if (!!topView === on) return;
  if (on) {
    exitCamView();
    topView = { offset: perspCam.position.clone().sub(orbit.target) };
    camera = topCam; orbit.object = topCam; gizmo.camera = topCam;
    orbit.enableRotate = false; orbit.mouseButtons = { LEFT: THREE.MOUSE.PAN, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN };
    placeTopCamera(orbit.target.clone(), Math.max(10, topView.offset.length() * Math.tan(deg(perspCam.fov / 2))));
    status('top view: drag pans, the wheel zooms, WASD moves - T goes back');
  } else {
    const back = topView.offset; topView = null;
    perspCam.position.copy(orbit.target).add(back);
    camera = perspCam; orbit.object = perspCam; gizmo.camera = perspCam;
    orbit.enableRotate = true; orbit.mouseButtons = { ...ORBIT_BUTTONS };
    orbit.update();
    status('perspective view');
  }
  $('top-btn').classList.toggle('on', on);
  requestRender();
}
$('top-btn').onclick = () => setTopView(!topView);

// ---- isolate (I): only the selection is drawn; everything else (world, props, markers) hidden until I again ----
let isolated = null;               // Set of record lines, or null
function applyIsolate() {
  if (!isolated) return;
  for (const g of Object.values(groups)) for (const c of g.children) c.visible = isolated.has(c.userData.line);
  requestRender();
}
function isolateSelection() {
  const lines = many.size ? [...many] : selection?.line !== undefined ? [selection.line] : [];
  if (!lines.length) return status('select something to isolate (I)', true);
  isolated = new Set(lines); applyIsolate();
  $('isolate-note').textContent = `Isolated: ${lines.length} record${lines.length > 1 ? 's' : ''} - everything else hidden (I shows it again)`;
  $('isolate-note').hidden = false; $('isolate-btn').classList.add('on');
}
function endIsolate() {
  if (!isolated) return;
  isolated = null;
  for (const g of Object.values(groups)) for (const c of g.children) c.visible = true;
  applyLayerFilter();
  $('isolate-note').hidden = true; $('isolate-btn').classList.remove('on');
}
$('isolate-btn').onclick = () => (isolated ? endIsolate() : isolateSelection());
window.addEventListener('keydown', (e) => {
  if (['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName) || document.querySelector('dialog[open]')) return;
  if (e.ctrlKey || e.altKey || e.metaKey) return;
  const k = e.key.toLowerCase();
  if (k === 'i') { e.preventDefault(); if (isolated) endIsolate(); else isolateSelection(); }
  else if (k === 't') { e.preventDefault(); setTopView(!topView); }
});

// ---- a handle for scripted UI tests (the browser console, test drivers): select records, stand in for 3D clicks ----
window.dse = {
  select: (sel, focus = false) => select(sel, focus),
  selectMany: (lines) => selectMany(lines),
  pick: (line, cat) => { if (pickMode?.accept({ line, cat })) pickMode.pick({ line, cat }); },
  finishPick: () => pickMode?.finish?.(),
  state: () => ({ selection, many: [...many], pickMode: !!pickMode, camView: !!camView,
                  translationSnap: gizmo.translationSnap, fog: !!scene3.fog, level,
                  shadows: renderer.shadowMap.enabled, shadowCasters: (() => { let n = 0; scene3.traverse((o) => { if (o.isMesh && o.castShadow) n++; }); return n; })(),
                  walkMap: walkGroup.visible ? walkGroup.children.length + walkFoot.children.length : 0 }),
  data: () => data,
  // zone chunks drawn (holders with their mesh loaded) and where a record's centre is on screen (page pixels)
  chunks: () => ({ holders: chunkHolders.size, drawn: [...chunkHolders.values()].filter((h) => h.children.length).length,
                   worldEdit, outlined: !!chunkBox }),
  screenOf: (line) => {
    const obj = byLine.get(line); if (!obj) return null;
    const box = new THREE.Box3().setFromObject(obj), c = box.isEmpty() ? obj.position.clone() : box.getCenter(new THREE.Vector3());
    const ndc = c.project(camera), r = renderer.domElement.getBoundingClientRect();
    return { x: r.left + (ndc.x + 1) / 2 * r.width, y: r.top + (1 - ndc.y) / 2 * r.height, visible: Math.abs(ndc.x) < 1 && Math.abs(ndc.y) < 1 && ndc.z < 1 };
  },
  vision: () => visionCones.map((c) => ({ line: c.userData.line, yaw: +THREE.MathUtils.radToDeg(c.rotation.y).toFixed(2),
                                          pos: c.position.toArray().map((v) => +v.toFixed(2)) })),
  envMode: () => envMode,
  setEnvMode: (m) => setEnvMode(m),
  // the view as seen: where the camera is, whether a line's object is on screen and what (if anything) is between
  view: (line) => {
    const out = { camera: toGame(camera.position), target: toGame(orbit.target), top: !!topView, isolated: isolated ? [...isolated] : null };
    const obj = line !== undefined ? byLine.get(line) : null;
    if (obj) {
      const box = new THREE.Box3().setFromObject(obj), c = box.isEmpty() ? obj.position.clone() : box.getCenter(new THREE.Vector3());
      const ndc = c.clone().project(camera);
      out.on_screen = Math.abs(ndc.x) <= 1 && Math.abs(ndc.y) <= 1 && ndc.z < 1;
      const self = new Set(); obj.traverse((x) => self.add(x));
      const dir = camera.position.clone().sub(c), dist = dir.length();
      const rc = new THREE.Raycaster(c, dir.normalize(), 0, dist);
      const hit = rc.intersectObjects([groups.world, groups.props, groups.objects], true)
        .find((h) => h.object.isMesh && !self.has(h.object) && shownInView(h.object) && h.distance > 0.05);
      out.distance = +dist.toFixed(2);
      out.blocked_by = hit ? { distance: +hit.distance.toFixed(2), line: hit.object.userData.line ?? null } : null;
    }
    return out;
  },
};

// ---- start ----
GAME = await api('game_info');
const levels = await api('list_levels');
document.title = `${GAME.short} Editor`;
for (const el of document.querySelectorAll('.app-name')) el.textContent = `${GAME.short} Editor`;
for (const l of levels) $('level').add(new Option(l, l));
// ?level=MISSION04 opens that level, &focus=<unit> puts the camera on that soldier
const urlArgs = new URLSearchParams(location.search);
const urlLevel = (urlArgs.get('level') || '').toUpperCase();
$('level').value = (levels.includes(urlLevel) && urlLevel) || ['MISSION01', 'MISSION1'].find((l) => levels.includes(l)) || levels[0];
// The server keeps its session when the page is reloaded / reopened: unsaved changes there are shown, not replaced by a
// fresh copy of the level, unless the user lets them go.
const startH = await api('history').catch(() => null);
const startName = startH?.mission ? startH.mission[1] : startH?.level;
if (startH?.level && startH.dirty && !confirm(`The editor still has unsaved changes in ${startName}.\n\n`
    + `OK: discard them and open ${$('level').value}. Cancel: keep working on ${startName}.`)) {
  level = startH.level;
  if (levels.includes(level)) $('level').value = level;
  await buildScene(true); await updateHistory();
  const hero = data.objects.find((o) => /^HERO/i.test(o.model) || /^(Bradley|Foley|Connors|Conners|Jones|Sherman)$/i.test(o.unit)) || data.objects[0];
  if (hero) focusOn(toThree(hero.pos));
  status(`${startName}: unsaved changes kept`);
} else openLevel($('level').value, false);


// ==== Vanilla lineage: every record related back to the shipped level (vanilla.py) - status badge, the changed fields
// with their shipped values and a ↺ per field, revert / restore, a see-through ghost where a moved record stood ====
const LISTS = ['props', 'objects', 'waypoints', 'regions', 'cameras', 'sounds', 'switches', 'emitters', 'chunks', 'zones', 'lights'];
function sceneItem(line) {
  for (const k of LISTS) { const it = data?.[k]?.find?.((x) => x.line === line); if (it) return it; }
  return null;
}
const fmtVal = (v) => (Array.isArray(v) ? v.map((x) => (typeof x === 'number' ? +x.toFixed(2) : x)).join(', ') : String(v));
const vanGhostMat = keep(new THREE.MeshBasicMaterial({ color: 0xff4fd8, wireframe: true, transparent: true, opacity: 0.55, depthWrite: false }));
const vanLineMat = keep(new THREE.LineDashedMaterial({ color: 0xff4fd8, dashSize: 0.4, gapSize: 0.25, transparent: true, opacity: 0.9 }));
let vanGhost = null;
function clearVanillaGhost() {
  if (!vanGhost) return;
  scene3.remove(vanGhost);
  vanGhost.traverse((c) => { if (c.userData.vanLine) c.geometry.dispose(); });   // the model clone shares its geometry
  vanGhost = null; requestRender();
}
function showVanillaGhost(item) {
  clearVanillaGhost();
  const holder = byLine.get(item.line);
  if (!holder || !item.vanilla_pos) return;
  const g = new THREE.Group();
  // the holder's meshes in its own space, sharing their geometry (a clone would copy userData, which points back at
  // the holder; skinned meshes show their bind pose)
  const shape = new THREE.Group();
  holder.updateMatrixWorld(true);
  const inv = holder.matrixWorld.clone().invert();
  holder.traverse((c) => {
    if (!(c.isMesh || c.isLine) || !c.geometry) return;
    const m = c.isMesh ? new THREE.Mesh(c.geometry, vanGhostMat) : new THREE.Line(c.geometry, vanLineMat);
    m.matrixAutoUpdate = false; m.matrix.multiplyMatrices(inv, c.matrixWorld);
    m.userData = { ghost: true }; m.raycast = () => {};
    shape.add(m);
  });
  shape.scale.copy(holder.scale);
  shape.position.copy(toThree(item.vanilla_pos));
  const r = item.vanilla_rot;
  if (r) shape.rotation.set(-deg(r[0]), -deg(r[1]), deg(r[2]), 'YXZ');
  g.add(shape);
  const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([toThree(item.vanilla_pos), holder.position.clone()]), vanLineMat);
  line.computeLineDistances(); line.userData = { ghost: true, vanLine: true }; line.raycast = () => {};
  g.add(line);
  vanGhost = g; scene3.add(g); requestRender();
}
async function showVanilla(rec, item) {
  const info = await api('vanilla_info', { line: rec.line });
  if (selection?.line !== rec.line) return;
  if (info.unit) {
    const u = info.unit;
    $('unit-info').innerHTML = `Soldier type <a href="#records/UNITS/${encodeURIComponent(u.key)}">${escHtml(u.key)}</a>: `
      + `experience ${escHtml(u.experience || '-')}, role ${escHtml(u.role || '-')}, stats ${escHtml(u.stats || '-')}. `
      + 'Shared by every soldier of this type - change it in Records (it goes into the mod as UNITS.replace.txt).';
  }
  if (!info.has_vanilla_scene || !info.status) return;
  $('vanilla-row').hidden = false;
  let html = '';
  if (info.dll_critical) {
    html += `<p class="crit"><span class="badge critical">🛡 DLL Critical: script id ${info.dll_critical.script_id}</span><br>`
      + `<span class="hint">API slots ${info.dll_critical.slots.join(', ')} - the id can't be changed and deleting needs a confirmation `
      + '(hide it in a layer instead).</span></p>';
  }
  if (info.status === 'custom') {
    html += '<p class="hint">Not in the shipped level: added in the editor, or a shipped record renamed / renumbered '
      + '(its original then shows under Changes… › Deleted).</p>';
  } else {
    const v = info.vanilla;
    html += `<p class="hint">Shipped as line ${info.vanilla_line}${v.name !== undefined ? ` "${escHtml(v.name)}"` : ''}`
      + `${v.script_id !== undefined ? `, script id ${v.script_id}` : ''}${v.model !== undefined ? `, model ${escHtml(v.model)}` : ''}.</p>`;
    if (v.pos) html += `<p class="hint">Vanilla pos: (${fmtVal(v.pos)})${info.status === 'modified' && item?.vanilla_pos ? ' - the magenta ghost in the view' : ''}</p>`;
    if (info.changed?.length) {
      html += '<table>' + info.changed.map((c) => `<tr><td class="k">${escHtml(c.field)}</td><td>${escHtml(fmtVal(c.vanilla ?? '(none)'))}</td></tr>`).join('')
        + '</table><p class="hint">Vanilla values of the changed fields (↺ in the field list puts one back).</p>';
    } else html += '<p class="hint">Unchanged.</p>';
  }
  $('van-body').innerHTML = html;
  $('van-revert').hidden = info.status !== 'modified';
  $('van-revert').onclick = async () => {
    try { await run('revert_record', { line: rec.line }, 'reverted to vanilla'); } catch { return; }
    await refresh(); await select({ line: rec.line }); updateHistory();
  };
  // the field list: changed fields in orange with their own ↺
  const changed = new Map((info.changed || []).map((c) => [c.field, c]));
  for (const tr of [...$('fields').children, ...$('fields-adv').children]) {
    const c = changed.get(tr.children[0]?.textContent);
    if (!c) continue;
    tr.classList.add('van-changed'); tr.title = `vanilla: ${fmtVal(c.vanilla ?? '(none)')}`;
    const b = document.createElement('button'); b.textContent = '↺'; b.title = `back to the vanilla value (${fmtVal(c.vanilla ?? '(none)')})`;
    b.onclick = async () => {
      try { await run('revert_field', { line: rec.line, field: c.field }, `${c.field} reverted`); } catch { return; }
      await refresh(); await select({ line: rec.line }); updateHistory();
    };
    tr.children[1].appendChild(b);
  }
  if (info.status === 'modified' && item?.vanilla_pos) showVanillaGhost(item);
}

// Changes… dialog: modified / added / deleted against the shipped level
function vanRow(text, title, onClick, btn) {
  const li = document.createElement('li');
  li.innerHTML = `<span title="${escHtml(title)}">${escHtml(text)}</span>`;
  li.onclick = onClick;
  if (btn) {
    const b = document.createElement('button'); b.textContent = btn.text; b.title = btn.title;
    b.onclick = (e) => { e.stopPropagation(); btn.run(); };
    li.appendChild(b);
  }
  return li;
}
async function openVanilla() {
  if (!data) return;
  if (!data.vanilla) { status('this level has no shipped original (a new level)', true); return; }
  let deleted = [];
  try { deleted = (await run('vanilla_deleted')).deleted; } catch { return; }
  const mod = $('van-modified'), cus = $('van-custom'), del = $('van-deleted');
  mod.innerHTML = cus.innerHTML = del.innerHTML = '';
  const go = (line) => async () => { $('vanilla-dialog').close(); await select({ line }, true); };
  for (const k of LISTS) for (const it of data[k] || []) {
    const label = `${it.unit || it.name || it.model || k} · line ${it.line}${it.script_id !== undefined ? ` · id ${it.script_id}` : ''}`;
    if (it.lineage === 'modified') {
      mod.appendChild(vanRow(label, k, go(it.line), { text: '↺', title: 'revert to vanilla', run: async () => {
        try { await run('revert_record', { line: it.line }, 'reverted to vanilla'); } catch { return; }
        await refresh(); updateHistory(); openVanilla();
      } }));
    } else if (it.lineage === 'custom') cus.appendChild(vanRow(label, k, go(it.line)));
  }
  for (const d of deleted) {
    const label = `${d.kind} ${d.unit || d.name || d.id}${d.script_id !== undefined ? ` · id ${d.script_id}` : ''}`;
    del.appendChild(vanRow(label, `shipped line ${d.vanilla_line}${d.pos ? ` at (${fmtVal(d.pos)})` : ''}`,
      () => { if (d.pos) { $('vanilla-dialog').close(); focusOn(toThree(d.pos)); requestRender(); } },
      { text: 'Restore', title: 'put it back as shipped', run: async () => {
        try { await run('restore_record', { vanilla_line: d.vanilla_line }, 'restored from vanilla'); } catch { return; }
        await refresh(); updateHistory(); openVanilla();
      } }));
  }
  for (const [ul, empty] of [[mod, 'Nothing changed.'], [cus, 'Nothing added.'], [del, 'Nothing deleted.']])
    if (!ul.children.length) ul.innerHTML = `<li class="hint">${empty}</li>`;
  const v = data.vanilla;
  $('van-sum').textContent = `${v.vanilla} unchanged · ${v.modified} modified · ${v.custom} added · ${deleted.length} deleted`;
  if (!$('vanilla-dialog').open) $('vanilla-dialog').showModal();
}
$('vanilla-btn').onclick = () => openVanilla().catch(() => {});
$('van-close').onclick = () => $('vanilla-dialog').close();

// Atmosphere: back to the shipped values
$('at-reset').onclick = async () => {
  if (!confirm('Put time of day, fog, sky layers, colour keys and the sun back as the game ships them? (Undo brings yours back.)')) return;
  try { await run('reset_atmosphere', {}, 'atmosphere reset to vanilla'); } catch { return; }
  updateHistory(); await openAtmos();
};

// Briefing: refill from the base mission's own texts
$('bf-reset').onclick = async () => {
  const base = (await run('briefing_get')).base;
  if (!base?.base) { $('bf-result').textContent = 'This level has no base-mission briefing.'; return; }
  if (base.texts_missing) { $('bf-result').textContent = 'The base mission\'s texts are unknown - start the editor with --game.'; return; }
  $('bf-title').value = base.title; $('bf-date').value = base.intro_date || base.date;
  $('bf-location').value = base.intro_location || base.location; $('bf-text').value = base.briefing_text;
  $('bf-result').textContent = 'Filled from the base mission\'s own briefing - Save dossier keeps it.';
  drawBriefing();
};

// Script: compare with the editor's template (line diff), reset to it
function lineDiff(a, b) {           // a = template lines, b = script lines -> [{t: 'same' | 'del' | 'add', s, ai, bi}]
  const n = a.length, m = b.length;
  if (n * m > 25e6) return b.map((s, bi) => ({ t: 'add', s, bi }));
  const w = m + 1, L = new Uint32Array((n + 1) * w);
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
    L[i * w + j] = a[i] === b[j] ? L[(i + 1) * w + j + 1] + 1 : Math.max(L[(i + 1) * w + j], L[i * w + j + 1]);
  const out = []; let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { out.push({ t: 'same', s: b[j], ai: i, bi: j }); i++; j++; }
    else if (L[(i + 1) * w + j] >= L[i * w + j + 1]) out.push({ t: 'del', s: a[i], ai: i++ });
    else out.push({ t: 'add', s: b[j], bi: j++ });
  }
  while (i < n) out.push({ t: 'del', s: a[i], ai: i++ });
  while (j < m) out.push({ t: 'add', s: b[j], bi: j++ });
  return out;
}
function editorRanges(text) {       // 0-based [first, last] lines of the "-- @editor begin ... -- @editor end" blocks
  const out = []; let start = null;
  text.split('\n').forEach((l, i) => {
    const s = l.trim();
    if (s.startsWith('-- @editor begin')) start = i;
    else if (start !== null && s.startsWith('-- @editor end')) { out.push([start, i]); start = null; }
  });
  return out;
}
const inRanges = (k, ranges) => k !== undefined && ranges.some(([f, l]) => k >= f && k <= l);
async function showScriptDiff(on) {
  $('script-diff').hidden = !on; $('code').hidden = on;
  $('script-compare').textContent = on ? 'Back to editing' : 'Compare with template';
  if (!on) { setTimeout(() => cm.refresh(), 0); return; }
  const r = await run('get_vanilla_script');
  const text = cm.getValue();                       // what is in the editor now (saved or not)
  const ranges = editorRanges(text);
  const d = lineDiff(r.template.split('\n'), text.split('\n'));
  const pre = $('diff-lines'); pre.innerHTML = '';
  let adds = 0, dels = 0;
  for (const x of d) {
    const div = document.createElement('div');
    const editor = x.t === 'del' ? inRanges(x.ai, r.template_editor_ranges) : inRanges(x.bi, ranges);
    div.className = editor && x.t !== 'del' ? 'd-editor' : 'd-' + x.t;
    if (x.t === 'add' && !editor) adds++;
    if (x.t === 'del' && !editor) dels++;
    div.textContent = `${x.t === 'add' ? '+' : x.t === 'del' ? '-' : ' '} ${x.bi !== undefined ? String(x.bi + 1).padStart(4) : '    '}  ${x.s}`;
    pre.appendChild(div);
  }
  $('diff-legend').innerHTML = `<span class="d-add">+ your code (${adds} lines)</span><span class="d-editor">editor blocks (rewritten on save)</span>`
    + `<span class="d-same">from the template</span><span class="d-del">- template lines you removed (${dels})</span>`
    + `<br>Template = what Save as mission writes for a new mission from ${escHtml(r.base_dll || 'this level')}.`;
}
$('script-compare').onclick = () => showScriptDiff($('script-diff').hidden).catch(() => showScriptDiff(false));
$('script-reset').onclick = async () => {
  if (!confirm('Replace the script with the editor\'s template? Your own code in it is lost (Undo can\'t bring it back).')) return;
  let r;
  try { r = await run('reset_script', {}, 'script reset to the template'); } catch { return; }
  cm.setValue(r.text); $('script-state').textContent = 'saved';
  if (!$('script-diff').hidden) showScriptDiff(true);
};

// Atmosphere: far fog band and the cloud layers
function fillSky() {
  $('at-xfog-start').value = atmos.far_fog_start != null ? Math.round(atmos.far_fog_start / 100) : '';
  $('at-xfog-stop').value = atmos.far_fog_stop != null ? Math.round(atmos.far_fog_stop / 100) : '';
  const box = $('at-sky'); box.innerHTML = '';
  for (const l of atmos.sky_layers || []) {
    const row = document.createElement('div'); row.className = 'row sky-layer';
    row.innerHTML = `<strong title="SKYTEXTURE">${escHtml(l.texture ?? 'layer ' + l.index)}</strong>
      <label class="inline" title="SKYTILEFACTOR">tile <input type="number" step="0.5" min="0.1" value="${l.tile ?? ''}"></label>
      <label class="inline" title="SKYSPEED x / z">speed <input type="number" step="0.005" value="${l.speed?.[0] ?? ''}"> <input type="number" step="0.005" value="${l.speed?.[1] ?? ''}"></label>
      <label class="inline" title="SKYCURVATURE">curve <input type="number" step="0.1" min="0" value="${l.curvature ?? ''}"></label>
      <label class="inline" title="SKYHEIGHT (m)">height <input type="number" step="10" min="1" value="${l.height != null ? l.height / 100 : ''}"> m</label>`;
    const [tile, sx, sz, curve, height] = row.querySelectorAll('input');
    const send = () => commitAtmos({ sky: [{ index: l.index, tile: +tile.value, curvature: +curve.value, height: +height.value * 100,
      speed: [+sx.value, +sz.value, ...(l.speed || []).slice(2)] }] }, `cloud layer ${l.index + 1} changed`);
    for (const i of [tile, sx, sz, curve, height]) i.onchange = send;
    box.appendChild(row);
  }
}
for (const id of ['at-xfog-start', 'at-xfog-stop']) {
  $(id).onchange = () => {
    const a = +$('at-xfog-start').value, b = +$('at-xfog-stop').value;
    if (b <= a) { status('far fog: the end must be beyond the start', true); return; }
    commitAtmos({ far_fog_start: a * 100, far_fog_stop: b * 100 }, `far fog ${a}-${b} m`);
  };
}

// ---- host object for add-on scripts (web/worldtools.js): read access to the view, the scene data and the selection,
// and the same api / run / refresh / select the panels here use. Nothing in it changes the scene by itself.
window.editorHost = {
  THREE, hooks: hostHooks, scene3, groups, byLine, toGame, toThree, deg, gameForward,
  api, run, status, refresh, select, selectMany, requestRender, focusOn, surfaceAt, pastePoint,
  get renderer() { return renderer; }, get camera() { return camera; }, get orbit() { return orbit; },
  get data() { return data; }, get selection() { return selection; }, get many() { return many; },
  selectedLines() { return many.size ? [...many] : selection?.line !== undefined ? [selection.line] : []; },
  viewCamera(c) { enterCamView(c); }, exitCameraView() { exitCamView(); },
  slowCommands: SLOW_COMMANDS,
  pickGround(hint, fn) { groundPick = { pick: (p) => { endGroundPick(); fn(p); } }; renderer.domElement.style.cursor = 'crosshair'; status(hint); },
  cancelGroundPick() { endGroundPick(); },
};
window.dispatchEvent(new Event('editor-host-ready'));
