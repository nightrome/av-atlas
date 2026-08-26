#!/usr/bin/env node
// Regression tests for author.html/institution.html's actual data-rendering
// path -- qa_smoke_test.js runs these two pages with no ?name= at all, which
// only exercises the "nothing found" empty-state branch and never touches
// the real render logic (co-authors table, citing-authors table,
// institution stats, ...). This picks a real author/institution out of the
// live data/stats.json and runs the page exactly the way a reader's browser
// would (?name=<real name>), so a future change to that logic gets caught
// here instead of only in a manual browser check.
//
// Usage: node av-atlas/tests/detail_page_test.js

'use strict';
const fs = require('fs');
const path = require('path');
const { runPage, BASE } = require('./dom_stub');

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

const stats = JSON.parse(fs.readFileSync(path.join(BASE, 'data', 'stats.json'), 'utf-8'));

const failures = [];
function check(label, cond) {
  if (!cond) failures.push(label);
}

async function testAuthorPage() {
  // The single most-published author -- guaranteed to have co-authors and
  // (very likely) citing authors, so every table on the page has real rows
  // to render, not just the empty-state branch.
  const authorCounts = {};
  (stats.all_papers || []).forEach(p => (p.authors || []).forEach(a => {
    authorCounts[a] = (authorCounts[a] || 0) + 1;
  }));
  const [name] = Object.entries(authorCounts).sort((a, b) => b[1] - a[1])[0] || [];
  if (!name) { check('author.html: no author found in data to test against', false); return; }

  const { error, idRegistry } = await runPage('author.html', { search: `?name=${encodeURIComponent(name)}` });
  check(`author.html (${name}): must not throw`, !error);
  if (error) return;

  const coauthorRows = (idRegistry['coauthors-body'].children || []).filter(c => c.tagName === 'TR');
  check('author.html: coauthors-body has at least one row', coauthorRows.length > 0);
  check('author.html: coauthors table defaults to at most 10 rows (Show dropdown default)',
    coauthorRows.length <= 10);

  check('author.html: citing-authors-body element exists', !!idRegistry['citing-authors-body']);
  const citingRows = (idRegistry['citing-authors-body'].children || []).filter(c => c.tagName === 'TR');
  check('author.html: citing-authors-body has at least one row (or the documented empty-state row)',
    citingRows.length > 0);

  const papersRows = (idRegistry['papers-body'].children || []).filter(c => c.tagName === 'TR');
  check('author.html: papers-body has at least one row', papersRows.length > 0);
}

async function testInstitutionPage() {
  const instAuthors = stats.institution_authors || {};
  const [name] = Object.entries(instAuthors).sort((a, b) => (b[1] || []).length - (a[1] || []).length)[0] || [];
  if (!name) { check('institution.html: no institution found in data to test against', false); return; }

  const { error } = await runPage('institution.html', { search: `?name=${encodeURIComponent(name)}` });
  check(`institution.html (${name}): must not throw`, !error);
}

// Regression guard for the user-reported bug (institution.html?name=Singapore
// %20%E2%80%A0 -- a bare country name plus a stray footnote marker,
// surviving as its own fake "institution"): no institution in the actual
// built stats.json may contain a footnote/reference marker character, or be
// exactly a country name. If this ever fails, a new instance of the same
// data-quality bug got past aggregate.py's normalize_institution/
// is_valid_institution -- see scripts/tests/test_aggregate.py for the
// Python-side unit coverage of that pipeline itself.
function testNoJunkInstitutionsInRealData() {
  // Anchored at the start/end for the marker characters, not "anywhere" --
  // a bare marker glued to the very front or back of a name is footnote
  // contamination (what aggregate.py's normalize_institution actually
  // strips), but the same characters can be part of a real name's own
  // ACRONYM mid-string ("A*STAR" -- Agency for Science, Technology and
  // Research), which must not be flagged. "correspond" and the Unicode
  // replacement character are unambiguous anywhere in the string.
  const JUNK_MARKER_RE = /^[†‡*§¶✉⋆∗]|[†‡*§¶✉⋆∗]$|correspond|�/i;
  const instAuthors = stats.institution_authors || {};
  const junk = Object.keys(instAuthors).filter(name => JUNK_MARKER_RE.test(name));
  check(`no institution names contain a footnote marker or "correspond" ` +
    `(found: ${junk.slice(0, 5).join(', ')})`, junk.length === 0);

  const countryNames = new Set((stats.top_countries || []).map(c => c.name).filter(Boolean));
  const bareCountryInstitutions = Object.keys(instAuthors).filter(name => countryNames.has(name));
  check(`no institution name is exactly a country name ` +
    `(found: ${bareCountryInstitutions.slice(0, 5).join(', ')})`, bareCountryInstitutions.length === 0);
}

async function main() {
  await testAuthorPage();
  await testInstitutionPage();
  testNoJunkInstitutionsInRealData();

  const elapsed = Date.now() - startedAt;
  if (failures.length) {
    failures.forEach(f => console.log(`  FAIL  ${f}`));
    console.log(`Detail page test: ${failures.length} failure(s) in ${elapsed}ms`);
    process.exit(1);
  }
  console.log(`Detail page test: all checks passed in ${elapsed}ms`);
}

if (Date.now() - startedAt > TIME_BUDGET_MS) {
  console.log('Detail page test: exceeded its time budget before starting, skipped.');
  process.exit(1);
}
main();
