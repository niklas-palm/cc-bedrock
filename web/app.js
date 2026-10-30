// The dashboard: signs in with the organisation's identity provider (OpenID Connect, code flow with PKCE) and
// shows what the API allows: admins everyone, everyone else themselves.

const config = await (await fetch("config.json")).json();
const $ = (id) => document.getElementById(id);
const back = location.origin + "/"; // the redirect URI registered in the IdP
let me = null,
  token = null,
  fresh = false, // signed in during this page load: a refusal now is the config, not an expired token
  view = new AbortController(); // the reads for what's on screen: a newer view cancels them

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
  waiting(1);
  try {
    const reply = await fetch(`/api/${path}`, {
      signal: options.method ? undefined : view.signal, // reads only: a save always finishes
      ...options,
      headers: { ...options.headers, authorization: `Bearer ${token}` },
    });
    if (reply.status === 401) {
      sessionStorage.removeItem("token");
      if (fresh)
        throw new SignInError(
          "The API refused a token you just got: check oidc.token and oidc.audience in the config.",
        );
      return signIn();
    }
    if (!reply.ok) {
      const body = await reply.json().catch(() => ({}));
      throw new Error(body.message || `The dashboard's API answered ${reply.status}`);
    }
    return reply;
  } finally {
    waiting(-1);
  }
}

// The bar along the top of the page, while anything waits for the API
let requests = 0;
function waiting(change) {
  requests += change;
  document.body.classList.toggle("busy", requests > 0);
}

// A button says what it's doing until it's done, and can't be pressed again meanwhile
async function pressed(button, doing, work) {
  const label = button.textContent;
  button.disabled = true;
  button.textContent = doing;
  try {
    return await work();
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

// Signing out ends the session at the identity provider too, or the next visit would sign straight back in
async function signOut() {
  const saved = sessionStorage.getItem("token");
  sessionStorage.removeItem("token");
  const end = (await provider()).end_session_endpoint;
  if (!end) return location.assign(back);
  const query = new URLSearchParams({ client_id: config.clientId, post_logout_redirect_uri: back });
  if (config.token === "id" && saved) query.set("id_token_hint", saved);
  location.assign(`${end}?${query}`);
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
      : x.toLocaleString(undefined, {
          style: "currency",
          currency: "USD",
          currencyDisplay: "narrowSymbol",
          minimumFractionDigits: 0,
          maximumFractionDigits: x < 10 && !Number.isInteger(x) ? 2 : 0,
        });
const count = (x) => Math.round(x || 0).toLocaleString();
const compact = (x) => (x || 0).toLocaleString(undefined, { notation: "compact", maximumFractionDigits: 1 });
const percent = (a, b) => (b ? `${Math.round((100 * a) / b)}%` : "—");
const iso = (d) => d.toISOString().slice(0, 10);
const day = (s, options = {}) =>
  new Date(s + "T00:00:00Z").toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
    ...options,
  });
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
      tr.tabIndex = 0;
      tr.onclick = () => onClick(row);
      tr.onkeydown = (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick(row);
        }
      };
    }
    body.append(tr);
  }
  t.append(body);
  const wrap = el("div", null, "scroll");
  wrap.append(t);
  return wrap;
}

// An amount with a bar for its share of the largest in its column, so a table reads at a glance
function meter(value, max, text = usd(value), over = false) {
  const span = el("span", null, "metered");
  const track = el("span", null, "meter" + (over ? " over" : ""));
  const fill_ = el("i");
  fill_.style.width = `${Math.min(100, max ? (100 * value) / max : 0)}%`;
  track.append(fill_);
  span.append(el("span", text), track); // the bars line up at the column's edge, whatever the amount's width
  return span;
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

// ------------------------------------------------------------------ filters, kept in the address

const filters = { from: "", to: "", user: "", tag: "" }; // tag: "project=apollo"
let range = 30; // 7, 30, 90 or "month"; null when from and to are picked by hand

function setRange(days) {
  range = days;
  const today = new Date();
  const from =
    days === "month"
      ? new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), 1))
      : new Date(today.getTime() - (days - 1) * 86_400_000);
  filters.from = iso(from);
  filters.to = iso(today);
  showRange();
}

