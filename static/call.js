// Call page logic.
// Flow: start call -> play greeting -> VAD detects caller speech -> send WAV to backend
//       -> backend does STT + LLM + TTS -> play reply -> listen again.
// Modes:  idle | listening | thinking | speaking | ended     (shown by the orb + status line)
// Phases: precall -> incall -> summary                        (which panels are visible)

const VAD_SETTINGS = {
  positiveSpeechThreshold: 0.5,  // probability above which a frame counts as speech
  negativeSpeechThreshold: 0.35, // probability below which a frame counts as silence
  redemptionMs: 800,             // silence needed before we decide the caller finished
  minSpeechMs: 300,              // ignore very short noises
  preSpeechPadMs: 300,           // keep a little audio before speech started
};

const BANT_LABELS = { budget: "Budget", authority: "Authority", need: "Need", timeline: "Timeline" };
const BANT_HINTS = {
  budget: "Monthly bill, budget or EMI interest",
  authority: "Owner / decision maker?",
  need: "Why solar, roof available?",
  timeline: "When to install",
};
const OUTCOME_LABELS = {
  appointment_booked: "Site survey booked",
  follow_up: "Follow-up needed",
  not_interested: "Not interested",
  no_decision: "No decision yet",
};
const MODE_LABELS = {
  idle: "Ready when you are",
  listening: "Listening…",
  thinking: "Priya is thinking…",
  speaking: "Priya is speaking",
  ended: "Call ended",
};

const state = {
  callId: null,
  mode: "idle",
  vad: null,
  audio: null,
  audioDone: null,
  audioCtx: null,      // Web Audio context, used to measure Priya's voice level for the orb
  analyser: null,
  analyserBuf: null,
  micLevel: 0,         // latest microphone level (0..1), from the VAD frames
  languages: {},       // code -> name, from /api/health
  timer: null,
  startedAt: 0,
  userTurns: 0,
};

const $ = (id) => document.getElementById(id);
const orb = window.createOrb($("orb"));

// LLM output is untrusted text: escape it before putting it into HTML.
function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = String(value ?? "");
  return div.innerHTML;
}

// ---------------- helpers ----------------
async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `Request failed (${response.status})`);
  }
  return data;
}

function languageName(code) {
  return state.languages[code] || code || "";
}

function setPhase(phase) {
  document.body.dataset.phase = phase;
  const inCall = phase === "incall";
  $("text-input").disabled = !inCall;
  $("send-text-btn").disabled = !inCall;
  document.querySelectorAll(".suggestion").forEach((b) => { b.disabled = !inCall; });
}

function setMode(mode, label) {
  state.mode = mode;
  document.body.dataset.mode = mode;
  $("status").textContent = label || MODE_LABELS[mode] || mode;
  orb.setMode(mode);
}

function showError(message) {
  $("call-error").textContent = message;
  setTimeout(() => { $("call-error").textContent = ""; }, 8000);
}

function initials(name) {
  const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) {
    return "–";
  }
  return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}

// ---------------- call timer ----------------
function startTimer() {
  state.startedAt = Date.now();
  stopTimer();
  const tick = () => {
    const s = Math.floor((Date.now() - state.startedAt) / 1000);
    $("timer").textContent = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  };
  tick();
  state.timer = setInterval(tick, 1000);
}

function stopTimer() {
  if (state.timer) {
    clearInterval(state.timer);
    state.timer = null;
  }
}

// ---------------- orb audio levels ----------------
// The orb asks for a level every frame: mic level while listening, Priya's voice while speaking.
function currentLevel() {
  if (state.mode === "listening") {
    return state.micLevel;
  }
  if (state.mode === "speaking") {
    if (state.analyser) {
      state.analyser.getByteTimeDomainData(state.analyserBuf);
      let sum = 0;
      for (let i = 0; i < state.analyserBuf.length; i++) {
        const v = (state.analyserBuf[i] - 128) / 128;
        sum += v * v;
      }
      return Math.min(1, Math.sqrt(sum / state.analyserBuf.length) * 4);
    }
    return 0.35 + 0.2 * Math.sin(performance.now() / 110); // no analyser -> gentle synthetic pulse
  }
  return 0;
}
orb.setLevelProvider(currentLevel);

// Must be created inside a click (browsers block audio until the user interacts).
function ensureAudioContext() {
  if (!state.audioCtx) {
    try {
      state.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    } catch (err) {
      state.audioCtx = null;
    }
  }
  if (state.audioCtx && state.audioCtx.state === "suspended") {
    state.audioCtx.resume().catch(() => {});
  }
}

