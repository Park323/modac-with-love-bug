(function (root) {
  function payload(scenarioFn) {
    return { waypoints: scenarioFn() };
  }

  async function post(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    return res;
  }

  function init() {
    const startBtn = document.querySelector("[data-auto-start]");
    const stopBtn = document.querySelector("[data-auto-stop]");
    const statusEl = document.querySelector("[data-auto-status]");
    if (!startBtn || !stopBtn) return;

    let poll = null;
    const setStatus = (t) => { if (statusEl) statusEl.textContent = t; };

    const stopPoll = () => { if (poll) { clearInterval(poll); poll = null; } };
    const startPoll = () => {
      stopPoll();
      poll = setInterval(async () => {
        try {
          const st = await (await fetch("/auto/status")).json();
          setStatus(`${st.state} · WP ${st.wp_done}/${st.wp_total}`);
          if (["done", "stopped", "error"].includes(st.state)) {
            stopPoll();
            startBtn.disabled = false;
          }
        } catch (e) { /* 무시 */ }
      }, 500);
    };

    startBtn.addEventListener("click", async () => {
      const wps = root.MapSelector.scenario();
      if (!wps.length) { setStatus("waypoint 없음"); return; }
      startBtn.disabled = true;
      const res = await post("/auto/start", payload(root.MapSelector.scenario));
      if (!res.ok) {
        startBtn.disabled = false;
        setStatus("시작 실패 (" + res.status + ")");
        return;
      }
      setStatus("running");
      startPoll();
    });

    stopBtn.addEventListener("click", async () => {
      await post("/auto/stop");
      setStatus("stopping…");
    });
  }

  const api = { payload, init };
  if (typeof module !== "undefined" && module.exports) module.exports = { AutoRun: api };
  root.AutoRun = api;
})(typeof window !== "undefined" ? window : globalThis);
