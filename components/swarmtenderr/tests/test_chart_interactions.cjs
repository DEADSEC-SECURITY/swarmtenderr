const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function fixture() {
  const instances = [], openings = [], observers = [];
  class Element {
    constructor() { this.children = []; this.attributes = {}; this.listeners = {}; this.clientWidth = 300; }
    replaceChildren(...children) { this.children = children; }
    setAttribute(name, value) { this.attributes[name] = value; }
    addEventListener(name, callback, options) {
      this.listeners[name] = callback;
      options?.signal.addEventListener('abort', () => delete this.listeners[name]);
    }
    key(key) { this.listeners.keydown({ key, preventDefault() {} }); }
  }
  const body = new Element();
  const context = {
    module: { exports: {} }, AbortController,
    document: { body, createElement: () => new Element(), querySelectorAll: () => [] },
    getComputedStyle: () => ({ fontFamily: 'sans-serif', getPropertyValue: () => '' }),
    ResizeObserver: class {
      constructor() { observers.push(this); }
      observe() {}
      disconnect() { this.disconnected = true; }
    },
    echarts: { init() {
      const chart = {
        handlers: {}, actions: [], on(name, fn) { this.handlers[name] = fn; },
        setOption(option) { this.option = option; }, getOption() { return this.option; }, resize() {},
        dispatchAction(action) { this.actions.push(action); }, dispose() { this.disposed = true; },
      };
      instances.push(chart);
      return chart;
    } },
  };
  vm.runInNewContext(fs.readFileSync(require.resolve('../src/swarmtenderr/static/charts.js'), 'utf8'), context);
  const charts = context.module.exports.create({ bytes: n => `${n} B`, zone: () => 'UTC', openPeriod: (...args) => openings.push(args) });
  return { charts, instances, openings, observers, target: () => new Element(), body };
}

test('mini charts inspect exact values, open the chosen period and dispose on sign-out', () => {
  const f = fixture(), target = f.target();
  const rows = [
    { period: '2026-10-01', count: null, bytes: null, partial: true },
    { period: '2026-11-01', count: 3, bytes: 4096, partial: true },
  ];
  f.charts.sparkline(target, rows, 'monthly', 'bytes');
  const chart = f.instances[0];
  assert.equal(chart.option.series[0].data[0].value, null);
  assert.equal(chart.option.series[0].data[1].value, 4096);
  assert.equal(chart.option.tooltip.appendTo, f.body);
  assert.match(chart.option.tooltip.formatter([{ dataIndex: 1 }]), /2026-11: 3 completed<br>4096 B<br>Partial coverage/);
  target.key('End');
  assert.equal(chart.actions.at(-1).dataIndex, 1);
  target.key('Enter');
  assert.deepEqual(f.openings, [['2026-11-01', 'monthly']]);
  chart.handlers.click({ componentType: 'series', dataIndex: 0 });
  assert.deepEqual(f.openings.at(-1), ['2026-10-01', 'monthly']);
  target.key('Escape');
  assert.equal(chart.actions.at(-1).type, 'hideTip');
  f.charts.dispose();
  assert.equal(chart.disposed, true);
  assert.equal(f.observers[0].disconnected, true);
  assert.equal(target.listeners.keydown, undefined);
  assert.equal(target.children.length, 0);
});

test('live refresh reuses the plot and preserves zoom; tooltips escape data', () => {
  const f = fixture(), target = f.target();
  const rows = [{ period: '2026-11-13', count: 2, bytes: 100 }];
  f.charts.sparkline(target, rows, 'daily', 'count');
  const chart = f.instances[0];
  Object.assign(chart.option.dataZoom[0], { start: 20, end: 80, startValue: 0, endValue: 0 });
  chart.handlers.datazoom();
  f.charts.sparkline(target, rows, 'daily', 'count');
  assert.equal(f.instances.length, 1);
  assert.equal(chart.option.dataZoom[0].startValue, '2026-11-13');
  target.key('+');
  assert.equal(chart.actions.at(-1).type, 'dataZoom');
  rows[0].period = '<img src=x onerror=alert(1)>';
  assert.match(chart.option.tooltip.formatter([{ dataIndex: 0 }]), /&lt;img/);
  assert.doesNotMatch(chart.option.tooltip.formatter([{ dataIndex: 0 }]), /<img/);
});
