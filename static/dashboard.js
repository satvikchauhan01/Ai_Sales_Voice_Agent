// Dashboard: KPIs, pipeline, sentiment, objections, languages, leads (local CRM),
// site surveys, recent calls, call detail drawer, AI insights.
// Data comes from GET /api/dashboard (see analysis.dashboard_metrics) and GET /api/calls/{id}.

const $ = (id) => document.getElementById(id);
const STATUS_ORDER = ["NEW", "COLD", "WARM", "HOT", "BOOKED"];
const OUTCOME_LABELS = {
  appointment_booked: "Survey booked",
  follow_up: "Follow-up",
  not_interested: "Not interested",
  no_decision: "No decision",
};
let languageNames = {};

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

// ---------------- formatting ----------------
function formatIst(iso, opts = { dateStyle: "medium", timeStyle: "short" }) {
  return new Date(iso).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", ...opts });
}

function relTime(iso) {
  if (!iso) {
    return "-";
  }
  const sec = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (sec < 60) return "just now";
  if (sec < 3600) return `${Math.floor(sec / 60)} min ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} h ago`;
  return `${Math.floor(sec / 86400)} d ago`;
}

function duration(sec) {
  sec = Math.max(0, Math.round(sec || 0));
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
}

function initials(name) {
  const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "–";
  return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}

function languageName(code) {
  return languageNames[code] || code || "-";
}

function sentimentWord(score) {
  if (score > 0.2) return "positive";
  if (score < -0.2) return "negative";
  return "neutral";
}

// ---------------- KPI tiles ----------------
const ICONS = {
  leads: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
  calls: '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z"/>',
  hot: '<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.07-2.14-.22-4.05 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.15.43-2.29 1-3a2.5 2.5 0 0 0 2.5 2.5z"/>',
  appts: '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
  rate: '<path d="M22 7 13.5 15.5 8.5 10.5 2 17"/><path d="M16 7h6v6"/>',
  mood: '<circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><path d="M9 9h.01M15 9h.01"/>',
};

function kpi(icon, label, value, sub) {
  return `<div class="kpi">
    <div class="k-label"><i><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[icon]}</svg></i>${label}</div>
    <div class="k-value">${value}</div>
    <div class="k-sub">${sub}</div>
  </div>`;
}

function renderKpis(t) {
  const mood = sentimentWord(t.avg_sentiment);
  const moodColor = mood === "positive" ? "var(--data)" : mood === "negative" ? "var(--data-neg)" : "var(--data-mid)";
  const signed = (t.avg_sentiment > 0 ? "+" : "") + Number(t.avg_sentiment).toFixed(2);
  $("kpis").innerHTML = [
    kpi("leads", "Leads", t.leads, "unique callers by phone"),
    kpi("calls", "Calls", t.calls, `avg ${duration(t.avg_call_sec)} per call`),
    kpi("hot", "Hot + booked", t.hot_leads, "ready for the sales team"),
    kpi("appts", "Site surveys", t.appointments, "booked by Priya"),
    kpi("rate", "Booking rate", `${t.booking_rate_pct}%`, "of finished calls"),
    kpi("mood", "Avg sentiment", signed, `<span class="dot" style="background:${moodColor}"></span>${mood} · scale −1 to +1`),
  ].join("");
}

// ---------------- bar charts (one series, value at the bar tip) ----------------
function renderBars(el, rows, emptyText) {
  const max = Math.max(0, ...rows.map((r) => r.value));
  if (!rows.length || max === 0) {
    el.innerHTML = `<div class="empty-note">${emptyText}</div>`;
    return;
  }
  const total = rows.reduce((sum, r) => sum + r.value, 0);
  el.innerHTML = rows
    .map((r) => {
      const pct = (r.value / max) * 100;
      const share = total ? Math.round((r.value / total) * 100) : 0;
      const tip = `${r.text}: ${r.value} (${share}%)`;
      return `<div class="bar-row" tabindex="0" data-tip="${escapeHtml(tip)}" aria-label="${escapeHtml(tip)}">
        <span class="b-label">${r.label}</span>
        <span class="b-track"><span class="b-fill" style="display:block;width:${pct}%"></span></span>
        <span class="b-value">${r.value}</span>
      </div>`;
    })
    .join("");
}

function renderPipeline(counts) {
  const rows = STATUS_ORDER.map((s) => ({ label: `<span class="badge ${s}">${s}</span>`, text: s, value: counts[s] || 0 }));
  renderBars($("pipeline"), rows, "No leads yet. Start a call to create one.");
}

