/* SwimTunes dashboard */
let LIB = null, CURRENT = null, SONGS = [], SELECTED = new Set(), RULES = [], UPDATING = false, LANG = 'en', STATUS = null;
const MODE_KEYS = ['one', 'rules', 'genre', 'style', 'tempo', 'decade', 'country', 'artist'];
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

// ---------- translations ----------
function has(key) { return (I18N[LANG] && key in I18N[LANG]) || key in I18N.en; }
function t(key, p = {}) {
  let s = (I18N[LANG] && I18N[LANG][key]) ?? I18N.en[key] ?? key;
  if (s.includes('|')) {
    const forms = s.split('|');
    let cat = 'other';
    try { cat = new Intl.PluralRules(LANG).select(Number(p.n ?? 0)); } catch (e) {}
    s = cat === 'one' ? forms[0] : forms[forms.length - 1];
  }
  return s.replace(/\{(\w+)\}/g, (_, k) => (p[k] ?? `{${k}}`));
}
function tEntry(e) {
  if (!e) return '';
  const txt = e.k && has(e.k) ? t(e.k, e.p || {}) : e.t;
  return e.error ? t('log.error', { error: txt }) : txt;
}
function detectLang() {
  for (const l of (navigator.languages || [navigator.language || 'en'])) {
    const c = String(l).slice(0, 2).toLowerCase();
    if (I18N[c]) return c;
  }
  return 'en';
}
function resolveLang(pref) { return pref && pref !== 'auto' && I18N[pref] ? pref : detectLang(); }
function applyI18n() {
  document.documentElement.lang = LANG;
  document.querySelectorAll('[data-i18n]').forEach(el => el.textContent = t(el.dataset.i18n));
  document.querySelectorAll('[data-i18n-html]').forEach(el => el.innerHTML = t(el.dataset.i18nHtml));
  document.querySelectorAll('[data-i18n-ph]').forEach(el => el.placeholder = t(el.dataset.i18nPh));
  const pref = LIB?.config.language || 'auto';
  document.querySelectorAll('.lang-select').forEach(sel => {
    sel.innerHTML = `<option value="auto">${esc(t('lang.auto', { name: LANG_NAMES[detectLang()] }))}</option>` +
      Object.entries(LANG_NAMES).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('');
    sel.value = pref;
  });
  fillModes($('pv-mode'), $('pv-mode').value || LIB?.config.sort_mode);
  fillModes($('org-mode'), $('org-mode').value || 'rules');
}
async function setLanguage(v) {
  await api('/api/config', { method: 'POST', body: { language: v } });
  LIB.config.language = v;
  LANG = resolveLang(v);
  applyI18n();
  await renderAll();
}

// ---------- formatting ----------
const nf = (x, d = 1) => new Intl.NumberFormat(LANG, { minimumFractionDigits: d, maximumFractionDigits: d }).format(x);
const dur = s => { s = Math.round(s || 0); const h = Math.floor(s/3600), m = Math.floor(s%3600/60), x = s%60;
  return h ? `${h}:${String(m).padStart(2,'0')}:${String(x).padStart(2,'0')}` : `${m}:${String(x).padStart(2,'0')}`; };
const longDur = s => { s = Math.round(s || 0); const h = Math.floor(s/3600), m = Math.round(s%3600/60); return h ? t('dur.hm', { h, m }) : t('dur.m', { m }); };
const size = b => b >= 1e9 ? `${nf(b/1e9, 2)} ${t('unit.gb')}` : `${nf(b/1e6, 1)} ${t('unit.mb')}`;
function countryName(code) {
  if (!code) return '';
  try { return new Intl.DisplayNames([LANG], { type: 'region' }).of(code) || code; } catch (e) { return code; }
}
function folderLabels(metas = []) {
  const codes = [...new Set(metas.filter(m => m && m.country).map(m => m.country))];
  return { other: t('fl.other'), unknown_tempo: t('fl.unknown_tempo'), easy: t('fl.easy'), steady: t('fl.steady'),
    fast: t('fl.fast'), sprint: t('fl.sprint'), unknown_year: t('fl.unknown_year'), unknown_artist: t('fl.unknown_artist'),
    russian_language: t('fl.russian_language'), japanese_language: t('fl.japanese_language'), downloads: t('fl.downloads'),
    countries: Object.fromEntries(codes.map(c => [c, countryName(c)])) };
}

// ---------- tooltips ----------
const TIP = $('tip');
let tipFor = null;
function showTip(el) {
  const text = el.dataset.tipRaw ?? (el.dataset.tip ? t(el.dataset.tip) : '');
  if (!text) return;
  const host = el.closest('dialog') || document.body;  // a dialog sits above everything else
  if (TIP.parentNode !== host) host.appendChild(TIP);
  TIP.textContent = text; tipFor = el;
  TIP.style.left = '0px'; TIP.style.top = '0px'; TIP.classList.add('show');
  const r = el.getBoundingClientRect(), w = TIP.offsetWidth, h = TIP.offsetHeight;
  let x = Math.max(8, Math.min(window.innerWidth - w - 8, r.left + r.width / 2 - w / 2));
  let y = r.bottom + 8;
  if (y + h > window.innerHeight - 8) y = r.top - h - 8;
  TIP.style.left = x + 'px'; TIP.style.top = Math.max(8, y) + 'px';
}
function hideTip() { TIP.classList.remove('show'); tipFor = null; }
document.addEventListener('mouseover', e => { const el = e.target.closest('[data-tip],[data-tip-raw]'); if (el && el !== tipFor) showTip(el); else if (!el && tipFor) hideTip(); });
document.addEventListener('focusin', e => { const el = e.target.closest('[data-tip],[data-tip-raw]'); if (el) showTip(el); });
document.addEventListener('focusout', hideTip);
document.addEventListener('click', e => { if (e.target.closest('.help')) { const el = e.target.closest('.help'); tipFor === el ? hideTip() : showTip(el); e.preventDefault(); } });
window.addEventListener('scroll', hideTip, true);

// ---------- basics ----------
function toast(msg) { const el = $('toast'); el.textContent = msg; el.classList.add('show'); clearTimeout(el._h); el._h = setTimeout(() => el.classList.remove('show'), 3500); }
function showClosed(title, text) { $('ov-title').textContent = title; $('ov-text').textContent = text; $('overlay').classList.remove('hidden'); }
async function api(path, opts = {}) {
  let r;
  try { r = await fetch(path, { headers: {'Content-Type': 'application/json'}, ...opts, body: opts.body ? JSON.stringify(opts.body) : undefined }); }
  catch (e) { if (!UPDATING) showClosed(t('ov.stopped_t'), t('ov.closed')); throw e; }
  const data = await r.json();
  if (!r.ok) { const msg = tEntry({ t: data.error, k: data.key, p: data.params }); toast(msg || t('common.error')); throw new Error(msg); }
  return data;
}
const ICON = (cls, extra = '') => `<i class="${cls}"${extra}></i>`;
const SPIN = ICON('fa-solid fa-circle-notch fa-spin');

