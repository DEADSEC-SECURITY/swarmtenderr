"use strict";
const $ = (id) => document.getElementById(id);
const state = {
  user: null,
  csrf: "",
  status: null,
  statistics: null,
  activity: { actions: [], samples: [], audit: [] },
  view: "overview",
  chartPeriod: "daily",
  activityType: "actions",
  torrentPage: 1,
  historyPage: 1,
  historyQuery: "",
  settings: null,
  settingsTab: "general",
  users: [],
  selectedTorrent: null,
  editingUser: null,
  passwordUser: null,
  socket: null,
  reconnect: 1000,
  loggingOut: false,
};
const names = {
  overview: "Overview",
  torrents: "Torrents",
  history: "Completion history",
  activity: "Activity",
  settings: "Settings",
  users: "Users",
};
const seedingStates = new Set(["uploading", "stalledUP", "forcedUP"]);
function node(tag, text, cls) {
  const n = document.createElement(tag);
  if (text !== null && text !== undefined) n.textContent = text;
  if (cls) n.className = cls;
  return n;
}
function bytes(value) {
  if (!Number.isFinite(value) || value < 0) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i++;
  }
  return `${value.toFixed(i && value < 100 ? 1 : 0)} ${units[i]}`;
}
function eta(value) {
  if (value == null) return "Unknown";
  if (value < 60) return "< 1 min";
  if (value < 3600) return `${Math.ceil(value / 60)} min`;
  if (value < 86400) return `${(value / 3600).toFixed(1)} hr`;
  return `${(value / 86400).toFixed(1)} days`;
}
function zone() {
  return state.statistics?.timezone || state.status?.timezone || "UTC";
}
function date(value, full = false) {
  if (!value) return "Never";
  try {
    return new Date(value * 1000).toLocaleString(
      [],
      full
        ? {
            timeZone: zone(),
            year: "numeric",
            month: "short",
            day: "numeric",
            hour: "numeric",
            minute: "2-digit",
          }
        : {
            timeZone: zone(),
            month: "short",
            day: "numeric",
            hour: "numeric",
            minute: "2-digit",
          },
    );
  } catch {
    return new Date(value * 1000).toISOString();
  }
}
function count(value) {
  return value == null ? "—" : Number(value).toLocaleString();
}
function definitions(target, pairs) {
  target.replaceChildren();
  for (const [label, value] of pairs)
    target.append(node("dt", label), node("dd", value));
}
function badge(text, kind = "") {
  return node("span", text, "badge " + kind);
}
function message(target, text, error = false) {
  target.textContent = text || "";
  target.classList.toggle("error", error);
}
function toast(text) {
  $("toast").textContent = text;
  $("toast").hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => ($("toast").hidden = true), 6000);
}
async function api(path, options = {}) {
  const headers = { ...options.headers };
  if (options.body && typeof options.body !== "string") {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }
  if (options.method && options.method !== "GET")
    headers["X-CSRF-Token"] = state.csrf;
  const response = await fetch(path, {
    credentials: "same-origin",
    cache: "no-store",
    ...options,
    headers,
  });
  let value;
  try {
    value = await response.json();
  } catch {
    throw new Error("The server returned an unreadable response.");
  }
  if (!response.ok) {
    const error = new Error(
      value.error || `Request failed (${response.status})`,
    );
    error.status = response.status;
    if (response.status === 401 && state.user && !path.endsWith("/login"))
      showLogin("Your session expired. Sign in again.");
    throw error;
  }
  return value;
}
function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const dark = theme === "dark";
  $("theme-toggle").textContent = dark ? "☀" : "☾";
  $("theme-toggle").setAttribute(
    "aria-label",
    dark ? "Switch to light mode" : "Switch to dark mode",
  );
}
try {
  setTheme(localStorage.getItem("swarmtenderr-theme") || "dark");
} catch {
  setTheme("dark");
}
$("theme-toggle").addEventListener("click", () => {
  const theme =
    document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  setTheme(theme);
  try {
    localStorage.setItem("swarmtenderr-theme", theme);
  } catch {}
});
function showLogin(reason = "") {
  state.loggingOut = true;
  state.socket?.close();
  state.user = null;
  state.csrf = "";
  state.status = null;
  state.statistics = null;
  state.settings = null;
  document.querySelectorAll("dialog[open]").forEach((d) => d.close());
  $("application").hidden = true;
  $("login-screen").hidden = false;
  $("login-password").value = "";
  message($("login-error"), reason, !!reason);
}
async function authOptions() {
  const result = await api("/api/v1/auth/options");
  $("login-demo").hidden = !result.demo;
  $("sso-login").hidden = !result.sso_enabled;
  $("sso-login-link").textContent = result.sso_label;
  $("login-version").textContent = "v" + result.version;
  return result;
}
async function enter(session) {
  state.user = session.user;
  state.csrf = session.csrf;
  state.loggingOut = false;
  $("login-screen").hidden = true;
  $("application").hidden = false;
  $("account-name").textContent = state.user.display_name;
  $("account-role").textContent = state.user.role;
  $("account-avatar").textContent = state.user.display_name
    .slice(0, 1)
    .toUpperCase();
  document
    .querySelectorAll(".admin-only")
    .forEach((n) => (n.hidden = state.user.role !== "admin"));
  await refresh();
  navigate("overview");
  connectLive();
}
$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  message($("login-error"), "");
  try {
    await enter(
      await api("/api/v1/auth/login", {
        method: "POST",
        body: {
          username: $("login-username").value.trim(),
          password: $("login-password").value,
        },
      }),
    );
    $("login-password").value = "";
  } catch (error) {
    message($("login-error"), error.message, true);
  } finally {
    button.disabled = false;
  }
});
async function refresh() {
  if (!state.user) return;
  $("refresh").disabled = true;
  try {
    const [status, statistics, activity] = await Promise.all([
      api("/api/v1/status"),
      api("/api/v1/statistics"),
      api("/api/v1/activity"),
    ]);
    state.status = status;
    state.statistics = statistics;
    state.activity = activity;
    render();
  } catch (error) {
    if (state.user) {
      $("notice").hidden = false;
      $("notice").textContent = error.message;
    }
  } finally {
    $("refresh").disabled = false;
  }
}
function connectLive() {
  if (!state.user || state.loggingOut) return;
  const socket = new WebSocket(
    `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/api/v1/live`,
  );
  state.socket = socket;
  socket.addEventListener("open", () => {
    state.reconnect = 1000;
  });
  socket.addEventListener("message", (event) => {
    let value;
    try {
      value = JSON.parse(event.data);
    } catch {
      return;
    }
    if (value.type === "snapshot") {
      const previousStatus = state.status;
      state.status = value.status;
      state.statistics = value.statistics;
      state.activity = value.activity;
      render();
      if (
        state.view === "history" &&
        (previousStatus?.completion_revision !==
          state.status.completion_revision ||
          previousStatus?.configuration_revision !==
            state.status.configuration_revision)
      )
        loadHistory().catch((e) => toast(e.message));
      if (
        state.view === "users" &&
        previousStatus?.activity_revision !== state.status.activity_revision
      )
        loadUsers().catch((e) => toast(e.message));
      if (
        state.settings &&
        state.settings.revision !== value.status.configuration_revision
      )
        $("settings-conflict").hidden = false;
    }
  });
  socket.addEventListener("close", (event) => {
    if (state.socket !== socket || state.loggingOut) return;
    if (event.code === 4401) {
      showLogin("Your session expired or access changed. Sign in again.");
      return;
    }
    $("connection").textContent = "Live updates disconnected. Reconnecting…";
    setTimeout(connectLive, state.reconnect);
    state.reconnect = Math.min(30000, state.reconnect * 2);
  });
  socket.addEventListener("error", () => socket.close());
}
function navigate(view) {
  if (!state.user) return;
  if (["settings", "users"].includes(view) && state.user.role !== "admin")
    return;
  state.view = view;
  document
    .querySelectorAll(".view")
    .forEach((n) => (n.hidden = n.id !== "view-" + view));
  document
    .querySelectorAll("[data-view]")
    .forEach((n) => n.classList.toggle("selected", n.dataset.view === view));
  $("page-title").textContent = names[view];
  if (view === "history") loadHistory().catch((e) => toast(e.message));
  if (view === "users") loadUsers().catch((e) => toast(e.message));
  if (view === "settings" && !state.settings)
    loadSettings().catch((e) => toast(e.message));
  render();
}
document
  .querySelectorAll("[data-view]")
  .forEach((n) => n.addEventListener("click", () => navigate(n.dataset.view)));
