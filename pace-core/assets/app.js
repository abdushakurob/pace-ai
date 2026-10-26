const views = Object.fromEntries([...document.querySelectorAll(".view")].map((view) => [view.id.replace("-view", ""), view]));
const timeline = document.querySelector("#timeline");
const toast = document.querySelector("#toast");
const focusDialog = document.querySelector("#focus-dialog");
let exampleMode = false;
const attention = document.querySelector("#attention-card");
const alerted = new Set();
let snoozedUntil = 0;

const exampleDay = {
  date: "Thursday, 23 October",
  activity: { app: "Code.exe", window: "calendar_sync.py — pace-core", idle_seconds: 12, paused: false, at: new Date().toISOString() },
  context: { analysis: { activity: "Implementing the calendar sync endpoint", related: true, confidence: 0.91, evidence: ["VS Code is open in calendar_sync.py", "Calendar sync tests ran in the terminal"] } },
  current_block: { title: "Calendar sync endpoint", start: "2026-10-23T13:00:00+01:00", end: "2026-10-23T14:30:00+01:00", related_app: "Linear · ENG-142", kind: "flexible" },
  timeline: [
    { title: "Calendar sync endpoint", start: "2026-10-23T13:00:00+01:00", end: "2026-10-23T14:30:00+01:00", kind: "flexible" },
    { title: "Design Review", start: "2026-10-23T15:00:00+01:00", end: "2026-10-23T15:45:00+01:00", kind: "fixed" },
    { title: "Review onboarding flow", start: "2026-10-23T16:15:00+01:00", end: "2026-10-23T17:00:00+01:00", kind: "flexible" },
    { title: "Protected evening", start: "2026-10-23T18:00:00+01:00", end: "2026-10-23T20:00:00+01:00", kind: "fixed" },
  ],
};

