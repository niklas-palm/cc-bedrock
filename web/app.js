// The dashboard: signs in with the organisation's identity provider (OpenID Connect, code flow with PKCE) and
// shows what the API allows: admins everyone, everyone else themselves.

const config = await (await fetch("config.json")).json();
const $ = (id) => document.getElementById(id);
const back = location.origin + "/"; // the redirect URI registered in the IdP
let me = null,
  token = null,
  fresh = false, // signed in during this page load: a refusal now is the config, not an expired token
  data = null;

// ------------------------------------------------------------------ sign-in

const b64url = (bytes) =>
  btoa(String.fromCharCode(...new Uint8Array(bytes)))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
const jwt = (t) => JSON.parse(atob(t.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
let discovery = null;
async function provider() {
  discovery ??= await (await fetch(`${config.issuer.replace(/\/$/, "")}/.well-known/openid-configuration`)).json();
  return discovery;
}

async function signIn() {
  const verifier = b64url(crypto.getRandomValues(new Uint8Array(32)));
  const state = b64url(crypto.getRandomValues(new Uint8Array(16)));
  sessionStorage.setItem("pkce", JSON.stringify({ verifier, state }));
  const challenge = b64url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
  const query = new URLSearchParams({
    client_id: config.clientId,
    response_type: "code",
    redirect_uri: back,
    scope: config.scopes,
    code_challenge: challenge,
    code_challenge_method: "S256",
    state,
  });
  location.assign(`${(await provider()).authorization_endpoint}?${query}`);
  return new Promise(() => {}); // the page is leaving
}

// Stops with a message instead of sending people round in circles when sign-in can't work
class SignInError extends Error {}

async function getToken() {
  const query = new URLSearchParams(location.search);
  if (query.has("error")) {
    history.replaceState(null, "", back);
    throw new SignInError(
      `Your identity provider refused the sign-in: ${query.get("error_description") || query.get("error")}`,
    );
  }
  if (query.has("code")) {
    const { verifier, state } = JSON.parse(sessionStorage.getItem("pkce") || "{}");
    history.replaceState(null, "", back);
    if (query.get("state") !== state) return signIn();
    const reply = await fetch((await provider()).token_endpoint, {
      method: "POST",
      body: new URLSearchParams({
        grant_type: "authorization_code",
        code: query.get("code"),
        redirect_uri: back,
        client_id: config.clientId,
        code_verifier: verifier,
      }),
    });
    if (!reply.ok) throw new SignInError(`The identity provider's token endpoint answered ${reply.status}.`);
    sessionStorage.removeItem("pkce");
    const tokens = await reply.json();
    const chosen = config.token === "id" ? tokens.id_token : tokens.access_token;
    sessionStorage.setItem("token", chosen); // this tab only, until it expires
    fresh = true;
    return chosen;
  }
  const saved = sessionStorage.getItem("token");
  if (saved && jwt(saved).exp * 1000 > Date.now() + 60_000) return saved;
  return signIn();
}

async function api(path, options = {}) {
  const reply = await fetch(`/api/${path}`, {
    ...options,
    headers: { ...options.headers, authorization: `Bearer ${token}` },
  });
  if (reply.status === 401) {
    sessionStorage.removeItem("token");
    if (fresh)
      throw new SignInError("The API refused a token you just got: check oidc.token and oidc.audience in the config.");
    return signIn();
  }
  if (!reply.ok) {
    const body = await reply.json().catch(() => ({}));
    throw new Error(body.message || `The dashboard's API answered ${reply.status}`);
  }
  return reply;
}

// ------------------------------------------------------------------ helpers

function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text != null) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}
const usd = (x) =>
  !x
    ? "$0"
    : x < 0.01
      ? "<$0.01"
      : x.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: x < 100 ? 2 : 0 });
const count = (x) => Math.round(x || 0).toLocaleString();
const compact = (x) => (x || 0).toLocaleString(undefined, { notation: "compact", maximumFractionDigits: 1 });
const percent = (a, b) => (b ? `${Math.round((100 * a) / b)}%` : "—");
const iso = (d) => d.toISOString().slice(0, 10);
const when = (ms) =>
  new Date(ms).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
