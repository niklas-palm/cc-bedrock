// The README's dashboard screenshots, from a deployment with the demo on (demo.py), signed in as an admin:
//
//   URL=https://<dashboard>/ USERNAME=admin@example.com PASSWORD_FILE=<file> npm run shoot
//
// Writes docs/images/dashboard-{usage,controls,setup}.png at 1440 wide, 2x. Signs in through a Cognito
// managed login page (username, then password); another IdP needs its own two lines.
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const OUT = path.join(__dirname, "..", "images");
const { URL, USERNAME, PASSWORD_FILE } = process.env;

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
  await page.goto(URL);
  await page.waitForSelector('input[name="username"]', { timeout: 20000 });
  await page.fill('input[name="username"]', USERNAME);
  if (!(await page.isVisible('input[name="password"]'))) {
    await page.click('button[type="submit"]');
    await page.waitForSelector('input[name="password"]');
  }
  await page.fill('input[name="password"]', fs.readFileSync(PASSWORD_FILE, "utf8").trim());
  await page.click('button[type="submit"]');
  await page.waitForURL((u) => u.toString().startsWith(URL), { timeout: 20000 });
  await page.waitForSelector("#cards .card", { timeout: 40000 });
  await page.waitForFunction(() => !document.querySelector("body.busy, .tab.loading"), null, { timeout: 40000 });
  await page.waitForTimeout(1000);
  await page.screenshot({ path: `${OUT}/dashboard-usage.png`, fullPage: true });

  await page.click('#tabs button[data-tab="controls"]');
  await page.waitForSelector("#model-controls tr", { timeout: 40000 });
  await page.waitForSelector("#budget-status tr", { timeout: 40000 });
  await page.waitForFunction(() => !document.querySelector("body.busy, .tab.loading"), null, { timeout: 40000 });
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/dashboard-controls.png`, fullPage: true });

  await page.click('#tabs button[data-tab="setup"]');
  await page.waitForFunction(() => !document.querySelector("body.busy, .tab.loading"), null, { timeout: 40000 });
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/dashboard-setup.png` });
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
