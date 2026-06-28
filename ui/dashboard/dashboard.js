(function () {
const { formatTime, qs, setText } = window.Lovebug;

const QA_RESULTS = ["FAIL", "UNCERTAIN", "NEED_REVIEW", "PASS"];
const REVIEW_FILTERS = ["ALL", "FAIL", "UNCERTAIN", "NEED_REVIEW"];
const DEFAULT_PAGE_SIZE = 10;

function esc(value) {
  return String(value ?? "-").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;"
  })[char]);
}

function fmtNumber(value, digits = 3) {
  return typeof value === "number" ? value.toFixed(digits) : "-";
}

function resultClass(result) {
  return QA_RESULTS.includes(result) ? result.toLowerCase().replace("_", "-") : "unknown";
}

function getChecks(report) {
  return Array.isArray(report.qa_checks) ? report.qa_checks : [];
}

function getAnalyzedFileCount(report) {
  return Array.isArray(report.input_videos) ? report.input_videos.length : "-";
}

function getIssueCount(report) {
  const counts = report.summary?.result_counts;
  if (!counts || typeof counts !== "object") return 0;
  return REVIEW_FILTERS
    .filter((result) => result !== "ALL")
    .reduce((total, result) => total + Number(counts[result] || 0), 0);
}

function getPackagedStageReports(report) {
  if (!Array.isArray(report.packaged_stage_reports)) return [];
  return report.packaged_stage_reports
    .map((stageGroup) => {
      const reports = stageGroup && typeof stageGroup.reports === "object" && !Array.isArray(stageGroup.reports)
        ? Object.entries(stageGroup.reports)
            .filter(([, path]) => typeof path === "string" && path.trim())
            .map(([name, path]) => ({ name, path }))
        : [];
      return {
        videoId: stageGroup?.video_id || stageGroup?.output_dir || "stage reports",
        outputDir: stageGroup?.output_dir || "",
        reports
      };
    })
    .filter((stageGroup) => stageGroup.reports.length);
}

function getCheckConditions(check) {
  return Array.isArray(check.decision_trace?.conditions) ? check.decision_trace.conditions : [];
}

function getCheckArtifacts(check) {
  const evidenceItems = Array.isArray(check.evidence) ? check.evidence : [];
  const linkedEvidenceItems = Array.isArray(check.trace_links?.evidence_items)
    ? check.trace_links.evidence_items
    : [];
  const moduleReports = check.trace_links?.module_reports && typeof check.trace_links.module_reports === "object"
    ? Object.entries(check.trace_links.module_reports).map(([name, path]) => ({
        kind: name,
        type: "module_report",
        path
    }))
    : [];

  return evidenceItems.concat(linkedEvidenceItems, moduleReports);
}

function formatCheckTime(check) {
  if (Array.isArray(check.time_range_sec)) {
    const [start, end] = check.time_range_sec;
    if (typeof start === "number" && typeof end === "number" && start !== end) {
      return `${fmtNumber(start)}s - ${fmtNumber(end)}s`;
    }
    if (typeof start === "number") return `${fmtNumber(start)}s`;
  }
  return "-";
}

function getCheckReason(check) {
  return check.reason || check.decision_trace?.final_decision_reason || "-";
}