function renderSentiment(counts) {
  const parts = [
    ["positive", "pos", "Positive", "var(--data)"],
    ["neutral", "neu", "Neutral", "var(--data-mid)"],
    ["negative", "neg", "Negative", "var(--data-neg)"],
  ];
  const total = parts.reduce((s, [k]) => s + (counts[k] || 0), 0);
  if (!total) {
    $("sentiment").innerHTML = '<div class="empty-note">No finished calls yet.</div>';
    return;
  }
  const blocks = parts
    .filter(([k]) => counts[k])
    .map(([k, cls, label]) => {
      const share = Math.round((counts[k] / total) * 100);
      const text = share >= 14 ? `${share}%` : "";
      return `<span class="${cls}" style="flex:${counts[k]}" tabindex="0" data-tip="${label}: ${counts[k]} calls (${share}%)">${text}</span>`;
    })
    .join("");
  const legend = parts
    .map(([k, , label, color]) => `<span><i style="background:${color}"></i>${label} ${counts[k] || 0}</span>`)
    .join("");
  $("sentiment").innerHTML = `<div style="display:grid;gap:10px"><div class="seg" role="img" aria-label="Sentiment split">${blocks}</div><div class="legend">${legend}</div></div>`;
}

function renderOutcomes(counts) {
  const rows = Object.keys(OUTCOME_LABELS)
    .filter((k) => counts[k])
    .map((k) => ({ label: escapeHtml(OUTCOME_LABELS[k]), text: OUTCOME_LABELS[k], value: counts[k] }));
  renderBars($("outcomes"), rows, "Outcomes appear after calls end.");
}

function renderObjections(list) {
  const rows = list.map(([text, count]) => ({
    label: escapeHtml(text.charAt(0).toUpperCase() + text.slice(1)),
    text,
    value: count,
  }));
  renderBars($("objections"), rows, "No objections recorded yet.");
}

function renderLanguages(counts) {
  const rows = Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .map(([code, n]) => ({ label: escapeHtml(languageName(code)), text: languageName(code), value: n }));
  renderBars($("languages"), rows, "No calls yet.");
}

// ---------------- tables & lists ----------------
function bantPills(bantJson) {
  const bant = JSON.parse(bantJson || "{}");
  return `<div class="bant-pills">${["budget", "authority", "need", "timeline"]
    .map((k) => {
      const a = bant[k];
      const cls = !a ? "" : a.qualified ? "yes" : "no";
      const tip = a ? `${k}: ${a.qualified ? "yes" : "no"} - ${a.note}` : `${k}: unknown`;
      return `<span class="${cls}" data-tip="${escapeHtml(tip)}">${k[0].toUpperCase()}</span>`;
    })
    .join("")}</div>`;
}

const CHECK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>';

function renderLeads(leads) {
  $("leads").innerHTML =
    leads
      .map((l) => `<tr>
        <td><div class="person"><div class="avatar">${escapeHtml(initials(l.name))}</div><div><b>${escapeHtml(l.name)}</b><small>${escapeHtml(l.phone)}</small></div></div></td>
        <td><span class="badge ${escapeHtml(l.status)}">${escapeHtml(l.status)}</span></td>
        <td><div class="score-cell"><span class="mini"><span style="width:${Math.max(0, Math.min(100, l.score))}%"></span></span><b>${l.score}</b></div></td>
        <td>${bantPills(l.bant_json)}</td>
        <td><span class="lang-tag">${escapeHtml(l.preferred_language === "auto" ? "Auto" : languageName(l.preferred_language))}</span></td>
        <td>${l.hubspot_contact_id ? `<span class="synced" data-tip="HubSpot contact ${escapeHtml(l.hubspot_contact_id)}">${CHECK}Synced</span>` : '<span class="muted">-</span>'}</td>
        <td class="muted">${relTime(l.updated_at)}</td>
      </tr>`)
      .join("") || '<tr><td colspan="7" class="empty-note">No leads yet. Start a call to create one.</td></tr>';
}

function renderAppointments(appts) {
  const now = Date.now();
  const sorted = appts.slice().sort((a, b) => new Date(a.start_time) - new Date(b.start_time));
  $("appointments").innerHTML =
    sorted
      .map((a) => {
        const past = new Date(a.start_time).getTime() < now;
        return `<li class="appt${past ? " past" : ""}">
          <div class="date"><small>${formatIst(a.start_time, { month: "short" })}</small><b>${formatIst(a.start_time, { day: "numeric" })}</b></div>
          <div class="who"><b>${escapeHtml(a.lead_name)}</b><small>${formatIst(a.start_time, { weekday: "short", hour: "numeric", minute: "2-digit" })} IST${past ? " · done" : ""}</small></div>
          <span class="src">${escapeHtml(a.source === "cal.com" ? "Cal.com" : "Local")}</span>
        </li>`;
      })
      .join("") || '<li class="empty-note">No site surveys booked yet.</li>';
}

