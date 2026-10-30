# Working on cc-bedrock

This repository is public. Keep it simple and readable, and keep it "deploy once, pass everything as config":
whatever an organisation decides belongs in `config.yaml`, and nothing account-specific is committed.

## The docs and pictures show the system as it is

The README, the help text in the dashboard and the pictures in `docs/images/` describe the system. A change that
makes any of them wrong updates them in the same pull request: the words, and the pictures recreated, not edited
by hand. That's part of the change, not a follow-up, and it's the agent's job as much as the code is.

| Picture | Shows | Recreate it when |
| --- | --- | --- |
| `architecture.png` | the moving parts and how they connect | a part, a connection or what one is called changes |
| `dashboard-usage.png` | the Usage tab, 30 days of demo data | the Usage tab's cards, chart or tables change |
| `dashboard-controls.png` | Controls: models, routings, required tags, budgets, one person blocked | the Controls tab changes |
| `dashboard-setup.png` | the Set up tab | the Set up tab or the installer's usage changes |

Look at every picture before you commit it: nothing cut off, covered or out of date, and nothing real in it (the
demo is eight made-up people at example.com).

### The architecture picture

`docs/architecture/` is the picture as a small React page: the content is `src/scene.ts` (cards, connectors, and a
sketch of the layout in its top comment, kept current); the rest draws it. Change the scene, then:

```bash
cd docs/architecture && npm ci && npx playwright install chromium
npm run dev                            # to look while you change it
npm run build && npm run preview       # in the background, then:
npm run shoot                          # writes docs/images/architecture.png
```

### The dashboard pictures

On a test deployment, never one people use, with an admin user in its identity provider:

```bash
cd docs/screenshots && npm ci && npx playwright install chromium
uv run --with boto3 --with pyarrow python -I demo.py on <stack name> <data bucket>
URL=https://<dashboard>/ USERNAME=<admin> PASSWORD_FILE=<file> npm run shoot
uv run --with boto3 --with pyarrow python -I demo.py off <stack name> <data bucket>
```

`on` moves the deployment's own telemetry, bill and controls aside and writes the demo; `off` puts them back.
Always run `off` afterwards, even when the screenshots failed.
