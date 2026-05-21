const API_KEY_STORAGE = "cancellation_scheduler_api_key";

const RESULT_COLUMNS = [
  { key: "cancel_by", label: "Cancel by" },
  { key: "confirmation", label: "Confirmation" },
  { key: "account", label: "Account" },
  { key: "resort", label: "Resort" },
  { key: "checkin", label: "Check-in" },
  { key: "checkout", label: "Check-out" },
  { key: "booking_date", label: "Booking date" },
  { key: "Unit", label: "Unit" },
  { key: "credits", label: "Credits" },
  { key: "rented?", label: "Rented?" },
  { key: "action", label: "Action" },
];

let appSettings = { auto_post: true };

const $ = (sel) => document.querySelector(sel);

function escapeHtml(str) {
  if (str == null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function getApiKey() {
  return $("#apiKey").value.trim() || localStorage.getItem(API_KEY_STORAGE) || "";
}

function apiHeaders() {
  const headers = { "Content-Type": "application/json" };
  const key = getApiKey();
  if (key) headers["X-API-Key"] = key;
  return headers;
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: { ...apiHeaders(), ...options.headers },
  });
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!res.ok) {
    const msg = data?.detail || data?.message || res.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

function log(message, type = "info") {
  const el = $("#activityLog");
  const time = new Date().toLocaleTimeString();
  const prefix = type === "error" ? "✗" : type === "success" ? "✓" : "·";
  el.textContent = `[${time}] ${prefix} ${message}\n` + el.textContent;
}

function showToast(message, isError = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.classList.remove("hidden");
  setTimeout(() => toast.classList.add("hidden"), 4000);
}

function showPanel(name) {
  document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
  document.querySelectorAll(".nav-btn").forEach((b) => b.classList.remove("active"));
  document.querySelector(`#panel-${name}`)?.classList.add("active");
  document.querySelector(`.nav-btn[data-panel="${name}"]`)?.classList.add("active");
  const titles = {
    dashboard: "Dashboard",
    schedule: "Schedule",
    run: "Run Job",
    settings: "Settings",
  };
  $("#pageTitle").textContent = titles[name] || name;
}

function canEditResult(result) {
  return result && !result.posted && !appSettings.auto_post;
}

function formatResultMeta(result) {
  if (!result?.generated_at_utc && result?.row_count == null) return "";
  const when = result.generated_at_utc
    ? new Date(result.generated_at_utc).toLocaleString()
    : "";
  const webhook = result.webhook?.status_code
    ? ` · Posted (HTTP ${result.webhook.status_code})`
    : "";
  const draft = result.posted === false && !appSettings.auto_post ? " · Draft" : "";
  const excluded =
    result.excluded_perfect_date > 0
      ? ` · ${result.excluded_perfect_date} excluded (Perfect date)`
      : "";
  const postedCount =
    result.posted && result.posted_row_count != null
      ? ` · ${result.posted_row_count} posted`
      : "";
  return `${result.row_count ?? 0} row(s)${postedCount}${excluded}${when ? ` · ${when}` : ""}${webhook}${draft}`;
}

const EXTRA_COLS = 2; // Post checkbox + Delete

function buildReadOnlyTable(rows) {
  if (!rows?.length) {
    return `<table class="results-table"><tbody><tr class="empty-row"><td colspan="${RESULT_COLUMNS.length}">No cancellations for today or tomorrow.</td></tr></tbody></table>`;
  }
  const head = RESULT_COLUMNS.map((c) => `<th>${c.label}</th>`).join("");
  const body = rows
    .map((row) => {
      const cells = RESULT_COLUMNS.map((c) => {
        const val = row[c.key] ?? "";
        const cls = c.key === "action" ? ' class="action-cell"' : "";
        return `<td${cls}>${escapeHtml(val)}</td>`;
      }).join("");
      return `<tr>${cells}</tr>`;
    })
    .join("");
  return `<table class="results-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function buildEditableTable(rows) {
  const colSpan = RESULT_COLUMNS.length + EXTRA_COLS;
  if (!rows?.length) {
    return `<table class="results-table" data-editable="true"><tbody><tr class="empty-row"><td colspan="${colSpan}">No rows to edit.</td></tr></tbody></table>`;
  }
  const head =
    `<th class="col-post"><input type="checkbox" class="include-all" title="Select all" checked /> Post</th>` +
    RESULT_COLUMNS.map((c) => `<th>${c.label}</th>`).join("") +
    `<th class="col-delete">Delete</th>`;
  const body = rows
    .map((row, i) => {
      const cells = RESULT_COLUMNS.map((c) => {
        const val = escapeHtml(row[c.key] ?? "");
        const cls = c.key === "action" ? "action-cell" : "";
        return `<td class="${cls}"><input class="cell-input" data-col="${c.key}" value="${val}" /></td>`;
      }).join("");
      return `<tr data-row="${i}">
        <td class="col-post"><input type="checkbox" class="include-row" checked title="Include when posting" /></td>
        ${cells}
        <td class="col-delete"><button type="button" class="btn-delete-row btn btn-ghost btn-sm" title="Remove row">✕</button></td>
      </tr>`;
    })
    .join("");
  return `<table class="results-table" data-editable="true"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function getTableFromWrap(wrapEl) {
  return wrapEl?.querySelector("table.results-table") || wrapEl;
}

function collectRowsFromTable(tableOrWrap, { forPost = false } = {}) {
  const tableEl = tableOrWrap?.matches?.("table") ? tableOrWrap : getTableFromWrap(tableOrWrap);
  if (!tableEl) return [];

  return [...tableEl.querySelectorAll("tbody tr[data-row]")]
    .filter((tr) => {
      if (!forPost) return true;
      const cb = tr.querySelector(".include-row");
      return cb?.checked;
    })
    .map((tr) => {
      const row = {};
      RESULT_COLUMNS.forEach((c) => {
        const inp = tr.querySelector(`[data-col="${c.key}"]`);
        row[c.key] = inp ? inp.value.trim() : "";
      });
      return row;
    });
}

function countRowsInTable(tableOrWrap) {
  const tableEl = getTableFromWrap(tableOrWrap);
  if (!tableEl) return { total: 0, selected: 0 };
  const rows = [...tableEl.querySelectorAll("tbody tr[data-row]")];
  const selected = rows.filter((tr) => tr.querySelector(".include-row")?.checked).length;
  return { total: rows.length, selected };
}

function updateRowCountLabel(tableOrWrap, labelEl) {
  if (!labelEl) return;
  const { total, selected } = countRowsInTable(tableOrWrap);
  if (total === 0) {
    labelEl.textContent = "";
    return;
  }
  labelEl.textContent =
    selected === total
      ? `${selected} row(s) will be posted`
      : `${selected} of ${total} row(s) selected for posting`;
}

function bindEditableTable(wrapEl, countLabelEl) {
  if (!wrapEl || wrapEl.dataset.bound === "1") return;
  wrapEl.dataset.bound = "1";

  wrapEl.addEventListener("click", (e) => {
    const del = e.target.closest(".btn-delete-row");
    if (del) {
      del.closest("tr")?.remove();
      updateRowCountLabel(wrapEl, countLabelEl);
      return;
    }
    if (e.target.classList.contains("include-all")) {
      const checked = e.target.checked;
      getTableFromWrap(wrapEl)
        ?.querySelectorAll(".include-row")
        .forEach((cb) => {
          cb.checked = checked;
        });
      updateRowCountLabel(wrapEl, countLabelEl);
    }
  });

  wrapEl.addEventListener("change", (e) => {
    if (e.target.classList.contains("include-row")) {
      updateRowCountLabel(wrapEl, countLabelEl);
      const table = getTableFromWrap(wrapEl);
      const all = table?.querySelectorAll(".include-row") || [];
      const master = table?.querySelector(".include-all");
      if (master && all.length) {
        master.checked = [...all].every((cb) => cb.checked);
      }
    }
  });
}

function setAllRowsSelected(tableOrWrap, selected) {
  getTableFromWrap(tableOrWrap)
    ?.querySelectorAll(".include-row")
    .forEach((cb) => {
      cb.checked = selected;
    });
  const master = getTableFromWrap(tableOrWrap)?.querySelector(".include-all");
  if (master) master.checked = selected;
}

function updateDraftActions(actionsEl, result) {
  if (!actionsEl) return;
  if (canEditResult(result)) {
    actionsEl.classList.remove("hidden");
  } else {
    actionsEl.classList.add("hidden");
  }
}

function updateResultsBadge(badgeEl, result) {
  if (!badgeEl) return;
  if (!result || result.row_count == null) {
    badgeEl.classList.add("hidden");
    return;
  }
  badgeEl.classList.remove("hidden");
  if (result.posted) {
    badgeEl.textContent = "Posted";
    badgeEl.className = "status-pill posted";
  } else if (!appSettings.auto_post) {
    badgeEl.textContent = "Draft";
    badgeEl.className = "status-pill draft";
  } else {
    badgeEl.classList.add("hidden");
  }
}

function renderJobResults(
  result,
  { tableEl, metaEl, summaryEl, actionsEl, badgeEl, rowCountEl } = {}
) {
  const rows = result?.rows || [];
  const editable = canEditResult(result);

  if (tableEl) {
    tableEl.innerHTML = editable ? buildEditableTable(rows) : buildReadOnlyTable(rows);
    if (editable) {
      bindEditableTable(tableEl, rowCountEl);
      updateRowCountLabel(tableEl, rowCountEl);
    } else if (rowCountEl) {
      rowCountEl.textContent = rows.length ? `${rows.length} row(s) posted` : "";
    }
  }
  if (metaEl) metaEl.textContent = formatResultMeta(result);
  if (badgeEl) updateResultsBadge(badgeEl, result);
  if (actionsEl) updateDraftActions(actionsEl, result);

  if (summaryEl) {
    summaryEl.classList.remove("hidden", "error");
    if (result?.success === false) {
      summaryEl.classList.add("error");
      summaryEl.textContent = result.summary || "Job failed";
    } else {
      summaryEl.textContent = result?.summary || "";
      if (editable) {
        summaryEl.textContent +=
          " — Uncheck or delete rows you do not want, then Post to Teams.";
      }
    }
  }
}

async function loadSettings() {
  try {
    const s = await api("/settings/");
    appSettings = s;
    $("#autoPostToggle").checked = !!s.auto_post;
  } catch {
    try {
      const health = await api("/health");
      if (health.settings) {
        appSettings = health.settings;
        $("#autoPostToggle").checked = !!health.settings.auto_post;
      }
    } catch (e) {
      log(`Settings load error: ${e.message}`, "error");
    }
  }
}

async function saveSettings() {
  const auto_post = $("#autoPostToggle").checked;
  const s = await api("/settings/", {
    method: "POST",
    body: JSON.stringify({ auto_post }),
  });
  appSettings = s;
  showToast(auto_post ? "Auto-post enabled" : "Manual review enabled");
  log(`Auto-post: ${auto_post ? "on" : "off"}`, "success");
  await loadLatestResults();
}

async function saveDraftFromTable(tableOrWrap, { forPost = false } = {}) {
  const rows = collectRowsFromTable(tableOrWrap, { forPost });
  if (forPost && rows.length === 0) {
    throw new Error("Select at least one row to post (check the Post column).");
  }
  return api("/results/draft", {
    method: "PUT",
    body: JSON.stringify({ rows }),
  });
}

async function postDraftToTeams() {
  return api("/results/post", { method: "POST" });
}

async function loadLatestResults() {
  try {
    const result = await api("/results/latest");
    renderJobResults(result, {
      tableEl: $("#resultsTableWrap"),
      metaEl: $("#resultsMeta"),
      badgeEl: $("#resultsBadge"),
      actionsEl: $("#resultsActions"),
      rowCountEl: $("#resultsRowCount"),
    });
    return result;
  } catch (e) {
    log(`Could not load results: ${e.message}`, "error");
    return null;
  }
}

function renderStatus(health) {
  const grid = $("#statusGrid");
  const s = health.schedule?.scheduled_time || {};
  if (health.settings) appSettings = health.settings;

  const cards = [
    { label: "Server", value: health.status === "ok" ? "Online" : "Error", ok: health.status === "ok" },
    { label: "Scheduler", value: health.scheduler_running ? "Running" : "Stopped", ok: health.scheduler_running },
    { label: "Auto-post", value: appSettings.auto_post ? "On" : "Off (manual)", ok: true },
    { label: "SharePoint", value: health.sharepoint_configured ? "Configured" : "Missing", ok: health.sharepoint_configured },
    { label: "Power Automate", value: health.webhook_configured ? "Configured" : "Missing", ok: health.webhook_configured },
    {
      label: "Next run",
      value: health.schedule?.next_run ? new Date(health.schedule.next_run).toLocaleString() : "—",
      ok: !!health.schedule?.next_run,
    },
  ];

  grid.innerHTML = cards
    .map(
      (c) => `
    <article class="card status-card">
      <span class="label">${c.label}</span>
      <span class="value">${escapeHtml(c.value)}</span>
      <span class="badge ${c.ok ? "ok" : "err"}">${c.ok ? "OK" : "Check"}</span>
    </article>`
    )
    .join("");
}

function renderSchedulePreview(schedule) {
  const dl = $("#schedulePreview");
  if (!schedule) {
    dl.innerHTML = "<p class='hint'>No schedule data</p>";
    return;
  }
  const t = schedule.scheduled_time || {};
  const rows = [
    ["Time", `${t.time_12h || "—"} (${t.time_24h || "—"})`],
    ["Timezone", t.timezone || "—"],
    ["Frequency", schedule.frequency || "daily"],
    ["Cron", schedule.cron || "—"],
    ["Next run (local)", schedule.next_run ? new Date(schedule.next_run).toLocaleString() : "—"],
    ["Next run (UTC)", schedule.next_run_utc || "—"],
  ];
  dl.innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${escapeHtml(v)}</dd>`).join("");
}

function fillScheduleForm(schedule) {
  const t = schedule?.scheduled_time || {};
  $("#scheduleHour").value = t.hour ?? 4;
  $("#scheduleMinute").value = t.minute ?? 0;
  const tz = t.timezone || "America/Los_Angeles";
  const sel = $("#scheduleTz");
  if ([...sel.options].some((o) => o.value === tz)) sel.value = tz;
}

async function loadDashboard() {
  const icon = $("#refreshBtn")?.querySelector(".refresh-icon");
  icon?.classList.add("spin");
  try {
    const health = await api("/health");
    if (health.settings) appSettings = health.settings;
    renderStatus(health);
    renderSchedulePreview(health.schedule);
    fillScheduleForm(health.schedule);
    await loadLatestResults();
    log("Dashboard refreshed", "success");
  } catch (e) {
    log(`Dashboard error: ${e.message}`, "error");
  } finally {
    icon?.classList.remove("spin");
  }
}

async function handleSaveDraft(tableEl, actionsEl, badgeEl, metaEl, summaryEl, rowCountEl) {
  const result = await saveDraftFromTable(tableEl, { forPost: false });
  showToast(`Draft saved (${result.row_count} row(s))`);
  log("Draft saved", "success");
  const dash = {
    tableEl: $("#resultsTableWrap"),
    metaEl: $("#resultsMeta"),
    badgeEl: $("#resultsBadge"),
    actionsEl: $("#resultsActions"),
    rowCountEl: $("#resultsRowCount"),
  };
  const isRun = tableEl?.id === "runResultsTable";
  renderJobResults(result, isRun
    ? { tableEl, summaryEl, actionsEl, rowCountEl: rowCountEl || $("#runRowCount") }
    : { ...dash });
  if (!isRun) {
    renderJobResults(result, {
      tableEl: $("#runResultsTable"),
      summaryEl: $("#runSummary"),
      actionsEl: $("#runResultsActions"),
      rowCountEl: $("#runRowCount"),
    });
  }
  return result;
}

async function handlePostTeams(tableEl, actionsEl, badgeEl, metaEl, summaryEl, rowCountEl) {
  if (getTableFromWrap(tableEl)?.dataset.editable === "true") {
    await saveDraftFromTable(tableEl, { forPost: true });
  }
  const posted = await postDraftToTeams();
  showToast(`Posted ${posted.row_count} row(s) to Teams`);
  log("Posted to Teams thread", "success");
  const dash = {
    tableEl: $("#resultsTableWrap"),
    metaEl: $("#resultsMeta"),
    badgeEl: $("#resultsBadge"),
    actionsEl: $("#resultsActions"),
    rowCountEl: $("#resultsRowCount"),
  };
  const isRun = tableEl?.id === "runResultsTable";
  renderJobResults(posted, isRun
    ? { tableEl, summaryEl, actionsEl, rowCountEl: rowCountEl || $("#runRowCount") }
    : { ...dash });
  if (!isRun) {
    renderJobResults(posted, {
      tableEl: $("#runResultsTable"),
      summaryEl: $("#runSummary"),
      actionsEl: $("#runResultsActions"),
      rowCountEl: $("#runRowCount"),
    });
  }
}

document.querySelectorAll(".nav-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    showPanel(btn.dataset.panel);
    if (btn.dataset.panel === "schedule") {
      api("/schedule/").then(renderSchedulePreview).catch((e) => log(e.message, "error"));
    }
    if (btn.dataset.panel === "settings") loadSettings();
    if (btn.dataset.panel === "run") {
      loadLatestResults().then((r) => {
        if (r) {
          renderJobResults(r, {
            tableEl: $("#runResultsTable"),
            summaryEl: $("#runSummary"),
            actionsEl: $("#runResultsActions"),
            rowCountEl: $("#runRowCount"),
          });
        }
      });
    }
  });
});

$("#saveApiKey")?.addEventListener("click", () => {
  const key = $("#apiKey").value.trim();
  if (key) {
    localStorage.setItem(API_KEY_STORAGE, key);
    showToast("API key saved");
  }
});

$("#refreshBtn")?.addEventListener("click", loadDashboard);

$("#scheduleForm")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const result = await api("/schedule/", {
      method: "POST",
      body: JSON.stringify({
        hour: parseInt($("#scheduleHour").value, 10),
        minute: parseInt($("#scheduleMinute").value, 10),
        timezone: $("#scheduleTz").value,
      }),
    });
    renderSchedulePreview(result);
    fillScheduleForm(result);
    showToast("Schedule updated");
    loadDashboard();
  } catch (err) {
    showToast(err.message, true);
  }
});

$("#saveSettingsBtn")?.addEventListener("click", () => {
  saveSettings().catch((e) => showToast(e.message, true));
});

$("#saveDraftBtn")?.addEventListener("click", () => {
  handleSaveDraft(
    $("#resultsTableWrap"),
    $("#resultsActions"),
    $("#resultsBadge"),
    $("#resultsMeta"),
    null,
    $("#resultsRowCount")
  ).catch((e) => showToast(e.message, true));
});

$("#postTeamsBtn")?.addEventListener("click", () => {
  handlePostTeams(
    $("#resultsTableWrap"),
    $("#resultsActions"),
    $("#resultsBadge"),
    $("#resultsMeta"),
    null,
    $("#resultsRowCount")
  ).catch((e) => showToast(e.message, true));
});

$("#selectAllRowsBtn")?.addEventListener("click", () => {
  setAllRowsSelected($("#resultsTableWrap"), true);
  updateRowCountLabel($("#resultsTableWrap"), $("#resultsRowCount"));
});

$("#runSelectAllRowsBtn")?.addEventListener("click", () => {
  setAllRowsSelected($("#runResultsTable"), true);
  updateRowCountLabel($("#runResultsTable"), $("#runRowCount"));
});

$("#runSaveDraftBtn")?.addEventListener("click", () => {
  handleSaveDraft(
    $("#runResultsTable"),
    $("#runResultsActions"),
    null,
    null,
    $("#runSummary"),
    $("#runRowCount")
  ).catch((e) => showToast(e.message, true));
});

$("#runPostTeamsBtn")?.addEventListener("click", () => {
  handlePostTeams(
    $("#runResultsTable"),
    $("#runResultsActions"),
    null,
    null,
    $("#runSummary"),
    $("#runRowCount")
  ).catch((e) => showToast(e.message, true));
});

$("#runJobBtn")?.addEventListener("click", async () => {
  const btn = $("#runJobBtn");
  btn.disabled = true;
  btn.textContent = "Running…";
  $("#runSummary").classList.add("hidden");
  $("#runResultsTable").innerHTML = "<p class='hint'>Loading from SharePoint…</p>";
  try {
    const result = await api("/run", { method: "POST" });
    renderJobResults(result, {
      tableEl: $("#runResultsTable"),
      summaryEl: $("#runSummary"),
      actionsEl: $("#runResultsActions"),
      rowCountEl: $("#runRowCount"),
    });
    renderJobResults(result, {
      tableEl: $("#resultsTableWrap"),
      metaEl: $("#resultsMeta"),
      badgeEl: $("#resultsBadge"),
      actionsEl: $("#resultsActions"),
      rowCountEl: $("#resultsRowCount"),
    });
    showToast(result.summary || "Job completed");
    log(result.summary || "Job completed", "success");
  } catch (err) {
    const summaryEl = $("#runSummary");
    summaryEl.classList.remove("hidden");
    summaryEl.classList.add("error");
    summaryEl.textContent = err.message;
    showToast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "Run now";
  }
});

const savedKey = localStorage.getItem(API_KEY_STORAGE);
if (savedKey) $("#apiKey").value = savedKey;

bindEditableTable($("#resultsTableWrap"), $("#resultsRowCount"));
bindEditableTable($("#runResultsTable"), $("#runRowCount"));

loadSettings().then(loadDashboard);
