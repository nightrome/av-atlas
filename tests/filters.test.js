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
  const code = fs.readFileSync(path.join(__dirname, '..', 'site', 'filters.js'), 'utf8');
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

test('the average divides by every paper with a citation number, a real 0 included', () => {
  const papers = [
    { authors: ['A'], citations: 100 },
    { authors: ['A'], citations: 0 }, // a real 0: counts
    { authors: ['A'], citations: null }, // no data at all: left out of both sides
  ];
  const [row] = aggregateByDimension(papers, p => p.authors, { minPapers: 0 });
  assert.strictEqual(row.papers, 3, 'paper count should include every paper');
  assert.strictEqual(row.avg_citations, 50, 'average must divide by both papers that have a number');
  assert.strictEqual(row.cited_papers, 1);
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
  assert.strictEqual(stats.all_papers[1].citations, 0, 'no in_corpus data is a real 0 (nothing in-corpus cites it), never an openalex fallback');
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

// latexToPlain -- real snippets taken from abstracts in the corpus.
const { latexToPlain } = loadFilters();
const NBSP = '\u00a0';

test('latexToPlain: the UniBEV abstract case, a percent sign inside maths', () => {
  assert.strictEqual(latexToPlain('achieves $52.5 \\%$ mAP on average'), 'achieves 52.5% mAP on average');
  assert.strictEqual(latexToPlain('($43.5 \\%$ mAP for BEVFusion)'), '(43.5% mAP for BEVFusion)');
});

test('latexToPlain: times, degrees, Greek letters and relations', () => {
  assert.strictEqual(latexToPlain('$256\\times 704$'), '256×704');
  assert.strictEqual(latexToPlain('$>180^{\\circ}$'), '>180°');
  assert.strictEqual(latexToPlain('$360^{\\circ}\\times(0^{\\circ}\\sim 93.5^{\\circ})$'), '360°×(0°∼93.5°)');
  assert.strictEqual(latexToPlain('$\\alpha=0.5$ and $r=0.74$'), 'α=0.5 and r=0.74');
  assert.strictEqual(latexToPlain('$\\approx 15\\%$'), '≈15%');
});

test('latexToPlain: styling commands keep their text and drop the spaced-out digits', () => {
  assert.strictEqual(latexToPlain('$\\textbf{40\\%}$'), '40%');
  assert.strictEqual(latexToPlain('$\\mathbf{1 5. 7 \\%}$'), '15.7%');
  assert.strictEqual(latexToPlain('$\\text{88}-\\text{96}\\%$'), '88-96%');
  assert.strictEqual(latexToPlain('The \\emph{proposed} method, \\textit{ModelNet40}'), 'The proposed method, ModelNet40');
});

test('latexToPlain: super and subscripts use Unicode where they exist, plain text where not', () => {
  assert.strictEqual(latexToPlain('$10^{-3}$'), '10⁻³');
  assert.strictEqual(latexToPlain('$\\mathbb{R}^3$'), 'ℝ³');
  assert.strictEqual(latexToPlain('$O(N)+O(m^{2})$'), 'O(N)+O(m²)');
  assert.strictEqual(latexToPlain('$H_{\\infty }$'), 'H_∞');
  assert.strictEqual(latexToPlain('${AP}_{3D}$'), 'AP_3D');
  assert.strictEqual(latexToPlain('$\\mathrm{TSR}_{0.5}$'), 'TSR_0.5');
});

test('latexToPlain: nested groups, fractions and spacing', () => {
  assert.strictEqual(latexToPlain('$\\sim {\\mathrm {300~\\text {m}\\text {W} }}$'), '∼300' + NBSP + 'mW');
  assert.strictEqual(latexToPlain('$\\frac{1}{2}$ and $\\sqrt{x}$'), '1/2 and √x');
});

test('latexToPlain: text-mode escapes and links outside maths', () => {
  assert.strictEqual(latexToPlain('gains 3\\% on R\\&D, code at \\url{https://github.com/a/b}'),
    'gains 3% on R&D, code at https://github.com/a/b');
  assert.strictEqual(latexToPlain('saves \\$5 per trip'), 'saves $5 per trip');
});

test('latexToPlain: money and unknown macros are left alone', () => {
  assert.strictEqual(latexToPlain('costs $5 million and $10 million'), 'costs $5 million and $10 million');
  assert.strictEqual(latexToPlain('We propose \\method{} for driving'), 'We propose \\method{} for driving');
  assert.strictEqual(latexToPlain('plain text, nothing to do'), 'plain text, nothing to do');
  assert.strictEqual(latexToPlain(''), '');
});

// computeEntityRanks(...).paper -- tied citation counts share a rank range.
test('paper ranks: papers with equal citations share a range, not distinct positions', () => {
  const { computeEntityRanks } = loadFilters();
  const papers = [
    { title: 'Top', citations: 50, venue: 'CVPR', year: 2020 },
    { title: 'Mid A', citations: 5, venue: 'CVPR', year: 2021 },
    { title: 'Mid B', citations: 5, venue: 'IV', year: 2021 },
    { title: 'Zero A', citations: 0, venue: 'IV', year: 2022 },
    { title: 'Zero B', citations: 0, venue: 'IV', year: 2022 },
    { title: 'Zero C', citations: 0, venue: 'CVPR', year: 2022 },
  ];
  const ranks = computeEntityRanks({ all_papers: papers });
  const top = ranks.paper('Top');
  assert.strictEqual(top[0].rank, 1);
  assert.strictEqual(top[0].rankTo, undefined, 'a unique count is a single rank');
  const zero = ranks.paper('Zero B')[0];
  assert.strictEqual(zero.rank, 4);
  assert.strictEqual(zero.rankTo, 6);
  assert.strictEqual(zero.total, 6);
  assert.ok(/3 papers have 0 citations/.test(zero.tip), zero.tip);
  const mid = ranks.paper('Mid A')[0];
  assert.deepStrictEqual([mid.rank, mid.rankTo], [2, 3]);
  // Within IV in 2022 the two zero-citation papers still tie, but only with each other.
  const inYear = ranks.paper('Zero A').find(b => b.label === 'in 2022');
  assert.deepStrictEqual([inYear.rank, inYear.rankTo, inYear.total], [1, 3, 3]);
});

// The finding this guards: the Researchers/Countries/Institutions/Venues
// lists divided by cited papers while the detail pages divided by all
// papers with one decimal, so Holger Caesar read 155 on one page and 118.7
// on the next. Both sides now go through the same helper.
test('listing and detail pages give the same citations per paper', () => {
  const { aggregateByDimension: agg, avgCitations } = loadFilters();
  const papers = [
    { authors: ['A', 'B'], countries: ['NL'], citations: 10 },
    { authors: ['A'], countries: ['NL'], citations: 0 },
    { authors: ['A'], countries: ['US'], citations: 3 },
    { authors: ['B'], countries: ['US'], citations: 0 },
  ];
  [['authors', p => p.authors], ['countries', p => p.countries]].forEach(([dim, accessor]) => {
    agg(papers, accessor, { minPapers: 0 }).forEach(row => {
      const own = papers.filter(p => accessor(p).includes(row.name));
      assert.strictEqual(row.avg_citations, avgCitations(own), `${dim} ${row.name}`);
    });
  });
  assert.strictEqual(avgCitations(papers.filter(p => p.authors.includes('A'))), 4, '13 / 3 rounds to 4');
  assert.strictEqual(avgCitations([]), null);

  // The same on the real data when it's there: every author with a
  // listing average, checked against the average a detail page computes.
  const statsPath = path.join(__dirname, '..', 'data', 'stats.json');
  if (fs.existsSync(statsPath)) {
    const stats = JSON.parse(fs.readFileSync(statsPath, 'utf8'));
    const all = stats.all_papers.map(p => Object.assign({}, p, {
      citations: ((p.citations_by_source || {}).in_corpus || {}).count || 0,
    }));
    const byAuthor = {};
    all.forEach(p => new Set(p.authors || []).forEach(a => { (byAuthor[a] = byAuthor[a] || []).push(p); }));
    const rows = agg(all, p => p.authors, { minPapers: 4 });
    // A listing leaves the average out (null) when too few papers are cited
    // to trust it, see minCitedForAvg; only a shown number has to match.
    const shown = rows.filter(row => row.avg_citations != null);
    assert.ok(shown.length > 100, 'expected real authors to compare');
    shown.forEach(row => assert.strictEqual(row.avg_citations, avgCitations(byAuthor[row.name]), row.name));
  }

  // The detail pages must use the helper, and show a whole number.
  ['author', 'country', 'institution', 'venue', 'compare'].forEach(page => {
    const html = fs.readFileSync(path.join(__dirname, '..', 'site', `${page}.html`), 'utf8');
    assert.ok(/avgCitations\(papers\)/.test(html), `${page}.html should call avgCitations(papers)`);
    assert.ok(!/decimals:\s*1/.test(html), `${page}.html should not show one decimal place`);
    assert.ok(!/citedPapers/.test(html), `${page}.html still has its own cited-papers average`);
  });
});

test('detailCanonicalUrl keeps only the identity parameter, escaped like the sitemap', () => {
  const { detailCanonicalUrl } = loadFilters();
  const loc = (pathname, search) => ({ origin: 'https://nightrome.github.io', pathname, search });
  assert.strictEqual(
    detailCanonicalUrl(loc('/av-atlas/author.html', '?name=Jane+Doe&sort=year&v=123&papers_page=2')),
    'https://nightrome.github.io/av-atlas/author.html?name=Jane%20Doe');
  // Python's urllib.parse.quote(v, safe='') output for the same title,
  // which is what write_sitemap puts in sitemap.xml.
  assert.strictEqual(
    detailCanonicalUrl(loc('/av-atlas/paper.html', `?title=${encodeURIComponent("Lift, Splat, Shoot (LSS): it's *fast*!")}`)),
    'https://nightrome.github.io/av-atlas/paper.html?title=Lift%2C%20Splat%2C%20Shoot%20%28LSS%29%3A%20it%27s%20%2Afast%2A%21');
  assert.strictEqual(
    detailCanonicalUrl(loc('/av-atlas/compare.html', '?names=A%7CB&type=author&metric=avg')),
    'https://nightrome.github.io/av-atlas/compare.html?type=author&names=A%7CB');
  assert.strictEqual(detailCanonicalUrl(loc('/av-atlas/author.html', '')),
    'https://nightrome.github.io/av-atlas/author.html');
  // Staging gets its own origin and path, so deploy.py has nothing to rewrite.
  assert.strictEqual(detailCanonicalUrl(loc('/av-atlas-staging/venue.html', '?name=CVPR')),
    'https://nightrome.github.io/av-atlas-staging/venue.html?name=CVPR');
});

test('setDetailPageMeta leaves exactly one canonical and one og:url, both the identity URL', () => {
  const sandbox = loadFilters('?name=Jane%20Doe&sort=year');
  const head = {
    children: [],
    appendChild(el) { this.children.push(el); return el; },
    querySelector(sel) {
      const m = /^(\w+)\[([\w:]+)="([^"]+)"\]$/.exec(sel);
      return this.children.find(el => el.tagName === m[1] && el.attrs[m[2]] === m[3]) || null;
    },
  };
  sandbox.document.head = head;
  sandbox.document.createElement = tag => ({ tagName: tag, attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } });
  sandbox.location.origin = 'https://nightrome.github.io';
  sandbox.location.pathname = '/av-atlas/author.html';
  sandbox.setDetailPageMeta('Jane Doe', 'desc');
  sandbox.setDetailPageMeta('Jane Doe', 'refined desc'); // pages call it twice
  const canon = head.children.filter(el => el.tagName === 'link' && el.attrs.rel === 'canonical');
  const ogUrl = head.children.filter(el => el.tagName === 'meta' && el.attrs.property === 'og:url');
  assert.strictEqual(canon.length, 1);
  assert.strictEqual(ogUrl.length, 1);
  assert.strictEqual(canon[0].attrs.href, 'https://nightrome.github.io/av-atlas/author.html?name=Jane%20Doe');
  assert.strictEqual(ogUrl[0].attrs.content, canon[0].attrs.href);
});

