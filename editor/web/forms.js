// Records tab of the Game data window: the game's data as records with ds1: IDs, changed field by field in the mod
// named in the header (editor/forms_cmds.py: forms_* / form_* commands -> Mods\<mod>\records\editor.lua, which
// DesertStormFix merges with every other mod's record edits field by field). Desert Storm only (it needs the records
// schema DesertStormFix installs).
(() => {
  const W = window.recordsWindow;
  if (!W) return;
  const { el, call, say, modName, needMod } = W;

  const types = el('div', { class: 'rec-tables' });
  const search = el('input', { placeholder: 'Search id or key', class: 'rec-search' });
  const list = el('div', { class: 'rec-list' });
  const form = el('div', { class: 'rec-form' }, el('p', { class: 'hint' },
    'Pick a record type and a record. Changes go to the mod in the header as field edits: other mods changing other ' +
    'fields of the same record still apply.'));
  const pane = el('div', { class: 'rec-body' }, types, el('div', { class: 'rec-mid' }, search, list), form);
  const tab = W.addTab('Records', pane, () => { if (!types.childElementCount) loadTypes(); }, true);

  let type = null, fields = null, rec = null;
  const plain = v => v === undefined || v === null ? '' : (typeof v === 'object' ? JSON.stringify(v) : String(v));
  // the same content whatever the key order (game and merged copies list fields in different orders)
  const canon = v => Array.isArray(v) ? '[' + v.map(canon).join(',') + ']'
    : v && typeof v === 'object' ? '{' + Object.keys(v).sort().map(k => JSON.stringify(k) + ':' + canon(v[k])).join(',') + '}'
    : JSON.stringify(v);

  async function loadTypes() {
    types.replaceChildren();
    try {
      for (const t of await call('forms_types')) {
        types.append(el('button', { class: 'rec-table', title: t.table, onclick: () => openType(t.type) },
          el('span', {}, t.type), el('small', {}, `${t.records} - ${t.table}`)));
      }
      types.append(el('button', { class: 'rec-table', onclick: showReport, title: 'What the switched-on mods’ record edits do together' },
        el('span', {}, 'Report'), el('small', {}, 'new / deleted records, conflicts')));
    } catch (e) {
      form.replaceChildren(el('p', { class: 'hint' }, 'Records need Desert Storm with DesertStormFix 3.3+ (its records ' +
        'schema): ' + e.message + '. The Tables tab edits the raw table rows.'));
    }
  }
  async function openType(t, thenId) {
    type = t;
    fields = await call('form_fields', { type: t });
    for (const b of types.children) b.classList.toggle('on', b.firstChild.textContent === t);
    await refreshList();
    if (thenId) openRecord(thenId);
  }
  async function refreshList() {
    if (!type) return;
    const rows = await call('forms_list', { type, search: search.value, limit: 500 });
    list.replaceChildren(...rows.map(r => el('button', { class: 'rec-item ' + r.state + (rec && r.id === rec.id ? ' on' : ''),
                                                         onclick: () => openRecord(r.id) },
      el('span', {}, r.id.split('/').pop()), r.state !== 'game' ? el('em', {}, r.state) : null,
      el('small', {}, Object.entries(r).filter(([k]) => !['id', 'state'].includes(k)).map(([k, v]) => `${k} ${plain(v)}`).join(' · ')))));
  }
  let timer = 0;
  search.oninput = () => { clearTimeout(timer); timer = setTimeout(refreshList, 200); };

  // ---- one record ----
  async function openRecord(id) {
    const t = id.split(':')[1]?.split('/')[0];
    if (t && t !== type) { await openType(t, id); return; }
    try { rec = await call('form_get', { id, mod: modName() }); }
    catch (e) { say(e.message, true); return; }
    for (const b of list.children) b.classList.toggle('on', id.endsWith('/' + b.firstChild.textContent));
    draw();
  }
  function parse(f, text) {   // input text -> value of the field's kind (undefined = bad)
    const k = f.kind;
    if (k === 'int') return /^-?\d+$/.test(text.trim()) ? parseInt(text, 10) : undefined;
    if (/^fixed\d$/.test(k)) return /^-?\d+(\.\d*)?$/.test(text.trim()) ? parseFloat(text) : undefined;
    if (f.max_len && text.length > f.max_len && !text.startsWith('ds1:')) return undefined;
    return text;
  }
  function draw() {
    const r = rec, game = r.game || {}, merged = r.merged || {}, mine = r.mod_edit || {};
    const state = !r.game ? 'new' : (canon(game) === canon(merged) ? 'game' : 'changed');
    const changes = {};   // field -> new value (null = take this mod's change out)
    const head = el('div', { class: 'rec-head' }, el('strong', {}, r.id), el('em', {}, state),
      mine.delete ? el('em', { class: 'bad' }, 'deleted in this mod') : null,
      el('span', { class: 'hint' }, fields.file));
    const conflicts = r.conflicts.length
      ? el('div', { class: 'rec-used' }, el('strong', {}, 'Conflicts '), ...r.conflicts.map(c => el('div', { class: 'bad' }, c)))
      : null;
    const body = el('div', { class: fields.rows_per_key ? 'rec-rows' : 'rec-fields' });
    const fieldRow = f => {
      const has = Object.prototype.hasOwnProperty.call(merged, f.name);
      const v = merged[f.name], g = game[f.name];
      const isMine = Object.prototype.hasOwnProperty.call(mine, f.name);
      const byOthers = !isMine && plain(v) !== plain(g);
      let input;
      if (f.kind === 'yesno') {
        input = el('input', { type: 'checkbox' });
        input.checked = !!v;
        input.onchange = () => { changes[f.name] = input.checked; };
      } else {
        input = el('input', { value: plain(v), placeholder: has ? '' : (f.none !== null && f.none !== undefined ? 'none' : ''),
                              class: f.kind === 'text' || f.kind === 'ref' ? '' : 'num',
                              ...(f.max_len ? { maxlength: f.kind === 'ref' ? 200 : f.max_len } : {}) });
        input.oninput = () => {
          const p = parse(f, input.value);
          input.classList.toggle('bad', p === undefined);
          if (p !== undefined && input.value !== plain(v)) changes[f.name] = p; else delete changes[f.name];
        };
      }
      const link = f.kind === 'ref' && typeof v === 'string' && v.startsWith('ds1:')
        ? el('button', { class: 'rec-link', title: 'Open ' + v, onclick: () => openRecord(v) }, '→') : null;
      const undo = isMine ? el('button', { class: 'rec-link', title: 'Take this mod’s change out (back to the game’s / other mods’ value)',
                                         onclick: () => { changes[f.name] = null; save(changes); } }, '↺') : null;
      const tip = [f.note, f.kind, f.max_len ? `at most ${f.max_len} characters` : '', f.ref.length ? 'links to ' + f.ref.join(' / ') : '']
        .filter(Boolean).join(' - ');
      return el('label', { class: 'rec-field' + (isMine ? ' mine' : '') + (byOthers ? ' others' : '') + (f.name.startsWith('col') ? ' unnamed' : ''),
                           title: tip },
        el('span', {}, f.name), input,
        el('small', {}, isMine ? 'this mod' : byOthers ? 'other mod' : ''),
        el('span', {}, link, undo, plain(v) !== plain(g) && r.game ? el('small', { class: 'hint' }, 'game: ' + (plain(g) || 'none')) : null));
    };
    if (fields.rows_per_key) {
      const cols = fields.row_columns.filter(c => c.name !== fields.key_column);
      const rows = (merged.rows || []).map(({ _line, ...x }) => x);   // (_line: the game's file order, not data)
      const grid = el('table', { class: 'rec-grid' }, el('tr', {}, ...cols.map(c => el('th', { title: c.note || c.kind }, c.name)), el('th')));
      const redraw = () => {
        grid.replaceChildren(grid.firstChild);
        rows.forEach((row, ri) => grid.append(el('tr', {}, ...cols.map(c => {
          const inp = el('input', { value: plain(row[c.name]), class: c.kind === 'int' || /^fixed/.test(c.kind) ? 'num' : (c.kind === 'ref' ? 'ref' : '') });
          inp.oninput = () => { const p = parse(c, inp.value); inp.classList.toggle('bad', p === undefined);
                                if (p !== undefined) { row[c.name] = p; changes.rows = rows; } };
          return el('td', {}, inp);
        }), el('td', {}, el('button', { title: 'Remove this row', onclick: () => { rows.splice(ri, 1); changes.rows = rows; redraw(); } }, '✕')))));
      };
      redraw();
      body.append(el('p', { class: 'hint' }, 'The rows are one field: saving writes the whole list (another mod’s ' +
        'change of these rows and yours can’t both apply).'), grid,
        el('button', { onclick: () => { const n = { ...(rows[rows.length - 1] || {}) }; delete n._line; rows.push(n); changes.rows = rows; redraw(); } }, '+ Row'));
    } else {
      for (const f of fields.fields) body.append(fieldRow(f));
    }
    const newId = el('input', { placeholder: `ds1:${type}/my_new_one`, size: 28 });
    const actions = el('div', { class: 'row rec-actions' },
      el('button', { onclick: () => save(changes) }, 'Save to mod'),
      el('button', { title: 'Take this mod’s whole edit of the record out', onclick: revert }, 'Revert'),
      newId, el('button', { title: 'A new record: a copy of this one with your changes', onclick: () => copyAs(newId.value, changes) }, 'Copy as new'),
      r.game ? el('button', { class: 'danger', title: 'Delete the game record in this mod', onclick: del }, 'Delete') : null);
    form.replaceChildren(...[head, conflicts, actions, body].filter(Boolean));
  }

  async function save(changes) {
    if (!needMod()) return;
    if (form.querySelector('input.bad')) { say('A field has a value its kind can’t take (red)', true); return; }
    if (!Object.keys(changes).length) { say('Nothing changed'); return; }
    try {
      const res = await call('form_set', { id: rec.id, fields: changes, mod: modName() });
      say('Saved: ' + res.written);
      await refreshList(); await openRecord(rec.id);
    } catch (e) { say(e.message, true); }
  }
  async function revert() {
    if (!needMod()) return;
    try { await call('form_revert', { id: rec.id, mod: modName() }); say('Reverted ' + rec.id); await refreshList(); await openRecord(rec.id); }
    catch (e) { say(e.message, true); }
  }
  async function del() {
    if (!needMod() || !confirm(`Delete ${rec.id} in mod ${modName()}? Levels or scripts that use it may break.`)) return;
    try { await call('form_delete', { id: rec.id, mod: modName() }); say('Deleted ' + rec.id + ' in ' + modName()); await refreshList(); await openRecord(rec.id); }
    catch (e) { say(e.message, true); }
  }
  async function copyAs(id, changes) {
    if (!needMod()) return;
    id = id.trim();
    if (!id.startsWith('ds1:') || !/^ds1:[a-z0-9_]+\/[A-Za-z0-9_\-.]{1,40}$/.test(id)) {
      if (/^[A-Za-z0-9_\-.]{1,40}$/.test(id)) id = `ds1:${type}/${id.toLowerCase()}`;
      else { say(`New id: ds1:${type}/<name> (letters, digits, _ - .)`, true); return; }
    }
    const fieldsOut = { ...changes };
    try {
      const r = await call('form_new', { id, based_on: rec.id, mod: modName(), fields: fieldsOut });
      say('Written: ' + r.written); await refreshList(); await openRecord(id);
    } catch (e) { say(e.message, true); }
  }
  async function showReport() {
    for (const b of types.children) b.classList.toggle('on', b.firstChild.textContent === 'Report');
    try {
      const lines = await call('forms_report');
      form.replaceChildren(el('div', { class: 'rec-head' }, el('strong', {}, 'Record edits of the switched-on mods')),
        lines.length ? el('div', { class: 'rec-used' }, ...lines.map(l =>
          el('div', { class: l.startsWith('[fail]') || l.startsWith('conflict') ? 'bad' : '' }, l)))
          : el('p', { class: 'hint' }, 'No new or deleted records, conflicts or problems in the switched-on mods’ record edits (plain field changes aren’t listed here).'));
    } catch (e) { say(e.message, true); }
  }

  // deep link: #forms/<record id> (e.g. #forms/ds1:weapon/us_wpn_m16a2)
  async function fromHash() {
    const m = decodeURIComponent(location.hash).match(/^#forms(?:\/(.+))?$/i);
    if (!m) return;
    if (!W.dlg.open) W.dlg.showModal();
    W.showTab(tab);
    if (m[1]) openRecord(m[1]);
  }
  window.addEventListener('hashchange', fromHash);
  fromHash();
})();
