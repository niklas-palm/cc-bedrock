// The architecture picture for the README: the presentation's last step, at rest, without its bottom bar.
//   node shoot.cjs http://localhost:4173/ <repo>/docs/images/architecture.png
const { chromium } = require("playwright");
(async () => {
  const [base, out] = process.argv.slice(2);
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 2 });
  await page.goto(`${base}#2`);
  await page.waitForTimeout(2800);
  await page.screenshot({ path: out, clip: { x: 0, y: 0, width: 1920, height: 1080 - 64 } });
  await browser.close();
})();