// ---------- metadata cells ----------
function metaCells(m, withYear = true, state = null) {
  if (state === 'waiting') return `<td colspan="${withYear ? 5 : 4}"><span class="state">${ICON('fa-regular fa-clock')}${t('st.lookup_waiting')}</span></td>`;
  if (state === 'looking') return `<td colspan="${withYear ? 5 : 4}"><span class="state busy">${SPIN}${t('st.looking')}</span></td>`;
  if (state === 'reading') return `<td colspan="${withYear ? 5 : 4}"><span class="state busy">${ICON('fa-brands fa-youtube fa-fade')}${t('st.reading')}</span></td>`;
  if (!m && state === 'have') return `<td colspan="${withYear ? 5 : 4}" class="muted">–</td>`;
  if (!m) {
    const why = state === 'error' ? t('st.lookup_failed') : state === 'skipped' ? t('st.lookup_skipped') : t('st.no_info');
    return `<td colspan="${withYear ? 5 : 4}"><span class="state">${ICON('fa-regular fa-circle-question')}${why}</span></td>`;
  }
  const tags = (m.tags || []).slice(0, 2).map(x => `<span class="chip">${esc(x)}</span>`).join('');
  const bpm = m.bpm ? `${m.bpm_measured ? '~' : ''}${m.bpm}` : `<span class="muted" data-tip="tip.tempo_unknown">?</span>`;
  const country = countryName(m.country) || (m.cyrillic ? t('fl.russian_language') : m.japanese ? t('fl.japanese_language') : '');
  const mark = m.guessed ? ` <i class="fa-solid fa-wand-magic-sparkles muted small" data-tip="tip.guessed"></i>`
    : m.maybe_cover ? ` <i class="fa-regular fa-circle-question muted small" data-tip="tip.maybe_cover"></i>` : '';
  return `<td>${esc(m.genre || '–')}${mark}</td><td>${tags}</td><td class="num">${bpm}</td>${withYear ? `<td class="num">${m.year || ''}</td>` : ''}<td>${esc(country)}</td>`;
}
function metaChips(m) {
  if (!m) return `<span class="muted">${t('st.no_info')}</span>`;
  const out = [];
  if (m.genre) out.push(`<span class="chip info">${esc(m.genre)}</span>`);
  if (m.tags && m.tags[0] && m.tags[0].toLowerCase() !== (m.genre || '').toLowerCase()) out.push(`<span class="chip">${esc(m.tags[0])}</span>`);
  if (m.country) out.push(`<span class="chip">${ICON('fa-solid fa-earth-europe')}${esc(countryName(m.country))}</span>`);
  if (m.bpm) out.push(`<span class="chip">${ICON('fa-solid fa-gauge-high')}${m.bpm} BPM</span>`);
  if (m.year) out.push(`<span class="chip">${m.year}</span>`);
  return out.join('') || `<span class="muted">${t('st.no_info')}</span>`;
}

// ---------- live progress card ----------
const SPEED = {};
function eta(jobId, done, total, workers = 1) {
  // estimate from the last minute or so of progress, not from the start: the first songs
  // (and songs YouTube refuses) can be much slower than the rest
  if (!total || done >= total) return '';
  const now = Date.now();
  const h = SPEED[jobId] ||= [];
  if (!h.length || h[h.length - 1][1] !== done) h.push([now, done]);
  while (h.length > 2 && now - h[0][0] > 90000) h.shift();
  if (h.length < 2 || h[h.length - 1][1] - h[0][1] < 1) return t('live.estimating');
  const [t0, d0] = h[0], [t1, d1] = h[h.length - 1];
  const perItem = (t1 - t0) / (d1 - d0);
  const sinceLast = now - t1;
  const left = Math.max(0, (total - done) * perItem - Math.min(sinceLast, perItem)) / 1000;
  if (left < 15) return t('live.eta_soon');
  if (left < 90) return t('live.eta_sec', { n: Math.round(left / 5) * 5 });
  return t('live.eta_min', { n: Math.round(left / 60) });
}
function liveCard({ jobId, icon, title, current, done, total, stop, stopTip, feed, sub }) {
  const pct = total ? Math.round(done / total * 100) : 0;
  return `<div class="live"><div class="live-head">
      <span style="font-size:20px;color:var(--accent)">${icon || SPIN}</span>
      <div class="what"><small>${esc(title)}</small><b>${esc(current || '…')}</b>${sub ? `<small>${esc(sub)}</small>` : ''}</div>
      ${stop ? `<button class="small" onclick="${stop}" data-tip="${stopTip}">${ICON('fa-solid fa-stop')}${t('live.stop')}</button>` : ''}
    </div>
    ${total ? `<div class="bar"><i style="width:${pct}%"></i></div><div class="meta"><span>${t('live.n_of', { done, n: total })}</span><span>${eta(jobId, done, total)}</span></div>` : ''}
    ${feed ? `<ul class="feed">${feed}</ul>` : ''}</div>`;
}
const FEED_TOP = {};
function markNew(ctx, html, key) {
  // only a result that just arrived fades in (the feed is redrawn every poll)
  if (!html) return html;
  const fresh = FEED_TOP[ctx] !== key;
  if (fresh) { FEED_TOP[ctx] = key; FEED_TOP[ctx + ':t'] = Date.now(); }
  return Date.now() - (FEED_TOP[ctx + ':t'] || 0) < 450 ? html.replace('<li>', '<li class="new">') : html;
}
function coverImg(meta, videoId, size = 36, path = null) {
  const src = path ? `/api/cover?path=${encodeURIComponent(path)}` : meta?.cover || (videoId ? `https://i.ytimg.com/vi/${videoId}/mqdefault.jpg` : '');
  return src ? `<img class="cover" src="${esc(src)}" width="${size}" height="${size}" loading="lazy" alt="" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'cover none',innerHTML:'<i class=\\'fa-solid fa-music\\'></i>'}))">`
    : `<span class="cover none" style="width:${size}px;height:${size}px"><i class="fa-solid fa-music"></i></span>`;
}
function feedLine(song, meta, state, videoId) {
  if (state === 'unavailable') return `<li>${ICON('fa-solid fa-ban', ' style="color:var(--danger)"')}<span class="song">${esc(song)}</span><i class="fa-solid fa-arrow-right arrow"></i><span class="chip bad">${t('st.unavailable')}</span></li>`;
  const icon = state === 'error' ? ICON('fa-solid fa-triangle-exclamation', ' style="color:var(--warn)"')
    : meta && (meta.genre || (meta.tags || []).length) ? ICON('fa-solid fa-circle-check', ' style="color:var(--ok)"')
    : ICON('fa-regular fa-circle-question', ' style="color:var(--muted)"');
  return `<li>${icon}${coverImg(meta, videoId, 24)}<span class="song">${esc(song)}</span><i class="fa-solid fa-arrow-right arrow"></i>${metaChips(meta)}</li>`;
}

