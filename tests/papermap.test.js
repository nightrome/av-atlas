#!/usr/bin/env node
// Tests for the paper map (site/papermap.js): the size-by-citations option is gone, the
// locked map used on author and institution pages stays hidden unless at least 3 of the
// page's papers are on the map, and the help text on the Papers page stays short.
//
// Usage: node tests/papermap.test.js

'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SITE = path.join(__dirname, '..', 'site');
const read = f => fs.readFileSync(path.join(SITE, f), 'utf8');
const MAP_JS = read('papermap.js');

let failures = 0;
async function test(name, fn) {
  try { await fn(); console.log('ok   ' + name); } catch (e) { failures++; console.log('FAIL ' + name + '\n     ' + (e && e.message)); }
}

// A tiny browser: just enough for papermap.js to load, add its stylesheet and ask for points.json.
function loadMap(points) {
  const appended = [];
  const sandbox = {
    console, URLSearchParams, Promise, Set,
    document: {
      head: { appendChild: e => appended.push(e) },
      getElementById: () => null,
      createElement: () => ({ style: {}, dataset: {} }),
    },
    location: { search: '' },
    fetch: async () => ({ ok: true, json: async () => points }),
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(MAP_JS, sandbox);
  return { map: sandbox.PaperMap, appended };
}

const POINTS = {
  titles: ['A', 'B', 'C', 'D', 'E'], x: [1, 2, 3, 4, 5], y: [1, 2, 3, 4, 5],
  year: [2020, 2020, 2021, 2021, 2022], cat: [0, 0, 1, 1, 1], cites: [5, 4, 3, 2, 1], cats: ['x', 'y'],
};
const tick = () => new Promise(r => setTimeout(r, 20));
const host = () => ({ dataset: {}, appended: [], appendChild(e) { this.appended.push(e); } });

(async () => {
  await test('size by citations is gone from the code and the page', () => {
    assert.ok(!/pm-size|sizeF|sized\b/.test(MAP_JS), 'papermap.js still mentions sizing');
    assert.ok(!/Size by citations|pm-size/.test(read('index.html')), 'index.html still has the checkbox');
    assert.ok(!/size by citations/i.test(read('about.html')), 'about.html still mentions it');
  });

  await test('the map exposes mount, mountLocked and the host hooks', () => {
    const { map } = loadMap(POINTS);
    for (const k of ['mount', 'mountLocked', 'setPapers', 'setFilterMatches']) assert.strictEqual(typeof map[k], 'function', k);
  });

  await test('the intro line on the Papers page is short and the keyboard help lives in a disclosure', () => {
    const html = read('index.html');
    const intro = html.match(/<p class="section-intro"[^>]*>([^<]*)<\/p>\s*<details class="pm-help">/);
    assert.ok(intro, 'intro paragraph followed by the help disclosure not found');
    assert.ok(intro[1].length < 160, 'intro is ' + intro[1].length + ' characters');
    assert.ok(/<details class="pm-help">[\s\S]*Keyboard/.test(html));
  });

  await test('locked map is skipped for fewer than 3 titles without loading anything', async () => {
    const { map } = loadMap(POINTS);
    const h = host();
    map.mountLocked(h, { titles: ['A', 'B'] });
    await tick();
    assert.strictEqual(h.appended.length, 0);
    assert.strictEqual(h.dataset.pmMounted, undefined);
  });

  await test('locked map stays hidden when fewer than 3 of the titles are on the map', async () => {
    const { map } = loadMap(POINTS);
    const h = host();
    map.mountLocked(h, { titles: ['A', 'B', 'not on the map', 'neither'] });
    await tick();
    assert.strictEqual(h.appended.length, 0);
  });

  if (failures) { console.log(`\n${failures} test(s) failed.`); process.exit(1); }
  console.log('\nAll paper map tests passed.');
})();
