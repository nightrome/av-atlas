#!/usr/bin/env node
// Regression tests for sortable.js's click-to-sort direction logic: first
// click on a column must land in its "preferred" order (numeric columns
// high-to-low, text columns A-Z), a second click on the SAME column must
// flip it, and switching to a different column must reset to that column's
// own preferred order rather than carrying over the previous direction.
//
// Usage: node av-atlas/tests/sortable.test.js

'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

// sortable.js expects just enough DOM to inject a <style> tag, walk
// thead th elements, and attach click listeners -- build the smallest fake
// table that satisfies that, no real DOM/jsdom dependency. Models a real
// children array (not just a captured string) so a test can plant existing
// child nodes (e.g. network.html's <span class="th-main">/th-sub>) and
// assert makeSortable() doesn't wipe them out -- a real bug this session
// (`th.textContent = th.textContent` before appending the indicator
// collapsed any such markup into flat text).
function fakeHeaderCell(text) {
  const listeners = [];
  const children = [{ isTextNode: true, text }];
  const classes = new Set();
  return {
    dataset: {},
    children,
    classList: {
      add: c => classes.add(c),
      remove: c => classes.delete(c),
      contains: c => classes.has(c),
    },
    get textContent() { return children.map(c => c.text || (c.textContent || '')).join(''); },
    set textContent(v) { children.length = 0; children.push({ isTextNode: true, text: v }); },
    appendChild(el) { children.push(el); return el; },
    querySelector(sel) {
      if (sel !== '.sort-indicator') return null;
      return children.find(c => c && c.className === 'sort-indicator') || null;
    },
    addEventListener(evt, fn) { if (evt === 'click') listeners.push(fn); },
    click() { listeners.forEach(fn => fn()); },
  };
}

function fakeIndicator() {
  const classes = new Set();
  return {
    className: '', textContent: '',
    classList: {
      add: c => classes.add(c),
      remove: c => classes.delete(c),
      contains: c => classes.has(c),
    },
  };
}

