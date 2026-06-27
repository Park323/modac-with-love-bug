(function () {
const { bridge, formatTime, qs, setText } = window.Lovebug;

function initDashboardPage() {
  const form = qs("[data-analysis-form]");
  if (!form) return;

  const directory = qs("#video-directory");
  const progress = qs("[data-progress]");
  const reportArea = qs("[data-report-area]");
  const analyzeButton = qs("[data-start-analysis]");
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

    if (bridge.analyzeVideos) {
      await bridge.analyzeVideos(payload);
    } else {
      console.info("Lovebug analysis payload", payload);
    }

    window.setTimeout(() => {
      progress.style.setProperty("--progress", "72%");
      setText("[data-status-text]", "리포트 생성 중");
    }, 500);

    window.setTimeout(() => {
      progress.style.setProperty("--progress", "100%");
      setText("[data-status-text]", "분석 완료");
      reportArea.innerHTML = `
        <div class="report-list">
          <article class="report-item">
            <div class="report-item__top">
              <strong>lovebug 분석 리포트</strong>
              <span class="tag">ready</span>
            </div>
            <span>소스: ${payload.videoDirectory || "미지정"} · 생성 ${formatTime()}</span>
            <div class="report-actions">
              <button class="secondary-button" type="button">리포트 보기</button>
              <button class="secondary-button" type="button">JSON 내보내기</button>
            </div>
          </article>
        </div>
      `;
      analyzeButton.disabled = false;
      analyzeButton.textContent = "분석 시작";
      isAnalyzing = false;
    }, 1100);
  };

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