function showRange() {
  $("from").value = filters.from;
  $("to").value = filters.to;
  for (const b of document.querySelectorAll(".ranges button"))
    b.classList.toggle("active", b.dataset.days === String(range));
  $("clear-filters").hidden = !filters.user && !filters.tag;
}

// The tab, range and filters are in the address, so a reload keeps them and a link shows someone the same
function remember() {
  const q = new URLSearchParams({ tab });
  if (range) q.set("range", range);
  else {
    q.set("from", filters.from);
    q.set("to", filters.to);
  }
  for (const key of ["user", "tag"]) if (filters[key]) q.set(key, filters[key]);
  history.replaceState(null, "", `#${q}`);
}

function recall() {
  const q = new URLSearchParams(location.hash.slice(1));
  const days = q.get("range");
  if (q.get("from") && q.get("to")) {
    range = null;
    filters.from = q.get("from");
    filters.to = q.get("to");
    showRange();
  } else setRange(days === "month" ? "month" : [7, 30, 90].includes(+days) ? +days : 30);
  for (const key of ["user", "tag"]) if (q.get(key) && (key === "tag" || me.admin)) choose(key, q.get(key));
  return ["usage", "setup", ...(me.admin ? ["controls"] : [])].includes(q.get("tab")) ? q.get("tab") : "usage";
}

function query() {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) if (v) q.set(k, v);
  return q.toString();
}

function setOptions(select, values, all) {
  const current = select.value;
  select.replaceChildren(new Option(all, ""), ...values.map((v) => new Option(v, v)));
  if (current && !values.includes(current)) select.add(new Option(current, current));
  select.value = current;
}

function choose(key, value) {
  filters[key] = value;
  if (value && ![...$(key).options].some((o) => o.value === value)) $(key).add(new Option(value, value));
  $(key).value = value;
  showRange();
}

const drill = (key, value) => {
  choose(key, value);
  refresh();
};

// ------------------------------------------------------------------ usage

const PALETTE = ["#d97757", "#6a9bcc", "#788c5d", "#c4a35a", "#9b7bb8", "#5aa5a0", "#b05f6d", "#8a8a8a"];

function chart(daily) {
  const days = [];
  for (let d = new Date(filters.from + "T00:00:00Z"); iso(d) <= filters.to; d = new Date(d.getTime() + 86_400_000))
    days.push(iso(d));
  const models = [...new Set(daily.map((r) => r.model))];
  const color = (m) => PALETTE[models.indexOf(m) % PALETTE.length];
  const byDay = Object.fromEntries(days.map((d) => [d, {}]));
  for (const r of daily) if (byDay[r.day]) byDay[r.day][r.model] = (byDay[r.day][r.model] || 0) + r.cost;
  const total = (d) => Object.values(byDay[d]).reduce((a, b) => a + b, 0);
  // The axis tops out at a round number: 1, 2 or 5 times a power of ten
  const peak = Math.max(0.01, ...days.map(total));
  const step = 10 ** Math.floor(Math.log10(peak));
  const max = [1, 2, 5, 10].map((m) => m * step).find((m) => m >= peak);
  const axis = (x) => (x >= 1000 ? `$${compact(x)}` : usd(x));
  // Drawn at the panel's own width, so text stays readable on a phone
  const W = Math.max(320, $("chart").clientWidth || 900),
    H = 220,
    top = 10,
    left = 12 + 7 * axis(max).length,
    bottom = 22,
    w = (W - left) / days.length;
  const ns = "http://www.w3.org/2000/svg",
    svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("class", "bars");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `Estimated spend per day, ${day(filters.from)} to ${day(filters.to)}`);
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
    add("text", { x: left - 6, y: y + 4, class: "axis", "text-anchor": "end" }, axis(max * f));
  }
  const labelEvery = Math.ceil(days.length / Math.max(2, Math.floor(W / 80)));
  days.forEach((d, i) => {
    // The whole column answers the pointer: the day, its total and each model's part
    const hit = add("rect", { x: left + i * w, y: top, width: w, height: H - bottom - top, class: "hit" });
    hit.onpointerenter = (e) => tooltip(e, d, byDay[d], total(d), color);
    hit.onpointermove = (e) => tooltip(e, d, byDay[d], total(d), color);
    hit.onpointerleave = () => ($("tooltip").hidden = true);
    let y = H - bottom;
    for (const m of models) {
      const v = byDay[d][m] || 0,
        h = ((H - bottom - top) * v) / max;
      if (!v) continue;
      y -= h;
      add("rect", { x: left + i * w + 1, y, width: Math.max(1, w - 2), height: h, fill: color(m), class: "bar" });
    }
    if (i % labelEvery === 0)
      add("text", { x: left + i * w + w / 2, y: H - 6, class: "axis", "text-anchor": "middle" }, day(d));
  });
  const legend = el("div", null, "legend");
  for (const m of models) {
    const s = el("span", m);
    s.style.setProperty("--c", color(m));
    legend.append(s);
  }
  const wrap = el("div");
  wrap.append(svg, legend);
  return wrap;
}

