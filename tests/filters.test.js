#!/usr/bin/env node
// Regression tests for filters.js's pure data logic (aggregateByDimension,
// filterPapers) -- the part of the file with no DOM dependency, run directly
// under plain Node with no framework or npm install. renderFilterBar /
// renderLimitControl are DOM-heavy UI and are exercised by hand in the
// browser instead (see the verification workflow in CLAUDE.md / session
// notes), not here.
//
// Usage: node av-atlas/tests/filters.test.js

'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

// filters.js expects a browser environment (it injects a <style> tag and
// reads location.search) even though the functions under test here don't
// touch either -- stub just enough to let the top-level IIFE run.
function loadFilters(searchString) {
  const store = {};
  const sandbox = {
    document: {
      createElement: () => ({
        appendChild: () => {}, style: {}, setAttribute: () => {}, addEventListener: () => {},
        classList: { add: () => {}, remove: () => {}, toggle: () => {}, contains: () => false },
      }),
      createTextNode: () => ({}),
      head: { appendChild: () => {} },
      body: { appendChild: () => {} },
      // No <div id="filter-bar-container"> in this stub -- filters.js's
      // top-level loading-spinner IIFE checks for null and no-ops, same as
      // it would on a real page that doesn't have that element either.
      getElementById: () => null,
      querySelectorAll: () => [],
      readyState: 'complete',
      addEventListener: () => {},
    },
    location: { search: searchString || '' },
    localStorage: {
      getItem: k => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
    },
    addEventListener: () => {},
    URLSearchParams,
    console,
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  const code = fs.readFileSync(path.join(__dirname, '..', 'filters.js'), 'utf8');
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

const { aggregateByDimension, filterPapers } = loadFilters();

test('excludes uncited papers from the average denominator, not just the numerator', () => {
  const papers = [
    { authors: ['A'], citations: 100 },
    { authors: ['A'], citations: null }, // no data, not a real 0
  ];
  const [row] = aggregateByDimension(papers, p => p.authors, { minPapers: 0 });
  assert.strictEqual(row.papers, 2, 'paper count should include the uncited paper');
  assert.strictEqual(row.avg_citations, 100, 'average must only divide by the 1 cited paper, not both');
});

test('avg_citations is null (not 0) when nothing in the group has citation data', () => {
  const papers = [{ authors: ['A'], citations: null }];
  const [row] = aggregateByDimension(papers, p => p.authors, { minPapers: 0 });
  assert.strictEqual(row.avg_citations, null);
  assert.strictEqual(row.papers, 1);
});

test('ranks by citations', () => {
  const papers = [
    { authors: ['LowerCited'], citations: 160 },
    { authors: ['HigherCited'], citations: 300 },
  ];
  const rows = aggregateByDimension(papers, p => p.authors, { minPapers: 0 });
  const byName = Object.fromEntries(rows.map(r => [r.name, r]));
  assert.strictEqual(byName.HigherCited.citations, 300);
  assert.strictEqual(byName.LowerCited.citations, 160);
});

test('minPapers filters out entries at or below the threshold', () => {
  const papers = [
    { authors: ['Solo'], citations: 10 },
    { authors: ['Duo'], citations: 5 },
    { authors: ['Duo'], citations: 15 },
  ];
  const rows = aggregateByDimension(papers, p => p.authors, { minPapers: 1 });
  const names = rows.map(r => r.name);
  assert.ok(!names.includes('Solo'), 'a single-paper entry must be excluded when minPapers=1');
  assert.ok(names.includes('Duo'));
});

test('minCitedPapers excludes an average built from too few real data points', () => {
  // An author with several papers but only one carrying a citation count
  // must not out-rank someone with genuinely well-supported stats -- caught
  // in practice: a researcher with 4 papers, 1 cited (490), topped the
  // leaderboard purely on that single paper's count.
  const papers = [
    { authors: ['ThinData'], citations: 490 },
    { authors: ['ThinData'], citations: null },
    { authors: ['ThinData'], citations: null },
    { authors: ['ThinData'], citations: null },
    { authors: ['WellSupported'], citations: 10 },
    { authors: ['WellSupported'], citations: 20 },
    { authors: ['WellSupported'], citations: 30 },
  ];
  const rows = aggregateByDimension(papers, p => p.authors, { minPapers: 0, minCitedPapers: 3 });
  const names = rows.map(r => r.name);
  assert.ok(!names.includes('ThinData'), 'only 1 cited paper must not qualify at minCitedPapers=3');
  assert.ok(names.includes('WellSupported'));
});

test('minCitedForAvg nulls the average but keeps the entry (unlike minCitedPapers)', () => {
  // Countries/Institutions/Venues show paper count and total citations even
  // when citation coverage is thin -- those numbers stay meaningful, so the
  // row must not disappear the way it does on Researchers (minCitedPapers).
  // Caught in practice: Singapore/France showed "0 avg citations/paper" off
  // a single cited paper, reading as a real zero rather than "not enough data".
  const papers = [
    { countries: ['Singapore'], citations: 0 },
    { countries: ['Singapore'], citations: null },
    { countries: ['China'], citations: 5 },
    { countries: ['China'], citations: 7 },
    { countries: ['China'], citations: 9 },
  ];
  const rows = aggregateByDimension(papers, p => p.countries, { minPapers: 0, minCitedForAvg: 3 });
  const byName = Object.fromEntries(rows.map(r => [r.name, r]));
  assert.ok(byName.Singapore, 'the entry must still be present, just without a trustworthy average');
  assert.strictEqual(byName.Singapore.avg_citations, null);
  assert.strictEqual(byName.Singapore.papers, 2, 'paper count must stay accurate regardless of minCitedForAvg');
  assert.strictEqual(byName.China.avg_citations, 7, 'an entry with enough cited papers keeps its real average');
});

test('applyCitationSource: always reads citations_by_source.in_corpus, never openalex', () => {
  const { applyCitationSource } = loadFilters();
  const stats = { all_papers: [
    { citations_by_source: { openalex: { count: 40 }, in_corpus: { count: 2 } } },
    { citations_by_source: { openalex: { count: 40 } } },
  ] };
  applyCitationSource(stats);
  assert.strictEqual(stats.all_papers[0].citations, 2, 'in_corpus is used even though openalex has a higher count');
  assert.strictEqual(stats.all_papers[1].citations, null, 'no in_corpus data stays null even if openalex has one -- there is no fallback');
});

test('computeSelfCitationStats: otherCitations subtracts self_citations back out of citations, avoiding double-count', () => {
  const { computeSelfCitationStats } = loadFilters();
  const ownPapers = [
    { citations: 10, self_citations: 4 }, // citations already includes the 4 self-citations
    { citations: 5 }, // no self-citations at all -- must not throw on the missing field
  ];
  const { selfCitations, otherCitations } = computeSelfCitationStats(ownPapers);
  assert.strictEqual(selfCitations, 4);
  assert.strictEqual(otherCitations, 11, '(10 - 4) + 5, not 10 + 5 -- citations already contains the self-citations');
});

test('computeAuthorPaperStats: sums papers and citations per author across the corpus', () => {
  const { computeAuthorPaperStats } = loadFilters();
  const allPapers = [
    { authors: ['Alice', 'Bob'], citations: 10 },
    { authors: ['Alice'], citations: 5 },
    { authors: ['Alice'], citations: null }, // no citation data -- must not count as 0
  ];
  const result = computeAuthorPaperStats(allPapers);
  // Individual property checks, not deepStrictEqual on the whole object --
  // computeAuthorPaperStats runs inside loadFilters()'s vm sandbox, so its
  // return value's objects are a different realm's Object than a plain
  // {...} literal written here, and Node's assert.deepStrictEqual treats
  // that as unequal even with identical own properties.
  assert.strictEqual(result.Alice.papers, 3);
  assert.strictEqual(result.Alice.citations, 15);
  assert.strictEqual(result.Bob.papers, 1);
  assert.strictEqual(result.Bob.citations, 10);
  assert.strictEqual(result.Carol, undefined, 'an author with no papers must not appear at all');
});

test('a paper crediting the same dimension value twice only counts once', () => {
  // e.g. two authors from the same institution on one paper -- the paper
  // must not be double-counted for that institution.
  const papers = [{ institutions: ['MIT', 'MIT'], citations: 10 }];
  const [row] = aggregateByDimension(papers, p => p.institutions, { minPapers: 0 });
  assert.strictEqual(row.papers, 1);
  assert.strictEqual(row.citations, 10);
});

test('falsy dimension values (empty string, null) are skipped', () => {
  const papers = [
    { authors: ['', null, 'Real'], citations: 10 },
  ];
  const rows = aggregateByDimension(papers, p => p.authors, { minPapers: 0 });
  assert.strictEqual(rows.length, 1);
  assert.strictEqual(rows[0].name, 'Real');
});

test('filterPapers: category/venue/country/institution/author all narrow independently', () => {
  const papers = [
    { category: 'planning-control', venue: 'CVPR', countries: ['China'], institutions: ['MIT'], authors: ['A'] },
    { category: 'segmentation', venue: 'ICCV', countries: ['Germany'], institutions: ['ETH'], authors: ['B'] },
  ];
  assert.strictEqual(filterPapers(papers, { category: 'planning-control' }).length, 1);
  assert.strictEqual(filterPapers(papers, { venue: 'ICCV' }).length, 1);
  assert.strictEqual(filterPapers(papers, { country: 'China' }).length, 1);
  assert.strictEqual(filterPapers(papers, { institution: 'ETH' }).length, 1);
  assert.strictEqual(filterPapers(papers, { author: 'A' }).length, 1);
  assert.strictEqual(filterPapers(papers, {}).length, 2, 'no active filters must return everything');
});

test('withParam / getFilters round-trip through location.search', () => {
  const sandbox = loadFilters('?category=tracking&venue=CVPR');
  const filters = sandbox.getFilters();
  assert.strictEqual(filters.category, 'tracking');
  assert.strictEqual(filters.venue, 'CVPR');
  assert.strictEqual(filters.country, null);
  assert.strictEqual(sandbox.withParam('index.html', 'category', null), 'index.html?venue=CVPR');
});

if (failures > 0) {
  console.log(`\n${failures} test(s) failed`);
  process.exit(1);
}
console.log('\nAll filters.js tests passed');
