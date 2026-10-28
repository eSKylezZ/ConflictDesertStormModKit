// Panels: every tool panel (Terrain, Road, Paths & Regions, Atmosphere, Mission Settings, Multiplayer Spawns, History,
// World Tools, Console) works the same way - its own window, shown / hidden on its own (its menu button, its Close
// button or the Panels menu), dragged by its title bar, resized from its corner, brought to the front when clicked,
// docked into the right column (⇥) or popped out of the editor into a window of its own (⧉, or drag it past the
// editor's edge) that can go anywhere - another monitor too, like the Creation Kit's. Placement is remembered on this PC.
// The panels' own open / close code (data loading, brush clean-up) still runs: showing and hiding go through their
// existing buttons. A popped-out panel is the same live element moved into a WebView2 popup window (app.py lets
// about:blank windows open) - its code keeps running in the editor; lookups by id find it there (see below).
(() => {
  const PANELS = [
    { id: 'world-tools', title: 'World Tools', toggle: 'worldtools-btn' },
    { id: 'terrain', title: 'Terrain', toggle: 'terrain-btn' },
    { id: 'road', title: 'Road', toggle: 'road-btn' },
    { id: 'paths-panel', title: 'Paths & Regions', toggle: 'paths-btn' },
    { id: 'atmos', title: 'Atmosphere', toggle: 'atmos-btn' },
    { id: 'mission-settings', title: 'Mission Settings', toggle: 'settings-btn' },
    { id: 'spawns', title: 'Multiplayer Spawns', toggle: 'spawns-btn' },
    { id: 'history-panel', title: 'History', toggle: 'history-btn' },
    { id: 'console', title: 'Console', toggle: 'console-btn', dockable: false },
  ];
  const KEY = 'panels.v1';
  const load = () => { try { return JSON.parse(localStorage.getItem(KEY) || '{}'); } catch { return {}; } };
  const save = (st) => { try { localStorage.setItem(KEY, JSON.stringify(st)); } catch { /* storage blocked */ } };
  let state = load();
  const order = [];                       // bottom -> top
  const Z0 = 21;                          // panels stay under the header (30) and its menus
  const popped = new Map();               // panel id -> its popup window

  // ---- lookups by id / selector also find controls in popped-out panels (app.js finds its controls with
  // document.getElementById / querySelector at the time it needs them) ----
  const popDocs = () => [...popped.values()].filter((w) => w && !w.closed).map((w) => w.document);
  const gid = document.getElementById.bind(document), qs = document.querySelector.bind(document);
  const qsa = document.querySelectorAll.bind(document);
  document.getElementById = (id) => { const e = gid(id); if (e) return e; for (const d of popDocs()) { const x = d.getElementById(id); if (x) return x; } return null; };
  document.querySelector = (sel) => { const e = qs(sel); if (e) return e; for (const d of popDocs()) { const x = d.querySelector(sel); if (x) return x; } return null; };
  document.querySelectorAll = (sel) => { const a = [...qsa(sel)]; for (const d of popDocs()) a.push(...d.querySelectorAll(sel)); return a; };
  const $ = (id) => document.getElementById(id);

  function front(el) {
    const i = order.indexOf(el); if (i >= 0) order.splice(i, 1);
    order.push(el);
    order.forEach((p, k) => { if (p.classList.contains('floating')) p.style.zIndex = String(Z0 + Math.min(k, 8)); });
  }
  function clamp(el) {
    if (!el.classList.contains('floating') || el.hidden) return;
    const r = el.getBoundingClientRect(), W = window.innerWidth, H = window.innerHeight;
    const x = Math.min(Math.max(r.left, 40 - r.width), W - 60), y = Math.min(Math.max(r.top, 44), H - 34);
    el.style.left = x + 'px'; el.style.top = y + 'px';
  }
  function remember(p, el) {
    const s = state[p.id] || (state[p.id] = {});
    s.docked = el.classList.contains('docked');
    s.popped = el.classList.contains('popped');
    if (!s.docked && !s.popped) {
      const r = el.getBoundingClientRect();
      Object.assign(s, { x: Math.round(r.left), y: Math.round(r.top) });
      if (el.dataset.sized) Object.assign(s, { w: Math.round(r.width), h: Math.round(r.height) });
    }
    save(state);
  }
  function defaultPlace(p, el, k) {
    if (p.id === 'console') {
      const h = Math.round(window.innerHeight * 0.34);
      Object.assign(el.style, { left: '0px', top: (window.innerHeight - h) + 'px', width: window.innerWidth + 'px', height: h + 'px' });
      el.dataset.sized = '1';
      return;
    }
    const w = p.id === 'world-tools' ? 340 : 300;
    Object.assign(el.style, { left: (window.innerWidth - w - 12 - 24 * (k % 5)) + 'px', top: (54 + 28 * (k % 5)) + 'px', width: w + 'px' });
  }
  function home(el) { if (el.ownerDocument !== document) document.body.appendChild(document.adoptNode(el)); }
  function float(p, el, k, at) {
    home(el);
    el.classList.remove('docked', 'popped'); el.classList.add('floating');
    if (el.parentElement !== document.body) document.body.appendChild(el);
    Object.assign(el.style, { position: 'fixed', right: 'auto', bottom: 'auto' });
    const s = state[p.id];
    if (at) Object.assign(el.style, { left: at.x + 'px', top: at.y + 'px', width: at.w + 'px' });
    else if (s && s.x !== undefined) {
      Object.assign(el.style, { left: s.x + 'px', top: s.y + 'px' });
      if (s.w) { el.style.width = s.w + 'px'; el.style.height = s.h + 'px'; el.dataset.sized = '1'; }
      else el.style.width = (p.id === 'world-tools' ? 340 : 300) + 'px';
    } else defaultPlace(p, el, k);
    clamp(el); front(el);
    buttons(p, el);
  }
  function dock(p, el) {
    const d = $('tool-dock'); if (!d || p.dockable === false) return;
    home(el);
    el.classList.remove('floating', 'popped'); el.classList.add('docked');
    for (const k of ['position', 'left', 'top', 'width', 'height', 'right', 'bottom', 'zIndex']) el.style[k] = '';
    delete el.dataset.sized;
    d.appendChild(el);
    buttons(p, el);
  }
  function buttons(p, el) {
    const b = el.querySelector(':scope > .console-head .wp-dock');
    if (b) {
      const docked = el.classList.contains('docked');
      b.hidden = el.classList.contains('popped');
      b.textContent = docked ? '⇤' : '⇥';
      b.title = docked ? 'Float this panel (or drag its title bar)' : 'Dock this panel into the right column';
    }
    const o = el.querySelector(':scope > .console-head .wp-pop');
    if (o) {
      const out = el.classList.contains('popped');
      o.textContent = out ? '⇲' : '⧉';
      o.title = out ? 'Put this panel back into the editor window' : 'Pop this panel out into a window of its own (or drag it past the editor\'s edge)';
    }
  }

  // ---- popped-out panels: a WebView2 popup window per panel ----
  function popOut(p, el, k, at) {
    const s = state[p.id] || (state[p.id] = {});
    const r = el.getBoundingClientRect();
    const w = Math.round(at?.w || s.sw || Math.max(300, r.width || 320));
    const h = Math.round(at?.h || s.sh || Math.min(700, Math.max(240, r.height || 480)));
    const x = Math.round(at?.x ?? s.sx ?? (window.screenX + window.outerWidth - w - 40));
    const y = Math.round(at?.y ?? s.sy ?? (window.screenY + 90));
    const win = window.open('about:blank', `ce_panel_${p.id}`, `popup,width=${w},height=${h},left=${x},top=${y}`);
    if (!win) { float(p, el, k); if (window.editorHost) window.editorHost.status('couldn\'t open a window for the panel', true); return; }
    const d = win.document;
    d.title = `${p.title} - Conflict Editor`;
    d.documentElement.setAttribute('style', document.documentElement.getAttribute('style') || '');
    for (const a of document.documentElement.attributes) if (a.name !== 'style') d.documentElement.setAttribute(a.name, a.value);
    for (const n of document.querySelectorAll('link[rel=stylesheet], style')) {
      const c = d.importNode(n, true);
      if (c.tagName === 'LINK') c.href = n.href;                   // absolute: the popup's address is about:blank
      d.head.appendChild(c);
    }
    d.body.className = 'wp-window';
    el.classList.remove('floating', 'docked'); el.classList.add('popped');
    for (const k2 of ['position', 'left', 'top', 'width', 'height', 'right', 'bottom', 'zIndex']) el.style[k2] = '';
    d.body.appendChild(d.adoptNode(el));
    popped.set(p.id, win);
    buttons(p, el);
    Object.assign(s, { popped: true, sx: x, sy: y, sw: w, sh: h }); save(state);
    // the window's own close button closes the panel (it opens popped out again next time)
    let timer = 0;
    const gone = () => {
      clearInterval(timer);
      if (popped.get(p.id) !== win) return;                // put back on purpose already
      popped.delete(p.id);
      home(el);
      el.classList.remove('popped');
      float(p, el, k); el.hidden = true;
      s.popped = true; save(state);
    };
    const where = () => {
      if (win.closed) { gone(); return; }                  // closed without a pagehide reaching us
      Object.assign(s, { sx: win.screenX, sy: win.screenY, sw: win.outerWidth, sh: win.outerHeight }); save(state);
    };
    timer = setInterval(where, 1000);
    win.addEventListener('resize', where);
    win.addEventListener('pagehide', gone);
    // keys typed in the panel's window that aren't for its fields work in the editor too (Ctrl+Z, Esc ...)
    win.addEventListener('keydown', (e) => {
      if (['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName) && !e.ctrlKey) return;
      window.dispatchEvent(new KeyboardEvent('keydown', e));
    });
    win.focus();
  }
  function popIn(p, el, k) {
    const win = popped.get(p.id);
    popped.delete(p.id);
    const s = state[p.id] || (state[p.id] = {});
    if (win && !win.closed) { Object.assign(s, { sx: win.screenX, sy: win.screenY, sw: win.outerWidth, sh: win.outerHeight }); }
    s.popped = false; save(state);
    float(p, el, k);
    if (win && !win.closed) win.close();
    remember(p, el);
  }

  function setup(p, k) {
    const el = $(p.id);
    if (!el || el.dataset.wp) return !!el;
    el.dataset.wp = '1';
    el.classList.add('wpanel');
    const head = el.querySelector(':scope > .console-head');
    if (head) {
      head.classList.add('wp-head');
      head.title = 'Drag to move (past the editor\'s edge: its own window) · double-click to dock / float';
      const close = [...head.querySelectorAll('button')].pop();
      const pop = document.createElement('button');
      pop.type = 'button'; pop.className = 'wp-pop';
      pop.onclick = (e) => { e.stopPropagation(); if (el.classList.contains('popped')) popIn(p, el, k); else popOut(p, el, k); };
      head.insertBefore(pop, close || null);
      if (p.dockable !== false) {
        const b = document.createElement('button');
        b.type = 'button'; b.className = 'wp-dock';
        b.onclick = (e) => { e.stopPropagation(); toggleDock(p, el, k); };
        head.insertBefore(b, pop);
      }
      head.addEventListener('pointerdown', (e) => startDrag(e, p, el, k));
      head.addEventListener('dblclick', (e) => { if (!el.classList.contains('popped') && !e.target.closest('button, input, select, textarea, label, a')) toggleDock(p, el, k); });
    }
    el.addEventListener('pointerdown', () => { if (el.classList.contains('floating')) front(el); }, true);
    // a user resize (the corner) is remembered; the panel's own content changes aren't
    let resizing = false;
    el.addEventListener('pointerdown', (e) => {
      const r = el.getBoundingClientRect();
      resizing = el.classList.contains('floating') && e.clientX > r.right - 18 && e.clientY > r.bottom - 18;
    });
    window.addEventListener('pointerup', () => { if (resizing) { resizing = false; el.dataset.sized = '1'; remember(p, el); } });
    // shown: back on screen and in front, or into its own window again; hidden while popped out: the window closes
    new MutationObserver(() => {
      if (!el.hidden) {
        if (state[p.id]?.popped && !popped.has(p.id)) popOut(p, el, k);
        else if (el.classList.contains('floating')) { clamp(el); front(el); }
      } else if (popped.has(p.id)) {
        const win = popped.get(p.id); popped.delete(p.id);
        home(el); el.classList.remove('popped'); float(p, el, k); el.hidden = true;
        if (win && !win.closed) win.close();
      }
    }).observe(el, { attributes: true, attributeFilter: ['hidden'] });
    if (state[p.id]?.docked && p.dockable !== false) dock(p, el); else float(p, el, k);
    return true;
  }
  function toggleDock(p, el, k) {
    if (el.classList.contains('docked')) {
      const r = el.getBoundingClientRect();
      float(p, el, k, { x: Math.max(60, r.left - 340), y: Math.max(54, r.top), w: Math.max(260, r.width) });
    } else dock(p, el);
    remember(p, el);
  }
  function startDrag(e, p, el, k) {
    if (e.button !== 0 || el.classList.contains('popped') || e.target.closest('button, input, select, textarea, label, a')) return;
    e.preventDefault();
    if (el.classList.contains('docked')) {                 // dragging a docked panel floats it under the pointer
      const r = el.getBoundingClientRect();
      float(p, el, k, { x: r.left, y: r.top, w: r.width });
    }
    front(el);
    const head = e.currentTarget;
    try { head.setPointerCapture(e.pointerId); } catch { /* synthetic events */ }
    const r = el.getBoundingClientRect(), dx = e.clientX - r.left, dy = e.clientY - r.top;
    const outside = (ev) => ev.clientX < 0 || ev.clientY < 0 || ev.clientX > window.innerWidth || ev.clientY > window.innerHeight;
    const move = (ev) => {
      el.style.left = Math.min(Math.max(ev.clientX - dx, 40 - r.width), window.innerWidth - 60) + 'px';
      el.style.top = Math.min(Math.max(ev.clientY - dy, 44), window.innerHeight - 34) + 'px';
      el.classList.toggle('wp-leaving', outside(ev));
    };
    const up = (ev) => {
      window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up);
      el.classList.remove('wp-leaving');
      if (outside(ev)) {                                     // let go past the editor's edge: its own window there
        popOut(p, el, k, { x: ev.screenX - dx, y: ev.screenY - dy, w: r.width, h: Math.max(240, r.height) });
        return;
      }
      remember(p, el);
    };
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
  }

  // ---- Panels menu (in the header, after Data) ----
  function isShown(p) { const el = $(p.id); return !!el && !el.hidden; }
  function toggle(p, on = !isShown(p)) {
    if (on === isShown(p)) return;
    const el = $(p.id);
    if (on) { $(p.toggle)?.click(); return; }
    const close = el?.querySelector(':scope > .console-head button:last-of-type');
    if (close && /close/i.test(close.textContent)) close.click(); else $(p.toggle)?.click();
    if (el && !el.hidden) el.hidden = true;                // a toggle that only opens
  }
  function buildMenu() {
    const data = [...document.querySelectorAll('.menu')].find((m) => /Data/.test(m.querySelector('.menu-btn')?.textContent || ''));
    if (!data || $('panels-menu')) return;
    const m = document.createElement('div'); m.className = 'menu'; m.id = 'panels-menu';
    m.innerHTML = '<button class="menu-btn" type="button">Panels ▾</button><div class="menu-list" hidden></div>';
    data.after(m);
    const btn = m.querySelector('.menu-btn'), list = m.querySelector('.menu-list');
    const fill = () => {
      list.innerHTML = '';
      for (const p of PANELS) {
        if (!$(p.id) || !$(p.toggle)) continue;
        const b = document.createElement('button'); b.type = 'button';
        b.textContent = `${isShown(p) ? '✓' : ' '} ${p.title}${state[p.id]?.popped ? '  ⧉' : ''}`;
        b.onclick = () => toggle(p);
        list.appendChild(b);
      }
      list.appendChild(document.createElement('hr'));
      const all = document.createElement('button'); all.type = 'button'; all.textContent = 'Close all panels';
      all.onclick = () => PANELS.forEach((p) => toggle(p, false));
      const back = document.createElement('button'); back.type = 'button'; back.textContent = 'Bring all panels into the editor';
      back.onclick = () => PANELS.forEach((p, k) => { const el = $(p.id); if (el && el.classList.contains('popped')) popIn(p, el, k); else if (state[p.id]) { state[p.id].popped = false; save(state); } });
      const reset = document.createElement('button'); reset.type = 'button'; reset.textContent = 'Reset panel positions';
      reset.title = 'Every panel back to its default place and size, floating in the editor';
      reset.onclick = () => {
        PANELS.forEach((p, k) => { const el = $(p.id); if (el?.classList.contains('popped')) popIn(p, el, k); });
        state = {}; save(state);
        PANELS.forEach((p, k) => { const el = $(p.id); if (el?.dataset.wp) { delete el.dataset.sized; el.style.height = ''; float(p, el, k); defaultPlace(p, el, k); clamp(el); } });
      };
      list.append(all, back, reset);
    };
    btn.onclick = () => {
      const open = list.hidden;
      for (const o of document.querySelectorAll('.menu')) { o.classList.remove('open'); o.querySelector('.menu-list').hidden = true; }
      if (open) { fill(); m.classList.add('open'); list.hidden = false; }
    };
    btn.onmouseenter = () => { if (document.querySelector('.menu.open') && !m.classList.contains('open')) btn.click(); };
    list.addEventListener('click', (e) => { if (e.target.closest('button')) { m.classList.remove('open'); list.hidden = true; } });
  }

  let tries = 0;
  function init() {
    let missing = 0;
    PANELS.forEach((p, k) => { if (!setup(p, k)) missing++; });
    buildMenu();
    if (missing && ++tries < 120) setTimeout(init, 500);   // World Tools is built by worldtools.js once the editor is up
  }
  window.addEventListener('resize', () => { for (const p of PANELS) { const el = $(p.id); if (el) clamp(el); } });
  window.addEventListener('beforeunload', () => { for (const w of popped.values()) if (w && !w.closed) w.close(); });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => setTimeout(init, 0));
  else setTimeout(init, 0);
  window.editorPanels = {
    toggle: (id, on) => { const p = PANELS.find((x) => x.id === id); if (p) toggle(p, on); },
    popOut: (id, at) => { const k = PANELS.findIndex((x) => x.id === id); const el = $(id); if (k >= 0 && el) popOut(PANELS[k], el, k, at); },
    popIn: (id) => { const k = PANELS.findIndex((x) => x.id === id); const el = $(id); if (k >= 0 && el) popIn(PANELS[k], el, k); },
    windowOf: (id) => popped.get(id) || null,
    panels: PANELS.map((p) => p.id),
  };
})();
