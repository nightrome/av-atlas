#!/usr/bin/env node
// Tests for the page-view counter in nav.js. GoatCounter must load only on
// the production site -- not on the staging preview (same host, different
// path), localhost or a fork -- and always as the pinned script with its SRI
// hash. Every page's Content-Security-Policy has to let that script and the
// count endpoint through, or the browser drops them without a word (that is
// how the old Google Analytics tag went unnoticed for a while). And Google
// Analytics must not creep back into any page or shared script.
//
// Usage: node tests/nav.test.js

'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { makeElement } = require('./dom_stub');

const SITE = path.join(__dirname, '..', 'site');
const NAV_JS = fs.readFileSync(path.join(SITE, 'nav.js'), 'utf8');
const ENDPOINT = 'https://av-atlas.goatcounter.com/count';

// Runs nav.js as if the page were at https://<hostname><pathname><search>.
// Returns the <script> elements it added to <head>, plus the sandbox so a
// test can look at window.goatcounter.
function loadNav(hostname, pathname, search, source) {
  const head = makeElement('head');
  const body = makeElement('body');
  const topnav = makeElement('nav');
  body.appendChild(topnav);
  const sandbox = {
    console,
    URLSearchParams,
    document: {
      head,
      body,
      createElement: makeElement,
      getElementById: id => (id === 'topnav' ? topnav : null),
    },
    location: {
      hostname,
      pathname,
      search: search || '',
      href: `https://${hostname}${pathname}${search || ''}`,
    },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(source || NAV_JS, sandbox, { filename: 'nav.js' });
  const nav = body.children.find(el => el.className === 'topbar');
  return { scripts: head.children.filter(el => el.tagName === 'SCRIPT'), sandbox, nav };
}

// {directive: [sources]} from a page's <meta http-equiv="Content-Security-Policy">.
function readCsp(html) {
  const m = /<meta http-equiv="Content-Security-Policy" content="([^"]*)">/.exec(html);
  if (!m) return null;
  const out = {};
  m[1].split(';').map(s => s.trim()).filter(Boolean).forEach(part => {
    const [name, ...sources] = part.split(/\s+/);
    out[name] = sources;
  });
  return out;
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

const pages = fs.readdirSync(SITE).filter(f => f.endsWith('.html')).sort();

test('loads GoatCounter on the production site', () => {
  ['/av-atlas/', '/av-atlas/index.html', '/av-atlas/author.html'].forEach(p => {
    const { scripts } = loadNav('nightrome.github.io', p);
    assert.strictEqual(scripts.length, 1, `${p}: expected one counter script, got ${scripts.length}`);
    const s = scripts[0];
    assert.match(s.src, /^https:\/\/gc\.zgo\.at\/count\.v\d+\.js$/,
      'must be a versioned count.js, since only those have a fixed SRI hash');
    assert.match(s.integrity, /^sha384-[A-Za-z0-9+/]{64}$/, 'SRI hash missing or malformed');
    assert.strictEqual(s.crossOrigin, 'anonymous', 'SRI on a cross-origin script needs crossorigin=anonymous');
    assert.strictEqual(s.attrs['data-goatcounter'], ENDPOINT);
    assert.strictEqual(s.async, true);
  });
});

test('sends nothing from staging, localhost or a fork', () => {
  [
    ['nightrome.github.io', '/av-atlas-staging/index.html'],
    ['nightrome.github.io', '/some-other-repo/index.html'],
    ['localhost', '/index.html'],
    ['127.0.0.1', '/index.html'],
    ['someone-else.github.io', '/av-atlas/index.html'],
  ].forEach(([host, p]) => {
    const { scripts, sandbox } = loadNav(host, p);
    assert.strictEqual(scripts.length, 0, `${host}${p} must not load the counter`);
    assert.strictEqual(sandbox.goatcounter, undefined, `${host}${p} must not set window.goatcounter`);
  });
});