function tooltip(event, d, parts, sum, color) {
  const tip = $("tooltip");
  const rows = Object.entries(parts).sort((a, b) => b[1] - a[1]);
  tip.replaceChildren(el("strong", day(d, { weekday: "short" })), el("div", `${usd(sum)} estimated`, "total"));
  for (const [m, v] of rows) {
    const row = el("div", null, "row");
    const name = el("span", m);
    name.style.setProperty("--c", color(m));
    row.append(name, el("span", usd(v)));
    tip.append(row);
  }
  if (!rows.length) tip.append(el("div", "No usage", "hint"));
  tip.hidden = false;
  const x = Math.min(event.clientX + 14, innerWidth - tip.offsetWidth - 8);
  tip.style.left = `${x}px`;
  tip.style.top = `${event.clientY + 14}px`;
}

const SOURCES = { main: "Main conversation" };

// The chart is drawn at its panel's width: a window made wider or narrower draws it again
let daily = [],
  resized;
addEventListener("resize", () => {
  clearTimeout(resized);
  resized = setTimeout(() => daily.length && fill("chart", chart(daily)), 150);
});

function showUsage(d) {
  const t = d.totals,
    billedTotal = d.billed.reduce((a, r) => a + r.cost, 0);
  const nothing = !t.cost && !t.sessions && !billedTotal;
  $("empty").hidden = !nothing;
  $("usage").hidden = nothing;
  const everyone = me.admin && !filters.user;
  const cards = [
    card(
      "Estimated spend",
      usd(t.cost),
      t.users > 1 ? `${usd(t.cost / t.users)} a person` : "Claude Code's own figure, at list price",
    ),
    card(
      "Billed",
      usd(billedTotal),
      filters.tag ? "AWS's bill, which knows no tags" : "AWS's bill, a day or so behind",
    ),
    card(
      everyone ? "People" : "Sessions",
      count(everyone ? t.users : t.sessions),
      everyone ? `${count(t.sessions)} sessions` : `${count(t.prompts)} prompts`,
    ),
    card("Tokens", compact(t.tokens), `${percent(t.cache_read, t.prompt_tokens)} of prompt tokens from cache`),
    card(
      "Lines added",
      `+${compact(t.lines_added)}`,
      `${compact(t.lines_removed)} removed, ${count(t.commits)} commits, ${count(t.pull_requests)} pull requests`,
    ),
    card("Active time", minutes((t.active_seconds || 0) * 1000), "typing, or Claude working"),
  ];
  // Your own budget, when what's shown is you
  const budget = me.budget;
  const periods = budget ? Object.keys(PERIODS).filter((p) => budget.limits[p] != null) : [];
  if (periods.length && (!me.admin || filters.user.toLowerCase() === me.user.toLowerCase())) {
    periods.sort((a, b) => budget.spent[b] / budget.limits[b] - budget.spent[a] / budget.limits[a]);
    const line = (p) => `${usd(budget.spent[p])} of ${usd(budget.limits[p])} ${PERIODS[p]}`;
    cards.push(card("Your budget", line(periods[0]), periods.slice(1).map(line).join(" · "), "small"));
  }
  $("cards").replaceChildren(...cards);
  daily = d.daily;
  fill("chart", chart(daily));

  // One row a person: what Claude Code estimates and what AWS billed, side by side. Billing knows no tags, so with
  // a tag chosen there's only the estimate.
  const billed = {};
  for (const r of d.billed) billed[r.user.toLowerCase()] = (billed[r.user.toLowerCase()] || 0) + r.cost;
  const people = d.users.map((r) => ({ ...r, billed: billed[r.user.toLowerCase()] || 0 }));
  if (!filters.tag)
    for (const [user, cost] of Object.entries(billed))
      if (!people.some((r) => r.user.toLowerCase() === user)) people.push({ user, cost: 0, billed: cost });
  const top = Math.max(...people.map((r) => Math.max(r.cost, r.billed)), 0);
  fill(
    "people",
    table(
      people,
      [
        ["Person", (r) => r.user],
        ["Estimated", (r) => meter(r.cost, top), 1],
        ...(filters.tag ? [] : [["Billed", (r) => usd(r.billed), 1]]),
        ["Sessions", (r) => count(r.sessions), 1],
        ["Prompts", (r) => count(r.prompts), 1],
        ["Last seen", (r) => (r.last_seen ? day(iso(new Date(r.last_seen))) : "—"), 1],
        ["", (r) => (r.billed > 0.5 && !r.cost ? el("span", "no telemetry", "badge warn") : "")],
      ],
      (r) => drill("user", r.user),
    ),
  );
  const tagTop = Math.max(0, ...d.tags.map((r) => r.cost));
  fill(
    "tags",
    table(
      d.tags,
      [
        ["Tag", (r) => (r.tag_value ? `${r.tag}=${r.tag_value}` : el("span", "no tags", "muted"))],
        ["Estimated", (r) => meter(r.cost, tagTop), 1],
        ...(me.admin ? [["People", (r) => count(r.users), 1]] : []),
        ["Sessions", (r) => count(r.sessions), 1],
      ],
      (r) => drill("tag", r.tag_value ? `${r.tag}=${r.tag_value}` : r.tag),
    ),
  );
  fill(
    "models",
    table(d.models, [
      ["Model", (r) => r.model],
      ["Estimated", (r) => meter(r.cost, t.cost), 1],
      ["Share", (r) => percent(r.cost, t.cost), 1],
      ["Tokens", (r) => compact(r.tokens), 1],
    ]),
  );
  fill(
    "sources",
    table(d.sources, [
      ["Source", (r) => SOURCES[r.source] || r.source],
      ["Estimated", (r) => meter(r.cost, t.cost), 1],
      ["Share", (r) => percent(r.cost, t.cost), 1],
    ]),
  );
  fill(
    "tools",
    table(d.tools, [
      ["Tool", (r) => r.tool],
      ["Calls", (r) => count(r.calls), 1],
      ["Succeeded", (r) => percent(r.ok, r.calls), 1],
      [
        "Average time",
        (r) => (r.avg_ms < 1000 ? `${Math.round(r.avg_ms)} ms` : `${(r.avg_ms / 1000).toFixed(1)} s`),
        1,
      ],
    ]),
  );

  if (me.admin)
    setOptions(
      $("user"),
      d.users.map((r) => r.user),
      "Everyone",
    );
  setOptions(
    $("tag"),
    d.tags.filter((r) => r.tag_value).map((r) => `${r.tag}=${r.tag_value}`),
    "All usage",
  );
}