// ---------- library ----------
async function loadLibrary() {
  LIB = await api('/api/library');
  $('s-count').textContent = LIB.count;
  $('s-dur').textContent = longDur(LIB.duration);
  $('s-folders').textContent = LIB.folders.length;
  const pct = Math.min(100, LIB.size / LIB.capacity * 100);
  $('s-cap').textContent = t('hd.capacity', { used: size(LIB.size), gb: LIB.config.capacity_gb, pct: Math.round(pct) });
  $('s-bar').firstElementChild.style.width = pct + '%';
  $('s-bar').classList.toggle('full', LIB.size > LIB.capacity * 0.95);
  const b = $('dupe-badge'); b.textContent = LIB.duplicates; b.classList.toggle('hidden', !LIB.duplicates);
  $('folder-names').innerHTML = LIB.folders.map(f => `<option value="${esc(f.name)}">`).join('');
  const all = `<li data-f="" class="${CURRENT === null ? 'active' : ''}">${ICON('fa-solid fa-layer-group')}<span>${t('side.all')}</span><small>${LIB.count} · ${dur(LIB.duration)}</small></li>`;
  $('folders').innerHTML = all + LIB.folders.map(f =>
    `<li data-f="${esc(f.name)}" class="${CURRENT === f.name ? 'active' : ''}" data-tip-raw="${esc(t('side.folder_tip', { n: f.count, time: longDur(f.duration), size: size(f.size) }))}">${ICON('fa-regular fa-folder')}<span>${esc(f.name)}</span><small>${f.count} · ${dur(f.duration)}</small></li>`).join('');
  $('folders').querySelectorAll('li').forEach(li => li.onclick = () => { openFolder(li.dataset.f || null); showTab('songs'); });
  for (const k of ['capacity_gb', 'bitrate', 'dupe_scope', 'normalize']) $('cfg-' + k).value = String(LIB.config[k]);
  $('cfg-root').value = LIB.root;
  RULES = LIB.config.rules || [];
  $('rules-count').textContent = RULES.length ? t('rules.count', { n: RULES.length }) : t('rules.none');
  const src = $('org-source').value;
  $('org-source').innerHTML = `<option value="">${t('side.all')}</option>` + LIB.folders.map(f => `<option value="${esc(f.name)}">${esc(f.name)}</option>`).join('');
  $('org-source').value = src;
  if (!$('dupe-scope').dataset.touched) $('dupe-scope').value = LIB.config.dupe_scope;
}
async function openFolder(name) {
  if (CURRENT !== name) { SELECTED.clear(); $('filter').value = ''; }
  CURRENT = name;
  SONGS = name === null ? await api('/api/tracks') : await api('/api/folders/' + encodeURIComponent(name));
  const f = LIB.folders.find(f => f.name === name);
  $('folder-title').textContent = name ?? t('side.all');
  const c = f || { count: LIB.count, duration: LIB.duration, size: LIB.size };
  $('folder-sub').textContent = `${t('common.n_songs', { n: c.count })} · ${longDur(c.duration)} · ${size(c.size)}`;
  $('btn-rename').classList.toggle('hidden', name === null);
  $('btn-delfolder').classList.toggle('hidden', name === null);
  $('th-folder').classList.toggle('hidden', name !== null);
  document.querySelectorAll('#folders li').forEach(li => li.classList.toggle('active', (li.dataset.f || null) === name));
  renderSongs();
}
function renderSongs() {
  const q = $('filter').value.toLowerCase();
  const rows = SONGS.filter(s => !q || (s.title + ' ' + s.artist + ' ' + (s.meta?.genre || '')).toLowerCase().includes(q));
  const opts = LIB.folders.map(f => `<option value="${esc(f.name)}">${esc(f.name)}</option>`).join('');
  $('songs').innerHTML = rows.length ? rows.map((s, i) => `<tr>
    <td><input type="checkbox" data-p="${esc(s.path)}" ${SELECTED.has(s.path) ? 'checked' : ''}></td>
    <td class="muted">${s.track_no ?? i + 1}</td>
    <td class="cv">${s.has_cover ? coverImg(null, null, 36, s.path) : coverImg(s.meta, null)}</td>
    <td class="t">${esc(s.title)}<small>${esc(s.artist)}</small></td>
    <td>${esc(s.meta?.genre || '')}</td><td class="num">${s.meta?.bpm ? (s.meta.bpm_measured ? '~' : '') + s.meta.bpm : ''}</td>
    <td class="${CURRENT === null ? '' : 'hidden'}">${esc(s.folder)}</td>
    <td class="num">${dur(s.duration)}</td><td class="num muted">${size(s.size)}</td>
    <td><select data-move="${esc(s.path)}"><option value="">—</option>${opts}</select></td></tr>`).join('')
    : `<tr><td colspan="10" class="empty">${SONGS.length ? t('common.no_matches') : t('songs.empty')}</td></tr>`;
  $('songs').querySelectorAll('input[data-p]').forEach(cb => cb.onchange = () => { cb.checked ? SELECTED.add(cb.dataset.p) : SELECTED.delete(cb.dataset.p); updateSel(); });
  $('songs').querySelectorAll('select[data-move]').forEach(sel => sel.onchange = async () => {
    if (!sel.value) return;
    await api('/api/tracks/move', { method: 'POST', body: { path: sel.dataset.move, folder: sel.value } });
    toast(t('songs.moved', { folder: sel.value })); await refresh();
  });
  updateSel();
}
function updateSel() { $('btn-delsel').disabled = !SELECTED.size; $('btn-delsel-text').textContent = SELECTED.size ? t('songs.delete_n', { n: SELECTED.size }) : t('songs.delete_selected'); }
function selectAll(on) { document.querySelectorAll('#songs input[data-p]').forEach(cb => { cb.checked = on; on ? SELECTED.add(cb.dataset.p) : SELECTED.delete(cb.dataset.p); }); updateSel(); }
async function refresh() { await loadLibrary(); if (CURRENT !== null && !LIB.folders.some(f => f.name === CURRENT)) CURRENT = null; await openFolder(CURRENT); }
async function createFolder() {
  const name = $('new-folder').value.trim(); if (!name) return;
  const r = await api('/api/folders', { method: 'POST', body: { name } });
  $('new-folder').value = ''; CURRENT = r.name; await refresh(); showTab('songs');
}
async function renameFolder() {
  const name = prompt(t('songs.rename_prompt', { name: CURRENT }), CURRENT); if (!name || name === CURRENT) return;
  const r = await api(`/api/folders/${encodeURIComponent(CURRENT)}/rename`, { method: 'POST', body: { name } });
  CURRENT = r.name; await refresh();
}
async function deleteFolder() {
  if (!confirm(t('songs.confirm_delete_folder', { name: CURRENT }))) return;
  await api('/api/folders/' + encodeURIComponent(CURRENT), { method: 'DELETE' });
  CURRENT = null; await refresh();
}
async function deleteSelected() {
  if (!confirm(t('songs.confirm_delete', { n: SELECTED.size }))) return;
  await api('/api/tracks/delete', { method: 'POST', body: { paths: [...SELECTED] } });
  SELECTED.clear(); await refresh();
}

// ---------- jobs ----------
async function followJob(id, { log, progress, btn, onUpdate } = {}) {
  if (btn) btn.disabled = true;
  const pr = progress ? $(progress) : null;
  if (pr) { pr.classList.remove('hidden'); pr.querySelector('i').style.width = '0%'; pr.querySelector('small').textContent = ''; }
  let j, fails = 0;
  while (true) {
    try { j = await api('/api/jobs/' + id); fails = 0; }
    catch (e) {  // a hiccup: try again a few times before giving up
      if (++fails > 5) { if (btn) btn.disabled = false; if (pr) pr.classList.add('hidden'); throw e; }
      await new Promise(r => setTimeout(r, 1500)); continue;
    }
    if (log) { const el = $(log); el.innerHTML = j.log.map(e => `<div class="${e.error ? 'err' : ''}">${esc(tEntry(e))}</div>`).join(''); el.scrollTop = el.scrollHeight; }
    if (pr && j.progress) { const [d, n] = j.progress; pr.querySelector('i').style.width = (n ? d / n * 100 : 0) + '%'; pr.querySelector('small').textContent = t('live.n_of', { done: d, n }); }
    if (onUpdate) onUpdate(j);
    if (j.status === 'done' || j.status === 'error') break;
    await new Promise(r => setTimeout(r, 700));
  }
  if (pr) pr.classList.add('hidden');
  if (btn) btn.disabled = false;
  if (j.status === 'error') toast(tEntry(j.log[j.log.length - 1]));
  return j;
}
async function cancelJob(id) { await api(`/api/jobs/${id}/cancel`, { method: 'POST' }); toast(t('live.stopping')); }