function renderCalls(calls) {
  $("calls").innerHTML =
    calls
      .map((c) => {
        const outcome = c.outcome ? `<span class="outcome-tag ${escapeHtml(c.outcome)}">${escapeHtml(OUTCOME_LABELS[c.outcome] || c.outcome)}</span>` : `<span class="muted">${c.ended_at ? "-" : "In progress"}</span>`;
        const senti = c.sentiment ? `<span class="senti ${escapeHtml(c.sentiment)}"><i></i>${escapeHtml(c.sentiment)}</span>` : '<span class="muted">-</span>';
        return `<tr class="clickable" tabindex="0" data-id="${c.id}">
          <td class="muted">${c.id}</td>
          <td><b>${escapeHtml(c.lead_name)}</b></td>
          <td><span class="lang-tag">${escapeHtml(languageName(c.language))}</span></td>
          <td class="num">${duration(c.duration_sec)}</td>
          <td>${senti}</td>
          <td>${outcome}</td>
          <td><span class="clamp">${escapeHtml(c.summary || "")}</span></td>
        </tr>`;
      })
      .join("") || '<tr><td colspan="7" class="empty-note">No calls yet.</td></tr>';

  document.querySelectorAll("#calls tr.clickable").forEach((row) => {
    row.addEventListener("click", () => showCall(row.dataset.id));
    row.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        showCall(row.dataset.id);
      }
    });
  });
}

// ---------------- call detail drawer ----------------
function chipList(items, cls) {
  if (!items || !items.length) return '<span class="muted">None</span>';
  return `<div class="chips ${cls}">${items.map((x) => `<span class="c">${escapeHtml(x)}</span>`).join("")}</div>`;
}

function crmText(crm) {
  if (crm.hubspot_contact_id) {
    return `<div class="crm-line ok">${CHECK}<span>Synced to HubSpot · contact <code>${escapeHtml(crm.hubspot_contact_id)}</code>${crm.hubspot_deal_id ? ` · deal <code>${escapeHtml(crm.hubspot_deal_id)}</code>` : ""}</span></div>`;
  }
  if (crm.hubspot_error) {
    return `<div class="crm-line err"><span>HubSpot sync failed: ${escapeHtml(crm.hubspot_error)}</span></div>`;
  }
  return '<div class="crm-line"><span>Saved in the local CRM</span></div>';
}

async function showCall(id) {
  openDrawer();
  $("detail-title").textContent = `Call #${id}`;
  $("detail-sub").textContent = "Loading…";
  $("detail").innerHTML = '<div class="shimmer"></div><div class="shimmer"></div>';
  try {
    const call = await api(`/api/calls/${id}`);
    const a = call.analysis || {};
    const score = Number(a.sentiment_score) || 0;
    const pos = ((Math.max(-1, Math.min(1, score)) + 1) / 2) * 100;
    $("detail-title").textContent = call.lead_name;
    $("detail-sub").textContent = `${call.lead_phone} · call #${call.id} · ${call.started_at ? formatIst(call.started_at) : ""}`;
    const turns = call.turns
      .map((t) => `<div class="msg ${t.role === "agent" ? "agent" : "user"}">
          <div class="who">${t.role === "agent" ? "<i></i>Priya" : "Caller"}</div>
          <div class="bubble">${escapeHtml(t.text)}</div>
          <div class="meta"><span>${escapeHtml(languageName(t.language))}</span></div>
        </div>`)
      .join("");
    const outcome = a.outcome ? `<span class="outcome ${escapeHtml(a.outcome)}">${escapeHtml(OUTCOME_LABELS[a.outcome] || a.outcome)}</span>` : "";
    $("detail").innerHTML = `
      <div class="sum-head"><span class="eyebrow">Summary</span>${outcome}</div>
      <p class="sum-text">${escapeHtml(a.summary || (call.ended_at ? "No summary." : "Call still in progress."))}</p>
      ${a.sentiment ? `<div class="meter">
        <span class="eyebrow">Sentiment · ${escapeHtml(a.sentiment)} (${score.toFixed(2)})${a.sentiment_trend ? " · " + escapeHtml(a.sentiment_trend) : ""}</span>
        <div class="meter-track"><span class="meter-mark" style="left:${pos}%"></span></div>
        <div class="meter-scale"><span>Negative</span><span>Neutral</span><span>Positive</span></div>
      </div>` : ""}
      <div class="facts">
        <div class="fact"><span class="eyebrow">Interested in</span><div>${escapeHtml(a.interested_in || "-")}</div></div>
        <div class="fact"><span class="eyebrow">Next step</span><div>${escapeHtml(a.next_step || "-")}</div></div>
      </div>
      <div class="facts">
        <div style="display:grid;gap:6px"><span class="eyebrow">Objections</span>${chipList(a.objections, "neg")}</div>
        <div style="display:grid;gap:6px"><span class="eyebrow">Buying signals</span>${chipList(a.buying_signals, "pos")}</div>
      </div>
      ${crmText(call.crm_sync || {})}
      <span class="eyebrow">Transcript · ${call.turns.length} messages</span>
      <div class="transcript">${turns || '<span class="muted">No messages.</span>'}</div>`;
  } catch (err) {
    $("detail-sub").textContent = "";
    $("detail").innerHTML = `<div class="crm-line err"><span>Could not load call: ${escapeHtml(err.message)}</span></div>`;
  }
}