// Blocked yourself: said at the top of every tab, with why and until when
const PERIODS = { daily: "today", weekly: "this week", monthly: "this month" };
function showOverBudget() {
  const budget = me.budget;
  const over = budget?.over || [];
  $("over-budget").hidden = !over.length;
  if (!over.length) return;
  const p = over[0];
  $("over-budget").textContent =
    `You've reached your budget, ${usd(budget.spent[p])} of ${usd(budget.limits[p])} ${PERIODS[p]}, so Bedrock ` +
    `refuses your Claude Code requests until the period ends (UTC) or an admin resets it.`;
}

// ------------------------------------------------------------------ controls

// A panel with a form says when it has changes to save, and its Save is only there for them
function unchanged(panel, said = "") {
  panel.querySelector("button.primary").disabled = true;
  panel.querySelector(".actions .hint").textContent = said;
}
const changed = (node) => node.closest("[data-form]").dispatchEvent(new Event("input", { bubbles: true }));

function budgetRow(who, limits = {}, defaults = null) {
  const tr = el("tr");
  const cell = (n) => {
    const td = el("td");
    td.append(n);
    tr.append(td);
  };
  const name = el("input");
  name.value = who;
  name.placeholder = "name@example.com";
  name.setAttribute("aria-label", "Identity Center user name");
  cell(defaults ? name : el("strong", "Everyone (default)"));
  for (const p of ["daily", "weekly", "monthly"]) {
    const input = el("input");
    input.type = "number";
    input.min = "0";
    input.step = "any";
    input.dataset.period = p;
    input.value = limits[p] ?? "";
    // An empty field: no limit for everyone, or the default's for a person
    input.placeholder = defaults ? (defaults[p] != null ? `${usd(defaults[p])} default` : "No limit") : "No limit";
    input.setAttribute("aria-label", `${who || "Default"}, ${PERIODS[p]}`);
    // × empties the field: no limit, or for a person the default's
    const clear = el("button", "×", "clear");
    clear.type = "button";
    clear.title = defaults ? "Use the default" : "No limit";
    clear.setAttribute("aria-label", clear.title);
    const cleared = () => (clear.hidden = input.value === "" && !input.validity.badInput);
    input.addEventListener("input", () => {
      input.classList.remove("invalid");
      cleared();
    });
    clear.onclick = () => {
      input.value = "";
      input.classList.remove("invalid");
      cleared();
      changed(tr);
      input.focus();
    };
    cleared();
    const field = el("span", null, "limit");
    field.append(input, clear);
    cell(field);
  }
  const remove = el("button", "Remove");
  remove.type = "button";
  remove.hidden = !defaults;
  remove.onclick = () => {
    changed(tr);
    tr.remove();
  };
  cell(remove);
  tr.dataset.who = defaults ? "" : "*";
  return tr;
}