function attachAnalyser(audio) {
  state.analyser = null;
  const ctx = state.audioCtx;
  if (!ctx || ctx.state !== "running") {
    return; // play normally; the orb uses the synthetic pulse
  }
  try {
    const source = ctx.createMediaElementSource(audio);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    source.connect(analyser);
    analyser.connect(ctx.destination);
    state.analyser = analyser;
    state.analyserBuf = new Uint8Array(analyser.fftSize);
  } catch (err) {
    state.analyser = null;
  }
}

// ---------------- live caption (words light up as Priya speaks) ----------------
function setCaption(text) {
  const box = $("caption");
  box.innerHTML = "";
  String(text).split(/(\s+)/).forEach((part) => {
    if (!part.trim()) {
      box.appendChild(document.createTextNode(part));
      return;
    }
    const span = document.createElement("span");
    span.className = "w";
    span.textContent = part;
    box.appendChild(span);
  });
}

function revealCaption(fraction) {
  const words = $("caption").querySelectorAll(".w");
  const count = Math.ceil(words.length * Math.max(0, Math.min(1, fraction)));
  words.forEach((w, i) => w.classList.toggle("on", i < count));
}

// ---------------- transcript ----------------
function clearEmptyState() {
  const empty = $("transcript").querySelector(".empty");
  if (empty) {
    empty.remove();
  }
}

function scrollTranscript() {
  const box = $("transcript");
  box.scrollTop = box.scrollHeight;
}

function addBubble(role, text, metaParts = []) {
  clearEmptyState();
  const msg = document.createElement("div");
  msg.className = "msg " + role;
  const who = document.createElement("div");
  who.className = "who";
  who.innerHTML = role === "agent" ? "<i></i>Priya" : "You";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  msg.append(who, bubble);
  setMeta(msg, metaParts);
  $("transcript").appendChild(msg);
  scrollTranscript();
  return msg;
}

function setMeta(msg, metaParts) {
  let meta = msg.querySelector(".meta");
  if (!meta) {
    meta = document.createElement("div");
    meta.className = "meta";
    msg.appendChild(meta);
  }
  meta.innerHTML = metaParts.filter(Boolean).map((p) => `<span>${escapeHtml(p)}</span>`).join("");
}

function addNote(text) {
  clearEmptyState();
  const note = document.createElement("div");
  note.className = "note";
  note.textContent = text;
  $("transcript").appendChild(note);
  scrollTranscript();
}

function showTyping() {
  hideTyping();
  clearEmptyState();
  const typing = document.createElement("div");
  typing.className = "typing";
  typing.id = "typing";
  typing.innerHTML = "<i></i><i></i><i></i>";
  $("transcript").appendChild(typing);
  scrollTranscript();
}

function hideTyping() {
  const typing = $("typing");
  if (typing) {
    typing.remove();
  }
}

function seconds(ms) {
  return ms == null ? null : (ms / 1000).toFixed(1) + "s";
}

// ---------------- lead panel ----------------
function updateLead(lead) {
  if (!lead) {
    return;
  }
  const scoreEl = $("lead-score");
  const previous = Number(scoreEl.textContent) || 0;
  scoreEl.textContent = lead.score;
  $("ring-fill").style.strokeDashoffset = String(326.73 * (1 - Math.max(0, Math.min(100, lead.score)) / 100));
  $("lead-status").textContent = lead.status;
  $("lead-status").className = "badge " + lead.status;
  document.body.dataset.lead = lead.status;
  if (lead.status === "BOOKED") {
    $("score-hint").textContent = "Site survey booked. Ready for the sales team.";
  } else if (lead.status === "HOT") {
    $("score-hint").textContent = "Strong lead. Priya will offer a site survey.";
  } else if (lead.score > previous) {
    $("score-hint").textContent = "Score went up from what the caller just said.";
  }

  const list = $("bant");
  ["budget", "authority", "need", "timeline"].forEach((key) => {
    let item = list.querySelector(`[data-key="${key}"]`);
    if (!item) {
      item = document.createElement("li");
      item.dataset.key = key;
      list.appendChild(item);
    }
    const answer = lead.bant[key];
    const cls = !answer ? "unknown" : answer.qualified ? "yes" : "no";
    const note = answer ? answer.note : BANT_HINTS[key];
    const changed = item.dataset.state !== undefined && item.dataset.state !== cls + note;
    item.className = "bant-item " + cls;
    let icon = "";
    if (cls === "yes") {
      icon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>';
    } else if (cls === "no") {
      icon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>';
    }
    item.innerHTML = `<span class="bi-icon">${icon}</span><div><b>${BANT_LABELS[key]}</b><small>${escapeHtml(note)}</small></div>`;
    if (changed) {
      item.classList.add("flash");
    }
    item.dataset.state = cls + note;
  });
}

