#!/usr/bin/env node
// Fast, no-browser stand-in for "click through every page and check for
// errors" -- runs each real page's actual inline <script> content (the same
// bytes that ship to gh-pages) against the real data/stats.json inside a
// minimal DOM stub (dom_stub.js), and fails if any page throws. Catches the
// class of bug this app has hit repeatedly (a stat grid re-appending itself,
// a bar chart's height floor crushing values, an undefined helper) without
// needing a real browser or a network call, so it can run in well under the
// 1-minute budget this is meant to stay inside.
//
// Deliberately NOT a substitute for an actual visual check in the browser --
// it proves the script runs without throwing against real data, not that the
// result looks right. See CLAUDE.md's verification workflow for that, and
// ui_regression_test.js for structural checks (control placement, no full
// page reload on Prev/Next, ...) that this test doesn't cover.
//
// Usage: node av-atlas/tests/qa_smoke_test.js

'use strict';
const path = require('path');
const { runPage } = require('./dom_stub');

const TIME_BUDGET_MS = 60000;
const startedAt = Date.now();

const PAGES = [
  'index.html', 'authors.html', 'institutions.html', 'venues.html',
  'countries.html', 'categories.html', 'about.html',
  'paper.html', 'author.html', 'institution.html', 'venue.html', 'network.html',
  'insights.html', 'compare.html',
];

function checkPage(file) {
  return runPage(file).then(({ error, allElements, noInlineScript }) => {
    if (noInlineScript) return { file, ok: true, note: 'no inline script' };
    if (error) return { file, ok: false, error };
    // A page's own top-level .catch(err => ...) writes "Could not load
    // stats.json: <err>" into the DOM instead of throwing -- so a real bug
    // inside the render pipeline wouldn't otherwise fail this test.
    const failed = allElements.find(el => (el._text || '').startsWith('Could not load stats.json'));
    if (failed) return { file, ok: false, error: new Error(failed._text) };
    return { file, ok: true };
  });
}

async function main() {
  const results = [];
  for (const file of PAGES) {
    if (Date.now() - startedAt > TIME_BUDGET_MS) {
      results.push({ file, ok: false, error: new Error('exceeded 1-minute QA budget, skipped remaining pages') });
      break;
    }
    try {
      results.push(await checkPage(file));
    } catch (e) {
      results.push({ file, ok: false, error: e });
    }
  }

  const elapsed = Date.now() - startedAt;
  let failed = false;
  results.forEach(r => {
    if (r.ok) {
      console.log(`  ok    ${r.file}${r.note ? ' (' + r.note + ')' : ''}`);
    } else {
      failed = true;
      console.log(`  FAIL  ${r.file}: ${r.error.message}`);
    }
  });
  console.log(`QA smoke test: ${results.length} pages in ${elapsed}ms`);
  if (failed) process.exit(1);
}

main();