let budgetDefaults = {};
function showBudgets(budgets) {
  budgetDefaults = budgets.default || {};
  $("budget-rows").replaceChildren(
    budgetRow("", budgetDefaults),
    ...Object.entries(budgets.users || {}).map(([u, l]) => budgetRow(u, l, budgetDefaults)),
  );
}

async function showControls() {
  const [{ budgets, status }, modelList, tags] = await Promise.all([
    api("budgets").then((r) => r.json()),
    api("models").then((r) => r.json()),
    api("tags").then((r) => r.json()),
  ]);
  showModels(modelList);
  showRequiredTags(tags);
  showBudgets(budgets);
  showStatus(status);
  for (const panel of document.querySelectorAll("#tab-controls [data-form]")) unchanged(panel);
}

const ROUTE_NAMES = { eu: "EU", us: "US", apac: "APAC", jp: "Japan", au: "Australia", global: "Global" };

function showModels({ models, default: start, routes }) {
  // Where requests may go: one switch per routing the region's inference profiles use (eu., global., ...)
  const switches = el("div", null, "routes");
  for (const r of routes) {
    const box = el("input");
    box.type = "checkbox";
    box.className = "switch";
    box.checked = !r.off;
    box.dataset.route = r.id;
    const label = el("label");
    label.append(box, r.name);
    switches.append(label);
  }
  const off = new Set(routes.filter((r) => r.off).map((r) => r.id));
  const profiles = (r) => {
    const span = el("span", null, "chips");
    for (const p of r.profiles) {
      const route = p.split(".")[0];
      const chip = el("span", ROUTE_NAMES[route] || route, "chip" + (off.has(route) ? " muted" : ""));
      chip.title = p;
      span.append(chip);
    }
    return span;
  };
  const table_ = table(models, [
    ["Model", (r) => (r.routable ? r.name : el("span", `${r.name} (not in the regions allowed)`, "muted"))],
    ["Runs in", profiles],
    [
      "Allowed",
      (r) => {
        const box = el("input");
        box.type = "checkbox";
        box.className = "switch";
        box.checked = !r.disabled;
        box.dataset.model = r.id;
        box.setAttribute("aria-label", `${r.name} allowed`);
        return box;
      },
    ],
    [
      "Default",
      (r) => {
        const radio = el("input");
        radio.type = "radio";
        radio.name = "default-model";
        radio.value = r.id;
        radio.checked = r.id === start;
        radio.dataset.routable = r.routable;
        radio.setAttribute("aria-label", `${r.name} as the default`);
        return radio;
      },
    ],
  ]);
  const heading = el("h3", "Where requests may go");
  const hint = el(
    "p",
    "A cross-region inference profile keeps requests in its geography (EU: EU regions only) or sends them to any commercial region (Global).",
    "hint",
  );
  $("model-controls").replaceChildren(heading, hint, switches, el("h3", "Models"), table_);
  defaultsAllowed();
}

