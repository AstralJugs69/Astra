"""Same-origin local presentation assets.

Assets are served only by the loopback presentation app.  They contain no
cloud URL, credential, service-account, Drive, or production-control details.
"""

WATCH_JAVASCRIPT = r"""(() => {
  "use strict";

  const status = document.getElementById("watch-connection");
  const automaticCycle = document.getElementById("watch-automatic-cycle");
  const stage = document.getElementById("watch-stage");
  const nextAction = document.getElementById("watch-next-action");
  const rows = document.getElementById("watch-incidents");
  const empty = document.getElementById("watch-empty");
  const banner = document.getElementById("mismatch-alert");
  const alertText = document.getElementById("mismatch-alert-text");
  const alertLive = document.getElementById("watch-alert-live");
  const enableSound = document.getElementById("enable-audible-alerts");
  const mute = document.getElementById("mute-audible-alerts");
  const acknowledge = document.getElementById("acknowledge-alert-locally");

  let audibleEnabled = false;
  let muted = false;
  let acknowledged = false;
  let audioContext = null;
  let alertTimer = null;
  let alertStartedAt = 0;
  let lastToneAt = 0;

  function setConnection(connected) {
    status.textContent = connected ? "Connected" : "Reconnecting";
    status.dataset.state = connected ? "connected" : "disconnected";
  }

  function safeText(value, fallback) {
    return typeof value === "string" && value ? value : fallback;
  }

  function renderRows(incidents) {
    rows.replaceChildren();
    const values = Array.isArray(incidents) ? incidents : [];
    empty.hidden = values.length !== 0;
    values.forEach((incident) => {
      if (!incident || typeof incident.incident_id !== "string") return;
      const item = document.createElement("li");
      item.className = "watch-incident";
      const link = document.createElement("a");
      link.href = "/incidents/" + encodeURIComponent(incident.incident_id);
      link.textContent = "Incident " + incident.incident_id.slice(0, 12) + "…";
      const meta = document.createElement("span");
      meta.textContent = safeText(incident.workflow_stage, "DETECTED") + " — " + safeText(incident.next_safe_action, "Review authoritative evidence.");
      item.append(link, meta);
      rows.append(item);
    });
  }

  function automationLabel(automation) {
    if (!automation || typeof automation !== "object") return "Automatic status temporarily unavailable";
    if (automation.state === "UNAVAILABLE") return "Automatic status temporarily unavailable";
    if (automation.state === "NOT_YET_RUN") return "Waiting for the background scheduler";
    if (automation.state === "RUNNING") return "Checking authoritative Drive source";
    let label = "Waiting for durable automatic-cycle evidence";
    if (automation.last_outcome === "FAILED") label = "Last automatic cycle failed safely; inspect scheduler and error state";
    else if (automation.last_status === "NEEDS_ATTENTION") label = "Automatic cycle needs attention";
    else if (automation.last_status === "SOURCE_UNAVAILABLE") label = "Authoritative source is currently unavailable";
    else if (automation.source_investigation_pending === true) label = "Drive source content detected; investigation is queued";
    else if (automation.content_equivalent_replay === true) label = "Drive revision matched existing source bytes; no new investigation";
    else if (automation.source_change_detected === true) label = "Drive source content detected; durable workflow advanced";
    else if (automation.last_status === "COMPLETED") label = "Completed; no new source content requiring investigation";
    if (automation.state === "IDLE" && typeof automation.last_completed_at === "string") {
      return label + " · " + automation.last_completed_at;
    }
    return label;
  }

  function renderAutomation(automation) {
    automaticCycle.textContent = automationLabel(automation);
  }

  function renderSnapshot(payload) {
    const snapshot = payload && payload.snapshot;
    if (!snapshot || typeof snapshot !== "object") return;
    const incidents = Array.isArray(snapshot.incidents) ? snapshot.incidents : [];
    renderAutomation(snapshot.automation);
    const lead = incidents[0] || null;
    stage.textContent = lead ? safeText(lead.workflow_stage, "DETECTED") : "WATCHING";
    nextAction.textContent = lead ? safeText(lead.next_safe_action, "Review authoritative evidence.") : "No incident is currently awaiting review.";
    renderRows(incidents);
  }

  function stopSound() {
    if (alertTimer !== null) {
      window.clearInterval(alertTimer);
      alertTimer = null;
    }
  }

  function tone() {
    if (!audibleEnabled || muted || acknowledged) return;
    const now = Date.now();
    if (now - lastToneAt < 10000) return;
    lastToneAt = now;
    try {
      audioContext = audioContext || new window.AudioContext();
      const oscillator = audioContext.createOscillator();
      const gain = audioContext.createGain();
      oscillator.frequency.setValueAtTime(740, audioContext.currentTime);
      gain.gain.setValueAtTime(0.045, audioContext.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.0001, audioContext.currentTime + 0.18);
      oscillator.connect(gain).connect(audioContext.destination);
      oscillator.start();
      oscillator.stop(audioContext.currentTime + 0.2);
    } catch (_) {
      /* Audio remains opt-in and failure is safely silent. */
    }
  }

  function startSound() {
    if (!audibleEnabled || muted || acknowledged) return;
    alertStartedAt = Date.now();
    tone();
    stopSound();
    alertTimer = window.setInterval(() => {
      if (Date.now() - alertStartedAt >= 60000 || muted || acknowledged) {
        stopSound();
        return;
      }
      tone();
    }, 10000);
  }

  function showAlert(payload) {
    if (acknowledged) return;
    const incident = payload && payload.incident;
    const stageValue = incident && safeText(incident.workflow_stage, "REVIEW");
    banner.hidden = false;
    alertText.textContent = "A newly observed durable transition requires human review (" + stageValue + ").";
    alertLive.textContent = "Source production mismatch; human review required.";
    startSound();
  }

  enableSound.addEventListener("click", () => {
    audibleEnabled = true;
    muted = false;
    acknowledged = false;
    enableSound.textContent = "Audible alerts enabled";
    startSound();
  });
  mute.addEventListener("click", () => {
    muted = true;
    stopSound();
  });
  acknowledge.addEventListener("click", () => {
    acknowledged = true;
    banner.hidden = true;
    stopSound();
    alertLive.textContent = "Alert acknowledged locally only. No professional record or production action was created.";
  });

  const events = new window.EventSource("/events");
  events.addEventListener("snapshot", (event) => {
    try { renderSnapshot(JSON.parse(event.data)); setConnection(true); } catch (_) { setConnection(false); }
  });
  events.addEventListener("incident_detected", () => setConnection(true));
  events.addEventListener("stage_changed", () => setConnection(true));
  events.addEventListener("automation_cycle", (event) => {
    try {
      const payload = JSON.parse(event.data);
      renderAutomation(payload && payload.automation);
      setConnection(true);
    } catch (_) { setConnection(false); }
  });
  events.addEventListener("report_ready", (event) => {
    try { showAlert(JSON.parse(event.data)); } catch (_) { /* Keep the monitor read-only. */ }
  });
  events.addEventListener("review_required", (event) => {
    try { showAlert(JSON.parse(event.data)); } catch (_) { /* Keep the monitor read-only. */ }
  });
  events.addEventListener("heartbeat", () => setConnection(true));
  events.addEventListener("upstream_unavailable", () => {
    setConnection(false);
    alertLive.textContent = "Authoritative review data is temporarily unavailable; retrying locally.";
  });
  events.onerror = () => setConnection(false);
})();
"""
