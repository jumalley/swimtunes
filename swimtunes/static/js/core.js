// Shared basics: state, translations, formatting, tooltips, API calls, live progress cards, jobs.
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
  nameIcons(); nameFields();
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
// "H:\\" stays as is; a long folder path keeps only its end ("…/Music/SwimTunes")
const shortPath = p => { p = String(p || ''); if (p.length <= 26) return p; const parts = p.split(/[\\/]/).filter(Boolean); return '…' + (p.includes('\\') ? '\\' : '/') + parts.slice(-2).join(p.includes('\\') ? '\\' : '/'); };
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
  TIP.setAttribute('aria-hidden', 'false'); el.setAttribute('aria-describedby', 'tip');   // read aloud with the element
  TIP.style.left = '0px'; TIP.style.top = '0px'; TIP.classList.add('show');
  const r = el.getBoundingClientRect(), w = TIP.offsetWidth, h = TIP.offsetHeight;
  let x = Math.max(8, Math.min(window.innerWidth - w - 8, r.left + r.width / 2 - w / 2));
  let y = r.bottom + 8;
  if (y + h > window.innerHeight - 8) y = r.top - h - 8;
  TIP.style.left = x + 'px'; TIP.style.top = Math.max(8, y) + 'px';
}
function hideTip() { TIP.classList.remove('show'); TIP.setAttribute('aria-hidden', 'true'); if (tipFor) tipFor.removeAttribute('aria-describedby'); tipFor = null; }
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


// ---------- accessibility: every icon-only control gets a spoken name ----------
// Buttons and help icons that show only an icon are named after their tooltip, in the page's language.
function nameIcons(root = document) {
  root.querySelectorAll('button, [role="button"], .help, a').forEach(el => {
    if (el.getAttribute('aria-label') && !el.dataset.autoLabel) return;
    const visible = [...el.childNodes].some(n => (n.nodeType === 3 && n.textContent.trim()) ||
      (n.nodeType === 1 && n.tagName !== 'I' && n.textContent.trim() && getComputedStyle(n).display !== 'none'));
    if (visible) { if (el.dataset.autoLabel) { el.removeAttribute('aria-label'); delete el.dataset.autoLabel; } return; }
    const name = el.dataset.tip ? t(el.dataset.tip) : el.dataset.tipRaw || el.title || '';
    if (name) { el.setAttribute('aria-label', name.split('\n')[0]); el.dataset.autoLabel = '1'; }
    if (el.classList.contains('help')) el.setAttribute('role', 'img');
  });
}
// Form fields whose visible label sits next to them (not linked with for=…) get that label as their name.
function nameFields(root = document) {
  root.querySelectorAll('select, input:not([type=hidden]), textarea').forEach(el => {
    if (el.labels?.length || el.closest('label') || (el.getAttribute('aria-label') && !el.dataset.autoLabel)) return;
    const box = el.parentElement;
    const lab = box?.querySelector(':scope > label') || box?.parentElement?.querySelector(':scope > label');
    let name = lab ? lab.textContent.trim() : '';
    if (!name && box) name = [...box.childNodes].filter(n => n !== el && !(n.nodeType === 1 && n.matches('select,input,option'))).map(n => n.textContent).join(' ').trim();
    if (!name) name = el.dataset.tip ? t(el.dataset.tip) : el.placeholder || '';
    if (name) { el.setAttribute('aria-label', name.replace(/\s+/g, ' ')); el.dataset.autoLabel = '1'; }
  });
}
new MutationObserver(muts => { for (const m of muts) for (const n of m.addedNodes) if (n.nodeType === 1) { nameIcons(n.parentNode || n); nameFields(n.parentNode || n); } })
  .observe(document.documentElement, { childList: true, subtree: true });
