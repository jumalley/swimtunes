// Status bar, tabs and start-up. Loaded last.
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
  fd.querySelector('span').textContent = s.device ? t('f.device', { label: s.device.label, path: shortPath(s.device.path), free: size(s.device.free) }) : t('f.no_device');
  if (s.device) fd.dataset.tipRaw = s.device.path; else delete fd.dataset.tipRaw;
  fd.className = 'item' + (s.device ? ' ok' : '');
  $('f-folder').querySelector('span').textContent = shortPath(s.library);
  $('f-folder').dataset.tipRaw = s.library;
}
const JOB_TAB = { playlists: 'add', preview: 'add', download: 'add', analyze: 'organize', suggest: 'organize', import: 'device', sync: 'device', shokz: 'device', update: 'settings' };
async function pollActivity() {
  let jobs = [];
  try { const r = await fetch('/api/jobs/active'); if (r.ok) jobs = await r.json(); } catch (e) { return; }
  const a = $('f-activity');
  if (!jobs.length) { a.classList.add('hidden'); return; }
  const upd = jobs.find(j => j.kind === 'update');
  if (upd && !FOLLOWING_UPDATE) followUpdate(upd.id, true);   // started by itself at launch
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
  if (name === 'device') { loadDrives(); loadShokz(); }
  if (name === 'settings') { loadHealth(); loadAccounts(); }
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
['cfg-capacity_gb', 'cfg-bitrate', 'cfg-dupe_scope', 'cfg-normalize'].forEach(id => $(id).addEventListener('change', saveSettings));
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
  loadVersion(); loadStatus(); pollActivity(); loadAccounts().catch(() => {});
})();
