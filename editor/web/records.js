// Records window: the game's data tables (weapons, soldier types, stats, kits, vehicles, items, scenery, doors) and
// texts - browse, follow links, change them in the mod named in the header (editor/records.py commands).
(() => {
  const el = (tag, props = {}, ...kids) => {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (k === 'class') e.className = v;
      else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
      else if (v !== undefined && v !== null) e.setAttribute(k, v);
    }
    for (const k of kids) if (k !== null && k !== undefined) e.append(k);
    return e;
  };
  async function call(cmd, args = {}) {
    const r = await fetch('/api/' + cmd, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                          body: JSON.stringify(args) });
    const j = await r.json();
    if (!j.ok) throw new Error(j.error);
    return j.result;
  }
  const modName = () => (document.getElementById('mod')?.value || '').trim();
  const say = (text, bad = false) => {
    const s = document.getElementById('status');
    if (s) { s.textContent = text; s.classList.toggle('bad', bad); }
    msg.textContent = text; msg.classList.toggle('bad', bad);
  };

  // ---- window ----
  const dlg = el('dialog', { id: 'records-dialog' });
  const tabRec = el('button', { class: 'on' }, 'Records');
  const tabTxt = el('button', {}, 'Texts');
  const msg = el('span', { class: 'hint rec-msg' });
  const tables = el('div', { class: 'rec-tables' });
  const search = el('input', { placeholder: 'Search', class: 'rec-search' });
  const list = el('div', { class: 'rec-list' });
  const form = el('div', { class: 'rec-form' }, el('p', { class: 'hint' }, 'Pick a table and a record.'));
  const recPane = el('div', { class: 'rec-body' }, tables, el('div', { class: 'rec-mid' }, search, list), form);
  const tSearch = el('input', { placeholder: 'Search texts (key or words)', class: 'rec-search' });
  const tKey = el('input', { placeholder: 'NEW_KEY', size: 22 });
  const tMission = el('input', { placeholder: 'mission (M01) or empty = all', size: 22,
                                 title: 'Only in this mission (its text file M01Strs.loc); empty = every level' });
  const tText = el('input', { placeholder: 'Text', class: 'grow' });
  const tList = el('div', { class: 'rec-texts' });
  const txtPane = el('div', { class: 'rec-body rec-text-body', hidden: '' },
    el('div', { class: 'row' }, tSearch),
    el('div', { class: 'row' }, tKey, tMission, tText, el('button', { onclick: () => setText(tKey.value, tText.value, tMission.value) }, 'Set text')),
    el('p', { class: 'hint' }, 'A game key changes that text everywhere the game shows it. A new key is a text for ' +
      'your mission: scripts use it by the hash shown. M01, T02 ... = a mission’s own texts (objectives, briefing, ' +
      'subtitles); missions reuse keys such as MOV_TXT1, so those change only in the mission shown. Search "M03" for one mission’s texts.'),
    tList);
  dlg.append(el('div', { class: 'console-head' }, el('strong', {}, 'Game data'), tabRec, tabTxt, msg,
    el('span', { class: 'grow' }), el('button', { onclick: () => dlg.close() }, 'Close')), recPane, txtPane);
  document.body.append(dlg);
  tabRec.onclick = () => { tabRec.classList.add('on'); tabTxt.classList.remove('on'); recPane.hidden = false; txtPane.hidden = true; };
  tabTxt.onclick = () => { tabTxt.classList.add('on'); tabRec.classList.remove('on'); recPane.hidden = true; txtPane.hidden = false; loadTexts(); };

  const btn = el('button', { id: 'records-btn', title: 'Game data: weapons, soldiers, stats, kits, vehicles, items, texts' },
                 'Records…');
  (document.getElementById('assets-btn') || document.querySelector('header'))?.after(btn);
  btn.onclick = () => { dlg.showModal(); if (!tables.childElementCount) loadTables(); };

  // ---- tables and lists ----
  let table = null, key = null, record = null;
  async function loadTables() {
    tables.replaceChildren();
    for (const t of await call('records_tables')) {
      tables.append(el('button', { class: 'rec-table', title: t.what, onclick: () => openTable(t.table) },
        el('span', {}, t.table.replace('.TXT', '')), el('small', {}, t.what)));
    }
  }
  async function openTable(t, thenKey) {
    table = t;
    for (const b of tables.children) b.classList.toggle('on', b.firstChild.textContent + '.TXT' === t);
    await refreshList();
    if (thenKey) openRecord(thenKey);
  }
  async function refreshList() {
    if (!table) return;
    const rows = await call('records_list', { table, mod: modName(), search: search.value });
    list.replaceChildren(...rows.map(r => el('button', { class: 'rec-item ' + r.state + (r.key === key ? ' on' : ''),
                                                         onclick: () => openRecord(r.key) },
      el('span', {}, r.key), r.state !== 'game' ? el('em', {}, r.state) : null, el('small', {}, r.summary))));
  }
  let timer = 0;
  search.oninput = () => { clearTimeout(timer); timer = setTimeout(refreshList, 200); };

  // ---- record form ----
  async function openRecord(k) {
    try {
      record = await call('record_get', { table, key: k, mod: modName() });
    } catch (e) { say(e.message, true); return; }
    key = record.key;
    for (const b of list.children) b.classList.toggle('on', b.firstChild.textContent.toUpperCase() === key.toUpperCase());
    drawForm();
  }
  function drawForm() {
    const r = record, cols = r.columns, multi = r.multi;
    const rows = r.rows.length ? r.rows.map(x => x.slice()) : [cols.map(() => '-1')];
    const game = r.game_rows[0] || [];
    const head = el('div', { class: 'rec-head' }, el('strong', {}, r.key), el('em', {}, r.state),
      el('span', { class: 'hint' }, r.table));
    const body = el('div', { class: multi ? 'rec-rows' : 'rec-fields' });
    const input = (ri, c) => {
      const v = rows[ri][c.index] ?? '';
      const inp = el('input', { value: v, title: c.name, type: c.type === 'text' ? 'text' : 'text',
                                class: c.type !== 'text' ? 'num' : '' });
      if (c.index === 0 && !multi) inp.disabled = true;
      inp.oninput = () => { rows[ri][c.index] = inp.value; inp.classList.toggle('diff', !multi && game[c.index] !== undefined && game[c.index] !== inp.value); };
      if (!multi && game[c.index] !== undefined && game[c.index] !== v) inp.classList.add('diff');
      return inp;
    };
    const linkBtn = (c, v) => c.link && v && v !== '-1'
      ? el('button', { class: 'rec-link', title: 'Open ' + c.link, onclick: () => openTable(c.link, v) }, '→ ' + c.link.replace('.TXT', ''))
      : null;
    if (!multi) {
      for (const c of cols) {
        body.append(el('label', { class: 'rec-field' + (c.name.startsWith('column') ? ' unnamed' : '') },
          el('span', {}, c.name), input(0, c), el('small', {}, c.type), linkBtn(c, rows[0][c.index]) || el('span')));
      }
    } else {
      const t = el('table', { class: 'rec-grid' }, el('tr', {}, ...cols.map(c => el('th', { title: c.link || '' }, c.name)), el('th')));
      rows.forEach((row, ri) => t.append(el('tr', {}, ...cols.map(c => el('td', {}, input(ri, c))),
        el('td', {}, el('button', { title: 'Remove this row', onclick: () => { rows.splice(ri, 1); record.rows = rows; drawForm(); } }, '✕')))));
      body.append(t, el('button', { onclick: () => { const n = rows[rows.length - 1].slice(); rows.push(n); record.rows = rows; drawForm(); } }, '+ Row'));
    }
    const used = r.used_by.length
      ? el('div', { class: 'rec-used' }, el('strong', {}, 'Used by '),
          ...r.used_by.slice(0, 40).map(u => el('button', { class: 'rec-link', onclick: () => openTable(u.table, u.key) },
            `${u.key} (${u.table.replace('.TXT', '')} ${u.column})`)))
      : null;
    const newKey = el('input', { placeholder: 'new key', size: 18 });
    const actions = el('div', { class: 'row rec-actions' },
      el('button', { onclick: () => save(rows) }, 'Save to mod'),
      el('button', { title: 'Back to the game\'s record (or remove a record the mod added)', onclick: revert }, 'Revert'),
      newKey, el('button', { onclick: () => copyAs(newKey.value) }, 'Copy as new'),
      r.table !== 'WEAPS.TXT' && r.state !== 'new'
        ? el('button', { class: 'danger', title: 'Remove the game record in this mod', onclick: del }, 'Delete') : null);
    form.replaceChildren(...[head, actions, body, used].filter(Boolean));
  }
  function needMod() {
    if (modName()) return true;
    say('Type a mod name in the header first', true);
    document.getElementById('mod')?.focus();
    return false;
  }
  async function save(rows) {
    if (!needMod()) return;
    try {
      const res = await call('record_set', { table, key, rows, mod: modName() });
      say('Saved: ' + res.written);
      await refreshList(); await openRecord(key);
    } catch (e) { say(e.message, true); }
  }
  async function revert() {
    if (!needMod()) return;
    try { await call('record_revert', { table, key, mod: modName() }); say('Reverted ' + key); await refreshList(); await openRecord(key); }
    catch (e) { say(e.message, true); }
  }
  async function del() {
    if (!needMod() || !confirm(`Delete ${key} in mod ${modName()}? Levels or scripts using it may break.`)) return;
    try { const r = await call('record_delete', { table, key, mod: modName() }); say(`Deleted ${key} (used by ${r.used_by})`); await refreshList(); }
    catch (e) { say(e.message, true); }
  }
  async function copyAs(newKey) {
    if (!needMod()) return;
    if (!/^[A-Za-z][A-Za-z0-9_\-]{1,40}$/.test(newKey || '')) { say('New key: letters, digits, _ and -', true); return; }
    try { const r = await call('record_new', { table, key: newKey, copy_from: key, mod: modName() }); say('Written: ' + r.written); await refreshList(); await openRecord(newKey); }
    catch (e) { say(e.message, true); }
  }

  // ---- texts ----
  async function loadTexts() {
    try {
      const rows = await call('strings_list', { mod: modName(), search: tSearch.value, limit: 200 });
      tList.replaceChildren(...rows.map(r => {
        const inp = el('input', { value: r.text, class: 'grow' });
        return el('div', { class: 'row rec-text ' + r.state },
          el('code', { title: r.hash }, r.key),
          el('small', { class: 'rec-src', title: r.source === 'catalog' ? 'Shared game text' : 'Text of mission ' + r.source }, r.source),
          r.state !== 'game' ? el('em', {}, r.state) : null, inp,
          el('button', { onclick: () => setText(r.key, inp.value, r.mission) }, 'Set'),
          r.state !== 'game' ? el('button', { onclick: () => revertText(r.key, r.mission) }, 'Revert') : null,
          r.game && r.state === 'changed' ? el('small', { class: 'hint' }, 'game: ' + r.game) : null);
      }));
    } catch (e) { say(e.message, true); }
  }
  let tTimer = 0;
  tSearch.oninput = () => { clearTimeout(tTimer); tTimer = setTimeout(loadTexts, 250); };
  async function setText(k, text, mission = '') {
    if (!needMod()) return;
    try {
      const r = await call('string_set', { key: k.trim(), text, mission: mission.trim(), mod: modName() });
      say(`${r.key} set${r.mission ? ' in ' + r.mission : ''} (hash ${r.hash})`); loadTexts();
    }
    catch (e) { say(e.message, true); }
  }
  async function revertText(k, mission = '') {
    try { await call('string_revert', { key: k, mission, mod: modName() }); say(k + ' reverted'); loadTexts(); }
    catch (e) { say(e.message, true); }
  }

  // deep link: #records[/TABLE[/KEY]] or #texts[/search] opens the window there (links from docs or assistants)
  async function fromHash() {
    const m = decodeURIComponent(location.hash).match(/^#(records|texts)(?:\/([^/]+))?(?:\/(.+))?$/i);
    if (!m) return;
    if (!dlg.open) dlg.showModal();
    await loadTables();
    if (m[1].toLowerCase() === 'texts') { if (m[2]) tSearch.value = m[2]; tabTxt.onclick(); return; }
    if (m[2]) await openTable(m[2].toUpperCase().replace(/\.TXT$/, '') + '.TXT', m[3]);
  }
  window.addEventListener('hashchange', fromHash);
  fromHash();
})();
