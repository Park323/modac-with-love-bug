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

  // 클릭 허용 판정:
  //  1) 장애물 박스(walkable!==true object) 위 → 불가
  //  2) 통로(walkable:true object) 위 → 가능 (상/하/좌/우 통로)
  //  3) 바닥 hole 안 → 가능 (중앙 빈 바닥)
  //  4) 그 외(외벽 밖) → 불가
  function isWalkable(mx, my, mapInfo) {
    const pt = [mx, my];
    for (const obj of mapInfo.objects || []) {
      if (obj.walkable !== true && obj.polygon && pointInPolygon(pt, obj.polygon)) {
        return false;  // 장애물 박스
      }
    }
    for (const obj of mapInfo.objects || []) {
      if (obj.walkable === true && obj.polygon && pointInPolygon(pt, obj.polygon)) {
        return true;   // 통로
      }
    }
    for (const wall of mapInfo.walls || []) {
      for (const hole of wall.holes || []) {
        if (pointInPolygon(pt, hole)) return true;  // 중앙 바닥
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
