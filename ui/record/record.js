(function () {
  const { qs, setText, formatTime } = window.Lovebug;
  const api = window.ManagerApi;

  const durationInput = qs("[data-duration]");
  const startBtn = qs("[data-rec-start]");
  const stopBtn = qs("[data-rec-stop]");
  const activity = qs("[data-activity-list]");
  const form = qs("[data-record-form]");

  let polling = null;
  let isRecording = false;

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

  function setPath(value) {
    const el = qs("[data-rec-path]");
    el.textContent = value || "—";
  }

  function render(st) {
    setText("[data-rec-state]", st.state || "—");
    setText("[data-status-text]", st.state || "대기 중");
    setText("[data-rec-count]", st.event_count != null ? String(st.event_count) : "—");
    setText("[data-rec-duration]", st.duration_sec != null ? `${st.duration_sec.toFixed(2)}s` : "—");
    setPath(st.path || null);
    return st;
  }

  function stopPolling() {
    if (polling) { clearInterval(polling); polling = null; }
  }

  function startPolling() {
    stopPolling();
    polling = setInterval(async () => {
      try {
        const st = render(await api.recordStatus());
        if (st.state === "done" || st.state === "error") {
          stopPolling();
          isRecording = false;
          startBtn.disabled = false;
          stopBtn.disabled = true;
          if (st.state === "done") {
            const path = st.path || "(경로 없음)";
            const count = st.event_count != null ? st.event_count : 0;
            log(`녹화 완료: ${count}개 이벤트, 저장 경로: `, "done");
            // Surface path via textContent to avoid injection
            const pathEl = qs("[data-rec-path]");
            pathEl.textContent = path;
          } else {
            const errMsg = st.error || "알 수 없는 오류";
            log(`오류: `, "error");
            const errDisplay = qs("[data-rec-path]");
            errDisplay.textContent = errMsg;
          }
        }
      } catch (e) {
        stopPolling();
        isRecording = false;
        startBtn.disabled = false;
        stopBtn.disabled = true;
        log(`상태 조회 실패: ${e}`, "error");
      }
    }, 300);
  }

  async function startRecording() {
    const raw = durationInput.value.trim();
    const parsed = parseFloat(raw);
    const durationSec = (raw === "" || isNaN(parsed)) ? null : parsed;

    startBtn.disabled = true;
    stopBtn.disabled = false;
    isRecording = true;

    const res = await api.recordStart(durationSec);
    if (!res.ok) {
      startBtn.disabled = false;
      stopBtn.disabled = true;
      isRecording = false;
      const detail = (res.data && res.data.detail) ? res.data.detail : String(res.status);
      log(`시작 실패: ${detail}`, "error");
      return;
    }
    log("녹화 시작", "queued");
    startPolling();
  }

  form.addEventListener("submit", (e) => { e.preventDefault(); startRecording(); });
  startBtn.addEventListener("click", (e) => { e.preventDefault(); startRecording(); });

  stopBtn.addEventListener("click", async (e) => {
    e.preventDefault();
    if (!isRecording) return;
    await api.recordStop();
    log("종료 요청", "warn");
  });
})();