// Only an allowed model people can reach can be the default
function defaultsAllowed() {
  for (const radio of document.querySelectorAll("#model-controls input[name=default-model]")) {
    const allowed = document.querySelector(`#model-controls input[data-model="${radio.value}"]`).checked;
    radio.disabled = !allowed || radio.dataset.routable !== "true";
    if (radio.disabled) radio.checked = false;
  }
}

async function saveModels() {
  const disabled = [...document.querySelectorAll("#model-controls input[data-model]")]
    .filter((box) => !box.checked)
    .map((box) => box.dataset.model);
  const routes_off = [...document.querySelectorAll("#model-controls input[data-route]")]
    .filter((box) => !box.checked)
    .map((box) => box.dataset.route);
  const start = document.querySelector("#model-controls input[name=default-model]:checked")?.value || "";
  try {
    const saved = await pressed($("save-models"), "Saving…", () =>
      api("models", put({ disabled, default: start, routes_off })),
    );
    showModels(await saved.json());
    unchanged(
      $("save-models").closest("[data-form]"),
      "Saved. Bedrock refuses what's turned off within a minute; the installer offers the rest from now on.",
    );
  } catch (e) {
    $("models-saved").textContent = e.message;
  }
}

// A required tag: its name, the variable that sets it, and what happens when a session doesn't carry it
function tagRow(name = "", mode = "warn") {
  const tr = el("tr");
  const input = el("input");
  input.value = name;
  input.placeholder = "project";
  input.setAttribute("aria-label", "Tag");
  const variable = el("code");
  const named = () => (variable.textContent = `CC_${(input.value.trim() || "…").toUpperCase()}`);
  input.oninput = named;
  named();
  const select = el("select");
  select.setAttribute("aria-label", "When it's missing");
  for (const [value, label] of [
    ["warn", "Warn"],
    ["require", "Require"],
  ])
    select.append(new Option(label, value, false, value === mode));
  const remove = el("button", "Remove");
  remove.type = "button";
  remove.onclick = () => {
    changed(tr);
    tr.remove();
    noTags();
  };
  for (const node of [input, variable, select, remove]) {
    const td = el("td");
    td.append(node);
    tr.append(td);
  }
  return tr;
}

const noTags = () => ($("no-tags").hidden = $("tag-rows").children.length > 0);

function showRequiredTags({ tags }) {
  $("tag-rows").replaceChildren(...Object.entries(tags || {}).map(([name, mode]) => tagRow(name, mode)));
  noTags();
}

async function saveRequiredTags() {
  const tags = {};
  for (const tr of $("tag-rows").children) {
    const name = tr.querySelector("input").value.trim();
    if (name) tags[name] = tr.querySelector("select").value;
  }
  try {
    const saved = await pressed($("save-tags"), "Saving…", () => api("tags", put({ tags })));
    showRequiredTags(await saved.json());
    unchanged($("save-tags").closest("[data-form]"), "Saved. New Claude Code sessions check for them from now on.");
  } catch (e) {
    $("tags-saved").textContent = e.message;
  }
}

async function reset(who) {
  const question =
    who === "*"
      ? "Reset everyone's budgets? Their spend so far this day, week and month stops counting, and anyone blocked can work again."
      : `Reset ${who}'s budgets? Their spend so far this day, week and month stops counting.`;
  if (!confirm(question)) return;
  // One reset at a time: each runs the budget check, and two at once could undo each other
  const buttons = [...document.querySelectorAll("#budget-status button, #reset-everyone")];
  buttons.forEach((b) => (b.disabled = true));
  try {
    showStatus(await (await api("reset", put({ who }))).json());
  } catch (e) {
    message(e.message);
  } finally {
    buttons.forEach((b) => (b.disabled = false));
  }
}

const put = (body) => ({ method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });

