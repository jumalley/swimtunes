// Add music tab: finding songs behind links, sorting them, downloading.
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
    ${it.unavailable ? unavailableCell(it) : metaCells(it.meta, true, it.existing ? 'have' : it.state)}
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
function unavailableCell(it) {
  const kind = it.unavailable_kind || 'unavailable';
  const label = ['private', 'unavailable'].includes(kind) ? t('st.unavailable_' + kind) : t('dlerr.' + kind);
  let help = t('dlerr_help.' + kind);
  if (['private', 'age', 'bot'].includes(kind) && !ACC?.youtube.connected) help += '\n\n' + t('acc.hint_connect_yt');
  return `<td colspan="5"><span class="chip ${kind === 'blocked' || kind === 'network' ? 'warn' : 'bad'}" data-tip-raw="${esc(help + '\n\n' + it.unavailable)}">${ICON('fa-solid fa-ban')}${esc(label)}</span></td>`;
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
    loginNotice(j);
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
  loginNotice(j);
  renderPreview();
  await refresh();
}

// The YouTube login couldn't be read: say so above the download, with a way to fix it.
function loginNotice(j) {
  const e = (j.log || []).find(e => (e.k || '').startsWith('log.yt_login_skipped'));
  if (!e || $('pv-live').querySelector('.login-notice')) return;
  $('pv-live').insertAdjacentHTML('beforeend', `<div class="notice warn-n login-notice">${ICON('fa-solid fa-user-lock')}<span>${esc(tEntry(e))}</span>
    <button class="small fit" onclick="showTab('settings')">${ICON('fa-solid fa-gear')}${t('acc.fix_login')}</button></div>`);
}

