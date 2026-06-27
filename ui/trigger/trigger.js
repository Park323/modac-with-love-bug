(function () {
const { bridge, formatTime, qs, setText } = window.Lovebug;

function initTriggerPage() {
  const form = qs("[data-trigger-form]");
  if (!form) return;

  const preset = qs("#preset");
  const repeat = qs("#repeat-count");
  const startButton = qs("[data-start-test]");
  const progress = qs("[data-progress]");
  const activityList = qs("[data-activity-list]");
  let isRunning = false;

  const startRun = async () => {
    if (isRunning) return;
    isRunning = true;
    const payload = {
      project: "lovebug",
      preset: preset.value,
      repeatCount: Number(repeat.value),
      requestedAt: new Date().toISOString()
    };

    startButton.disabled = true;
    startButton.textContent = "테스트 요청 중";
    setText("[data-status-text]", "테스트 실행 준비");
    setText("[data-last-run]", formatTime());
    progress.style.setProperty("--progress", "18%");

    if (bridge.startTestRun) {
      await bridge.startTestRun(payload);
    } else {
      console.info("Lovebug trigger payload", payload);
    }

    window.setTimeout(() => {
      progress.style.setProperty("--progress", "64%");
      setText("[data-status-text]", "테스트 실행 중");
    }, 450);

    window.setTimeout(() => {
      const item = document.createElement("article");
      item.className = "activity-item";
      item.innerHTML = `
        <div class="activity-item__top">
          <strong>${preset.options[preset.selectedIndex].text}</strong>
          <span class="tag">queued</span>
        </div>
        <span>${repeat.value}회 반복 요청 · ${formatTime()}</span>
      `;
      activityList.prepend(item);
      progress.style.setProperty("--progress", "100%");
      setText("[data-status-text]", "테스트 요청 완료");
      startButton.disabled = false;
      startButton.textContent = "테스트 시작";
      isRunning = false;
    }, 950);
  };

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    startRun();
  });

  startButton.addEventListener("click", (event) => {
    event.preventDefault();
    startRun();
  });
}

initTriggerPage();
})();
