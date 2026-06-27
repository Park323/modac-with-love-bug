window.ManagerApi = (() => {
  function apiUrl(path) {
    return API_BASE + path;
  }

  async function post(url, body) {
    const res = await fetch(apiUrl(url), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined
    });
    return { ok: res.ok, status: res.status, data: await res.json().catch(() => ({})) };
  }

  return {
    browse: () => post("/scenario/browse"),
    scenarioWaypoints: (scenario) => post("/scenario/waypoints", { scenario }),
    start: (path, repeat) => post("/run/start", { path, repeat }),
    stop: () => post("/run/stop"),
    status: async () => {
      const res = await fetch(apiUrl("/run/status"));
      return res.json();
    },
    recordStart: (durationSec) => post("/record/start", { duration_sec: durationSec }),
    recordStop: () => post("/record/stop"),
    recordStatus: async () => {
      const res = await fetch(apiUrl("/record/status"));
      return res.json();
    }
  };
})();
