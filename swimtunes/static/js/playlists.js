// Songs tab: the library, playlist cards, swims, playlist pictures.
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
  const all = `<li data-f="" class="${CURRENT === null ? 'active' : ''}">${ICON('fa-solid fa-layer-group')}<span>${t('side.all_playlists')}</span><small>${LIB.count} · ${dur(LIB.duration)}</small></li>`;
  $('folders').innerHTML = all + LIB.folders.map(f =>
    `<li data-f="${esc(f.name)}" class="${CURRENT === f.name ? 'active' : ''}" data-tip-raw="${esc(t('side.folder_tip', { n: f.count, time: longDur(f.duration), size: size(f.size) }))}">${ICON('fa-regular fa-folder')}<span>${esc(f.name)}</span><small>${f.count} · ${dur(f.duration)}</small></li>`).join('');
  $('folders').querySelectorAll('li').forEach(li => li.onclick = () => { if (!li.dataset.f) SHOW_ALL = false; openFolder(li.dataset.f || null); showTab('songs'); });
  $('swim-min').innerHTML = [...new Set([...SWIM_CHOICES, swimMin()])].sort((a, b) => a - b).map(m => `<option value="${m}">${esc(t('dur.m', { m }))}</option>`).join('');
  $('swim-min').value = String(swimMin());
  renderBatteryFooter();
  for (const k of ['capacity_gb', 'bitrate', 'dupe_scope', 'normalize']) $('cfg-' + k).value = String(LIB.config[k]);
  $('cfg-root').value = LIB.root;
  $('cfg-auto_update').checked = LIB.config.auto_update !== false;
  $('cfg-channel').value = LIB.config.update_channel || 'stable';
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
  const cards = name === null && !SHOW_ALL;
  $('cards-view').classList.toggle('hidden', !cards);
  $('list-view').classList.toggle('hidden', cards);
  document.querySelectorAll('#folders li').forEach(li => li.classList.toggle('active', (li.dataset.f || null) === name));
  if (cards) { SONGS = []; renderCards(); loadEarphoneFolders(); return; }
  SONGS = name === null ? await api('/api/tracks') : await api('/api/folders/' + encodeURIComponent(name));
  const f = LIB.folders.find(f => f.name === name);
  $('folder-title').textContent = name ?? t('side.all');
  const c = f || { count: LIB.count, duration: LIB.duration, size: LIB.size };
  $('folder-sub').textContent = `${t('common.n_songs', { n: c.count })} · ${longDur(c.duration)} · ${size(c.size)}`;
  $('btn-rename').classList.toggle('hidden', name === null);
  $('btn-delfolder').classList.toggle('hidden', name === null);
  $('th-folder').classList.toggle('hidden', name !== null);
  renderHero();
  if (f) loadEarphoneFolders();
  renderSongs();
}