// ---------- add music: preview ----------
let PV = null, PV_JOB = null, DL_JOB = null;
function fillModes(sel, current) {
  sel.innerHTML = MODE_KEYS.map(k => `<option value="${k}">${esc(t('mode.' + k))}</option>`).join('');
  sel.value = MODE_KEYS.includes(current) ? current : 'one';
}
async function startPreview() {
  const urls = $('urls').value.trim();
  if (!urls) { toast(t('add.need_link')); return; }
  PV = null; $('dl-log').textContent = '';
  const r = await api('/api/preview', { method: 'POST', body: { urls, allow_dupes: $('allow-dupes').checked } });
  PV_JOB = r.id;
  $('how').classList.add('hidden');
  $('pv-panel').classList.remove('hidden');
  $('pv-controls').classList.add('hidden');
  $('pv-rows').innerHTML = '';
  $('pv-live').innerHTML = liveCard({ jobId: r.id, title: t('live.reading_link'), current: urls.split(/\s+/)[0] });
  const j = await followJob(r.id, { log: 'pv-log', btn: $('find-btn'), onUpdate: previewUpdate });
  PV_JOB = null;
  if (j.status !== 'done') { $('pv-live').innerHTML = ''; if (!PV) $('pv-panel').classList.add('hidden'); return; }
  previewUpdate(j);
  PV.loading = false;
  PV.name = j.result.name === 'Downloads' && !j.name ? t('fl.downloads') : j.result.name;
  $('pv-folder').value = PV.name;
  const looked = PV.items.filter(it => !it.existing);
  const found = looked.filter(it => it.meta && (it.meta.genre || (it.meta.tags || []).length)).length;
  const genres = {}; looked.forEach(it => { if (it.meta?.genre) genres[it.meta.genre] = (genres[it.meta.genre] || 0) + 1; });
  const top = Object.entries(genres).sort((a, b) => b[1] - a[1]).slice(0, 6).map(([g, n]) => `<span class="chip info">${esc(g)} · ${n}</span>`).join('');
  $('pv-live').innerHTML = `<div class="live done-card"><div class="live-head">${ICON('fa-solid fa-circle-check', ' style="font-size:20px;color:var(--ok)"')}
    <div class="what"><b>${t('live.pv_done', { n: PV.items.length, have: PV.items.length - looked.length })}</b>
    <small>${t('live.pv_found', { found, n: looked.length })}${PV.items.some(i => i.unavailable) ? ' · ' + t('live.pv_unavailable', { n: PV.items.filter(i => i.unavailable).length }) : ''}</small>
    <small>${t('live.pv_fix_hint')}</small></div></div>${top ? `<div style="margin-top:8px">${top}</div>` : ''}</div>`;
  $('pv-controls').classList.remove('hidden');
  fillModes($('pv-mode'), LIB.config.sort_mode);
  await applySort();
}
function previewUpdate(j) {
  if (!j.items) return;
  if (!PV) {
    PV = { name: j.name, items: j.items, loading: true };
    PV.items.forEach(it => { it.checked = !it.existing && !it.unavailable; it.manual = false; });
  } else {
    for (const s of j.items) {
      const it = PV.items[s.id];
      if (it.edited) continue;   // the user corrected this one by hand
      Object.assign(it, { state: s.state, meta: s.meta, artist: s.artist, title: s.title, duration: s.duration, unavailable: s.unavailable });
      if (s.unavailable) it.checked = false;
      if (s.existing && !it.existing) { it.existing = s.existing; it.checked = false; }   // turned out we have it
    }
  }
  if (j.status === 'running' && PV.loading) {
    const [done, total] = j.progress || [0, PV.items.filter(i => !i.existing).length];
    const rec = (j.recent || []).slice(-6).reverse();
    const feed = markNew('pv', rec.map(id => { const it = PV.items[id]; return feedLine(`${it.artist ? it.artist + ' – ' : ''}${it.title}`, it.meta, it.state, it.video_id); }).join(''), rec[0]);
    const cur = j.current_id != null ? PV.items[j.current_id] : null;
    $('pv-live').innerHTML = liveCard({ jobId: j.id, title: t('live.reading_song'), current: cur ? `${cur.artist ? cur.artist + ' – ' : ''}${cur.title}` : t('live.preparing'),
      done, total, stop: `cancelJob('${j.id}')`, stopTip: 'tip.stop_lookup', feed });
  }
  renderPreview(j.current_id);
}
async function applySort() {
  if (!PV || PV.loading) return;
  const mode = $('pv-mode').value;
  $('pv-folder-wrap').classList.toggle('hidden', !(mode === 'one' || mode === 'rules'));
  $('pv-folder-label').textContent = mode === 'rules' ? t('add.no_rule_to') : t('add.folder');
  $('pv-rules-btn').classList.toggle('hidden', mode !== 'rules');
  $('pv-mode-help').textContent = t('mh.' + mode) + (mode === 'rules' && !RULES.length ? ' ' + t('mh.no_rules') : '');
  const metas = PV.items.map(i => i.meta);
  const r = await api('/api/sort', { method: 'POST', body: { items: PV.items.map(i => ({ artist: i.artist, meta: i.meta })), mode, folder: $('pv-folder').value, remember: true, labels: folderLabels(metas) } });
  LIB.config.sort_mode = mode;
  PV.items.forEach((it, i) => {
    it.tempo_folder = mode === 'tempo' && !(it.meta && it.meta.bpm);
    if (!it.manual) it.folder = it.tempo_folder ? '' : r.folders[i];
  });
  renderPreview();
}
function dlState(d) {
  const S = {
    waiting: ['', ICON('fa-regular fa-clock'), t('st.waiting')],
    searching: ['busy', SPIN, t('st.searching')],
    downloading: ['busy', SPIN, `${d.retry ? t('st.retrying') : t('st.downloading')} ${d.pct ?? 0}%<span class="bar thin"><i style="width:${d.pct ?? 0}%"></i></span>`],
    converting: ['busy', ICON('fa-solid fa-gear fa-spin'), t('st.converting')],
    measuring: ['busy', ICON('fa-solid fa-wave-square fa-beat-fade'), t('st.measuring')],
    done: ['ok', ICON('fa-solid fa-circle-check'), t('st.done', { folder: (d.saved_to || '').split('/')[0] })],
    skipped: ['warn', ICON('fa-solid fa-forward'), t('st.skipped')],
    failed: ['bad', ICON('fa-solid fa-triangle-exclamation'), t('dlerr.' + (d.error_kind || 'other'))],
    saving: ['busy', ICON('fa-solid fa-floppy-disk'), t('st.saving')],
    stopped: ['', ICON('fa-solid fa-ban'), t('st.stopped')],
  }[d.status] || ['', '', d.status];
  const tip = d.status === 'failed' ? `${t('dlerr_help.' + (d.error_kind || 'other'))}\n\n${d.error || ''}` : d.status === 'skipped' ? t('st.already_in', { where: d.where || '' }) : '';
  return `<span class="state ${S[0]}"${tip ? ` data-tip-raw="${esc(tip)}"` : ''}>${S[1]}${S[2]}</span>`;
}
function renderPreview(nowId) {
  if (!PV) return;
  const q = $('pv-filter').value.toLowerCase(), showHave = $('pv-show-have').checked;
  const rows = PV.items.filter(it => (showHave || !it.existing || it.dl) && (!q || (it.title + ' ' + it.artist + ' ' + (it.meta?.genre || '') + ' ' + (it.folder || '')).toLowerCase().includes(q)));
  const anyDl = PV.items.some(it => it.dl);
  $('pv-last-col').textContent = anyDl ? t('col.status') : t('col.goes_to');
  const have = it => it.existing === 'listed twice' ? t('pv.listed_twice') : it.existing === 'just' ? t('pv.just') : t('pv.have', { where: it.existing });
  $('pv-rows').innerHTML = rows.length ? rows.map(it => `<tr class="${it.checked || it.dl ? '' : 'dim'} ${it.id === nowId || ['downloading', 'converting', 'measuring', 'searching'].includes(it.dl?.status) ? 'now' : ''}">
    <td><input type="checkbox" data-id="${it.id}" ${it.checked ? 'checked' : ''} ${anyDl && DL_JOB ? 'disabled' : ''}></td>
    <td class="cv">${coverImg(it.meta, it.video_id)}</td>
    ${songCell(it, have)}
    ${it.unavailable ? `<td colspan="5"><span class="chip bad" data-tip-raw="${esc(it.unavailable)}">${ICON('fa-solid fa-ban')}${t('st.unavailable')}</span></td>` : metaCells(it.meta, true, it.existing ? 'have' : it.state)}
    <td class="num">${dur(it.duration)}</td>
    <td>${it.dl ? dlState(it.dl)
      : PV.loading || it.unavailable ? '<span class="muted">–</span>'
      : it.tempo_folder && !it.manual ? `<span class="chip info" data-tip="tip.by_tempo">${ICON('fa-solid fa-gauge-high')}${t('add.by_tempo')}</span>`
      : `<input class="dest" data-dest="${it.id}" value="${esc(it.folder)}" list="folder-names">`}</td></tr>`).join('')
    : `<tr><td colspan="10" class="empty">${PV.items.length ? t('add.all_have') : t('add.none_found')}</td></tr>`;
  $('pv-rows').querySelectorAll('input[data-id]').forEach(cb => cb.onchange = () => { PV.items[cb.dataset.id].checked = cb.checked; renderPreview(); });
  $('pv-rows').querySelectorAll('[data-edit]').forEach(b => b.onclick = () => { EDITING = +b.dataset.edit; renderPreview(); $('ed-artist')?.focus(); });
  $('pv-rows').querySelectorAll('[data-ed-cancel]').forEach(b => b.onclick = () => { EDITING = null; renderPreview(); });
  $('pv-rows').querySelectorAll('[data-ed-save]').forEach(b => b.onclick = () => saveEdit(+b.dataset.edSave));
  $('pv-rows').querySelectorAll('.edit-song input').forEach(inp => inp.onkeydown = e => { if (e.key === 'Enter') saveEdit(EDITING); if (e.key === 'Escape') { EDITING = null; renderPreview(); } });
  $('pv-rows').querySelectorAll('input[data-dest]').forEach(inp => inp.onchange = () => { const it = PV.items[inp.dataset.dest]; it.folder = inp.value.trim(); it.manual = true; renderSummary(); });
  renderSummary();
}
let EDITING = null;
function songCell(it, have) {
  if (EDITING === it.id) return `<td class="t edit-song"><div class="row" style="gap:6px;flex-wrap:nowrap">
      <input id="ed-artist" value="${esc(it.artist)}" placeholder="${esc(t('pv.artist_ph'))}" style="min-width:110px">
      <input id="ed-title" value="${esc(it.title)}" placeholder="${esc(t('pv.title_ph'))}" style="min-width:140px">
      <button class="small primary" data-ed-save="${it.id}" data-tip="tip.save_lookup">${ICON('fa-solid fa-check')}</button>
      <button class="small" data-ed-cancel="1">${ICON('fa-solid fa-xmark')}</button></div></td>`;
  const canEdit = !it.existing && !it.dl && !PV.loading && !it.unavailable;
  return `<td class="t">${esc(it.title)}${canEdit ? ` <button class="small" style="padding:1px 6px;border:0" data-edit="${it.id}" data-tip="tip.edit_song">${ICON('fa-solid fa-pen muted')}</button>` : ''}
    <small>${esc(it.artist)}${it.existing && !it.dl ? ` · <span class="chip warn">${esc(have(it))}</span>` : ''}</small></td>`;
}
async function saveEdit(id) {
  const it = PV.items[id];
  const artist = $('ed-artist').value.trim(), title = $('ed-title').value.trim();
  if (!title) { toast(t('err.no_title')); return; }
  it.state = 'looking'; EDITING = null; renderPreview();
  try {
    const r = await api('/api/lookup', { method: 'POST', body: { artist, title, text: it.text || '' } });
    Object.assign(it, { artist, title, meta: r.meta, state: r.state, edited: true, reliable: true });
    toast(t('pv.updated', { song: `${artist ? artist + ' – ' : ''}${title}` }));
  } catch (e) { it.state = 'error'; }
  if (!it.manual) await applySort(); else renderPreview();
}
function summarize(items, key) {
  const groups = {};
  for (const it of items) { const k = key(it); (groups[k] ||= { n: 0, d: 0 }); groups[k].n++; groups[k].d += it.duration || 0; }
  return Object.entries(groups).sort((a, b) => b[1].n - a[1].n);
}
function renderSummary() {
  const chosen = PV.items.filter(it => it.checked);
  const byTempo = t('add.by_tempo');
  if (!PV.loading) {
    const groups = summarize(chosen, it => it.tempo_folder && !it.manual ? byTempo : (it.folder || '?'));
    const known = new Set(LIB.folders.map(f => f.name));
    $('pv-summary').innerHTML = groups.map(([name, g]) => `<div class="card"><b>${ICON(name === byTempo ? 'fa-solid fa-gauge-high' : 'fa-regular fa-folder')}${esc(name)} ${known.has(name) || name === byTempo ? '' : `<span class="chip info" data-tip="tip.new_folder_chip">${t('add.new')}</span>`}</b><small>${t('common.n_songs', { n: g.n })} · ${longDur(g.d)}</small></div>`).join('');
  }
  const total = chosen.reduce((a, it) => a + (it.duration || 0), 0);
  const mb = total * LIB.config.bitrate * 1000 / 8 / 1e6;
  const btn = $('dl-btn');
  if (PV.loading) { btn.disabled = true; $('dl-btn-text').textContent = t('add.wait_lookup'); $('dl-summary').textContent = ''; return; }
  if (DL_JOB) return;
  btn.disabled = !chosen.length;
  $('dl-btn-text').textContent = chosen.length ? t('add.download_n', { n: chosen.length }) : t('add.download');
  $('dl-summary').textContent = chosen.length ? t('add.dl_size', { time: longDur(total), mb: Math.round(mb) }) : t('add.tick_some');
}
function checkAll(on) { if (!PV) return; PV.items.forEach(it => { if (!it.existing || $('pv-show-have').checked) it.checked = on; }); renderPreview(); }
async function retryFailed() {
  PV.items.forEach(it => { if (it.dl && it.dl.status === 'failed') { it.checked = true; it.dl = null; } else if (it.dl) it.checked = false; });
  renderPreview();
  await startDownload(PV.items.filter(it => it.checked));
}
async function startDownload(only) {
  const chosen = only || PV.items.filter(it => it.checked && !it.unavailable);
  const items = chosen.map(it => ({ id: it.id, kind: it.kind, video_id: it.video_id, artist: it.artist, title: it.title,
    duration: it.duration, meta: it.meta, folder: it.folder, tempo_folder: it.tempo_folder && !it.manual }));
  const missing = items.find(it => !it.folder && !it.tempo_folder);
  if (missing) { toast(t('add.need_folder', { title: missing.title })); return; }
  const r = await api('/api/download', { method: 'POST', body: { items, allow_dupes: $('allow-dupes').checked, labels: folderLabels(items.map(i => i.meta)) } });
  DL_JOB = r.id;
  chosen.forEach(it => it.dl = { status: 'waiting' });
  $('dl-btn').disabled = true; $('dl-btn-text').textContent = t('add.downloading');
  const j = await followJob(r.id, { log: 'dl-log', onUpdate: j => {
    for (const s of j.items || []) if (PV.items[s.id]) PV.items[s.id].dl = s;
    const cur = (j.items || []).find(s => ['searching', 'downloading', 'converting', 'measuring', 'saving'].includes(s.status));
    const [done, n] = j.progress || [0, items.length];
    if (j.status === 'running') $('pv-live').innerHTML = liveCard({ jobId: j.id, title: cur ? t('live.dl_now', { i: done + 1, n }) : t('live.dl_prep'),
      current: cur ? `${cur.artist ? cur.artist + ' – ' : ''}${cur.title}` : '', done, total: n, stop: `cancelJob('${j.id}')`, stopTip: 'tip.stop_download',
      sub: (j.items || []).filter(s => ['searching', 'downloading', 'converting', 'measuring', 'saving'].includes(s.status)).length > 1 ? t('live.parallel', { n: (j.items || []).filter(s => ['searching', 'downloading', 'converting', 'measuring', 'saving'].includes(s.status)).length }) : '' });
    renderPreview();
    if (j.progress) loadLibrary();
  } });
  DL_JOB = null;
  if (j.status === 'done') {
    const res = j.result;
    PV.items.forEach(it => { if (it.dl && it.dl.status === 'done') { it.existing = 'just'; it.checked = false; } });
    $('pv-live').innerHTML = `<div class="live done-card"><div class="live-head">${ICON('fa-solid fa-circle-check', ' style="font-size:20px;color:var(--ok)"')}
      <div class="what"><b>${t('live.dl_done', { n: res.downloaded.length })}</b><small>${t('live.dl_done_detail', { skipped: res.skipped.length, failed: res.failed.length })}</small></div>
      ${res.failed.length ? `<button class="small" onclick="retryFailed()" data-tip="tip.retry">${ICON('fa-solid fa-rotate-right')}${t('live.retry_n', { n: res.failed.length })}</button>` : ''}
      <button class="small" onclick="showTab('songs')">${ICON('fa-solid fa-music')}${t('live.see_songs')}</button>
      <button class="small primary" onclick="showTab('device')">${ICON('fa-solid fa-headphones')}${t('dev.sync')}</button></div></div>`;
  }
  renderPreview();
  await refresh();
}