document
  .querySelectorAll("[data-open-view]")
  .forEach((n) =>
    n.addEventListener("click", () => navigate(n.dataset.openView)),
  );
$("refresh").addEventListener("click", refresh);
function classification(row) {
  if (row.progress >= 1)
    return seedingStates.has(row.state)
      ? ["Seeding", "good", "seeding"]
      : ["Complete", "", "complete"];
  if (row.state === "metaDL") return ["Metadata", "warning", "metadata"];
  if (row.state === "queuedDL") return ["Queued", "", "queued"];
  if (row.state === "stalledDL") return ["Stalled", "warning", "stalled"];
  if (["stoppedDL", "pausedDL"].includes(row.state))
    return ["Stopped", "", "stopped"];
  if (row.state === "error" || row.state === "missingFiles")
    return ["Error", "error", "error"];
  if (row.download_speed > 0) return ["Downloading", "info", "downloading"];
  return [row.state, "", "other"];
}
function render() {
  if (!state.user || !state.status) return;
  const focusedKey = document.activeElement?.dataset?.focusKey;
  const status = state.status,
    snapshot = status.snapshot,
    rows = snapshot?.torrents || [],
    stats = state.statistics;
  if (status.user) {
    state.user = status.user;
    $("account-name").textContent = state.user.display_name;
    $("account-role").textContent = state.user.role;
    $("account-avatar").textContent = state.user.display_name
      .slice(0, 1)
      .toUpperCase();
  }
  const trouble =
    status.error ||
    (status.stale
      ? "Client observations are stale. Check the connection in Settings."
      : "");
  $("notice").hidden = !trouble;
  $("notice").textContent = trouble;
  $("demo-badge").hidden = !status.demo;
  $("version").textContent = "v" + status.version;
  $("nav-count").textContent = rows.length;
  $("client-status").textContent = status.demo
    ? "Demo client"
    : status.stale || status.error
      ? "Client unavailable"
      : "qBittorrent connected";
  $("client-dot").className =
    "status-dot " + (status.stale || status.error ? "bad" : "good");
  updateFreshness();
  if (stats) {
    $("completed-today").textContent = count(stats.today.count);
    $("completed-month").textContent = count(stats.month.count);
    $("payload-month").textContent = bytes(stats.month.bytes);
    const previousDay = stats.comparison?.previous_day || stats.daily.at(-2);
    const previousMonth =
      stats.comparison?.previous_month || stats.monthly.at(-2);
    $("today-note").textContent =
      previousDay.count == null
        ? "No prior-day coverage"
        : `Yesterday ${count(previousDay.count)}${previousDay.partial ? " · partial coverage" : ""}`;
    $("month-note").textContent =
      previousMonth.count == null
        ? "No prior-month coverage"
        : `Last month ${count(previousMonth.count)}${previousMonth.partial ? " · partial coverage" : ""}`;
    $("today-note").title = stats.today.partial
      ? "Today's coverage is partial"
      : "Today is a calendar period in progress";
    $("month-note").title = stats.month.partial
      ? "This month's coverage is partial"
      : "This month is a calendar period in progress";
    renderCategories(stats.categories);
    $("coverage-note").textContent = coverageText();
    $("history-coverage").textContent = coverageText();
    renderCompletionChart();
  }
  $("download-speed").textContent = bytes(snapshot?.download_speed) + "/s";
  $("upload-speed").textContent =
    "Upload " + bytes(snapshot?.upload_speed) + "/s";
  renderQueueSummary(rows);
  renderTransferChart();
  renderActive(rows);
  renderTorrents(rows);
  renderActivity();
  renderOptimizer(snapshot?.optimizer);
  if ($("details-dialog").open) renderDetails();
  if (focusedKey) {
    const target = [...document.querySelectorAll("[data-focus-key]")].find(element => element.dataset.focusKey === focusedKey && element.getClientRects().length);
    target?.focus({preventScroll:true});
  }
}
function updateFreshness() {
  if (!state.status) return;
  const stamp = state.status.snapshot?.observed_at;
  if (!stamp) {
    $("connection").textContent = "Waiting for the first client observation";
    return;
  }
  const age = Math.max(0, Math.floor(Date.now() / 1000 - stamp));
  $("connection").textContent =
    (state.status.stale || state.status.error ? "Stale observation" : "Live") +
    " · updated " +
    (age < 60 ? `${age}s` : `${Math.floor(age / 60)}m`) +
    " ago";
}
setInterval(() => {
  if (state.socket?.readyState === WebSocket.OPEN) updateFreshness();
}, 1000);
function coverageText() {
  const coverage = state.statistics?.coverage;
  if (!coverage?.since)
    return "Completion collection starts with the first successful client observation.";
  return `Collection began ${date(coverage.since, true)}. Older surviving client records are included; previously removed torrents cannot be reconstructed. Timezone: ${zone()}.`;
}
function renderCategories(categories) {
  $("category-panel").hidden = !categories.length;
  const fragment = document.createDocumentFragment();
  for (const category of categories.slice(0, 8)) {
    const button = node("button", null, "category-row");
    button.type = "button";
    button.dataset.focusKey = "category:" + category.category;
    button.append(
      node("strong", category.category || "Uncategorized"),
      node("span", count(category.count) + " completed"),
      node("span", bytes(category.bytes)),
    );
    button.addEventListener("click", () => {
      $("history-search").value = "";
      $("history-category").value = category.category;
      $("history-uncategorized").checked = !category.category;
      $("history-category").disabled = !category.category;
      const start = state.statistics.month.period;
      $("history-from").value = start;
      $("history-to").value = "";
      const query = new URLSearchParams({ from: start });
      query.set(
        category.category ? "category" : "uncategorized",
        category.category || "1",
      );
      state.historyQuery = "&" + query;
      state.historyPage = 1;
      navigate("history");
    });
    fragment.append(button);
  }
  $("category-breakdown").replaceChildren(fragment);
}
function renderQueueSummary(rows) {
  const values = [
    ["Total", rows.length, "all"],
    [
      "Downloading",
      rows.filter((r) => classification(r)[2] === "downloading").length,
      "downloading",
    ],
    ["Queued", rows.filter((r) => r.state === "queuedDL").length, "queued"],
    ["Stalled", rows.filter((r) => r.state === "stalledDL").length, "stalled"],
    [
      "Seeding",
      rows.filter((r) => classification(r)[2] === "seeding").length,
      "seeding",
    ],
  ];
  $("queue-summary").replaceChildren();
  for (const [label, value, filter] of values) {
    const button = node("button");
    button.dataset.focusKey = "queue:" + filter;
    button.append(node("strong", count(value)), node("span", label));
    button.addEventListener("click", () => {
      $("torrent-filter").value = filter;
      state.torrentPage = 1;
      navigate("torrents");
    });
    $("queue-summary").append(button);
  }
}
const svgNS = "http://www.w3.org/2000/svg";
function svg(tag, attrs = {}, text) {
  const value = document.createElementNS(svgNS, tag);
  for (const [key, item] of Object.entries(attrs))
    value.setAttribute(key, String(item));
  if (text !== undefined) value.textContent = text;
  return value;
}
function chartBase(target, max, unit = "") {
  target.replaceChildren();
  const css = getComputedStyle(target),
    width = Math.max(
      280,
      target.clientWidth -
        parseFloat(css.paddingLeft) -
        parseFloat(css.paddingRight),
    ),
    left = unit ? 68 : 35,
    plotWidth = width - left - 16;
  const s = svg("svg", {
    viewBox: `0 0 ${width} 210`,
    preserveAspectRatio: "none",
  });
  s.dataset.plotWidth = plotWidth;
  s.dataset.plotLeft = left;
  for (let i = 0; i <= 3; i++) {
    const y = 15 + i * 55;
    s.append(
      svg("line", {
        x1: left,
        y1: y,
        x2: width - 16,
        y2: y,
        class: "chart-grid-line",
      }),
    );
    s.append(
      svg(
        "text",
        { x: left - 9, y: y + 4, "text-anchor": "end", class: "chart-label" },
        unit ? bytes((max * (3 - i)) / 3) : Math.round((max * (3 - i)) / 3),
      ),
    );
  }
  target.append(s);
  return s;
}
function renderCompletionChart() {
  if (!state.statistics) return;
  const values = state.statistics[state.chartPeriod],
    max = Math.max(3, ...values.map((v) => v.count || 0));
  const s = chartBase($("completion-chart"), max),
    step = Number(s.dataset.plotWidth) / values.length,
    left = Number(s.dataset.plotLeft);
  values.forEach((value, i) => {
    const h =
        value.count == null
          ? 3
          : Math.max(value.count ? 2 : 1, (value.count / max) * 165),
      bar = svg("rect", {
        x: left + i * step,
        y: 180 - h,
        width: Math.max(3, step - 5),
        height: h,
        rx: 2,
        class:
          "completion-bar " +
          (value.count == null ? "unknown" : value.partial ? "partial" : ""),
      });
    bar.append(
      svg(
        "title",
        {},
        `${value.period}: ${value.count == null ? "No historical coverage" : value.count + " completed"}${value.partial ? " (partial coverage)" : ""}`,
      ),
    );
    s.append(bar);
    if (
      i === 0 ||
      i === values.length - 1 ||
      i === Math.floor(values.length / 2)
    ) {
      s.append(
        svg(
          "text",
          {
            x: left + i * step + step / 2,
            y: 203,
            "text-anchor": "middle",
            class: "chart-label",
          },
          state.chartPeriod === "monthly"
            ? value.period.slice(0, 7)
            : value.period.slice(5),
        ),
      );
    }
  });
  $("completion-chart-caption").textContent =
    (state.chartPeriod === "daily" ? "Last 30 days" : "Last 12 months") +
    " · " +
    zone();
  const total = values.reduce((n, v) => n + (v.count || 0), 0);
  $("completion-legend").replaceChildren(
    node("span", count(total) + " observed completions"),
    node("span", "Faded bars: partial coverage"),
  );
}
document.querySelectorAll("[data-chart-period]").forEach((b) =>
  b.addEventListener("click", () => {
    state.chartPeriod = b.dataset.chartPeriod;
    document
      .querySelectorAll("[data-chart-period]")
      .forEach((n) => n.classList.toggle("selected", n === b));
    renderCompletionChart();
  }),
);
function renderTransferChart() {
  const values = state.activity.samples || [];
  if (values.length < 2) {
    $("transfer-chart").replaceChildren(
      node("div", "Waiting for rate observations", "chart-empty"),
    );
    return;
  }
  const max = Math.max(1024, ...values.flatMap((v) => [v.download, v.upload])),
    s = chartBase($("transfer-chart"), max, "bytes"),
    plotWidth = Number(s.dataset.plotWidth),
    left = Number(s.dataset.plotLeft);
  for (const field of ["download", "upload"]) {
    const points = values.map(
      (v, i) =>
        `${left + (i / (values.length - 1)) * plotWidth},${180 - (v[field] / max) * 165}`,
    );
    s.append(
      svg("path", { d: "M" + points.join(" L"), class: field + "-line" }),
    );
  }
  $("transfer-legend").replaceChildren(
    node("span", date(values[0].at)),
    node("span", date(values.at(-1).at)),
  );
}
function nameCell(row) {
  const td = node("td", null, "name-cell");
  td.append(
    node("strong", row.name),
    node("small", row.category || "Uncategorized"),
  );
  return td;
}
function progressCell(row) {
  const td = node("td", null, "progress-cell");
  td.append(node("span", (row.progress * 100).toFixed(1) + "%"));
  const progress = node("progress");
  progress.max = 1;
  progress.value = row.progress;
  progress.setAttribute("aria-label", row.name + " progress");
  td.append(progress);
  return td;
}
function detailsCell(row) {
  const td = node("td"),
    button = node("button", "›", "icon-button");
  button.dataset.torrent = row.hash;
  button.dataset.focusKey = "torrent:" + row.hash;
  button.setAttribute("aria-label", "Details for " + row.name);
  button.addEventListener("click", () => openDetails(row));
  td.append(button);
  return td;
}
function renderActive(rows) {
  const active = rows
      .filter((r) => r.progress < 1 && r.download_speed > 0)
      .sort((a, b) => b.download_speed - a.download_speed)
      .slice(0, 8),
    fragment = document.createDocumentFragment();
  for (const row of active) {
    const tr = node("tr");
    tr.append(
      nameCell(row),
      progressCell(row),
      node("td", bytes(row.download_speed) + "/s"),
      node("td", bytes(row.remaining_bytes)),
      node("td", eta(row.eta_seconds)),
      detailsCell(row),
    );
    fragment.append(tr);
  }
  $("active-rows").replaceChildren(fragment);
  $("active-empty").hidden = !!active.length;
}
function renderTorrents(rows) {
  const category = $("torrent-category").value,
    categories = [
      ...new Set(rows.map((r) => r.category).filter(Boolean)),
    ].sort(),
    previous = [...$("torrent-category").options].slice(1).map((n) => n.value);
  if (JSON.stringify(categories) !== JSON.stringify(previous)) {
    const options = [node("option", "All categories")];
    options[0].value = "";
    for (const value of categories) {
      const option = node("option", value);
      option.value = value;
      options.push(option);
    }
    $("torrent-category").replaceChildren(...options);
    $("torrent-category").value = category;
  }
  const search = $("torrent-search").value.trim().toLowerCase(),
    filter = $("torrent-filter").value;
  let filtered = rows.filter(
    (r) =>
      (!search || r.name.toLowerCase().includes(search)) &&
      (!category || r.category === category) &&
      (filter === "all" || classification(r)[2] === filter),
  );
  const sort = $("torrent-sort").value;
  filtered.sort((a, b) =>
    sort === "name"
      ? a.name.localeCompare(b.name)
      : sort === "speed"
        ? b.download_speed - a.download_speed
        : sort === "progress"
          ? b.progress - a.progress
          : (a.priority < 0 ? 1e9 : a.priority) -
            (b.priority < 0 ? 1e9 : b.priority),
  );
  state.torrentPage = Math.max(
    1,
    Math.min(state.torrentPage, Math.ceil(filtered.length / 50) || 1),
  );
  const fragment = document.createDocumentFragment();
  for (const row of filtered.slice(
    (state.torrentPage - 1) * 50,
    state.torrentPage * 50,
  )) {
    const tr = node("tr"),
      status = node("td"),
      [label, kind] = classification(row);
    status.append(badge(label, kind));
    tr.append(
      nameCell(row),
      status,
      progressCell(row),
      node("td", row.download_speed ? bytes(row.download_speed) + "/s" : "—"),
      node("td", row.upload_speed ? bytes(row.upload_speed) + "/s" : "—"),
      node("td", row.progress >= 1 ? "Complete" : eta(row.eta_seconds)),
      node("td", `${row.seeds} / ${row.peers}`),
      node("td", row.priority < 0 ? "—" : row.priority),
      detailsCell(row),
    );
    fragment.append(tr);
  }
  $("torrent-rows").replaceChildren(fragment);
  $("table-count").textContent = count(rows.length);
  $("torrent-summary").textContent =
    `${count(filtered.length)} matching torrents`;
  $("torrent-empty").hidden = !!filtered.length;
  $("torrent-page-label").textContent =
    `Page ${state.torrentPage} of ${Math.ceil(filtered.length / 50) || 1} · ${count(filtered.length)} torrents`;
  $("torrent-prev").disabled = state.torrentPage === 1;
  $("torrent-next").disabled = state.torrentPage * 50 >= filtered.length;
}
for (const id of [
  "torrent-search",
  "torrent-category",
  "torrent-filter",
  "torrent-sort",
])
  $(id).addEventListener(id === "torrent-search" ? "input" : "change", () => {
    state.torrentPage = 1;
    renderTorrents(state.status?.snapshot?.torrents || []);
  });
