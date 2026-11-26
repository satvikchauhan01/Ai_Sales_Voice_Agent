// Call page logic.
// Flow: start call -> play greeting -> VAD detects caller speech -> send WAV to backend
//       -> backend does STT + LLM + TTS -> play reply -> listen again.
// Modes: idle | listening | thinking | speaking

const VAD_SETTINGS = {
  positiveSpeechThreshold: 0.5,  // probability above which a frame counts as speech
  negativeSpeechThreshold: 0.35, // probability below which a frame counts as silence
  redemptionMs: 800,             // silence needed before we decide the caller finished
  minSpeechMs: 300,              // ignore very short noises
  preSpeechPadMs: 300,           // keep a little audio before speech started
};

const state = {
  callId: null,
  mode: "idle",
  vad: null,
  audio: null,
  audioDone: null,
};

const $ = (id) => document.getElementById(id);

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

function setMode(mode) {
  state.mode = mode;
  const labels = { idle: "Idle", listening: "Listening...", thinking: "Thinking...", speaking: "Speaking" };
  $("status").textContent = labels[mode];
  $("status").className = "pill " + mode;
}

function addBubble(role, text, meta) {
  const box = $("transcript");
  if (box.querySelector(".muted")) {
    box.innerHTML = "";
  }
  const bubble = document.createElement("div");
  bubble.className = "bubble " + role;
  bubble.textContent = text;
  if (meta) {
    const small = document.createElement("span");
    small.className = "meta";
    small.textContent = meta;
    bubble.appendChild(small);
  }
  box.appendChild(bubble);
  box.scrollTop = box.scrollHeight;
}

function updateLead(lead) {
  if (!lead) {
    return;
  }
  $("lead-score").textContent = lead.score;
  $("lead-status").textContent = lead.status;
  $("lead-status").className = "badge " + lead.status;
  const chips = ["need", "budget", "authority", "timeline"].map((key) => {
    const answer = lead.bant[key];
    if (!answer) {
      return `<span>${key}: ?</span>`;
    }
    const cls = answer.qualified ? "yes" : "no";
    return `<span class="${cls}" title="${escapeHtml(answer.note)}">${key}: ${answer.qualified ? "yes" : "no"}</span>`;
  });
  $("bant").innerHTML = chips.join("");
}

function showError(message) {
  $("call-error").textContent = message;
  setTimeout(() => { $("call-error").textContent = ""; }, 8000);
}

// ---------------- audio playback ----------------
function stopAudio() {
  if (state.audio) {
    state.audio.pause();
    state.audio = null;
  }
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
    audio.onended = () => stopAudio();
    audio.onerror = () => stopAudio();
    audio.play().catch(() => stopAudio());
  });
}

async function agentSays(data) {
  addBubble("agent", data.agent_text, data.language);
  updateLead(data.lead);
  setMode("speaking");
  await playAudio(data.audio_b64);
  if (state.callId && state.mode === "speaking") {
    setMode("listening");
  }
}

// ---------------- conversation turns ----------------
async function handleTurnResponse(data) {
  if (data.busy || data.empty) {
    setMode("listening");
    return;
  }
  let meta = data.language;
  if (data.latency_ms) {
    const l = data.latency_ms;
    meta += ` | stt ${l.stt ?? "-"}ms, llm ${l.llm}ms, tts ${l.tts}ms`;
  }
  addBubble("user", data.user_text, meta);
  await agentSays(data);
}

async function sendAudio(float32Audio) {
  setMode("thinking");
  // 16-bit PCM WAV at 16 kHz (format 1 = PCM)
  const wavBuffer = vad.utils.encodeWAV(float32Audio, 1, 16000, 1, 16);
  const form = new FormData();
  form.append("audio", new Blob([wavBuffer], { type: "audio/wav" }), "speech.wav");
  try {
    const data = await api(`/api/calls/${state.callId}/turn`, { method: "POST", body: form });
    await handleTurnResponse(data);
  } catch (err) {
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
  try {
    const data = await api(`/api/calls/${state.callId}/text`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    await handleTurnResponse(data);
  } catch (err) {
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
  });
  state.vad.start();
}

// ---------------- call lifecycle ----------------
async function startCall() {
  const body = {
    name: $("name").value.trim(),
    phone: $("phone").value.trim(),
    email: $("email").value.trim(),
    language: $("language").value,
  };
  if (!body.name || !body.phone) {
    $("start-error").textContent = "Name and phone are required.";
    return;
  }
  $("start-error").textContent = "";
  $("start-btn").disabled = true;
  $("start-btn").textContent = "Connecting...";

  let micReady = true;
  try {
    await startVad(); // asks for microphone permission
  } catch (err) {
    micReady = false;
    console.error(err);
  }

  try {
    const data = await api("/api/calls/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    state.callId = data.call_id;
    $("lead-card").classList.add("hidden");
    $("call-card").classList.remove("hidden");
    if (!micReady) {
      showError("Microphone/VAD could not start - use the text box instead.");
    }
    if (data.returning_lead) {
      addBubble("agent", "(Returning lead - memory from earlier calls loaded)", "");
    }
    await agentSays(data);
  } catch (err) {
    $("start-error").textContent = err.message;
    $("start-btn").disabled = false;
    $("start-btn").textContent = "Start call";
    if (state.vad) {
      state.vad.destroy();
    }
  }
}

async function endCall() {
  if (!state.callId) {
    return;
  }
  const callId = state.callId;
  state.callId = null;
  stopAudio();
  if (state.vad) {
    state.vad.destroy();
    state.vad = null;
  }
  setMode("idle");
  $("end-btn").disabled = true;
  $("end-btn").textContent = "Analysing call...";

  try {
    const result = await api(`/api/calls/${callId}/end`, { method: "POST" });
    $("call-card").classList.add("hidden");
    $("summary-card").classList.remove("hidden");
    const objections = result.objections.length ? result.objections.join(", ") : "none";
    $("summary").innerHTML = `
      <p>${escapeHtml(result.summary)}</p>
      <p><strong>Outcome:</strong> ${escapeHtml(result.outcome)} &nbsp; <strong>Sentiment:</strong> ${escapeHtml(result.sentiment)} (${result.sentiment_score})</p>
      <p><strong>Trend:</strong> ${escapeHtml(result.sentiment_trend || "-")}</p>
      <p><strong>Objections:</strong> ${escapeHtml(objections)}</p>
      <p><strong>Next step:</strong> ${escapeHtml(result.next_step)}</p>
      <p><strong>Lead:</strong> <span class="badge ${result.lead.status}">${result.lead.status}</span> score ${result.lead.score}/100</p>
      <p class="muted">CRM sync: ${escapeHtml(JSON.stringify(result.crm))}</p>`;
  } catch (err) {
    showError(err.message);
    $("end-btn").disabled = false;
    $("end-btn").textContent = "End call";
  }
}

async function loadLanguages() {
  try {
    const health = await api("/api/health");
    for (const [code, name] of Object.entries(health.languages)) {
      const option = document.createElement("option");
      option.value = code;
      option.textContent = name;
      $("language").appendChild(option);
    }
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
loadLanguages();
