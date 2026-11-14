const { test } = require('node:test');
const assert = require('node:assert/strict');
const { split, total, format, editor } = require('../src/swarmtenderr/static/durations.js');

test('existing durations round trip without losing seconds or capping hours', () => {
  for (const seconds of [0, 1, 59, 60, 120, 2100, 3600, 3661, 4321, 7200, 86400]) {
    assert.equal(total(split(seconds)), seconds);
  }
  assert.deepEqual(split(7200), [2, 0, 0]);
  assert.deepEqual(split(2100), [0, 35, 0]);
  assert.deepEqual(split(86400), [24, 0, 0]);
  assert.equal(format(7200), '2 hours');
  assert.equal(format(3661), '1 hour 1 minute 1 second');
  assert.equal(format(0), '0 seconds');
});

test('blank, negative, fractional, overflow and nonnumeric components cannot become settings', () => {
  for (const parts of [['', '0', '0'], ['-1', '0', '0'], ['0', '1.5', '0'],
    ['0', '60', '0'], ['0', '0', '60'], ['1e3', '0', '0'], ['9007199254740991', '0', '0']]) {
    assert.equal(total(parts), null);
  }
  assert.throws(() => split(-1));
  assert.throws(() => split(1.5));
});

function fixture(locked = false, value = 7200) {
  const elements = [];
  const document = { createElement(tag) {
    const element = { tag, children: [], dataset: {}, listeners: {}, attributes: {}, value: '',
      classList: { toggle() {} }, append(...children) { this.children.push(...children); },
      setAttribute(key, value) { this.attributes[key] = value; },
      addEventListener(event, callback) { this.listeners[event] = callback; },
      setCustomValidity(message) { this.error = message; } };
    elements.push(element);
    return element;
  } };
  const result = editor(document, { name: 'retry_seconds', label: 'No-progress retry delay', value,
    minimum: 300, maximum: 86400, locked });
  const controls = elements.filter(e => e.type === 'number');
  function enter(parts) {
    controls.forEach((control, i) => { control.value = String(parts[i]); });
    controls[0].listeners.input();
  }
  return { ...result, controls, enter, elements };
}

test('editing mixed units produces only the existing seconds field for the API', () => {
  const f = fixture();
  assert.equal(f.input.name, 'retry_seconds');
  assert.equal(f.input.dataset.original, '7200');
  assert.equal(f.element.children[0].textContent, 'No-progress retry delay');
  assert.equal(f.focusId, f.controls[0].id);
  assert.ok(f.controls.every(c => !c.name && c.required));
  f.enter([1, 2, 3]);
  assert.equal(Number(f.input.value), 3723);
  assert.equal(f.controls[0].error, '');
  assert.equal(f.elements.find(e => e.className === 'duration-summary').textContent, '1 hour 2 minutes 3 seconds');
  assert.equal(f.input.dataset.original, '7200');
});

test('combined duration honors minimum and maximum rather than validating each part alone', () => {
  const f = fixture();
  for (const parts of [[0, 0, 0], [0, 4, 59], [24, 0, 1], [0, '', 0]]) {
    f.enter(parts);
    assert.ok(f.controls[0].error);
    assert.equal(f.input.value, '');
  }
  for (const [parts, expected] of [[[0, 5, 0], 300], [[24, 0, 0], 86400]]) {
    f.enter(parts);
    assert.equal(f.controls[0].error, '');
    assert.equal(Number(f.input.value), expected);
  }
});

test('deployment locks disable the entire editor and preserve non-round saved durations', () => {
  const f = fixture(true, 4321);
  assert.equal(f.element.disabled, true);
  assert.equal(f.input.disabled, true);
  assert.ok(f.controls.every(c => c.disabled));
  assert.deepEqual(f.controls.map(c => Number(c.value)), [1, 12, 1]);
  assert.equal(Number(f.input.value), 4321);
});
