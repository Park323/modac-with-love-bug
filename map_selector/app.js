let mapInfo  = null;
let scale    = 1;
let waypoints = [];

// pending point while mouse is held down
let pending   = null;   // { x, y } in map coords
let dragging  = false;
let currentRot = 90;    // degrees, 0=north, clockwise

// ── coordinate helpers ────────────────────────────────────────────────────────

function toCanvas(mx, my) {
  return [mx * scale, my * scale];
}

function toMap(cx, cy) {
  return [cx / scale, cy / scale];
}

// rot from drag delta: 0=north, 90=east, clockwise
function calcRot(pcx, pcy, cx, cy) {
  const dx = cx - pcx;
  const dy = cy - pcy;
  if (Math.hypot(dx, dy) < 4) return currentRot;
  return ((Math.atan2(dx, -dy) * 180 / Math.PI) + 360) % 360;
}

// ── draw ──────────────────────────────────────────────────────────────────────

function drawPolygon(ctx, pts, fill, stroke, lw = 1) {
  if (!pts.length) return;
  ctx.beginPath();
  ctx.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
  ctx.closePath();
  if (fill)   { ctx.fillStyle   = fill;  ctx.fill();                        }
  if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = lw; ctx.stroke(); }
}

function drawArrow(ctx, cx, cy, rot, len, color) {
  const angle = (rot - 90) * Math.PI / 180;
  const ex = cx + Math.cos(angle) * len;
  const ey = cy + Math.sin(angle) * len;
  ctx.beginPath();
  ctx.moveTo(cx, cy);
  ctx.lineTo(ex, ey);
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.stroke();

  // arrowhead
  const hw = 4;
  const hlen = 8;
  const bx = ex - Math.cos(angle) * hlen;
  const by = ey - Math.sin(angle) * hlen;
  ctx.beginPath();
  ctx.moveTo(ex, ey);
  ctx.lineTo(bx + Math.cos(angle + Math.PI / 2) * hw, by + Math.sin(angle + Math.PI / 2) * hw);
  ctx.lineTo(bx - Math.cos(angle + Math.PI / 2) * hw, by - Math.sin(angle + Math.PI / 2) * hw);
  ctx.closePath();
  ctx.fillStyle = color;
  ctx.fill();
}

function drawMap(ctx) {
  const s = pts => pts.map(p => toCanvas(p[0], p[1]));

  for (const wall of mapInfo.walls) {
    drawPolygon(ctx, s(wall.polygon), '#2c2c3e', null);
    for (const hole of wall.holes || []) {
      drawPolygon(ctx, s(hole), '#c5d0d4', '#8fa0a5', 0.5);
    }
  }

  for (const obj of mapInfo.objects) {
    drawPolygon(ctx, s(obj.polygon), '#6d7a7c', '#556062', 1);
  }
}

function drawWaypoints(ctx) {
  waypoints.forEach((wp, i) => {
    const [cx, cy] = toCanvas(wp.x, wp.y);
    drawArrow(ctx, cx, cy, wp.rot, 14, '#e74c3c');

    ctx.beginPath();
    ctx.arc(cx, cy, 7, 0, Math.PI * 2);
    ctx.fillStyle = '#e74c3c';
    ctx.fill();
    ctx.strokeStyle = '#fff';
    ctx.lineWidth = 1.5;
    ctx.stroke();

    ctx.fillStyle = '#fff';
    ctx.font = 'bold 8px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(i + 1, cx, cy);
  });
}

function drawPending(ctx) {
  if (!pending) return;
  const [cx, cy] = toCanvas(pending.x, pending.y);

  if (dragging) {
    drawArrow(ctx, cx, cy, currentRot, 18, '#f0c040');
  }

  ctx.beginPath();
  ctx.arc(cx, cy, 7, 0, Math.PI * 2);
  ctx.fillStyle = '#f0c040';
  ctx.fill();
  ctx.strokeStyle = '#fff';
  ctx.lineWidth = 1.5;
  ctx.stroke();
}

function redraw() {
  const canvas = document.getElementById('mapCanvas');
  const ctx    = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  drawMap(ctx);
  drawWaypoints(ctx);
  drawPending(ctx);
}

// ── panel ─────────────────────────────────────────────────────────────────────

