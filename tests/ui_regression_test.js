#!/usr/bin/env node
// Structural UI regression tests -- catches the two classes of bug that keep
// slipping back in even though a human clicked "looks fine" in the browser
// at the time: (1) a control that LOOKS like it's part of the shared SEARCH
// filter bar but actually lives in its own standalone spot again, and (2) a
// control that silently starts doing a full page reload instead of an
// in-place re-render. Runs each real page's actual inline <script> (via
// dom_stub.js, shared with qa_smoke_test.js) against real data/stats.json,
// then inspects the DOM tree it built and fires real click/change events on
// the controls it created.
//
// Deliberately NOT a substitute for an actual visual check in the browser --
// see CLAUDE.md's verification workflow for that.
//
// Usage: node av-atlas/tests/ui_regression_test.js

'use strict';
const { runPage } = require('./dom_stub');

const TIME_BUDGET_MS = 60000;
const startedAt = Date.now();

function findAll(root, predicate, out) {
  out = out || [];
  if (!root || !root.children) return out;
  root.children.forEach(c => {
    if (predicate(c)) out.push(c);
    findAll(c, predicate, out);
  });
  return out;
}
function isDescendantOf(el, ancestor) {
  let node = el;
  while (node) {
    if (node === ancestor) return true;
    node = node.parentNode;
  }
  return false;
}
function allText(el) {
  if (el.nodeType === 3) return el.textContent || '';
  return (el._text || '') + (el.children || []).map(allText).join('');
}

// Every page below opted into the "Show top N" chart/graph control -- it
// must live inside the shared filter bar, not in a standalone container of
// its own (that was the actual bug behind "the show menu must be in the
// SEARCH menu" -- it silently reappeared once for the graph-size control on
// Network even after being fixed on the chart pages). `minMax` is the
// smallest ceiling acceptable for that page's control -- regression-guards
// against "not possible to change the number to more than 30".
const TOPN_PAGES = {
  'index.html': 100,
  'institutions.html': 100,
  'venues.html': 100,
  'countries.html': 100,
  'categories.html': 100,
  'network.html': 250,
};

// Pages with a "Show" AV-relevance dropdown -- every listing page except
// Insights, About (about.html), and Categories, by long-standing user
// instruction. Categories dropped it (user-requested): a non-AV-relevant
// paper isn't meaningfully categorized in the first place, so browsing the
// adjacent set by category never answered a real question the way it does
// on Papers/Authors/Institutions.
const RELEVANCE_PAGES = [
  'index.html', 'authors.html', 'institutions.html', 'venues.html',
  'countries.html', 'network.html',
];

// Pages with fixed-50-per-page Prev/Next pagination.
const PAGINATION_PAGES = ['index.html', 'authors.html', 'institutions.html', 'venues.html'];

function isTopNSelect(el) {
  // "Show top N" is a <select> whose every option is a bare number (10, 25,
  // 50, ...) -- distinguishes it from the bar's other selects (relevance,
  // category, venue, sort, min), none of which are all-numeric options.
  if (el.tagName !== 'SELECT' || !el.children.length) return false;
  return el.children.every(o => o.tagName === 'OPTION' && /^\d+$/.test(allText(o).trim()));
}

function checkTopNInFilterBar(file, minMax, result) {
  const errors = [];
  const filterBar = result.idRegistry['filter-bar-container'];
  if (!filterBar) { errors.push('no #filter-bar-container was ever looked up'); return errors; }

  // allElements includes every element ever created, whether or not it's
  // still attached anywhere (a control that got replaced by a later
  // container.innerHTML = '' still shows up here) -- walk from the real
  // roots we care about instead: everything actually reachable from
  // filter-bar-container, and separately, every topN-shaped select that
  // exists ANYWHERE, so a stray one living outside the filter bar is
  // caught rather than silently ignored.
  const allTopNSelects = result.allElements.filter(isTopNSelect);
  const inBar = allTopNSelects.filter(e => isDescendantOf(e, filterBar));
  const outsideBar = allTopNSelects.filter(e => !isDescendantOf(e, filterBar));

  if (!inBar.length) {
    errors.push('no "Show top N" dropdown found inside #filter-bar-container');
  } else {
    const maxOption = Math.max(...inBar[0].children.map(o => parseInt(allText(o), 10)));
    if (!(maxOption >= minMax)) {
      errors.push(`"Show top N" dropdown's highest option (${maxOption}) is below the required ceiling of ${minMax}`);
    }
  }
  if (outsideBar.length) {
    errors.push(`${outsideBar.length} topN-shaped select(s) exist OUTSIDE #filter-bar-container -- the old standalone "Show top N" control may have come back`);
  }
  return errors;
}

