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

// buildScenario — 봉투 구조 / t 순번 / 반올림
const out = buildScenario(
  [{ x: 116.4, y: 261.6, rot: 90 }, { x: 990.2, y: 320.0, rot: 45.25 }],
  { map: "Transport Ship 2.0", size: { width: 1980, height: 654 } }
);
assert.strictEqual(out.schema_version, "wp-0.1");
assert.strictEqual(out.map, "Transport Ship 2.0");
assert.deepStrictEqual(out.size, { width: 1980, height: 654 });
assert.strictEqual(out.events.length, 2);
assert.deepStrictEqual(out.events[0], { t: 0, type: "waypoint", x: 116, y: 262, rot: 90 });
assert.deepStrictEqual(out.events[1], { t: 1, type: "waypoint", x: 990, y: 320, rot: 45.3 });

// 빈 waypoint → events 빈 배열
assert.deepStrictEqual(
  buildScenario([], { map: "m", size: { width: 1, height: 2 } }).events, []
);

console.log("map_geometry.test.js: ALL PASS");
