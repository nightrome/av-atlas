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
       glance, not just a small arrow easy to miss (flagged in review: "not
       immediately obvious which column is sorted"). */
    table.sortable thead th.sort-active { color: var(--accent); background: var(--panel2); }
    table.sortable thead th[data-sortable]:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
  `;
  document.head.appendChild(style);

  function compareBy(col, dir) {
    return (a, b) => {
      let av = col.accessor(a);
      let bv = col.accessor(b);
      if (col.numeric) {
        av = av == null ? -Infinity : av;
        bv = bv == null ? -Infinity : bv;
        return (av - bv) * dir;
      }
      av = av == null ? '' : String(av);
      bv = bv == null ? '' : String(bv);
      return av.localeCompare(bv) * dir;
    };
  }

  // Sorts a FULL row array by the table's current click-to-sort state and
  // returns a new array (or `rows` itself when no header has been clicked, so
  // the page's default order stays untouched). Paginated pages call this on
  // the whole list BEFORE slicing out the current page, so a header click
  // orders every row of the table, not just the ones on screen. `columns`
  // is the same array the page passes to makeSortable.
  window.applyTableSort = function (table, rows, columns) {
    const state = table && table._sortableState;
    if (!state || state.idx == null) return rows;
    const col = (columns || state.columns)[state.idx];
    if (!col) return rows;
    return [...rows].sort(compareBy(col, state.dir));
  };

  // Usage: makeSortable(table, data, columns, renderBody[, opts])
  // Without opts the table is not paginated: `data` is the whole table and a
  // click re-sorts it and hands it to renderBody. With opts.onSort (a paginated
  // table), `data` is only the current page of an already-sorted full list; a
  // click just records the new sort column/direction and calls onSort(), which
  // re-renders the page (resetting to page 1), sorting the full list through
  // applyTableSort before slicing.
  window.makeSortable = function (table, data, columns, renderBody, opts) {
    // Every page's render() calls this again on the SAME <table> element on
    // every search keystroke, pagination click, topN change, etc. -- only
    // the <tbody> gets rebuilt each time, the <thead>/<th> nodes persist.
    // Re-running the setup below on an already-wired table appended a
    // second indicator span and a second click listener on top of the
    // first, a third on top of that, and so on -- each with its OWN
    // independent {idx, dir} state, and each click firing every stacked
    // listener at once. Concretely: a header's clear-all-other-indicators
    // step only ever finds the FIRST indicator span via querySelector, so
    // later ones never got cleared and stayed lit (reported: "the
    // triangle symbol occurs 6 times"); which listener's renderBody(sorted)
    // call won the race depended on registration order, so a click could
    // as easily appear to do nothing as sort correctly (reported:
    // "sorting Venues by paper is not possible"); and on a page where the
    // header never happened to end up with an active-looking survivor, no
    // triangle showed at all (reported on All countries). Guarding
    // re-entry and swapping the live data/columns/renderBody into the one
    // state object already on the table fixes all three at the root.
    if (table._sortableState) {
      const state = table._sortableState;
      state.data = data;
      state.columns = columns;
      state.renderBody = renderBody;
      state.onSort = opts && opts.onSort ? opts.onSort : null;
      // A paginated page already sorted the full list before slicing.
      if (state.onSort) return;
      if (state.idx != null) state.resort(); else renderBody(data);
      return;
    }

    table.classList.add('sortable');
    // renderCompareSelection() (filters.js) prepends its own <th class="compare-col">
    // to the header row on pages with row-comparison checkboxes. It carries no
    // data and isn't in the `columns` array the caller passes, so it must not
    // be counted here -- otherwise every real column's click handler is wired
    // to the NEXT column's accessor (reported: sorting Venues by Citations
    // actually sorted by Citations / paper).
    if (window.markTableHeaders) window.markTableHeaders(table);
    const ths = [...table.querySelectorAll('thead th')].filter(th => !th.classList.contains('compare-col'));
    const state = { idx: null, dir: 1, data, columns, renderBody, onSort: opts && opts.onSort ? opts.onSort : null };
    table._sortableState = state;

    state.resort = function () {
      const col = state.columns[state.idx];
      state.renderBody([...state.data].sort(compareBy(col, state.dir)));
    };

    ths.forEach((th, idx) => {
      const col = columns[idx];
      if (!col) return;
      th.dataset.sortable = 'true';
      // Keyboard operable: focusable, announced as a sortable column header.
      th.setAttribute('tabindex', '0');
      th.setAttribute('role', 'columnheader');
      th.setAttribute('aria-sort', 'none');
      const indicator = document.createElement('span');
      indicator.className = 'sort-indicator';
      // Only the column actually driving the current sort ever shows a
      // glyph (▲/▼, filled, accent-colored) -- a hollow glyph on every
      // sortable column at once (a prior version of this) reads as "all of
      // these are sorted simultaneously", which isn't a real state a table
      // can be in (reported: "the triangle can only be shown if the
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
      // the next column -- reported as "columns overlap").
      th.appendChild(indicator);

      const activate = () => {
        // Preferred order first click: numeric columns start high-to-low
        // (most citations/papers/etc. first is what people actually want),
        // text columns start A-Z. A second click on the same column flips it.
        const defaultDir = col.numeric ? -1 : 1;
        state.dir = state.idx === idx ? state.dir * -1 : defaultDir;
        state.idx = idx;
        ths.forEach(t => {
          t.classList.remove('sort-active');
          if (t.getAttribute && t.getAttribute('aria-sort') != null) t.setAttribute('aria-sort', 'none');
          const ind = t.querySelector('.sort-indicator');
          if (!ind) return;
          ind.classList.remove('active');
          ind.textContent = '';
        });
        th.classList.add('sort-active');
        indicator.classList.add('active');
        indicator.textContent = state.dir === 1 ? '▲' : '▼';
        th.setAttribute('aria-sort', state.dir === 1 ? 'ascending' : 'descending');
        if (state.onSort) state.onSort(); else state.resort();
      };
      th.addEventListener('click', activate);
      th.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') {
          e.preventDefault();
          activate();
        }
      });
    });
  };
})();
