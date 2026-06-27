const assert = require("assert");
const { pointInPolygon, isWalkable, buildScenario } = require("./map_geometry");

// pointInPolygon — 단순 정사각형 [0,0]-[10,10]
const square = [[0, 0], [10, 0], [10, 10], [0, 10]];
assert.strictEqual(pointInPolygon([5, 5], square), true, "내부 점");
assert.strictEqual(pointInPolygon([15, 5], square), false, "외부 점(우측)");
assert.strictEqual(pointInPolygon([-1, 5], square), false, "외부 점(좌측)");

// isWalkable — 합성 맵: 큰 hole + 가운데 작은 object
const mapInfo = {
  size: { width: 100, height: 100 },
  walls: [{ holes: [[[10, 10], [90, 10], [90, 90], [10, 90]]] }],
  objects: [{ polygon: [[40, 40], [60, 40], [60, 60], [40, 60]] }],
};
assert.strictEqual(isWalkable(50, 20, mapInfo), true, "hole 안 & object 밖");
assert.strictEqual(isWalkable(50, 50, mapInfo), false, "object 위");
assert.strictEqual(isWalkable(5, 5, mapInfo), false, "hole 밖(벽 띠/맵 밖)");

// buildScenario — 평탄 배열 [{idx,x,y,rot}], 1자리 반올림
const out = buildScenario(
  [{ x: 12.04, y: 22.0, rot: 90 }, { x: 50.16, y: 66.94, rot: 270 }]
);
assert.deepStrictEqual(out, [
  { idx: 0, x: 12.0, y: 22.0, rot: 90.0 },
  { idx: 1, x: 50.2, y: 66.9, rot: 270.0 },
]);
assert.strictEqual(out[0].idx, 0);
assert.strictEqual(out[1].idx, 1);

// 빈 waypoint → 빈 배열
assert.deepStrictEqual(buildScenario([]), []);

console.log("map_geometry.test.js: ALL PASS");
