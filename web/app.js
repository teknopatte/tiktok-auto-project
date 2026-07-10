let dashboard = null;
let loadedAnalysisKey = null;
const ACCOUNT_NICHE = "Divertissement / gaming";
const ACCOUNT_ALLOWED_NICHES = ["Divertissement pur", "Gaming"];

const DEFAULT_RUN_OPTIONS = {
  dryRun: false,
  sinceHours: 24,
  maxVideosPerChannel: 1,
  limit: "",
  cookiesFromBrowser: "",
  outputRoot: "",
  includeUndated: true,
  forceResolve: false,
  clipSegmentSeconds: 60,
  skipSplit: false,
  satisfyingRoot: "videos_satisfaisantes",
  skipVerticalRender: false,
  autoPublishTikTok: false,
  tiktokPrivacyLevel: "SELF_ONLY",
  tiktokCaptionTemplate: "{title} #{niche} #fyp",
  tiktokPublishLimit: 1,
  tiktokPublishDelayMinSeconds: 600,
  tiktokPublishDelayMaxSeconds: 1200,
  allowedNiches: ACCOUNT_ALLOWED_NICHES,
};

const els = {
  refreshBtn: document.querySelector("#refreshBtn"),
  stopBtn: document.querySelector("#stopBtn"),
  lastRefresh: document.querySelector("#lastRefresh"),
  autoState: document.querySelector("#autoState"),
  autoEnabled: document.querySelector("#autoEnabled"),
  statDownloaded: document.querySelector("#statDownloaded"),
  statPublished: document.querySelector("#statPublished"),
  statViews: document.querySelector("#statViews"),
  jobStatus: document.querySelector("#jobStatus"),
  activeJob: document.querySelector("#activeJob"),
  activeJobTitle: document.querySelector("#activeJobTitle"),
  activeJobLine: document.querySelector("#activeJobLine"),
  downloadList: document.querySelector("#downloadList"),
  logs: document.querySelector("#logs"),
  tokenState: document.querySelector("#tokenState"),
  tiktokConnectionTitle: document.querySelector("#tiktokConnectionTitle"),
  tiktokConnectionDetails: document.querySelector("#tiktokConnectionDetails"),
  connectTikTokBtn: document.querySelector("#connectTikTokBtn"),
  disconnectTikTokBtn: document.querySelector("#disconnectTikTokBtn"),
  anaPublished: document.querySelector("#anaPublished"),
  anaViews: document.querySelector("#anaViews"),
  anaShorts: document.querySelector("#anaShorts"),
  anaAverage: document.querySelector("#anaAverage"),
  publishedList: document.querySelector("#publishedList"),
  youtubeForm: document.querySelector("#youtubeForm"),
  youtubeUrl: document.querySelector("#youtubeUrl"),
  manualNiche: document.querySelector("#manualNiche"),
  manualChannel: document.querySelector("#manualChannel"),
  testOneShort: document.querySelector("#testOneShort"),
  delayMinMinutes: document.querySelector("#delayMinMinutes"),
  delayMaxMinutes: document.querySelector("#delayMaxMinutes"),
  satisfyingForm: document.querySelector("#satisfyingForm"),
  satisfyingUrl: document.querySelector("#satisfyingUrl"),
  satisfyingCount: document.querySelector("#satisfyingCount"),
  folderButtons: [...document.querySelectorAll(".folder-btn")],
  cleanupFailedBtn: document.querySelector("#cleanupFailedBtn"),
  analysisForm: document.querySelector("#analysisForm"),
  analysisVideoSelect: document.querySelector("#analysisVideoSelect"),
  analysisVideoPath: document.querySelector("#analysisVideoPath"),
  analysisDurations: document.querySelector("#analysisDurations"),
  analysisStep: document.querySelector("#analysisStep"),
  analysisSilenceThreshold: document.querySelector("#analysisSilenceThreshold"),
  analysisModel: document.querySelector("#analysisModel"),
  analysisDevice: document.querySelector("#analysisDevice"),
  analysisComputeType: document.querySelector("#analysisComputeType"),
  analysisSubmit: document.querySelector("#analysisSubmit"),
  analysisError: document.querySelector("#analysisError"),
  analysisStatus: document.querySelector("#analysisStatus"),
  analysisSource: document.querySelector("#analysisSource"),
  analysisCandidateCount: document.querySelector("#analysisCandidateCount"),
  analysisSourceDuration: document.querySelector("#analysisSourceDuration"),
  analysisEngine: document.querySelector("#analysisEngine"),
  analysisCacheHit: document.querySelector("#analysisCacheHit"),
  analysisResultNotice: document.querySelector("#analysisResultNotice"),
  analysisCandidateRows: document.querySelector("#analysisCandidateRows"),
  tabs: [...document.querySelectorAll(".tab")],
  panels: [...document.querySelectorAll(".tab-panel")],
};