// ---------- playlists overview: cards, pictures, swims ----------
let SHOW_ALL = false, EAR = null, EAR_AT = 0;
const SWIM_CHOICES = [20, 30, 45, 60, 75, 90, 120];
function swimMin() { return +(LIB?.config.swim_minutes) || 45; }
function showCards() { SHOW_ALL = false; openFolder(null); }
function showAllSongs() { SHOW_ALL = true; openFolder(null); }
async function setSwimMinutes(v) {
  await api('/api/config', { method: 'POST', body: { swim_minutes: +v } });
  LIB.config.swim_minutes = +v;
  renderCards(); renderHero(); renderBatteryFooter(); if (SHOKZ) renderShokz();
}
function ago(ts) {
  if (!ts) return '';
  const s = (Date.now() / 1000) - ts, rtf = new Intl.RelativeTimeFormat(LANG, { numeric: 'auto' });
  if (s < 60) return rtf.format(0, 'second');
  for (const [u, n] of [['minute', 60], ['hour', 3600], ['day', 86400], ['week', 604800], ['month', 2629800], ['year', 31557600]].reverse())
    if (s >= n) return rtf.format(-Math.floor(s / n), u);
  return rtf.format(-Math.floor(s / 60), 'minute');
}
function swims(sec) { return (sec || 0) / (swimMin() * 60); }
function swimShort(sec) {
  const n = swims(sec);
  return n >= 1 ? t('swim.short', { n, x: nf(n, n < 10 ? 1 : 0) }) : t('swim.short_part', { pct: Math.round(n * 100) });
}
function swimText(sec) {
  const n = swims(sec), m = swimMin();
  if (!sec) return t('swim.none');
  return n >= 1 ? t('swim.long', { n, x: nf(n, n < 10 ? 1 : 0), m, time: longDur(sec) })
    : t('swim.part', { pct: Math.round(n * 100), m, time: longDur(sec) });
}
function swimBlocks(sec, max = 8) {
  const n = swims(sec), count = Math.max(1, Math.min(max, Math.ceil(n)));
  let html = '';
  for (let i = 0; i < count; i++) html += `<i><b style="width:${Math.round(Math.max(0, Math.min(1, n - i)) * 100)}%"></b></i>`;
  return `<span class="swims">${html}${n > max ? '<em>+</em>' : ''}</span>`;
}
function folderArt(f) {
  if (f.cover) return `<img class="art" src="/api/folders/${encodeURIComponent(f.name)}/picture?v=${f.cover}" alt="">`;
  const pics = f.pics || [];
  if (pics.length >= 4) return `<div class="art mosaic">${pics.map(p => `<img src="/api/cover?path=${encodeURIComponent(p)}" alt="" loading="lazy">`).join('')}</div>`;
  if (pics.length) return `<img class="art" src="/api/cover?path=${encodeURIComponent(pics[0])}" alt="" loading="lazy">`;
  return `<div class="art empty">${ICON('fa-solid fa-music')}</div>`;
}
function earChip(f) {
  const st = EAR?.path ? EAR.folders[f.name] : undefined, synced = LIB.config.synced?.[f.name];
  if (EAR?.path) {
    if (st === 'same') return `<span class="chip ok" data-tip="tip.ear_same">${ICON('fa-solid fa-check')}${t('ear.same')}</span>`;
    if (st === 'different') return `<span class="chip warn" data-tip="tip.ear_changed">${ICON('fa-solid fa-not-equal')}${t('ear.changed')}</span>`;
    return `<span class="chip info" data-tip="tip.ear_missing">${ICON('fa-solid fa-upload')}${t('ear.missing')}</span>`;
  }
  if (!synced) return `<span class="chip" data-tip="tip.ear_never">${ICON('fa-solid fa-headphones')}${t('ear.never')}</span>`;
  if (f.updated > synced + 2) return `<span class="chip warn" data-tip="tip.ear_newer">${ICON('fa-solid fa-clock-rotate-left')}${t('ear.newer', { when: ago(synced) })}</span>`;
  return `<span class="chip" data-tip="tip.ear_copied">${ICON('fa-solid fa-headphones')}${t('ear.copied', { when: ago(synced) })}</span>`;
}
async function loadEarphoneFolders(force = false) {
  if (!force && Date.now() - EAR_AT < 15000) return;
  EAR_AT = Date.now();
  try { EAR = await api('/api/earphones/folders'); } catch (e) { return; }
  if (CURRENT === null && !SHOW_ALL) renderCards(); else renderHero();
}
function renderCards() {
  if (!LIB) return;
  const total = LIB.folders.reduce((a, f) => a + f.duration, 0);
  $('cards-sub').textContent = LIB.folders.length ? t('cards.sub', { n: LIB.folders.length, time: longDur(total), swims: swimShort(total) }) : '';
  $('cards').innerHTML = LIB.folders.length ? LIB.folders.map(f => `<div class="card" data-f="${esc(f.name)}" tabindex="0">
      <div class="art-wrap">${folderArt(f)}<button class="pic-btn" data-pic="${esc(f.name)}" data-tip="tip.pic">${ICON('fa-regular fa-image')}<span>${t(f.cover ? 'pic.change' : 'pic.add')}</span></button></div>
      <div class="card-body"><b class="name">${esc(f.name)}</b>
        <small class="muted">${t('common.n_songs', { n: f.count })} · ${longDur(f.duration)} · ${size(f.size)}</small>
        <div class="swim-line" data-tip-raw="${esc(swimText(f.duration))}">${ICON('fa-solid fa-person-swimming')}${swimBlocks(f.duration)}<small>${esc(swimShort(f.duration))}</small></div>
        <div class="genres">${(f.genres || []).map(g => `<span class="chip">${esc(g)}</span>`).join('')}</div>
        <div class="card-foot"><small class="muted" data-tip-raw="${esc(t('cards.updated_tip', { date: new Date(f.updated * 1000).toLocaleString(LANG) }))}">${ICON('fa-regular fa-clock')} ${esc(t('cards.updated', { when: ago(f.updated) }))}</small>${earChip(f)}</div>
      </div></div>`).join('')
    : `<div class="empty">${t('songs.empty')}</div>`;
  $('cards').querySelectorAll('.card').forEach(c => {
    c.onclick = e => { if (e.target.closest('.pic-btn')) return; openFolder(c.dataset.f); };
    c.onkeydown = e => { if (e.key === 'Enter') openFolder(c.dataset.f); };
  });
  $('cards').querySelectorAll('.pic-btn').forEach(b => b.onclick = () => openPictures(b.dataset.pic));
  $('cards-dev').innerHTML = EAR?.path ? ICON('fa-solid fa-plug') + ' ' + esc(t('cards.ear_on', { path: shortPath(EAR.path) })) : ICON('fa-solid fa-plug-circle-xmark') + ' ' + esc(t('cards.ear_off'));
}
function renderHero() {
  const f = LIB?.folders.find(f => f.name === CURRENT);
  $('hero').classList.toggle('hidden', !f);
  if (!f) return;
  const synced = LIB.config.synced?.[f.name];
  $('hero').innerHTML = `<div class="art-wrap big">${folderArt(f)}<button class="pic-btn" onclick="openPictures(CURRENT)" data-tip="tip.pic">${ICON('fa-regular fa-image')}<span>${t(f.cover ? 'pic.change' : 'pic.add')}</span></button></div>
    <div class="hero-info">
      <div class="facts">
        <div><small>${t('hero.plays')}</small><b>${longDur(f.duration)}</b></div>
        <div data-tip="tip.swim_minutes"><small>${t('hero.swims', { m: swimMin() })}</small><b>${esc(swimShort(f.duration))}</b></div>
        <div data-tip-raw="${esc(new Date(f.updated * 1000).toLocaleString(LANG))}"><small>${t('hero.updated')}</small><b>${esc(ago(f.updated))}</b></div>
        <div ${synced ? `data-tip-raw="${esc(new Date(synced * 1000).toLocaleString(LANG))}"` : ''}><small>${t('hero.copied')}</small><b>${synced ? esc(ago(synced)) : '—'}</b></div>
      </div>
      <div class="swim-line big">${ICON('fa-solid fa-person-swimming')}${swimBlocks(f.duration, 12)}<small>${esc(swimText(f.duration))}</small></div>
      <div class="genres">${earChip(f)} ${(f.genres || []).map(g => `<span class="chip">${esc(g)}</span>`).join('')}${(f.artists || []).map(a => `<span class="chip info">${ICON('fa-solid fa-microphone')}${esc(a)}</span>`).join('')}</div>
    </div>`;
}

