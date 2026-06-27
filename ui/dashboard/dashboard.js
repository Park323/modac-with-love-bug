(function () {
const { bridge, formatTime, qs, setText } = window.Lovebug;

const QA_RESULTS = ["FAIL", "UNCERTAIN", "NEED_REVIEW", "PASS"];
const QA_FILTERS = ["ALL", ...QA_RESULTS];
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

function fmtTimeRange(range) {
  if (!Array.isArray(range) || range.length === 0) return "-";
  return range.map((value) => fmtNumber(value)).join(" - ");
}

function resultClass(result) {
  return QA_RESULTS.includes(result) ? result.toLowerCase().replace("_", "-") : "unknown";
}

function getResultCounts(report) {
  const checks = Array.isArray(report.qa_checks) ? report.qa_checks : [];
  const counts = Object.fromEntries(QA_RESULTS.map((result) => [result, 0]));

  if (report.summary?.result_counts) {
    QA_RESULTS.forEach((result) => {
      counts[result] = Number(report.summary.result_counts[result] || 0);
    });
    return counts;
  }

  checks.forEach((check) => {
    if (counts[check.result] !== undefined) counts[check.result] += 1;
  });
  return counts;
}

function renderSummary(report) {
  const summary = report.summary || {};
  const counts = getResultCounts(report);
  const items = [
    ["Overall", report.overall_result || "-"],
    ["Events", summary.total_events ?? (report.events || []).length],
    ["QA Checks", summary.total_checks ?? (report.qa_checks || []).length],
    ["PASS", counts.PASS],
    ["FAIL", counts.FAIL],
    ["UNCERTAIN", counts.UNCERTAIN],
    ["NEED_REVIEW", counts.NEED_REVIEW]
  ];

  return `
    <section class="final-report__section">
      <div class="section-heading">
        <h4>Summary</h4>
        <span>${esc(report.game || "Unknown game")} · ${esc(report.generated_at || "-")}</span>
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

function renderQaReport(report, state) {
  const checks = Array.isArray(report.qa_checks) ? report.qa_checks : [];
  const events = Array.isArray(report.events) ? report.events : [];
  const filteredChecks = state.filter === "ALL"
    ? checks
    : checks.filter((check) => check.result === state.filter);
  const pageCount = Math.max(1, Math.ceil(filteredChecks.length / state.pageSize));
  state.page = Math.min(Math.max(1, state.page), pageCount);

  const startIndex = (state.page - 1) * state.pageSize;
  const pageItems = filteredChecks.slice(startIndex, startIndex + state.pageSize);

  const filterButtons = QA_FILTERS.map((filter) => `
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
        <h4>QA Checks</h4>
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
                    <span>${esc(check.video_id)}</span>
                    <span>${esc(check.severity || "-")}</span>
                    <span>confidence ${fmtNumber(check.confidence)}</span>
                  </div>
                  <p>${esc(check.reason || check.rule?.description || "-")}</p>
                  ${(check.notes || []).length ? `
                    <div class="note-list">
                      ${check.notes.map((note) => `<span>${esc(note)}</span>`).join("")}
                    </div>
                  ` : ""}
                </div>
                ${canExpand ? `
                  <button class="secondary-button qa-detail-button" type="button" data-check-detail="${esc(check.check_id)}">
                    상세
                  </button>
                ` : ""}
              </div>
              ${state.openCheckId === check.check_id ? renderCheckDetail(check, events) : ""}
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
  `;
}

function renderCheckDetail(check, events) {
  const relatedEvents = events.filter((event) => event.video_id === check.video_id);

  return `
    <div class="qa-detail">
      <div>
        <h5>PASS가 아닌 근거</h5>
        <p>${esc(check.reason || "-")}</p>
      </div>
      <div>
        <h5>동일 video_id 이벤트 (${esc(check.video_id)})</h5>
        <div class="event-table-wrap">
          <table class="event-table">
            <thead>
              <tr>
                <th>event_id</th>
                <th>time_range_sec</th>
                <th>confidence</th>
              </tr>
            </thead>
            <tbody>
              ${relatedEvents.length ? relatedEvents.map((event) => `
                <tr>
                  <td>${esc(event.event_id)}</td>
                  <td>${esc(fmtTimeRange(event.time_range_sec))}</td>
                  <td>${esc(fmtNumber(event.confidence))}</td>
                </tr>
              `).join("") : `
                <tr>
                  <td colspan="3">동일한 video_id의 이벤트가 없습니다.</td>
                </tr>
              `}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  `;
}

function mountFinalReport(reportArea, report) {
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

    if (bridge.onAnalysisComplete) {
      bridge.onAnalysisComplete((result) => {
        progress.style.setProperty("--progress", "100%");
        setText("[data-status-text]", "분석 완료");
        analyzeButton.disabled = false;
        analyzeButton.textContent = "분석 시작";
        isAnalyzing = false;

        const el = document.createElement("div");
        el.className = "report-item";
        el.innerHTML = `
          <div class="report-item__top">
            <strong>분석 완료</strong>
            <span class="tag">done</span>
          </div>
          <p class="result-dir" title="${result.resultDir || ""}">${result.resultDir || "경로 없음"}</p>
          <div class="report-actions">
            <button class="secondary-button" type="button" data-open-result>결과 폴더 열기</button>
          </div>
        `;
        if (bridge.openAnalysisResultFolder && result.resultDir) {
          el.querySelector("[data-open-result]").addEventListener("click", () => {
            bridge.openAnalysisResultFolder(result.resultDir);
          });
        }
        reportArea.innerHTML = "";
        reportArea.appendChild(el);
        const reportDetail = document.createElement("div");
        reportArea.appendChild(reportDetail);

        if (bridge.readFinalReport && result.resultDir) {
          bridge.readFinalReport(result.resultDir).then((report) => {
            mountFinalReport(reportDetail, report);
          }).catch((err) => {
            reportDetail.innerHTML = `
              <div class="empty-state">
                <div>
                  <strong>final_report.json을 읽지 못했습니다.</strong>
                  <span>${esc(err.message || err)}</span>
                </div>
              </div>
            `;
          });
        }
      });
    }

    if (bridge.analyzeVideos) {
      progress.style.setProperty("--progress", "60%");
      setText("[data-status-text]", "Python 분석 중");
      await bridge.analyzeVideos(payload);
    } else {
      console.info("Lovebug analysis payload", payload);
    }
  };

  if (folderPickerBtn) {
    if (!bridge.selectRawDataFolder) {
      folderPickerBtn.disabled = true;
      folderPickerBtn.title = "브릿지 미연결 — 경로를 직접 입력하세요";
    } else {
      folderPickerBtn.addEventListener("click", async () => {
        try {
          const path = await bridge.selectRawDataFolder();
          if (path) directory.value = path;
        } catch (err) {
          console.warn("폴더 선택 실패", err);
        }
      });
    }
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
