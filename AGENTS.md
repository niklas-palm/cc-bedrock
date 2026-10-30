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
| `dashboard-session.png` | a session opened prompt by prompt | the session view changes |
| `dashboard-controls.png` | Controls: models, routings, budgets, one person blocked | the Controls tab changes |
| `dashboard-setup.png` | the Set up tab | the Set up tab or the installer's usage changes |

Look at every picture before you commit it: nothing cut off, covered or out of date, and nothing real in it (the
demo is eight made-up people at example.com).

### The architecture picture

`docs/architecture/scene.ts` is its content, for the template of the `unfold` skill (niklas-palm/my-cc-skills,
`unfold/assets/template`). Change the scene, keep the sketch in its top comment current, then:

```bash
cp -r <my-cc-skills>/unfold/assets/template /tmp/architecture && cp docs/architecture/scene.ts /tmp/architecture/src/
cd /tmp/architecture && npm install && npm run build && npx vite preview --port 4173   # in the background
node <this repo>/docs/architecture/shoot.cjs http://localhost:4173/ <this repo>/docs/images/architecture.png
```

### The dashboard pictures

On a test deployment, never one people use, with an admin user in its identity provider:

```bash
uv run --with boto3 --with pyarrow python -I docs/screenshots/demo.py on <stack name> <data bucket>
URL=https://<dashboard>/ USERNAME=<admin> PASSWORD_FILE=<file> node docs/screenshots/shoot.js
uv run --with boto3 --with pyarrow python -I docs/screenshots/demo.py off <stack name> <data bucket>
```

`on` moves the deployment's own telemetry, bill and controls aside and writes the demo; `off` puts them back.
Always run `off` afterwards, even when the screenshots failed.