function updatePanel() {
  const list = document.getElementById('waypointList');
  list.innerHTML = '';

  waypoints.forEach((wp, i) => {
    const div = document.createElement('div');
    div.className = 'waypoint-item';
    div.innerHTML = `
      <span class="wp-num">${i + 1}</span>
      <span class="wp-coords">
        x: ${wp.x.toFixed(1)}<br>
        y: ${wp.y.toFixed(1)}<br>
        rot: ${wp.rot.toFixed(1)}°
      </span>
      <button onclick="removeWaypoint(${i})">✕</button>
    `;
    list.appendChild(div);
  });

  const out = waypoints.map(wp => ({
    x:   +wp.x.toFixed(2),
    y:   +wp.y.toFixed(2),
    rot: +wp.rot.toFixed(1),
  }));
  document.getElementById('jsonPreview').textContent = JSON.stringify(out, null, 2);
}

function removeWaypoint(i) {
  waypoints.splice(i, 1);
  redraw();
  updatePanel();
}

// ── init ──────────────────────────────────────────────────────────────────────

async function init() {
  const res = await fetch('/map');
  mapInfo   = await res.json();

  const canvas = document.getElementById('mapCanvas');
  const wrap   = document.getElementById('canvas-wrap');
  const maxW   = wrap.clientWidth - 32;

  scale          = maxW / mapInfo.size.width;
  canvas.width   = Math.floor(mapInfo.size.width  * scale);
  canvas.height  = Math.floor(mapInfo.size.height * scale);

  redraw();

  // ── mouse events ────────────────────────────────────────────────────────────

  canvas.addEventListener('mousedown', e => {
    const r        = canvas.getBoundingClientRect();
    const cx       = (e.clientX - r.left) * (canvas.width  / r.width);
    const cy       = (e.clientY - r.top)  * (canvas.height / r.height);
    const [mx, my] = toMap(cx, cy);

    pending    = { x: mx, y: my };
    currentRot = 90;
    dragging   = true;
    redraw();
  });

  canvas.addEventListener('mousemove', e => {
    if (!dragging || !pending) return;
    const r   = canvas.getBoundingClientRect();
    const cx  = (e.clientX - r.left) * (canvas.width  / r.width);
    const cy  = (e.clientY - r.top)  * (canvas.height / r.height);
    const [pcx, pcy] = toCanvas(pending.x, pending.y);

    currentRot = calcRot(pcx, pcy, cx, cy);
    redraw();

    document.getElementById('coord-display').textContent =
      `x: ${pending.x.toFixed(1)}  y: ${pending.y.toFixed(1)}  rot: ${currentRot.toFixed(1)}°`;
  });

  canvas.addEventListener('mouseup', () => {
    if (!dragging || !pending) return;
    dragging = false;
    waypoints.push({ x: pending.x, y: pending.y, rot: currentRot });
    pending = null;
    redraw();
    updatePanel();
    document.getElementById('coord-display').textContent =
      `Added — x: ${waypoints.at(-1).x.toFixed(1)}  y: ${waypoints.at(-1).y.toFixed(1)}  rot: ${waypoints.at(-1).rot.toFixed(1)}°`;
  });

  canvas.addEventListener('mouseleave', () => {
    if (dragging) {
      dragging = false;
      if (pending) {
        waypoints.push({ x: pending.x, y: pending.y, rot: currentRot });
        pending = null;
        redraw();
        updatePanel();
      }
    }
  });

  // ── buttons ─────────────────────────────────────────────────────────────────

  document.getElementById('copyBtn').addEventListener('click', () => {
    const out = waypoints.map(wp => ({
      x: +wp.x.toFixed(2), y: +wp.y.toFixed(2), rot: +wp.rot.toFixed(1),
    }));
    navigator.clipboard.writeText(JSON.stringify(out, null, 2));
    const btn = document.getElementById('copyBtn');
    btn.textContent = 'Copied!';
    setTimeout(() => btn.textContent = 'Copy JSON', 1500);
  });

  document.getElementById('downloadBtn').addEventListener('click', () => {
    const out  = waypoints.map(wp => ({
      x: +wp.x.toFixed(2), y: +wp.y.toFixed(2), rot: +wp.rot.toFixed(1),
    }));
    const blob = new Blob([JSON.stringify(out, null, 2)], { type: 'application/json' });
    const a    = document.createElement('a');
    a.href     = URL.createObjectURL(blob);
    a.download = 'waypoints.json';
    a.click();
  });

  document.getElementById('clearBtn').addEventListener('click', () => {
    waypoints = [];
    pending   = null;
    dragging  = false;
    redraw();
    updatePanel();
    document.getElementById('coord-display').textContent = '—';
  });
}

document.addEventListener('DOMContentLoaded', init);