function showView(name, updateUrl = true) {
  if (!views[name]) name = "today";
  Object.entries(views).forEach(([key, node]) => node.classList.toggle("active", key === name));
  document.querySelectorAll(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === name));
  if (updateUrl) history.pushState({ view: name }, "", name === "today" ? "/app" : `/app/${name}`);
}
function formatTime(value) { return new Intl.DateTimeFormat([], { hour: "2-digit", minute: "2-digit" }).format(new Date(value)); }
function render(data) {
  const planned = data.current_block || data.timeline.find((item) => item.kind === "flexible");
  const analysis = data.context?.analysis;
  const suggestion = data.suggestion;
  const activity = data.activity;
  const google = (data.connections || []).find((connection) => connection.provider === "google");
  const ai = data.ai || {};
  const sync = document.querySelector(".sync-google");
  const connectGoogle = document.querySelector(".connect-google");
  sync.hidden = !google;
  connectGoogle.hidden = Boolean(google);
  document.querySelector("#ai-mode").value = ["uno-q", "openai-compatible"].includes(ai.mode) ? ai.mode : "off";
  document.querySelector("#ai-base-url").value = ai.base_url || "";
  document.querySelector("#ai-model").value = ai.model || "";
  const shareLink = document.querySelector("#share-link");
  shareLink.href = data.share_url || window.location.origin;
  shareLink.textContent = data.share_url || window.location.origin;
  document.querySelector("#pairing-qr").src = "/api/pairing-qr?at=" + Date.now();
  document.querySelector("#date-label").textContent = data.date || "Your workday";
  document.querySelector("#greeting").textContent = data.greeting ? `${data.greeting}.` : "Start with what needs to happen.";
  document.querySelector("#observer-title").textContent = activity?.paused ? "Observer paused" : activity ? "Observer active" : "Observer ready";
  document.querySelector("#observer-detail").textContent = activity?.paused ? "Pace is not collecting context" : activity ? (activity.window || activity.app) : "Connect this computer when you are ready";
  document.querySelector("#focus-heading").textContent = planned?.title || "Connect your workday";
  document.querySelector("#focus-description").textContent = analysis?.activity || (planned ? "Pace is waiting for context from this computer." : "Connect a calendar and task tool to create your first Pace plan.");
  document.querySelector("#focus-time").textContent = planned ? `${formatTime(planned.start)}–${formatTime(planned.end)}` : "No focus block yet";
  document.querySelector("#focus-source").textContent = planned?.related_app || "Pace plan";
  document.querySelector("#focus-remaining").textContent = planned ? "Current focus block" : "Start with your calendar";
  const state = document.querySelector("#focus-state");
  state.textContent = analysis?.related ? "On track" : planned ? "Waiting for context" : "Set up Pace";
  state.className = analysis?.related ? "on-track" : "waiting-state";
  document.querySelector("#evidence-one").textContent = analysis?.evidence?.[0] || activity?.window || "No desktop context yet";
  document.querySelector("#evidence-two").textContent = analysis?.evidence?.[1] || "Pace only uses context you enable";
  const suggestionCard = document.querySelector(".suggestion-card");
  suggestionCard.hidden = !suggestion;
  if (suggestion) {
    document.querySelector(".suggestion-copy h2").textContent = suggestion.summary;
    document.querySelector(".suggestion-copy p:last-child").textContent = suggestion.detail;
    document.querySelector("#apply-suggestion").dataset.proposalId = suggestion.id;
    document.querySelector("#keep-plan").dataset.proposalId = suggestion.id;
  }
  timeline.replaceChildren();
  for (const item of data.timeline) {
    const node = document.createElement("li");
    const active = planned && item.title === planned.title;
    node.className = `timeline-item ${active ? "current" : ""}`;
    node.innerHTML = `<time>${formatTime(item.start)}</time><span class="timeline-line"></span><div><strong>${escapeHtml(item.title)}</strong><p>${item.kind === "fixed" ? "Commitment" : "Focus block"} · ${formatTime(item.start)}–${formatTime(item.end)}</p></div><span class="event-kind">${item.kind === "fixed" ? "Fixed" : "Flexible"}</span>`;
    timeline.append(node);
  }
  maybeNotify(data, planned, analysis);
}
function maybeNotify(data, planned, analysis) {
  if (!planned || analysis?.related === false || Date.now() < snoozedUntil) return;
  const next = data.timeline.filter((item) => item.kind === "fixed" && new Date(item.start) > new Date()).sort((a, b) => new Date(a.start) - new Date(b.start))[0];
  if (!next) return;
  const minutes = Math.round((new Date(next.start) - new Date()) / 60000);
  if (minutes > 20 || minutes < 0) return;
  const key = `${next.title}:${next.start}`;
  if (alerted.has(key)) return;
  alerted.add(key);
  document.querySelector("#attention-title").textContent = `${next.title} starts in ${minutes} minutes.`;
  document.querySelector("#attention-detail").textContent = `You are still focused on ${planned.title}. Pace can protect a stopping point so you have time to prepare.`;
  attention.hidden = false;
  if ("Notification" in window && Notification.permission === "granted") new Notification("Pace", { body: `${next.title} starts in ${minutes} minutes. Choose your next step.` });
}
function escapeHtml(value) { const node = document.createElement("span"); node.textContent = value || "Untitled"; return node.innerHTML; }
async function load() {
  try {
    const response = await fetch("/api/state");
    if (!response.ok) throw new Error("Pace Hub is unavailable");
    const state = await response.json();
    render(state);
  } catch (_error) { render({ date: "Pace is offline", timeline: [], connections: [], ai: {}, greeting: "Pace is unavailable" }); }
}
async function tellPace(text) {
  if (exampleMode) return showToast("Pace would record this choice in your real workspace.");
  const response = await fetch("/api/instructions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
  const result = await response.json();
  showToast(result.ok ? "Pace saved your decision." : result.error || "Pace could not save that.");
  load();
}
function showToast(message) { toast.textContent = message; toast.classList.add("show"); window.setTimeout(() => toast.classList.remove("show"), 3500); }
document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => showView(button.dataset.view)));
window.addEventListener("popstate", () => showView(location.pathname.split("/")[2] || "today", false));
document.querySelector("#enable-alerts").addEventListener("click", async () => {
  if (!("Notification" in window)) return showToast("This browser does not support notifications.");
  const permission = await Notification.requestPermission();
  showToast(permission === "granted" ? "Pace alerts are enabled for this browser." : "Alerts remain off. You can enable them in browser settings later.");
});
document.querySelector("#attention-snooze").addEventListener("click", () => { snoozedUntil = Date.now() + 10 * 60 * 1000; attention.hidden = true; showToast("Pace will stay quiet for 10 minutes."); });
document.querySelector("#attention-prepare").addEventListener("click", () => { attention.hidden = true; tellPace("Prepare now for my next commitment."); });
document.querySelector("#attention-continue").addEventListener("click", () => { attention.hidden = true; tellPace("Continue until the suggested stopping point."); });
document.querySelector("#apply-suggestion").addEventListener("click", async (event) => {
  const id = event.currentTarget.dataset.proposalId;
  if (!id) return tellPace("Use the suggested plan and prepare for the next meeting.");
  const response = await fetch("/api/proposals/apply", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id }) });
  const result = await response.json();
  showToast(result.ok ? "Pace added the approved focus block." : result.error || "Pace could not apply that proposal.");
  if (result.ok) load();
});
document.querySelector("#keep-plan").addEventListener("click", async (event) => {
  const id = event.currentTarget.dataset.proposalId;
  if (!id) return tellPace("Keep the current plan.");
  const response = await fetch("/api/proposals/keep", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id }) });
  const result = await response.json();
  showToast(result.ok ? "Pace will leave this plan alone." : result.error || "Pace could not save that choice.");
  if (result.ok) load();
});
document.querySelector("#add-block").addEventListener("click", () => {
  document.querySelector("#capture-text").value = "";
  document.querySelector("#focus-error").textContent = "";
  focusDialog.showModal();
});
document.querySelector("#focus-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (event.submitter?.value === "cancel") { focusDialog.close(); return; }
  if (exampleMode) return showToast("Exit preview mode by refreshing Pace, then add a real focus block.");
  const button = document.querySelector("#focus-form .primary-action");
  const error = document.querySelector("#focus-error");
  const originalLabel = button.textContent;
  button.disabled = true;
  button.textContent = "Finding a time…";
  button.setAttribute("aria-busy", "true");
  focusDialog.classList.add("is-loading");
  error.textContent = "Pace is checking your calendar and planning an opening…";
  try {
    const response = await fetch("/api/capture", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: document.querySelector("#capture-text").value.trim() }) });
    const result = await response.json();
    if (!result.ok) { error.textContent = result.error || "Pace could not find a time."; return; }
    focusDialog.close();
    showToast(`Pace found a ${formatTime(result.proposal.start)} slot. Review the proposal before it is added.`);
    load();
  } catch (_error) {
    error.textContent = "Pace could not reach the planning service. Try again.";
  } finally {
    button.disabled = false;
    button.textContent = originalLabel;
    button.removeAttribute("aria-busy");
    focusDialog.classList.remove("is-loading");
  }
});
document.querySelector("#pause-observer").addEventListener("click", () => showToast("Use the Pace desktop observer to pause local context collection."));
document.querySelectorAll(".connection-card button").forEach((button) => button.addEventListener("click", () => {
  if (button.classList.contains("sync-google")) return;
  const name = button.closest(".connection-card").querySelector("h2").textContent;
  const provider = name === "Google Calendar" ? "google" : name === "Linear" ? "linear" : "";
  if (!provider) return showToast("Custom MCP setup is next after the built-in connectors.");
  window.location.assign(`/api/connect/${provider}`);
}));
document.querySelector(".sync-google").addEventListener("click", async () => {
  const response = await fetch("/api/google/sync", { method: "POST" });
  const result = await response.json();
  showToast(result.ok ? "Google Calendar is synced." : result.error || "Google Calendar could not sync.");
  if (result.ok) load();
});
document.querySelector("#ai-settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const response = await fetch("/api/ai/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
    mode: document.querySelector("#ai-mode").value,
    base_url: document.querySelector("#ai-base-url").value.trim(),
    model: document.querySelector("#ai-model").value.trim(),
    api_key: document.querySelector("#ai-api-key").value.trim(),
  }) });
  const result = await response.json();
  document.querySelector("#ai-settings-error").textContent = result.ok ? "" : (result.error || "Pace could not save AI settings.");
  if (result.ok) { document.querySelector("#ai-api-key").value = ""; showToast("AI settings saved. Pace can now plan tasks with this model."); load(); }
});
document.querySelector("#copy-share-link").addEventListener("click", async () => {
  const value = document.querySelector("#share-link").href;
  try { await navigator.clipboard.writeText(value); showToast("Pace link copied."); }
  catch (_error) { showToast(value); }
});
showView(location.pathname.split("/")[2] || "today", false);
load();
window.setInterval(() => { if (!exampleMode) load(); }, 15000);
