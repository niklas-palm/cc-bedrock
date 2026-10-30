// The README's architecture picture: the presentation's last step, at rest, without its bottom bar.
// With `npm run preview` running: npm run shoot (BASE= for another address).
const { chromium } = require("playwright");
const path = require("path");

(async () => {
  const base = process.env.BASE ?? "http://localhost:4173/";
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 2 });
  await page.goto(`${base}#2`);
  await page.waitForTimeout(2800);
  await page.screenshot({ path: path.join(__dirname, "..", "images", "architecture.png"),
                          clip: { x: 0, y: 0, width: 1920, height: 1080 - 64 } });
  await browser.close();
})();