const minutes = (ms) =>
  ms < 60_000
    ? `${Math.round(ms / 1000)} s`
    : ms < 3_600_000
      ? `${Math.round(ms / 60_000)} min`
      : `${(ms / 3_600_000).toFixed(1)} h`;

function message(text) {
  $("message").hidden = !text;
  $("message").textContent = text || "";
}

// A table from rows: columns are [heading, value(row), numeric?]; onClick makes rows a drill-down
function table(rows, columns, onClick) {
  if (!rows.length) return el("p", "Nothing in this range.", "hint");
  const t = el("table"),
    head = el("tr");
  for (const [heading, , numeric] of columns) head.append(el("th", heading, numeric ? "n" : ""));
  t.append(el("thead"));
  t.tHead.append(head);
  const body = el("tbody");
  for (const row of rows) {
    const tr = el("tr");
    for (const [, value, numeric] of columns) {
      const v = value(row);
      const td = el("td", v instanceof Node ? null : v, numeric ? "n" : "");
      if (v instanceof Node) td.append(v);
      tr.append(td);
    }
    if (onClick) {
      tr.className = "clickable";
      tr.onclick = () => onClick(row);
    }
    body.append(tr);
  }
  t.append(body);
  const wrap = el("div", null, "scroll");
  wrap.append(t);
  return wrap;
}

function card(label, value, note, cls) {
  const c = el("div", null, "card" + (cls ? ` ${cls}` : ""));
  c.append(el("div", label, "label"), el("div", value, "value"));
  if (note) c.append(el("div", note, "note"));
  return c;
}

const fill = (id, node) => {
  $(id).replaceChildren(node);
};

// ------------------------------------------------------------------ filters

const filters = { from: "", to: "", user: "", tag: "" }; // tag: "project=apollo"

function setRange(days) {
  const today = new Date();
  const from =
    days === "month"
      ? new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), 1))
      : new Date(today.getTime() - (days - 1) * 86_400_000);
  filters.from = iso(from);
  filters.to = iso(today);
  $("from").value = filters.from;
  $("to").value = filters.to;
  for (const b of document.querySelectorAll(".ranges button"))
    b.classList.toggle("active", b.dataset.days === String(days));
}

function query(extra = {}) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries({ ...filters, ...extra })) if (v) q.set(k, v);
  return q.toString();
}

function setOptions(select, values, all) {
  const current = select.value;
  select.replaceChildren(new Option(all, ""), ...values.map((v) => new Option(v, v)));
  if (values.includes(current) || current === "") select.value = current;
  else {
    select.add(new Option(current, current));
    select.value = current;
  }
}

function drill(key, value) {
  filters[key] = value;
  $(key).value = value;
  if (![...$(key).options].some((o) => o.value === value)) {
    $(key).add(new Option(value, value));
    $(key).value = value;
  }
  refresh();
}

// ------------------------------------------------------------------ usage

const PALETTE = ["#d97757", "#6a9bcc", "#788c5d", "#c4a35a", "#9b7bb8", "#5aa5a0", "#b05f6d", "#8a8a8a"];