// ---------------- audio playback ----------------
function stopAudio() {
  if (state.audio) {
    state.audio.pause();
    state.audio = null;
  }
  state.analyser = null;
  revealCaption(1);
  if (state.audioDone) {
    state.audioDone();
    state.audioDone = null;
  }
}

function playAudio(base64Wav) {
  stopAudio();
  return new Promise((resolve) => {
    const audio = new Audio("data:audio/wav;base64," + base64Wav);
    state.audio = audio;
    state.audioDone = resolve;
    attachAnalyser(audio);
    audio.ontimeupdate = () => {
      if (audio.duration && isFinite(audio.duration)) {
        revealCaption(audio.currentTime / audio.duration + 0.08);
      }
    };
    audio.onended = () => stopAudio();
    audio.onerror = () => stopAudio();
    audio.play().catch(() => stopAudio());
  });
}

async function agentSays(data, metaParts = []) {
  hideTyping();
  addBubble("agent", data.agent_text, metaParts);
  updateLead(data.lead);
  if (data.language) {
    $("lang-chip").textContent = languageName(data.language);
  }
  setCaption(data.agent_text);
  revealCaption(0);
  setMode("speaking");
  await playAudio(data.audio_b64);
  if (state.callId && state.mode === "speaking") {
    setMode("listening");
  }
}

// ---------------- conversation turns ----------------
async function handleTurnResponse(data, userMsg) {
  if (data.busy || data.empty) {
    if (userMsg && userMsg.classList.contains("pending")) {
      userMsg.remove();
    }
    hideTyping();
    setMode("listening");
    return;
  }
  if (userMsg) {
    userMsg.classList.remove("pending");
    userMsg.querySelector(".bubble").textContent = data.user_text;
    setMeta(userMsg, [languageName(data.language)]);
  } else {
    addBubble("user", data.user_text, [languageName(data.language)]);
  }
  state.userTurns += 1;
  $("turn-count").textContent = `${state.userTurns} ${state.userTurns === 1 ? "turn" : "turns"}`;

  const l = data.latency_ms || {};
  const meta = [];
  if (l.stt != null) meta.push(`STT ${seconds(l.stt)}`);
  if (l.llm != null) meta.push(`LLM ${seconds(l.llm)}`);
  if (l.tts != null) meta.push(`TTS ${seconds(l.tts)}`);
  await agentSays(data, meta);
}

async function sendAudio(float32Audio) {
  setMode("thinking");
  const pending = addBubble("user", "Transcribing…");
  pending.classList.add("pending");
  showTyping();
  // 16-bit PCM WAV at 16 kHz (format 1 = PCM)
  const wavBuffer = vad.utils.encodeWAV(float32Audio, 1, 16000, 1, 16);
  const form = new FormData();
  form.append("audio", new Blob([wavBuffer], { type: "audio/wav" }), "speech.wav");
  try {
    const data = await api(`/api/calls/${state.callId}/turn`, { method: "POST", body: form });
    await handleTurnResponse(data, pending);
  } catch (err) {
    pending.remove();
    hideTyping();
    showError(err.message);
    setMode("listening");
  }
}

