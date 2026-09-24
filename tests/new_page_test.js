#!/usr/bin/env node
// Checks new.html against a small hand-made new_papers.json: one panel per
// month, newest month first, a row per paper with the right links, and the
// empty-list message. Needs no built data, so it runs on a fresh clone and in
// CI, unlike qa_smoke_test.js.
//
// Usage: node tests/new_page_test.js

'use strict';
const assert = require('assert');
const { runPage } = require('./dom_stub');

const PAYLOAD = {
  generated_at: '2026-11-03',
  since: '2026-08-03',
  window_days: 92,
  papers: [
    { title: 'Occupancy Flow for Everyone', first_seen: '2026-11-02', venue: 'arXiv', year: 2026,
      authors: ['Ada Lovelace', 'Alan Turing', 'Grace Hopper', 'Edsger Dijkstra'], n_authors: 4,
      arxiv_url: 'https://arxiv.org/abs/2611.00001' },
    { title: 'A Second November Paper', first_seen: '2026-11-01', venue: 'CVPR', year: 2026,
      authors: ['Ada Lovelace'], n_authors: 1, arxiv_url: null },
    { title: 'Planning & <Prediction>', first_seen: '2026-10-05', venue: 'ICRA', year: 2026,
      authors: [], n_authors: 0, arxiv_url: 'javascript:alert(1)' },
  ],
};

function textOf(el) {
  return (el._text || '') + (el.children || []).map(textOf).join('');
}

async function main() {
  const { error, allElements, idRegistry } = await runPage('new.html', { newPapersRaw: JSON.stringify(PAYLOAD) });
  assert.ifError(error);

  const months = idRegistry.months;
  const panels = months.children.filter(c => c.className === 'panel');
  assert.strictEqual(panels.length, 2, 'one panel per month');
  assert.ok(textOf(panels[0].children[0]).startsWith('November 2026'), textOf(panels[0].children[0]));
  assert.ok(textOf(panels[0].children[0]).includes('2 papers'));
  assert.ok(textOf(panels[1].children[0]).startsWith('October 2026'));

  const links = allElements.filter(el => el.tagName === 'A');
  const hrefs = links.map(a => a.href);
  assert.ok(hrefs.includes('paper.html?title=' + encodeURIComponent('Planning & <Prediction>')));
  assert.ok(hrefs.includes('https://arxiv.org/abs/2611.00001'));
  assert.ok(!hrefs.some(h => String(h).startsWith('javascript:')), 'non-http arXiv link must not become an href');
  // Three authors shown, the fourth folded into "et al."
  assert.ok(hrefs.includes('author.html?name=' + encodeURIComponent('Grace Hopper')));
  assert.ok(!hrefs.includes('author.html?name=' + encodeURIComponent('Edsger Dijkstra')));

  const empty = await runPage('new.html', { newPapersRaw: JSON.stringify({ papers: [] }) });
  assert.ifError(empty.error);
  assert.ok(textOf(empty.idRegistry.months).includes('No papers have been added'));

  console.log('new_page_test: ok');
}

main().catch(e => { console.error('new_page_test FAILED:', e.message); process.exit(1); });
