#!/usr/bin/env node
// The site treats every researcher the same way: no page or script carries a per-person
// disclosure or any other special case keyed on a name. The About page states the rule once,
// in plain words, for everyone.
//
// Usage: node tests/no_person_special_cases.test.js

'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const SITE = path.join(__dirname, '..', 'site');
const read = f => fs.readFileSync(path.join(SITE, f), 'utf8');

let failures = 0;
function test(name, fn) {
  try { fn(); console.log('ok   ' + name); } catch (e) { failures++; console.log('FAIL ' + name + '\n     ' + (e && e.message)); }
}

const sources = fs.readdirSync(SITE).filter(f => /\.(html|js)$/.test(f));

test('no site file defines or reads a maintainer name', () => {
  for (const f of sources) {
    assert.ok(!/SITE_MAINTAINER/.test(read(f)), f + ' still mentions SITE_MAINTAINER');
  }
});

test('the insights page has no per-person disclosure', () => {
  const html = read('insights.html');
  assert.ok(!/maintainer-disclosure/.test(html), 'disclosure element still present');
  assert.ok(!/Disclosure:/.test(html), 'disclosure text still present');
});

test('the About page says the maintainer\'s papers are ranked like everyone else\'s', () => {
  const about = read('about.html').replace(/\s+/g, ' ');
  assert.ok(/maintainer's own papers are in the data and are ranked by the same rules as everyone else's/.test(about));
});

if (failures) { console.log(`\n${failures} test(s) failed.`); process.exit(1); }
console.log('\nAll person special-case tests passed.');