async function sendText() {
  const text = $("text-input").value.trim();
  if (!text || !state.callId || state.mode === "thinking") {
    return;
  }
  $("text-input").value = "";
  stopAudio();
  setMode("thinking");
  const userMsg = addBubble("user", text);
  showTyping();
  try {
    const data = await api(`/api/calls/${state.callId}/text`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    await handleTurnResponse(data, userMsg);
  } catch (err) {
    hideTyping();
    showError(err.message);
    setMode("listening");
  }
}

// ---------------- VAD (hands-free turn taking) ----------------
async function startVad() {
  state.vad = await vad.MicVAD.new({
    ...VAD_SETTINGS,
    model: "v5",
    baseAssetPath: "/static/vendor/vad/",
    onnxWASMBasePath: "/static/vendor/ort/",
    startOnLoad: false,
    // Barge-in: caller talks while the agent is speaking. We use onSpeechRealStart (fires only
    // after minSpeechMs of real speech) instead of onSpeechStart, because on laptop speakers the
    // agent's own voice leaks into the mic and would instantly cut the agent off.
    // Off by default; enable the checkbox when using headphones.
    onSpeechRealStart: () => {
      if (state.mode === "speaking" && $("allow-interrupt").checked) {
        stopAudio();
        setMode("listening");
      }
    },
    onSpeechEnd: (audio) => {
      if (state.mode === "listening") {
        sendAudio(audio);
      }
    },
    // Every ~30 ms audio frame: measure loudness so the orb can react to the caller's voice.
    onFrameProcessed: (probs, frame) => {
      if (!frame || !frame.length) {
        return;
      }
      let sum = 0;
      for (let i = 0; i < frame.length; i++) {
        sum += frame[i] * frame[i];
      }
      state.micLevel = Math.min(1, Math.sqrt(sum / frame.length) * 9);
    },
  });
  await state.vad.start(); // rejects if the microphone permission is denied -> caller sees "type instead"
}

// Stop the VAD safely (destroy() itself fails when the mic never started).
function destroyVad() {
  if (!state.vad) {
    return;
  }
  try {
    const result = state.vad.destroy();
    if (result && result.catch) {
      result.catch(() => {});
    }
  } catch (err) {
    // nothing to clean up
  }
  state.vad = null;
}

// ---------------- call lifecycle ----------------
function setStartBusy(busy) {
  $("start-btn").disabled = busy;
  $("start-label").textContent = busy ? "Connecting…" : "Start call";
}

async function startCall() {
  const body = {
    name: $("name").value.trim(),
    phone: $("phone").value.trim(),
    email: $("email").value.trim(),
    language: $("language").value,
  };
  if (!body.name || !body.phone) {
    $("start-error").textContent = "Enter a name and phone number to start the call.";
    (body.name ? $("phone") : $("name")).focus();
    return;
  }
  ensureAudioContext();
  $("start-error").textContent = "";
  setStartBusy(true);
  setMode("thinking", "Connecting to Priya…");

  let micReady = true;
  try {
    await startVad(); // asks for microphone permission
  } catch (err) {
    micReady = false;
    destroyVad();
    console.warn("Microphone/VAD not available, text mode only:", err);
  }

  try {
    const data = await api("/api/calls/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    state.callId = data.call_id;
    $("lead-avatar").textContent = initials(body.name);
    $("lead-name").textContent = body.name;
    $("lead-phone").textContent = body.phone;
    $("lang-chip").textContent = languageName(data.language);
    setPhase("incall");
    startTimer();
    if (!micReady) {
      showError("Microphone could not start. Type your messages in the transcript panel instead.");
    }
    if (data.returning_lead) {
      $("memory-chip").hidden = false;
      addNote("Returning lead · memory from earlier calls loaded");
    }
    await agentSays(data);
  } catch (err) {
    $("start-error").textContent = err.message;
    setStartBusy(false);
    setMode("idle");
    destroyVad();
  }
}

async function endCall() {
  if (!state.callId) {
    return;
  }
  const callId = state.callId;
  state.callId = null;
  stopAudio();
  hideTyping();
  destroyVad();
  stopTimer();
  setMode("thinking", "Writing the call summary…");
  $("end-btn").disabled = true;
  $("end-label").textContent = "Analysing…";
  setPhase("summary");
  $("summary").innerHTML = "";
  $("summary-card").hidden = true;

  try {
    const result = await api(`/api/calls/${callId}/end`, { method: "POST" });
    renderSummary(result);
    $("summary-card").hidden = false;
    setMode("ended");
    updateLead(result.lead);
  } catch (err) {
    showError(err.message);
    setPhase("incall");
    state.callId = callId;
    setMode("listening");
    $("end-btn").disabled = false;
    $("end-label").textContent = "End call";
  }
}

function crmLine(crm) {
  crm = crm || {};
  const icon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/></svg>';
  if (crm.hubspot_contact_id) {
    let text = `Synced to HubSpot · contact <code>${escapeHtml(crm.hubspot_contact_id)}</code>`;
    if (crm.hubspot_deal_id) {
      text += ` · deal <code>${escapeHtml(crm.hubspot_deal_id)}</code>`;
    }
    if (crm.hubspot_deal_error) {
      text += " · deal not updated";
    }
    return `<div class="crm-line ok">${icon}<span>${text} · AI summary added as a note</span></div>`;
  }
  if (crm.hubspot_error) {
    return `<div class="crm-line err">${icon}<span>HubSpot sync failed: ${escapeHtml(crm.hubspot_error)}. Saved in the local CRM.</span></div>`;
  }
  return `<div class="crm-line">${icon}<span>Saved in the local CRM (HubSpot not connected)</span></div>`;
}

function chipList(items, cls) {
  if (!items || !items.length) {
    return '<span class="muted">None</span>';
  }
  return `<div class="chips ${cls}">${items.map((x) => `<span class="c">${escapeHtml(x)}</span>`).join("")}</div>`;
}

function renderSummary(result) {
  const score = Number(result.sentiment_score) || 0;
  const pos = ((Math.max(-1, Math.min(1, score)) + 1) / 2) * 100;
  const outcome = OUTCOME_LABELS[result.outcome] || result.outcome || "No decision yet";
  $("summary").innerHTML = `
    <div style="display:grid;gap:18px">
      <div class="sum-head">
        <div><span class="eyebrow">Call summary</span><h2>${escapeHtml(outcome)}</h2></div>
        <span class="outcome ${escapeHtml(result.outcome || "")}">Call length ${escapeHtml($("timer").textContent)}</span>
      </div>
      <p class="sum-text">${escapeHtml(result.summary)}</p>
      <div class="meter">
        <span class="eyebrow">Caller sentiment · ${escapeHtml(result.sentiment || "neutral")} (${score.toFixed(2)})</span>
        <div class="meter-track"><span class="meter-mark" style="left:${pos}%"></span></div>
        <div class="meter-scale"><span>Negative</span><span>Neutral</span><span>Positive</span></div>
      </div>
      <div class="facts">
        <div class="fact"><span class="eyebrow">Interested in</span><div>${escapeHtml(result.interested_in || "Unknown")}</div></div>
        <div class="fact"><span class="eyebrow">Next step</span><div>${escapeHtml(result.next_step || "-")}</div></div>
        <div class="fact"><span class="eyebrow">Mood during call</span><div>${escapeHtml(result.sentiment_trend || "-")}</div></div>
      </div>
      <div class="facts">
        <div style="display:grid;gap:6px"><span class="eyebrow">Objections</span>${chipList(result.objections, "neg")}</div>
        <div style="display:grid;gap:6px"><span class="eyebrow">Buying signals</span>${chipList(result.buying_signals, "pos")}</div>
      </div>
      ${crmLine(result.crm)}
    </div>`;
}

// ---------------- page setup ----------------
async function loadHealth() {
  try {
    const health = await api("/api/health");
    state.languages = health.languages || {};
    for (const [code, name] of Object.entries(state.languages)) {
      const option = document.createElement("option");
      option.value = code;
      option.textContent = name;
      $("language").appendChild(option);
    }
    const services = [
      ["Gemini", health.gemini_key],
      ["Sarvam voice", health.sarvam_key],
      [health.crm === "local" ? "Local CRM" : "HubSpot CRM", true],
      [health.calendar === "local" ? "Local calendar" : "Cal.com", true],
    ];
    $("health").innerHTML = services
      .map(([name, ok]) => `<span class="svc${ok ? "" : " off"}">${escapeHtml(name)}</span>`)
      .join("");
    if (!health.gemini_key || !health.sarvam_key) {
      $("start-error").textContent = "API keys missing in .env (GEMINI_API_KEY / SARVAM_API_KEY).";
    }
  } catch (err) {
    $("start-error").textContent = "Backend not reachable: " + err.message;
  }
}

$("start-btn").addEventListener("click", startCall);
$("end-btn").addEventListener("click", endCall);
$("send-text-btn").addEventListener("click", sendText);
$("text-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    sendText();
  }
});
["name", "phone", "email"].forEach((id) => {
  $(id).addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      startCall();
    }
  });
});
document.querySelectorAll(".suggestion").forEach((button) => {
  button.addEventListener("click", () => {
    if (!state.callId) {
      return;
    }
    $("text-input").value = button.textContent;
    sendText();
  });
});

setMode("idle");
setPhase("precall");
updateLead({ score: 0, status: "NEW", bant: {} });
loadHealth();