// ---------- organize ----------
let ORG = null;
function recentFeed(j) {
  const rec = (j.recent || []).slice(-6).reverse();
  return markNew(j.id, rec.map(r => feedLine(r.song, r.meta, r.meta ? 'found' : 'unknown')).join(''), (j.recent || []).length);
}
async function startAnalyze() {
  const r = await api('/api/organize/analyze', { method: 'POST', body: { folder: $('org-source').value || null } });
  $('org-result').classList.add('hidden');
  const j = await followJob(r.id, { log: 'org-log', btn: $('org-btn'), onUpdate: j => {
    $('org-live').innerHTML = j.status === 'running' ? liveCard({ jobId: j.id, title: t('live.reading_song'), current: j.current || t('live.preparing'),
      done: j.progress?.[0] || 0, total: j.progress?.[1] || 0, stop: `cancelJob('${j.id}')`, stopTip: 'tip.stop_lookup', feed: recentFeed(j) }) : '';
  } });
  $('org-live').innerHTML = '';
  if (j.status !== 'done') return;
  ORG = j.result.tracks;
  $('org-result').classList.remove('hidden');
  await applyOrgSort();
  await refresh();
}
async function applyOrgSort() {
  const mode = $('org-mode').value;
  $('org-folder-wrap').classList.toggle('hidden', mode !== 'rules');
  if (!ORG) return;
  const r = await api('/api/sort', { method: 'POST', body: { items: ORG.map(x => ({ artist: x.artist, meta: x.meta })), mode, folder: '__keep__', labels: folderLabels(ORG.map(x => x.meta)) } });
  const fallback = $('org-folder').value.trim();
  ORG.forEach((x, i) => { let dest = r.folders[i]; if (dest === '__keep__') dest = fallback || x.folder; x.dest = dest; x.checked = dest !== x.folder; });
  renderOrg();
}
function renderOrg() {
  $('org-rows').innerHTML = ORG.length ? ORG.map((x, i) => `<tr class="${x.checked ? '' : 'dim'}">
    <td><input type="checkbox" data-i="${i}" ${x.checked ? 'checked' : ''}></td>
    <td class="t">${esc(x.title)}<small>${esc(x.artist)}</small></td>
    ${metaCells(x.meta, false)}
    <td>${esc(x.folder)}</td>
    <td><input class="dest" data-od="${i}" value="${esc(x.dest)}" list="folder-names"></td></tr>`).join('')
    : `<tr><td colspan="8" class="empty">${t('org.empty')}</td></tr>`;
  $('org-rows').querySelectorAll('input[data-i]').forEach(cb => cb.onchange = () => { ORG[cb.dataset.i].checked = cb.checked; renderOrg(); });
  $('org-rows').querySelectorAll('input[data-od]').forEach(inp => inp.onchange = () => { const x = ORG[inp.dataset.od]; x.dest = inp.value.trim(); x.checked = !!x.dest && x.dest !== x.folder; renderOrg(); });
  const moving = ORG.filter(x => x.checked && x.dest && x.dest !== x.folder);
  $('org-summary').innerHTML = summarize(moving, x => x.dest).map(([name, g]) => `<div class="card"><b>${ICON('fa-solid fa-arrow-right')}${ICON('fa-regular fa-folder')}${esc(name)}</b><small>${t('common.n_songs', { n: g.n })} · ${longDur(g.d)}</small></div>`).join('') || `<span class="muted">${t('org.all_right')}</span>`;
  $('org-apply').disabled = !moving.length;
  $('org-apply-text').textContent = moving.length ? t('org.move_n', { n: moving.length }) : t('org.move');
}
async function applyMoves() {
  const moves = ORG.filter(x => x.checked && x.dest && x.dest !== x.folder).map(x => ({ path: x.path, folder: x.dest }));
  if (!confirm(t('org.confirm', { n: moves.length }))) return;
  await api('/api/tracks/move', { method: 'POST', body: { moves } });
  toast(t('org.moved', { n: moves.length }));
  ORG = null; $('org-result').classList.add('hidden');
  await refresh();
}