function loadSortable() {
  const created = [];
  const sandbox = {
    document: {
      createElement: (tag) => {
        if (tag === 'style') return { textContent: '' };
        const el = fakeIndicator();
        created.push(el);
        return el;
      },
      head: { appendChild: () => {} },
    },
    console,
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  const code = fs.readFileSync(path.join(__dirname, '..', 'site', 'sortable.js'), 'utf8');
  vm.runInContext(code, sandbox);
  return sandbox;
}

let failures = 0;
function test(name, fn) {
  try {
    fn();
    console.log(`  ok  ${name}`);
  } catch (err) {
    failures++;
    console.log(`FAIL  ${name}`);
    console.log(`      ${err.message}`);
  }
}

function makeTable(ths) {
  return {
    classList: { add() {} },
    querySelectorAll: (sel) => (sel === 'thead th' ? ths : []),
  };
}

test('first click on a numeric column sorts high-to-low (descending)', () => {
  const sandbox = loadSortable();
  const nameTh = fakeHeaderCell('Author');
  const citTh = fakeHeaderCell('Citations');
  const table = makeTable([nameTh, citTh]);
  const data = [{ name: 'Low', citations: 10 }, { name: 'High', citations: 90 }];
  const columns = [
    { accessor: r => r.name, numeric: false },
    { accessor: r => r.citations, numeric: true },
  ];
  let rendered = null;
  sandbox.makeSortable(table, data, columns, (sorted) => { rendered = sorted; });
  citTh.click();
  assert.deepStrictEqual(Array.from(rendered, r => r.name), ['High', 'Low']);
  assert.strictEqual(citTh.querySelector('.sort-indicator').textContent, '▼');
});

test('first click on a text column sorts A-Z (ascending)', () => {
  const sandbox = loadSortable();
  const nameTh = fakeHeaderCell('Author');
  const table = makeTable([nameTh]);
  const data = [{ name: 'Zeta' }, { name: 'Alpha' }];
  const columns = [{ accessor: r => r.name, numeric: false }];
  let rendered = null;
  sandbox.makeSortable(table, data, columns, (sorted) => { rendered = sorted; });
  nameTh.click();
  assert.deepStrictEqual(Array.from(rendered, r => r.name), ['Alpha', 'Zeta']);
  assert.strictEqual(nameTh.querySelector('.sort-indicator').textContent, '▲');
});

test('second click on the same column flips the direction', () => {
  const sandbox = loadSortable();
  const citTh = fakeHeaderCell('Citations');
  const table = makeTable([citTh]);
  const data = [{ citations: 10 }, { citations: 90 }];
  const columns = [{ accessor: r => r.citations, numeric: true }];
  let rendered = null;
  sandbox.makeSortable(table, data, columns, (sorted) => { rendered = sorted; });
  citTh.click(); // descending: 90, 10
  citTh.click(); // ascending: 10, 90
  assert.deepStrictEqual(Array.from(rendered, r => r.citations), [10, 90]);
  assert.strictEqual(citTh.querySelector('.sort-indicator').textContent, '▲');
});

test('clicking a different column resets to ITS preferred order, not the previous direction', () => {
  const sandbox = loadSortable();
  const nameTh = fakeHeaderCell('Author');
  const citTh = fakeHeaderCell('Citations');
  const table = makeTable([nameTh, citTh]);
  const data = [{ name: 'Zeta', citations: 10 }, { name: 'Alpha', citations: 90 }];
  const columns = [
    { accessor: r => r.name, numeric: false },
    { accessor: r => r.citations, numeric: true },
  ];
  let rendered = null;
  sandbox.makeSortable(table, data, columns, (sorted) => { rendered = sorted; });
  citTh.click(); // descending citations: 90, 10 -- state.dir now -1
  nameTh.click(); // switch column: must be ascending A-Z, not inherit dir=-1
  assert.deepStrictEqual(Array.from(rendered, r => r.name), ['Alpha', 'Zeta']);
  assert.strictEqual(nameTh.querySelector('.sort-indicator').textContent, '▲');
  // Reverts to blank, not some "inactive but sortable" glyph -- only the
  // column actually driving the current sort ever shows an indicator, since
  // a table can only be sorted by one column at a time.
  assert.strictEqual(citTh.querySelector('.sort-indicator').textContent, '', 'previous column\'s indicator must clear, not show a neutral glyph');
});

test('no column shows a sort indicator before any click', () => {
  const sandbox = loadSortable();
  const nameTh = fakeHeaderCell('Author');
  const citTh = fakeHeaderCell('Citations');
  const table = makeTable([nameTh, citTh]);
  const columns = [
    { accessor: r => r.name, numeric: false },
    { accessor: r => r.citations, numeric: true },
  ];
  sandbox.makeSortable(table, [], columns, () => {});
  assert.strictEqual(nameTh.querySelector('.sort-indicator').textContent, '');
  assert.strictEqual(citTh.querySelector('.sort-indicator').textContent, '');
});

test('preserves existing header markup instead of collapsing it to plain text', () => {
  // Real bug, user-reported as "columns overlap": network.html's headers
  // carry a two-line <span class="th-main">/<span class="th-sub"> structure
  // (e.g. "Papers" / "w/ other institutions"). makeSortable used to reset
  // th.textContent before appending its indicator, which wiped that markup
  // down to one flat run of text with no line break and no th-sub class for
  // the "don't wrap" CSS to even target -- the sub-label then spilled into
  // the next column instead of wrapping inside its own.
  const sandbox = loadSortable();
  const th = fakeHeaderCell('');
  const mainSpan = { className: 'th-main', textContent: 'Papers' };
  const subSpan = { className: 'th-sub', textContent: 'w/ other institutions' };
  th.children.length = 0; // this header's real content is the two spans, not a bare text node
  th.appendChild(mainSpan);
  th.appendChild(subSpan);
  const table = makeTable([th]);
  const columns = [{ accessor: r => r.papers, numeric: true }];
  sandbox.makeSortable(table, [], columns, () => {});
  assert.ok(th.children.includes(mainSpan), 'th-main span must survive untouched');
  assert.ok(th.children.includes(subSpan), 'th-sub span must survive untouched');
});

test('calling makeSortable again on the same table does not stack a second indicator or listener', () => {
  // Real bug: every page's render() calls makeSortableSafe/makeSortable
  // again on the SAME <table> on every search keystroke, pagination click,
  // topN change, etc. -- only <tbody> gets rebuilt, <thead>/<th> persist.
  // The old code re-appended a fresh indicator span and a fresh click
  // listener on every call, so after N renders a header carried N
  // indicators (user-reported: "the triangle symbol occurs 6 times") and a
  // click fired N listeners at once.
  const sandbox = loadSortable();
  const citTh = fakeHeaderCell('Citations');
  const table = makeTable([citTh]);
  const columns = [{ accessor: r => r.citations, numeric: true }];
  let renderCount = 0;
  const renderBody = () => { renderCount++; };
  sandbox.makeSortable(table, [{ citations: 1 }], columns, renderBody);
  sandbox.makeSortable(table, [{ citations: 2 }], columns, renderBody);
  const indicators = citTh.children.filter(c => c && c.className === 'sort-indicator');
  assert.strictEqual(indicators.length, 1, 'a second makeSortable call must not append a second indicator span');
  renderCount = 0;
  citTh.click();
  assert.strictEqual(renderCount, 1, 'a click must invoke renderBody exactly once, not once per stacked listener');
});

test('a second makeSortable call operates on the freshly-passed data, not the stale data from the first call', () => {
  const sandbox = loadSortable();
  const citTh = fakeHeaderCell('Citations');
  const table = makeTable([citTh]);
  const columns = [{ accessor: r => r.citations, numeric: true }];
  let rendered = null;
  const renderBody = (sorted) => { rendered = sorted; };
  sandbox.makeSortable(table, [{ citations: 999 }], columns, renderBody);
  sandbox.makeSortable(table, [{ citations: 10 }, { citations: 90 }], columns, renderBody);
  citTh.click();
  assert.deepStrictEqual(Array.from(rendered, r => r.citations), [90, 10],
    'must sort the data from the LATEST makeSortable call, not an earlier one');
});

test('an already-active sort is re-applied to fresh data on the next render, without a fresh click', () => {
  const sandbox = loadSortable();
  const citTh = fakeHeaderCell('Citations');
  const table = makeTable([citTh]);
  const columns = [{ accessor: r => r.citations, numeric: true }];
  let rendered = null;
  const renderBody = (sorted) => { rendered = sorted; };
  sandbox.makeSortable(table, [{ citations: 10 }, { citations: 90 }], columns, renderBody);
  citTh.click(); // descending: 90, 10 -- this column is now the active sort
  sandbox.makeSortable(table, [{ citations: 5 }, { citations: 50 }, { citations: 1 }], columns, renderBody);
  assert.deepStrictEqual(Array.from(rendered, r => r.citations), [50, 5, 1],
    "the new render's data must come back already sorted by the previously-active column/direction");
});

if (failures > 0) {
  console.log(`\n${failures} test(s) failed`);
  process.exit(1);
}
console.log('\nAll sortable.js tests passed');
