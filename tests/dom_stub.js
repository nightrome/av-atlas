// Shared minimal-DOM sandbox for running a real page's actual inline
// <script> content (the same bytes that ship to gh-pages) against real data,
// without a browser or network call. Extracted out of qa_smoke_test.js so
// ui_regression_test.js can reuse the exact same stub instead of drifting
// out of sync with a second hand-rolled copy -- a stub bug fixed in one but
// not the other is worse than no stub at all (it makes one suite lie).
//
// Deliberately NOT a substitute for an actual visual check in the browser --
// see CLAUDE.md's verification workflow for that.

'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const BASE = path.join(__dirname, '..');

function drainMicrotasks() {
  return new Promise(resolve => setImmediate(resolve));
}

function makeElement(tag) {
  const el = {
    tagName: (tag || 'div').toUpperCase(),
    children: [],
    attrs: {},
    style: {},
    dataset: {},
    // toggle() is part of the real DOMTokenList and IS used by page code
    // (filters.js's collapsible filter panel and its chart legends). It was
    // missing here purely because the paths using it had only ever run
    // inside click handlers, which these tests don't fire -- so the first
    // page-load call to it blew up renderFilterBar before it attached
    // anything, and every page came back empty with no clue why.
    classList: {
      add() {}, remove() {}, contains() { return false; },
      toggle(_name, force) { return force === undefined ? true : !!force; },
    },
    _listeners: {},
    get className() { return this.attrs.class || ''; },
    set className(v) { this.attrs.class = v; },
    get textContent() { return this._text || ''; },
    set textContent(v) { this._text = String(v); this.children = []; },
    get innerHTML() { return this._html || ''; },
    set innerHTML(v) { this._html = String(v); this.children = []; },
    setAttribute(k, v) { this.attrs[k] = v; },
    getAttribute(k) { return this.attrs[k]; },
    removeAttribute(k) { delete this.attrs[k]; },
    appendChild(child) { this.children.push(child); child.parentNode = this; return child; },
    append(...args) { args.forEach(a => this.appendChild(typeof a === 'string' ? makeText(a) : a)); },
    insertBefore(child) { this.children.unshift(child); child.parentNode = this; return child; },
    remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(c => c !== this); },
    replaceWith(other) {
      if (this.parentNode) {
        const idx = this.parentNode.children.indexOf(this);
        if (idx >= 0) this.parentNode.children[idx] = other;
        other.parentNode = this.parentNode;
      }
    },
    addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); },
    dispatch(type) { (this._listeners[type] || []).forEach(fn => fn()); },
    closest(sel) {
      let node = this;
      const tagSel = sel.replace('.', '').toUpperCase();
      while (node) {
        if (node.tagName === tagSel || (node.attrs.class || '').includes(sel.replace('.', ''))) return node;
        node = node.parentNode;
      }
      // Real pages parse markup, so e.g. a #results-body <tbody> genuinely
      // sits inside a real <table>; this stub only ever builds the tree the
      // scripts construct via createElement, so a getElementById() id that
      // was actually declared in the page's own static HTML (like most
      // <tbody id="...">s) comes back detached with no ancestors at all.
      // Synthesize (and cache) a matching ancestor rather than returning
      // null, so table.classList.add()/querySelectorAll() downstream still
      // work -- this stub cares about "does the script crash", not layout.
      this._closestCache = this._closestCache || {};
      if (!this._closestCache[sel]) this._closestCache[sel] = makeElement(tagSel.toLowerCase());
      return this._closestCache[sel];
    },
    querySelector(sel) { return queryAll(this, sel)[0] || null; },
    querySelectorAll(sel) { return queryAll(this, sel); },
    appendTo(parent) { parent.appendChild(this); return this; },
  };
  el.value = '';
  el.options = [];
  if (tag === 'select' || tag === 'option') {
    Object.defineProperty(el, 'selected', { value: false, writable: true });
  }
  return el;
}
function makeText(s) { return { nodeType: 3, textContent: s }; }
// Matches a single compound selector: a tag name, .class, #id, or [attr].
//
// This used to strip the leading . or # and compare the remainder against
// tagName, so '.export-row' looked for a <export-row> element and matched
// nothing -- every class- and id-based query silently returned []. Combined
// with the tag-only limitation below, whole code paths were "passing" tests
// that never ran them: the compare-selection column shipped a crash on
// countries.html (nameFor called on the totals row, which has no data entry)
// that the smoke test could not have caught, because its
// querySelectorAll('tbody tr') matched nothing.
function matchesSimple(el, part) {
  if (part === '*') return true;
  if (part.startsWith('.')) return (el.attrs.class || '').split(/\s+/).includes(part.slice(1));
  if (part.startsWith('#')) return el.attrs.id === part.slice(1);
  if (part.startsWith('[')) {
    const m = /^\[([^\]=]+)(?:=["']?([^\]"']*)["']?)?\]$/.exec(part);
    if (!m) return false;
    const have = el.attrs[m[1]];
    return m[2] === undefined ? have !== undefined : have === m[2];
  }
  // A tag with a class or id suffix ("td.num", "input#foo").
  const m = /^([a-zA-Z][\w-]*)(.*)$/.exec(part);
  if (!m) return false;
  if (el.tagName !== m[1].toUpperCase()) return false;
  return !m[2] || matchesSimple(el, m[2]);
}

