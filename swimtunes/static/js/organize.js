// Sort my songs tab and the folder rules editor.
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