$("torrent-prev").addEventListener("click", () => {
  state.torrentPage--;
  renderTorrents(state.status.snapshot.torrents);
});
$("torrent-next").addEventListener("click", () => {
  state.torrentPage++;
  renderTorrents(state.status.snapshot.torrents);
});
function renderOptimizer(engine) {
  $("optimizer-panel").hidden = !!state.status?.demo || !engine;
  if (!engine || state.status?.demo) return;
  $("optimizer-mode").textContent =
    engine.owner === "swarmtenderr"
      ? "Standalone policy · " + (engine.mode || "observe")
      : "External controller · observation only";
  $("optimizer-state").textContent = engine.stale
    ? "Stale"
    : !engine.connected
      ? "Unavailable"
      : engine.mode === "enforce"
        ? "Enforce"
        : "Observe";
  $("optimizer-state").className =
    "badge " + (engine.stale || !engine.connected ? "warning" : "good");
  $("optimizer-explanation").textContent = engine.reason || "";
  $("optimizer-explanation").hidden = !engine.reason;
  $("optimizer-capacity").hidden = !engine.connected;
  if (!engine.connected) return;
  const capacity = engine.capacity || {};
  $("optimizer-capacity").replaceChildren();
  for (const [label, value] of [
    [
      "Download slots",
      `${capacity.applied_slots ?? "—"} allowed / ${capacity.requested_slots ?? "—"} requested`,
    ],
    [
      "Download reservation",
      `${bytes(capacity.ssd_reserved_bytes)} / ${bytes(capacity.ssd_budget_bytes)}`,
    ],
    [
      "Import reservation",
      capacity.library_capacity_applicable === false
        ? "No separate import destination"
        : `${bytes(capacity.library_reserved_bytes)} / ${bytes(capacity.library_budget_bytes)}`,
    ],
  ]) {
    const n = node("div");
    n.append(node("small", label), node("strong", value));
    $("optimizer-capacity").append(n);
  }
}
function openDetails(row) {
  state.selectedTorrent = row;
  $("action-result").textContent = "";
  renderDetails();
  $("details-dialog").showModal();
}
function renderDetails() {
  if (!state.selectedTorrent) return;
  const current = state.status?.snapshot?.torrents.find(
      (r) => r.hash === state.selectedTorrent.hash,
    ),
    row = current || state.selectedTorrent;
  state.selectedTorrent = row;
  $("detail-name").textContent = row.name;
  const [label, kind] = classification(row);
  $("detail-state").replaceChildren(badge(label, kind));
  definitions($("detail-values"), [
    ["Progress", (row.progress * 100).toFixed(1) + "%"],
    ["Size", bytes(row.size)],
    ["Remaining", bytes(row.remaining_bytes)],
    [
      "Download / upload",
      bytes(row.download_speed) + "/s / " + bytes(row.upload_speed) + "/s",
    ],
    ["Seeds / peers", `${row.seeds} / ${row.peers}`],
    [
      "Queue position",
      row.priority < 0 ? "Outside download queue" : row.priority,
    ],
    ["ETA", row.progress >= 1 ? "Complete" : eta(row.eta_seconds)],
    ...(row.trial_until > Date.now() / 1000
      ? [["Trial ends", date(row.trial_until, true)]]
      : []),
    ...(row.cooldown_until > Date.now() / 1000
      ? [["Retry after", date(row.cooldown_until, true)]]
      : []),
  ]);
  const policy = state.status?.snapshot?.optimizer?.connected;
  $("detail-policy").hidden = !policy;
  $("detail-policy").textContent = policy
    ? `${row.decision}: ${row.reason}`
    : "";
  const recentActions = state.activity.actions.filter(action => action.torrent === row.hash).slice(0,5);
  const actions = $("detail-actions");
  actions.replaceChildren();
  if (!recentActions.length) actions.append(node("p","No actions in recent activity.","muted"));
  for (const action of recentActions){
    const item = node("div",null,"detail-action");
    item.append(node("time",date(action.at)),node("span",action.action),badge(action.outcome,action.outcome === "confirmed" ? "good" : action.outcome === "uncertain" ? "warning" : ""));
    actions.append(item);
  }
  let reason = "";
  if (!current) reason = "This torrent is no longer in the client.";
  else if (row.protected)
    reason = "Completed, force-started and keep-tagged torrents are protected.";
  else if (state.user.role === "viewer")
    reason = "Your viewer account cannot change torrents.";
  else if (state.status.stale)
    reason = "Controls are unavailable while observations are stale.";
  else if (!state.status.snapshot.controls)
    reason = state.status.demo
      ? "Client operations are disabled for demo data."
      : "Torrent controls are disabled by this installation’s settings.";
  $("control-note").textContent =
    reason ||
    "Actions are recorded before submission. The next observation confirms supported state changes.";
  document
    .querySelectorAll("[data-action]")
    .forEach((b) => (b.disabled = !!reason));
}
document.querySelectorAll("[data-action]").forEach((button) =>
  button.addEventListener("click", async () => {
    const hash = state.selectedTorrent?.hash;
    if (!hash) return;
    document
      .querySelectorAll("[data-action]")
      .forEach((b) => (b.disabled = true));
    try {
      const result = await api(`/api/v1/torrents/${hash}/actions`, {
        method: "POST",
        body: { action: button.dataset.action },
      });
      message($("action-result"), result.message);
    } catch (error) {
      message($("action-result"), error.message, true);
    } finally {
      await refresh();
    }
  }),
);
async function loadHistory() {
  const result = await api(
      `/api/v1/completions?page=${state.historyPage}&limit=50${state.historyQuery}`,
    ),
    fragment = document.createDocumentFragment();
  for (const row of result.rows) {
    const tr = node("tr");
    tr.append(
      nameCell(row),
      node("td", row.category || "Uncategorized"),
      node("td", date(row.completed, true)),
      node("td", bytes(row.bytes)),
    );
    const source = node("td");
    source.append(
      badge(
        row.source === "backfill"
          ? "Surviving history"
          : row.source === "demo"
            ? "Demo"
            : "Observed",
      ),
    );
    tr.append(source);
    fragment.append(tr);
  }
  $("history-rows").replaceChildren(fragment);
  $("history-empty").hidden = !!result.rows.length;
  $("history-page-label").textContent =
    `Page ${result.page} of ${Math.ceil(result.total / result.limit) || 1} · ${count(result.total)} completions`;
  $("history-prev").disabled = result.page === 1;
  $("history-next").disabled = result.page * result.limit >= result.total;
  $("export-history").textContent =
    result.total > 10000 ? "Export first 10,000" : "Export CSV";
  $("export-history").href =
    "/api/v1/completions.csv" +
    (state.historyQuery ? "?" + state.historyQuery.slice(1) : "");
}
$("history-filter").addEventListener("submit", (event) => {
  event.preventDefault();
  const q = new URLSearchParams();
  if ($("history-search").value) q.set("search", $("history-search").value);
  if ($("history-uncategorized").checked) q.set("uncategorized", "1");
  else if ($("history-category").value)
    q.set("category", $("history-category").value.trim());
  if ($("history-from").value) q.set("from", $("history-from").value);
  if ($("history-to").value) q.set("to", $("history-to").value);
  state.historyQuery = q.size ? "&" + q : "";
  state.historyPage = 1;
  loadHistory().catch((e) => toast(e.message));
});
$("history-prev").addEventListener("click", () => {
  state.historyPage--;
  loadHistory().catch((e) => toast(e.message));
});
$("history-next").addEventListener("click", () => {
  state.historyPage++;
  loadHistory().catch((e) => toast(e.message));
});
function renderActivity() {
  const target = $("activity-list");
  target.replaceChildren();
  const events =
    state.activityType === "audit"
      ? state.activity.audit
      : state.activity.actions;
  if (!events?.length) {
    target.append(node("p", "No activity recorded.", "empty"));
    return;
  }
  for (const event of events) {
    const row = node("div", null, "activity-row"),
      body = node("div");
    if (state.activityType === "audit") {
      body.append(
        node("strong", event.event.replaceAll(".", " ")),
        node(
          "small",
          `${event.actor} · ${Object.entries(event.detail)
            .map(
              ([k, v]) =>
                `${k}: ${Array.isArray(v) ? v.join(", ") : typeof v === "object" ? JSON.stringify(v) : v}`,
            )
            .join(" · ")}`,
        ),
      );
    } else {
      const torrent = state.status?.snapshot?.torrents.find(
        (r) => r.hash === event.torrent,
      );
      body.append(
        node(
          "strong",
          event.action.charAt(0).toUpperCase() + event.action.slice(1),
        ),
        node("small", torrent?.name || event.torrent),
      );
    }
    row.append(node("time", date(event.at, true)), body);
    if (state.activityType === "actions")
      row.append(
        badge(
          event.outcome,
          event.outcome === "confirmed"
            ? "good"
            : ["uncertain", "pending"].includes(event.outcome)
              ? "warning"
              : event.outcome === "rejected"
                ? "error"
                : "",
        ),
      );
    target.append(row);
  }
}
document.querySelectorAll("[data-activity]").forEach((b) =>
  b.addEventListener("click", () => {
    state.activityType = b.dataset.activity;
    document
      .querySelectorAll("[data-activity]")
      .forEach((n) => n.classList.toggle("selected", n === b));
    renderActivity();
  }),
);
const settingSections = {
  general: {
    title: "General",
    description: "Observation cadence and completion accounting.",
    fields: [
      [
        "timezone",
        "Timezone",
        "text",
        "Used for daily and monthly statistics, dates and exports.",
      ],
      [
        "poll_seconds",
        "Observation interval (seconds)",
        "number",
        "How often the server reads qBittorrent. WebSocket updates are pushed to open dashboards.",
        1,
        3600,
      ],
      [
        "retention_days",
        "Rate history retention (days)",
        "number",
        "Completion records are retained independently of rate samples.",
        1,
        365,
      ],
    ],
  },
  client: {
    title: "qBittorrent",
    description:
      "Credentials stay on the server and are never returned to the browser.",
    fields: [
      [
        "qbit_url",
        "Client URL",
        "url",
        "HTTP(S) origin reachable from this installation, such as http://qbittorrent:8080.",
      ],
      [
        "qbit_username",
        "Client username",
        "text",
        "The existing qBittorrent WebUI account.",
      ],
      [
        "qbit_password",
        "Client password",
        "password",
        "Enter a replacement, or leave blank to keep the configured password.",
      ],
    ],
  },
  optimizer: {
    title: "Optimization",
    description:
      "Observe proposed decisions before enabling enforcement. Protected torrents remain excluded.",
    fields: [
      [
        "policy_mode",
        "Policy mode",
        "select",
        "Enforcement requires explicit managed categories.",
        [
          ["observe", "Observe"],
          ["enforce", "Enforce"],
        ],
      ],
      [
        "controls_enabled",
        "Manual torrent controls",
        "checkbox",
        "Allows administrators and operators to use permitted pause, resume, priority and reannounce actions.",
      ],
      [
        "managed_categories",
        "Managed categories",
        "list",
        "Comma-separated qBittorrent categories. Empty means no automatic ownership.",
      ],
      [
        "excluded_tags",
        "Excluded tags",
        "list",
        "Comma-separated tags excluded from automatic scheduling.",
      ],
      [
        "download_slots",
        "Requested download slots",
        "number",
        "The optimizer may allow fewer slots when a complete batch does not fit.",
        1,
        100,
      ],
      [
        "minimum_free_gib",
        "Download free-space reserve (GiB)",
        "number",
        "Space kept outside automatic download reservations.",
        1,
        100000,
      ],
      [
        "work_budget_gib",
        "Download working budget (GiB)",
        "number",
        "Maximum wanted data charged to managed work.",
        1,
        100000,
      ],
      [
        "library_reserve_gib",
        "Import destination reserve (GiB)",
        "number",
        "Used when a separate import-capacity probe is configured.",
        1,
        100000,
      ],
    ],
  },
  sso: {
    title: "Single sign-on",
    description:
      "OpenID Connect with Authentik or another compatible identity provider. Local administrator login remains available.",
    fields: [
      [
        "sso_enabled",
        "Enable SSO",
        "checkbox",
        "Adds an SSO option to the sign-in page.",
      ],
      [
        "sso_label",
        "Sign-in button label",
        "text",
        "For example, Sign in with Authentik.",
      ],
      [
        "sso_issuer",
        "Issuer URL",
        "url",
        "Paste the exact issuer URL from the provider’s discovery document.",
      ],
      [
        "sso_client_id",
        "Client ID",
        "text",
        "Register this installation as an OpenID Connect client.",
      ],
      [
        "sso_client_secret",
        "Client secret",
        "password",
        "Leave blank to retain the configured secret.",
      ],
      [
        "sso_allowed_groups",
        "Allowed groups",
        "list",
        "Comma-separated group names. Empty allows any identity accepted by the provider.",
      ],
      [
        "sso_default_role",
        "New SSO account role",
        "select",
        "Administrators can change the role after the account signs in.",
        [
          ["viewer", "Viewer"],
          ["operator", "Operator"],
        ],
      ],
    ],
  },
};
function inputValue(input, type) {
  if (type === "checkbox") return input.checked;
  if (type === "number") return Number(input.value);
  if (type === "list")
    return input.value
      .split(",")
      .map((v) => v.trim())
      .filter(Boolean);
  return input.value.trim();
}
function settingsForm(key) {
  const section = settingSections[key],
    form = node("form", null, "settings-form panel");
  form.dataset.section = key;
  form.append(node("h2", section.title), node("p", section.description));
  if (key === "sso") {
    const uri = node("div", null, "inset");
    uri.append(
      node("strong", "Redirect URI"),
      node("p", state.settings.deployment.public_url + "/auth/oidc/callback"),
    );
    form.append(uri);
  }
  for (const field of section.fields) {
    const [name, label, type, help, options, max] = field,
      wrapper = node("div", null, "settings-field"),
      description = node("div"),
      title = node("label", label, "field-title");
    title.htmlFor = "setting-" + name;
    description.append(title, node("p", help, "field-help"));
    const locked = state.settings.locked.includes(name);
    description.append(
      node(
        "span",
        locked
          ? "Locked by deployment"
          : state.settings.sources[name] === "ui"
            ? "Saved in app"
            : "Deployment default",
        "field-source",
      ),
    );
    let input;
    if (type === "select") {
      input = node("select");
      for (const [value, text] of options) {
        const option = node("option", text);
        option.value = value;
        input.append(option);
      }
    } else {
      input = node("input");
      input.type = type === "list" ? "text" : type;
      if (type === "number") {
        input.min = options;
        input.max = max;
        input.step = 1;
      }
      if (type === "password") {
        input.autocomplete = "new-password";
        input.placeholder = state.settings.secrets[name]
          ? "Configured; leave blank to keep"
          : "Not configured";
      } else input.autocomplete = "off";
    }
    input.id = "setting-" + name;
    input.name = name;
    input.disabled = locked;
    if (type === "checkbox") input.checked = !!state.settings.values[name];
    else if (type === "password") input.value = "";
    else if (type === "list")
      input.value = (state.settings.values[name] || []).join(", ");
    else input.value = state.settings.values[name] ?? "";
    input.dataset.type = type;
    input.dataset.original = JSON.stringify(inputValue(input, type));
    wrapper.append(description, input);
    form.append(wrapper);
  }
  const controls = node("div", null, "button-row"),
    save = node("button", "Save changes", "button primary");
  save.type = "submit";
  save.disabled = section.fields.every((f) =>
    state.settings.locked.includes(f[0]),
  );
  const reset = node("button", "Reset to deployment defaults", "button");
  reset.type = "button";
  reset.disabled = save.disabled;
  reset.addEventListener("click", async () => {
    reset.disabled = true;
    try {
      state.settings = await api("/api/v1/settings/reset", {
        method: "POST",
        body: {
          revision: state.settings.revision,
          keys: section.fields
            .map((f) => f[0])
            .filter((k) => !state.settings.locked.includes(k)),
        },
      });
      form.replaceWith(settingsForm(key));
      toast("Deployment defaults restored");
      $("settings-conflict").hidden = true;
    } catch (error) {
      message(form.querySelector(".form-message"), error.message, true);
    } finally {
      reset.disabled = false;
    }
  });
  controls.append(save, reset);
  if (key === "client" || key === "sso") {
    const test = node(
      "button",
      key === "client" ? "Test connection" : "Check discovery",
      "button",
    );
    test.type = "button";
    test.disabled = state.settings.deployment.demo;
    test.addEventListener("click", async () => {
      test.disabled = true;
      try {
        const values = {};
        for (const name of key === "client"
          ? ["qbit_url", "qbit_username", "qbit_password"]
          : ["sso_issuer", "sso_client_id", "sso_client_secret"]) {
          const input = form.elements.namedItem(name);
          if (input.value)
            values[name] =
              input.type === "password" ? input.value : input.value.trim();
        }
        const result = await api(
          "/api/v1/settings/" +
            (key === "client" ? "test-connection" : "test-sso"),
          { method: "POST", body: values },
        );
        message(form.querySelector(".form-message"), result.message);
      } catch (error) {
        message(form.querySelector(".form-message"), error.message, true);
      } finally {
        test.disabled = state.settings.deployment.demo;
      }
    });
    controls.append(test);
  }
  form.append(controls, node("p", "", "form-message"));
  if (
    state.settings.deployment.demo &&
    (key === "client" || key === "sso" || key === "optimizer")
  )
    form.append(
      node(
        "p",
        "Real client connections, SSO changes and torrent mutations are locked in the demo. General settings and local accounts remain functional.",
        "disabled-hint",
      ),
    );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const changes = {};
    for (const input of form.querySelectorAll("input[name],select[name]")) {
      if (input.disabled) continue;
      const value = inputValue(input, input.dataset.type);
      if (
        JSON.stringify(value) !== input.dataset.original &&
        (input.type !== "password" || value)
      )
        changes[input.name] = value;
    }
    if (!Object.keys(changes).length) {
      message(form.querySelector(".form-message"), "No changes to save.");
      return;
    }
    save.disabled = true;
    try {
      state.settings = await api("/api/v1/settings", {
        method: "PATCH",
        body: { revision: state.settings.revision, changes },
      });
      form.replaceWith(settingsForm(key));
      $("settings-conflict").hidden = true;
      toast("Settings saved");
    } catch (error) {
      message(form.querySelector(".form-message"), error.message, true);
    } finally {
      save.disabled = false;
    }
  });
  form.hidden = key !== state.settingsTab;
  return form;
}
function deploymentPanel() {
  const panel = node("section", null, "settings-readonly panel");
  panel.dataset.section = "deployment";
  panel.append(node("h2", "Deployment"));
  const dl = node("dl", null, "definition-grid");
  definitions(dl, [
    [
      "Database",
      state.settings.deployment.database === "sqlite" ? "SQLite" : "PostgreSQL",
    ],
    ["Public URL", state.settings.deployment.public_url],
    [
      "External controller",
      state.settings.deployment.external_optimizer
        ? "Apollo optimizer"
        : "None",
    ],
    ["Demo data", state.settings.deployment.demo ? "Enabled" : "Disabled"],
  ]);
  panel.append(
    dl,
    node(
      "p",
      "Database backend and public URL are deployment settings. Changing the database requires an explicit migration; changing the URL requires updating proxy and sign-in configuration.",
    ),
    node(
      "p",
      "Integration credentials are encrypted with this installation’s key. Include /data/secret.key when backing up the database. The key is never shown here.",
    ),
  );
  panel.hidden = state.settingsTab !== "deployment";
  return panel;
}
async function loadSettings() {
  state.settings = await api("/api/v1/settings");
  $("settings-conflict").hidden = true;
  $("settings-tabs").replaceChildren();
  $("settings-forms").replaceChildren();
  for (const [key, section] of [
    ...Object.entries(settingSections),
    ["deployment", { title: "Deployment" }],
  ]) {
    const button = node("button", section.title);
    button.type = "button";
    button.classList.toggle("selected", key === state.settingsTab);
    button.addEventListener("click", () => {
      state.settingsTab = key;
      document
        .querySelectorAll("#settings-tabs button")
        .forEach((b) => b.classList.toggle("selected", b === button));
      document
        .querySelectorAll("#settings-forms>[data-section]")
        .forEach((n) => (n.hidden = n.dataset.section !== key));
    });
    $("settings-tabs").append(button);
    if (key !== "deployment") $("settings-forms").append(settingsForm(key));
    else $("settings-forms").append(deploymentPanel());
  }
}
$("reload-settings").addEventListener("click", () =>
  loadSettings().catch((e) => toast(e.message)),
);
async function loadUsers() {
  const result = await api("/api/v1/users");
  state.users = result.users;
  const fragment = document.createDocumentFragment();
  for (const user of result.users) {
    const tr = node("tr"),
      name = node("td", null, "name-cell");
    name.append(
      node("strong", user.display_name),
      node("small", user.username),
    );
    const role = node("td");
    role.append(badge(user.role, user.role === "admin" ? "info" : ""));
    const status = node("td");
    status.append(
      badge(user.active ? "Enabled" : "Disabled", user.active ? "good" : ""),
    );
    const actions = node("td"),
      edit = node("button", "Edit", "button compact");
    edit.setAttribute("aria-label", "Edit " + user.username);
    edit.addEventListener("click", () => editUser(user));
    actions.append(edit);
    tr.append(
      name,
      role,
      node("td", user.provider === "sso" ? "SSO" : "Local password"),
      status,
      node("td", date(user.last_login, true)),
      actions,
    );
    fragment.append(tr);
  }
  $("user-rows").replaceChildren(fragment);
}
function editUser(user = null) {
  state.editingUser = user;
  $("user-dialog-title").textContent = user ? "Edit user" : "Add user";
  $("user-username").value = user?.username || "";
  $("user-username").disabled = !!user;
  $("user-display-name").value = user?.display_name || "";
  $("user-role").value = user?.role || "viewer";
  $("user-password").value = "";
  $("user-password").required = !user;
  $("user-password-label").hidden = !!user;
  $("user-active").checked = user ? !!user.active : true;
  $("user-active-label").hidden = !user;
  $("reset-user-password").hidden = !user || user.provider === "sso";
  message($("user-error"), "");
  $("user-dialog").showModal();
}
$("new-user").addEventListener("click", () => editUser());
$("user-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  try {
    if (state.editingUser) {
      await api("/api/v1/users/" + state.editingUser.id, {
        method: "PATCH",
        body: {
          display_name: $("user-display-name").value,
          role: $("user-role").value,
          active: $("user-active").checked,
        },
      });
    } else {
      await api("/api/v1/users", {
        method: "POST",
        body: {
          username: $("user-username").value.trim(),
          display_name: $("user-display-name").value,
          role: $("user-role").value,
          password: $("user-password").value,
        },
      });
    }
    $("user-dialog").close();
    $("user-password").value = "";
    await loadUsers();
    toast("User saved");
  } catch (error) {
    message($("user-error"), error.message, true);
  } finally {
    button.disabled = false;
  }
});
function passwordDialog(user = null) {
  state.passwordUser = user;
  $("password-dialog-title").textContent = user
    ? "Reset password: " + user.username
    : "Change password";
  $("current-password-label").hidden = !!user;
  $("current-password").required = !user;
  for (const id of ["current-password", "new-password", "confirm-password"])
    $(id).value = "";
  message($("password-error"), "");
  $("password-dialog").showModal();
}
$("reset-user-password").addEventListener("click", () => {
  const user = state.editingUser;
  $("user-dialog").close();
  passwordDialog(user);
});
$("password-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if ($("new-password").value !== $("confirm-password").value) {
    message($("password-error"), "New passwords do not match.", true);
    return;
  }
  const button = event.submitter;
  button.disabled = true;
  try {
    const target = state.passwordUser;
    if (target) {
      await api("/api/v1/users/" + target.id + "/password", {
        method: "POST",
        body: { password: $("new-password").value },
      });
    } else {
      await api("/api/v1/account/password", {
        method: "POST",
        body: {
          current_password: $("current-password").value,
          new_password: $("new-password").value,
        },
      });
    }
    $("password-dialog").close();
    if (!target || target.id === state.user?.id) {
      showLogin("Password changed. Sign in again.");
    } else toast("Password reset and existing sessions revoked");
  } catch (error) {
    message($("password-error"), error.message, true);
  } finally {
    button.disabled = false;
  }
});
$("account-button").addEventListener("click", () => {
  definitions($("account-values"), [
    ["Name", state.user.display_name],
    ["Username", state.user.username],
    ["Role", state.user.role],
    [
      "Sign-in method",
      state.user.provider === "sso" ? "Single sign-on" : "Local password",
    ],
  ]);
  $("change-password").hidden = state.user.provider === "sso";
  $("account-sso-note").hidden = state.user.provider !== "sso";
  $("account-dialog").showModal();
});
$("change-password").addEventListener("click", () => {
  $("account-dialog").close();
  passwordDialog();
});
$("logout").addEventListener("click", async () => {
  try {
    await api("/api/v1/auth/logout", { method: "POST", body: {} });
    showLogin();
  } catch (error) {
    toast(error.message);
  }
});
document.querySelectorAll("[data-close]").forEach((button) =>
  button.addEventListener("click", (event) => {
    event.preventDefault();
    $(button.dataset.close).close();
  }),
);
async function boot() {
  try {
    await authOptions();
    let session;
    try {
      session = await api("/api/v1/me");
    } catch (error) {
      if (error.status !== 401) throw error;
    }
    if (session) {
      await enter(session);
    } else {
      const error = new URLSearchParams(location.search).get("auth_error");
      const errors = {
        sso_disabled: "SSO is not enabled for this installation.",
        sso_unavailable:
          "The SSO provider is unavailable. You can still use a local account.",
        sso_denied:
          "SSO sign-in failed or this identity is not allowed. Retry or use a local account.",
      };
      showLogin(errors[error] || "");
      if (error) history.replaceState({}, "", location.pathname);
    }
  } catch (error) {
    showLogin("Unable to reach the application: " + error.message);
  }
}
boot();

let chartResizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(chartResizeTimer);
  chartResizeTimer = setTimeout(() => {
    if (state.user) {
      renderCompletionChart();
      renderTransferChart();
    }
  }, 100);
});

$("history-uncategorized").addEventListener("change", () => {
  $("history-category").disabled = $("history-uncategorized").checked;
});
