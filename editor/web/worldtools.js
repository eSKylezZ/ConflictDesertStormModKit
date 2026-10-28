// World Tools: Render Window tools modelled on the Fallout 4 Creation Kit (CreationKit.exe dialogs read with REA -
// ideas, not code): Object Palette brush, Duplicate Selection (arrays), Apply Transform, Search & Replace base object,
// Normal Pathing Test, Go to Location, hide / freeze (CK layers), Picking Preferences, Ref Placements export.
// Scene changes go through the server commands in editor/worldtools.py; hide / freeze / picking are view settings only.
(() => {
  const el = (tag, props = {}, ...kids) => {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (k === 'class') e.className = v;
      else if (k === 'text') e.textContent = v;
      else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
      else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v === true ? '' : v);
    }
    for (const k of kids) if (k !== null && k !== undefined) e.append(k);
    return e;
  };
  const num = (v, d = 0) => (Number.isFinite(+v) && v !== '' ? +v : d);
  const store = {
    get(k, d) { try { const v = localStorage.getItem('worldTools.' + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem('worldTools.' + k, JSON.stringify(v)); } catch { /* private window */ } },
  };

  function start(H) {
    const { THREE } = H;
    const $ = (id) => document.getElementById(id);
    const lines = () => H.selectedLines();
    const need = () => { const l = lines(); if (!l.length) { H.status('select something first (Shift+click for several)', true); return null; } return l; };
    const after = async (r) => { await H.refresh(); const l = r?.lines?.filter(Boolean) || []; if (l.length > 1) H.selectMany(l); else if (l.length) await H.select({ line: l[0] }); };

    // ---------- panel ----------
    const TABS = [['pal', 'Palette'], ['dup', 'Array'], ['xf', 'Transform'], ['rep', 'Replace'], ['link', 'Prefab links'],
                  ['spawn', 'Spawn lists'], ['cam', 'Camera paths'], ['terr', 'Terrain'], ['path', 'Path test'], ['check', 'Checks'],
                  ['doors', 'Doors'], ['notes', 'Notes'], ['view', 'View'], ['keys', 'Keys'], ['csv', 'CSV']];
    const panel = el('div', { id: 'world-tools', class: 'float-panel', hidden: true },
      el('div', { class: 'console-head' }, el('strong', { text: 'World Tools' }), el('span', { class: 'grow' }),
        el('button', { id: 'wt-close', text: 'Close' })),
      el('div', { class: 'tabs', id: 'wt-tabs' }, ...TABS.map(([k, t]) => el('button', { 'data-tab': k, text: t }))),
      el('div', { class: 't-body', id: 'wt-body' }));
    document.body.append(panel);
    const pages = {};
    for (const [k] of TABS) { pages[k] = el('div', { class: 'wt-page', hidden: true }); $('wt-body').append(pages[k]); }
    function showTab(k) {
      for (const [t] of TABS) { pages[t].hidden = t !== k; panel.querySelector(`[data-tab=${t}]`).classList.toggle('on', t === k); }
      store.set('tab', k);
      if (k !== 'pal') paintOn(false);
      if (k === 'rep') fillReplaceFind();
    }
    $('wt-tabs').onclick = (e) => { const k = e.target.dataset?.tab; if (k) showTab(k); };
    $('wt-close').onclick = () => { panel.hidden = true; paintOn(false); clearPath(); };
    function open(tab) { panel.hidden = false; showTab(tab || store.get('tab', 'pal')); loadLibrary().catch(() => {}); }

    // menu entry: World ▾ › World Tools…
    const worldMenu = $('terrain-btn')?.parentElement;
    if (worldMenu) worldMenu.append(el('button', { id: 'worldtools-btn', title: 'Object Palette brush, arrays, exact transforms, replace a type, path test, hide / freeze, CSV placements (Creation Kit style)', text: 'World Tools…', onclick: () => (panel.hidden ? open() : (panel.hidden = true)) }));

    // ---------- library (templates) ----------
    let lib = null;
    const libList = el('datalist', { id: 'wt-lib' }); document.body.append(libList);
    async function loadLibrary(force = false) {
      if (lib && !force) return lib;
      lib = await H.api('library', { kind: 'all' });
      libList.innerHTML = '';
      for (const e of lib) libList.append(el('option', { value: `${e.label} [${e.kind} ${e.id}]` }));
      return lib;
    }
    const libId = (text) => { const m = String(text).match(/\[(?:prop|object) (\d+)\]\s*$/); return m ? +m[1] : null; };
    const libLabel = (id) => { const e = lib?.find((x) => x.id === id); return e ? `${e.label} [${e.kind} ${e.id}]` : ''; };
    const libFromSelection = () => {
      const d = H.data, l = lines()[0]; if (!d || !l) return null;
      const p = d.props.find((x) => x.line === l), o = d.objects.find((x) => x.line === l);
      if (p) return lib?.find((e) => e.kind === 'prop' && e.model.toUpperCase() === String(p.model).toUpperCase());
      if (o) return lib?.find((e) => e.kind === 'object' && e.model.toUpperCase() === String(o.model).toUpperCase()
                                 && String(e.unit || '').toUpperCase() === String(o.unit || '').toUpperCase());
      return null;
    };

    // ================================================================ Object Palette ================
    let palette = store.get('palette', { name: '', entries: [] });
    const P = pages.pal;
    P.append(
      el('div', { class: 'row' }, el('select', { id: 'wt-pal-saved', title: 'Saved palettes' }),
        el('button', { id: 'wt-pal-load', text: 'Load' }), el('button', { id: 'wt-pal-del', text: 'Delete' })),
      el('div', { class: 'row' }, el('input', { id: 'wt-pal-add', list: 'wt-lib', placeholder: 'Add a prop or object…' }),
        el('button', { id: 'wt-pal-addsel', title: 'Add the selected prop / object type', text: '+ Selected' })),
      el('div', { id: 'wt-pal-entries', class: 'wt-entries' }),
      el('fieldset', {}, el('legend', { text: 'Brush' }),
        el('label', { class: 'row check' }, el('input', { type: 'checkbox', id: 'wt-paint' }), ' Paint (left-drag in the view; Esc stops)'),
        el('label', {}, 'Radius (m) ', el('input', { id: 'wt-radius', type: 'range', min: 1, max: 40, value: store.get('radius', 6) })),
        el('label', {}, 'Density ', el('input', { id: 'wt-density', type: 'range', min: 1, max: 12, value: store.get('density', 3), title: 'objects dropped per brush step' })),
        el('label', {}, 'Spacing (m) ', el('input', { id: 'wt-spacing', type: 'number', min: 0, step: 0.5, value: store.get('spacing', 2), style: 'width:4em' }))),
      el('div', { class: 'row' }, el('input', { id: 'wt-pal-name', placeholder: 'Palette name' }), el('button', { id: 'wt-pal-save', text: 'Save palette' })),
      el('p', { class: 'hint', text: 'Each object: weight (how often), facing range, tilt ±°, size range and sink (cm) - props can lean with the slope (conform). One stroke = one undo step.' }));

    function entryRow(e, i) {
      const inp = (key, idx, attrs = {}) => {
        const v = idx === undefined ? e[key] : e[key][idx];
        return el('input', { type: 'number', value: v, step: attrs.step || 1, style: 'width:3.6em', title: attrs.title || key,
          onchange: (ev) => { if (idx === undefined) e[key] = +ev.target.value; else e[key][idx] = +ev.target.value; savePal(); } });
      };
      const prop = e.kind === 'prop';
      return el('div', { class: 'wt-entry' },
        el('div', { class: 'row' }, el('strong', { text: e.label, title: e.model }), el('span', { class: 'grow' }),
          el('label', { title: 'weight' }, '×', inp('weight', undefined, { step: 0.5, title: 'weight: how often it is picked' })),
          el('button', { text: '✕', title: 'remove', onclick: () => { palette.entries.splice(i, 1); savePal(); drawPal(); } })),
        el('div', { class: 'row' }, 'yaw', inp('yaw', 0, { title: 'facing from (°)' }), '-', inp('yaw', 1, { title: 'facing to (°)' }),
          'sink', inp('sink', 0, { title: 'sink from (cm)' }), '-', inp('sink', 1, { title: 'sink to (cm)' })),
        prop ? el('div', { class: 'row' }, 'size', inp('scale', 0, { step: 0.05, title: 'size from' }), '-', inp('scale', 1, { step: 0.05, title: 'size to' }),
          'tilt ±', inp('tilt', undefined, { title: 'random lean (°)' }),
          el('label', { title: 'lean with the ground (up to the max tilt)' }, el('input', { type: 'checkbox', checked: e.conform || null,
            onchange: (ev) => { e.conform = ev.target.checked; savePal(); } }), 'conform')) : null);
    }
    function drawPal() {
      const box = $('wt-pal-entries'); box.innerHTML = '';
      if (!palette.entries.length) box.append(el('p', { class: 'hint', text: 'No objects yet - add some from the library or the selection.' }));
      palette.entries.forEach((e, i) => box.append(entryRow(e, i)));
      $('wt-pal-name').value = palette.name || '';
    }
    const savePal = () => store.set('palette', palette);
    function addEntry(t) {
      if (!t) return H.status('pick a library entry', true);
      palette.entries.push({ kind: t.kind, model: t.model, unit: t.unit, label: t.label, weight: 1, yaw: [0, 360], tilt: 0,
                             scale: [1, 1], sink: [0, 0], conform: t.kind === 'prop', max_tilt: 30 });
      savePal(); drawPal();
    }
    $('wt-pal-add').onchange = async (ev) => { await loadLibrary(); const id = libId(ev.target.value); addEntry(lib.find((x) => x.id === id)); ev.target.value = ''; };
    $('wt-pal-addsel').onclick = async () => { await loadLibrary(); addEntry(libFromSelection()); };
    async function fillSaved() {
      const list = await H.api('palette_list').catch(() => []);
      const sel = $('wt-pal-saved'); sel.innerHTML = '';
      for (const p of list) sel.add(new Option(`${p.name} (${p.objects.length})`, p.name));
    }
    $('wt-pal-load').onclick = async () => {
      const name = $('wt-pal-saved').value; if (!name) return;
      const p = await H.run('palette_get', { name });
      palette = { name: p.name, entries: p.entries.filter((e) => !e.missing) };
      const missing = p.entries.filter((e) => e.missing).length;
      savePal(); drawPal(); H.status(`palette ${p.name} loaded${missing ? ` - ${missing} object(s) unknown here` : ''}`, !!missing);
    };
    $('wt-pal-del').onclick = async () => { const name = $('wt-pal-saved').value; if (name && confirm(`Delete palette ${name}?`)) { await H.run('palette_delete', { name }, 'palette deleted'); fillSaved(); } };
    $('wt-pal-save').onclick = async () => {
      const name = $('wt-pal-name').value.trim(); if (!name) return H.status('name the palette', true);
      await H.run('palette_save', { name, entries: palette.entries }, `palette ${name} saved`);
      palette.name = name; savePal(); fillSaved();
    };
    for (const id of ['wt-radius', 'wt-density', 'wt-spacing']) $(id).onchange = (ev) => store.set(id.slice(3), +ev.target.value);

    // brush: a ring follows the surface; a stroke collects random points under it (raycast straight down, with the
    // surface normal for conform) and sends them as one palette_paint
    const canvas = () => H.renderer.domElement;
    const ray = new THREE.Raycaster(), down = new THREE.Vector3(0, -1, 0);
    const ring = new THREE.Mesh(new THREE.RingGeometry(0.96, 1, 48).rotateX(-Math.PI / 2),
                                new THREE.MeshBasicMaterial({ color: 0x6fd0ff, depthTest: false, transparent: true }));
    ring.renderOrder = 10; ring.visible = false; H.scene3.add(ring);
    const dots = new THREE.Group(); H.scene3.add(dots);
    const dotGeo = new THREE.SphereGeometry(0.12, 8, 6), dotMat = new THREE.MeshBasicMaterial({ color: 0x6fd0ff, depthTest: false });
    let stroke = null;
    const surfaces = () => [H.groups.world, H.groups.props].filter(Boolean);
    function hitAt(ev) {
      const r = canvas().getBoundingClientRect();
      ray.setFromCamera(new THREE.Vector2(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1), H.camera);
      return ray.intersectObjects(surfaces(), true).find((h) => h.object.isMesh && h.object.visible) || null;
    }
    function groundAt(x, z, yTop) {
      ray.set(new THREE.Vector3(x, yTop + 30, z), down);
      const h = ray.intersectObjects(surfaces(), true).find((q) => q.object.isMesh && q.object.visible);
      if (!h) return null;
      const n = h.face ? h.face.normal.clone().transformDirection(h.object.matrixWorld) : new THREE.Vector3(0, 1, 0);
      if (n.y < 0) n.negate();
      return { pos: H.toGame(h.point), normal: [+n.x.toFixed(4), +n.y.toFixed(4), +(-n.z).toFixed(4)], p: h.point };
    }
    function dab(centre) {
      const rad = num($('wt-radius').value, 6), n = num($('wt-density').value, 3);
      for (let i = 0; i < n; i++) {
        const a = Math.random() * Math.PI * 2, d = Math.sqrt(Math.random()) * rad;
        const g = groundAt(centre.x + Math.cos(a) * d, centre.z + Math.sin(a) * d, centre.y);
        if (!g) continue;
        stroke.points.push({ pos: g.pos, normal: g.normal });
        const m = new THREE.Mesh(dotGeo, dotMat); m.position.copy(g.p); dots.add(m);
      }
      stroke.last = centre.clone(); H.requestRender();
    }
    function paintOn(on) {
      $('wt-paint').checked = !!on; ring.visible = false;
      if (!on && stroke) { stroke = null; dots.clear(); H.orbit.enabled = true; }
      H.requestRender();
    }
    $('wt-paint').onchange = (ev) => { if (ev.target.checked && !palette.entries.length) { H.status('add objects to the palette first', true); ev.target.checked = false; } };
    const painting = () => !panel.hidden && !pages.pal.hidden && $('wt-paint').checked;
    canvas().addEventListener('pointerdown', (ev) => {
      if (!painting() || ev.button !== 0 || ev.altKey || ev.ctrlKey) return;
      const h = hitAt(ev); if (!h) return;
      ev.stopImmediatePropagation(); ev.preventDefault();
      H.orbit.enabled = false; try { canvas().setPointerCapture(ev.pointerId); } catch { /* synthetic events */ }
      stroke = { points: [], last: null }; dab(h.point);
    }, { capture: true });
    canvas().addEventListener('pointermove', (ev) => {
      if (!painting()) { if (ring.visible) { ring.visible = false; H.requestRender(); } return; }
      const h = hitAt(ev); ring.visible = !!h;
      if (h) { ring.position.copy(h.point).add(new THREE.Vector3(0, 0.05, 0)); ring.scale.setScalar(num($('wt-radius').value, 6)); }
      if (stroke && h && (ev.buttons & 1)) {
        ev.stopImmediatePropagation();
        if (h.point.distanceTo(stroke.last) >= Math.max(0.5, num($('wt-radius').value, 6) * 0.6)) dab(h.point);
      }
      H.requestRender();
    }, { capture: true });
    canvas().addEventListener('pointerup', async (ev) => {
      if (!stroke) return;
      ev.stopImmediatePropagation();
      const s = stroke; stroke = null; H.orbit.enabled = true;
      dots.clear(); H.requestRender();
      if (!s.points.length) return H.status('no ground under the brush', true);
      const pts = s.points.slice(0, 500);
      const r = await H.run('palette_paint', { entries: palette.entries, points: pts, spacing: num($('wt-spacing').value, 0) * 100 },
                            'painted').catch(() => null);
      if (r) { await after(r); H.status(`painted ${r.placed} object${r.placed === 1 ? '' : 's'} (Ctrl+Z undoes the stroke)`); }
    }, { capture: true });
    window.addEventListener('keydown', (ev) => { if (ev.key === 'Escape' && painting()) { paintOn(false); H.status('painting off'); } });

    // ================================================================ Duplicate Selection (array) ================
    pages.dup.append(
      el('label', {}, 'Copies ', el('input', { id: 'wt-d-count', type: 'number', min: 1, max: 200, value: 3, style: 'width:4em' })),
      el('div', { class: 'row' }, 'Offset (m)', ...['x', 'y', 'z'].map((a) => el('input', { id: 'wt-d-' + a, type: 'number', step: 0.5, value: a === 'x' ? 4 : 0, style: 'width:4em', title: a }))),
      el('label', {}, 'Turn each copy (°) ', el('input', { id: 'wt-d-yaw', type: 'number', value: 0, style: 'width:4em' })),
      el('div', { class: 'row' }, el('input', { id: 'wt-d-prefix', placeholder: 'Prefix', style: 'width:6em' }), el('input', { id: 'wt-d-suffix', placeholder: 'Suffix', style: 'width:6em' })),
      el('div', { class: 'row' }, el('input', { id: 'wt-d-search', placeholder: 'Search', style: 'width:6em' }), el('input', { id: 'wt-d-replace', placeholder: 'Replace', style: 'width:6em' })),
      el('button', { id: 'wt-d-go', text: 'Duplicate selection' }),
      el('p', { class: 'hint', text: 'Copy k is moved k × offset and turned k × the angle about the group centre - fence lines, rows of tents, a ring of sandbags. Names (props, regions) get the prefix / suffix; objects get new script ids, vehicles their own crews.' }));
    $('wt-d-go').onclick = async () => {
      const l = need(); if (!l) return;
      const r = await H.run('duplicate_array', { lines: l, count: num($('wt-d-count').value, 1),
        offset: ['x', 'y', 'z'].map((a) => num($('wt-d-' + a).value) * 100),
        yaw_step: num($('wt-d-yaw').value), prefix: $('wt-d-prefix').value, suffix: $('wt-d-suffix').value,
        search: $('wt-d-search').value, replace: $('wt-d-replace').value }, 'duplicated').catch(() => null);
      if (r) { await after(r); H.status(`${r.copies} cop${r.copies === 1 ? 'y' : 'ies'} - ${r.records} records`); }
    };

    // ================================================================ Apply Transform ================
    pages.xf.append(
      el('div', { class: 'row' }, 'Move (m)', ...['x', 'y', 'z'].map((a) => el('input', { id: 'wt-x-' + a, type: 'number', step: 0.1, value: 0, style: 'width:4em', title: a }))),
      el('label', {}, 'Turn (°) ', el('input', { id: 'wt-x-yaw', type: 'number', value: 0, style: 'width:4em' })),
      el('div', { class: 'row' }, el('label', {}, el('input', { type: 'radio', name: 'wt-x-around', value: 'group', checked: true }), ' about the group centre'),
        el('label', {}, el('input', { type: 'radio', name: 'wt-x-around', value: 'each' }), ' each in place')),
      el('button', { id: 'wt-x-go', text: 'Apply to selection' }),
      el('p', { class: 'hint', text: 'Exact moves in game axes (x east, y up, z north). One undo step.' }));
    $('wt-x-go').onclick = async () => {
      const l = need(); if (!l) return;
      const r = await H.run('transform_selection', { lines: l, translate: ['x', 'y', 'z'].map((a) => num($('wt-x-' + a).value) * 100),
        yaw: num($('wt-x-yaw').value), around: panel.querySelector('input[name=wt-x-around]:checked').value }, 'transformed').catch(() => null);
      if (r) await after(r);
    };

    // ================================================================ Search & Replace base object ================
    pages.rep.append(
      el('label', {}, 'Find model ', el('input', { id: 'wt-r-find', placeholder: 'e.g. CRATE3', style: 'width:10em' })),
      el('label', {}, 'Replace with ', el('input', { id: 'wt-r-with', list: 'wt-lib', placeholder: 'library entry', style: 'width:14em' })),
      el('div', { class: 'row' }, el('label', {}, el('input', { type: 'radio', name: 'wt-r-scope', value: 'sel', checked: true }), ' selection'),
        el('label', {}, el('input', { type: 'radio', name: 'wt-r-scope', value: 'level' }), ' whole level')),
      el('button', { id: 'wt-r-go', text: 'Replace' }),
      el('p', { class: 'hint', text: 'Each match becomes the new type where it stood (position, facing, size, zone, name; objects keep their script id). Vehicles are left alone (crews).' }));
    function fillReplaceFind() {
      const d = H.data, l = lines()[0]; if (!d || !l || $('wt-r-find').value) return;
      const rec = d.props.find((x) => x.line === l) || d.objects.find((x) => x.line === l);
      if (rec) $('wt-r-find').value = rec.model;
    }
    $('wt-r-go').onclick = async () => {
      await loadLibrary();
      const t = libId($('wt-r-with').value); if (t === null) return H.status('pick the new type from the list', true);
      const scope = panel.querySelector('input[name=wt-r-scope]:checked').value;
      const args = { template: t, find: $('wt-r-find').value.trim() || undefined };
      if (scope === 'sel') { const l = need(); if (!l) return; args.lines = l; }
      else if (!args.find) return H.status('type the model to find', true);
      const r = await H.run('replace_base', args, 'replaced').catch(() => null);
      if (r) { await after(r); H.status(`${r.replaced} replaced`); }
    };

    // ================================================================ Pathing test ================
    const pathGroup = new THREE.Group(); H.scene3.add(pathGroup);
    let pathEnds = { start: null, goal: null };
    pages.path.append(
      el('div', { class: 'row' }, el('button', { id: 'wt-p-start', text: 'Pick start' }), el('button', { id: 'wt-p-goal', text: 'Pick goal' }),
        el('button', { id: 'wt-p-sel', title: 'Start at the selected record', text: 'Start = selection' })),
      el('div', { class: 'row' }, el('button', { id: 'wt-p-run', text: 'Find route' }), el('button', { id: 'wt-p-clear', text: 'Clear' })),
      el('p', { id: 'wt-p-out', class: 'hint', text: 'The AI route between two points over the route markers, searched like the game\'s router (links followed in their direction). Green: found; orange: the closest it gets; dashed: the walk to / from the nearest marker.' }));
    const endMat = { start: new THREE.MeshBasicMaterial({ color: 0x4fd16b, depthTest: false }), goal: new THREE.MeshBasicMaterial({ color: 0xff5a4a, depthTest: false }) };
    function clearPath() { pathGroup.clear(); H.requestRender(); }
    function drawEnds() {
      clearPath();
      for (const k of ['start', 'goal']) if (pathEnds[k]) {
        const m = new THREE.Mesh(new THREE.ConeGeometry(0.4, 1.2, 12).rotateX(Math.PI).translate(0, 0.6, 0), endMat[k]);
        m.renderOrder = 11; m.position.copy(H.toThree(pathEnds[k])); pathGroup.add(m);
      }
      H.requestRender();
    }
    const pick = (k) => H.pickGround(`click the ${k} on the ground (Esc cancels)`, (p) => { pathEnds[k] = p; drawEnds(); if (pathEnds.start && pathEnds.goal) runPath(); });
    $('wt-p-start').onclick = () => pick('start');
    $('wt-p-goal').onclick = () => pick('goal');
    $('wt-p-sel').onclick = () => {
      const l = lines()[0], d = H.data; if (!l) return H.status('select something', true);
      const rec = ['objects', 'props', 'waypoints', 'regions'].map((k) => d[k]?.find((x) => x.line === l)).find(Boolean);
      if (rec) { pathEnds.start = rec.pos; drawEnds(); }
    };
    $('wt-p-clear').onclick = () => { pathEnds = { start: null, goal: null }; clearPath(); $('wt-p-out').textContent = ''; };
    async function runPath() {
      if (!pathEnds.start || !pathEnds.goal) return H.status('pick a start and a goal', true);
      const r = await H.run('path_test', { start: pathEnds.start, goal: pathEnds.goal }).catch(() => null);
      if (!r) return;
      drawEnds();
      const lift = (p) => H.toThree(p).add(new THREE.Vector3(0, 0.4, 0));
      const pts = r.points.map(lift);
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),
        new THREE.LineBasicMaterial({ color: r.found ? 0x4fd16b : 0xffa040, depthTest: false }));
      line.renderOrder = 11; pathGroup.add(line);
      const dash = (a, b) => { const g = new THREE.Line(new THREE.BufferGeometry().setFromPoints([a, b]), new THREE.LineDashedMaterial({ color: 0xffffff, dashSize: 0.4, gapSize: 0.3, depthTest: false })); g.computeLineDistances(); g.renderOrder = 11; pathGroup.add(g); };
      dash(lift(pathEnds.start), pts[0]);
      if (r.found) dash(pts[pts.length - 1], lift(pathEnds.goal));
      H.requestRender();
      const m = (cm) => `${(cm / 100).toFixed(1)} m`;
      $('wt-p-out').textContent = r.found
        ? `Route found: ${r.markers.length} markers, ${m(r.length_cm)} (searched ${r.searched}). Start ${m(r.start_gap_cm)} from marker ${r.start_marker}, goal ${m(r.goal_gap_cm)} from marker ${r.goal_marker}.`
        : `No route: the closest it gets is marker ${r.markers[r.markers.length - 1]}, ${m(r.unreached_cm)} short of marker ${r.goal_marker}. Link the gap with route markers (AI routes layer).`;
    }
    $('wt-p-run').onclick = runPath;

    // ================================================================ View: go to, hide / freeze, picking ================
    const KINDS = [['props', 'Props'], ['objects', 'Objects'], ['waypoints', 'Waypoints'], ['regions', 'Regions'], ['cameras', 'Cameras'],
                   ['sounds', 'Sounds'], ['lights', 'Lights'], ['routes', 'Route markers'], ['switches', 'Switches'], ['emitters', 'Emitters'], ['chunks', 'World chunks']];
    let pickPrefs = store.get('pick', { mode: 'exclude', kinds: [] });
    pages.view.append(
      el('fieldset', {}, el('legend', { text: 'Go to location (m, game axes)' }),
        el('div', { class: 'row' }, ...['x', 'y', 'z'].map((a) => el('input', { id: 'wt-g-' + a, type: 'number', step: 1, placeholder: a, style: 'width:4.5em' })),
          el('button', { id: 'wt-g-go', text: 'Go' }), el('button', { id: 'wt-g-here', title: 'Fill in the view centre', text: 'Here' }))),
      el('fieldset', {}, el('legend', { text: 'Hide / freeze (this PC only, not saved in the mission)' }),
        el('div', { class: 'row' }, el('button', { id: 'wt-h-hide', text: 'Hide selected' }), el('button', { id: 'wt-h-freeze', text: 'Freeze selected' })),
        el('div', { class: 'row' }, el('button', { id: 'wt-h-unhide', text: 'Show all' }), el('button', { id: 'wt-h-unfreeze', text: 'Unfreeze all' })),
        el('p', { id: 'wt-h-count', class: 'hint' })),
      el('fieldset', {}, el('legend', { text: 'Picking preferences' }),
        el('div', { class: 'row' }, el('label', {}, el('input', { type: 'radio', name: 'wt-pk-mode', value: 'exclude' }), ' Exclude ticked'),
          el('label', {}, el('input', { type: 'radio', name: 'wt-pk-mode', value: 'only' }), ' Pick only ticked')),
        el('div', { class: 'wt-kinds' }, ...KINDS.map(([k, t]) => el('label', {}, el('input', { type: 'checkbox', 'data-kind': k }), ' ' + t))),
        el('p', { class: 'hint', text: 'Clicks in the view skip what is excluded - e.g. pick only Objects while working in a crowded camp.' })));
    $('wt-g-go').onclick = () => {
      const p = ['x', 'y', 'z'].map((a) => num($('wt-g-' + a).value) * 100);
      if ($('wt-g-y').value === '') { const g = groundAt(p[0] / 100, -p[2] / 100, 2000); if (g) p[1] = g.pos[1]; }
      H.focusOn(H.toThree(p), 12); H.requestRender(); H.status(`at ${p.map((c) => (c / 100).toFixed(1)).join(', ')} m`);
    };
    $('wt-g-here').onclick = () => { const p = H.toGame(H.orbit.target); ['x', 'y', 'z'].forEach((a, i) => { $('wt-g-' + a).value = (p[i] / 100).toFixed(1); }); };

    // hidden / frozen are kept by stable keys (lines shift): objects by script id, the rest by name
    const keyOf = (line) => {
      const d = H.data; if (!d) return null;
      const o = d.objects.find((x) => x.line === line); if (o) return `#${o.script_id}`;
      for (const k of ['props', 'waypoints', 'regions', 'cameras', 'sounds', 'lights', 'chunks']) {
        const r = d[k]?.find((x) => x.line === line); if (r) return `${k}:${r.name ?? r.id}`;
      }
      return null;
    };
    const levelKey = () => `${H.data?.level || ''}|${(H.data?.mission || []).join('/')}`;
    const vis = () => store.get('vis.' + levelKey(), { hidden: [], frozen: [] });
    const setVis = (v) => { store.set('vis.' + levelKey(), v); applyVis(); };
    let hiddenLines = new Set(), frozenLines = new Set();
    function applyVis() {
      const d = H.data; if (!d) return;
      const v = vis(), hid = new Set(v.hidden), fro = new Set(v.frozen);
      hiddenLines = new Set(); frozenLines = new Set();
      for (const [line, obj] of H.byLine) {
        const k = keyOf(line); if (!k) continue;
        const h = hid.has(k);
        if (h) hiddenLines.add(line); if (fro.has(k)) frozenLines.add(line);
        if (obj.userData.wtHidden !== h) { obj.visible = !h; obj.userData.wtHidden = h; }
      }
      $('wt-h-count').textContent = `${v.hidden.length} hidden, ${v.frozen.length} frozen`;
      H.requestRender();
    }
    const addVis = (what) => {
      const l = need(); if (!l) return;
      const v = vis(); for (const line of l) { const k = keyOf(line); if (k && !v[what].includes(k)) v[what].push(k); }
      setVis(v); if (what === 'hidden') H.select(null);
      H.status(`${l.length} ${what} - World Tools › View brings them back`);
    };
    $('wt-h-hide').onclick = () => addVis('hidden');
    $('wt-h-freeze').onclick = () => addVis('frozen');
    $('wt-h-unhide').onclick = () => { const v = vis(); for (const line of hiddenLines) { const o = H.byLine.get(line); if (o) { o.visible = true; o.userData.wtHidden = false; } } v.hidden = []; setVis(v); };
    $('wt-h-unfreeze').onclick = () => { const v = vis(); v.frozen = []; setVis(v); };
    H.hooks.afterBuild.push(() => applyVis());

    function syncPickUi() {
      panel.querySelector(`input[name=wt-pk-mode][value=${pickPrefs.mode}]`).checked = true;
      for (const c of panel.querySelectorAll('.wt-kinds input')) c.checked = pickPrefs.kinds.includes(c.dataset.kind);
    }
    panel.querySelector('.wt-kinds').onchange = () => { pickPrefs.kinds = [...panel.querySelectorAll('.wt-kinds input:checked')].map((c) => c.dataset.kind); store.set('pick', pickPrefs); };
    for (const r of panel.querySelectorAll('input[name=wt-pk-mode]')) r.onchange = () => { pickPrefs.mode = r.value; store.set('pick', pickPrefs); };
    H.hooks.pickBlocked = (u) => {
      if (u.line !== undefined && (hiddenLines.has(u.line) || frozenLines.has(u.line))) return true;
      if (!pickPrefs.kinds.length) return false;
      const kind = u.chunk ? 'chunks' : u.cat;
      const ticked = pickPrefs.kinds.includes(kind);
      return pickPrefs.mode === 'only' ? !ticked : ticked;
    };

    // ================================================================ CSV placements ================
    pages.csv.append(
      el('div', { class: 'row' }, el('button', { id: 'wt-c-sel', text: 'Export selection' }), el('button', { id: 'wt-c-all', text: 'Export all props + objects' })),
      el('div', { class: 'row' }, el('button', { id: 'wt-c-folder', text: 'Open exports folder' })),
      el('div', { class: 'row' }, el('button', { id: 'wt-c-import', text: 'Import CSV…' }), el('input', { id: 'wt-c-file', type: 'file', accept: '.csv,text/csv', hidden: true })),
      el('p', { class: 'hint', text: 'Positions (cm), rotation (°) and size per record - key = #script id for objects, the name for props. Edit in a spreadsheet and import: matching records move, nothing else changes (one undo step).' }));
    async function exportCsv(sel) {
      const args = sel ? { lines: need() } : {}; if (sel && !args.lines) return;
      const r = await H.run('export_placements', args);
      await saveExport(`${H.data?.level || 'level'}-placements.csv`, { text: r.csv }, `${r.rows} records`);
    }
    $('wt-c-sel').onclick = () => exportCsv(true);
    $('wt-c-all').onclick = () => exportCsv(false);
    $('wt-c-import').onclick = () => $('wt-c-file').click();
    $('wt-c-folder').onclick = () => H.run('open_export_folder').catch(() => {});
    $('wt-c-file').onchange = async (ev) => {
      const f = ev.target.files[0]; ev.target.value = ''; if (!f) return;
      const r = await H.run('import_placements', { csv_text: await f.text() }, 'placements imported').catch(() => null);
      if (r) { await H.refresh(); H.status(`${r.updated} records updated${r.missing.length ? ` - not in this scene: ${r.missing.slice(0, 5).join(', ')}` : ''}`, !!r.missing.length); }
    };

    // exports are written by the server (the editor is ConflictEditor.exe - a WebView2 window, no browser downloads)
    async function saveExport(name, content, what = '') {
      const r = await H.run('save_export', { name, ...content }).catch(() => null);
      if (r) H.status(`saved ${what ? what + ' to ' : ''}${r.path}`);
      return r;
    }
    // ---------- Paste in Place (Ctrl+Shift+V) ----------
    H.hooks.pasteInPlace = async () => { const r = await H.run('paste_in_place', {}, 'pasted in place'); await after(r); };

    // ================================================================ round 2 ================
    H.slowCommands?.add('check_all_levels');
    const regionsOf = () => H.data?.regions || [];
    const gameToThreeXZ = (gx, gz) => [gx / 100, -gz / 100];
    const turnXZ = (x, z, yawDeg) => { const a = yawDeg * Math.PI / 180; return [x * Math.cos(a) + z * Math.sin(a), -x * Math.sin(a) + z * Math.cos(a)]; };
    const fillSelect = (sel, items, keep = true) => {
      const old = sel.value; sel.innerHTML = '';
      for (const [v, t] of items) sel.add(new Option(t, v));
      if (keep && [...sel.options].some((o) => o.value === old)) sel.value = old;
    };
    const table = (rows, cols) => el('table', { class: 'wt-table' },
      el('tr', {}, ...cols.map(([, t]) => el('th', { text: t }))),
      ...rows.map((r) => el('tr', {}, ...cols.map(([k]) => el('td', { text: Array.isArray(r[k]) ? r[k].join('; ') : String(r[k] ?? '') })))));

    // ---------- Palette: fill a region ----------
    P.append(el('fieldset', {}, el('legend', { text: 'Fill a region with the palette' }),
      el('div', { class: 'row' }, el('select', { id: 'wt-fill-region', style: 'max-width:11em' }),
        el('label', { title: 'objects per 100 m²' }, 'per 100 m² ', el('input', { id: 'wt-fill-density', type: 'number', min: 0.1, step: 0.5, value: store.get('fillDensity', 3), style: 'width:3.5em' })),
        el('button', { id: 'wt-fill-go', text: 'Fill' }))));
    const refreshRegions = () => fillSelect($('wt-fill-region'), regionsOf().map((r) => [r.line, `${r.name} (${r.type === 1 ? 'sphere' : 'box'})`]));
    $('wt-fill-go').onclick = async () => {
      if (!palette.entries.length) return H.status('add objects to the palette first', true);
      const reg = regionsOf().find((r) => String(r.line) === $('wt-fill-region').value);
      if (!reg) return H.status('pick a region (Paths & Regions makes them)', true);
      const dens = num($('wt-fill-density').value, 3); store.set('fillDensity', dens);
      let area, sample, top;
      if (reg.type === 1) {
        const R = reg.radius; area = Math.PI * R * R / 1e4; top = (reg.pos[1] + R) / 100;
        sample = () => { const a = Math.random() * 2 * Math.PI, d = Math.sqrt(Math.random()) * R; return [reg.pos[0] + Math.cos(a) * d, reg.pos[2] + Math.sin(a) * d]; };
      } else {
        const [mn, mx] = [reg.min, reg.max]; area = Math.abs((mx[0] - mn[0]) * (mx[2] - mn[2])) / 1e4; top = (reg.pos[1] + Math.max(mx[1], 0)) / 100;
        sample = () => { const lx = mn[0] + Math.random() * (mx[0] - mn[0]), lz = mn[2] + Math.random() * (mx[2] - mn[2]);
          const [ox, oz] = turnXZ(lx, lz, reg.rot?.[1] || 0); return [reg.pos[0] + ox, reg.pos[2] + oz]; };
      }
      const want = Math.min(500, Math.max(1, Math.round(area / 100 * dens)));
      const pts = [];
      for (let i = 0; i < want * 2 && pts.length < want; i++) {
        const [gx, gz] = sample(), [tx, tz] = gameToThreeXZ(gx, gz);
        const g = groundAt(tx, tz, top + 2); if (g) pts.push({ pos: g.pos, normal: g.normal });
      }
      if (!pts.length) return H.status('no ground found inside the region', true);
      const r = await H.run('palette_paint', { entries: palette.entries, points: pts, spacing: num($('wt-spacing').value, 0) * 100 }, 'filled').catch(() => null);
      if (r) { await after(r); H.status(`filled ${reg.name}: ${r.placed} object${r.placed === 1 ? '' : 's'} (${area.toFixed(0)} m²) - Ctrl+Z undoes it`); }
    };

    // ---------- Replace: use report ----------
    pages.rep.append(el('div', { class: 'row' }, el('button', { id: 'wt-r-uses', text: 'Find uses', title: 'Every record using the model above - selects the props and objects' }),
      el('span', { id: 'wt-r-usecount', class: 'hint' })));
    $('wt-r-uses').onclick = async () => {
      const model = $('wt-r-find').value.trim(); if (!model) return H.status('type a model (or select one)', true);
      const r = await H.run('model_use', { model }).catch(() => null); if (!r) return;
      const lines = r.uses.filter((u) => u.kind === 'prop' || u.kind === 'object').map((u) => u.line);
      $('wt-r-usecount').textContent = `${r.count} use${r.count === 1 ? '' : 's'}: ` + Object.entries(r.by_kind).map(([k, n]) => `${n} ${k}`).join(', ');
      if (lines.length > 1) H.selectMany(lines); else if (lines.length) await H.select({ line: lines[0] }, true);
    };

    // ---------- Transform: snap next to another object ----------
    pages.xf.append(el('fieldset', {}, el('legend', { text: 'Snap to an object' }),
      el('div', { class: 'row' }, el('button', { id: 'wt-snap', text: 'Snap selection next to…' }),
        el('label', {}, el('input', { type: 'checkbox', id: 'wt-snap-align', checked: true }), ' line up')),
      el('p', { class: 'hint', text: 'Click near the object to snap against: the selection moves the shortest way until the two touch side to side (sandbag runs, wall pieces, crates in a row); "line up" also matches their edge on the other axis.' })));
    const boxOf = (lines) => { const b = new THREE.Box3(); for (const l of lines) { const o = H.byLine.get(l); if (o) b.expandByObject(o); } return b; };
    $('wt-snap').onclick = () => {
      const sel = need(); if (!sel) return;
      H.pickGround('click next to the object to snap against (Esc cancels)', async (p) => {
        const at = H.toThree(p);
        let best = null;
        for (const [line, o] of H.byLine) {
          if (sel.includes(line) || !o.visible || !['props', 'objects'].includes(o.userData.cat)) continue;
          const d = new THREE.Box3().setFromObject(o).distanceToPoint(at);
          if (!best || d < best.d) best = { d, line };
        }
        if (!best || best.d > 10) return H.status('no prop or object within 10 m of that spot', true);
        const a = boxOf(sel), b = boxOf([best.line]);
        const moves = [['x', b.max.x - a.min.x], ['x', b.min.x - a.max.x], ['z', b.max.z - a.min.z], ['z', b.min.z - a.max.z]];
        moves.sort((m, n) => Math.abs(m[1]) - Math.abs(n[1]));
        const [axis, delta] = moves[0];
        const d = new THREE.Vector3(); d[axis] = delta;
        if ($('wt-snap-align').checked) {
          const other = axis === 'x' ? 'z' : 'x';
          const opts = [b.min[other] - a.min[other], b.max[other] - a.max[other]];
          d[other] = Math.abs(opts[0]) < Math.abs(opts[1]) ? opts[0] : opts[1];
        }
        const r = await H.run('transform_selection', { lines: sel, translate: [d.x * 100, 0, -d.z * 100] }, 'snapped').catch(() => null);
        if (r) await after(r);
      });
    };

    // ================================================================ Checks: line of sight, route islands, all levels ================
    const checkGroup = new THREE.Group(); H.scene3.add(checkGroup);
    const C = pages.check;
    C.append(
      el('fieldset', {}, el('legend', { text: 'Line of sight' }),
        el('div', { class: 'row' }, el('button', { id: 'wt-los-a', text: 'Pick eye' }), el('button', { id: 'wt-los-b', text: 'Pick target' }),
          el('select', { id: 'wt-los-h' }, el('option', { value: 1.7, text: 'standing' }), el('option', { value: 1.0, text: 'crouched' }), el('option', { value: 0.35, text: 'prone' }))),
        el('div', { class: 'row' }, el('button', { id: 'wt-los-fan', text: 'Viewshed from the eye' }),
          el('label', {}, 'range (m) ', el('input', { id: 'wt-los-range', type: 'number', value: 60, min: 10, max: 300, style: 'width:4em' }))),
        el('p', { id: 'wt-los-out', class: 'hint', text: 'Is the target visible from the eye (level geometry and props)? The viewshed shades the ground a soldier at the eye can see (green) or not (red) - for snipers, guard posts and cameras.' })),
      el('fieldset', {}, el('legend', { text: 'AI route islands' }),
        el('div', { class: 'row' }, el('button', { id: 'wt-isl-go', text: 'Check from the squad' }), el('button', { id: 'wt-isl-sel', text: 'from the selection' })),
        el('p', { id: 'wt-isl-out', class: 'hint', text: 'Red: route markers the AI can never reach from the start; orange: traps - reachable, but no way back.' })),
      el('fieldset', {}, el('legend', { text: 'Check every level' }),
        el('div', { class: 'row' }, el('button', { id: 'wt-all-go', text: 'Check all levels + missions' }), el('button', { id: 'wt-check-clear', text: 'Clear marks' })),
        el('div', { id: 'wt-all-out', class: 'wt-scroll' })));
    const los = { a: null, b: null };
    const eyeOf = (p) => H.toThree(p).add(new THREE.Vector3(0, num($('wt-los-h').value, 1.7), 0));
    const blockedBetween = (from, to) => {
      const dir = to.clone().sub(from), dist = dir.length(); dir.normalize();
      ray.set(from, dir); ray.far = dist - 0.05;
      const h = ray.intersectObjects(surfaces(), true).find((q) => q.object.isMesh && q.object.visible);
      ray.far = Infinity; return h || null;
    };
    const lineMesh = (a, b, colour) => { const l = new THREE.Line(new THREE.BufferGeometry().setFromPoints([a, b]), new THREE.LineBasicMaterial({ color: colour, depthTest: false })); l.renderOrder = 11; checkGroup.add(l); };
    function runLos() {
      if (!los.a || !los.b) return;
      checkGroup.clear();
      const a = eyeOf(los.a), b = eyeOf(los.b), h = blockedBetween(a, b), dist = a.distanceTo(b);
      if (h) { lineMesh(a, h.point, 0x4fd16b); lineMesh(h.point, b, 0xff5a4a); }
      else lineMesh(a, b, 0x4fd16b);
      $('wt-los-out').textContent = h ? `Blocked ${h.distance.toFixed(1)} m from the eye (of ${dist.toFixed(1)} m) - red part hidden.`
        : `Clear line of sight, ${dist.toFixed(1)} m.`;
      H.requestRender();
    }
    $('wt-los-a').onclick = () => H.pickGround('click where the eye stands', (p) => { los.a = p; runLos(); });
    $('wt-los-b').onclick = () => H.pickGround('click the target', (p) => { los.b = p; runLos(); });
    $('wt-los-h').onchange = runLos;
    $('wt-los-fan').onclick = async () => {
      if (!los.a) return H.status('pick the eye first', true);
      checkGroup.clear();
      const eye = eyeOf(los.a), range = num($('wt-los-range').value, 60), dirs = 48, step = Math.max(2, range / 25);
      const geo = new THREE.SphereGeometry(Math.min(0.3, step / 6), 6, 4);
      const mats = [new THREE.MeshBasicMaterial({ color: 0x4fd16b, depthTest: false, transparent: true, opacity: 0.7 }),
                    new THREE.MeshBasicMaterial({ color: 0xff5a4a, depthTest: false, transparent: true, opacity: 0.5 })];
      let seen = 0, total = 0;
      for (let k = 0; k < dirs; k++) {
        const ang = k / dirs * Math.PI * 2;
        for (let d = step; d <= range; d += step) {
          const g = groundAt(eye.x + Math.cos(ang) * d, eye.z + Math.sin(ang) * d, eye.y + 20);
          if (!g) continue;
          const target = g.p.clone().add(new THREE.Vector3(0, 1.0, 0));
          const vis = !blockedBetween(eye, target);
          const m = new THREE.Mesh(geo, mats[vis ? 0 : 1]); m.position.copy(g.p); m.renderOrder = 11; checkGroup.add(m);
          total++; if (vis) seen++;
        }
        if (k % 6 === 5) { H.requestRender(); await new Promise((r) => setTimeout(r, 0)); }
      }
      $('wt-los-out').textContent = `Viewshed: ${Math.round(seen / Math.max(1, total) * 100)} % of the ground within ${range} m is in sight (a man-height target).`;
      H.requestRender();
    };
    async function runIslands(start) {
      const r = await H.run('route_islands', start ? { start } : {}).catch(() => null); if (!r) return;
      checkGroup.clear();
      const geo = new THREE.SphereGeometry(0.5, 8, 6);
      for (const [list, colour] of [[r.unreachable, 0xff4040], [r.traps, 0xffa040]]) {
        const mat = new THREE.MeshBasicMaterial({ color: colour, depthTest: false });
        for (const m of list) { const s = new THREE.Mesh(geo, mat); s.position.copy(H.toThree(m.pos)).add(new THREE.Vector3(0, 0.6, 0)); s.renderOrder = 11; checkGroup.add(s); }
      }
      $('wt-isl-out').textContent = `${r.reachable} of ${r.markers} markers reachable from marker ${r.start_marker}; ${r.unreachable_count} unreachable (red), ${r.trap_count} traps (orange).`;
      if (r.unreachable[0]) H.focusOn(H.toThree(r.unreachable[0].pos), 20);
      H.requestRender();
    }
    $('wt-isl-go').onclick = () => runIslands(null);
    $('wt-isl-sel').onclick = () => {
      const l = lines()[0], d = H.data; if (!l) return H.status('select something', true);
      const rec = ['objects', 'props', 'waypoints', 'regions'].map((k) => d[k]?.find((x) => x.line === l)).find(Boolean);
      if (rec) runIslands(rec.pos);
    };
    $('wt-check-clear').onclick = () => { checkGroup.clear(); H.requestRender(); };
    $('wt-all-go').onclick = async () => {
      $('wt-all-out').textContent = 'Checking - this takes a while (about a second per level)…';
      const r = await H.run('check_all_levels', { missions: true, limit: 3 }).catch(() => null);
      if (!r) { $('wt-all-out').textContent = ''; return; }
      $('wt-all-out').innerHTML = '';
      $('wt-all-out').append(el('p', { class: r.with_errors ? 'bad' : 'hint', text: `${r.checked} checked, ${r.with_errors} with errors` }),
        table(r.rows, [['name', 'Level / mission'], ['errors', 'Err'], ['warnings', 'Warn'], ['issues', 'First problems']]));
    };

    // ================================================================ Notes ================
    const N = pages.notes;
    N.append(
      el('div', { class: 'row' }, el('input', { id: 'wt-n-text', placeholder: 'Note text', style: 'flex:1' }),
        el('input', { id: 'wt-n-colour', type: 'color', value: '#ffd23f', title: 'colour' })),
      el('div', { class: 'row' }, el('button', { id: 'wt-n-add', text: 'Place note…' }), el('button', { id: 'wt-n-here', text: 'At the view centre' })),
      el('div', { id: 'wt-n-list', class: 'wt-scroll' }),
      el('p', { class: 'hint', text: 'Sticky notes in the world, saved with the mission (never in the game) - for to-dos and for a team.' }));
    const noteGroup = new THREE.Group(); H.scene3.add(noteGroup);
    let notes = [];
    function noteSprite(n) {
      const cv = document.createElement('canvas'), ctx = cv.getContext('2d'), text = n.text.length > 40 ? n.text.slice(0, 39) + '…' : n.text;
      cv.width = 512; cv.height = 96; ctx.font = 'bold 34px Segoe UI, sans-serif';
      const w = Math.min(500, ctx.measureText(text).width + 36);
      ctx.fillStyle = n.colour; ctx.globalAlpha = 0.92; ctx.beginPath(); ctx.roundRect(4, 8, w, 72, 12); ctx.fill();
      ctx.globalAlpha = 1; ctx.fillStyle = '#1b1d1a'; ctx.fillText(text, 22, 56);
      const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(cv), depthTest: false, transparent: true }));
      sp.center.set(0, 0); sp.scale.set(4, 0.75, 1); sp.renderOrder = 12;
      sp.position.copy(H.toThree(n.pos)).add(new THREE.Vector3(0, 2, 0));
      const pin = new THREE.Line(new THREE.BufferGeometry().setFromPoints([H.toThree(n.pos), sp.position]), new THREE.LineBasicMaterial({ color: n.colour, depthTest: false }));
      pin.renderOrder = 12; noteGroup.add(pin); return sp;
    }
    async function loadNotes() {
      if (!H.data) return;
      notes = (await H.api('notes_list').catch(() => ({ notes: [] }))).notes;
      for (const c of noteGroup.children) c.material?.map?.dispose?.();
      noteGroup.clear(); for (const n of notes) noteGroup.add(noteSprite(n));
      const box = $('wt-n-list'); box.innerHTML = '';
      for (const n of notes) box.append(el('div', { class: 'row wt-note' },
        el('span', { class: 'wt-swatch', style: `background:${n.colour}` }),
        el('a', { href: '#', text: n.text, style: 'flex:1', onclick: (e) => { e.preventDefault(); H.focusOn(H.toThree(n.pos), 10); } }),
        el('button', { text: '✎', title: 'edit text', onclick: async () => { const t = prompt('Note', n.text); if (t !== null && t.trim()) { await H.run('note_set', { id: n.id, text: t }, 'note changed'); loadNotes(); } } }),
        el('button', { text: '✕', title: 'delete', onclick: async () => { await H.run('note_delete', { id: n.id }, 'note deleted'); loadNotes(); } })));
      if (!notes.length) box.append(el('p', { class: 'hint', text: 'No notes yet.' }));
      H.requestRender();
    }
    const addNote = async (pos) => {
      const text = $('wt-n-text').value.trim(); if (!text) return H.status('type the note first', true);
      await H.run('note_add', { pos, text, colour: $('wt-n-colour').value }, 'note placed'); $('wt-n-text').value = ''; loadNotes();
    };
    $('wt-n-add').onclick = () => { if (!$('wt-n-text').value.trim()) return H.status('type the note first', true); H.pickGround('click where the note goes', addNote); };
    $('wt-n-here').onclick = () => addNote(H.toGame(H.orbit.target));
    H.hooks.afterBuild.push(() => loadNotes());

    // ================================================================ Linked prefabs ================
    const L = pages.link;
    L.append(el('p', { id: 'wt-l-sel', class: 'hint' }),
      el('div', { class: 'row' }, el('button', { id: 'wt-l-apply', text: 'Make this the prefab', title: 'The selected instance as it is now (plus other selected records) becomes the prefab; every other copy is updated' }),
        el('button', { id: 'wt-l-unlink', text: 'Unlink' })),
      el('div', { class: 'row' }, el('button', { id: 'wt-l-restamp', text: 'Re-stamp all from the saved prefab' }), el('button', { id: 'wt-l-refresh', text: 'Refresh' })),
      el('div', { id: 'wt-l-list', class: 'wt-scroll' }),
      el('p', { class: 'hint', text: 'Every prefab you stamp is linked: edit one copy (move, add, delete records), then "Make this the prefab" and all other copies follow, each keeping its spot and turn.' }));
    let linkInfo = null;
    async function refreshLinks() {
      const l = lines();
      linkInfo = l.length ? await H.api('prefab_instance_of', { line: l[0] }).catch(() => null) : null;
      $('wt-l-sel').textContent = linkInfo?.id ? `Selection: copy #${linkInfo.id} of ${linkInfo.name}` : 'Select a record of a stamped prefab.';
      const r = await H.api('prefab_instances').catch(() => ({ instances: [] }));
      const box = $('wt-l-list'); box.innerHTML = '';
      for (const i of r.instances) box.append(el('div', { class: 'row' },
        el('a', { href: '#', text: `#${i.id} ${i.name} (${i.records} records)`, onclick: (e) => { e.preventDefault(); if (i.lines.length > 1) H.selectMany(i.lines); else if (i.lines.length) H.select({ line: i.lines[0] }, true); } })));
      if (!r.instances.length) box.append(el('p', { class: 'hint', text: 'No linked copies yet - stamp a prefab from the Prefabs tab.' }));
    }
    $('wt-l-refresh').onclick = refreshLinks;
    $('wt-l-apply').onclick = async () => {
      if (!linkInfo?.id) return H.status('select a record of a stamped prefab', true);
      const r = await H.run('prefab_apply_instance', { instance: linkInfo.id, extra_lines: lines() }, 'prefab updated').catch(() => null);
      if (r) { await H.refresh(); H.status(`${r.saved} saved - ${r.updated} other cop${r.updated === 1 ? 'y' : 'ies'} updated`); refreshLinks(); }
    };
    $('wt-l-unlink').onclick = async () => { if (!linkInfo?.id) return; await H.run('prefab_unlink', { instance: linkInfo.id }, 'unlinked'); refreshLinks(); };
    $('wt-l-restamp').onclick = async () => {
      if (!linkInfo?.name) return H.status('select a record of a stamped prefab', true);
      const r = await H.run('prefab_update', { name: linkInfo.name }, 'prefab copies re-stamped').catch(() => null);
      if (r) { await H.refresh(); refreshLinks(); }
    };

    // ================================================================ Spawn lists ================
    const S = pages.spawn;
    const objList = el('datalist', { id: 'wt-lib-obj' }); document.body.append(objList);
    S.append(
      el('div', { class: 'row' }, el('input', { id: 'wt-s-name', placeholder: 'List name (e.g. GatePost)', style: 'flex:1' })),
      el('div', { id: 'wt-s-variants' }),
      el('div', { class: 'row' }, el('input', { id: 'wt-s-add', list: 'wt-lib-obj', placeholder: 'Add a soldier type…', style: 'flex:1' })),
      el('label', { title: 'weight of nobody being there' }, 'Nobody weight ', el('input', { id: 'wt-s-empty', type: 'number', min: 0, step: 0.5, value: 0, style: 'width:4em' })),
      el('div', { class: 'row' }, el('button', { id: 'wt-s-place', text: 'Place list…' }), el('label', {}, 'facing ', el('input', { id: 'wt-s-yaw', type: 'number', value: 0, style: 'width:4em' }))),
      el('div', { id: 'wt-s-list', class: 'wt-scroll' }),
      el('p', { class: 'hint', text: 'Each playthrough picks one of the variants by weight (or nobody): each is placed in its own hidden layer and a "mission start" rule shows the winner. Edit the list and place it again to replace it.' }));
    let spawnDraft = [];
    // per-difficulty weights: 5 numbers in the menu's order (Very Easy … Very Hard); the game's levels are 4, 0, 1, 2, 3
    const DIFF = [['VE', 4, 'very easy'], ['E', 0, 'easy'], ['M', 1, 'medium'], ['H', 2, 'hard'], ['VH', 3, 'very hard']];
    const fromLevels = (by, base) => (by ? DIFF.map(([, lv]) => +(by[lv] ?? by[String(lv)] ?? base)) : null);
    let spawnEmptyBy = null;
    function diffRow(arr, base, set) {
      const on = el('input', { type: 'checkbox', checked: arr ? true : null, title: 'different weights per difficulty' });
      const row = el('div', { class: 'row wt-diff', hidden: !arr || null },
        ...DIFF.map(([short, , long], k) => el('label', { title: long }, short + ' ',
          el('input', { type: 'number', min: 0, step: 0.5, value: arr ? arr[k] : base, style: 'width:3em',
            onchange: (e) => { const a = arr || DIFF.map(() => base); a[k] = +e.target.value; arr = a; set(a); } }))));
      on.onchange = () => { row.hidden = !on.checked; arr = on.checked ? (arr || DIFF.map(() => base)) : null; set(arr); };
      return [el('label', { title: 'different weights per difficulty' }, on, ' by difficulty'), row];
    }
    function drawSpawnDraft() {
      const box = $('wt-s-variants'); box.innerHTML = '';
      spawnDraft.forEach((v, i) => {
        const [toggle, row] = diffRow(v.byDiff, v.weight, (a) => { v.byDiff = a; });
        box.append(el('div', { class: 'wt-entry' },
          el('div', { class: 'row' }, el('span', { text: v.label, style: 'flex:1' }),
            el('label', {}, '×', el('input', { type: 'number', min: 0.1, step: 0.5, value: v.weight, style: 'width:3.5em', onchange: (e) => { v.weight = +e.target.value; } })),
            el('button', { text: '✕', onclick: () => { spawnDraft.splice(i, 1); drawSpawnDraft(); } })),
          el('div', { class: 'row' }, toggle), row));
      });
      const [etoggle, erow] = diffRow(spawnEmptyBy, num($('wt-s-empty').value), (a) => { spawnEmptyBy = a; });
      box.append(el('div', { class: 'row hint' }, 'Nobody: ', etoggle), erow);
    }
    $('wt-s-add').onchange = async (e) => {
      await loadLibrary(); const id = libId(e.target.value), t = lib.find((x) => x.id === id); e.target.value = '';
      if (!t || t.kind !== 'object') return H.status('pick a soldier type from the list', true);
      spawnDraft.push({ template: t.id, label: t.label, weight: 1 }); drawSpawnDraft();
    };
    async function refreshSpawns() {
      const r = await H.api('spawn_lists').catch(() => ({ lists: [] }));
      const box = $('wt-s-list'); box.innerHTML = '';
      for (const sp of r.lists) box.append(el('div', { class: 'row' },
        el('a', { href: '#', text: `${sp.name}: ${sp.variants.map((v) => `${v.label} ×${v.weight}`).join(', ')}${sp.empty_weight ? `, nobody ×${sp.empty_weight}` : ''}`, style: 'flex:1',
          onclick: (e) => { e.preventDefault(); $('wt-s-name').value = sp.name; $('wt-s-empty').value = sp.empty_weight; $('wt-s-yaw').value = sp.yaw;
                            spawnDraft = sp.variants.map((v) => ({ template: v.template, label: v.label, weight: v.weight, byDiff: fromLevels(v.by_difficulty, v.weight) }));
                            spawnEmptyBy = fromLevels(sp.empty_by_difficulty, sp.empty_weight); drawSpawnDraft(); H.focusOn(H.toThree(sp.pos), 10); } }),
        el('button', { text: '✕', title: 'delete the list, its soldiers and rule', onclick: async () => { await H.run('spawn_list_delete', { name: sp.name }, 'spawn list deleted'); await H.refresh(); refreshSpawns(); } })));
      if (!r.lists.length) box.append(el('p', { class: 'hint', text: 'No spawn lists yet.' }));
    }
    $('wt-s-place').onclick = () => {
      const name = $('wt-s-name').value.trim();
      if (!name || !spawnDraft.length) return H.status('name the list and add at least one soldier type', true);
      H.pickGround(`click where ${name} stands`, async (p) => {
        const r = await H.run('spawn_list_set', { name, pos: p, yaw: num($('wt-s-yaw').value), empty_weight: num($('wt-s-empty').value),
          empty_by_difficulty: spawnEmptyBy,
          variants: spawnDraft.map((v) => ({ template: v.template, weight: v.weight, by_difficulty: v.byDiff })) }, `spawn list ${name} placed`).catch(() => null);
        if (r) { await after(r); refreshSpawns(); }
      });
    };

    // ================================================================ Camera paths ================
    const K = pages.cam;
    K.append(
      el('div', { class: 'row' }, el('input', { id: 'wt-c-name', placeholder: 'Path name', style: 'flex:1' })),
      el('div', { id: 'wt-c-shots' }),
      el('div', { class: 'row' }, el('button', { id: 'wt-c-add', text: '+ Shot' }), el('button', { id: 'wt-c-preview', text: '▶ Preview' })),
      el('div', { class: 'row' }, 'Starts ', el('select', { id: 'wt-c-trig' }, el('option', { value: 'mission_start', text: 'at mission start' }),
        el('option', { value: 'after_time', text: 'seconds in' }), el('option', { value: 'region_enter', text: 'when a player enters' })),
        el('input', { id: 'wt-c-sec', type: 'number', value: 10, style: 'width:3.5em' }), el('select', { id: 'wt-c-region', style: 'max-width:9em' })),
      el('div', { class: 'row' }, el('label', {}, el('input', { type: 'checkbox', id: 'wt-c-letter', checked: true }), ' letterbox'),
        el('label', {}, el('input', { type: 'checkbox', id: 'wt-c-ctrl' }), ' player keeps control')),
      el('button', { id: 'wt-c-save', text: 'Save camera path' }),
      el('div', { id: 'wt-c-list', class: 'wt-scroll' }),
      el('p', { class: 'hint', text: 'Scene cameras played in order as a cutscene (new cameras: Paths & Regions › Camera). Saved as an event rule.' }));
    let shots = [];
    const camNames = () => (H.data?.cameras || []).map((c) => c.name);
    function drawShots() {
      const box = $('wt-c-shots'); box.innerHTML = '';
      shots.forEach((sh, i) => {
        const sel = el('select', { style: 'flex:1', onchange: (e) => { sh.camera = e.target.value; } });
        fillSelect(sel, camNames().map((n) => [n, n]), false); sel.value = sh.camera;
        box.append(el('div', { class: 'row' }, el('span', { text: `${i + 1}.` }), sel,
          el('input', { type: 'number', min: 0.2, step: 0.5, value: sh.seconds, style: 'width:3.5em', title: 'seconds', onchange: (e) => { sh.seconds = +e.target.value; } }), 's',
          el('button', { text: '✕', onclick: () => { shots.splice(i, 1); drawShots(); } })));
      });
    }
    $('wt-c-add').onclick = () => { const n = camNames(); if (!n.length) return H.status('this scene has no cameras', true); shots.push({ camera: n[Math.min(n.length - 1, shots.length + 1)] ?? n[0], seconds: 4 }); drawShots(); };
    $('wt-c-preview').onclick = async () => {
      for (const sh of shots) {
        const c = H.data.cameras.find((x) => x.name === sh.camera); if (!c) continue;
        H.viewCamera(c); H.requestRender(); H.status(`previewing ${sh.camera} (${sh.seconds} s)`);
        await new Promise((r) => setTimeout(r, sh.seconds * 1000));
      }
      H.exitCameraView(); H.requestRender(); H.status('preview done');
    };
    $('wt-c-save').onclick = async () => {
      const name = $('wt-c-name').value.trim(); if (!name || !shots.length) return H.status('name the path and add shots', true);
      const tt = $('wt-c-trig').value;
      const trigger = tt === 'after_time' ? { type: tt, seconds: num($('wt-c-sec').value, 10) }
        : tt === 'region_enter' ? { type: tt, region: $('wt-c-region').value, who: 'player', object: -1 } : { type: tt };
      const r = await H.run('camera_path_set', { name, shots, trigger, letterbox: $('wt-c-letter').checked, control: $('wt-c-ctrl').checked },
                            `camera path ${name} saved`).catch(() => null);
      if (r) refreshCams();
    };
    async function refreshCams() {
      fillSelect($('wt-c-region'), regionsOf().map((r) => [r.name, r.name]));
      const r = await H.api('camera_paths').catch(() => ({ paths: [] }));
      const box = $('wt-c-list'); box.innerHTML = '';
      for (const p of r.paths) box.append(el('div', { class: 'row' },
        el('a', { href: '#', style: 'flex:1', text: `${p.name}: ${p.shots.length} shots, ${p.shots.reduce((a, s) => a + s.seconds, 0)} s`,
          onclick: (e) => { e.preventDefault(); $('wt-c-name').value = p.name; shots = p.shots.map((s) => ({ ...s })); $('wt-c-trig').value = p.trigger.type;
                            if (p.trigger.seconds) $('wt-c-sec').value = p.trigger.seconds; if (p.trigger.region) $('wt-c-region').value = p.trigger.region;
                            $('wt-c-letter').checked = p.letterbox; $('wt-c-ctrl').checked = p.control; drawShots(); } }),
        el('button', { text: '✕', onclick: async () => { await H.run('camera_path_delete', { name: p.name }, 'camera path deleted'); refreshCams(); } })));
      if (!r.paths.length) box.append(el('p', { class: 'hint', text: 'No camera paths yet.' }));
    }

    // ================================================================ Terrain: heightmaps + brush falloff ================
    const T = pages.terr;
    T.append(el('p', { id: 'wt-t-sel', class: 'hint' }),
      el('div', { class: 'row' }, el('button', { id: 'wt-t-export', text: 'Export heightmap PNG' })),
      el('div', { class: 'row' }, 'Import: low ', el('input', { id: 'wt-t-low', type: 'number', step: 0.5, style: 'width:4em', title: 'black = this height (m)' }),
        ' high ', el('input', { id: 'wt-t-high', type: 'number', step: 0.5, style: 'width:4em', title: 'white = this height (m)' }), ' m'),
      el('div', { class: 'row' }, el('button', { id: 'wt-t-import', text: 'Import heightmap…' }), el('input', { id: 'wt-t-file', type: 'file', accept: 'image/*', hidden: true })),
      el('p', { class: 'hint', text: 'Greyscale, north up; black = low, white = high. Any size - it is resampled to the patch\'s vertices. The exported file name carries its height range. Brush falloff: Terrain panel.' }));
    const selTerrain = () => { const l = lines()[0]; return H.data?.props.find((p) => p.line === l && p.terrain) || null; };
    function showTerrain() {
      const p = selTerrain();
      if (!p) { $('wt-t-sel').textContent = 'Select a terrain patch (World › Terrain…).'; return; }
      const h = p.terrain.heights, lo = Math.min(...h), hi = Math.max(...h);
      $('wt-t-sel').textContent = `${p.model}: ${p.terrain.n}×${p.terrain.n} vertices, ${((p.terrain.n - 1) * p.terrain.cell / 100).toFixed(0)} m, heights ${(lo / 100).toFixed(1)}–${(hi / 100).toFixed(1)} m`;
      if ($('wt-t-low').value === '') $('wt-t-low').value = (lo / 100).toFixed(1);
      if ($('wt-t-high').value === '') $('wt-t-high').value = (Math.max(hi, lo + 100) / 100).toFixed(1);
    }
    $('wt-t-export').onclick = async () => {
      const p = selTerrain(); if (!p) return H.status('select a terrain patch', true);
      const { n, heights } = p.terrain, lo = Math.min(...heights), hi = Math.max(...heights), span = Math.max(1, hi - lo);
      const cv = document.createElement('canvas'); cv.width = cv.height = n;
      const ctx = cv.getContext('2d'), img = ctx.createImageData(n, n);
      for (let row = 0; row < n; row++) for (let i = 0; i < n; i++) {
        const v = Math.round((heights[(n - 1 - row) * n + i] - lo) / span * 255), o = (row * n + i) * 4;
        img.data[o] = img.data[o + 1] = img.data[o + 2] = v; img.data[o + 3] = 255;
      }
      ctx.putImageData(img, 0, 0);
      saveExport(`${p.model}_${(lo / 100).toFixed(1)}m_${(hi / 100).toFixed(1)}m.png`, { data_base64: cv.toDataURL('image/png') });
    };
    $('wt-t-import').onclick = () => { if (!selTerrain()) return H.status('select a terrain patch', true); $('wt-t-file').click(); };
    $('wt-t-file').onchange = async (ev) => {
      const f = ev.target.files[0]; ev.target.value = ''; const p = selTerrain(); if (!f || !p) return;
      const bmp = await createImageBitmap(f), n = p.terrain.n;
      const cv = document.createElement('canvas'); cv.width = cv.height = n;
      const ctx = cv.getContext('2d'); ctx.imageSmoothingQuality = 'high'; ctx.drawImage(bmp, 0, 0, n, n);
      const px = ctx.getImageData(0, 0, n, n).data, lo = num($('wt-t-low').value) * 100, hi = num($('wt-t-high').value, 10) * 100;
      const heights = new Array(n * n);
      for (let row = 0; row < n; row++) for (let i = 0; i < n; i++) {
        const o = (row * n + i) * 4, g = (px[o] * 0.299 + px[o + 1] * 0.587 + px[o + 2] * 0.114) / 255;
        heights[(n - 1 - row) * n + i] = +(lo + g * (hi - lo)).toFixed(1);
      }
      const r = await H.run('terrain_set', { model: p.model, heights }, 'heightmap imported').catch(() => null);
      if (r) { await H.refresh(); await H.select({ line: p.line }); showTerrain(); }
    };
    // brush falloff slider in the Terrain panel (app.js applyBrush reads #t-falloff)
    const strength = $('t-strength')?.closest('label');
    if (strength && !$('t-falloff')) strength.after(el('label', { title: 'how much of the radius gets the full strength (100 = soft all the way, 10 = hard edge)' },
      'Falloff % ', el('input', { id: 't-falloff', type: 'range', min: 1, max: 100, value: store.get('falloff', 100), onchange: (e) => store.set('falloff', +e.target.value) })));

    // ================================================================ Keys (rebindable hotkeys) ================
    const ACTIONS = [['drop', 'Drop to ground', 'f'], ['focus', 'Focus selection', 'c'], ['isolate', 'Isolate', 'i'], ['top', 'Top view', 't'],
      ['dup', 'Duplicate', 'ctrl+d'], ['del', 'Delete', 'delete'], ['copy', 'Copy', 'ctrl+c'], ['paste', 'Paste under the mouse', 'ctrl+v'],
      ['pip', 'Paste in place', 'ctrl+shift+v'], ['undo', 'Undo', 'ctrl+z'], ['redo', 'Redo', 'ctrl+y'], ['hist', 'History', 'ctrl+h'],
      ['fw', 'Fly forward', 'w'], ['bk', 'Fly back', 's'], ['lt', 'Fly left', 'a'], ['rt', 'Fly right', 'd'], ['dn', 'Fly down', 'q'], ['up', 'Fly up', 'e']];
    let binds = store.get('keys', {});
    const comboOf = (e) => [e.ctrlKey && 'ctrl', e.shiftKey && 'shift', e.altKey && 'alt', e.key.toLowerCase()].filter(Boolean).join('+');
    const parse = (c) => { const p = c.split('+'); return { ctrlKey: p.includes('ctrl'), shiftKey: p.includes('shift'), altKey: p.includes('alt'), key: p[p.length - 1] }; };
    const keyName = (c) => c.split('+').map((p) => (p.length === 1 ? p.toUpperCase() : p[0].toUpperCase() + p.slice(1))).join('+');
    let capturing = null;
    function drawKeys() {
      const box = $('wt-k-list'); box.innerHTML = '';
      for (const [id, label, def] of ACTIONS) {
        const cur = binds[id] || def;
        box.append(el('div', { class: 'row' }, el('span', { text: label, style: 'flex:1' }),
          el('button', { text: capturing === id ? 'press a key…' : keyName(cur), class: binds[id] ? 'on' : '', onclick: () => { capturing = id; drawKeys(); } }),
          binds[id] ? el('button', { text: '↺', title: `back to ${keyName(def)}`, onclick: () => { delete binds[id]; store.set('keys', binds); drawKeys(); } }) : null));
      }
    }
    pages.keys.append(el('div', { id: 'wt-k-list', class: 'wt-scroll' }),
      el('div', { class: 'row' }, el('button', { id: 'wt-k-reset', text: 'Reset all' })),
      el('p', { class: 'hint', text: 'Click a key, then press the new one (Esc cancels). The old key stops working for that action. Kept on this PC.' }));
    $('wt-k-reset').onclick = () => { binds = {}; store.set('keys', binds); drawKeys(); };
    const typing = (e) => ['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target?.tagName) || document.querySelector('dialog[open]');
    function remap(e) {
      if (e.wtRemapped || typing(e)) return;
      const combo = comboOf(e), bare = e.key.toLowerCase();
      if (e.type === 'keydown' && capturing) {
        if (['control', 'shift', 'alt', 'meta'].includes(bare)) return;
        e.preventDefault(); e.stopImmediatePropagation();
        if (bare !== 'escape') { const def = ACTIONS.find((a) => a[0] === capturing)[2]; if (combo === def) delete binds[capturing]; else binds[capturing] = combo; store.set('keys', binds); }
        capturing = null; drawKeys(); return;
      }
      if (!Object.keys(binds).length) return;
      const fly = new Set(['fw', 'bk', 'lt', 'rt', 'dn', 'up']);
      for (const [id, , def] of ACTIONS) {
        const mine = binds[id]; if (!mine) continue;
        const hit = e.type === 'keyup' && fly.has(id) ? bare === parse(mine).key : combo === mine;
        if (hit) {
          e.preventDefault(); e.stopImmediatePropagation();
          const out = new KeyboardEvent(e.type, { ...parse(def), bubbles: true, cancelable: true }); out.wtRemapped = true;
          window.dispatchEvent(out);
          return;
        }
      }
      // a default key whose action moved elsewhere does nothing now
      for (const [id, , def] of ACTIONS) if (binds[id] && (e.type === 'keyup' ? bare === parse(def).key && !Object.values(binds).some((b) => parse(b).key === bare) : combo === def)) {
        if (Object.entries(binds).some(([, b]) => b === combo)) continue;
        e.stopImmediatePropagation(); return;
      }
    }
    window.addEventListener('keydown', remap, { capture: true });
    window.addEventListener('keyup', remap, { capture: true });

    // ---------- refresh per tab ----------
    const onShow = { pal: refreshRegions, check: () => {}, notes: loadNotes, link: refreshLinks, spawn: refreshSpawns, cam: () => { refreshCams(); drawShots(); }, terr: showTerrain, keys: drawKeys };
    panel.querySelector('#wt-tabs').addEventListener('click', (e) => { const k = e.target.dataset?.tab; if (k && onShow[k]) Promise.resolve(onShow[k]()).catch(() => {}); });
    H.hooks.afterBuild.push(() => { if (!panel.hidden) { const k = store.get('tab', 'pal'); if (onShow[k] && k !== 'notes') Promise.resolve(onShow[k]()).catch(() => {}); } });
    loadLibrary().then(() => { objList.innerHTML = ''; for (const e of lib) if (e.kind === 'object' && !/_VEH_|HMG0/i.test(e.unit || '')) objList.append(el('option', { value: `${e.label} [${e.kind} ${e.id}]` })); }).catch(() => {});

    // ================================================================ round 3 ================
    H.slowCommands?.add('play_from_here');

    // ---------- collision view (View tab) ----------
    const CLASS_COLOUR = { walk: [0.30, 0.82, 0.42], blocking: [0.95, 0.45, 0.20], invisible: [0.85, 0.35, 0.95],
                           barrier: [0.30, 0.60, 1.00], render: [0.60, 0.60, 0.60] };
    const colGroup = new THREE.Group(); H.scene3.add(colGroup);
    let colAt = null, colBusy = false, colTimer = null;
    pages.view.append(el('fieldset', {}, el('legend', { text: 'Collision' }),
      el('div', { class: 'row' }, el('label', {}, el('input', { type: 'checkbox', id: 'wt-col-on' }), ' Show collision'),
        el('label', {}, 'within ', el('input', { id: 'wt-col-r', type: 'number', min: 10, max: 300, value: store.get('colRadius', 60), style: 'width:4em' }), ' m')),
      el('label', { class: 'row', title: 'Draw it on top of everything (else only where not hidden by the level)' },
        el('input', { type: 'checkbox', id: 'wt-col-xray', checked: store.get('colXray', true) || null }), ' see through walls'),
      el('div', { id: 'wt-col-legend', class: 'wt-legend' }),
      el('p', { class: 'hint', text: 'What the game collides with around the view centre (follows it): green = walkable, orange = blocking, blue = barriers (players / enemies only), purple = invisible walls, grey = render only. Terrain patches and roads you made get theirs when the mission saves.' })));
    function clearCollision() { for (const c of colGroup.children) c.geometry?.dispose(); colGroup.clear(); H.requestRender(); }
    async function loadCollision(force = false) {
      if (!$('wt-col-on').checked || colBusy || !H.data) return;
      const at = H.orbit.target.clone(), radius = num($('wt-col-r').value, 60);
      if (!force && colAt && colAt.distanceTo(at) < radius / 3) return;
      colBusy = true;
      try {
        const r = await H.api('collision_view', { center: H.toGame(at), radius });
        const bytes = Uint8Array.from(atob(r.positions), (c) => c.charCodeAt(0));
        const pos = new Float32Array(bytes.buffer), surf = Uint8Array.from(atob(r.surfaces), (c) => c.charCodeAt(0));
        const xray = $('wt-col-xray').checked;
        if (!xray) {                       // lift each triangle 5 cm off its surface (the log depth buffer ignores polygonOffset)
          const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3(), n = new THREE.Vector3();
          for (let i = 0; i < pos.length; i += 9) {
            a.fromArray(pos, i); b.fromArray(pos, i + 3); c.fromArray(pos, i + 6);
            n.subVectors(b, a).cross(c.clone().sub(a)).normalize(); if (n.y < 0) n.negate(); n.multiplyScalar(0.05);
            for (let k = 0; k < 9; k += 3) { pos[i + k] += n.x; pos[i + k + 1] += n.y; pos[i + k + 2] += n.z; }
          }
        }
        const cls = Object.fromEntries(r.legend.map((l) => [l.surface, l.class]));
        const col = new Float32Array(pos.length);
        for (let t = 0; t < surf.length; t++) { const c = CLASS_COLOUR[cls[surf[t]]] || [1, 1, 1]; for (let k = 0; k < 3; k++) col.set(c, (t * 3 + k) * 3); }
        const g = new THREE.BufferGeometry();
        g.setAttribute('position', new THREE.BufferAttribute(pos, 3)); g.setAttribute('color', new THREE.BufferAttribute(col, 3));
        clearCollision();
        const fill = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ vertexColors: true, transparent: true, opacity: xray ? 0.3 : 0.45,
          side: THREE.DoubleSide, depthWrite: false, depthTest: !xray }));
        const wire = new THREE.LineSegments(new THREE.WireframeGeometry(g), new THREE.LineBasicMaterial({ color: 0x101010, transparent: true,
          opacity: xray ? 0.35 : 0.3, depthTest: !xray }));
        fill.renderOrder = 9; wire.renderOrder = 9; colGroup.add(fill, wire);
        colAt = at;
        const box = $('wt-col-legend'); box.innerHTML = '';
        for (const l of r.legend.slice(0, 10)) box.append(el('div', { class: 'row' },
          el('span', { class: 'wt-swatch', style: `background:rgb(${(CLASS_COLOUR[l.class] || [1, 1, 1]).map((v) => Math.round(v * 255)).join(',')})` }),
          el('span', { text: `${l.name} - ${l.triangles}` })));
        if (r.truncated) box.append(el('p', { class: 'bad', text: 'Too much to show - make the radius smaller.' }));
        H.requestRender();
      } catch (e) { H.status('collision: ' + e.message, true); }
      finally { colBusy = false; }
    }
    $('wt-col-on').onchange = (e) => {
      clearInterval(colTimer); colAt = null;
      if (e.target.checked) { loadCollision(true); colTimer = setInterval(() => loadCollision(), 1000); }
      else { clearCollision(); $('wt-col-legend').innerHTML = ''; }
    };
    $('wt-col-r').onchange = (e) => { store.set('colRadius', +e.target.value); loadCollision(true); };
    $('wt-col-xray').onchange = (e) => { store.set('colXray', e.target.checked); loadCollision(true); };
    H.hooks.afterBuild.push((fresh) => { if (fresh) { clearCollision(); colAt = null; } if ($('wt-col-on').checked) loadCollision(true); });

    // ---------- Play from here (next to ▶ Play) ----------
    const playBtn = $('play');
    if (playBtn && !$('play-here')) {
      playBtn.after(el('button', { id: 'play-here', text: '▶ From here', title: 'Save the mission and start the game with the squad standing where the view is centred (moved there when you get control, after the intro). Test only - the start point is never saved into the mission.',
        onclick: async () => {
          if (!(await scriptOk(null))) return;
          const at = H.orbit.target.clone(), dir = new THREE.Vector3(); H.camera.getWorldDirection(dir);
          const yaw = Math.atan2(dir.x, -dir.z) * 180 / Math.PI;
          const g = groundAt(at.x, at.z, at.y + 3);
          const pos = g ? g.pos : H.toGame(at);
          const r = await H.run('play_from_here', { pos, yaw: +yaw.toFixed(1) }, 'starting the game…').catch(() => null);
          if (!r) return;
          if ($('console')?.hidden) $('console-btn')?.click();
          H.status(`game starting in ${r.level} - the squad moves to ${pos.map((c) => (c / 100).toFixed(0)).join(', ')} m once you have control`);
        } }));
    }

    // ---------- script check (script window + before Play) ----------
    const cmOf = () => document.querySelector('#script-dialog .CodeMirror')?.CodeMirror || null;
    let marked = [];
    function showCheck(r) {
      const cm = cmOf();
      if (cm) { for (const l of marked) cm.removeLineClass(l, 'background', 'wt-err'); marked = []; }
      const items = [...r.errors.map((x) => ({ ...x, bad: true })), ...r.warnings];
      if (cm) for (const x of items) if (x.line) marked.push(cm.addLineClass(x.line - 1, 'background', x.bad ? 'wt-err' : 'wt-warn'));
      const first = items[0];
      if (!first) H.status(`script OK (${r.lines} lines)`);
      else H.status(`${r.errors.length ? 'script error' : 'script warning'}${first.line ? ` line ${first.line}` : ''}: ${first.message}`
        + (items.length > 1 ? ` (+${items.length - 1} more)` : ''), !!r.errors.length);
      if (cm && first?.line) { cm.scrollIntoView({ line: first.line - 1, ch: 0 }, 80); cm.setCursor(first.line - 1, 0); }
    }
    async function scriptOk(text) {
      let r;
      try { r = await H.api('check_script', text === null ? {} : { text }); } catch { return true; }   // no mission yet: Play explains
      showCheck(r);
      if (!r.errors.length) return true;
      const e = r.errors[0];
      return confirm(`The mission script has an error${e.line ? ` on line ${e.line}` : ''}:\n\n${e.message}\n\nThe level won't start with it. Play anyway?`);
    }
    const saveBtn = $('script-save');
    if (saveBtn && !$('script-check')) saveBtn.before(el('button', { id: 'script-check', text: '✓ Check', title: 'Check the script for errors (syntax, unknown game.* functions) - nothing runs',
      onclick: () => { const cm = cmOf(); H.api('check_script', { text: cm ? cm.getValue() : '' }).then(showCheck).catch((e) => H.status(e.message, true)); } }));
    // Play buttons check first (capture: before app.js's own click handlers)
    for (const [id, fromEditor] of [['play', false], ['script-play', true]]) {
      const b = $(id); if (!b) continue;
      b.addEventListener('click', async (e) => {
        if (b.dataset.wtChecked === '1') { b.dataset.wtChecked = ''; return; }
        e.stopImmediatePropagation(); e.preventDefault();
        const cm = cmOf();
        if (await scriptOk(fromEditor && cm ? cm.getValue() : null)) { b.dataset.wtChecked = '1'; b.click(); }
      }, { capture: true });
    }

    // ---------- measure (Checks tab) ----------
    pages.check.prepend(el('fieldset', {}, el('legend', { text: 'Measure' }),
      el('div', { class: 'row' }, el('button', { id: 'wt-m-go', text: 'Measure…' }), el('button', { id: 'wt-m-clear', text: 'Clear' })),
      el('p', { id: 'wt-m-out', class: 'hint', text: 'Click two points on the ground or a building: distance, across the ground, and height difference.' })));
    const measureGroup = new THREE.Group(); H.scene3.add(measureGroup);
    $('wt-m-clear').onclick = () => { measureGroup.clear(); $('wt-m-out').textContent = ''; H.requestRender(); };
    $('wt-m-go').onclick = () => H.pickGround('measure: click the first point', (a) => H.pickGround('click the second point', (b) => {
      measureGroup.clear();
      const pa = H.toThree(a), pb = H.toThree(b);
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([pa, pb]), new THREE.LineBasicMaterial({ color: 0xffffff, depthTest: false }));
      line.renderOrder = 12; measureGroup.add(line);
      for (const p of [pa, pb]) { const m = new THREE.Mesh(new THREE.SphereGeometry(0.15, 10, 8), new THREE.MeshBasicMaterial({ color: 0xffffff, depthTest: false })); m.position.copy(p); m.renderOrder = 12; measureGroup.add(m); }
      const d = pa.distanceTo(pb), flat = Math.hypot(pa.x - pb.x, pa.z - pb.z), dy = pb.y - pa.y;
      const slope = flat > 0.01 ? Math.atan2(Math.abs(dy), flat) * 180 / Math.PI : 90;
      $('wt-m-out').textContent = `${d.toFixed(2)} m (across the ground ${flat.toFixed(2)} m, height ${dy >= 0 ? '+' : ''}${dy.toFixed(2)} m, slope ${slope.toFixed(0)}°)`;
      H.status($('wt-m-out').textContent); H.requestRender();
    }));
    // ---------- doors & keys ----------
    const D = pages.doors;
    D.append(el('div', { id: 'wt-d-list', class: 'wt-scroll', style: 'max-height:420px' }),
      el('datalist', { id: 'wt-d-items' }),
      el('p', { class: 'hint', text: 'Every door with the switches that drive it and what the base mission does to it (its DLL: keys, unlocking). Locked / key here are your settings for this mission (a mission-start rule) - not yet verified in game.' }));
    async function refreshDoors() {
      const r = await H.api('doors_report').catch((e) => { H.status(e.message, true); return null; }); if (!r) return;
      const items = $('wt-d-items'); items.innerHTML = '';
      for (const o of H.data.objects) if (!/HERO|_VEH_|IR_DOOR/i.test(o.model + o.unit)) items.append(el('option', { value: `${o.unit} #${o.script_id}` }));
      const box = $('wt-d-list'); box.innerHTML = '';
      if (!r.doors.length) box.append(el('p', { class: 'hint', text: 'No door objects in this scene.' }));
      for (const d of r.doors) {
        const ed = d.editor || {};
        const lock = el('select', { title: 'locked at the start' }, el('option', { value: '', text: 'as the level' }), el('option', { value: '1', text: 'locked' }), el('option', { value: '0', text: 'unlocked' }));
        lock.value = ed.locked === true ? '1' : ed.locked === false ? '0' : '';
        const key = el('input', { list: 'wt-d-items', placeholder: 'key item…', style: 'width:9em', value: ed.key != null ? `#${ed.key}` : '' });
        const save = async () => {
          const m = key.value.match(/#(-?\d+)\s*$/), k = key.value.trim() ? (m ? +m[1] : null) : -1;
          if (k === null) return H.status('pick the key item from the list', true);
          await H.run('door_set', { door: d.script_id, locked: lock.value === '' ? null : lock.value === '1', key: k }, `door #${d.script_id} set`).catch(() => {});
        };
        lock.onchange = save; key.onchange = save;
        const dll = [...d.dll_keys.map((k) => `key ${k.item || ''} #${k.key}`), ...d.dll_flags.map((f) => (f.mask & 8) ? (f.value & 8 ? 'locks it' : 'unlocks it') : `flags ${f.mask}=${f.value}`)];
        box.append(el('div', { class: 'wt-entry' },
          el('div', { class: 'row' }, el('a', { href: '#', text: `#${d.script_id} ${d.unit}`, style: 'flex:1', onclick: (e) => { e.preventDefault(); H.select({ line: d.line }, true); } })),
          el('div', { class: 'row hint' }, `switches: ${d.switches.join(', ') || 'none'}${dll.length ? ' · base mission: ' + dll.join(', ') : ''}`),
          el('div', { class: 'row' }, lock, key)));
      }
    }
    onShow.doors = refreshDoors;
    // ---------- portals (View: show; Checks: problems + fix) ----------
    const portalGroup = new THREE.Group(); H.scene3.add(portalGroup);
    pages.view.append(el('fieldset', {}, el('legend', { text: 'Portals' }),
      el('label', { class: 'row' }, el('input', { type: 'checkbox', id: 'wt-p-show' }), ' Show portals'),
      el('p', { class: 'hint', text: 'The openings between zones the game draws through (a zone is drawn only when a portal to it is in view): cyan = fine, orange = a problem (Checks › Portals).' })));
    pages.check.append(el('fieldset', {}, el('legend', { text: 'Portals' }),
      el('div', { class: 'row' }, el('button', { id: 'wt-pc-go', text: 'Check portals' }), el('button', { id: 'wt-pc-fix', text: 'Fix plane / centre (all)' })),
      el('div', { id: 'wt-pc-out', class: 'wt-scroll' })));
    async function drawPortals() {
      for (const c of portalGroup.children) c.geometry?.dispose();
      portalGroup.clear();
      if (!$('wt-p-show').checked) { H.requestRender(); return null; }
      const r = await H.api('portals').catch((e) => { H.status(e.message, true); return null; });
      if (!r) return null;
      for (const p of r.portals) {
        const pts = p.vertices.map((v) => H.toThree(v));
        if (pts.length < 3) continue;
        const bad = p.problems.length > 0, colour = bad ? 0xffa040 : 0x40e0f0;
        const pos = [];
        for (let k = 1; k < pts.length - 1; k++) for (const q of [pts[0], pts[k], pts[k + 1]]) pos.push(q.x, q.y, q.z);
        const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
        const fill = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color: colour, transparent: true, opacity: 0.18, side: THREE.DoubleSide, depthWrite: false }));
        const edge = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: colour }));
        fill.renderOrder = edge.renderOrder = 8; portalGroup.add(fill, edge);
      }
      H.requestRender();
      return r;
    }
    $('wt-p-show').onchange = drawPortals;
    async function checkPortals() {
      const r = await H.api('portals').catch((e) => { H.status(e.message, true); return null; }); if (!r) return;
      const box = $('wt-pc-out'); box.innerHTML = '';
      box.append(el('p', { class: r.with_problems ? 'bad' : 'hint', text: `${r.portals.length} portals, ${r.with_problems} with problems` }));
      for (const p of r.portals.filter((x) => x.problems.length)) box.append(el('div', { class: 'wt-entry' },
        el('div', { class: 'row' }, el('a', { href: '#', text: p.name, style: 'flex:1', onclick: (e) => { e.preventDefault(); H.focusOn(H.toThree(p.centre), 12); } }),
          p.problems.some((q) => q.fix) ? el('button', { text: 'Fix', onclick: async () => { await H.run('portal_fix', { line: p.line }, 'portal fixed'); checkPortals(); drawPortals(); } }) : null),
        ...p.problems.map((q) => el('div', { class: q.severity === 'error' ? 'bad' : 'hint', text: q.message }))));
    }
    $('wt-pc-go').onclick = checkPortals;
    $('wt-pc-fix').onclick = async () => { const r = await H.run('portal_fix', {}, 'portals fixed').catch(() => null); if (r) { H.status(`${r.fixed.length} portal(s) fixed`); checkPortals(); drawPortals(); } };
    H.hooks.afterBuild.push(() => { if ($('wt-p-show').checked) drawPortals(); });
    drawPal(); syncPickUi(); fillSaved().catch(() => {});
    if (new URLSearchParams(location.search).get('worldtools')) open(new URLSearchParams(location.search).get('worldtools'));
  }

  if (window.editorHost) start(window.editorHost);
  else window.addEventListener('editor-host-ready', () => start(window.editorHost), { once: true });
})();
