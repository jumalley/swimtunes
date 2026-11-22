// Browser smoke test: opens every tab of the dashboard in Chromium, on a computer and a phone,
// in English and French, and fails on any page error or any untranslated text.
//   python tests/browser/sample_server.py 8799 &
//   node tests/browser/smoke.js http://localhost:8799 [screenshot folder]
const { chromium } = require('playwright');
const fs = require('fs');

const URL = process.argv[2] || 'http://localhost:8799';
const OUT = process.argv[3] || '';
const TABS = ['add', 'songs', 'organize', 'device', 'dupes', 'settings'];
const problems = [];

async function waitForServer() {
  for (let i = 0; i < 60; i++) {
    try { const r = await fetch(URL + '/api/version'); if (r.ok) return; } catch (e) {}
    await new Promise(r => setTimeout(r, 500));
  }
  throw new Error('the dashboard did not start at ' + URL);
}

async function check(page, where) {
  // a translation key on screen ("cards.title") means a missing translation
  const raw = await page.evaluate(() => {
    const keys = Object.keys(I18N.en);
    const text = document.body.innerText;
    return keys.filter(k => new RegExp('(^|\\s)' + k.replace('.', '\\.') + '($|\\s)').test(text));
  });
  if (raw.length) problems.push(`${where}: untranslated ${raw.join(', ')}`);
  // nothing wider than the screen (no sideways scrolling)
  const wide = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  if (wide > 2) problems.push(`${where}: page is ${wide}px wider than the screen`);
}

(async () => {
  await waitForServer();
  const browser = await chromium.launch();
  const sizes = { computer: { viewport: { width: 1280, height: 800 } }, phone: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } };
  for (const [sizeName, size] of Object.entries(sizes)) {
    for (const locale of ['en-US', 'fr-FR']) {
      const ctx = await browser.newContext({ ...size, locale });
      const page = await ctx.newPage();
      const tag = `${sizeName}/${locale}`;
      page.on('pageerror', e => problems.push(`${tag}: page error: ${e.message}`));
      page.on('console', m => { if (m.type() === 'error') problems.push(`${tag}: console error: ${m.text()}`); });
      page.on('dialog', d => d.dismiss());
      await page.goto(URL);
      await page.waitForFunction(() => window.LIB && document.querySelector('#cards .card'), null, { timeout: 15000 }).catch(() => {});
      for (const tab of TABS) {
        await page.evaluate(t => showTab(t), tab);
        await page.waitForTimeout(600);
        await check(page, `${tag} ${tab}`);
        if (OUT) await page.screenshot({ path: `${OUT}/${sizeName}-${locale}-${tab}.png`, fullPage: true });
      }
      // the playlists: cards, open one, picture chooser
      await page.evaluate(() => showTab('songs'));
      await page.evaluate(() => showCards());
      const cards = await page.locator('#cards .card').count();
      if (cards !== 3) problems.push(`${tag}: expected 3 playlist cards, found ${cards}`);
      await page.locator('#cards .card').first().click();
      await page.waitForTimeout(500);
      if (!(await page.locator('#hero').isVisible())) problems.push(`${tag}: opening a playlist shows no header`);
      if ((await page.locator('#songs tr').count()) < 1) problems.push(`${tag}: opening a playlist shows no songs`);
      await check(page, `${tag} playlist`);
      await page.evaluate(() => openPictures(CURRENT));
      await page.waitForSelector('#pic-body .tile, #pic-body .notice', { timeout: 10000 }).catch(() => problems.push(`${tag}: picture chooser stays empty`));
      await check(page, `${tag} pictures`);
      await page.evaluate(() => $('pic-dlg').close());
      // duplicates: the sample has one
      await page.evaluate(() => showTab('dupes'));
      await page.waitForTimeout(500);
      if ((await page.locator('#dupes .group').count()) !== 1) problems.push(`${tag}: the duplicate song is not listed`);
      // adding music without a link explains what to do (and doesn't crash)
      await page.evaluate(() => showTab('add'));
      await page.click('#find-btn');
      await page.waitForTimeout(300);
      if (!(await page.locator('#toast.show').count())) problems.push(`${tag}: no message when "Find songs" has no link`);
      await ctx.close();
    }
  }
  await browser.close();
  if (problems.length) {
    console.error('Problems found:\n- ' + problems.join('\n- '));
    process.exit(1);
  }
  console.log('Browser smoke test passed: every tab, computer and phone, English and French.');
})().catch(e => { console.error(e); process.exit(1); });
