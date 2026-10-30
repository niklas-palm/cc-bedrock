// The README's dashboard screenshots, from a deployment with the demo on (demo.py), signed in as an admin:
//
//   URL=https://<dashboard>/ USERNAME=admin@example.com PASSWORD_FILE=<file> npm run shoot
//
// Writes docs/images/dashboard-{usage,session,controls,setup}.png at 1440 wide, 2x. Signs in through a Cognito
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
  await page.waitForTimeout(1000);
  await page.screenshot({ path: `${OUT}/dashboard-usage.png`, fullPage: true });

  // A session's first few prompts, the first one open
  await page.click('#tabs button[data-tab="sessions"]');
  await page.waitForSelector("#session-list tr.clickable", { timeout: 40000 });
  await page.click("#session-list tr.clickable >> nth=3");
  await page.waitForSelector("#session-detail details", { timeout: 40000 });
  await page.click("#session-detail summary >> nth=0");
  await page.waitForSelector("#session-detail details[open] tr", { timeout: 20000 });
  await page.waitForTimeout(800);
  const box = await page.$eval("#session-panel", (e) => {
    const r = e.getBoundingClientRect();
    return { x: r.left + scrollX, y: r.top + scrollY, width: r.width, height: Math.min(r.height, 470) };
  });
  await page.screenshot({ path: `${OUT}/dashboard-session.png`, fullPage: true, clip: box });

  await page.click('#tabs button[data-tab="controls"]');
  await page.waitForSelector("#model-controls tr", { timeout: 40000 });
  await page.waitForSelector("#budget-status tr", { timeout: 40000 });
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/dashboard-controls.png`, fullPage: true });

  await page.click('#tabs button[data-tab="setup"]');
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/dashboard-setup.png` });
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
