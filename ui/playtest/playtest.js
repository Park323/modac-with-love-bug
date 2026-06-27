(function () {
  const { qs, setText, initStepper, formatTime } = window.Lovebug;
  const api = window.ManagerApi;

  initStepper();

  const pathInput = qs("[data-scenario-path]");
  const repeatInput = qs("#repeat-count");
  const browseBtn = qs("[data-browse]");
  const startBtn = qs("[data-start]");
  const stopBtn = qs("[data-stop]");
  const progress = qs("[data-progress]");
  const activity = qs("[data-activity-list]");
  const form = qs("[data-playtest-form]");

  let polling = null;

  function log(text, tag) {
    const item = document.createElement("article");
    item.className = "activity-item";
    item.innerHTML = `
      <div class="activity-item__top">
        <strong>${text}</strong>
        <span class="tag">${tag || ""}</span>
      </div>
      <span>${formatTime()}</span>`;
    activity.prepend(item);
  }

  function render(st) {
    setText("[data-metric-repeat]", `${st.repeat_index} / ${st.repeat}`);
    setText("[data-metric-item]", `${st.item_index} / ${st.total}`);
    setText("[data-metric-state]", st.state);
    setText("[data-status-text]", st.state);
    const pct = st.total > 0 ? Math.round((st.item_index / st.total) * 100) : 0;
    progress.style.setProperty("--progress", pct + "%");
    return st;
  }

  function resetProgress() {
    // transition 끄고 즉시 0% (슬라이드 연출 없이), 복원
    progress.style.transition = "none";
    progress.style.setProperty("--progress", "0%");
    void progress.offsetWidth;  // reflow 강제 → 0% 즉시 반영
    progress.style.transition = "";
    setText("[data-metric-repeat]", "0 / 0");
    setText("[data-metric-item]", "0 / 0");
    setText("[data-metric-state]", "running");
    setText("[data-status-text]", "running");
  }

  function stopPolling() {
    if (polling) { clearInterval(polling); polling = null; }
  }

  function startPolling() {
    stopPolling();
    polling = setInterval(async () => {
      try {
        const st = render(await api.status());
        if (["done", "stopped", "error"].includes(st.state)) {
          stopPolling();
          startBtn.disabled = false;
          log(st.state === "error" ? `에러: ${st.error}` : `종료 (${st.state})`,
              st.state === "error" ? "error" : "done");
        }
      } catch (e) {
        stopPolling();
        startBtn.disabled = false;
        log(`상태 조회 실패: ${e}`, "error");
      }
    }, 300);
  }

  browseBtn.addEventListener("click", async () => {
    const res = await api.browse();
    if (res.data && res.data.path) {
      pathInput.value = res.data.path;
      log("시나리오 선택됨", "ready");
    }
  });

  async function startRun() {
    if (!pathInput.value) { log("JSON 먼저 선택", "warn"); return; }
    startBtn.disabled = true;
    resetProgress();
    const res = await api.start(pathInput.value, Number(repeatInput.value));
    if (!res.ok) {
      startBtn.disabled = false;
      log(`시작 실패: ${res.data.detail || res.status}`, "error");
      return;
    }
    log(`${repeatInput.value}회 반복 시작`, "queued");
    startPolling();
  }

  form.addEventListener("submit", (e) => { e.preventDefault(); startRun(); });
  startBtn.addEventListener("click", (e) => { e.preventDefault(); startRun(); });
  stopBtn.addEventListener("click", async (e) => {
    e.preventDefault();
    await api.stop();
    log("중단 요청", "warn");
  });
})();
