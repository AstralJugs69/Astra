"""Same-origin local presentation assets.

The watch browser receives only bounded snapshots from the loopback server. It
does not fetch cloud data, hold credentials, open tabs, or call a production
surface.
"""

WATCH_JAVASCRIPT = r"""(() => {
  "use strict";

  const status = document.getElementById("watch-connection");
  const automaticCycle = document.getElementById("watch-automatic-cycle");
  const stage = document.getElementById("watch-stage");
  const nextAction = document.getElementById("watch-next-action");
  const rows = document.getElementById("watch-incidents");
  const empty = document.getElementById("watch-empty");
  const hero = document.getElementById("watch-hero");
  const heroKicker = document.getElementById("watch-hero-kicker");
  const heroTitle = document.getElementById("watch-hero-title");
  const heroStatus = document.getElementById("watch-hero-status");
  const heroImpact = document.getElementById("watch-hero-impact");
  const heroLink = document.getElementById("watch-hero-link");
  const pipeline = document.getElementById("watch-pipeline");
  const banner = document.getElementById("mismatch-alert");
  const alertText = document.getElementById("mismatch-alert-text");
  const alertLive = document.getElementById("watch-alert-live");
  const enableSound = document.getElementById("enable-audible-alerts");
  const mute = document.getElementById("mute-audible-alerts");
  const acknowledge = document.getElementById("acknowledge-alert-locally");

  const orderedStages = ["DETECTED", "DIFF_READY", "CANDIDATE_READY", "IMPACT_READY", "SEMANTIC_READY", "REPORT_READY"];
  const stageLabels = {
    DETECTED: "Authoritative revision verified",
    DIFF_READY: "Source correction isolated",
    CANDIDATE_READY: "Candidate Braille regenerated",
    IMPACT_READY: "Braille page impact calculated",
    SEMANTIC_READY: "Gemini assessment recorded",
    REPORT_READY: "Professional report ready",
    NEEDS_REVIEW: "Stopped safely — human review required"
  };

  let audibleEnabled = false;
  let muted = false;
  let acknowledged = false;
  let audioContext = null;
  let alertTimer = null;
  let alertStartedAt = 0;
  let lastToneAt = 0;

  function setConnection(connected) {
    status.textContent = connected ? "Live connection" : "Reconnecting";
    status.dataset.state = connected ? "connected" : "disconnected";
  }

  function safeText(value, fallback) {
    return typeof value === "string" && value ? value : fallback;
  }

  function stageLabel(value) {
    return safeText(stageLabels[value], "Waiting for durable workflow evidence");
  }

  function rangeLabel(value) {
    if (!value || typeof value !== "object") return "not recorded";
    const start = value.start;
    const end = value.end;
    if (!Number.isInteger(start) || !Number.isInteger(end) || start < 1 || end < start) return "not recorded";
    return start === end ? String(start) : String(start) + "–" + String(end);
  }

  function highlightText(highlight) {
    if (!highlight || typeof highlight !== "object") return "Deterministic page impact is available in the incident report.";
    const oldRange = rangeLabel(highlight.old_page_range);
    const newRange = rangeLabel(highlight.new_page_range);
    const total = Number.isInteger(highlight.candidate_page_count) ? highlight.candidate_page_count : null;
    const resync = Number.isInteger(highlight.resynchronized_after_page) ? " Suffix resynchronized after page " + String(highlight.resynchronized_after_page) + "." : "";
    return "Braille impact: baseline pages " + oldRange + "; candidate pages " + newRange + (total ? " of " + String(total) : "") + "." + resync;
  }

  function renderPipeline(stageValue) {
    if (!pipeline) return;
    const steps = pipeline.querySelectorAll("[data-stage]");
    const stageIndex = orderedStages.indexOf(stageValue);
    steps.forEach((step) => {
      const itemStage = step.dataset.stage;
      step.classList.remove("complete", "current", "waiting", "blocked");
      if (stageValue === "NEEDS_REVIEW") {
        const itemIndex = orderedStages.indexOf(itemStage);
        step.classList.add(itemStage === "REPORT_READY" ? "blocked" : itemIndex >= 0 && itemIndex < 5 ? "complete" : "waiting");
      } else if (stageIndex >= 0 && orderedStages.indexOf(itemStage) < stageIndex) {
        step.classList.add("complete");
      } else if (itemStage === stageValue) {
        step.classList.add("current");
      } else {
        step.classList.add("waiting");
      }
    });
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
      link.textContent = "Review incident " + incident.incident_id.slice(0, 12) + "…";
      const meta = document.createElement("span");
      meta.textContent = safeText(incident.workflow_label, stageLabel(incident.workflow_stage)) + " — " + safeText(incident.next_safe_action, "Review authoritative evidence.");
      item.append(link, meta);
      rows.append(item);
    });
  }

  function automationLabel(automation) {
    if (!automation || typeof automation !== "object") return "Automatic status temporarily unavailable";
    if (automation.state === "UNAVAILABLE") return "Automatic status temporarily unavailable";
    if (automation.state === "NOT_YET_RUN") return "Waiting for the background scheduler";
    if (automation.state === "RUNNING") return "Checking authoritative Drive source";
    if (automation.last_outcome === "FAILED") return "Last automatic cycle failed safely; inspect scheduler and error state";
    if (automation.last_status === "NEEDS_ATTENTION") return "Automatic cycle needs attention";
    if (automation.last_status === "SOURCE_UNAVAILABLE") return "Authoritative source is currently unavailable";
    if (automation.source_investigation_pending === true) return "Drive source content detected; investigation is queued";
    if (automation.content_equivalent_replay === true) return "Drive revision matched existing source bytes; no new investigation";
    if (automation.source_change_detected === true) return "Drive source content detected; durable workflow advanced";
    if (automation.last_status === "COMPLETED") return "Completed; no new source content requiring investigation";
    return "Waiting for durable automatic-cycle evidence";
  }

  function renderHero(lead) {
    if (!hero || !heroKicker || !heroTitle || !heroStatus || !heroImpact || !heroLink) return;
    const highlight = lead && lead.watch_highlight;
    const isResult = lead && highlight && (lead.workflow_stage === "REPORT_READY" || lead.workflow_stage === "NEEDS_REVIEW");
    hero.hidden = !isResult;
    if (!isResult) return;
    const needsReview = lead.workflow_stage === "NEEDS_REVIEW";
    heroKicker.textContent = needsReview ? "Material issue detected — safe human review required" : "Professional recovery report ready";
    heroTitle.textContent = safeText(highlight.materiality, "MATERIAL") + " " + safeText(highlight.change_kind, "CORRECTION").replaceAll("_", " ");
    heroStatus.textContent = needsReview ? "Astra completed the bounded investigation and stopped safely for qualified human review." : "The bounded autonomous investigation is complete; a production coordinator can review the recovery report.";
    heroImpact.textContent = highlightText(highlight);
    heroLink.href = "/incidents/" + encodeURIComponent(lead.incident_id);
  }

  function renderSnapshot(payload) {
    const snapshot = payload && payload.snapshot;
    if (!snapshot || typeof snapshot !== "object") return;
    const incidents = Array.isArray(snapshot.incidents) ? snapshot.incidents : [];
    const lead = incidents[0] || null;
    automaticCycle.textContent = automationLabel(snapshot.automation);
    stage.textContent = lead ? safeText(lead.workflow_label, stageLabel(lead.workflow_stage)) : "Monitoring authoritative source";
    nextAction.textContent = lead ? safeText(lead.next_safe_action, "Review authoritative evidence.") : "No incident is currently awaiting review.";
    renderPipeline(lead ? lead.workflow_stage : "");
    renderHero(lead);
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
    banner.hidden = false;
    alertText.textContent = "A newly observed durable transition requires human review (" + safeText(incident && incident.workflow_label, stageLabel(incident && incident.workflow_stage)) + ").";
    alertLive.textContent = "Source / production mismatch; human review required.";
    startSound();
  }

  enableSound.addEventListener("click", () => {
    audibleEnabled = true;
    muted = false;
    acknowledged = false;
    enableSound.textContent = "Audible alerts enabled";
    startSound();
  });
  mute.addEventListener("click", () => { muted = true; stopSound(); });
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
    try { automaticCycle.textContent = automationLabel(JSON.parse(event.data).automation); setConnection(true); } catch (_) { setConnection(false); }
  });
  events.addEventListener("report_ready", (event) => { try { showAlert(JSON.parse(event.data)); } catch (_) {} });
  events.addEventListener("review_required", (event) => { try { showAlert(JSON.parse(event.data)); } catch (_) {} });
  events.addEventListener("heartbeat", () => setConnection(true));
  events.addEventListener("upstream_unavailable", () => {
    setConnection(false);
    alertLive.textContent = "Authoritative review data is temporarily unavailable; retrying locally.";
  });
  events.onerror = () => setConnection(false);
})();
"""


REPORT_JAVASCRIPT = r"""(() => {
  "use strict";
  const button = document.getElementById("print-report");
  if (button) button.addEventListener("click", () => window.print());
})();
"""