// ---------- folder pictures ----------
let PIC = null;
async function openPictures(name) {
  PIC = { name, seq: 0 };
  $('pic-title').textContent = t('pic.title', { name });
  $('pic-q').value = '';
  $('pic-remove').classList.toggle('hidden', !LIB.folders.find(f => f.name === name)?.cover);
  $('pic-dlg').showModal();
  loadPictures();
}
async function loadPictures(q = '') {
  const seq = ++PIC.seq;
  $('pic-body').innerHTML = `<div class="notice">${SPIN}<span>${t('pic.looking')}</span></div>`;
  let r;
  try { r = await api(`/api/folders/${encodeURIComponent(PIC.name)}/pictures` + (q ? '?q=' + encodeURIComponent(q) : '')); }
  catch (e) { $('pic-body').innerHTML = ''; return; }
  if (seq !== PIC.seq) return;
  $('pic-keywords').innerHTML = r.keywords.length ? `<small class="muted">${t('pic.keywords')}</small>` + r.keywords.map(k => `<button class="chip kwb" data-k="${esc(k)}">${esc(k)}</button>`).join('') : '';
  $('pic-keywords').querySelectorAll('.kwb').forEach(b => b.onclick = () => { $('pic-q').value = b.dataset.k; loadPictures(b.dataset.k); });
  const tile = (img, attrs, label) => `<button class="tile" ${attrs}><img src="${esc(img)}" alt="" loading="lazy">${label ? `<small>${esc(label)}</small>` : ''}</button>`;
  let html = '';
  if (r.songs.length >= 4) html += `<h3>${ICON('fa-solid fa-table-cells-large')} ${t('pic.mosaic')}</h3><div class="tiles"><button class="tile" data-mosaic="1"><canvas id="pic-mosaic" width="600" height="600"></canvas><small>${t('pic.mosaic_label', { n: r.songs.length >= 9 ? 9 : 4 })}</small></button></div>`;
  if (r.songs.length) html += `<h3>${ICON('fa-solid fa-compact-disc')} ${t('pic.from_songs')}</h3><div class="tiles">${r.songs.map(p => tile('/api/cover?path=' + encodeURIComponent(p), `data-song="${esc(p)}"`, '')).join('')}</div>`;
  const kinds = { album: 'pic.albums', playlist: 'pic.playlists', artist: 'pic.artists' };
  for (const k of ['album', 'playlist', 'artist']) {
    const list = r.web.filter(w => w.kind === k);
    if (list.length) html += `<h3>${ICON(k === 'artist' ? 'fa-solid fa-microphone' : k === 'album' ? 'fa-solid fa-record-vinyl' : 'fa-solid fa-list')} ${t(kinds[k])}</h3><div class="tiles">${list.map(w => tile(w.url, `data-url="${esc(w.url)}"`, w.label)).join('')}</div>`;
  }
  if (!html) html = `<div class="notice">${ICON('fa-regular fa-image')}<span>${t('pic.nothing')}</span></div>`;
  else if (!r.web.length) html += `<p class="muted small">${t('pic.offline')}</p>`;
  $('pic-body').innerHTML = html;
  if (r.songs.length >= 4) drawMosaic($('pic-mosaic'), r.songs);
  $('pic-body').querySelectorAll('.tile').forEach(b => b.onclick = () => {
    if (b.dataset.mosaic) return setPicture({ data: $('pic-mosaic').toDataURL('image/jpeg', 0.9) });
    if (b.dataset.song) return setPicture({ song: b.dataset.song });
    if (b.dataset.url) return setPicture({ url: b.dataset.url });
  });
}
async function drawMosaic(canvas, paths) {
  const n = paths.length >= 9 ? 3 : 2, use = Array.from({ length: n * n }, (_, i) => paths[i % paths.length]);
  const ctx = canvas.getContext('2d'), s = canvas.width / n;
  ctx.fillStyle = '#888'; ctx.fillRect(0, 0, canvas.width, canvas.height);
  const imgs = await Promise.all(use.map(p => new Promise(res => { const im = new Image(); im.onload = () => res(im); im.onerror = () => res(null); im.src = '/api/cover?path=' + encodeURIComponent(p); })));
  imgs.forEach((im, i) => { if (im) ctx.drawImage(im, (i % n) * s, Math.floor(i / n) * s, s, s); });
}
async function setPicture(body, file) {
  $('pic-body').querySelectorAll('.tile').forEach(b => b.disabled = true);
  const url = `/api/folders/${encodeURIComponent(PIC.name)}/picture`;
  try {
    if (file) {
      const fd = new FormData(); fd.append('file', file);
      const r = await fetch(url, { method: 'POST', body: fd });
      const d = await r.json();
      if (!r.ok) { toast(tEntry({ t: d.error, k: d.key, p: d.params })); return; }
    } else await api(url, { method: 'POST', body });
    $('pic-dlg').close(); toast(t('pic.saved'));
    await loadLibrary(); CURRENT === null && !SHOW_ALL ? renderCards() : renderHero();
  } finally { $('pic-body').querySelectorAll('.tile').forEach(b => b.disabled = false); }
}
async function removePicture() {
  await api(`/api/folders/${encodeURIComponent(PIC.name)}/picture`, { method: 'DELETE' });
  $('pic-dlg').close();
  await loadLibrary(); CURRENT === null && !SHOW_ALL ? renderCards() : renderHero();
}
function pictureFromFiles(files) { const f = [...(files || [])].find(f => f.type.startsWith('image/')); if (f) setPicture(null, f); else toast(t('pic.not_image')); }
$('pic-file').onchange = e => { pictureFromFiles(e.target.files); e.target.value = ''; };
$('pic-drop').ondragover = e => { e.preventDefault(); $('pic-drop').classList.add('over'); };
$('pic-drop').ondragleave = () => $('pic-drop').classList.remove('over');
$('pic-drop').ondrop = e => { e.preventDefault(); $('pic-drop').classList.remove('over'); pictureFromFiles(e.dataTransfer.files); };
$('pic-q').onkeydown = e => { if (e.key === 'Enter') loadPictures($('pic-q').value); };
document.addEventListener('paste', e => { if ($('pic-dlg').open && e.clipboardData?.files?.length) pictureFromFiles(e.clipboardData.files); });