function showStatus(status) {
  const full = status.not_blocked?.length
    ? ` The policy is full (IAM allows about 150 people), so these are over budget but not blocked: ${status.not_blocked.join(", ")}.`
    : "";
  const blocked = status.blocked || [];
  $("budget-updated").textContent = status.updated
    ? `Checked ${when(Date.parse(status.updated))}, every 10 minutes and whenever you save. ` +
      `${blocked.length ? `Blocked now: ${blocked.join(", ")}.` : "Nobody is blocked."}${full}`
    : "Not checked yet: the first check runs within 10 minutes of the deployment.";
  const rows = Object.values(status.users || {}).sort(
    (a, b) => b.over.length - a.over.length || b.spent.monthly - a.spent.monthly,
  );
  // Spend against each limit: a bar when there is one, red once it's reached
  const against = (r, p) =>
    r.limits[p] != null
      ? meter(r.spent[p], r.limits[p], `${usd(r.spent[p])} of ${usd(r.limits[p])}`, r.spent[p] >= r.limits[p])
      : usd(r.spent[p]);
  fill(
    "budget-status",
    table(rows, [
      ["Person", (r) => r.user],
      ["Today", (r) => against(r, "daily"), 1],
      ["This week", (r) => against(r, "weekly"), 1],
      ["This month", (r) => against(r, "monthly"), 1],
      ["", (r) => (r.over.length ? el("span", "Blocked", "badge warn") : "")],
      [
        "",
        (r) => {
          const button = el("button", "Reset", "small");
          button.onclick = () => reset(r.user);
          return button;
        },
      ],
    ]),
  );
}

async function saveBudgets(event) {
  event.preventDefault();
  const body = { default: {}, users: {} };
  // A limit is 0 or more; an empty field is no limit
  const wrong = [...$("budget-rows").querySelectorAll("input[data-period]")].filter(
    (input) => input.validity.badInput || (input.value !== "" && !(parseFloat(input.value) >= 0)),
  );
  for (const input of wrong) input.classList.add("invalid");
  if (wrong.length) {
    wrong[0].focus();
    $("budget-saved").textContent = "A limit is 0 or more. Press × for no limit.";
    return;
  }
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
    const saved = await pressed($("budget-form").querySelector("[type=submit]"), "Saving…", () =>
      api("budgets", put(body)),
    );
    showBudgets(await saved.json());
    unchanged($("budget-form").closest("[data-form]"), "Saved. It applies within a minute.");
    // By then the check has run: where everyone stands, without touching what's being edited
    setTimeout(
      () =>
        api("budgets", { signal: null })
          .then((r) => r.json())
          .then((r) => showStatus(r.status))
          .catch(() => {}), // the next visit to the tab shows it anyway
      20_000,
    );
  } catch (e) {
    $("budget-saved").textContent = e.message;
  }
}

// ------------------------------------------------------------------ set up