function checkRelevanceInFilterBar(file, result) {
  const errors = [];
  const filterBar = result.idRegistry['filter-bar-container'];
  if (!filterBar) { errors.push('no #filter-bar-container was ever looked up'); return errors; }
  const selects = findAll(filterBar, e => e.tagName === 'SELECT');
  const hasRelevanceSelect = selects.some(sel => {
    const optionTexts = (sel.children || []).map(allText);
    return optionTexts.some(t => t.includes('Not AV relevant'));
  });
  if (!hasRelevanceSelect) {
    errors.push('no "Show" (AV relevant / Not AV relevant) dropdown found inside #filter-bar-container');
  }
  return errors;
}

function checkPaginationDoesNotReload(file, result) {
  const errors = [];
  const container = result.idRegistry['pagination-container'];
  if (!container) { errors.push('no #pagination-container was ever looked up'); return errors; }
  const buttons = findAll(container, e => e.tagName === 'BUTTON');
  const next = buttons.find(b => allText(b).includes('Next'));
  if (!next) { errors.push('no Next button found in #pagination-container (result set may be under 50 items in test data)'); return errors; }
  if (next.attrs.disabled || next.disabled) { return errors; } // fewer than 2 pages of results -- nothing to click

  const hrefBefore = result.sandbox._locationState.hrefAssignments.length;
  const historyBefore = result.sandbox._historyCalls.length;
  next.dispatch('click');
  const hrefAfter = result.sandbox._locationState.hrefAssignments.length;
  const historyAfter = result.sandbox._historyCalls.length;

  if (hrefAfter > hrefBefore) {
    errors.push('clicking Next assigned location.href -- this causes a full page reload instead of an in-place re-render');
  }
  if (historyAfter <= historyBefore) {
    errors.push('clicking Next did not call history.replaceState/pushState -- the URL page param would not update');
  }
  return errors;
}

async function main() {
  const results = [];
  const allFiles = [...new Set([...Object.keys(TOPN_PAGES), ...RELEVANCE_PAGES, ...PAGINATION_PAGES])];

  for (const file of allFiles) {
    if (Date.now() - startedAt > TIME_BUDGET_MS) {
      results.push({ file, ok: false, errors: ['exceeded 1-minute UI regression budget, skipped'] });
      break;
    }
    let r;
    try {
      r = await runPage(file);
    } catch (e) {
      results.push({ file, ok: false, errors: ['threw while loading: ' + e.message] });
      continue;
    }
    if (r.error) {
      results.push({ file, ok: false, errors: ['page threw while rendering: ' + r.error.message] });
      continue;
    }

    const errors = [];
    if (TOPN_PAGES[file]) errors.push(...checkTopNInFilterBar(file, TOPN_PAGES[file], r));
    if (RELEVANCE_PAGES.includes(file)) errors.push(...checkRelevanceInFilterBar(file, r));
    if (PAGINATION_PAGES.includes(file)) errors.push(...checkPaginationDoesNotReload(file, r));

    results.push({ file, ok: errors.length === 0, errors });
  }

  const elapsed = Date.now() - startedAt;
  let failed = false;
  results.forEach(r => {
    if (r.ok) {
      console.log(`  ok    ${r.file}`);
    } else {
      failed = true;
      console.log(`  FAIL  ${r.file}:`);
      r.errors.forEach(e => console.log(`          - ${e}`));
    }
  });
  console.log(`UI regression test: ${results.length} pages in ${elapsed}ms`);
  if (failed) process.exit(1);
}

main();