function renderSongs() {
  const q = $('filter').value.toLowerCase();
  const rows = SONGS.filter(s => !q || (s.title + ' ' + s.artist + ' ' + (s.meta?.genre || '')).toLowerCase().includes(q));
  const opts = LIB.folders.map(f => `<option value="${esc(f.name)}">${esc(f.name)}</option>`).join('');
  $('songs').innerHTML = rows.length ? rows.map((s, i) => `<tr>
    <td><input type="checkbox" data-p="${esc(s.path)}" ${SELECTED.has(s.path) ? 'checked' : ''} aria-label="${esc(s.title)}"></td>
    <td class="muted">${s.track_no ?? i + 1}</td>
    <td class="cv">${s.has_cover ? coverImg(null, null, 36, s.path) : coverImg(s.meta, null)}</td>
    <td class="t">${esc(s.title)}<small>${esc(s.artist)}</small></td>
    <td>${esc(s.meta?.genre || '')}</td><td class="num">${s.meta?.bpm ? (s.meta.bpm_measured ? '~' : '') + s.meta.bpm : ''}</td>
    <td class="${CURRENT === null ? '' : 'hidden'}">${esc(s.folder)}</td>
    <td class="num">${dur(s.duration)}</td><td class="num muted">${size(s.size)}</td>
    <td><select data-move="${esc(s.path)}" aria-label="${esc(t('col.move_to') + ': ' + s.title)}"><option value="">—</option>${opts}</select></td></tr>`).join('')
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
async function createFolder(fromPrompt) {
  const name = (fromPrompt ? prompt(t('side.new_folder')) || '' : $('new-folder').value).trim(); if (!name) return;
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

