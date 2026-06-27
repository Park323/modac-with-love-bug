window.MapSelector = (function () {
  let mapInfo = null;
  let scale = 1;
  let waypoints = [];
  let pending = null;      // 드래그 중인 점 { x, y } (맵 좌표)
  let dragging = false;
  let currentRot = 90;     // 0=북, 시계방향
  let started = false;     // init 중복 방지

  let canvas, ctx, coordEl, listEl, previewEl;

  // ── 좌표 변환 ──
  const toCanvas = (mx, my) => [mx * scale, my * scale];
  const toMap = (cx, cy) => [cx / scale, cy / scale];

  function calcRot(pcx, pcy, cx, cy) {
    const dx = cx - pcx, dy = cy - pcy;
    if (Math.hypot(dx, dy) < 4) return currentRot;
    return ((Math.atan2(dx, -dy) * 180) / Math.PI + 360) % 360;
  }

  // ── 그리기 ──
  function drawPolygon(pts, fill, stroke, lw) {
    if (!pts.length) return;
    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
    ctx.closePath();
    if (fill) { ctx.fillStyle = fill; ctx.fill(); }
    if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = lw; ctx.stroke(); }
  }

  function drawArrow(cx, cy, rot, len, color) {
    const angle = ((rot - 90) * Math.PI) / 180;
    const ex = cx + Math.cos(angle) * len, ey = cy + Math.sin(angle) * len;
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(ex, ey);
    ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.stroke();
    const hw = 4, hlen = 8;
    const bx = ex - Math.cos(angle) * hlen, by = ey - Math.sin(angle) * hlen;
    ctx.beginPath(); ctx.moveTo(ex, ey);
    ctx.lineTo(bx + Math.cos(angle + Math.PI / 2) * hw, by + Math.sin(angle + Math.PI / 2) * hw);
    ctx.lineTo(bx - Math.cos(angle + Math.PI / 2) * hw, by - Math.sin(angle + Math.PI / 2) * hw);
    ctx.closePath(); ctx.fillStyle = color; ctx.fill();
  }

  function drawMap() {
    const s = (pts) => pts.map((p) => toCanvas(p[0], p[1]));
    for (const wall of mapInfo.walls || []) {
      if (wall.polygon) drawPolygon(s(wall.polygon), "#2c2c3e", null, 0);
      for (const hole of wall.holes || []) drawPolygon(s(hole), "#c5d0d4", "#8fa0a5", 0.5);
    }
    for (const obj of mapInfo.objects || []) {
      if (obj.polygon) drawPolygon(s(obj.polygon), "#6d7a7c", "#556062", 1);
    }
  }

  function drawWaypoints() {
    waypoints.forEach((wp, i) => {
      const [cx, cy] = toCanvas(wp.x, wp.y);
      drawArrow(cx, cy, wp.rot, 14, "#e74c3c");
      ctx.beginPath(); ctx.arc(cx, cy, 7, 0, Math.PI * 2);
      ctx.fillStyle = "#e74c3c"; ctx.fill();
      ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.5; ctx.stroke();
      ctx.fillStyle = "#fff"; ctx.font = "bold 8px sans-serif";
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(String(i + 1), cx, cy);
    });
  }

  function drawPending() {
    if (!pending) return;
    const [cx, cy] = toCanvas(pending.x, pending.y);
    if (dragging) drawArrow(cx, cy, currentRot, 18, "#f0c040");
    ctx.beginPath(); ctx.arc(cx, cy, 7, 0, Math.PI * 2);
    ctx.fillStyle = "#f0c040"; ctx.fill();
    ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.5; ctx.stroke();
  }

  function redraw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!mapInfo) return;
    drawMap(); drawWaypoints(); drawPending();
  }

  // ── 패널 ──
  function scenario() {
    return window.MapGeometry.buildScenario(waypoints);
  }

  function updatePanel() {
    listEl.innerHTML = "";
    waypoints.forEach((wp, i) => {
      const div = document.createElement("div");
      div.className = "waypoint-item";
      div.draggable = true;
      div.dataset.index = i;
      div.innerHTML =
        '<span class="wp-num">' + (i + 1) + '</span>' +
        '<span class="wp-coords">x: ' + wp.x.toFixed(1) +
        '<br>y: ' + wp.y.toFixed(1) +
        '<br>rot: ' + wp.rot.toFixed(1) + '°</span>' +
        '<button type="button" data-remove="' + i + '">✕</button>';
      listEl.appendChild(div);
    });
    previewEl.textContent = JSON.stringify(scenario(), null, 2);
  }

  function setCoord(text, isError) {
    coordEl.textContent = text;
    coordEl.classList.toggle("is-error", !!isError);
  }

  // ── 마우스 ──
  function canvasPos(e) {
    const r = canvas.getBoundingClientRect();
    return [
      (e.clientX - r.left) * (canvas.width / r.width),
      (e.clientY - r.top) * (canvas.height / r.height),
    ];
  }

  function onDown(e) {
    const [cx, cy] = canvasPos(e);
    const [mx, my] = toMap(cx, cy);
    if (!window.MapGeometry.isWalkable(mx, my, mapInfo)) {
      pending = null; dragging = false;
      setCoord("장애물 — 클릭 불가", true);
      return;
    }
    pending = { x: mx, y: my };
    currentRot = 90; dragging = true;
    redraw();
    setCoord("x: " + mx.toFixed(1) + "  y: " + my.toFixed(1), false);
  }

  function onMove(e) {
    const [cx, cy] = canvasPos(e);
    if (dragging && pending) {
      const [pcx, pcy] = toCanvas(pending.x, pending.y);
      currentRot = calcRot(pcx, pcy, cx, cy);
      redraw();
      setCoord("x: " + pending.x.toFixed(1) + "  y: " + pending.y.toFixed(1) +
               "  rot: " + currentRot.toFixed(1) + "°", false);
      return;
    }
    // 호버: 드래그 아니어도 현재 맵 좌표 실시간 표시
    const [mx, my] = toMap(cx, cy);
    setCoord("x: " + mx.toFixed(1) + "  y: " + my.toFixed(1), false);
  }

  function onLeave() {
    const added = dragging && pending;   // commit will add a waypoint
    commit();
    if (!added) setCoord("—", false);    // 호버만 하다 나간 경우에만 readout 비움
  }

  function commit() {
    if (!dragging || !pending) return;
    dragging = false;
    waypoints.push({ x: pending.x, y: pending.y, rot: currentRot });
    pending = null;
    redraw(); updatePanel();
    const last = waypoints[waypoints.length - 1];
    setCoord("추가 — x: " + last.x.toFixed(1) + "  y: " + last.y.toFixed(1) +
             "  rot: " + last.rot.toFixed(1) + "°", false);
  }

  function init() {
    if (started) return;
    started = true;
    canvas = document.getElementById("mapCanvas");
    ctx = canvas.getContext("2d");
    coordEl = document.querySelector("[data-coord-display]");
    listEl = document.querySelector("[data-waypoint-list]");
    previewEl = document.querySelector("[data-json-preview]");

    fetch("./mapinfo.json")
      .then((res) => res.json())
      .then((data) => {
        mapInfo = data;
        const wrap = canvas.parentElement;
        const maxW = Math.max(1, wrap.clientWidth - 32);
        scale = maxW / mapInfo.size.width;
        canvas.width = Math.floor(mapInfo.size.width * scale);
        canvas.height = Math.floor(mapInfo.size.height * scale);
        redraw(); updatePanel();
      })
      .catch((err) => {
        setCoord("맵 로드 실패: " + err, true);
        console.error(err);
      });

    canvas.addEventListener("mousedown", onDown);
    canvas.addEventListener("mousemove", onMove);
    canvas.addEventListener("mouseup", commit);
    canvas.addEventListener("mouseleave", onLeave);

    document.querySelector("[data-copy-json]").addEventListener("click", () => {
      navigator.clipboard.writeText(JSON.stringify(scenario(), null, 2))
        .catch(() => setCoord("복사 실패", true));
    });
    document.querySelector("[data-download-json]").addEventListener("click", () => {
      const blob = new Blob([JSON.stringify(scenario(), null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "waypoints_scenario.json";
      a.click();
    });
    document.querySelector("[data-clear-waypoints]").addEventListener("click", () => {
      waypoints = []; pending = null; dragging = false;
      redraw(); updatePanel(); setCoord("—", false);
    });
    listEl.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-remove]");
      if (!btn) return;
      waypoints.splice(Number(btn.dataset.remove), 1);
      redraw(); updatePanel();
    });

    // ── waypoint 순서 drag-drop 재배치 → 맵 index도 갱신 ──
    let dragSrc = null;
    listEl.addEventListener("dragstart", (e) => {
      const it = e.target.closest(".waypoint-item");
      if (!it) return;
      dragSrc = Number(it.dataset.index);
      e.dataTransfer.effectAllowed = "move";
    });
    listEl.addEventListener("dragover", (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      const it = e.target.closest(".waypoint-item");
      listEl.querySelectorAll(".waypoint-item.drag-over")
        .forEach((n) => n.classList.remove("drag-over"));
      if (it) it.classList.add("drag-over");
    });
    listEl.addEventListener("drop", (e) => {
      e.preventDefault();
      const it = e.target.closest(".waypoint-item");
      listEl.querySelectorAll(".waypoint-item.drag-over")
        .forEach((n) => n.classList.remove("drag-over"));
      if (!it || dragSrc === null) { dragSrc = null; return; }
      const tgt = Number(it.dataset.index);
      if (Number.isNaN(tgt) || tgt === dragSrc) { dragSrc = null; return; }
      const [moved] = waypoints.splice(dragSrc, 1);
      waypoints.splice(tgt, 0, moved);
      dragSrc = null;
      redraw(); updatePanel();
    });
    listEl.addEventListener("dragend", () => {
      dragSrc = null;
      listEl.querySelectorAll(".waypoint-item.drag-over")
        .forEach((n) => n.classList.remove("drag-over"));
    });
  }

  return { init };
})();