// ---------- rules editor ----------
let DRAFT = [];
function openRules() { DRAFT = JSON.parse(JSON.stringify(RULES)); if (!DRAFT.length) DRAFT.push({ folder: '', keywords: [] }); renderRules(); $('learn-live').innerHTML = ''; $('rules-dlg').showModal(); }
function closeRules() { hideTip(); $('rules-dlg').close(); }
function renderRules() {
  $('rules-list').innerHTML = DRAFT.map((r, i) => `<div class="rule">
    <input placeholder="${esc(t('rules.folder_ph'))}" value="${esc(r.folder)}" data-rf="${i}" list="folder-names">
    <input placeholder="${esc(t('rules.keywords_ph'))}" value="${esc((r.keywords || []).join(', '))}" data-rk="${i}" data-tip="tip.keywords">
    <button class="danger" data-rd="${i}" data-tip="tip.remove_rule">${ICON('fa-solid fa-xmark')}</button>
    ${r.covers ? `<small class="muted" style="grid-column:1/-1;margin-top:-4px">${ICON('fa-solid fa-wand-magic-sparkles')} ${esc(t('rules.learned', { covers: r.covers }))}</small>` : ''}</div>`).join('');
  $('rules-list').querySelectorAll('[data-rf]').forEach(el => el.oninput = () => DRAFT[el.dataset.rf].folder = el.value);
  $('rules-list').querySelectorAll('[data-rk]').forEach(el => el.oninput = () => DRAFT[el.dataset.rk].keywords = el.value.split(',').map(s => s.trim()).filter(Boolean));
  $('rules-list').querySelectorAll('[data-rd]').forEach(el => el.onclick = () => { DRAFT.splice(+el.dataset.rd, 1); renderRules(); });
}
function addRule() { DRAFT.push({ folder: '', keywords: [] }); renderRules(); }
async function learnRules() {
  const r = await api('/api/rules/suggest', { method: 'POST' });
  const j = await followJob(r.id, { btn: $('learn-btn'), onUpdate: j => {
    $('learn-live').innerHTML = j.status === 'running' ? liveCard({ jobId: j.id, title: t('live.reading_song'), current: j.current || t('live.preparing'),
      done: j.progress?.[0] || 0, total: j.progress?.[1] || 0, stop: `cancelJob('${j.id}')`, stopTip: 'tip.stop_lookup', feed: recentFeed(j) }) : '';
  } });
  $('learn-live').innerHTML = j.status === 'done' ? `<p class="muted small">${esc(tEntry(j.log[j.log.length - 1]))}</p>` : '';
  if (j.status !== 'done') return;
  for (const s of j.result.rules) {
    const ex = DRAFT.find(d => d.folder === s.folder);
    if (ex) { ex.keywords = [...new Set([...(ex.keywords || []), ...s.keywords])]; ex.covers = s.covers; } else DRAFT.push(s);
  }
  DRAFT = DRAFT.filter(d => d.folder || (d.keywords || []).length);
  renderRules();
}
async function saveRules() {
  const r = await api('/api/rules', { method: 'POST', body: { rules: DRAFT } });
  RULES = r.rules; LIB.config.rules = RULES;
  $('rules-count').textContent = RULES.length ? t('rules.count', { n: RULES.length }) : t('rules.none');
  closeRules(); toast(t('rules.saved'));
  if (PV && !PV.loading) applySort();
  if (ORG) applyOrgSort();
}

