// Dashboard: KPIs, objections, leads (local CRM), appointments, calls, call detail, AI insights.

const $ = (id) => document.getElementById(id);

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = String(value ?? "");
  return div.innerHTML;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `Request failed (${response.status})`);
  }
  return data;
}

function formatIst(iso) {
  return new Date(iso).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", dateStyle: "medium", timeStyle: "short" });
}

function renderKpis(t) {
  const items = [
    ["Leads", t.leads],
    ["Calls", t.calls],
    ["Hot / booked leads", t.hot_leads],
    ["Appointments", t.appointments],
    ["Booking rate", t.booking_rate_pct + "%"],
    ["Avg sentiment (-1..1)", t.avg_sentiment],
  ];
  $("kpis").innerHTML = items
    .map(([label, value]) => `<div class="kpi"><div class="value">${escapeHtml(value)}</div><div class="label">${label}</div></div>`)
    .join("");
}

function renderCounts(obj) {
  const entries = Object.entries(obj);
  if (!entries.length) {
    return "-";
  }
  return entries.map(([k, v]) => `${escapeHtml(k)}: ${v}`).join(" &middot; ");
}

function bantSummary(bantJson) {
  const bant = JSON.parse(bantJson || "{}");
  const keys = ["need", "budget", "authority", "timeline"];
  return keys
    .map((k) => {
      if (!bant[k]) {
        return `${k[0].toUpperCase()}:?`;
      }
      return `${k[0].toUpperCase()}:${bant[k].qualified ? "Y" : "N"}`;
    })
    .join(" ");
}

async function load() {
  const data = await api("/api/dashboard");
  renderKpis(data.totals);

  if (data.top_objections.length) {
    $("objections").innerHTML = data.top_objections
      .map(([text, count]) => `${escapeHtml(text)} <strong>(${count})</strong>`)
      .join("<br>");
  }
  $("mix").innerHTML =
    `Languages: ${renderCounts(data.languages)}<br>` +
    `Sentiment: ${renderCounts(data.sentiment)}<br>` +
    `Outcomes: ${renderCounts(data.outcomes)}<br>` +
    `Lead status: ${renderCounts(data.lead_status)}`;

  $("leads").innerHTML = data.leads
    .map((l) => `<tr>
        <td>${escapeHtml(l.name)}</td><td>${escapeHtml(l.phone)}</td>
        <td><span class="badge ${l.status}">${l.status}</span></td><td>${l.score}</td>
        <td>${bantSummary(l.bant_json)}</td><td>${escapeHtml(l.hubspot_contact_id || "-")}</td></tr>`)
    .join("") || `<tr><td colspan="6" class="muted">No leads yet</td></tr>`;

  $("appointments").innerHTML = data.appointments
    .map((a) => `<tr><td>${escapeHtml(a.lead_name)}</td><td>${formatIst(a.start_time)}</td>
        <td>${escapeHtml(a.source)}</td><td>${escapeHtml(a.external_id || "-")}</td></tr>`)
    .join("") || `<tr><td colspan="4" class="muted">No appointments yet</td></tr>`;

  $("calls").innerHTML = data.calls
    .map((c) => `<tr class="clickable" data-id="${c.id}">
        <td>${c.id}</td><td>${escapeHtml(c.lead_name)}</td><td>${escapeHtml(c.language)}</td>
        <td>${c.duration_sec || 0}s</td><td>${escapeHtml(c.sentiment || "-")}</td>
        <td>${escapeHtml(c.outcome || (c.ended_at ? "-" : "in progress"))}</td>
        <td>${escapeHtml(c.summary || "")}</td></tr>`)
    .join("") || `<tr><td colspan="7" class="muted">No calls yet</td></tr>`;

  document.querySelectorAll("#calls tr.clickable").forEach((row) => {
    row.addEventListener("click", () => showCall(row.dataset.id));
  });
}

async function showCall(id) {
  const call = await api(`/api/calls/${id}`);
  const a = call.analysis;
  const turns = call.turns
    .map((t) => `<div class="bubble ${t.role}">${escapeHtml(t.text)}<span class="meta">${t.role} &middot; ${escapeHtml(t.language)}</span></div>`)
    .join("");
  $("detail").innerHTML = `
    <p><strong>${escapeHtml(call.lead_name)}</strong> (${escapeHtml(call.lead_phone)}) &middot; call #${call.id}</p>
    <p>${escapeHtml(a.summary || "")}</p>
    <p><strong>Sentiment:</strong> ${escapeHtml(a.sentiment || "-")} (${a.sentiment_score ?? "-"}) &middot; ${escapeHtml(a.sentiment_trend || "")}</p>
    <p><strong>Objections:</strong> ${escapeHtml((a.objections || []).join(", ") || "none")}</p>
    <p><strong>Buying signals:</strong> ${escapeHtml((a.buying_signals || []).join(", ") || "none")}</p>
    <p><strong>Interested in:</strong> ${escapeHtml(a.interested_in || "-")} &middot; <strong>Next step:</strong> ${escapeHtml(a.next_step || "-")}</p>
    <p class="muted">CRM sync: ${escapeHtml(JSON.stringify(call.crm_sync))}</p>
    <div style="display:flex;flex-direction:column;gap:8px;margin-top:12px">${turns}</div>`;
  $("detail-card").classList.remove("hidden");
  $("detail-card").scrollIntoView({ behavior: "smooth" });
}

$("insights-btn").addEventListener("click", async () => {
  $("insights-btn").disabled = true;
  $("insights").textContent = "Analysing recent calls...";
  try {
    const data = await api("/api/insights", { method: "POST" });
    $("insights").textContent = data.insights;
  } catch (err) {
    $("insights").textContent = "Error: " + err.message;
  }
  $("insights-btn").disabled = false;
});

load().catch((err) => {
  $("kpis").textContent = "Could not load dashboard: " + err.message;
});