// Supports comma-separated alternatives and descendant combinators
// ("tbody tr", "thead tr th"). Child (>), sibling and pseudo-class
// combinators are not supported and match nothing, deliberately: silently
// treating '>' as a descendant would make a test pass against structure it
// never actually checked.
function queryAll(root, sel) {
  const out = [];
  const seen = new Set();
  sel.split(',').map(s => s.trim()).filter(Boolean).forEach(one => {
    if (one.includes('>') || one.includes('+') || one.includes('~') || one.includes(':')) return;
    const parts = one.split(/\s+/);
    let current = [root];
    parts.forEach(part => {
      const next = [];
      current.forEach(node => {
        (function walk(n) {
          if (!n.children) return;
          n.children.forEach(c => {
            if (matchesSimple(c, part)) next.push(c);
            walk(c);
          });
        })(node);
      });
      current = next;
    });
    current.forEach(el => { if (!seen.has(el)) { seen.add(el); out.push(el); } });
  });
  return out;
}

// Runs `file`'s inline <script> (plus any <script src="...js"> siblings it
// declares, loaded first, same as document order in a real browser) inside a
// fresh sandbox. `opts.search` seeds location.search (default: none).
// `opts.statsRaw` overrides the stats.json payload fetch('stats.json')
// resolves with (default: the real data/data/stats.json on disk).
//
// Resolves to {error, allElements, idRegistry, sandbox} -- allElements is
// every element the page's script created via document.createElement (so a
// caller can search the whole tree, not just whatever got attached to
// <body>), sandbox exposes location/history/sessionStorage so a caller can
// inspect what the script actually did to them (e.g. "did a click handler
// try to navigate by writing location.href").
function runPage(file, opts) {
  opts = opts || {};
  const html = fs.readFileSync(path.join(BASE, file), 'utf-8');
  const srcScripts = [...html.matchAll(/<script src="([^"]+\.js)"><\/script>/g)]
    .map(m => m[1])
    .filter(src => fs.existsSync(path.join(BASE, src)))
    .map(src => fs.readFileSync(path.join(BASE, src), 'utf-8'));
  const inlineScripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
  const scripts = [...srcScripts, ...inlineScripts];
  if (!inlineScripts.length) return Promise.resolve({ file, error: null, allElements: [], idRegistry: {}, sandbox: null, noInlineScript: true });

  const statsRaw = opts.statsRaw || fs.readFileSync(path.join(BASE, 'data', 'stats.json'), 'utf-8');
  const idRegistry = {};
  const body = makeElement('body');

  const allElements = [];
  const documentStub = {
    head: makeElement('head'),
    body,
    createElement(tag) {
      const el = makeElement(tag);
      Object.defineProperty(el, 'id', {
        get() { return el.attrs.id || ''; },
        set(v) { el.attrs.id = v; idRegistry[v] = el; },
      });
      allElements.push(el);
      return el;
    },
    createElementNS(ns, tag) { return this.createElement(tag); },
    createTextNode(text) { return makeText(text); },
    querySelector(sel) {
      if (sel.startsWith('#')) return this.getElementById(sel.slice(1));
      return queryAll(body, sel)[0] || null;
    },
    querySelectorAll(sel) { return queryAll(body, sel); },
    readyState: 'complete',
    // Real pages have a real HTML element for every id their scripts look
    // up; this stub only ever creates elements the scripts themselves ask
    // for via createElement, so an id declared in the page's own static
    // <div id="..."> markup (not built by JS) would otherwise come back
    // null and crash on the first .textContent assignment. Auto-vivify on
    // miss -- same effect as the real element being there, just empty.
    getElementById(id) {
      if (!idRegistry[id]) idRegistry[id] = documentStub.createElement('div');
      return idRegistry[id];
    },
    documentElement: makeElement('html'),
    addEventListener() {},
  };
  idRegistry['topnav'] = documentStub.createElement('nav');
  body.appendChild(idRegistry['topnav']);
  idRegistry['filter-bar-container'] = documentStub.createElement('div');
  idRegistry['limit-container'] = documentStub.createElement('div');

  // history/location are plain recording stubs, not just no-ops -- a caller
  // (ui_regression_test.js) needs to tell "this control navigated the whole
  // page" (location.href assignment) apart from "this control updated the
  // URL in place and re-rendered" (history.replaceState/pushState call).
  const historyCalls = [];
  const locationState = { search: opts.search || '', pathname: '/' + file, hrefAssignments: [] };
  const locationProxy = {
    get search() { return locationState.search; },
    get pathname() { return locationState.pathname; },
    get href() { return 'http://localhost' + locationState.pathname + locationState.search; },
    set href(v) { locationState.hrefAssignments.push(v); },
    reload() {},
  };

  let capturedError = null;
  const sandbox = {
    console,
    document: documentStub,
    location: locationProxy,
    history: {
      replaceState(state, title, url) { historyCalls.push({ type: 'replaceState', url }); },
      pushState(state, title, url) { historyCalls.push({ type: 'pushState', url }); },
    },
    localStorage: { getItem: () => null, setItem() {} },
    sessionStorage: { getItem: () => null, setItem() {} },
    URLSearchParams,
    URL,
    fetch(url) {
      if (String(url).includes('stats.json')) {
        return Promise.resolve({ json: () => Promise.resolve(JSON.parse(statsRaw)) });
      }
      return Promise.reject(new Error('unexpected fetch: ' + url));
    },
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    Node: function () {},
    setTimeout, clearTimeout, setImmediate,
    addEventListener() {},
    removeEventListener() {},
    scrollTo() {},
    scrollY: 0,
    matchMedia: () => ({ matches: false }),
    navigator: { clipboard: null },
  };
  sandbox.window = sandbox;
  sandbox.self = sandbox;
  sandbox._historyCalls = historyCalls;
  sandbox._locationState = locationState;
  vm.createContext(sandbox);

  try {
    vm.runInContext(scripts.join('\n;\n'), sandbox, { filename: file, timeout: 10000 });
  } catch (e) {
    capturedError = e;
  }
  if (capturedError) return Promise.resolve({ file, error: capturedError, allElements, idRegistry, sandbox });

  return drainMicrotasks().then(() => drainMicrotasks()).then(() => (
    { file, error: null, allElements, idRegistry, sandbox }
  ));
}

module.exports = { makeElement, makeText, queryAll, runPage, BASE };