function safe(value, fallback = "--") {
  return value === null || value === undefined || value === "" ? fallback : value;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function decimal(value, digits = 2) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return "--";
  return new Intl.NumberFormat("fr-FR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(parsed);
}

function number(value) {
  return new Intl.NumberFormat("fr-FR").format(Number(value || 0));
}

function fmtDate(value) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("fr-FR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(date);
}

function badge(status) {
  return `<span class="pill ${status || "idle"}">${safe(status, "idle")}</span>`;
}

function latestLog(job) {
  const logs = job.logs || [];
  return logs.length ? logs[logs.length - 1] : "En attente...";
}

function renderTop(stats, automation) {
  const active = Boolean(automation.enabled);
  els.autoState.textContent = active ? "Active" : "Inactive";
  els.autoState.className = active ? "active" : "inactive";
  els.autoEnabled.checked = active;
  els.statDownloaded.textContent = number(stats.videos_downloaded);
  els.statPublished.textContent = number(stats.tiktok_published);
  els.statViews.textContent = number(stats.tiktok_views);
  els.lastRefresh.textContent = fmtDate(dashboard.generated_at);
}

function renderJob(job) {
  const status = job.status || "idle";
  const running = status === "running" || status === "stopping";
  const sourceLabel = job.source === "satisfying" ? "satisfying" : job.source === "analysis" ? "analyse" : "";
  els.jobStatus.textContent = sourceLabel ? `${status} / ${sourceLabel}` : status;
  els.jobStatus.className = `pill ${status}`;
  els.activeJob.classList.toggle("hidden", !running);
  els.activeJobTitle.textContent =
    job.source === "satisfying"
      ? "Ajout satisfying en cours"
      : job.source === "analysis"
        ? "Analyse video locale en cours"
        : "Telechargement en cours";
  els.activeJobLine.textContent = latestLog(job);
  els.logs.textContent = (job.logs || []).join("\n");
  els.logs.scrollTop = els.logs.scrollHeight;
}

function renderAnalysisVideoOptions(videos) {
  const current = els.analysisVideoSelect.value;
  const known = videos.filter((video) => video.downloaded_path);
  els.analysisVideoSelect.innerHTML = [
    '<option value="">Choisir une video connue</option>',
    ...known.map(
      (video) =>
        `<option value="${escapeHtml(video.downloaded_path)}">${escapeHtml(
          video.title || video.video_id || video.downloaded_path,
        )}</option>`,
    ),
  ].join("");
  if ([...els.analysisVideoSelect.options].some((option) => option.value === current)) {
    els.analysisVideoSelect.value = current;
  }
}

function renderAnalysisSummary(summary, job) {
  const running = job?.source === "analysis" && ["running", "stopping"].includes(job.status);
  els.analysisSubmit.disabled = running;
  els.analysisStatus.textContent = running
    ? "Analyse en cours"
    : summary.available
      ? `${number(summary.candidate_count)} candidats`
      : "Aucune analyse";
  els.analysisStatus.className = running ? "pill running" : summary.available ? "pill ok" : "pill";
  els.analysisSource.textContent = summary.source_video || "Aucune video analysee";
  els.analysisCandidateCount.textContent = number(summary.candidate_count || 0);
  els.analysisSourceDuration.textContent = summary.global_analysis?.duration_seconds
    ? `${decimal(summary.global_analysis.duration_seconds, 1)} s`
    : "--";
  els.analysisEngine.textContent = summary.global_analysis?.transcription_engine || "--";
  els.analysisCacheHit.textContent = summary.global_analysis?.cache_hit === true
    ? "Reutilise"
    : summary.global_analysis?.cache_hit === false
      ? "Nouveau"
      : "--";
}

function renderAnalysisCandidates(result) {
  const candidates = result.candidates || [];
  els.analysisResultNotice.textContent = result.pagination?.has_more
    ? `${number(candidates.length)} affiches sur ${number(result.pagination.total)} candidats.`
    : `${number(result.pagination?.total || candidates.length)} candidats affiches.`;
  if (!candidates.length) {
    els.analysisCandidateRows.innerHTML = '<tr><td colspan="7" class="empty-cell">Aucun candidat pour ces durees.</td></tr>';
    return;
  }
  els.analysisCandidateRows.innerHTML = candidates
    .map((candidate) => {
      const metrics = candidate.metrics || {};
      return `
        <tr>
          <td><strong>${escapeHtml(candidate.candidate_id)}</strong><span>${decimal(candidate.start_seconds, 1)} → ${decimal(candidate.end_seconds, 1)} s</span></td>
          <td>${decimal(Number(metrics.silence_ratio) * 100, 1)} %</td>
          <td>${decimal(metrics.longest_silence_seconds, 2)} s</td>
          <td>${decimal(Number(metrics.speech_density) * 100, 1)} %</td>
          <td>${decimal(metrics.words_per_minute, 1)}</td>
          <td>${decimal(Number(metrics.hesitation_ratio) * 100, 1)} %</td>
          <td>${decimal(metrics.startup_latency_seconds, 2)} s</td>
        </tr>`;
    })
    .join("");
}

async function refreshAnalysisResult(summary) {
  if (!summary.available) return;
  const key = `${summary.source_video}|${summary.updated_at}|${summary.candidate_count}`;
  if (key === loadedAnalysisKey) return;
  const response = await fetch("/api/analysis/latest?offset=0&limit=100");
  if (!response.ok) return;
  const result = await response.json();
  if (result.ok) {
    loadedAnalysisKey = key;
    renderAnalysisCandidates(result);
  }
}

function renderDownloads(videos) {
  if (!videos.length) {
    els.downloadList.innerHTML = `<article class="empty">Aucune video pour le moment.</article>`;
    return;
  }
  els.downloadList.innerHTML = videos
    .slice(0, 50)
    .map(
      (video) => `
        <article class="row-card">
          <div>
            <strong>${safe(video.title, video.video_id)}</strong>
            <p>${safe(video.channel)} / ${safe(video.niche)} / ${fmtDate(video.downloaded_at || video.last_seen_at)}</p>
            <p>${video.clips_count || 0} clips / ${video.shorts_count || 0} shorts / TikTok ${safe(
              video.tiktok_publish_status,
              "non publie",
            )}</p>
            <p>${pipelineText(video)}</p>
            ${video.tiktok_publish_error ? `<p class="error-line">${safe(video.tiktok_publish_error)}</p>` : ""}
          </div>
          ${badge(video.status)}
        </article>
      `,
    )
    .join("");
}

function pipelineText(video) {
  const stage = video.pipeline_stage || video.render_status || video.clip_status || video.status || "idle";
  const current = video.pipeline_current_part;
  const total = video.pipeline_total_parts;
  const progress = current && total ? ` ${current}/${total}` : "";
  return `${safe(stage)}${progress} - ${safe(video.pipeline_message, "en attente")}`;
}

function renderAnalytics(stats, videos) {
  const published = Number(stats.tiktok_published || 0);
  const views = Number(stats.tiktok_views || 0);
  const tiktok = dashboard.tiktok || {};
  els.tokenState.textContent = tiktok.connected
    ? "TikTok connecte"
    : tiktok.configured
      ? "Pret a connecter"
      : "Cles manquantes";
  els.tokenState.className = tiktok.connected ? "pill ok" : "pill warning";
  els.tiktokConnectionTitle.textContent = tiktok.connected
    ? "Compte TikTok connecte"
    : "Compte TikTok non connecte";
  els.tiktokConnectionDetails.textContent = tiktok.connected
    ? `Scopes: ${safe(tiktok.scope || tiktok.scopes)}`
    : tiktok.configured
      ? `Redirect: ${safe(tiktok.redirect_uri)}`
      : "Renseigne TIKTOK_CLIENT_KEY et TIKTOK_CLIENT_SECRET dans .env.";
  els.connectTikTokBtn.classList.toggle("disabled", !tiktok.configured);
  els.connectTikTokBtn.setAttribute("aria-disabled", String(!tiktok.configured));
  els.disconnectTikTokBtn.disabled = !tiktok.connected;
  els.anaPublished.textContent = number(published);
  els.anaViews.textContent = number(views);
  els.anaShorts.textContent = number(stats.shorts_generated);
  els.anaAverage.textContent = number(published ? Math.round(views / published) : 0);

  const publishedVideos = videos.filter((video) => Number(video.tiktok_publish_count || 0) > 0);
  els.publishedList.innerHTML = publishedVideos.length
    ? publishedVideos
        .slice(0, 30)
        .map(
          (video) => `
            <article class="row-card">
              <div>
                <strong>${safe(video.title, video.video_id)}</strong>
                <p>${safe(video.channel)} / publie le ${fmtDate(video.tiktok_published_at)}</p>
              </div>
              ${badge(video.tiktok_publish_status)}
            </article>
          `,
        )
        .join("")
    : `<article class="empty">Aucune publication TikTok enregistree.</article>`;
}

function renderNicheSelect(niches) {
  const current = els.manualNiche.value || ACCOUNT_NICHE;
  const knownNiches = (niches || [])
    .map((item) => item.niche)
    .filter(Boolean)
    .sort((a, b) => a.localeCompare(b, "fr"));
  const options = [ACCOUNT_NICHE, ...knownNiches.filter((niche) => niche !== ACCOUNT_NICHE)];
  els.manualNiche.innerHTML = options
    .map((niche) => `<option value="${safe(niche)}">${safe(niche)}</option>`)
    .join("");
  els.manualNiche.value = options.includes(current) ? current : ACCOUNT_NICHE;
}

function render(data) {
  dashboard = data;
  renderTop(data.stats || {}, data.automation || {});
  renderJob(data.job || {});
  renderDownloads(data.videos || []);
  renderAnalytics(data.stats || {}, data.videos || []);
  renderNicheSelect(data.niches || []);
  renderAnalysisVideoOptions(data.videos || []);
  renderAnalysisSummary(data.candidate_analysis || {}, data.job || {});
  els.satisfyingCount.textContent = `${number(data.stats?.satisfying_videos)} videos`;
}

async function refresh() {
  const response = await fetch("/api/dashboard");
  const data = await response.json();
  render(data);
  await refreshAnalysisResult(data.candidate_analysis || {});
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  if (!result.ok) {
    throw new Error(result.error || "Action impossible.");
  }
  await refresh();
}

async function saveAutomation() {
  await postJson("/api/automation", {
    ...DEFAULT_RUN_OPTIONS,
    dryRun: true,
    enabled: els.autoEnabled.checked,
    intervalMinutes: 5,
  });
}

async function submitYoutube(event) {
  event.preventDefault();
  const delayMin = Math.max(0, Number(els.delayMinMinutes.value || 10));
  const delayMax = Math.max(delayMin, Number(els.delayMaxMinutes.value || 20));
  await postJson("/api/jobs", {
    ...DEFAULT_RUN_OPTIONS,
    autoPublishTikTok: true,
    tiktokPublishLimit: els.testOneShort.checked ? 1 : 100,
    tiktokPublishDelayMinSeconds: Math.round(delayMin * 60),
    tiktokPublishDelayMaxSeconds: Math.round(delayMax * 60),
    videoUrl: els.youtubeUrl.value.trim(),
    manualNiche: els.manualNiche.value.trim() || "Manuel",
    manualChannel: els.manualChannel.value.trim() || "Lien manuel",
  });
  els.youtubeForm.reset();
  els.manualNiche.value = ACCOUNT_NICHE;
  els.manualChannel.value = "Lien manuel";
  switchTab("downloads");
}

async function submitSatisfying(event) {
  event.preventDefault();
  await postJson("/api/satisfying-jobs", {
    videoUrl: els.satisfyingUrl.value.trim(),
  });
  els.satisfyingForm.reset();
  switchTab("downloads");
}

async function submitAnalysis(event) {
  event.preventDefault();
  const videoPath = els.analysisVideoPath.value.trim() || els.analysisVideoSelect.value;
  els.analysisError.classList.add("hidden");
  try {
    await postJson("/api/analysis-jobs", {
      videoPath,
      durations: els.analysisDurations.value.trim(),
      step: Number(els.analysisStep.value || 3),
      silenceThresholdDb: Number(els.analysisSilenceThreshold.value || -35),
      model: els.analysisModel.value,
      language: "fr",
      device: els.analysisDevice.value,
      computeType: els.analysisComputeType.value,
    });
    loadedAnalysisKey = null;
  } catch (error) {
    els.analysisError.textContent = error.message || "Analyse impossible.";
    els.analysisError.classList.remove("hidden");
  }
}

async function stopJob() {
  try {
    await postJson("/api/jobs/stop", {});
  } catch (error) {
    await refresh();
  }
}

async function disconnectTikTok() {
  await postJson("/api/tiktok/disconnect", {});
}

async function openFolder(folder) {
  await postJson("/api/folders/open", { folder });
}

async function cleanupFailed() {
  await postJson("/api/cleanup-failed", {});
}

function switchTab(tabId) {
  els.tabs.forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === tabId));
  els.panels.forEach((panel) => panel.classList.toggle("active", panel.id === tabId));
}

els.tabs.forEach((tab) => tab.addEventListener("click", () => switchTab(tab.dataset.tab)));
els.refreshBtn.addEventListener("click", refresh);
els.stopBtn.addEventListener("click", stopJob);
els.autoEnabled.addEventListener("change", saveAutomation);
els.youtubeForm.addEventListener("submit", submitYoutube);
els.satisfyingForm.addEventListener("submit", submitSatisfying);
els.analysisForm.addEventListener("submit", submitAnalysis);
els.analysisVideoSelect.addEventListener("change", () => {
  if (els.analysisVideoSelect.value) els.analysisVideoPath.value = "";
});
els.disconnectTikTokBtn.addEventListener("click", disconnectTikTok);
els.cleanupFailedBtn.addEventListener("click", cleanupFailed);
els.folderButtons.forEach((button) => {
  button.addEventListener("click", () => openFolder(button.dataset.folder));
});
els.connectTikTokBtn.addEventListener("click", (event) => {
  if (els.connectTikTokBtn.getAttribute("aria-disabled") === "true") {
    event.preventDefault();
  }
});

refresh();
setInterval(refresh, 3000);
