const assert = require("assert");
const { AutoRun } = require("./autorun.js");

// payload: scenario 함수 결과를 {waypoints} 봉투로 감싼다.
const fakeScenario = () => [
  { idx: 0, x: 1.1, y: 2.2, rot: 90 },
  { idx: 1, x: 3.3, y: 4.4, rot: 0 },
];

const p = AutoRun.payload(fakeScenario);
assert.deepStrictEqual(p, {
  waypoints: [
    { idx: 0, x: 1.1, y: 2.2, rot: 90 },
    { idx: 1, x: 3.3, y: 4.4, rot: 0 },
  ],
});

// 빈 시나리오도 빈 waypoints 봉투
assert.deepStrictEqual(AutoRun.payload(() => []), { waypoints: [] });

console.log("autorun.test.js passed");
