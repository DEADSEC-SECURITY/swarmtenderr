const { test } = require('node:test');
const assert = require('node:assert/strict');
const { periodBounds, nearestIndex, zoomWindow } = require('../src/swarmtenderr/static/charts.js');
test('day and leap-year month drill down use inclusive calendar dates', () => {
  assert.deepEqual(periodBounds('2024-02'), { from: '2024-02-01', to: '2024-02-29' });
  assert.deepEqual(periodBounds('2026-11-13'), { from: '2026-11-13', to: '2026-11-13' });
  assert.deepEqual(periodBounds('2026-11-01', 'monthly'), { from: '2026-11-01', to: '2026-11-30' });
  assert.throws(() => periodBounds('2026-13'));
  assert.throws(() => periodBounds('2026-02-30'));
  assert.throws(() => periodBounds('javascript:bad'));
});
test('irregularly spaced samples select the closest observation', () => {
  assert.equal(nearestIndex([], 3), -1);
  assert.equal(nearestIndex([2, 5, 100], 50), 1);
  assert.equal(nearestIndex([2, 5, 100], 99), 2);
  assert.equal(nearestIndex([2, 5], -5), 0);
});
test('zoom remains bounded at either edge and can return to full history', () => {
  assert.deepEqual(zoomWindow(0, 100, 2), [0, 100]);
  assert.deepEqual(zoomWindow(0, 10, 2), [0, 20]);
  assert.deepEqual(zoomWindow(90, 100, 2), [80, 100]);
  assert.deepEqual(zoomWindow(10, 90, 0), [49, 51]);
});