// ---------- earphones ----------
let DRIVE = null, DEV = null;
async function loadDrives() {
  const drives = await api('/api/drives');
  const saved = LIB?.config.device_path;
  if (!drives.some(d => d.path === DRIVE)) DRIVE = (drives.find(d => d.path === saved) || drives.find(d => d.likely) || (drives.length === 1 ? drives[0] : null))?.path ?? null;
  $('drives').innerHTML = drives.length ? drives.map(d => `<label class="drive ${d.path === DRIVE ? 'sel' : ''}">
      <input type="radio" name="drive" value="${esc(d.path)}" ${d.path === DRIVE ? 'checked' : ''}>${ICON(d.likely ? 'fa-solid fa-headphones' : 'fa-brands fa-usb')}
      <span><b>${esc(d.label)}</b><small class="muted">${esc(d.path)} · ${t('dev.used_of', { used: size(d.total - d.free), total: size(d.total) })}</small></span></label>`).join('')
    : `<div class="notice">${ICON('fa-solid fa-plug-circle-xmark')}<span>${t('dev.none')}</span></div>`;
  $('drives').querySelectorAll('input[name=drive]').forEach(r => r.onchange = () => { DRIVE = r.value; loadDrives(); });
  if (DRIVE) loadDevice(); else $('dev-info').classList.add('hidden');
}
function useManualPath() { const p = $('device-path').value.trim(); if (p) { DRIVE = p; loadDevice(); } }
const DEV_STATUS = { imported: ['ok', 'fa-solid fa-check'], same: ['ok', 'fa-solid fa-check'], different: ['warn', 'fa-solid fa-not-equal'], only_on_earphones: ['warn', 'fa-solid fa-headphones'], only_in_swimtunes: ['info', 'fa-solid fa-upload'] };
async function loadDevice() {
  DEV = await api('/api/device?path=' + encodeURIComponent(DRIVE));
  await loadLibrary();
  loadStatus();
  $('dev-info').classList.remove('hidden');
  $('dev-title').textContent = t('dev.whats_on', { gb: DEV.capacity_gb, free: size(DEV.free) });
  $('dev-rows').innerHTML = DEV.folders.length ? DEV.folders.map(f => {
    const [cls, icon] = DEV_STATUS[f.status];
    const extra = f.only_on_earphones && f.status !== 'only_on_earphones' ? `<span class="chip warn">${t('dev.n_only', { n: f.only_on_earphones })}</span>` : '';
    const missing = f.missing_on_earphones && f.status === 'different' ? `<span class="chip info">${t('dev.n_to_copy', { n: f.missing_on_earphones })}</span>` : '';
    return `<tr><td>${f.only_on_earphones ? `<input type="checkbox" data-imp="${esc(f.name)}" checked data-tip="tip.import_tick">` : ''}</td>
      <td>${f.name === '.' ? ICON('fa-solid fa-headphones') + ' ' + esc(t('dev.root')) : ICON('fa-regular fa-folder') + ' ' + esc(f.name)}</td><td class="num">${f.on_earphones}</td><td class="num">${f.in_swimtunes}</td>
      <td><span class="chip ${cls}" data-tip="tip.dst_${f.status}">${ICON(icon)}${t('dst.' + f.status)}</span> ${extra} ${missing}</td></tr>`;
  }).join('') : `<tr><td colspan="5" class="empty">${t('dev.empty')}</td></tr>`;
  $('import-btn').classList.toggle('hidden', !DEV.folders.some(f => f.only_on_earphones));
}
async function startImport() {
  const folders = [...document.querySelectorAll('[data-imp]:checked')].map(c => c.dataset.imp);
  if (!folders.length) { toast(t('dev.tick_import')); return; }
  const r = await api('/api/device/import', { method: 'POST', body: { device: DRIVE, folders, allow_dupes: $('imp-dupes').checked, root_name: t('dev.root_folder') } });
  const line = e => {
    const icon = e.status === 'imported' ? ICON('fa-solid fa-circle-check', ' style="color:var(--ok)"')
      : e.status === 'skipped' ? ICON('fa-solid fa-forward', ' style="color:var(--warn)"') : ICON('fa-solid fa-triangle-exclamation', ' style="color:var(--danger)"');
    const what = e.status === 'imported' ? t('imp.imported', { folder: e.folder }) : e.status === 'skipped' ? t('imp.skipped', { where: e.where }) : t('imp.failed', { error: e.error });
    return `<li>${icon}<span class="song">${esc(e.file)}</span><i class="fa-solid fa-arrow-right arrow"></i><span class="muted">${esc(what)}</span></li>`;
  };
  const j = await followJob(r.id, { log: 'dev-log', btn: $('import-btn'), onUpdate: j => {
    if (j.status !== 'running') return;
    $('dev-live').innerHTML = liveCard({ jobId: j.id, title: t('imp.title'), current: j.current || t('live.preparing'),
      done: j.progress?.[0] || 0, total: j.progress?.[1] || 0, feed: (j.recent || []).slice(-8).reverse().map(line).join('') });
  } });
  if (j.status === 'done') {
    const res = j.result, rec = j.recent || [];
    const skipped = rec.filter(e => e.status === 'skipped'), failed = rec.filter(e => e.status === 'failed');
    $('dev-live').innerHTML = `<div class="live done-card"><div class="live-head">${ICON('fa-solid fa-circle-check', ' style="font-size:20px;color:var(--ok)"')}
      <div class="what"><b>${t('imp.done', { n: res.imported })}</b><small>${t('imp.done_detail', { skipped: res.skipped, failed: res.failed })}</small>
      ${res.skipped && !res.imported ? `<small>${t('imp.all_skipped_hint')}</small>` : ''}</div></div>
      ${skipped.length || failed.length ? `<details style="margin-top:8px"><summary>${t('imp.show_details')}</summary><ul class="feed">${[...failed, ...skipped].map(line).join('')}</ul></details>` : ''}</div>`;
  } else $('dev-live').innerHTML = '';
  await refresh(); await loadDevice();
}
async function startSync() {
  if (!DRIVE) { toast(t('dev.pick_first')); return; }
  if ($('mirror').checked && !confirm(t('dev.confirm_mirror'))) return;
  const r = await api('/api/sync', { method: 'POST', body: { device: DRIVE, mirror: $('mirror').checked } });
  await followJob(r.id, { log: 'dev-log', progress: 'dev-progress', btn: $('sync-btn') });
  await loadDevice();
}

// ---------- duplicates ----------
let DUPES = [];
async function loadDupes() {
  DUPES = await api('/api/duplicates?scope=' + $('dupe-scope').value);
  $('dupes').innerHTML = DUPES.length ? DUPES.map((g, gi) => `<div class="group">
    <div class="group-title">${esc(g[0].artist ? g[0].artist + ' – ' : '')}${esc(g[0].title)} <span class="muted">(${t('dup.copies', { n: g.length })})</span></div>
    <table>${g.map((x, ti) => `<tr><td><label class="check" style="margin:0" data-tip="tip.keep"><input type="radio" name="keep-${gi}" value="${ti}" ${ti === 0 ? 'checked' : ''}> ${t('dup.keep')}</label></td>
      <td>${ICON('fa-regular fa-folder')} ${esc(x.folder)}</td><td class="t">${esc(x.filename)}</td><td class="num">${dur(x.duration)}</td></tr>`).join('')}</table>
    <button class="danger" style="margin-top:8px" onclick="resolveDupe(${gi})">${ICON('fa-regular fa-trash-can')}${t('dup.remove_others')}</button></div>`).join('')
    : `<div class="empty">${ICON('fa-solid fa-circle-check', ' style="color:var(--ok)"')} ${t('dup.none')}</div>`;
}
function extraCopies(gi) { const keep = +document.querySelector(`input[name="keep-${gi}"]:checked`).value; return DUPES[gi].filter((_, i) => i !== keep).map(x => x.path); }
async function resolveDupe(gi) { await api('/api/tracks/delete', { method: 'POST', body: { paths: extraCopies(gi) } }); await refresh(); loadDupes(); }
async function removeAllDupes() {
  const paths = DUPES.flatMap((_, gi) => extraCopies(gi)); if (!paths.length) return;
  if (!confirm(t('dup.confirm', { n: paths.length }))) return;
  await api('/api/tracks/delete', { method: 'POST', body: { paths } }); await refresh(); loadDupes();
}

