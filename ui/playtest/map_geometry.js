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

  // waypoint 허용 = walkable:true 로 표시된 통로 object 위에서만.
  // 중앙 바닥/장애물 등 그 외 영역은 전부 클릭 불가.
  function isWalkable(mx, my, mapInfo) {
    const pt = [mx, my];
    for (const obj of mapInfo.objects || []) {
      if (obj.walkable === true && obj.polygon && pointInPolygon(pt, obj.polygon)) {
        return true;
      }
    }
    return false;
  }

  // waypoint set → 평탄 배열 [{idx,x,y,rot}] (모듈 전달용 입력)
  function buildScenario(waypoints) {
    return waypoints.map((wp, i) => ({
      idx: i,
      x: +Number(wp.x).toFixed(1),
      y: +Number(wp.y).toFixed(1),
      rot: +Number(wp.rot).toFixed(1),
    }));
  }

  return { pointInPolygon, isWalkable, buildScenario };
});