async function download(path, name) {
  const blob = await (await api(path, { signal: null })).blob(); // switching tabs doesn't stop a download
  const a = el("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// The tags this organisation asks for, said where people set Claude Code up
async function showSetup() {
  const { tags } = await (await api("tags")).json();
  const named = (mode) => Object.keys(tags || {}).filter((t) => tags[t] === mode);
  const list = (names) =>
    (names.length === 1 ? "the tag " : "the tags ") + names.map((t) => `${t} (CC_${t.toUpperCase()})`).join(", ");
  const required = named("require"),
    asked = named("warn");
  $("setup-tags").hidden = !required.length && !asked.length;
  $("setup-tags").textContent = [
    required.length &&
      `Your organisation requires ${list(required)}: Claude Code holds prompts back until ${required.length === 1 ? "it's" : "they're"} set.`,
    asked.length &&
      `${required.length ? "It" : "Your organisation"} asks for ${list(asked)}: Claude Code reminds you when ${asked.length === 1 ? "it's" : "they're"} missing.`,
  ]
    .filter(Boolean)
    .join(" ");
}

// Every command and snippet on the page has a button that copies it
for (const box of document.querySelectorAll(".copyable")) {
  const button = el("button", "Copy", "copy small");
  button.type = "button";
  button.onclick = async () => {
    await navigator.clipboard.writeText(box.querySelector("pre").textContent);
    button.textContent = "Copied";
    setTimeout(() => (button.textContent = "Copy"), 1500);
  };
  box.append(button);
}

// ------------------------------------------------------------------ the page

let tab = "usage";
let pending;
function refresh() {
  // Clicking 7, 30 and 90 days quickly loads only the last: each load runs several Athena queries, and stopping
  // the page's request doesn't stop them
  clearTimeout(pending);
  remember();
  loading(tab); // at once, though the load itself waits a moment
  pending = setTimeout(load, 300);
}

// A tab being loaded dims what it shows, or shows placeholders until it has something
function loading(name) {
  for (const s of document.querySelectorAll(".tab")) {
    s.classList.toggle("loading", s.id === `tab-${name}`);
    s.setAttribute("aria-busy", s.id === `tab-${name}`);
  }
}

async function load() {
  view.abort(); // and a view still loading isn't drawn over the new one
  const mine = (view = new AbortController());
  message("");
  try {
    if (tab === "usage") showUsage(await (await api(`usage?${query()}`)).json());
    else if (tab === "controls") await showControls();
    else await showSetup();
  } catch (e) {
    if (e.name !== "AbortError") message(e.message);
  } finally {
    if (view === mine) loading(null);
  }
}

function show(name) {
  tab = name;
  for (const b of document.querySelectorAll("#tabs button")) {
    b.classList.toggle("active", b.dataset.tab === name);
    if (b.dataset.tab === name) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  }
  for (const s of document.querySelectorAll(".tab")) s.hidden = s.id !== `tab-${name}`;
  $("filters").hidden = name !== "usage";
  refresh();
}

try {
  token = await getToken();
  me = await (await api("me")).json();
} catch (e) {
  message(e.message);
  $("filters").hidden = true;
  loading(null);
  document.body.classList.remove("busy");
  throw e;
}
for (const n of document.querySelectorAll(".admin-only")) n.hidden = !me.admin;
const signOutButton = el("button", "Sign out", "link");
signOutButton.onclick = signOut;
$("who").replaceChildren(el("span", me.user), ...(me.admin ? [el("span", "admin", "badge")] : []), signOutButton);
showOverBudget();

for (const b of document.querySelectorAll(".ranges button"))
  b.onclick = () => {
    setRange(b.dataset.days === "month" ? "month" : +b.dataset.days);
    refresh();
  };
for (const id of ["from", "to"])
  $(id).onchange = () => {
    filters[id] = $(id).value;
    range = null;
    showRange();
    refresh();
  };
for (const id of ["user", "tag"])
  $(id).onchange = () => {
    choose(id, $(id).value);
    refresh();
  };
$("clear-filters").onclick = () => {
  choose("user", "");
  choose("tag", "");
  refresh();
};
for (const b of document.querySelectorAll("#tabs button, [data-go]"))
  b.onclick = () => show(b.dataset.tab || b.dataset.go);

for (const panel of document.querySelectorAll("[data-form]")) {
  panel.addEventListener("input", () => {
    panel.querySelector("button.primary").disabled = false;
    panel.querySelector(".actions .hint").textContent = "Unsaved changes";
  });
  panel.addEventListener("change", () => panel.dispatchEvent(new Event("input")));
}
$("model-controls").addEventListener("change", defaultsAllowed);
$("budget-form").onsubmit = saveBudgets;
$("save-models").onclick = saveModels;
$("save-tags").onclick = saveRequiredTags;
$("add-tag").onclick = () => {
  const row = tagRow();
  $("tag-rows").append(row);
  noTags();
  changed(row);
  row.querySelector("input").focus();
};
$("add-budget").onclick = () => {
  const row = budgetRow("", {}, budgetDefaults);
  $("budget-rows").append(row);
  changed(row);
  row.querySelector("input").focus();
};
$("reset-everyone").onclick = () => reset("*");
$("download").onclick = () =>
  pressed($("download"), "Preparing the installer…", () => download("install.sh", "install-claude-code.sh")).catch(
    (e) => message(e.message),
  );
$("download-managed").onclick = () =>
  pressed($("download-managed"), "Preparing…", () => download("managed-settings.json", "managed-settings.json")).catch(
    (e) => message(e.message),
  );
show(recall());
