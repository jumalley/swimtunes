// Duplicates and Settings tabs: preferences, updates, health, accounts.
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
  $('dupes-remove-all').disabled = !DUPES.length;
}
function extraCopies(gi) { const keep = +document.querySelector(`input[name="keep-${gi}"]:checked`).value; return DUPES[gi].filter((_, i) => i !== keep).map(x => x.path); }
async function resolveDupe(gi) { await api('/api/tracks/delete', { method: 'POST', body: { paths: extraCopies(gi) } }); await refresh(); loadDupes(); }
async function removeAllDupes() {
  const paths = DUPES.flatMap((_, gi) => extraCopies(gi)); if (!paths.length) return;
  if (!confirm(t('dup.confirm', { n: paths.length }))) return;
  await api('/api/tracks/delete', { method: 'POST', body: { paths } }); await refresh(); loadDupes();
}

// ---------- settings, updates, health ----------
async function saveSettings() {   // preferences save as soon as one changes
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
    if (u.available && u.prerelease) $('update-status').innerHTML += ` <span class="chip warn">${t('upd.test_version')}</span>`;
    $('rollback-wrap').classList.toggle('hidden', !u.previous);
    if (u.previous) { $('rollback-text').textContent = t('upd.rollback', { v: u.previous }); ROLLBACK_TO = u.previous; }
    loadStatus();
  } catch (e) { $('check-btn').disabled = false; }
}
async function applyUpdate() {
  if (!confirm(t('upd.confirm'))) return;
  const r = await api('/api/update/apply', { method: 'POST' });
  await followUpdate(r.id, false);
}
let FOLLOWING_UPDATE = null;
async function followUpdate(id, auto) {
  if (FOLLOWING_UPDATE === id) return;
  FOLLOWING_UPDATE = id;
  $('update-btn').disabled = true;
  if (auto) showClosed(t('ov.auto_updating_t'), t('ov.auto_updating'));
  const j = await followJob(id, { log: 'upd-log', onUpdate: j => {
    const line = tEntry(j.log[j.log.length - 1]) || t('upd.updating');
    $('update-changes').innerHTML = `<li>${esc(line)}</li>`;
    if (auto) showClosed(t('ov.auto_updating_t') + (j.current ? ` (${j.current})` : ''), line);
  } });
  if (j.status !== 'done') {   // failed: keep working with this version, the banner stays
    $('overlay').classList.add('hidden'); $('update-btn').disabled = false; FOLLOWING_UPDATE = null;
    toast(t('upd.auto_failed')); return;
  }
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
async function setAutoUpdate(on) {
  await api('/api/config', { method: 'POST', body: { auto_update: on } });
  LIB.config.auto_update = on; toast(t('common.saved'));
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
  document.body.classList.toggle('remote', !!v.remote);   // opened on a phone: hide what only works on the computer
  if (v.updating) followUpdate(v.updating, true);
  $('quit-btn').classList.toggle('hidden', !v.can_quit);
  showUpdate(v.update);
}

// ---------- accounts ----------
let ACC = null;
const BROWSER_NAMES = { firefox: 'Firefox', chrome: 'Google Chrome', edge: 'Microsoft Edge', brave: 'Brave', opera: 'Opera',
  vivaldi: 'Vivaldi', chromium: 'Chromium', safari: 'Safari', whale: 'Naver Whale' };
const CHROMIUM = ['chrome', 'edge', 'brave', 'opera', 'vivaldi', 'chromium', 'whale'];   // lock their login on Windows
async function loadAccounts() {
  ACC = await api('/api/accounts');
  const yt = ACC.youtube, sp = ACC.spotify;
  $('yt-browser').innerHTML = `<option value="">${esc(t('acc.choose_browser'))}</option>` + ACC.browsers.map(b => `<option value="${b}">${esc(BROWSER_NAMES[b] || b)}${ACC.platform === 'win32' && CHROMIUM.includes(b) ? ' ' + esc(t('acc.blocked_windows')) : b === 'firefox' ? ' ' + esc(t('acc.recommended')) : ''}</option>`).join('');
  $('yt-browser').value = yt.browser || '';
  $('yt-status').className = 'chip ' + (yt.connected ? 'ok' : '');
  $('yt-status').textContent = yt.connected ? (yt.browser ? t('acc.yt_via', { browser: BROWSER_NAMES[yt.browser] || yt.browser }) : t('acc.yt_via_file')) : t('acc.not_connected');
  $('sp-status').className = 'chip ' + (sp.connected ? 'ok' : '');
  $('sp-status').textContent = sp.connected ? t('acc.connected') : t('acc.not_connected');
  $('sp-setup').classList.toggle('hidden', sp.connected);
  $('sp-connected').classList.toggle('hidden', !sp.connected);
  $('sp-user').textContent = sp.connected ? t('acc.sp_as', { user: sp.user || '?' }) : '';
  if (!$('sp-client').value) $('sp-client').value = sp.client_id || '';
  $('sp-redirect').textContent = `http://127.0.0.1:${location.port || 80}/spotify/callback`;
}
async function connectYouTube() {
  const browser = $('yt-browser').value;
  if (!browser) { toast(t('acc.choose_browser')); return; }
  await api('/api/accounts/youtube', { method: 'POST', body: { browser } });
  await loadAccounts();
  testYouTube();
}
async function uploadCookies() {
  const f = $('yt-file').files[0];
  if (!f) { toast(t('acc.pick_file')); return; }
  const text = await f.text();
  await api('/api/accounts/youtube/cookies', { method: 'POST', body: { text } });
  $('yt-file').value = '';
  await loadAccounts();
  testYouTube();
}
async function testYouTube() {
  $('yt-result').innerHTML = `<span class="state busy">${SPIN}${t('acc.testing')}</span>`;
  const r = await api('/api/accounts/playlists', { method: 'POST' });
  const j = await followJob(r.id, { btn: $('yt-test') });
  const e = j.result?.errors?.youtube;
  if (e) $('yt-result').innerHTML = `<span class="bad-text">${ICON('fa-solid fa-triangle-exclamation')} ${esc(t('acc.yt_err_' + e.kind))}</span><br><span class="muted">${esc(e.text)}</span>`;
  else if (j.status === 'done') {
    const n = (j.result.playlists || []).filter(p => p.source === 'youtube' && !p.liked && p.name !== 'Watch later').length;
    $('yt-result').innerHTML = `<span class="ok-text">${ICON('fa-solid fa-circle-check')} ${esc(t('acc.yt_ok', { n }))}</span>`;
  }
}
async function connectSpotify() {
  const client_id = $('sp-client').value.trim();
  const r = await api('/api/accounts/spotify/start', { method: 'POST', body: { client_id } });
  window.open(r.url, '_blank', 'noopener');
  toast(t('acc.sp_waiting'));
  const until = Date.now() + 5 * 60000;
  while (Date.now() < until) {
    await new Promise(res => setTimeout(res, 2000));
    const a = await api('/api/accounts');
    if (a.spotify.connected) { await loadAccounts(); toast(t('acc.sp_done_t')); return; }
  }
}
function copyRedirect() { navigator.clipboard?.writeText($('sp-redirect').textContent); toast(t('acc.copied')); }
async function disconnect(which) {
  if (!confirm(t('acc.confirm_disconnect'))) return;
  await api('/api/accounts/' + which, { method: 'DELETE' });
  $('yt-result').innerHTML = '';
  await loadAccounts();
}
async function openMyPlaylists() {
  if (!ACC) await loadAccounts();
  $('pl-dlg').showModal();
  if (!ACC.youtube.connected && !ACC.spotify.connected) {
    $('pl-body').innerHTML = `<div class="notice">${ICON('fa-solid fa-user-plus')}<span>${t('acc.none_connected')}</span></div>
      <div class="row" style="margin-top:12px"><button class="fit primary" onclick="$('pl-dlg').close();showTab('settings');$('accounts-panel').scrollIntoView()">${ICON('fa-solid fa-gear')}${t('acc.go_settings')}</button></div>`;
    return;
  }
  $('pl-body').innerHTML = `<span class="state busy">${SPIN}${t('acc.loading_playlists')}</span>`;
  const r = await api('/api/accounts/playlists', { method: 'POST' });
  const j = await followJob(r.id, {});
  if (j.status !== 'done') { $('pl-body').innerHTML = ''; return; }
  const { playlists, errors } = j.result;
  const errHtml = Object.entries(errors).map(([src, e]) => `<div class="notice" style="margin-bottom:8px">${ICON('fa-solid fa-triangle-exclamation')}<span>${src === 'youtube' ? esc(t('acc.yt_err_' + e.kind)) : esc(t('acc.sp_err', { error: e }))}</span></div>`).join('');
  const row = p => `<tr><td class="cv">${p.image ? `<img class="cover" src="${esc(p.image)}" width="36" height="36" loading="lazy" alt="">` : `<span class="cover none">${ICON(p.liked ? 'fa-solid fa-heart' : 'fa-solid fa-list')}</span>`}</td>
    <td class="t">${ICON(p.source === 'spotify' ? 'fa-brands fa-spotify' : 'fa-brands fa-youtube', ` style="color:${p.source === 'spotify' ? '#1db954' : '#ff0033'}"`)} ${esc(p.liked ? (p.source === 'spotify' ? t('acc.liked_songs') : t('acc.liked_videos')) : p.name === 'Watch later' ? t('acc.watch_later') : p.name)}
      <small>${p.count != null ? t('common.n_songs', { n: p.count }) : ''} ${p.private ? `<span class="chip">${ICON('fa-solid fa-lock')}${t('acc.private')}</span>` : ''}</small></td>
    <td><button class="small" data-pl="${esc(p.url)}">${ICON('fa-solid fa-plus')}${t('acc.add_link')}</button></td></tr>`;
  $('pl-body').innerHTML = errHtml + (playlists.length ? `<div class="table-wrap" style="max-height:60vh;overflow:auto"><table>${playlists.map(row).join('')}</table></div>` : `<p class="muted">${t('acc.no_playlists')}</p>`);
  $('pl-body').querySelectorAll('[data-pl]').forEach(b => b.onclick = () => {
    const ta = $('urls'), lines = ta.value.split(/\s+/).filter(Boolean);
    if (!lines.includes(b.dataset.pl)) ta.value = [...lines, b.dataset.pl].join('\n');
    b.disabled = true; b.innerHTML = ICON('fa-solid fa-check') + t('acc.added');
  });
}


// ---------- problem report ----------
async function copyReport() {
  const r = await api('/api/report');
  $('report-text').value = r.text;
  $('report-box').classList.remove('hidden');
  try { await navigator.clipboard.writeText(r.text); toast(t('report.copied')); }
  catch (e) { $('report-box').open = true; $('report-text').select(); toast(t('report.select')); }
}

// ---------- versions: test channel and going back ----------
let ROLLBACK_TO = null, UPDATE_SEEN = false;   // the updates panel checks once per visit
async function setChannel(ch) {
  await api('/api/update/channel', { method: 'POST', body: { channel: ch } });
  LIB.config.update_channel = ch;
  toast(t(ch === 'beta' ? 'upd.beta_on' : 'upd.stable_on'));
  checkUpdate();
}
async function rollback() {
  if (!ROLLBACK_TO || !confirm(t('upd.rollback_confirm', { v: ROLLBACK_TO }))) return;
  const r = await api('/api/update/rollback', { method: 'POST' });
  await followUpdate(r.id, false);
}

// ---------- use from my phone ----------
let PHONE = null;
async function loadPhone() {
  try { PHONE = await api('/api/phone'); } catch (e) { return; }
  $('phone-on').checked = PHONE.enabled;
  const p = PHONE;
  let html = '';
  if (p.enabled && p.listening && p.urls.length) {
    html = `<div class="phone-grid">
      <div class="qr" role="img" aria-label="${esc(t('phone.qr_label', { url: p.urls[0] }))}">${p.qr}</div>
      <ol class="phone-steps">
        <li>${t('phone.s1')}</li>
        <li>${t('phone.s2')} <code class="addr">${esc(p.urls[0])}</code></li>
        <li>${t('phone.s3')} <b class="code">${esc(p.code.slice(0, 3) + ' ' + p.code.slice(3))}</b></li>
        <li>${t('phone.s4')}</li>
      </ol></div>
      <p class="muted small">${t('phone.keep_on')}</p>
      <p class="muted small">${t('phone.firewall')}</p>
      <button class="fit small" onclick="newPhoneCode()" data-tip="phone.new_code_tip">${ICON('fa-solid fa-key')}${t('phone.new_code')}</button>`;
  } else if (p.enabled && !p.urls.length) {
    html = `<div class="notice">${ICON('fa-solid fa-wifi')}<span>${t('phone.no_network')}</span></div>`;
  } else if (p.enabled && !p.listening) {
    html = `<div class="notice">${ICON('fa-solid fa-rotate')}<span>${t(p.can_restart ? 'phone.needs_restart' : 'phone.restart_yourself')}</span></div>`;
  }
  $('phone-body').innerHTML = html;
}
async function setPhone(on) {
  const r = await api('/api/phone', { method: 'POST', body: { enabled: on } });
  if (r.restarting) { await waitForRestart(t('phone.restarting')); return; }
  loadPhone();
}
async function newPhoneCode() {
  if (!confirm(t('phone.new_code_confirm'))) return;
  await api('/api/phone', { method: 'POST', body: { new_code: true } });
  loadPhone();
}
// SwimTunes restarts itself (phone access on/off): wait, then reload the page
async function waitForRestart(text) {
  UPDATING = true;
  showClosed(t('ov.restarting_t'), text);
  const before = Date.now();
  await new Promise(r => setTimeout(r, 2500));
  while (Date.now() - before < 60000) {
    try { const v = await fetch('/api/version'); if (v.ok) { location.reload(); return; } } catch (e) {}
    await new Promise(r => setTimeout(r, 1000));
  }
  showClosed(t('ov.restarting_t'), t('ov.restarting'));
}