test("counted path keeps a detail page's identity and drops filters and the cache-buster", () => {
  const cases = [
    ['/av-atlas/author.html', '?name=Jane%20Doe&v=abc123&sort=citations', '/av-atlas/author.html?name=Jane+Doe'],
    ['/av-atlas/paper.html', '?v=abc&title=Some%20Paper', '/av-atlas/paper.html?title=Some+Paper'],
    ['/av-atlas/index.html', '?q=lidar&venue=CVPR&v=abc', '/av-atlas/index.html'],
    ['/av-atlas/', '', '/av-atlas/'],
  ];
  cases.forEach(([p, search, want]) => {
    const { sandbox } = loadNav('nightrome.github.io', p, search);
    assert.strictEqual(typeof sandbox.goatcounter.path, 'function');
    assert.strictEqual(sandbox.goatcounter.path(), want, `${p}${search}`);
  });
});

test("every page's CSP allows the counter script and endpoint, and nothing from Google", () => {
  const { scripts } = loadNav('nightrome.github.io', '/av-atlas/index.html');
  const scriptOrigin = new URL(scripts[0].src).origin;
  pages.forEach(page => {
    const csp = readCsp(fs.readFileSync(path.join(SITE, page), 'utf8'));
    assert.ok(csp, `${page}: no Content-Security-Policy meta tag`);
    assert.ok((csp['script-src'] || []).includes(scriptOrigin),
      `${page}: script-src must allow ${scriptOrigin}`);
    assert.ok((csp['connect-src'] || []).includes(ENDPOINT),
      `${page}: connect-src must allow ${ENDPOINT}`);
    Object.entries(csp).forEach(([name, sources]) => {
      sources.forEach(src => assert.ok(!/google/i.test(src), `${page}: ${name} still allows ${src}`));
    });
  });
});

test('no page or shared script loads Google Analytics', () => {
  const files = fs.readdirSync(SITE).filter(f => /\.(html|js)$/.test(f));
  files.forEach(f => {
    const text = fs.readFileSync(path.join(SITE, f), 'utf8');
    const hit = /googletagmanager|google-analytics|analytics\.google|\bgtag\b|\bdataLayer\b/i.exec(text);
    assert.ok(!hit, `${f} mentions "${hit && hit[0]}"`);
  });
});

// The version is shown only on the About page, never in the nav bar.
test('the nav bar carries no version', () => {
  assert.ok(!/__AV_ATLAS_VERSION__|__AV_ATLAS_DATA_DATE__|site-version|AV_ATLAS_VERSION/.test(NAV_JS),
    'nav.js still has version code');
  const { nav, sandbox } = loadNav('nightrome.github.io', '/av-atlas/about.html');
  assert.ok(!nav.children.some(el => /Version \d/.test(el.textContent || '')), 'nav bar shows a version');
  assert.strictEqual(sandbox.AV_ATLAS_VERSION, undefined);
});

test('siteVersionText formats the date by hand and ignores bad input', () => {
  const { sandbox } = loadNav('localhost', '/index.html');
  const f = sandbox.siteVersionText;
  assert.strictEqual(f('0.1.2', '2026-01-05'), 'Version 0.1.2 \u00b7 data as of 5 Jan 2026');
  assert.strictEqual(f('0.1.2', 'soon'), 'Version 0.1.2');
  assert.strictEqual(f('0.1.2', undefined), 'Version 0.1.2');
  assert.strictEqual(f(undefined, '2026-01-05'), '');
  assert.strictEqual(f('__AV_ATLAS_VERSION__', '2026-01-05'), '');
});

test('no page has a separate "Data last updated" or "Site version" line any more', () => {
  fs.readdirSync(SITE).filter(f => /\.(html|js)$/.test(f)).forEach(f => {
    const text = fs.readFileSync(path.join(SITE, f), 'utf8');
    assert.ok(!/Data last updated|Site version v|dataUpdatedText/.test(text), `${f} still has the old line`);
  });
});

if (failures > 0) {
  console.log(`\n${failures} test(s) failed`);
  process.exit(1);
}
console.log('\nAll nav.js tests passed');