function formatReportLabel(label) {
  return String(label || "report")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function resetResultMetrics() {
  setText("[data-metric-report]", "-");
  setText("[data-metric-files]", "-");
  setText("[data-metric-issues]", "-");
  const reportMetric = qs("[data-metric-report]");
  if (reportMetric) {
    reportMetric.classList.remove("metric__value--pass", "metric__value--fail");
  }
}

function renderResultMetrics(report) {
  setText("[data-metric-report]", report.overall_result || "READY");
  setText("[data-metric-files]", getAnalyzedFileCount(report));
  setText("[data-metric-issues]", getIssueCount(report));
  const reportMetric = qs("[data-metric-report]");
  if (reportMetric) {
    reportMetric.classList.remove("metric__value--pass", "metric__value--fail");
    reportMetric.classList.add(report.overall_result === "PASS" ? "metric__value--pass" : "metric__value--fail");
  }
}

function getResultCounts(report) {
  const counts = Object.fromEntries(QA_RESULTS.map((result) => [result, 0]));

  QA_RESULTS.forEach((result) => {
    counts[result] = Number(report.summary?.result_counts?.[result] || 0);
  });
  return counts;
}

function renderSummary(report) {
  const summary = report.summary || {};
  const counts = getResultCounts(report);
  const totalChecks = summary.total_checks ?? 0;
  const items = [
    ["PASS", `${counts.PASS} / ${totalChecks}`],
    ["FAIL", `${counts.FAIL} / ${totalChecks}`],
    ["UNCERTAIN", `${counts.UNCERTAIN} / ${totalChecks}`],
    ["NEED_REVIEW", `${counts.NEED_REVIEW} / ${totalChecks}`]
  ];

  return `
    <section class="final-report__section">
      <div class="section-heading">
        <h4>요약</h4>
        <span>${esc(report.game || "QA report")} · schema ${esc(report.schema_version || "-")}</span>
      </div>
      <div class="summary-grid">
        ${items.map(([label, value]) => `
          <div class="summary-card">
            <span>${esc(label)}</span>
            <strong>${esc(value)}</strong>
          </div>
        `).join("")}
      </div>
    </section>
  `;
}

function renderPackagedStageReports(report) {
  const stageReports = getPackagedStageReports(report);
  const reportCount = stageReports.reduce((total, stageGroup) => total + stageGroup.reports.length, 0);
  if (!stageReports.length) return "";

  return `
    <section class="final-report__section">
      <div class="section-heading">
        <h4>단계별 리포트</h4>
        <span>${esc(stageReports.length)} videos · ${esc(reportCount)} files</span>
      </div>
      <div class="stage-report-list">
        ${stageReports.map((stageGroup) => `
          <article class="stage-report-card">
            <div class="stage-report-card__top">
              <strong>${esc(stageGroup.videoId)}</strong>
              ${stageGroup.outputDir ? `<span>${esc(stageGroup.outputDir)}</span>` : ""}
            </div>
            <div class="stage-report-actions">
              ${stageGroup.reports.map((stageReport) => `
                <button
                  class="stage-report-button"
                  type="button"
                  title="${esc(stageReport.path)}"
                  data-stage-report-path="${esc(stageReport.path)}"
                >
                  <span>${esc(formatReportLabel(stageReport.name))}</span>
                  <small>JSON</small>
                </button>
              `).join("")}
            </div>
          </article>
        `).join("")}
      </div>
    </section>
  `;
}

function renderQaReport(report, state) {
  const checks = getChecks(report);
  const filteredChecks = state.filter === "ALL"
    ? checks
    : checks.filter((check) => check.result === state.filter);
  const pageCount = Math.max(1, Math.ceil(filteredChecks.length / state.pageSize));
  state.page = Math.min(Math.max(1, state.page), pageCount);

  const startIndex = (state.page - 1) * state.pageSize;
  const pageItems = filteredChecks.slice(startIndex, startIndex + state.pageSize);

  const filterButtons = REVIEW_FILTERS.map((filter) => `
    <button
      class="filter-chip${state.filter === filter ? " is-active" : ""}"
      type="button"
      data-report-filter="${esc(filter)}"
    >${esc(filter)}</button>
  `).join("");

  return `
    ${renderSummary(report)}
    <section class="final-report__section">
      <div class="section-heading">
        <h4>검토 필요 이벤트</h4>
        <span>${esc(filteredChecks.length)} of ${esc(checks.length)} items</span>
      </div>
      <div class="report-toolbar">
        <div class="filter-group" aria-label="QA result filter">
          ${filterButtons}
        </div>
        <label class="page-size">
          <span>Rows</span>
          <select class="control" data-report-page-size>
            ${[5, 10, 20, 50].map((size) => `
              <option value="${size}"${state.pageSize === size ? " selected" : ""}>${size}</option>
            `).join("")}
          </select>
        </label>
      </div>
      <div class="qa-check-list">
        ${pageItems.length ? pageItems.map((check, index) => {
          const absoluteIndex = startIndex + index + 1;
          const canExpand = true;
          return `
            <article class="qa-check-card${canExpand ? " qa-check-card--actionable" : ""}">
              <div class="qa-check-card__main">
                <div class="qa-check-card__index">#${absoluteIndex}</div>
                <div class="qa-check-card__body">
                  <div class="qa-check-card__top">
                    <strong>${esc(check.check_id)} · ${esc(check.check_type)}</strong>
                    <span class="result-badge result-badge--${resultClass(check.result)}">${esc(check.result)}</span>
                  </div>
                  <div class="qa-check-card__meta">
                    <span>이벤트 시각: ${esc(formatCheckTime(check))}</span>
                    <span>파일: ${esc(check.video_id || "-")}</span>
                  </div>
                  <p>메세지: "${esc(getCheckReason(check))}"</p>
                </div>
                ${canExpand ? `
                  <button class="secondary-button qa-detail-button" type="button" data-check-detail="${esc(check.check_id)}">
                    상세
                  </button>
                ` : ""}
              </div>
              ${state.openCheckId === check.check_id ? renderCheckDetail(check) : ""}
            </article>
          `;
        }).join("") : `
          <div class="empty-state empty-state--compact">
            <div>
              <strong>표시할 QA 항목이 없습니다.</strong>
              <span>다른 result 필터를 선택하세요.</span>
            </div>
          </div>
        `}
      </div>
      <div class="pagination-bar">
        <button class="secondary-button" type="button" data-page-prev${state.page <= 1 ? " disabled" : ""}>이전</button>
        <span>Page ${state.page} / ${pageCount}</span>
        <button class="secondary-button" type="button" data-page-next${state.page >= pageCount ? " disabled" : ""}>다음</button>
      </div>
    </section>
    ${renderPackagedStageReports(report)}
  `;
}

function renderCheckDetail(check) {
  const conditions = getCheckConditions(check);
  const artifacts = getCheckArtifacts(check);

  return `
    <div class="qa-detail">
      <div>
        <h5>Conditions</h5>
        ${conditions.length ? `
          <div class="condition-list">
            ${conditions.map((condition) => `
              <div class="condition-item">
                <div class="condition-item__top">
                  <strong>${esc(condition.condition || condition.condition_id || "-")}</strong>
                  <span>${esc(condition.result || "-")}</span>
                </div>
                <dl>
                  <div><dt>expected</dt><dd>${esc(condition.expected)}</dd></div>
                  <div class="${condition.expected !== condition.observed ? "condition-item__mismatch" : ""}"><dt>observed</dt><dd>${esc(condition.observed)}</dd></div>
                  <div><dt>confidence</dt><dd>${esc(fmtNumber(condition.confidence))}</dd></div>
                </dl>
              </div>
            `).join("")}
          </div>
        ` : `<p>표시할 condition이 없습니다.</p>`}
      </div>
      <div>
        <h5>Artifacts</h5>
        ${artifacts.length ? `
          <div class="artifact-list">
            ${artifacts.map((artifact) => `
              <div class="artifact-item">
                <div>
                  <strong>${esc(artifact.kind || artifact.type || "artifact")}</strong>
                  <span>${esc(artifact.type || "-")}</span>
                  <p>${esc(artifact.path || "-")}</p>
                </div>
                ${artifact.path ? `
                  <button class="secondary-button artifact-link" type="button" data-artifact-path="${esc(artifact.path)}">
                    열기
                  </button>
                ` : ""}
              </div>
            `).join("")}
          </div>
        ` : `<p>표시할 artifact가 없습니다.</p>`}
      </div>
    </div>
  `;
}

function mountFinalReport(reportArea, report, resultDir) {
  const mountId = (reportArea.__finalReportMountId || 0) + 1;
  reportArea.__finalReportMountId = mountId;
  const state = {
    filter: "ALL",
    page: 1,
    pageSize: DEFAULT_PAGE_SIZE,
    openCheckId: null
  };

  const render = () => {
    reportArea.innerHTML = `<div class="final-report">${renderQaReport(report, state)}</div>`;
  };

  reportArea.addEventListener("click", (event) => {
    if (reportArea.__finalReportMountId !== mountId) return;
    const filter = event.target.closest("[data-report-filter]");
    if (filter) {
      state.filter = filter.dataset.reportFilter;
      state.page = 1;
      state.openCheckId = null;
      render();
      return;
    }

    if (event.target.closest("[data-page-prev]")) {
      state.page -= 1;
      state.openCheckId = null;
      render();
      return;
    }

    if (event.target.closest("[data-page-next]")) {
      state.page += 1;
      state.openCheckId = null;
      render();
      return;
    }

    const detailButton = event.target.closest("[data-check-detail]");
    if (detailButton) {
      const nextId = detailButton.dataset.checkDetail;
      state.openCheckId = state.openCheckId === nextId ? null : nextId;
      render();
      return;
    }

    const artifactButton = event.target.closest("[data-artifact-path]");
    if (artifactButton) {
      const params = new URLSearchParams({
        result_dir: resultDir,
        path: artifactButton.dataset.artifactPath
      });
      window.open(`/dashboard/artifact?${params.toString()}`, "_blank");
      return;
    }

    const stageReportButton = event.target.closest("[data-stage-report-path]");
    if (stageReportButton) {
      const params = new URLSearchParams({
        result_dir: resultDir,
        path: stageReportButton.dataset.stageReportPath
      });
      window.open(`/dashboard/artifact?${params.toString()}`, "_blank");
    }
  });

  reportArea.addEventListener("change", (event) => {
    if (reportArea.__finalReportMountId !== mountId) return;
    const pageSize = event.target.closest("[data-report-page-size]");
    if (!pageSize) return;
    state.pageSize = Number(pageSize.value) || DEFAULT_PAGE_SIZE;
    state.page = 1;
    state.openCheckId = null;
    render();
  });

  render();
}

function initDashboardPage() {
  const form = qs("[data-analysis-form]");
  if (!form) return;

  const directory       = qs("#video-directory");
  const folderPickerBtn = qs("[data-folder-picker]");
  const progress        = qs("[data-progress]");
  const reportArea      = qs("[data-report-area]");
  const reportPanel     = qs("[data-report-panel]");
  const analyzeButton   = qs("[data-start-analysis]");
  let isAnalyzing = false;

  const startAnalysis = async () => {
    if (isAnalyzing || !form.reportValidity()) return;
    isAnalyzing = true;
    const payload = {
      project: "lovebug",
      videoDirectory: directory.value.trim(),
      requestedAt: new Date().toISOString()
    };

    analyzeButton.disabled = true;
    analyzeButton.textContent = "분석 중";
    setText("[data-status-text]", "영상 분석 시작");
    progress.style.setProperty("--progress", "30%");
    if (reportPanel) reportPanel.hidden = true;
    resetResultMetrics();

    try {
      progress.style.setProperty("--progress", "60%");
      setText("[data-status-text]", "Python 분석 중");
      const analyzeRes = await fetch("/dashboard/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      if (!analyzeRes.ok) throw new Error(`분석 요청 실패 (${analyzeRes.status})`);
      const result = await analyzeRes.json();
      const resultDir = result.resultDir || payload.videoDirectory;

      progress.style.setProperty("--progress", "82%");
      setText("[data-status-text]", "리포트 생성 중");
      if (reportPanel) reportPanel.hidden = false;

      reportArea.innerHTML = "";
      const el = document.createElement("div");
      el.className = "report-item";
      el.innerHTML = `
        <div class="report-item__top">
          <strong>분석 완료</strong>
          <span class="tag">done</span>
        </div>
        <p class="result-dir" title="${esc(resultDir || "")}">${esc(resultDir || "경로 없음")}</p>
        <span>생성 ${formatTime()}</span>
      `;
      reportArea.appendChild(el);
      const reportDetail = document.createElement("div");
      reportArea.appendChild(reportDetail);

      const reportRes = await fetch("/dashboard/final-report", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resultDir })
      });
      if (!reportRes.ok) {
        let detail = "";
        try {
          const errorBody = await reportRes.json();
          detail = errorBody.detail ? ` ${errorBody.detail}` : "";
        } catch (parseErr) {
          detail = "";
        }
        throw new Error(`final_report.json을 읽지 못했습니다. (${reportRes.status})${detail}`);
      }
      const report = await reportRes.json();
      renderResultMetrics(report);
      mountFinalReport(reportDetail, report, resultDir);

      progress.style.setProperty("--progress", "100%");
      setText("[data-status-text]", "분석 완료");
    } catch (err) {
      if (reportPanel) reportPanel.hidden = false;
      setText("[data-status-text]", "분석 오류");
      setText("[data-metric-report]", "오류");
      reportArea.innerHTML = `
        <div class="empty-state">
          <div>
            <strong>final_report.json을 읽지 못했습니다.</strong>
            <span>${esc(err.message || err)}</span>
          </div>
        </div>
      `;
    } finally {
      analyzeButton.disabled = false;
      analyzeButton.textContent = "분석 시작";
      isAnalyzing = false;
    }
  };

  if (folderPickerBtn) {
    folderPickerBtn.addEventListener("click", async () => {
      try {
        const res = await fetch("/dashboard/browse", { method: "POST" });
        const data = await res.json();
        if (data.path) directory.value = data.path;
      } catch (err) {
        console.warn("폴더 선택 실패", err);
      }
    });
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    startAnalysis();
  });

  analyzeButton.addEventListener("click", (event) => {
    event.preventDefault();
    startAnalysis();
  });
}

initDashboardPage();
})();
