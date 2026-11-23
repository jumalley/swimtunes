// Earphones tab: drives, compare, import, copy; battery and settings over Bluetooth.
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
  EAR_AT = 0;
}

// ---------- earphones over Bluetooth ----------
let SHOKZ = null;
function batteryIcon(pct) { return pct >= 85 ? 'fa-battery-full' : pct >= 60 ? 'fa-battery-three-quarters' : pct >= 35 ? 'fa-battery-half' : pct >= 15 ? 'fa-battery-quarter' : 'fa-battery-empty'; }
function swimsLeft(info) {   // swims are done in MP3 mode (OpenSwim Pro: 9 h of MP3 on a full charge)
  const h = /openswim pro/i.test(info.name || '') ? 9 : (info.play_hours || 8);
  return info.battery == null ? null : Math.floor(info.battery / 100 * h * 60 / swimMin());
}
async function loadShokz() {
  try { SHOKZ = await api('/api/shokz'); } catch (e) { return; }
  renderShokz();
}
function fwPretty(fw) {
  const m = /R_(\d+)_(\d{4})(\d{2})(\d{2})/.exec(fw || '');
  if (!m) return fw || '';
  const d = new Date(+m[2], +m[3] - 1, +m[4]);
  return `R${+m[1]} · ${d.toLocaleDateString(LANG, { year: 'numeric', month: 'short', day: 'numeric' })}`;
}
let BT_BUSY = false;
function renderShokz() {
  const s = SHOKZ, last = s.last || {};
  const plugged = !!STATUS?.device;
  const devSel = s.devices.length > 1 ? `<select id="bt-dev">${s.devices.map(d => `<option value="${esc(d.address)}">${esc(d.name)}</option>`).join('')}</select>` : '';
  const btn = `<button class="fit" id="bt-btn" onclick="readShokz()" ${!s.devices.length || BT_BUSY ? 'disabled' : ''}>${ICON('fa-solid fa-rotate')}<span>${t(last.time ? 'bt.read_again' : 'bt.read')}</span></button>`;
  const charging = plugged ? `<span class="chip warn" data-tip="bt.charging_tip">${ICON('fa-solid fa-plug-circle-bolt')}${t('bt.charging')}</span>` : '';
  let html;
  if (last.time) {
    const pct = last.battery, left = swimsLeft(last);
    const color = pct == null ? 'var(--muted)' : pct < 25 ? 'var(--danger)' : pct < 50 ? 'var(--warn)' : 'var(--ok)';
    const row = (icon, k, v, extra = '', tip = '') => v ? `<div class="bt-row"${tip ? ` data-tip-raw="${esc(tip)}"` : ''}>${ICON(icon)}<span class="k">${t(k)}</span><span class="v">${v}</span>${extra}</div>` : '';
    const shuffle = last.mp3_mode === 'shuffle';
    html = `<div class="bt-top">
        <div class="ring" style="--p:${pct ?? 0};--c:${color}"><b>${pct != null ? pct + '%' : '?'}</b></div>
        <div class="bt-who"><b>${esc(last.name || '')}</b>
          ${left != null ? `<span class="bt-left">${ICON('fa-solid fa-person-swimming')} ${esc(t('bt.swims_left', { n: left, m: swimMin() }))}</span>` : ''}
          <small class="muted">${esc(t('bt.read_ago', { when: ago(last.time) }))}${last.partial ? ' · ' + esc(t('bt.partial')) : ''}</small>
          ${charging ? `<div>${charging}</div>` : ''}</div>
        <div class="bt-act">${devSel}${btn}</div>
      </div>
      <div class="bt-rows">
        ${row('fa-solid fa-shuffle', 'bt.mp3_mode', last.mp3_mode ? esc(t('bt.mode_' + last.mp3_mode)) : '',
          shuffle ? `<button class="small" onclick="readShokz('normal')" data-tip="bt.shuffle_note" ${BT_BUSY ? 'disabled' : ''}>${ICON('fa-solid fa-arrow-down-1-9')}${t('bt.play_in_order')}</button>` : '')}
        ${row('fa-solid fa-music', 'bt.source', last.media_source ? esc(t('bt.src_' + last.media_source)) : '')}
        ${row('fa-solid fa-sliders', 'bt.eq', last.equalizer ? esc(t('bt.eq_' + last.equalizer)) : '')}
        ${row('fa-solid fa-volume-high', 'bt.volume', last.volume != null ? last.volume + '%' : '')}
        ${row('fa-solid fa-microchip', 'bt.firmware', esc(fwPretty(last.firmware)), '', last.firmware || '')}
      </div>
      ${shuffle ? `<p class="small bt-warn">${ICON('fa-solid fa-triangle-exclamation')} ${t('bt.shuffle_short')}</p>` : ''}`;
  } else {
    const msg = s.devices.length ? esc(t('bt.found', { name: s.devices.map(d => d.name).join(', ') })) : t('bt.none');
    html = `<div class="bt-top"><div class="ring empty"><b>${ICON('fa-brands fa-bluetooth-b')}</b></div>
      <div class="bt-who"><span class="muted">${msg}</span>${charging ? `<div>${charging}</div>` : ''}</div><div class="bt-act">${devSel}${btn}</div></div>`;
  }
  $('bt-body').innerHTML = html;
  if (last.address && $('bt-dev')) $('bt-dev').value = last.address;
  if (BT_BUSY) $('bt-btn').innerHTML = SPIN + `<span>${t(BT_BUSY === 'switch' ? 'bt.switching' : 'bt.reading')}</span>`;
  $('bt-help').textContent = s.platform === 'darwin' ? t('bt.help_mac') : t(plugged ? 'bt.help_plugged' : 'bt.help');
}
async function readShokz(mode) {
  const body = { address: $('bt-dev')?.value || '' };
  if (mode) body.mp3_mode = mode;
  const r = await api('/api/shokz/read', { method: 'POST', body });
  BT_BUSY = mode ? 'switch' : 'read'; renderShokz();
  let j;
  try { j = await followJob(r.id, {}); } finally { BT_BUSY = false; }
  await loadLibrary();
  await loadShokz();
  if (j.status === 'done') toast(t(mode ? (j.result.mp3_mode === mode ? 'bt.switched' : 'bt.not_switched') : 'bt.done'));
}
function renderBatteryFooter() {
  const s = LIB?.config.shokz || {}, el = $('f-battery');
  const show = s.battery != null && s.time;
  el.classList.toggle('hidden', !show); $('f-battery-sep').classList.toggle('hidden', !show);
  if (!show) return;
  el.querySelector('i').className = 'fa-solid ' + batteryIcon(s.battery);
  const left = swimsLeft(s);
  el.querySelector('span').textContent = `${s.battery}%` + (left != null ? ' · ' + t('bt.swims_left_short', { n: left }) : '');
  el.dataset.tipRaw = t('bt.footer_tip', { name: s.name || '', when: ago(s.time), fw: s.firmware || '?' });
}