function chart(daily) {
  const days = [];
  for (let d = new Date(filters.from + "T00:00:00Z"); iso(d) <= filters.to; d = new Date(d.getTime() + 86_400_000))
    days.push(iso(d));
  const models = [...new Set(daily.map((r) => r.model))];
  const byDay = Object.fromEntries(days.map((d) => [d, {}]));
  for (const r of daily) if (byDay[r.day]) byDay[r.day][r.model] = (byDay[r.day][r.model] || 0) + r.cost;
  const max = Math.max(0.01, ...days.map((d) => Object.values(byDay[d]).reduce((a, b) => a + b, 0)));
  // Drawn at the panel's own width, so text stays readable on a phone
  const W = Math.max(320, $("chart").clientWidth || 900),
    H = 220,
    top = 10,
    left = 52,
    bottom = 22,
    w = (W - left) / days.length;
  const ns = "http://www.w3.org/2000/svg",
    svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("class", "bars");
  const add = (tag, attrs, text) => {
    const n = document.createElementNS(ns, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (text) n.textContent = text;
    svg.append(n);
    return n;
  };
  for (const f of [0, 0.5, 1]) {
    const y = top + (H - bottom - top) * (1 - f);
    add("line", { x1: left, x2: W, y1: y, y2: y, class: "grid" });
    add("text", { x: left - 6, y: y + 4, class: "axis", "text-anchor": "end" }, usd(max * f));
  }
  days.forEach((d, i) => {
    let y = H - bottom;
    models.forEach((m, j) => {
      const v = byDay[d][m] || 0,
        h = ((H - bottom - top) * v) / max;
      if (!v) return;
      y -= h;
      const r = add("rect", {
        x: left + i * w + 1,
        y,
        width: Math.max(1, w - 2),
        height: h,
        fill: PALETTE[j % PALETTE.length],
      });
      const t = document.createElementNS(ns, "title");
      t.textContent = `${d} · ${m}: ${usd(v)}`;
      r.append(t);
    });
    if (i % Math.ceil(days.length / Math.max(2, Math.floor(W / 90))) === 0)
      add("text", { x: left + i * w + w / 2, y: H - 6, class: "axis", "text-anchor": "middle" }, d.slice(5));
  });
  const legend = el("div", null, "legend");
  models.forEach((m, j) => {
    const s = el("span", m);
    s.style.setProperty("--c", PALETTE[j % PALETTE.length]);
    legend.append(s);
  });
  const wrap = el("div");
  wrap.append(svg, legend);
  return wrap;
}

function showUsage(d) {
  const t = d.totals,
    billedTotal = d.billed.reduce((a, r) => a + r.cost, 0);
  const cards = [
    card(
      "Estimated spend",
      usd(t.cost),
      t.users > 1 ? `${usd(t.cost / t.users)} a person` : "Claude Code's figure, list price",
    ),
    card("Billed", usd(billedTotal), "AWS's bill, a day or so behind" + (filters.tag ? "; billing knows no tags" : "")),
    card(
      me.admin && !filters.user ? "People" : "Sessions",
      count(me.admin && !filters.user ? t.users : t.sessions),
      me.admin && !filters.user ? `${count(t.sessions)} sessions` : `${count(t.prompts)} prompts`,
    ),
    card("Tokens", compact(t.tokens), `${percent(t.cache_read, t.prompt_tokens)} of prompt tokens from cache`),
    card(
      "Lines changed",
      `+${compact(t.lines_added)} −${compact(t.lines_removed)}`,
      `${count(t.commits)} commits, ${count(t.pull_requests)} pull requests`,
    ),
    card("Active time", minutes((t.active_seconds || 0) * 1000), "typing, or Claude working"),
  ];
  // Your budget: the period closest to its limit, the others underneath
  const budget = me.budget,
    names = { daily: "today", weekly: "this week", monthly: "this month" };
  const periods = budget ? Object.keys(names).filter((p) => budget.limits[p] != null) : [];
  if (periods.length) {
    periods.sort((a, b) => budget.spent[b] / budget.limits[b] - budget.spent[a] / budget.limits[a]);
    const line = (p) => `${usd(budget.spent[p])} of ${usd(budget.limits[p])} ${names[p]}`;
    cards.push(
      card(
        budget.over.length ? "Over budget: Bedrock refuses you until it resets" : "Your budget",
        line(periods[0]),
        periods.slice(1).map(line).join(" · "),
        budget.over.length ? "warn" : "small",
      ),
    );
  }
  $("cards").replaceChildren(...cards);
  fill("chart", chart(d.daily));
  fill(
    "people",
    table(
      d.users,
      [
        ["Person", (r) => r.user],
        ["Estimated", (r) => usd(r.cost), 1],
        ["Sessions", (r) => count(r.sessions), 1],
        ["Prompts", (r) => count(r.prompts), 1],
        ["Lines added", (r) => count(r.lines_added), 1],
        ["Last seen", (r) => (r.last_seen ? when(r.last_seen) : "—")],
      ],
      (r) => drill("user", r.user),
    ),
  );
  fill(
    "tags",
    table(
      d.tags,
      [
        ["Tag", (r) => r.tag],
        ["Value", (r) => r.tag_value],
        ["Estimated", (r) => usd(r.cost), 1],
        ["People", (r) => count(r.users), 1],
        ["Sessions", (r) => count(r.sessions), 1],
      ],
      (r) => drill("tag", r.tag_value ? `${r.tag}=${r.tag_value}` : r.tag),
    ),
  );
  fill(
    "models",
    table(d.models, [
      ["Model", (r) => r.model],
      ["Estimated", (r) => usd(r.cost), 1],
      ["Share", (r) => percent(r.cost, t.cost), 1],
      ["Tokens", (r) => compact(r.tokens), 1],
    ]),
  );
  fill(
    "sources",
    table(d.sources, [
      ["Source", (r) => r.source],
      ["Estimated", (r) => usd(r.cost), 1],
      ["Share", (r) => percent(r.cost, t.cost), 1],
    ]),
  );
  fill(
    "tools",
    table(d.tools, [
      ["Tool", (r) => r.tool],
      ["Calls", (r) => count(r.calls), 1],
      ["Succeeded", (r) => percent(r.ok, r.calls), 1],
      ["Average", (r) => (r.avg_ms < 1000 ? `${Math.round(r.avg_ms)} ms` : `${(r.avg_ms / 1000).toFixed(1)} s`), 1],
    ]),
  );

  const estimated = Object.fromEntries(d.users.map((r) => [r.user.toLowerCase(), r.cost]));
  const billed = {};
  for (const r of d.billed) billed[r.user.toLowerCase()] = (billed[r.user.toLowerCase()] || 0) + r.cost;
  const people = [...new Set([...Object.keys(billed), ...(filters.tag ? [] : Object.keys(estimated))])];
  fill(
    "billed",
    table(
      people
        .map((p) => ({ user: p, billed: billed[p] || 0, estimated: estimated[p] || 0 }))
        .sort((a, b) => b.billed - a.billed),
      [
        ["Person", (r) => r.user],
        ["Billed", (r) => usd(r.billed), 1],
        ["Estimated", (r) => usd(r.estimated), 1],
        ["", (r) => (r.billed > 0.5 && r.estimated === 0 ? el("span", "no telemetry", "badge warn") : "")],
      ],
    ),
  );

  if (me.admin)
    setOptions(
      $("user"),
      d.users.map((r) => r.user),
      "Everyone",
    );
  setOptions(
    $("tag"),
    d.tags.map((r) => (r.tag_value ? `${r.tag}=${r.tag_value}` : r.tag)),
    "All usage",
  );
}

// ------------------------------------------------------------------ sessions

const tagText = (tags) =>
  Object.entries(tags || {})
    .map(([k, v]) => `${k}=${v}`)
    .join(", ");

async function showSessions() {
  const rows = await (await api(`sessions?${query()}`)).json();
  fill(
    "session-list",
    table(
      rows,
      [
        ["Started", (r) => when(r.started)],
        ...(me.admin ? [["Person", (r) => r.user]] : []),
        ["Tags", (r) => tagText(r.tags)],
        ["Model", (r) => r.model],
        ["Prompts", (r) => count(r.prompts), 1],
        ["Model calls", (r) => count(r.requests), 1],
        ["Tools", (r) => count(r.tools), 1],
        ["Estimated", (r) => usd(r.cost), 1],
        ["Length", (r) => minutes(r.ended - r.started), 1],
      ],
      showSession,
    ),
  );
}

async function showSession(s) {
  $("session-panel").hidden = false;
  $("session-title").textContent = [s.user, tagText(s.tags), when(s.started)].filter(Boolean).join(" · ");
  fill("session-detail", el("p", "Loading…", "hint"));
  const from = iso(new Date(s.started)),
    to = iso(new Date(s.ended + 86_400_000));
  const events = await (await api(`session?${new URLSearchParams({ id: s.session, from, to })}`)).json();
  const prompts = [],
    byId = {};
  for (const e of events) {
    const id = e["prompt.id"] || "none";
    if (!byId[id]) {
      byId[id] = { id, ts: e.ts, events: [], cost: 0 };
      if (id !== "none") prompts.push(byId[id]);
    }
    byId[id].events.push(e);
    byId[id].cost += parseFloat(e.cost_usd || 0);
  }
  const list = el("div", null, "timeline");
  prompts.forEach((p, i) => {
    const box = el("details", null, "prompt");
    const calls = p.events.filter((e) => e.name === "api_request"),
      tools = p.events.filter((e) => e.name === "tool_result");
    const length = p.events.find((e) => e.name === "user_prompt")?.prompt_length;
    box.append(
      el(
        "summary",
        `${when(p.ts)} · prompt ${i + 1}${length ? ` (${length} characters)` : ""} · ${calls.length} model calls · ${tools.length} tools · ${usd(p.cost)}`,
      ),
    );
    const rows = p.events.filter((e) => ["api_request", "tool_result", "api_error", "tool_decision"].includes(e.name));
    if (!rows.length) {
      box.append(
        el("p", "No model calls: the request didn't reach the model (see the person's budget and sign-in).", "hint"),
      );
      list.append(box);
      return;
    }
    box.append(
      table(rows, [
        ["Time", (e) => new Date(e.ts).toLocaleTimeString()],
        ["Event", (e) => e.name.replace("_", " ")],
        [
          "What",
          (e) =>
            e.name === "api_request"
              ? `${e.model} (${e.query_source || "main"})`
              : e.tool_name || e.error || e.decision || "",
        ],
        [
          "Tokens in / out / cached",
          (e) =>
            e.name === "api_request"
              ? `${count(e.input_tokens)} / ${count(e.output_tokens)} / ${compact(+e.cache_read_tokens)}`
              : "",
          1,
        ],
        ["Took", (e) => (e.duration_ms ? `${count(e.duration_ms)} ms` : ""), 1],
        [
          "Cost",
          (e) => (e.cost_usd ? usd(+e.cost_usd) : e.success === "false" ? el("span", "failed", "badge warn") : ""),
          1,
        ],
      ]),
    );
    list.append(box);
  });
  fill(
    "session-detail",
    prompts.length
      ? list
      : el("p", "No events for this session (sessions from a few seconds' `claude -p` may send only metrics).", "hint"),
  );
  $("session-panel").scrollIntoView({ behavior: "smooth" });
}

// ------------------------------------------------------------------ budgets

function budgetRow(who, limits = {}, fixed = false) {
  const tr = el("tr");
  const name = el("input");
  name.value = who;
  name.placeholder = "name@example.com";
  name.readOnly = fixed;
  const cell = (n) => {
    const td = el("td");
    td.append(n);
    return td;
  };
  tr.append(cell(fixed ? el("strong", "Everyone (default)") : name));
  for (const p of ["daily", "weekly", "monthly"]) {
    const input = el("input");
    input.type = "number";
    input.min = "0";
    input.step = "any";
    input.dataset.period = p;
    input.value = limits[p] ?? "";
    tr.append(cell(input));
  }
  const remove = el("button", fixed ? "" : "Remove");
  remove.type = "button";
  remove.hidden = fixed;
  remove.onclick = () => tr.remove();
  tr.append(cell(remove));
  tr.dataset.who = fixed ? "*" : "";
  return tr;
}

async function showBudgets() {
  const { budgets, status } = await (await api("budgets")).json();
  $("budget-rows").replaceChildren(
    budgetRow("", budgets.default, true),
    ...Object.entries(budgets.users || {}).map(([u, l]) => budgetRow(u, l)),
  );
  $("budget-updated").textContent = status.updated
    ? `Last checked ${when(Date.parse(status.updated))}. Blocked now: ${status.blocked.join(", ") || "nobody"}.`
    : "Not checked yet.";
  const rows = Object.values(status.users || {}).sort((a, b) => b.spent.monthly - a.spent.monthly);
  const of = (r, p) => (r.limits[p] != null ? `${usd(r.spent[p])} of ${usd(r.limits[p])}` : usd(r.spent[p]));
  fill(
    "budget-status",
    table(rows, [
      ["Person", (r) => r.user],
      ["Today", (r) => of(r, "daily"), 1],
      ["This week", (r) => of(r, "weekly"), 1],
      ["This month", (r) => of(r, "monthly"), 1],
      ["", (r) => (r.over.length ? el("span", `blocked (${r.over.join(", ")})`, "badge warn") : "")],
    ]),
  );
}

async function saveBudgets(event) {
  event.preventDefault();
  const body = { default: {}, users: {} };
  for (const tr of $("budget-rows").children) {
    const limits = {};
    for (const input of tr.querySelectorAll("input[data-period]"))
      if (input.value !== "") limits[input.dataset.period] = parseFloat(input.value);
    if (tr.dataset.who === "*") body.default = limits;
    else {
      const who = tr.querySelector("input").value.trim();
      if (who) body.users[who] = limits;
    }
  }
  try {
    await api("budgets", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    $("budget-saved").textContent = "Saved. It applies at the next check, within 10 minutes.";
    showBudgets();
  } catch (e) {
    $("budget-saved").textContent = e.message;
  }
}

// ------------------------------------------------------------------ set up

function setupCommand() {
  $("setup-command").textContent =
    `bash ~/Downloads/install-claude-code.sh${$("setup-desktop").checked ? " --desktop" : ""}`;
}

async function download(path, name) {
  const blob = await (await api(path)).blob();
  const a = el("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ------------------------------------------------------------------ the page

let tab = "usage";
async function refresh() {
  message("");
  try {
    if (tab === "usage") {
      $("cards").classList.add("loading");
      data = await (await api(`usage?${query()}`)).json();
      $("cards").classList.remove("loading");
      showUsage(data);
    } else if (tab === "sessions") await showSessions();
    else if (tab === "budgets") await showBudgets();
  } catch (e) {
    message(e.message);
  }
}

function show(name) {
  tab = name;
  for (const b of document.querySelectorAll("#tabs button")) b.classList.toggle("active", b.dataset.tab === name);
  for (const s of document.querySelectorAll(".tab")) s.hidden = s.id !== `tab-${name}`;
  $("filters").hidden = !["usage", "sessions"].includes(name);
  refresh();
}

try {
  token = await getToken();
  me = await (await api("me")).json();
} catch (e) {
  message(e.message);
  $("filters").hidden = true;
  throw e;
}
for (const n of document.querySelectorAll(".admin-only")) n.hidden = !me.admin;
const who = el("span", me.user);
if (me.admin) who.append(el("span", "admin", "badge"));
$("who").replaceChildren(who);

setRange(30);
for (const b of document.querySelectorAll(".ranges button"))
  b.onclick = () => {
    setRange(b.dataset.days === "month" ? "month" : +b.dataset.days);
    refresh();
  };
for (const id of ["from", "to"])
  $(id).onchange = () => {
    filters[id] = $(id).value;
    document.querySelectorAll(".ranges button").forEach((b) => b.classList.remove("active"));
    refresh();
  };
for (const id of ["user", "tag"])
  $(id).onchange = () => {
    filters[id] = $(id).value;
    refresh();
  };
for (const b of document.querySelectorAll("#tabs button")) b.onclick = () => show(b.dataset.tab);
$("budget-form").onsubmit = saveBudgets;
$("add-budget").onclick = () => $("budget-rows").append(budgetRow(""));

$("setup-desktop").onchange = setupCommand;
setupCommand();
$("download").onclick = () => download("install.sh", "install-claude-code.sh").catch((e) => message(e.message));
$("download-managed").onclick = () =>
  download("managed-settings.json", "managed-settings.json").catch((e) => message(e.message));
show(new URLSearchParams(location.hash.slice(1)).get("tab") || "usage");
