// Shared filter bar + metric switcher, used by every av-atlas page so
// filtering/ranking works consistently everywhere, not just the Overview page.
(function () {
  const style = document.createElement('style');
  style.textContent = `
    /* A wide table (many columns, or long text cells) must scroll inside
       its own box, not push the whole page wider -- without this, a table
       on a narrow/mobile viewport drags the entire <body> into horizontal
       scroll instead (user-reported: "on my phone all tables on Papers
       page go out of the limits"). Wrap any <table> that might overflow in
       <div class="table-scroll">...</div>. */
    .table-scroll { overflow-x: auto; }

    /* A long paper title otherwise wraps unpredictably across a table's
       other columns, especially next to narrower numeric ones -- a fixed
       max-width + ellipsis keeps every row the same height, with the full
       title still available via the link's title="" attribute on hover. */
    .truncate-cell {
      display: inline-block; max-width: 420px; overflow: hidden;
      text-overflow: ellipsis; white-space: nowrap; vertical-align: bottom;
    }

    /* One consistent control panel per page: a top row of controls (search,
       then filter dropdowns, then sort), a middle row of active-filter chips
       + result count, and a bottom row for "Show N" -- same structure and
       same visual language (panel/border/radius) on every page, so a reader
       who's learned one page's controls already knows every other page's. */
    .controls-panel { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; margin-bottom: 16px; }
    .filter-bar { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 14px; }
    .filter-bar .field { display: flex; flex-direction: column; gap: 4px; }
    .filter-bar .field-label { font-size: 0.74em; color: var(--muted); text-transform: uppercase; letter-spacing: 0.03em; font-weight: 600; }
    .filter-bar select {
      background: var(--panel2); color: var(--text); border: 1px solid var(--border);
      border-radius: 6px; padding: 6px 8px; font-size: 0.88em; min-height: 32px;
      /* A long option (a venue/category name) otherwise sizes the closed
         select to fit it in full, which was routinely wide enough to push
         a later field (e.g. Venue) onto its own wrapped row -- capped so
         a run of several dropdowns has a real chance of sharing one row;
         the full text is still available in the open dropdown and via the
         title="" set below. */
      max-width: 220px; overflow: hidden; text-overflow: ellipsis;
    }
    .filter-bar .search-field {
      /* flex-grow: 0, not 1 -- the search box used to stretch to fill all
         leftover row width (past 500px on a typical viewport) purely
         because it came first, at the direct expense of later fields
         (Venue, Year, ...) having room to share that row instead of
         wrapping (user-reported: "the [Venue field] must be one row up").
         220-320px is already generous for a title/name search. */
      flex: 0 1 320px; min-width: 180px;
    }
    .filter-bar .checkbox-field {
      flex-direction: row; align-items: center; gap: 6px; cursor: pointer;
      font-size: 0.88em; padding-bottom: 6px; white-space: nowrap;
    }
    .filter-bar .checkbox-field input { margin: 0; cursor: pointer; }
    .filter-bar .search-field input {
      width: 100%; background: var(--panel2); color: var(--text); border: 1px solid var(--border);
      border-radius: 6px; padding: 6px 10px; font-size: 0.9em; box-sizing: border-box; min-height: 32px;
    }
    .filter-bar .search-field input:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
    .filter-bar-secondary { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border); }
    .pagination-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; font-size: 0.85em; color: var(--muted); margin: 8px 0; }
    .pagination-row button {
      background: var(--panel2); color: var(--text); border: 1px solid var(--border); border-radius: 6px;
      padding: 4px 10px; font-size: 0.95em; cursor: pointer;
    }
    .pagination-row button:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
    .pagination-row button:disabled { opacity: 0.4; cursor: default; }
    .pagination-row .page-size { display: inline-flex; align-items: center; gap: 6px; margin-right: 4px; }
    .pagination-row .page-size select {
      background: var(--panel2); color: var(--text); border: 1px solid var(--border);
      border-radius: 6px; padding: 3px 6px; font-size: 0.95em; cursor: pointer;
    }
    /* An info affordance in a panel/section header row -- a hover/tap tooltip
       describing what a table shows. Uses a data-tip attribute + ::after so
       it works without JS. A base layout for .panel-title-row so the icon
       lands top-right consistently even on pages that don't style the class
       themselves. */
    .panel-title-row { position: relative; }
    .info-tip {
      display: inline-flex; align-items: center; justify-content: center;
      width: 16px; height: 16px; border-radius: 50%; border: 1px solid var(--border);
      color: var(--muted); font-size: 11px; font-style: normal; font-weight: 700;
      cursor: help; flex-shrink: 0; user-select: none; margin-left: auto;
    }
    .info-tip:hover, .info-tip:focus { color: var(--accent); border-color: var(--accent); outline: none; }
    .info-tip::after {
      content: attr(data-tip); position: absolute; right: 0; top: calc(100% + 6px);
      width: max-content; max-width: min(320px, 80vw); white-space: normal;
      background: var(--panel); color: var(--text); border: 1px solid var(--border);
      border-radius: 8px; padding: 8px 10px; font-size: 0.82em; font-weight: 400; line-height: 1.4;
      box-shadow: 0 4px 14px rgba(0,0,0,0.25); z-index: 20;
      opacity: 0; visibility: hidden; transition: opacity 0.12s;
    }
    .info-tip:hover::after, .info-tip:focus::after { opacity: 1; visibility: visible; }
    .controls-meta-row { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--border); }
    .controls-meta-row:empty { display: none; }
    .chip-row { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
    .chip { display: inline-flex; align-items: center; gap: 6px; background: var(--accent); color: #fff; border-radius: 14px; padding: 4px 6px 4px 12px; font-size: 0.82em; }
    .chip button { background: rgba(255,255,255,0.25); border: none; color: #fff; border-radius: 50%; width: 18px; height: 18px; cursor: pointer; font-size: 0.85em; line-height: 1; }
    .chip button:hover { background: rgba(255,255,255,0.4); }
    .result-count { color: var(--muted); font-size: 0.85em; margin-left: auto; white-space: nowrap; }
    .clear-all-btn, .copy-link-btn {
      background: none; border: 1px solid var(--border); color: var(--muted); border-radius: 6px;
      padding: 4px 10px; font-size: 0.82em; cursor: pointer; white-space: nowrap;
    }
    .clear-all-btn:hover, .copy-link-btn:hover { border-color: var(--accent); color: var(--accent); }
    .legend-toggle-all { display: flex; gap: 10px; width: 100%; margin-bottom: 2px; }
    .legend-toggle-all button {
      background: none; border: none; color: var(--muted); font-size: 0.78em; cursor: pointer;
      padding: 0; text-decoration: underline;
    }
    .legend-toggle-all button:hover { color: var(--accent); }
    .empty-state {
      display: flex; flex-direction: column; align-items: flex-start; gap: 10px;
      padding: 24px 4px; color: var(--muted); font-size: 0.92em;
    }
    .empty-state p { margin: 0; }
    .av-loading-row {
      display: flex; align-items: center; gap: 10px; padding: 14px 16px; color: var(--muted); font-size: 0.9em;
      background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    }
    .av-spinner {
      width: 16px; height: 16px; border-radius: 50%; flex-shrink: 0;
      border: 2px solid var(--border); border-top-color: var(--accent);
      animation: av-spin 0.7s linear infinite;
    }
    @keyframes av-spin { to { transform: rotate(360deg); } }
    @media (prefers-reduced-motion: reduce) { .av-spinner { animation: none; } }
    .detail-back-link {
      display: inline-block; color: var(--muted); text-decoration: none; font-size: 0.88em;
      margin-bottom: 12px;
    }
    .detail-back-link:hover { color: var(--accent); text-decoration: underline; }
    .limit-row { display: flex; align-items: center; gap: 8px; font-size: 0.85em; color: var(--muted); }
    .limit-row select { background: var(--panel2); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 4px 6px; font-size: 0.95em; }
    .panel-title-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
    .export-csv-btn {
      background: var(--panel2); color: var(--text); border: 1px solid var(--border); border-radius: 6px;
      padding: 5px 10px; font-size: 0.82em; cursor: pointer;
    }
    .export-csv-btn:hover { border-color: var(--accent); color: var(--accent); }
    .export-row { display: inline-flex; gap: 6px; }
    .export-badge {
      display: inline-flex; align-items: center; gap: 4px;
      background: var(--panel2); color: var(--muted); border: 1px solid var(--border);
      border-radius: 6px; padding: 3px 8px; font-size: 0.74em; font-weight: 700;
      letter-spacing: 0.03em; cursor: pointer;
    }
    .export-badge:hover { border-color: var(--accent); color: var(--accent); }
    .export-badge::before { content: "\\2913"; font-weight: 400; font-size: 1.1em; line-height: 1; }
    .sum-row td { font-weight: 600; border-top: 2px solid var(--border); border-bottom: none; color: var(--text); }

    .av-toast {
      position: fixed; left: 50%; bottom: 24px; transform: translate(-50%, 12px);
      background: var(--text); color: var(--panel); padding: 9px 18px; border-radius: 8px;
      font-size: 0.86em; box-shadow: 0 6px 20px rgba(0,0,0,0.3); opacity: 0; pointer-events: none;
      transition: opacity 0.18s, transform 0.18s; z-index: 200; max-width: 90vw;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .av-toast.show { opacity: 1; transform: translate(-50%, 0); }

    /* Below ~480px, the filter bar's several fields wrapping mid-row reads
       as cramped/misaligned rather than a clean stack -- force every field
       (including the search box) to its own full-width row instead. */
    @media (max-width: 480px) {
      .filter-bar .field, .filter-bar .search-field { flex: 1 1 100%; }
    }

    .back-to-top-btn {
      position: fixed; right: 20px; bottom: 20px; z-index: 50;
      background: var(--panel); color: var(--text); border: 1px solid var(--border);
      border-radius: 50%; width: 42px; height: 42px; font-size: 1.1em; cursor: pointer;
      box-shadow: 0 4px 14px rgba(0,0,0,0.25); opacity: 0; pointer-events: none;
      transition: opacity 0.15s, transform 0.15s;
    }
    .back-to-top-btn.show { opacity: 1; pointer-events: auto; }
    .back-to-top-btn:hover { border-color: var(--accent); color: var(--accent); transform: translateY(-2px); }
  `;
  document.head.appendChild(style);

  // stats.json is tens of MB -- on a slow connection a first-time visitor
  // otherwise stares at a blank page for several seconds with zero
  // indication anything is happening. #filter-bar-container already sits in
  // every page's static markup before this <script> tag runs, so this fills
  // it with a spinner immediately; renderFilterBar's own `container.innerHTML
  // = ''` (once stats.json actually resolves) clears it automatically --
  // no separate "hide the spinner" call needed anywhere.
  (function showInitialLoadingSpinner() {
    const el = document.getElementById('filter-bar-container');
    if (!el) return;
    el.innerHTML = '';
    const row = document.createElement('div');
    row.className = 'av-loading-row';
    const spinner = document.createElement('span');
    spinner.className = 'av-spinner';
    row.appendChild(spinner);
    row.appendChild(document.createTextNode('Loading…'));
    el.appendChild(row);
  })();

  // Citations always come from the in-corpus count -- how many other papers
  // already in this corpus cite it -- never OpenAlex or any other external
  // provider. There is no user-facing choice here (there used to be a
  // picker; it's gone): a blend of an external global count and this
  // in-corpus one mixes two incomparable scales into one number with no way
  // to tell which source produced it, and OpenAlex's own counts can't be
  // redistributed the way a number computed entirely in-house can. See
  // Methodology.
  function inCorpusCitations(p) {
    // No in_corpus entry means the citation graph found nothing citing this
    // paper -- a real 0, not "unknown". aggregate.py's citation_count()
    // makes the same call server-side; kept in sync here for the client's
    // own re-derivation off citations_by_source.
    const c = ((p.citations_by_source || {})["in_corpus"] || {}).count;
    return c != null ? c : 0;
  }

  // Every page already reads p.citations directly off stats.all_papers
  // (Papers table, aggregateByDimension, the researcher/venues/timelines
  // pages' own client-side grouping -- see DECISIONS.md's "Filters compute
  // client-side" section). Rather than thread the citation field through
  // every one of those call sites, mutate it in place once, right after
  // fetching stats.json and before anything renders.
  window.applyCitationSource = function (stats) {
    const mutate = p => { p.citations = inCorpusCitations(p); };
    // top_papers is a server-side slice of the same underlying list as
    // all_papers, but after JSON.parse each paper that appears in both is
    // two independent JS objects, not shared references -- mutating one
    // array would silently leave the other showing stale numbers.
    (stats.all_papers || []).forEach(mutate);
    (stats.top_papers || []).forEach(mutate);
    return stats;
  };

  // Human-readable labels for the raw category slugs papers are tagged with
  // -- used to be duplicated (categories.html had its own private copy;
  // every other page just showed the raw slug, e.g. "llm-vlm-driving",
  // straight from the data). Centralized here so the Category dropdown and
  // every table's Category column show the same friendly name everywhere.
  window.CATEGORY_LABELS = {
    'control': 'Control',
    'llm-vlm-driving': 'LLM/VLM for Driving',
    'dataset-benchmark-paper': 'Datasets',
    'simulation-benchmarking': 'Simulation',
    'end-to-end-driving': 'End-to-End Driving & Planning',
    'world-models': 'World Models',
    'reinforcement-learning': 'Reinforcement Learning',
    'domain-adaptation': 'Domain Adaptation & Generalization',
    'adversarial-robustness-safety': 'Adversarial Robustness & Safety',
    'tracking': 'Tracking',
    'mapping-localization': 'Mapping & Localization',
    'segmentation': 'Segmentation',
    'occupancy': 'Occupancy Prediction',
    'v2x-cooperative': 'V2X / Cooperative Perception',
    'depth-3d-geometry': 'Depth & 3D Geometry',
    'novel-view-synthesis': 'Novel View Synthesis',
    'optical-flow': 'Optical & Scene Flow',
    'object-detection': 'Object Detection',
    'motion-prediction': 'Motion Prediction',
    'uncategorized': 'Uncategorized',
    'sensor-fusion': 'Sensor Fusion',
    'driver-behavior-hmi': 'Driver Behavior & Human-Machine Interaction',
    'general-cv-ml-method': 'General CV/ML Method',
  };
  // Matches if every word in the (already-lowercased) query appears
  // somewhere in text, in any order -- not just as one contiguous
  // substring. User-flagged: searching "Julian Kooij" found nothing for
  // "Julian Francisco Pieter Kooij" (a real corpus name, after two prior
  // name-spelling variants got merged into this canonical one) because a
  // plain text.includes(query) requires the words to be adjacent with
  // nothing in between. A multi-word name search should work the way a
  // reader actually types a name they half-remember, not require they
  // guess the exact on-file spelling.
  window.matchesSearchQuery = function (text, query) {
    if (!query) return true;
    const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const haystack = (text || '').toLowerCase();
    return words.every(w => haystack.includes(w));
  };

  window.categoryLabel = function (cat) {
    if (!cat) return cat;
    return CATEGORY_LABELS[cat] || cat.split('-').map(w => w[0].toUpperCase() + w.slice(1)).join(' ');
  };

  // Venue codes stored on papers are the short form used in tables. This
  // maps each to its full name for the venue detail-page heading only.
  // Entries whose stored code is already the full name (most journals) are
  // deliberately absent -- venueDisplayName() then shows the code as-is.
  window.VENUE_LONG_NAMES = {
    '3DV': 'International Conference on 3D Vision',
    'AAAI': 'AAAI Conference on Artificial Intelligence',
    'ACC': 'American Control Conference',
    'ACM MM': 'ACM International Conference on Multimedia',
    'CDC': 'IEEE Conference on Decision and Control',
    'CVPR': 'IEEE/CVF Conference on Computer Vision and Pattern Recognition',
    'CVPRW': 'CVPR Workshops',
    'CoRL': 'Conference on Robot Learning',
    'ECCV': 'European Conference on Computer Vision',
    'ECCVW': 'ECCV Workshops',
    'IAVVC': 'IEEE International Automated Vehicle Validation Conference',
    'ICASSP': 'IEEE International Conference on Acoustics, Speech and Signal Processing',
    'ICCV': 'IEEE/CVF International Conference on Computer Vision',
    'ICCVW': 'ICCV Workshops',
    'ICLR': 'International Conference on Learning Representations',
    'ICPR': 'International Conference on Pattern Recognition',
    'ICRA': 'IEEE International Conference on Robotics and Automation',
    'IJCAI': 'International Joint Conference on Artificial Intelligence',
    'IJCNN': 'International Joint Conference on Neural Networks',
    'IROS': 'IEEE/RSJ International Conference on Intelligent Robots and Systems',
    'ITSC': 'IEEE International Conference on Intelligent Transportation Systems',
    'IV': 'IEEE Intelligent Vehicles Symposium',
    'NeurIPS': 'Conference on Neural Information Processing Systems',
    'RA-L': 'IEEE Robotics and Automation Letters',
    'SMC': 'IEEE International Conference on Systems, Man, and Cybernetics',
    'T-CST': 'IEEE Transactions on Control Systems Technology',
    'T-CSVT': 'IEEE Transactions on Circuits and Systems for Video Technology',
    'T-IP': 'IEEE Transactions on Image Processing',
    'T-ITS': 'IEEE Transactions on Intelligent Transportation Systems',
    'T-IV': 'IEEE Transactions on Intelligent Vehicles',
    'T-MM': 'IEEE Transactions on Multimedia',
    'T-RO': 'IEEE Transactions on Robotics',
    'TNNLS': 'IEEE Transactions on Neural Networks and Learning Systems',
    'TPAMI': 'IEEE Transactions on Pattern Analysis and Machine Intelligence',
    'TVT': 'IEEE Transactions on Vehicular Technology',
    'WACV': 'IEEE/CVF Winter Conference on Applications of Computer Vision',
    'WACVW': 'WACV Workshops',
  };
  // A handful of venues are stored under their full name but have a short
  // code readers know them by; show "Full Name (CODE)" for those too.
  window.VENUE_SHORT_CODES = {
    'IEEE Transactions on Vehicular Technology': 'TVT',
  };

  // Heading text for the venue detail page: "Full Name (CODE)" when both are
  // known, otherwise whichever single form we have.
  window.venueDisplayName = function (venue) {
    if (!venue) return 'Unknown venue';
    var long = VENUE_LONG_NAMES[venue];
    if (long) return venue === long ? long : long + ' (' + venue + ')';
    var code = VENUE_SHORT_CODES[venue];
    if (code) return venue + ' (' + code + ')';
    return venue;
  };

  const FILTER_KEYS = ['category', 'venue', 'year', 'country', 'institution', 'author'];

  window.getFilters = function () {
    const p = new URLSearchParams(location.search);
    const out = {};
    FILTER_KEYS.forEach(k => { out[k] = p.get(k) || null; });
    return out;
  };

  // A pagination URL param is either "page" or "<something>_page" (a page
  // with more than one independently paged table -- e.g. author.html --
  // gives each its own key so they don't fight over a single "page").
  const PAGE_PARAM_RE = /(^|_)page$/;

  window.withParam = function (page, key, value) {
    const p = new URLSearchParams(location.search);
    if (value) p.set(key, value); else p.delete(key);
    // Changing any filter/sort/search resets pagination to page 1 -- without
    // this, a reader on page 3 of one filter combination who then narrows
    // the category would land on page 3 of the new, much shorter list,
    // which is silently either empty or the wrong slice. A change to one
    // table's own page key leaves the others alone.
    if (!PAGE_PARAM_RE.test(key)) {
      [...p.keys()].forEach(k => { if (PAGE_PARAM_RE.test(k)) p.delete(k); });
    }
    return page + (p.toString() ? '?' + p.toString() : '');
  };

  // Fetches stats.json (always) and stats_adjacent.json (only when a page
  // has actually switched away from the default "AV relevant" view, since
  // adjacent is a large separate file -- see the ADJACENT_OUT_FILE comment
  // in aggregate.py), then swaps stats.all_papers to whichever set the
  // relevance param asks for. Centralized here so every listing page's
  // Show dropdown behaves identically instead of each page re-implementing
  // its own fetch-and-swap (which is how the Papers page's version of this
  // first shipped, before the dropdown moved into the shared filter bar).
  window.fetchStatsWithRelevance = function (relevance) {
    const statsFetch = fetch('stats.json').then(r => r.json()).then(applyCitationSource);
    const adjacentFetch = relevance ? fetch('stats_adjacent.json').then(r => r.json()) : Promise.resolve(null);
    return Promise.all([statsFetch, adjacentFetch]).then(([stats, adjacent]) => {
      if (relevance === 'adjacent') {
        stats.all_papers = adjacent || [];
      } else if (relevance === 'both') {
        stats.all_papers = [...(stats.all_papers || []), ...(adjacent || [])];
      } else {
        return stats;
      }
      // renderResults()-style code on some pages reads stats.top_papers (a
      // server-precomputed, core-only top-50) instead of stats.all_papers
      // whenever no filter is active, as a size optimization -- recomputed
      // the same way aggregate.py builds it (already-citation-sorted top
      // 50) from whatever all_papers now actually is, so that shortcut
      // doesn't silently keep showing (only) core papers after the swap.
      stats.top_papers = [...stats.all_papers]
        .sort((a, b) => (b.citations != null) - (a.citations != null) || (b.citations || 0) - (a.citations || 0))
        .slice(0, 50);
      return stats;
    });
  };

  window.filterPapers = function (papers, filters) {
    let out = papers;
    if (filters.category) out = out.filter(p => p.category === filters.category);
    if (filters.venue) {
      // 'Other' is the synthetic bucket for every venue below the
      // big_venues threshold (see renderFilterBar's showVenue block) -- no
      // real paper's own venue field is ever literally "Other", so this
      // needs the big-venues set, not a plain equality check.
      if (filters.venue === 'Other' && filters.bigVenues) {
        out = out.filter(p => p.venue && !filters.bigVenues.has(p.venue));
      } else {
        out = out.filter(p => p.venue === filters.venue);
      }
    }
    if (filters.year) out = out.filter(p => String(p.year) === filters.year);
    if (filters.minCitations) out = out.filter(p => (p.citations || 0) >= filters.minCitations);
    if (filters.country) out = out.filter(p => (p.countries || []).includes(filters.country));
    if (filters.institution) out = out.filter(p => (p.institutions || []).includes(filters.institution));
    if (filters.author) out = out.filter(p => (p.authors || []).includes(filters.author));
    return out;
  };

  // Renders one consistent control panel per page, two rows (user-requested:
  // filters in the first row, sort/show in the second):
  //   Row 1 (.filter-bar): SEARCH, Show (AV relevance), Category, Venue, Year
  //     -- everything that narrows WHICH papers are in play.
  //   Row 2 (.filter-bar-secondary): Sort by, Min. (papers threshold) --
  //     everything that changes how the already-narrowed set is ranked or
  //     thresholded, not which papers are in it.
  // Then a third row of removable chips for whichever of the 5 filter
  // dimensions is active -- country/institution/author arrive via links from
  // other pages, not a dropdown here, but are still shown and clearable like
  // the rest -- plus the result count. Every page builds this same panel
  // from the same function so the controls always look and behave the same
  // way, page to page.
  window.renderFilterBar = function (container, stats, page, opts) {
    opts = opts || {};
    const filters = getFilters();
    container.innerHTML = '';
    const panel = document.createElement('div');
    panel.className = 'controls-panel';
    const bar = document.createElement('div');
    bar.className = 'filter-bar';
    // opts.singleRow: keep every control on one line (used on Categories,
    // where there are only a few) instead of the default two-row split.
    const bar2 = opts.singleRow ? bar : document.createElement('div');
    if (!opts.singleRow) bar2.className = 'filter-bar filter-bar-secondary';

    function field(labelText, el) {
      const wrap = document.createElement('div');
      wrap.className = 'field';
      const label = document.createElement('span');
      label.className = 'field-label';
      label.textContent = labelText;
      wrap.appendChild(label);
      wrap.appendChild(el);
      return wrap;
    }

    // opts.search is either one search-field config or an array of them (a
    // page like Papers wants two independent free-text boxes -- title and
    // institution -- side by side). Each needs its own `param` (and, when
    // there's more than one, its own `id`, since 'search-input' is the
    // shared default).
    const searchFields = Array.isArray(opts.search) ? opts.search : (opts.search ? [opts.search] : []);
    searchFields.forEach(searchOpts => {
      const wrap = document.createElement('div');
      wrap.className = 'field search-field';
      const label = document.createElement('span');
      label.className = 'field-label';
      label.textContent = searchOpts.label || 'Search';
      const input = document.createElement('input');
      input.type = 'search';
      input.autocomplete = 'off';
      input.placeholder = searchOpts.placeholder || 'Search…';
      input.id = searchOpts.id || 'search-input';
      const param = searchOpts.param || 'q';
      const q = new URLSearchParams(location.search).get(param);
      if (q) input.value = q;
      // The URL updates on every keystroke (cheap, and keeps "copy link"
      // accurate mid-typing), but the actual re-render (re-scanning the
      // full paper/author/institution list) is debounced -- on the largest
      // lists (40k+ authors) re-filtering on literally every keystroke was
      // visibly janky while typing a longer name.
      let debounceTimer = null;
      input.addEventListener('input', () => {
        const u = new URL(location.href);
        if (input.value) u.searchParams.set(param, input.value);
        else u.searchParams.delete(param);
        // A new query means a different (usually shorter) result set, so
        // reset every table's pagination to page 1 -- same rule withParam
        // applies to the dropdowns. Without this a reader on page 2 sees
        // "11 to 20 of N" of a fresh search instead of the first results.
        [...u.searchParams.keys()].forEach(k => { if (PAGE_PARAM_RE.test(k)) u.searchParams.delete(k); });
        history.replaceState(null, '', u.toString());
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => searchOpts.onInput(input.value), 200);
      });
      wrap.appendChild(label);
      wrap.appendChild(input);
      bar.appendChild(wrap);
    });

    // AV-relevance dropdown ("AV relevant" / "Not AV relevant" / "Both"),
    // the same control and behavior on every page that opts in -- previously
    // only existed as a one-off hand-rolled dropdown at the very top of the
    // Papers page, above the whole filter bar including SEARCH (user-
    // requested: move it down into the filter bar, below SEARCH, and reuse
    // it on every listing page for a consistent place/behavior). Adjacent
    // (not core-AV-relevant) papers are shipped as a separate stats_adjacent.json
    // (see aggregate.py's ADJACENT_OUT_FILE comment for the size reasoning);
    // fetchStatsWithRelevance below does the actual fetch-and-swap.
    // "Both" (re-added, user-requested) unions the two sets -- category/venue/
    // year counts and chart series computed client-side from the resulting
    // all_papers describe that union same as any other selection; the one
    // place that stays core-only regardless is the Category dropdown's own
    // per-option counts below (stats.category_breakdown is a server-side
    // precomputation over core papers only -- recomputing it for every
    // possible relevance selection wasn't worth it for a count next to an
    // option label, not a hard filter).
    if (opts.relevance) {
      const relValue = new URLSearchParams(location.search).get('relevance') || '';
      const sel = document.createElement('select');
      [['', 'AV papers'], ['adjacent', 'Non-AV papers'], ['both', 'Both']].forEach(([value, text]) => {
        const opt = document.createElement('option');
        opt.value = value;
        opt.textContent = text;
        if (value === relValue) opt.selected = true;
        sel.appendChild(opt);
      });
      sel.addEventListener('change', () => { location.href = withParam(page, 'relevance', sel.value); });
      bar.appendChild(field('Show', sel));
      filters.relevance = relValue;
    }

    if (opts.showCategory !== false) {
      const sel = document.createElement('select');
      sel.innerHTML = '<option value="">All categories</option>';
      (stats.category_breakdown || []).forEach(c => {
        const opt = document.createElement('option');
        opt.value = c.category;
        opt.textContent = `${categoryLabel(c.category)} (${c.papers})`;
        if (c.category === filters.category) opt.selected = true;
        sel.appendChild(opt);
      });
      sel.addEventListener('change', () => { location.href = withParam(page, 'category', sel.value); });
      bar.appendChild(field('Category', sel));
    }

    if (opts.showVenue !== false) {
      const sel = document.createElement('select');
      sel.innerHTML = '<option value="">All venues</option>';
      // Only venues aggregate.py already flagged as common enough to list
      // individually (corpus_stats.big_venues, >25 core AV-relevant papers)
      // get their own option -- everything else collapses into one "Other"
      // entry. Replaces reading every distinct venue straight off
      // corpus_stats.by_venue, which blew this dropdown from ~60 entries to
      // 3,000+ once backfill_citing_venues.py started filling in real
      // per-paper venues for ~68k citation-discovered papers (user-reported:
      // "the All Venues menu is messed up (too long)"). Counts shown are
      // still from the CURRENTLY ACTIVE paper set (stats.all_papers, which
      // already respects the core/adjacent "Show" toggle above) -- only
      // which venues QUALIFY for their own row is fixed by the core count.
      const bigVenues = new Set((stats.corpus_stats || {}).big_venues || []);
      const venueCounts = {};
      let otherCount = 0;
      (stats.all_papers || []).forEach(p => {
        if (!p.venue) return;
        if (bigVenues.has(p.venue)) venueCounts[p.venue] = (venueCounts[p.venue] || 0) + 1;
        else otherCount++;
      });
      const venueEntries = Object.entries(venueCounts);
      if (filters.venue && filters.venue !== 'Other' && !bigVenues.has(filters.venue)) {
        // Reached via a direct link to a below-threshold venue -- keep it
        // selected rather than silently reverting to "All venues".
        venueEntries.push([filters.venue, null]);
      }
      venueEntries.sort((a, b) => a[0].localeCompare(b[0]));
      venueEntries.forEach(([v, c]) => {
        const opt = document.createElement('option');
        opt.value = v;
        opt.textContent = c == null ? v : `${v} (${c})`;
        if (v === filters.venue) opt.selected = true;
        sel.appendChild(opt);
      });
      if (otherCount) {
        const opt = document.createElement('option');
        opt.value = 'Other';
        opt.textContent = `Other (${otherCount})`;
        if (filters.venue === 'Other') opt.selected = true;
        sel.appendChild(opt);
      }
      sel.addEventListener('change', () => { location.href = withParam(page, 'venue', sel.value); });
      bar.appendChild(field('Venue', sel));
      // filterPapers() needs this to know what "Other" means -- see there.
      filters.bigVenues = bigVenues;
    }

    if (opts.showYear) {
      const sel = document.createElement('select');
      sel.innerHTML = '<option value="">All years</option>';
      const years = Object.keys((stats.corpus_stats || {}).by_year || {}).sort().reverse();
      const currentYear = new URLSearchParams(location.search).get('year') || '';
      years.forEach(y => {
        const opt = document.createElement('option');
        opt.value = y;
        opt.textContent = y;
        if (y === currentYear) opt.selected = true;
        sel.appendChild(opt);
      });
      sel.addEventListener('change', () => { location.href = withParam(page, 'year', sel.value); });
      bar.appendChild(field('Year', sel));
    }

    // A WHICH-papers filter: keep only papers with at least this many
    // in-corpus citations. Navigates via ?mincites= like the other row-1
    // filters; filters.minCitations is the parsed integer (0 == off).
    if (opts.minCitations) {
      const cur = parseInt(new URLSearchParams(location.search).get('mincites'), 10) || 0;
      const sel = document.createElement('select');
      [[0, 'Any'], [1, '1+'], [5, '5+'], [10, '10+'], [25, '25+'], [50, '50+'], [100, '100+']].forEach(([v, t]) => {
        const o = document.createElement('option');
        o.value = String(v); o.textContent = t;
        if (v === cur) o.selected = true;
        sel.appendChild(o);
      });
      sel.addEventListener('change', () => { location.href = withParam(page, 'mincites', sel.value === '0' ? null : sel.value); });
      bar.appendChild(field('Min citations', sel));
      filters.minCitations = cur;
    }

    // One optional boolean toggle in the bar (currently only Categories'
    // "Exclude dataset papers") -- used to be its own standalone checkbox
    // sitting above that page's chart, disconnected from every other
    // control (user-flagged, same complaint and same fix as topN's comment
    // above: "the [control] must be in the SEARCH menu"). Like topN, a
    // change here does not navigate -- it persists to sessionStorage and
    // calls onChange directly, since the caller already has what it needs
    // to redraw in memory.
    // One optional include/exclude control, rendered as a labelled Yes/No
    // dropdown so it matches every other field in the bar (was a lone
    // checkbox). cbOpts.dropdownLabel is the short field label (e.g.
    // "DATASETS", "PREPRINTS"); "Yes" means include those papers, "No"
    // means exclude them. filters.checkbox stays true == "exclude" so
    // callers don't change.
    if (opts.checkbox) {
      const cbOpts = opts.checkbox;
      let excluded = false;
      if (cbOpts.storageKey) { try { excluded = sessionStorage.getItem(cbOpts.storageKey) === '1'; } catch (e) { /* ignore */ } }
      const sel = document.createElement('select');
      sel.id = cbOpts.id;
      [['yes', 'Yes'], ['no', 'No']].forEach(([v, t]) => {
        const o = document.createElement('option');
        o.value = v; o.textContent = t;
        if ((v === 'no') === excluded) o.selected = true;
        sel.appendChild(o);
      });
      sel.addEventListener('change', () => {
        const nowExcluded = sel.value === 'no';
        if (cbOpts.storageKey) { try { sessionStorage.setItem(cbOpts.storageKey, nowExcluded ? '1' : '0'); } catch (e) { /* ignore */ } }
        filters.checkbox = nowExcluded;
        if (cbOpts.onChange) cbOpts.onChange(nowExcluded);
      });
      bar.appendChild(field(cbOpts.dropdownLabel || 'Include', sel));
      filters.checkbox = excluded;
    }

    if (opts.metrics && opts.metrics.length) {
      const sel = document.createElement('select');
      const currentMetric = new URLSearchParams(location.search).get('metric') || opts.metrics[0].key;
      opts.metrics.forEach(m => {
        const o = document.createElement('option');
        o.value = m.key;
        o.textContent = m.label;
        if (m.key === currentMetric) o.selected = true;
        sel.appendChild(o);
      });
      sel.addEventListener('change', () => { location.href = withParam(page, 'metric', sel.value); });
      bar2.appendChild(field('Sort by', sel));
    }

    // Min-papers threshold, in the second row (a ranking/thresholding
    // control, not a WHICH-papers filter) -- still navigates via a URL
    // param on change, same as every row-1 control, and its current value
    // is returned on `filters` the same way. Row-count is no longer a
    // dropdown here at all -- see renderPagination below, which replaced
    // it (a 50/100/250/All picker whose "All" could mean rendering
    // thousands of rows at once, vs. fixed 50-per-page with Prev/Next).
    if (opts.minPapers) {
      const choices = opts.minPapers.options || [
        { value: 1, label: '1+ papers' },
        { value: 2, label: '2+ papers' },
        { value: 10, label: '10+ papers' },
        { value: 25, label: '25+ papers' },
        { value: 50, label: '50+ papers' },
        { value: 100, label: '100+ papers' },
      ];
      const defaultVal = String(opts.minPapers.default != null ? opts.minPapers.default : 2);
      const current = new URLSearchParams(location.search).get('minPapers') || defaultVal;
      const sel = document.createElement('select');
      choices.forEach(o => {
        const opt = document.createElement('option');
        opt.value = String(o.value);
        opt.textContent = o.label;
        if (String(o.value) === current) opt.selected = true;
        sel.appendChild(opt);
      });
      sel.addEventListener('change', () => {
        location.href = withParam(page, 'minPapers', sel.value === defaultVal ? null : sel.value);
      });
      sel.title = 'Only show rows with at least this many papers in the current filtered set';
      const minField = field('Min.', sel);
      minField.title = sel.title;
      bar2.appendChild(minField);
      filters.minPapers = parseInt(current, 10);
    }

    // "Show top N" for a page's adoption-over-time chart (or, on Network,
    // how many authors the graph itself draws) -- used to be its own
    // standalone control sitting above that chart/graph, disconnected from
    // every other control on the page (user-flagged: "the show menu must be
    // in the SEARCH menu", after multiple rounds of consolidating every
    // other control here already). Unlike every other field in this bar, a
    // change here does NOT navigate -- it calls opts.topN.onChange(n)
    // directly, same as the search field above, since the caller already
    // has everything it needs (the current filtered papers) in memory and
    // redrawing a chart doesn't need a fresh fetch or a URL change.
    if (opts.topN) {
      const tOpts = opts.topN;
      // A dropdown of fixed choices (user-requested, replacing the earlier
      // free-typed number field) -- also now the same value that caps the
      // page's table/list, not just its chart (see each page's render()),
      // so the choices are round numbers a reader would actually recognize
      // as "how many rows am I looking at", not an arbitrary spinner value.
      const choices = tOpts.options || [10, 25, 50, 100, 250];
      const defaultN = tOpts.default || 10;
      let stored = null;
      if (tOpts.storageKey) { try { stored = sessionStorage.getItem(tOpts.storageKey); } catch (e) { /* ignore */ } }
      let current = parseInt(stored, 10);
      if (!current || !choices.includes(current)) current = defaultN;
      const sel = document.createElement('select');
      choices.forEach(n => {
        const opt = document.createElement('option');
        opt.value = String(n);
        opt.textContent = String(n);
        if (n === current) opt.selected = true;
        sel.appendChild(opt);
      });
      sel.addEventListener('change', () => {
        const n = parseInt(sel.value, 10);
        if (tOpts.storageKey) { try { sessionStorage.setItem(tOpts.storageKey, String(n)); } catch (e) { /* ignore */ } }
        // filters.topN is on the same object the page is holding as
        // currentFilters (renderFilterBar's return value) -- mutate it in
        // place so a page's render()/renderResults(), called from
        // tOpts.onChange right below, reads the NEW value instead of
        // whatever topN was at the moment the filter bar was first built
        // (confirmed as a real bug: the dropdown and sessionStorage both
        // updated, but the table stayed capped at the original default
        // since renderResults() read filters.topN off the stale object).
        filters.topN = n;
        if (tOpts.onChange) tOpts.onChange(n);
      });
      // Row 1 (user-requested for Papers specifically -- opts.topN.row: 1),
      // row 2 everywhere else, same default as before.
      (tOpts.row === 1 ? bar : bar2).appendChild(field(tOpts.label || 'Show top', sel));
      filters.topN = current;
    }

    panel.appendChild(bar);
    if (bar2 !== bar && bar2.children.length) panel.appendChild(bar2);

    const metaRow = document.createElement('div');
    metaRow.className = 'controls-meta-row';
    metaRow.id = 'controls-meta-row';

    const chipRow = document.createElement('div');
    chipRow.className = 'chip-row';
    const active = FILTER_KEYS.filter(k => filters[k]).map(k => [k, filters[k]]);
    active.forEach(([key, value]) => {
      const chip = document.createElement('span');
      chip.className = 'chip';
      chip.append(`${key}: ${key === 'category' ? categoryLabel(value) : value} `);
      const clear = document.createElement('button');
      clear.textContent = '×';
      clear.title = `Remove ${key} filter`;
      clear.onclick = () => { location.href = withParam(page, key, null); };
      chip.appendChild(clear);
      chipRow.appendChild(chip);
    });
    metaRow.appendChild(chipRow);

    // "Clear all" -- previously a reader had to click each filter chip's own
    // × one at a time to get back to the page's default view. Only shown
    // when something is actually active (any URL param at all -- covers
    // filters, search, sort, minPapers, and page, not just the chip-tracked
    // FILTER_KEYS), and drops every one of them at once by navigating to the
    // bare page URL.
    if (new URLSearchParams(location.search).toString()) {
      const clearAll = document.createElement('button');
      clearAll.type = 'button';
      clearAll.className = 'clear-all-btn';
      clearAll.textContent = 'Clear all';
      clearAll.addEventListener('click', () => { location.href = page; });
      metaRow.appendChild(clearAll);
    }

    // "Copy link" -- the current URL already encodes every active
    // filter/search/sort/page, but a reader still had to copy it out of the
    // address bar by hand. One click, with a brief inline confirmation so
    // it's clear the click actually did something (clipboard writes are
    // otherwise silent).
    const copyLinkBtn = document.createElement('button');
    copyLinkBtn.type = 'button';
    copyLinkBtn.className = 'copy-link-btn';
    copyLinkBtn.textContent = 'Copy link';
    copyLinkBtn.addEventListener('click', () => {
      const original = copyLinkBtn.textContent;
      const showCopied = () => {
        copyLinkBtn.textContent = 'Copied!';
        setTimeout(() => { copyLinkBtn.textContent = original; }, 1500);
      };
      const showFailed = () => {
        copyLinkBtn.textContent = 'Copy failed';
        setTimeout(() => { copyLinkBtn.textContent = original; }, 1500);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(location.href).then(showCopied, showFailed);
      } else {
        showFailed();
      }
    });
    metaRow.appendChild(copyLinkBtn);

    const count = document.createElement('span');
    count.className = 'result-count';
    count.id = 'result-count';
    metaRow.appendChild(count);

    panel.appendChild(metaRow);
    container.appendChild(panel);
    return filters;
  };

  window.getMetric = function (defaultKey) {
    return new URLSearchParams(location.search).get('metric') || defaultKey;
  };

  // Small "i" info icon with a hover/tap tooltip, dropped into a panel or
  // section header so every table can say what it shows. Pure CSS tooltip
  // (see .info-tip in the style block above); tabindex so it's keyboard- and
  // touch-reachable. `text` is set as an attribute, never as markup.
  window.infoTip = function (text) {
    const el = document.createElement('span');
    el.className = 'info-tip';
    el.textContent = 'i';
    el.setAttribute('role', 'img');
    el.setAttribute('tabindex', '0');
    el.setAttribute('aria-label', text);
    el.setAttribute('data-tip', text);
    return el;
  };

  // Appends an info icon to an element (typically a panel's title row). If
  // the target isn't a flex row already, it still lands top-right via the
  // .info-tip { margin-left: auto } rule when the row is display:flex.
  window.addInfoTip = function (headerEl, text) {
    if (headerEl && !headerEl.querySelector('.info-tip')) headerEl.appendChild(infoTip(text));
  };

  // Pagination: a "Show N" page-size dropdown (10 / 25 / 50 / 100, default
  // 10) plus Prev/Next. Replaced an older fixed-50 design (itself a
  // replacement for a "Rows: 50/100/250/All" picker whose "All" could dump
  // thousands of rows and, on pages like Institutions, only ever meant "all
  // of the already-narrowed set"). The dropdown gives control back without
  // reintroducing an unbounded "All": the ceiling is 100 and Prev/Next
  // reach the rest.
  //
  // `total` is the length of the list the caller is about to render (after
  // every other filter/search). Returns {offset, pageSize, page,
  // totalPages} so the caller slices list.slice(offset, offset + pageSize).
  //
  // Prev/Next and the size dropdown update the URL via history.replaceState
  // and call opts.onChange() -- they do NOT navigate (clicking them used to
  // reload the whole page just to swap rows already in memory). replaceState
  // (not pushState) so Back leaves the list page rather than stepping one
  // page at a time.
  //
  // opts.paramKey ("page" by default) namespaces the page param so several
  // independently paged tables can coexist on one page (author.html); the
  // matching size param is "<base>_show" (or plain "show"). opts.pageSizes
  // and opts.defaultPageSize override the 10/25/50/100 default-10 choices.
  window.renderPagination = function (container, page, total, opts) {
    opts = opts || {};
    const paramKey = opts.paramKey || 'page';
    const sizeKey = paramKey === 'page' ? 'show' : paramKey.replace(/page$/, 'show');
    const sizes = opts.pageSizes || [10, 25, 50, 100];
    const defaultSize = opts.defaultPageSize || 10;
    const params = new URLSearchParams(location.search);

    // A caller can suppress the "Show N" dropdown (opts.showSizeControl:
    // false) and/or dictate the page size from an outside control
    // (opts.pageSize) -- e.g. the Papers page, where the filter bar's
    // "Show top" is the single control for both the table and the chart.
    const showSizeControl = opts.showSizeControl !== false && opts.pageSize == null;
    let pageSize;
    if (opts.pageSize != null) {
      pageSize = opts.pageSize;
    } else {
      pageSize = parseInt(params.get(sizeKey), 10);
      if (!sizes.includes(pageSize)) pageSize = defaultSize;
    }

    const totalPages = Math.max(1, Math.ceil(total / pageSize));
    let current = parseInt(params.get(paramKey), 10) || 1;
    if (current < 1) current = 1;
    if (current > totalPages) current = totalPages;
    const offset = (current - 1) * pageSize;

    // Touches only this table's own two params -- leaves other tables'
    // page/size state on the same URL alone.
    function apply(updates) {
      const u = new URL(location.href);
      Object.entries(updates).forEach(([k, v]) => {
        if (v == null) u.searchParams.delete(k); else u.searchParams.set(k, v);
      });
      if (opts.onChange) { history.replaceState(null, '', u); opts.onChange(); }
      else location.href = u.pathname + u.search;
    }
    const goTo = newPage => apply({ [paramKey]: newPage == null ? null : String(newPage) });

    container.innerHTML = '';
    const row = document.createElement('div');
    row.className = 'pagination-row';

    // A caller-supplied control (e.g. author.html's AV/Non-AV papers
    // dropdown) rendered on the same line, before the page-size box.
    if (opts.leadingControl) row.appendChild(opts.leadingControl);

    if (showSizeControl) {
      const showWrap = document.createElement('span');
      showWrap.className = 'page-size';
      showWrap.append('Show ');
      const sizeSel = document.createElement('select');
      sizes.forEach(n => {
        const o = document.createElement('option');
        o.value = String(n); o.textContent = String(n);
        if (n === pageSize) o.selected = true;
        sizeSel.appendChild(o);
      });
      sizeSel.addEventListener('change', () => {
        // New size -> back to page 1 for this table (drop its page param).
        apply({ [sizeKey]: sizeSel.value === String(defaultSize) ? null : sizeSel.value, [paramKey]: null });
      });
      showWrap.appendChild(sizeSel);
      row.appendChild(showWrap);
    }

    const prev = document.createElement('button');
    prev.type = 'button';
    prev.textContent = '‹ Prev';
    prev.disabled = current <= 1;
    prev.addEventListener('click', () => goTo(current > 2 ? current - 1 : null));
    row.appendChild(prev);

    const label = document.createElement('span');
    label.textContent = total
      ? `${(offset + 1).toLocaleString()} to ${Math.min(offset + pageSize, total).toLocaleString()} of ${total.toLocaleString()}`
      : '0 of 0';
    row.appendChild(label);

    const next = document.createElement('button');
    next.type = 'button';
    next.textContent = 'Next ›';
    next.disabled = current >= totalPages;
    next.addEventListener('click', () => goTo(current + 1));
    row.appendChild(next);

    container.appendChild(row);
    return { offset, pageSize, page: current, totalPages };
  };

  // Aggregates a set of papers by an arbitrary dimension (authors,
  // institutions, countries, category, venue, ...), so every page can compute
  // its own ranking from whatever subset of all_papers the active filters
  // leave behind, instead of relying on a single precomputed leaderboard that
  // ignores filters.
  window.aggregateByDimension = function (papers, accessor, options) {
    options = options || {};
    const minPapers = options.minPapers || 0;
    // Separate from minPapers: a paper counts toward minPapers even with no
    // citation data (see citedCounts below), so an entry can clear minPapers
    // while its avg_citations is really an average of just one or two real
    // data points -- easy to mistake for a well-supported ranking. minCitedPapers
    // requires that many papers with an actual citation number specifically.
    const minCitedPapers = options.minCitedPapers || 0;
    // Different from minCitedPapers: that one drops the whole entry (right
    // for a ranked-by-average leaderboard like Researchers, where an
    // unreliable average isn't worth showing at all). Countries/Institutions/
    // Venues also show paper count and total citations, which stay
    // meaningful even with thin citation coverage -- so instead of hiding
    // the row, just null out avg_citations until there's enough data to
    // trust it (caught in practice: Singapore/France showed "0 avg
    // citations/paper" off a single cited paper, reading as a real zero
    // rather than "we only have data for 1 paper from this country").
    const minCitedForAvg = options.minCitedForAvg || 0;
    // citedCounts tracks only papers with at least one citation, so the
    // average stays "citations per cited paper" -- a paper the corpus
    // doesn't reference (a real 0 now, no longer null) is counted in
    // `papers` but kept out of the average's denominator rather than
    // dragging it toward zero.
    const citations = {}, counts = {}, citedCounts = {};
    papers.forEach(p => {
      const vals = accessor(p) || [];
      new Set(vals).forEach(v => {
        if (!v) return;
        counts[v] = (counts[v] || 0) + 1;
        if (p.citations) {
          citations[v] = (citations[v] || 0) + p.citations;
          citedCounts[v] = (citedCounts[v] || 0) + 1;
        }
      });
    });
    // Citations and their average are always whole numbers (see Methodology).
    return Object.keys(counts)
      .filter(k => counts[k] > minPapers && (citedCounts[k] || 0) >= minCitedPapers)
      .map(k => ({
        name: k, citations: Math.round(citations[k] || 0), papers: counts[k],
        avg_citations: (citedCounts[k] || 0) >= Math.max(1, minCitedForAvg)
          ? Math.round(citations[k] / citedCounts[k]) : null,
      }));
  };

  // Every page's render() calls renderBody(shown) once and then
  // makeSortable(table, shown, columns, renderBody) right after -- if
  // sortable.js (loaded via its own <script src>, after this file) hasn't
  // finished loading for any reason (a transient fetch hiccup, e.g. right
  // after a fresh deploy while GitHub Pages' CDN is still propagating --
  // confirmed as the actual cause once, see DECISIONS.md), calling the bare
  // global directly throws a ReferenceError that the page's own top-level
  // .catch(err => ...) swallows into a misleading "Could not load
  // stats.json" message, blanking a table that in fact loaded fine and just
  // isn't click-to-sort this pageview. This wrapper lives in filters.js
  // (always the first script tag on every page, so always defined) and
  // degrades to "render once, skip sorting" instead of taking the whole
  // page down over one optional feature.
  window.makeSortableSafe = function (table, data, columns, renderBody) {
    renderBody(data);
    if (typeof window.makeSortable === 'function') {
      window.makeSortable(table, data, columns, renderBody);
    } else {
      console.warn('sortable.js did not load in time -- table is not click-to-sort this pageview.');
    }
  };

  // Appends a "Total" row summing whichever columns have a numeric
  // accessor. accessors is one entry per column, aligned with the table's
  // actual <td>s (including the leading name/link column) -- pass null for
  // any column that isn't a plain integer count (averages, ratios, years,
  // text/links), since summing those would be meaningless or misleading.
  window.appendSumRow = function (tbody, data, accessors) {
    if (!data.length || !accessors.some(a => a)) return;
    const tr = document.createElement('tr');
    tr.className = 'sum-row';
    accessors.forEach((acc, i) => {
      const td = document.createElement('td');
      if (i === 0) {
        td.textContent = 'Total';
        td.style.fontWeight = '600';
      } else if (acc) {
        const total = data.reduce((sum, r) => sum + (acc(r) || 0), 0);
        td.textContent = total.toLocaleString();
        td.className = 'num';
      }
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  };

  function csvField(v) {
    const s = String(v == null ? '' : v);
    return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }
  window.csvField = csvField;

  // A brief, self-dismissing confirmation in the bottom corner -- a download
  // click is otherwise silent (the browser's own download indicator is easy
  // to miss, especially on mobile), so this is the only feedback a reader
  // gets that the export actually happened.
  window.showToast = function (message) {
    let toast = document.getElementById('av-toast');
    if (!toast) {
      toast = document.createElement('div');
      toast.id = 'av-toast';
      toast.className = 'av-toast';
      document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.classList.add('show');
    clearTimeout(toast._hideTimer);
    toast._hideTimer = setTimeout(() => toast.classList.remove('show'), 2200);
  };

  window.downloadText = function (filename, text, mime) {
    const blob = new Blob([text], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    showToast(`Exported ${filename}`);
  };

  // Reads whatever is CURRENTLY rendered in the table's <thead>/<tbody> --
  // one field per <td>, always exactly as many fields as header columns,
  // which is what actually guarantees every row has the same column count
  // (a from-data-object CSV builder that special-cases a multi-value field
  // like "authors" is what caused a real bug: joining authors with "; "
  // inside a single field looks fine in a plain-comma-delimited reader, but
  // many regional Excel builds (semicolon as the default list separator)
  // instead split on every "; ", so a paper's column count silently grew
  // with its author count). DOM-based export has no such special case --
  // every table on the site gets the same button wired to the same
  // function, not a bespoke per-page CSV builder.
  window.exportTableToCsv = function (table, filename) {
    const rows = [];
    rows.push([...table.querySelectorAll('thead th')].map(th => th.textContent.trim()));
    table.querySelectorAll('tbody tr').forEach(tr => {
      rows.push([...tr.children].map(td => td.textContent.trim()));
    });
    // Excel's own CSV import (rather than double-click-open) always
    // respects an explicit sep= directive on line 1, regardless of the
    // system's regional list-separator default -- belt-and-suspenders
    // alongside the DOM-based per-cell approach above.
    const csv = 'sep=,\r\n' + rows.map(r => r.map(csvField).join(',')).join('\r\n');
    downloadText(filename, csv, 'text/csv;charset=utf-8');
  };

  // Self citations: how much of an author's OWN incoming citation count
  // (within this corpus) comes from their own later papers vs. an
  // independent paper. p.citations counts every in-corpus edge including
  // self-citations (they're no longer excluded from the total -- see
  // aggregate.py's in_corpus_counts), so it already contains whatever
  // p.self_citations reports; otherCitations here is the REMAINDER after
  // subtracting self_citations back out, not a second independent figure,
  // to avoid double-counting self-citations into the total. Takes the
  // author's own papers (already filtered by the caller) and returns a
  // {selfCitations, otherCitations} pair rather than a raw percentage,
  // since "no citation data at all" and "0% self-cited" both come out as 0
  // self-citations and need to stay distinguishable at render time.
  // Picks which of an author's institutions (author_detail's chronological
  // {name, first_year, last_year} list) to show alongside them on a table
  // about a specific *relationship* (co-authoring with page X, citing paper
  // Y) -- the institution whose own year range overlaps the years of the
  // papers that relationship is actually about, not just whichever
  // institution happens to be that author's most recent one on file. Those
  // can be years apart (user-flagged: a co-author who wrote papers with the
  // page's subject in 2018-2021 was shown at an institution first credited
  // in 2023, reading like a plainly wrong "current employer" line). Falls
  // back to the latest institution when no overlap exists, same as before.
  window.institutionForYears = function (institutions, relevantYears) {
    if (!institutions || !institutions.length) return null;
    const years = (relevantYears || []).filter(y => y != null);
    if (years.length) {
      const minY = Math.min(...years), maxY = Math.max(...years);
      const overlapping = institutions.filter(inst =>
        inst.first_year != null && inst.last_year != null &&
        inst.first_year <= maxY && inst.last_year >= minY);
      if (overlapping.length) {
        overlapping.sort((a, b) => {
          const overlapA = Math.min(a.last_year, maxY) - Math.max(a.first_year, minY);
          const overlapB = Math.min(b.last_year, maxY) - Math.max(b.first_year, minY);
          return overlapB - overlapA || b.last_year - a.last_year;
        });
        return overlapping[0];
      }
    }
    return institutions[institutions.length - 1];
  };

  window.computeSelfCitationStats = function (ownPapers) {
    let selfCitations = 0, otherCitations = 0;
    (ownPapers || []).forEach(p => {
      const self = p.self_citations || 0;
      selfCitations += self;
      otherCitations += (p.citations || 0) - self;
    });
    return { selfCitations, otherCitations };
  };

  // Total paper count and total citations for every author in the corpus,
  // computed in one pass over all_papers -- shared so a "Citing authors"
  // table (author.html, paper.html) can show each citing author's own
  // overall standing (papers/citations columns, user-requested) without
  // each page re-scanning all_papers once per citing author found.
  window.computeAuthorPaperStats = function (allPapers) {
    const stats = {};
    (allPapers || []).forEach(p => (p.authors || []).forEach(a => {
      const rec = stats[a] || (stats[a] = { papers: 0, citations: 0 });
      rec.papers += 1;
      if (p.citations != null) rec.citations += p.citations;
    }));
    return stats;
  };

  // A small "Export CSV" button, styled to match the rest of the site's
  // controls, meant to sit in a panel's title row next to its <h2>. Kept
  // here (not duplicated per page) so every table's export button looks
  // and behaves identically.
  // A "← Back" link for detail pages (researcher/institution/venue/paper),
  // using history.back() rather than a static href to the bare list page --
  // arriving here from a filtered/searched/sorted/paginated list is a real
  // <a href> navigation, so the browser's own history already has that exact
  // view; a plain link back to e.g. institutions.html would silently drop
  // whatever filters got the reader here in the first place (user-flagged).
  // Only rendered when there's actually a same-site page to return to --
  // arriving via a bookmark, a shared link, or a fresh tab has no useful
  // "back" destination, so history.back() there would do nothing or leave
  // the site entirely.
  window.renderBackLink = function () {
    if (!(window.history && history.length > 1 && document.referrer)) return null;
    let sameSite = false;
    try { sameSite = new URL(document.referrer).origin === location.origin; } catch (e) { /* ignore */ }
    if (!sameSite) return null;
    const a = document.createElement('a');
    a.href = '#';
    a.className = 'detail-back-link';
    a.textContent = '← Back';
    a.addEventListener('click', ev => { ev.preventDefault(); history.back(); });
    return a;
  };

  // BibTeX for a list of paper objects. A stable key = first-author surname
  // + year + a short title slug -- unique enough for a corpus this size and
  // what most reference managers generate on import anyway.
  function bibtexField(v) { return String(v == null ? '' : v).replace(/[{}]/g, ''); }
  window.papersToBibtex = function (papers) {
    const seen = new Set();
    return (papers || []).map(p => {
      const surname = ((p.authors || [])[0] || 'anon').trim().split(/\s+/).pop().replace(/[^a-zA-Z]/g, '') || 'anon';
      const firstWord = (p.title || '').split(/\s+/).find(w => /[a-zA-Z]{3,}/.test(w)) || '';
      let key = `${surname}${p.year || ''}${firstWord.replace(/[^a-zA-Z0-9]/g, '')}`;
      let unique = key, n = 2;
      while (seen.has(unique)) { unique = key + n; n += 1; }
      seen.add(unique);
      const fields = [
        ['title', bibtexField(p.title)],
        ['author', (p.authors || []).join(' and ')],
        ['year', p.year || ''],
        ['booktitle', p.venue || ''],
      ];
      if (p.doi) fields.push(['doi', p.doi]);
      const body = fields.filter(([, v]) => v).map(([k, v]) => `  ${k} = {${v}}`).join(',\n');
      return `@inproceedings{${unique},\n${body}\n}`;
    }).join('\n\n');
  };

  function exportBadge(label, onClick) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'export-badge';
    b.textContent = label;
    b.title = `Download ${label}`;
    b.addEventListener('click', onClick);
    return b;
  }

  // CSV badge for any table; optionally a BibTeX badge too when the caller
  // has the underlying paper objects (a table alone doesn't carry authors
  // or DOIs). Returns a small inline row of one or two icon badges.
  window.renderExportButtons = function (opts) {
    const row = document.createElement('span');
    row.className = 'export-row';
    row.appendChild(exportBadge('CSV', () => exportTableToCsv(opts.table, opts.csvName || 'export.csv')));
    if (opts.bibtexPapers) {
      row.appendChild(exportBadge('BibTeX', () => downloadText(
        opts.bibtexName || 'export.bib',
        papersToBibtex(typeof opts.bibtexPapers === 'function' ? opts.bibtexPapers() : opts.bibtexPapers),
        'application/x-bibtex;charset=utf-8')));
    }
    return row;
  };

  // Back-compat: the CSV-only badge, same shape callers already append.
  window.renderExportButton = function (table, filename) {
    return renderExportButtons({ table, csvName: filename });
  };

  // Animates a stat tile's number counting up from 0 to its real value on
  // first render, instead of the value just appearing -- a small bit of
  // life on pages whose whole job is showing a handful of big numbers.
  // Skips the animation entirely under prefers-reduced-motion, and for
  // anyone who opens the page with the tab backgrounded (no visible frames
  // to animate into anyway).
  const REDUCE_MOTION = typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const CAN_ANIMATE = typeof requestAnimationFrame === 'function' && typeof performance !== 'undefined';
  window.animateCount = function (el, target, opts) {
    opts = opts || {};
    const duration = opts.duration || 900;
    const decimals = opts.decimals || 0;
    const suffix = opts.suffix || '';
    const format = v => v.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals }) + suffix;
    if (REDUCE_MOTION || !CAN_ANIMATE || !isFinite(target)) {
      el.textContent = format(target);
      return;
    }
    const start = performance.now();
    function tick(now) {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3); // ease-out cubic -- fast start, gentle settle
      el.textContent = format(target * eased);
      if (t < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  };

  // Shared "N series over time" line chart + Top-N control, extracted out of
  // categories.html and the since-retired datasets.html (which each had their
  // own copy of this ~150-line renderer) so Papers/Institutions/Venues/Countries can get the
  // same "top N over time" panel without re-duplicating it a fourth and
  // fifth time. A page using this must still define its own .line-chart-wrap
  // /.series-line/.axis-line/.crosshair/.legend-row/.legend-item/.tooltip-box
  // CSS (kept per-page, not injected here, since some pages already ship
  // slight variants like .series-line.dimmed).
  const TIMELINE_PALETTE = [
    '#e6614f', '#3987e5', '#3fae6a', '#e6a53f', '#9366d9', '#3fb8bd',
    '#d9527a', '#7a9e3f', '#c98e3f', '#5f7de6', '#4fa88a', '#c95fd0',
  ];
  window.TIMELINE_PALETTE = TIMELINE_PALETTE;

  // Builds a legend-item's content (color swatch + a text label sourced from
  // scraped third-party data -- an institution/venue/country/category/paper
  // name) via real DOM nodes, not an innerHTML template literal. Every one
  // of those names came from a PDF/HTML scrape at some point in the
  // pipeline; a garbled parse could in principle contain markup, and
  // interpolating it straight into innerHTML would let it execute. Replaces
  // the `item.innerHTML = \`<span class="swatch" ...>${name}...\`` pattern
  // that was previously duplicated across every legend on the site.
  window.renderSwatchLabel = function (container, color, text) {
    container.textContent = '';
    const swatch = document.createElement('span');
    swatch.className = 'swatch';
    swatch.style.background = color;
    container.appendChild(swatch);
    container.appendChild(document.createTextNode(text));
  };

  // "Show all" / "Hide all" for a legend whose entries can be toggled
  // individually (click a swatch to hide that series) -- with several
  // toggled off there was no quick way back to "everything visible" short
  // of clicking each one again. `hiddenSet` is the caller's own Set of
  // hidden keys (mutated in place); `allKeys` is every key currently shown
  // in the legend; `redraw` is the caller's own full legend+chart rebuild
  // function, called again after mutating the set so the legend items'
  // struck-through state and the chart lines stay in sync the same way a
  // single legend-item click already keeps them in sync.
  window.renderLegendToggleAll = function (legendEl, hiddenSet, allKeys, redraw) {
    const row = document.createElement('div');
    row.className = 'legend-toggle-all';
    const showAll = document.createElement('button');
    showAll.type = 'button';
    showAll.textContent = 'Show all';
    showAll.addEventListener('click', () => { hiddenSet.clear(); redraw(); });
    const hideAll = document.createElement('button');
    hideAll.type = 'button';
    hideAll.textContent = 'Hide all';
    hideAll.addEventListener('click', () => { allKeys.forEach(k => hiddenSet.add(k)); redraw(); });
    row.appendChild(showAll);
    row.appendChild(hideAll);
    legendEl.appendChild(row);
  };

  // A helpful empty state ("0 authors match the current filters" on its own
  // is a dead end) -- explains what to try next and gives a one-click way
  // to actually do it, instead of making the reader hunt for which of the
  // several active filters to loosen by hand.
  window.renderEmptyState = function (container, page, message) {
    container.innerHTML = '';
    const wrap = document.createElement('div');
    wrap.className = 'empty-state';
    const msg = document.createElement('p');
    msg.textContent = message || 'Nothing matches the current filters.';
    wrap.appendChild(msg);
    if (new URLSearchParams(location.search).toString()) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'clear-all-btn';
      btn.textContent = 'Clear all filters';
      btn.addEventListener('click', () => { location.href = page; });
      wrap.appendChild(btn);
    }
    container.appendChild(wrap);
  };

  // Rounds a chart's data maximum up to a "nice" axis top so the 4 evenly
  // spaced gridlines land on readable numbers (0 / 750 / 1500 / 2250 / 3000)
  // instead of raw quarter-fractions of the data max (0 / 554 / 1108 / ...).
  // Small integer ranges get plain integer steps; a data point up to ~5%
  // above the nice top is tolerated rather than doubling the axis height for
  // it (it just sits a hair above the top gridline).
  window.niceAxisMax = function (dataMax) {
    if (!(dataMax > 0)) return 1;
    // Small integer-count ranges get plain integer steps. Guarded to
    // dataMax >= 1 so normalized/fractional charts (e.g. a 0..0.14 "share of
    // papers" axis) fall through to the nice-number path instead of being
    // snapped up to 4.
    if (dataMax >= 1 && dataMax <= 12) return Math.max(1, Math.ceil(dataMax / 4)) * 4;
    const rough = dataMax / 4;
    const base = Math.pow(10, Math.floor(Math.log10(rough)));
    const f = rough / base;
    const nice = f <= 1.05 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 3 ? 3
      : f <= 4 ? 4 : f <= 5 ? 5 : f <= 7.5 ? 7.5 : 10;
    return nice * base * 4;
  };

  window.lineChart = function (svgEl, tooltipEl, series, years, formatValue, opts) {
    opts = opts || {};
    svgEl.innerHTML = '';
    if (!years.length || !series.some(s => Object.values(s.values).some(v => v != null))) {
      const msg = document.createElementNS(svgEl.namespaceURI, 'text');
      msg.setAttribute('x', 20); msg.setAttribute('y', 30);
      msg.setAttribute('fill', 'var(--muted)'); msg.setAttribute('font-size', '13');
      msg.textContent = 'No data for the current filters.';
      svgEl.appendChild(msg);
      return;
    }

    // A left-margin label naming what the numbers actually count -- plain
    // tick numbers with no unit read as ambiguous on a page with several
    // different metrics nearby (user-flagged on Venues: "the y axis is
    // unclear" -- a paper count next to a table whose columns include
    // citations, ratios, and "citations/paper" all at once).
    const PAD_L = opts.yLabel ? 56 : 46;
    const W = 1100, H = 340, PAD_R = 16, PAD_T = 16, PAD_B = 28;
    svgEl.setAttribute('viewBox', `0 0 ${W} ${H}`);
    const ns = svgEl.namespaceURI;
    if (opts.yLabel) {
      const yLabelEl = document.createElementNS(ns, 'text');
      yLabelEl.setAttribute('x', 14); yLabelEl.setAttribute('y', (PAD_T + (H - PAD_B)) / 2);
      yLabelEl.setAttribute('text-anchor', 'middle'); yLabelEl.setAttribute('class', 'axis-label');
      yLabelEl.setAttribute('transform', `rotate(-90, 14, ${(PAD_T + (H - PAD_B)) / 2})`);
      yLabelEl.textContent = opts.yLabel;
      svgEl.appendChild(yLabelEl);
    }

    const allValues = series.flatMap(s => years.map(y => s.values[y])).filter(v => v != null);
    const dataMax = opts.fixedMax != null ? opts.fixedMax : Math.max(1e-9, ...allValues);
    // opts.log -> logarithmic y axis (gridlines at powers of ten).
    // opts.tightAxis -> round the top up only to the next 50, so the axis
    // sits just above the data instead of niceAxisMax's roomier ceiling.
    const useLog = !!opts.log;
    const logLo = 1;
    const logHi = Math.max(10, Math.pow(10, Math.ceil(Math.log10(Math.max(logLo + 1e-9, dataMax)))));
    const maxV = opts.fixedMax != null ? dataMax
      : opts.tightAxis ? Math.max(1, Math.ceil(dataMax / 50) * 50)
      : niceAxisMax(dataMax);
    const x = i => PAD_L + (years.length <= 1 ? 0 : (i / (years.length - 1)) * (W - PAD_L - PAD_R));
    const y = useLog
      ? v => H - PAD_B - ((Math.log10(Math.max(logLo, v)) - Math.log10(logLo)) / (Math.log10(logHi) - Math.log10(logLo))) * (H - PAD_T - PAD_B)
      : v => H - PAD_B - (Math.max(0, v) / maxV) * (H - PAD_T - PAD_B);

    const gridVals = useLog
      ? Array.from({ length: Math.round(Math.log10(logHi)) + 1 }, (_, e) => Math.pow(10, e))
      : Array.from({ length: 5 }, (_, i) => (maxV / 4) * i);
    gridVals.forEach((v, i) => {
      const gy = y(v);
      const line = document.createElementNS(ns, 'line');
      line.setAttribute('x1', PAD_L); line.setAttribute('x2', W - PAD_R);
      line.setAttribute('y1', gy); line.setAttribute('y2', gy);
      line.setAttribute('class', 'axis-line'); line.setAttribute('stroke-opacity', i === 0 ? 0.6 : 0.25);
      svgEl.appendChild(line);
      const label = document.createElementNS(ns, 'text');
      label.setAttribute('x', PAD_L - 6); label.setAttribute('y', gy + 3);
      label.setAttribute('text-anchor', 'end'); label.setAttribute('class', 'axis-label');
      label.textContent = opts.formatAxis ? opts.formatAxis(v)
        : useLog ? v.toLocaleString() : Math.round(v);
      svgEl.appendChild(label);
    });
    const xStep = Math.max(1, Math.ceil(years.length / 18));
    years.forEach((yr, i) => {
      if (i % xStep !== 0 && i !== years.length - 1) return;
      const label = document.createElementNS(ns, 'text');
      label.setAttribute('x', x(i)); label.setAttribute('y', H - PAD_B + 16);
      label.setAttribute('text-anchor', 'middle'); label.setAttribute('class', 'axis-label');
      label.textContent = yr;
      svgEl.appendChild(label);
    });

    const crosshair = document.createElementNS(ns, 'line');
    crosshair.setAttribute('class', 'crosshair');
    crosshair.setAttribute('y1', PAD_T); crosshair.setAttribute('y2', H - PAD_B);
    crosshair.style.display = 'none';
    svgEl.appendChild(crosshair);

    const seriesEls = series.map(s => {
      let d = '';
      let drawing = false;
      years.forEach((yr, i) => {
        const v = s.values[yr];
        if (v == null) { drawing = false; return; }
        d += (drawing ? 'L' : 'M') + x(i) + ',' + y(v) + ' ';
        drawing = true;
      });
      const path = document.createElementNS(ns, 'path');
      path.setAttribute('d', d.trim());
      path.setAttribute('class', 'series-line');
      path.setAttribute('stroke', s.color);
      svgEl.appendChild(path);

      years.forEach((yr, i) => {
        const v = s.values[yr];
        if (v == null) return;
        const dot = document.createElementNS(ns, 'circle');
        dot.setAttribute('cx', x(i)); dot.setAttribute('cy', y(v)); dot.setAttribute('r', opts.dotRadius || 3);
        dot.setAttribute('fill', s.color);
        dot.setAttribute('class', 'series-dot');
        svgEl.appendChild(dot);
      });
      return { series: s };
    });

    years.forEach((yr, i) => {
      const colW = years.length > 1 ? (W - PAD_L - PAD_R) / (years.length - 1) : (W - PAD_L - PAD_R);
      const hit = document.createElementNS(ns, 'rect');
      hit.setAttribute('x', x(i) - colW / 2); hit.setAttribute('y', PAD_T);
      hit.setAttribute('width', colW); hit.setAttribute('height', H - PAD_T - PAD_B);
      hit.setAttribute('fill', 'transparent');
      hit.addEventListener('mouseenter', () => {
        crosshair.style.display = '';
        crosshair.setAttribute('x1', x(i)); crosshair.setAttribute('x2', x(i));
        // Built via DOM nodes, not an innerHTML template -- se.series.name is
        // scraped third-party text (institution/venue/paper/etc. name), not
        // safe to interpolate straight into markup (see renderSwatchLabel's
        // comment for why).
        tooltipEl.textContent = '';
        const yearEl = document.createElement('div');
        yearEl.className = 'tt-year';
        yearEl.textContent = yr;
        tooltipEl.appendChild(yearEl);
        const visible = seriesEls
          .filter(se => se.series.values[yr] != null)
          .sort((a, b) => b.series.values[yr] - a.series.values[yr]);
        if (!visible.length) {
          const none = document.createElement('div');
          none.style.color = 'var(--muted)';
          none.textContent = 'no data';
          tooltipEl.appendChild(none);
        }
        visible.forEach(se => {
          const rowEl = document.createElement('div');
          rowEl.className = 'tt-row';
          const swatch = document.createElement('span');
          swatch.className = 'tt-swatch';
          swatch.style.background = se.series.color;
          rowEl.appendChild(swatch);
          rowEl.appendChild(document.createTextNode(se.series.name));
          const val = document.createElement('span');
          val.className = 'tt-val';
          val.textContent = formatValue(se.series.values[yr]);
          rowEl.appendChild(val);
          tooltipEl.appendChild(rowEl);
        });
        tooltipEl.style.display = 'block';
        tooltipEl.style.left = Math.min(W - 220, Math.max(0, x(i) - 60)) / W * 100 + '%';
        tooltipEl.style.top = '8px';
      });
      hit.addEventListener('mouseleave', () => {
        crosshair.style.display = 'none';
        tooltipEl.style.display = 'none';
      });
      svgEl.appendChild(hit);
    });
    svgEl.appendChild(crosshair);
  };

  // A "Show top N" number input, same URL/sessionStorage-persisted pattern
  // as renderLimitControl/renderMinPapersControl above -- how many series a
  // timeline chart draws at once. Kept separate from renderLimitControl
  // (which caps table ROWS, a much larger and page-scoped number) because a
  // chart with more than ~20 lines stops being readable regardless of how
  // many rows the table below it shows.
  window.renderTopNControl = function (container, storageKey, opts) {
    opts = opts || {};
    const min = opts.min || 1, max = opts.max || 30;
    const defaultN = opts.default || 10;
    // sessionStorage can throw (private-browsing storage lockdowns, or this
    // running inside the qa_smoke_test.js sandbox, which has no Storage
    // implementation at all) -- same defensive pattern already used for the
    // legend show/hide state below, so a blocked/missing sessionStorage
    // degrades to "always the default N" instead of crashing the page.
    let stored = null;
    try { stored = sessionStorage.getItem(storageKey); } catch (e) { /* ignore */ }
    let current = parseInt(stored, 10);
    if (!current || current < min || current > max) current = defaultN;
    container.innerHTML = '';
    const row = document.createElement('div');
    row.className = 'limit-row topn-row';
    row.append('Show top ');
    const input = document.createElement('input');
    input.type = 'number';
    input.min = String(min);
    input.max = String(max);
    input.value = String(current);
    input.style.cssText = 'width:56px;background:var(--panel2);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:4px 6px;font-size:0.95em;';
    input.addEventListener('change', () => {
      let n = parseInt(input.value, 10);
      if (!n || n < min) n = min;
      if (n > max) n = max;
      input.value = String(n);
      try { sessionStorage.setItem(storageKey, String(n)); } catch (e) { /* ignore */ }
      if (opts.onChange) opts.onChange(n);
    });
    row.appendChild(input);
    row.append(' over time');
    container.appendChild(row);
    return current;
  };

  // Groups papers by an arbitrary dimension (institutions/venues/countries/
  // category/...) and draws the top-N groups (by paper count) as a shared
  // line chart, with a legend that persists its own show/hide toggles. The
  // Top-N control itself lives in the page's SEARCH filter bar (opts.topN on
  // renderFilterBar), not here -- this used to render its own standalone
  // control via renderTopNControl, disconnected from every other control on
  // the page (user-flagged, see renderFilterBar's opts.topN comment). Returns
  // `draw` so the caller wires the filter bar's topN.onChange straight to it.
  window.renderDimensionTimeline = function (opts) {
    const { papers, dimensionFn, labelFor, elIds, rankedNames } = opts;
    const label = labelFor || (v => v);

    const byGroup = {};
    const eligibleNames = rankedNames ? new Set(rankedNames) : null;
    papers.forEach(p => {
      if (!p.year) return;
      const vals = new Set(dimensionFn(p) || []);
      vals.forEach(v => {
        if (!v) return;
        // Restricted to the same min-papers-filtered set the list/cards
        // below use, when the caller passes one.
        if (eligibleNames && !eligibleNames.has(v)) return;
        (byGroup[v] = byGroup[v] || []).push(p);
      });
    });
    const allYears = [...new Set(papers.map(p => p.year).filter(Boolean))].sort();
    // Total papers per year across ALL groups (not just the top-N shown),
    // used by the optional "normalize by total papers that year" toggle --
    // otherwise an early, thin year with only 2 papers (both from the same
    // institution) would show a misleading 100% line next to a later, much
    // busier year's smaller share.
    const totalByYear = {};
    papers.forEach(p => { if (p.year) totalByYear[p.year] = (totalByYear[p.year] || 0) + 1; });

    const normalizeCheckbox = elIds.normalize ? document.getElementById(elIds.normalize) : null;

    const legend = document.getElementById(elIds.legend);
    const chart = document.getElementById(elIds.chart);
    const tooltip = document.getElementById(elIds.tooltip);

    // Which series are toggled off, in memory only for this pageview -- NOT
    // persisted (user-flagged: a struck-out legend entry surviving a
    // refresh reads as a stuck/broken toggle, not a remembered preference).
    // Every reload starts with every series visible again.
    const hidden = new Set();

    function draw(topN) {
      // When the caller supplies the list/cards' own ranking (rankedNames,
      // already sorted by whatever metric the page is currently using --
      // avg citations by default on Institutions/Countries), the chart's
      // "top N" means the same N as the list right below it. Falls back to
      // ranking by raw paper count here only when no ranking was supplied.
      // Without this, the chart always ranked by paper count regardless of
      // the list's own sort, so its #1 could silently differ from the
      // list's #1 (user-reported: Institutions' chart didn't include
      // Google, #1 in the list by average citations, since Google isn't
      // top-10 by raw paper count).
      const topGroups = rankedNames
        ? rankedNames.filter(g => byGroup[g]).slice(0, topN)
        : Object.entries(byGroup).sort((a, b) => b[1].length - a[1].length).slice(0, topN).map(kv => kv[0]);

      legend.innerHTML = '';
      renderLegendToggleAll(legend, hidden, topGroups, () => draw(topN));
      topGroups.forEach((g, i) => {
        const item = document.createElement('div');
        item.className = 'legend-item' + (hidden.has(g) ? ' off' : '');
        renderSwatchLabel(item, TIMELINE_PALETTE[i % TIMELINE_PALETTE.length], `${label(g)} (${byGroup[g].length})`);
        item.addEventListener('click', () => {
          if (hidden.has(g)) hidden.delete(g); else hidden.add(g);
          item.classList.toggle('off');
          drawSeries();
        });
        legend.appendChild(item);
      });

      function drawSeries() {
        const normalize = !!(normalizeCheckbox && normalizeCheckbox.checked);
        const series = topGroups
          .filter(g => !hidden.has(g))
          .map((g, i) => {
            const byYear = {};
            byGroup[g].forEach(p => { byYear[p.year] = (byYear[p.year] || 0) + 1; });
            const values = {};
            allYears.forEach(yr => {
              if (normalize) {
                const total = totalByYear[yr] || 0;
                values[yr] = total && byYear[yr] ? byYear[yr] / total : (byYear[yr] ? 0 : null);
              } else if (opts.skipZeros) {
                // For a dimension whose "0 that year" usually means "didn't
                // publish at all that year" rather than "had a slow year"
                // (a biannual venue like ECCV/ICCV, which simply doesn't run
                // on its off years) -- a plotted 0 reads as a crash-to-zero
                // every other year instead of the gap it actually is.
                values[yr] = byYear[yr] || null;
              } else {
                values[yr] = byYear[yr] || 0;
              }
            });
            return { name: label(g), color: TIMELINE_PALETTE[topGroups.indexOf(g) % TIMELINE_PALETTE.length], values };
          });
        const fmt = normalize ? (v => `${(v * 100).toFixed(1)}% of that year's papers`) : (v => `${v} paper${v === 1 ? '' : 's'}`);
        const axisFmt = normalize ? (v => `${Math.round(v * 100)}%`) : undefined;
        const yLabel = normalize ? '% of that year\'s papers' : (opts.yLabel || 'Papers per year');
        lineChart(chart, tooltip, series, allYears, fmt, { formatAxis: axisFmt, yLabel });
      }
      if (normalizeCheckbox) normalizeCheckbox.onchange = drawSeries;
      drawSeries();
    }

    return draw;
  };

  // Every <table> on every page gets auto-wrapped in .table-scroll, not
  // just the ones a page author remembered to wrap by hand -- a table this
  // was missed on (venues.html's, user-flagged: its "Best paper" column
  // straddled the table on a laptop-width viewport) is exactly the failure
  // mode a manual per-page wrapper can't prevent, and neither can a page
  // author remember to do this for every table a FUTURE page adds either.
  // Runs on DOMContentLoaded since filters.js's <script> tag loads before
  // the page's own static <table> markup appears later in the body.
  function autoWrapTables() {
    document.querySelectorAll('table').forEach(table => {
      if (table.parentElement && table.parentElement.classList.contains('table-scroll')) return;
      const wrap = document.createElement('div');
      wrap.className = 'table-scroll';
      table.parentNode.insertBefore(wrap, table);
      wrap.appendChild(table);
    });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', autoWrapTables);
  } else {
    autoWrapTables();
  }

  // A floating "back to top" button -- the longest paginated tables here
  // are still 50 rows a page, easy to scroll well past the filter bar
  // controls with no quick way back up short of the Home key. Injected once
  // per page (same as nav.js's reload/report buttons), shown only once the
  // reader has actually scrolled a meaningful distance.
  function initBackToTop() {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'back-to-top-btn';
    btn.title = 'Back to top';
    btn.setAttribute('aria-label', 'Back to top');
    btn.textContent = '↑';
    btn.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));
    document.body.appendChild(btn);
    window.addEventListener('scroll', () => {
      btn.classList.toggle('show', window.scrollY > 600);
    }, { passive: true });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initBackToTop);
  } else {
    initBackToTop();
  }

  // A column header's title="" attribute (e.g. "Self-citation %", Network's
  // "Centrality") is invisible on touch devices -- there's no hover to
  // trigger it. Tapping a header with an explanation shows it as a toast
  // instead, the same confirmation surface exports already use.
  document.addEventListener('touchend', ev => {
    const th = ev.target && ev.target.closest && ev.target.closest('th[title]');
    if (!th) return;
    showToast(th.getAttribute('title'));
  }, { passive: true });
})();
