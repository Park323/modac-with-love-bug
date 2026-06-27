(function () {
const { bridge, formatTime, qs, setText } = window.Lovebug;

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

        if (bridge.readFinalReport && result.resultDir) {
          bridge.readFinalReport(result.resultDir).then((report) => {
            // TODO: render report card from report data
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
