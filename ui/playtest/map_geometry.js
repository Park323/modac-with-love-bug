(function (factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof window !== "undefined") window.MapGeometry = api;
})(function () {
  // ray casting: pt가 닫힌 polygon 내부인지 (poly 첫 점 반복 안 함)
  function pointInPolygon(pt, poly) {
    const px = pt[0], py = pt[1];
    let inside = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const xi = poly[i][0], yi = poly[i][1];
      const xj = poly[j][0], yj = poly[j][1];
      const intersect =
        (yi > py) !== (yj > py) &&
        px < ((xj - xi) * (py - yi)) / (yj - yi) + xi;
      if (intersect) inside = !inside;
    }
    return inside;
  }

  // walkable = 어떤 wall hole 안 AND 모든 object polygon 밖
  function isWalkable(mx, my, mapInfo) {
    const pt = [mx, my];
    let inHole = false;
    for (const wall of mapInfo.walls || []) {
      for (const hole of wall.holes || []) {
        if (pointInPolygon(pt, hole)) { inHole = true; break; }
      }
      if (inHole) break;
    }
    if (!inHole) return false;
    for (const obj of mapInfo.objects || []) {
      if (obj.polygon && pointInPolygon(pt, obj.polygon)) return false;
    }
    return true;
  }

  // waypoint set → 시나리오 봉투 (모듈 전달용 입력 계약)
  function buildScenario(waypoints, meta) {
    const size = (meta && meta.size) || { width: 0, height: 0 };
    return {
      schema_version: "wp-0.1",
      map: (meta && meta.map) || "unknown",
      size: { width: size.width, height: size.height },
      events: waypoints.map((wp, i) => ({
        t: i,
        type: "waypoint",
        x: Math.round(wp.x),
        y: Math.round(wp.y),
        rot: +Number(wp.rot).toFixed(1),
      })),
    };
  }

  return { pointInPolygon, isWalkable, buildScenario };
});