function openDrawer() {
  $("drawer-backdrop").hidden = false;
  $("detail-card").classList.add("open");
  $("detail-card").setAttribute("aria-hidden", "false");
  $("detail-close").focus();
}

function closeDrawer() {
  $("drawer-backdrop").hidden = true;
  $("detail-card").classList.remove("open");
  $("detail-card").setAttribute("aria-hidden", "true");
}

// ---------------- AI insights ----------------
function renderInsights(text) {
  const lines = String(text || "")
    .split("\n")
    .map((l) => l.replace(/\*\*/g, "").trim())
    .filter(Boolean)
    .map((l) => l.replace(/^(\d+[.)]|[-*•])\s*/, ""));
  if (!lines.length) {
    $("insights").innerHTML = '<div class="insights-empty"><b>No insights returned</b>Try again after a few more calls.</div>';
    return;
  }
  $("insights").innerHTML = lines
    .map((l, i) => `<div class="insight"><b class="n">${i + 1}</b><span>${escapeHtml(l)}</span></div>`)
    .join("");
}

$("insights-btn").addEventListener("click", async () => {
  const btn = $("insights-btn");
  btn.disabled = true;
  $("insights").innerHTML = '<div class="shimmer"></div><div class="shimmer"></div><div class="shimmer"></div>';
  try {
    const data = await api("/api/insights", { method: "POST" });
    renderInsights(data.insights);
  } catch (err) {
    $("insights").innerHTML = `<div class="crm-line err"><span>Could not generate insights: ${escapeHtml(err.message)}</span></div>`;
  }
  btn.disabled = false;
});

// ---------------- tooltip for chart marks and pills ----------------
function showTip(el, x, y) {
  const tip = $("tip");
  tip.textContent = el.dataset.tip;
  tip.hidden = false;
  const w = tip.offsetWidth;
  const left = Math.min(window.innerWidth - w - 8, Math.max(8, x - w / 2));
  tip.style.left = `${left}px`;
  tip.style.top = `${Math.max(8, y - 40)}px`;
}
document.addEventListener("mousemove", (e) => {
  const el = e.target.closest("[data-tip]");
  if (el) {
    showTip(el, e.clientX, e.clientY);
  } else {
    $("tip").hidden = true;
  }
});
document.addEventListener("focusin", (e) => {
  const el = e.target.closest("[data-tip]");
  if (el) {
    const r = el.getBoundingClientRect();
    showTip(el, r.left + r.width / 2, r.top);
  }
});
document.addEventListener("focusout", () => { $("tip").hidden = true; });

// ---------------- load ----------------
async function load() {
  const [data, health] = await Promise.all([
    api("/api/dashboard"),
    api("/api/health").catch(() => ({})),
  ]);
  languageNames = health.languages || {};
  renderKpis(data.totals);
  renderPipeline(data.lead_status);
  renderSentiment(data.sentiment);
  renderOutcomes(data.outcomes);
  renderObjections(data.top_objections);
  renderLanguages(data.languages);
  renderLeads(data.leads);
  renderAppointments(data.appointments);
  renderCalls(data.calls);
  const crm = health.crm === "hubspot + local" ? "HubSpot connected" : "Local CRM";
  $("updated").textContent = `Live from Priya's calls · ${crm} · updated ${new Date().toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" })}`;
}

function refresh() {
  load().catch((err) => {
    $("updated").textContent = "Could not load dashboard: " + err.message;
  });
}

$("refresh-btn").addEventListener("click", refresh);
$("detail-close").addEventListener("click", closeDrawer);
$("drawer-backdrop").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeDrawer();
  }
});
setInterval(refresh, 30000); // keep numbers fresh while the dashboard is open during a demo
refresh();
