// Click-to-sort table headers, shared across every av-atlas page.
// Usage: makeSortable(tableEl, data, columns, renderBody)
//   columns[i]  -> { accessor: row => value, numeric: bool } or null (not sortable)
//   renderBody  -> (sortedData) => void, re-fills the <tbody>
(function () {
  const style = document.createElement('style');
  style.textContent = `
    table.sortable thead th[data-sortable] { cursor: pointer; user-select: none; white-space: nowrap; }
    table.sortable thead th[data-sortable]:hover { color: var(--text); }
    table.sortable thead th .sort-indicator { opacity: 0.35; font-size: 0.85em; margin-left: 3px; }
    table.sortable thead th .sort-indicator.active { opacity: 1; color: var(--accent); font-size: 1em; font-weight: 700; }
    /* The column actually driving the sort should be unmistakable at a
       glance, not just a small arrow easy to miss (user-flagged: "not
       immediately obvious which column is sorted"). */
    table.sortable thead th.sort-active { color: var(--accent); background: var(--panel2); }
  `;
  document.head.appendChild(style);

  window.makeSortable = function (table, data, columns, renderBody) {
    // Every page's render() calls this again on the SAME <table> element on
    // every search keystroke, pagination click, topN change, etc. -- only
    // the <tbody> gets rebuilt each time, the <thead>/<th> nodes persist.
    // Re-running the setup below on an already-wired table appended a
    // second indicator span and a second click listener on top of the
    // first, a third on top of that, and so on -- each with its OWN
    // independent {idx, dir} state, and each click firing every stacked
    // listener at once. Concretely: a header's clear-all-other-indicators
    // step only ever finds the FIRST indicator span via querySelector, so
    // later ones never got cleared and stayed lit (user-reported: "the
    // triangle symbol occurs 6 times"); which listener's renderBody(sorted)
    // call won the race depended on registration order, so a click could
    // as easily appear to do nothing as sort correctly (user-reported:
    // "sorting Venues by paper is not possible"); and on a page where the
    // header never happened to end up with an active-looking survivor, no
    // triangle showed at all (user-reported on All countries). Guarding
    // re-entry and swapping the live data/columns/renderBody into the one
    // state object already on the table fixes all three at the root.
    if (table._sortableState) {
      const state = table._sortableState;
      state.data = data;
      state.columns = columns;
      state.renderBody = renderBody;
      if (state.idx != null) state.resort(); else renderBody(data);
      return;
    }

    table.classList.add('sortable');
    const ths = table.querySelectorAll('thead th');
    const state = { idx: null, dir: 1, data, columns, renderBody };
    table._sortableState = state;

    state.resort = function () {
      const col = state.columns[state.idx];
      const sorted = [...state.data].sort((a, b) => {
        let av = col.accessor(a);
        let bv = col.accessor(b);
        if (col.numeric) {
          av = av == null ? -Infinity : av;
          bv = bv == null ? -Infinity : bv;
          return (av - bv) * state.dir;
        }
        av = av == null ? '' : String(av);
        bv = bv == null ? '' : String(bv);
        return av.localeCompare(bv) * state.dir;
      });
      state.renderBody(sorted);
    };

    ths.forEach((th, idx) => {
      const col = columns[idx];
      if (!col) return;
      th.dataset.sortable = 'true';
      const indicator = document.createElement('span');
      indicator.className = 'sort-indicator';
      // Only the column actually driving the current sort ever shows a
      // glyph (▲/▼, filled, accent-colored) -- a hollow glyph on every
      // sortable column at once (a prior version of this) reads as "all of
      // these are sorted simultaneously", which isn't a real state a table
      // can be in (user-reported: "the triangle can only be shown if the
      // table is sorted according to this criteria and thus only on one
      // column at a time"). The cursor-on-hover + header hover color are
      // the only "this is sortable" affordance before the first click.
      indicator.textContent = '';
      // Append only -- a header can carry its own markup (e.g. network.html's
      // two-line <span class="th-main">/<span class="th-sub">), and this
      // used to do `th.textContent = th.textContent` before appending,
      // which silently collapsed any such structure into flat text (real
      // bug: network.html's "Papers"/"w/ other institutions" sub-label
      // lost its own line and its no-wrap CSS target, spilling text into
      // the next column -- user-reported as "columns overlap").
      th.appendChild(indicator);

      th.addEventListener('click', () => {
        // Preferred order first click: numeric columns start high-to-low
        // (most citations/papers/etc. first is what people actually want),
        // text columns start A-Z. A second click on the same column flips it.
        const defaultDir = col.numeric ? -1 : 1;
        state.dir = state.idx === idx ? state.dir * -1 : defaultDir;
        state.idx = idx;
        ths.forEach(t => {
          t.classList.remove('sort-active');
          const ind = t.querySelector('.sort-indicator');
          if (!ind) return;
          ind.classList.remove('active');
          ind.textContent = '';
        });
        th.classList.add('sort-active');
        indicator.classList.add('active');
        indicator.textContent = state.dir === 1 ? '▲' : '▼';
        state.resort();
      });
    });
  };
})();