test('detail templates ship no static canonical or og:url, listing pages keep theirs', () => {
  const site = path.join(__dirname, '..', 'site');
  const detail = ['author', 'paper', 'institution', 'venue', 'country', 'compare'];
  fs.readdirSync(site).filter(f => f.endsWith('.html')).forEach(f => {
    const html = fs.readFileSync(path.join(site, f), 'utf8');
    const hasCanonical = /<link rel="canonical"/.test(html);
    const hasOgUrl = /<meta property="og:url"/.test(html);
    if (detail.includes(f.replace('.html', ''))) {
      assert.ok(!hasCanonical && !hasOgUrl, `${f} must leave canonical/og:url to setDetailPageMeta`);
      assert.ok(/setDetailPageMeta\(/.test(html), `${f} must call setDetailPageMeta`);
    } else {
      assert.ok(hasCanonical && hasOgUrl, `${f} should keep its static canonical and og:url`);
    }
  });
});

test('dataUpdatedText shows content_updated as a plain date, never another field', () => {
  const { dataUpdatedText } = loadFilters();
  assert.strictEqual(dataUpdatedText({ content_updated: '2026-09-24' }), 'Data last updated 24 Sep 2026');
  assert.strictEqual(dataUpdatedText({ content_updated: '2026-01-05' }), 'Data last updated 5 Jan 2026');
  assert.strictEqual(dataUpdatedText({ generated_at: '2026-09-24' }), '');
  assert.strictEqual(dataUpdatedText({ content_updated: 'soon' }), '');
  assert.strictEqual(dataUpdatedText(null), '');
});

if (failures > 0) {
  console.log(`\n${failures} test(s) failed`);
  process.exit(1);
}
console.log('\nAll filters.js tests passed');