// ---------- settings, updates, health ----------
async function saveSettings() {
  await api('/api/config', { method: 'POST', body: { capacity_gb: +$('cfg-capacity_gb').value, bitrate: +$('cfg-bitrate').value,
    dupe_scope: $('cfg-dupe_scope').value, normalize: $('cfg-normalize').value === 'true' } });
  toast(t('common.saved')); refresh();
}
async function emptyTrash() { if (!confirm(t('set.confirm_trash'))) return; const r = await api('/api/trash/empty', { method: 'POST' }); toast(t('set.trash_emptied', { n: r.deleted })); }
async function openMusicFolder() { await api('/api/open', { method: 'POST', body: {} }); }
async function makeShortcut() { const r = await api('/api/shortcut', { method: 'POST' }); toast(t('set.shortcut_done', { path: r.path })); }
async function loadHealth() {
  const h = await api('/api/health');
  $('ver-current').textContent = h.tag !== 'dev' ? h.tag : h.version;
  $('ver-kind').textContent = t('kind.' + h.kind);
  $('health').innerHTML = `<table>${h.tools.map((x, i) => `<tr><td>${x.ok ? ICON('fa-solid fa-circle-check', ' style="color:var(--ok)"') : ICON('fa-solid fa-circle-xmark', ' style="color:var(--danger)"')}</td><td>${esc(t('health.' + i))}</td><td class="muted small">${esc(x.detail)}</td></tr>`).join('')}</table>
    <p class="${h.tools.every(x => x.ok) ? 'ok-text' : 'bad-text'}">${h.tools.every(x => x.ok) ? t('health.ok') : t('health.bad')}</p>`;
}
function showUpdate(u) {
  $('update').classList.toggle('hidden', !u);
  if (u) $('update-changes').innerHTML = (u.changes || []).slice(0, 6).map(c => `<li>${esc(c)}</li>`).join('');
}
async function checkUpdate() {
  $('update-status').textContent = t('upd.checking'); $('check-btn').disabled = true;
  try {
    const u = await api('/api/update');
    $('check-btn').disabled = false;
    if (u.error) { $('update-status').textContent = t('upd.cant_check', { error: u.error }); return; }
    $('update-status').innerHTML = u.available ? `<b>${t('upd.available')}</b> <button class="primary small" onclick="applyUpdate()">${ICON('fa-solid fa-download')}${t('upd.now')}</button>` : `${ICON('fa-solid fa-circle-check', ' style="color:var(--ok)"')} ${t('upd.newest')}`;
    $('update-list').innerHTML = u.available ? (u.changes || []).map(c => `<li>${esc(c)}</li>`).join('') : '';
    if (u.note) $('update-status').innerHTML += `<br><span class="small">${esc(u.note)}</span>`;
    showUpdate(u.available ? u : null);
    loadStatus();
  } catch (e) { $('check-btn').disabled = false; }
}
async function applyUpdate() {
  if (!confirm(t('upd.confirm'))) return;
  const r = await api('/api/update/apply', { method: 'POST' });
  $('update-btn').disabled = true;
  const j = await followJob(r.id, { log: 'upd-log', onUpdate: j => { $('update-changes').innerHTML = `<li>${esc(tEntry(j.log[j.log.length - 1]) || t('upd.updating'))}</li>`; } });
  if (j.status !== 'done') { $('update-btn').disabled = false; showTab('settings'); return; }
  UPDATING = true;
  showClosed(t('ov.updating_t'), t('ov.updating'));
  const before = Date.now();
  await new Promise(r => setTimeout(r, 2500));
  while (Date.now() - before < 120000) {
    try { const v = await fetch('/api/version'); if (v.ok) { location.reload(); return; } } catch (e) {}
    await new Promise(r => setTimeout(r, 1500));
  }
  showClosed(t('ov.restarting_t'), t('ov.restarting'));
}
async function quitApp() {
  let r = await fetch('/api/quit', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}' });
  if (!r.ok) {
    if (!confirm(t('ov.quit_busy'))) return;
    await fetch('/api/quit', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{"force":true}' });
  }
  showClosed(t('ov.closed_t'), t('ov.closed'));
}
async function loadVersion() {
  const v = await api('/api/version');
  $('quit-btn').classList.toggle('hidden', !v.can_quit);
  showUpdate(v.update);
}

// ---------- status bar ----------
async function loadStatus() {
  try { STATUS = await api('/api/status'); } catch (e) { return; }
  const s = STATUS;
  const fh = $('f-health');
  fh.className = 'item ' + (s.ok ? 'ok' : 'bad');
  fh.querySelector('i').className = s.ok ? 'fa-solid fa-circle-check' : 'fa-solid fa-circle-exclamation';
  fh.querySelector('span').textContent = s.ok ? t('f.ok') : t('f.bad');
  const fv = $('f-version');
  fv.className = 'item' + (s.update ? ' accent' : '');
  fv.querySelector('i').className = s.update ? 'fa-solid fa-circle-arrow-up' : 'fa-solid fa-tag';
  fv.querySelector('span').textContent = s.update ? t('f.update', { v: s.update }) : `SwimTunes ${s.tag !== 'dev' ? s.tag : s.version}`;
  const fd = $('f-device');
  fd.querySelector('span').textContent = s.device ? t('f.device', { label: s.device.label, path: s.device.path, free: size(s.device.free) }) : t('f.no_device');
  fd.className = 'item' + (s.device ? ' ok' : '');
  $('f-folder').querySelector('span').textContent = s.library;
}
const JOB_TAB = { preview: 'add', download: 'add', analyze: 'organize', suggest: 'organize', import: 'device', sync: 'device', update: 'settings' };
async function pollActivity() {
  let jobs = [];
  try { const r = await fetch('/api/jobs/active'); if (r.ok) jobs = await r.json(); } catch (e) { return; }
  const a = $('f-activity');
  if (!jobs.length) { a.classList.add('hidden'); return; }
  const j = jobs[0];
  const prog = j.progress ? ` ${j.progress[0]}/${j.progress[1]}` : '';
  a.querySelector('span').textContent = `${t('job.' + j.kind)}${prog}${j.current ? ' · ' + j.current : ''}`;
  a.onclick = () => showTab(JOB_TAB[j.kind] || 'add');
  a.dataset.tipRaw = j.last ? tEntry(j.last) : '';
  a.classList.remove('hidden');
}

// ---------- tabs & start ----------
function showTab(name) {
  hideTip();
  document.querySelectorAll('#tabs button').forEach(x => x.classList.toggle('on', x.dataset.tab === name));
  document.querySelectorAll('main > section').forEach(s => s.classList.toggle('hidden', s.id !== 'tab-' + name));
  if (name === 'dupes') loadDupes();
  if (name === 'device') loadDrives();
  if (name === 'settings') loadHealth();
  try { localStorage.setItem('swimtunes-tab', name); } catch (e) {}
}
async function renderAll() {
  await refresh();
  loadStatus();
  if (PV) { if (!PV.loading) await applySort(); else renderPreview(); }
  if (ORG) renderOrg();
  if (!$('tab-dupes').classList.contains('hidden')) loadDupes();
  if (!$('tab-device').classList.contains('hidden') && DRIVE) loadDevice();
  if (!$('tab-settings').classList.contains('hidden')) loadHealth();
}
document.querySelectorAll('#tabs button').forEach(b => b.onclick = () => showTab(b.dataset.tab));
$('dupe-scope').addEventListener('change', e => e.target.dataset.touched = 1);
$('new-folder').onkeydown = e => { if (e.key === 'Enter') createFolder(); };
$('device-path').onkeydown = e => { if (e.key === 'Enter') useManualPath(); };
setInterval(() => { if ($('overlay').classList.contains('hidden')) api('/api/ping', { method: 'POST' }).catch(() => {}); }, 30000);
setInterval(() => { if ($('overlay').classList.contains('hidden')) pollActivity(); }, 1500);
setInterval(() => { if ($('overlay').classList.contains('hidden')) loadStatus(); }, 20000);
(async () => {
  LIB = await api('/api/library');
  LANG = resolveLang(LIB.config.language);
  applyI18n();
  await refresh();
  let tab = null; try { tab = localStorage.getItem('swimtunes-tab'); } catch (e) {}
  showTab(tab && $('tab-' + tab) ? tab : (LIB.count ? 'songs' : 'add'));
  loadVersion(); loadStatus(); pollActivity();
})();
