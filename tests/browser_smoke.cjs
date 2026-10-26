// Optional developer check. Requires a separately installed Playwright, no frontend build.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
(async () => {
  const browser = await chromium.launch({headless: true, channel: 'chrome'});
  try {
    const context = await browser.newContext({viewport: {width: 1280, height: 960}});
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://127.0.0.1:8765');
    await page.locator('#token').fill('ui-test-only');
    await page.locator('#auth_form button').click();
    await page.locator('#workspace').waitFor({state: 'visible'});
    await page.locator('#new_batch').click();
    await page.locator('#upload:not([disabled])').waitFor();
    await page.locator('#files').setInputFiles([
      path.resolve('extension/icons/icon48.png'), path.resolve('extension/icons/icon128.png')]);
    await page.waitForFunction(() => document.querySelectorAll('.thumb').length === 2);
    assert.match(await page.locator('#count').innerText(), /2 \/ 20/);
    await page.locator('.thumb button').first().click();
    await page.waitForFunction(() => document.querySelectorAll('.thumb').length === 1);
    assert.match(await page.locator('.thumb figcaption').innerText(), /第 1 张/);
    await page.locator('#analyze').click();
    await page.locator('.answer').waitFor();
    assert.equal(await page.locator('.answer').innerText(), '答案：A');
    assert.equal(await page.locator('.thumb').count(), 0);
    await page.locator('.history_row').first().waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    if (process.env.CHECK_OUTPUT_DIR) await page.screenshot({path: path.join(process.env.CHECK_OUTPUT_DIR, 'desktop.png'), fullPage: true});
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    if (process.env.CHECK_OUTPUT_DIR) await page.screenshot({path: path.join(process.env.CHECK_OUTPUT_DIR, 'mobile.png'), fullPage: true});
    await page.locator('#new_batch').click();
    await page.locator('#upload:not([disabled])').waitFor();
    await page.reload();
    await page.locator('#upload:not([disabled])').waitFor();
    await page.locator('#cancel').click();
    await page.locator('#new_batch:not([disabled])').waitFor();
    await page.locator('#clear_token').click();
    await page.locator('#auth').waitFor({state: 'visible'});
    assert.equal(await page.evaluate(() => localStorage.getItem('app_token')), null);
    assert.deepEqual(errors, []);
    console.log('Browser checks passed: auth, multi-upload, delete/resequence, analyze/result, preview disposal, history, task recovery, mobile/desktop overflow, clear token; no page errors.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
