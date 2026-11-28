// Shared top nav bar for every av-atlas page. Injects its own styles once,
// then renders into the page's <nav id="topnav"></nav> placeholder with the
// current page highlighted.
(function () {
  const PAGES = [
    { href: 'index.html', label: 'Papers' },
    { href: 'authors.html', label: 'Authors' },
    { href: 'institutions.html', label: 'Institutions' },
    { href: 'venues.html', label: 'Venues' },
    { href: 'countries.html', label: 'Countries' },
    { href: 'categories.html', label: 'Categories' },
    { href: 'network.html', label: 'Network' },
    { href: 'insights.html', label: 'Insights' },
    { href: 'about.html', label: 'About' },
  ];

  const style = document.createElement('style');
  style.textContent = `
    .topbar {
      display: flex; flex-wrap: wrap; gap: 2px; align-items: center;
      background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
      padding: 6px; margin-bottom: 20px; position: sticky; top: 8px; z-index: 10;
    }
    .topbar a {
      color: var(--muted); text-decoration: none; font-size: 0.88em; font-weight: 600;
      padding: 7px 12px; border-radius: 7px; transition: background 0.12s, color 0.12s;
    }
    .topbar a:hover { background: var(--panel2); color: var(--text); }
    .topbar a.active { background: var(--accent); color: #fff; }
    .topbar #hardReload {
      margin-left: auto; background: none; border: 1px solid var(--border); color: var(--muted);
      font-size: 1em; line-height: 1; width: 30px; height: 30px; border-radius: 7px; cursor: pointer;
    }
    .topbar #hardReload:hover { background: var(--panel2); color: var(--text); }
    .topbar #reportIssueBtn {
      background: none; border: 1px solid var(--border); color: var(--muted);
      font-size: 1em; line-height: 1; width: 30px; height: 30px; border-radius: 7px; cursor: pointer;
      display: flex; align-items: center; justify-content: center;
    }
    .topbar #reportIssueBtn:hover { background: var(--panel2); color: var(--text); }
    .site-brand { display: flex; align-items: center; gap: 9px; margin: 0 0 16px; font-size: 1.15rem; }
    .site-brand svg { width: 26px; height: 26px; flex-shrink: 0; }
    .site-brand span { font-size: 1.5em; font-weight: 700; letter-spacing: -0.01em; color: var(--text); }

    .report-issue-overlay {
      display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 100;
      align-items: center; justify-content: center; padding: 20px;
    }
    .report-issue-overlay.open { display: flex; }
    .report-issue-modal {
      background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
      padding: 20px 22px; max-width: 420px; width: 100%;
    }
    .report-issue-modal h3 { margin: 0 0 6px; font-size: 1.05em; }
    .report-issue-modal p { margin: 0 0 14px; color: var(--muted); font-size: 0.85em; }
    .report-issue-modal label { display: block; font-size: 0.82em; color: var(--muted); margin: 10px 0 4px; }
    .report-issue-modal textarea, .report-issue-modal input {
      width: 100%; box-sizing: border-box; background: var(--panel2); color: var(--text);
      border: 1px solid var(--border); border-radius: 6px; padding: 8px 10px; font-size: 0.9em;
      font-family: inherit;
    }
    .report-issue-modal textarea { min-height: 80px; resize: vertical; }
    .report-issue-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
    .report-issue-actions button {
      border-radius: 6px; padding: 7px 14px; font-size: 0.88em; cursor: pointer;
    }
    .report-issue-actions .cancel-btn { background: none; border: 1px solid var(--border); color: var(--text); }
    .report-issue-actions .send-btn { background: var(--accent); border: 1px solid var(--accent); color: #fff; }

    /* Keyboard users: one visible focus ring everywhere, a skip link to jump
       past the nav, and a footer that carries the version and data date. */
    a:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible,
    textarea:focus-visible, summary:focus-visible, [tabindex]:focus-visible {
      outline: 2px solid var(--accent); outline-offset: 2px;
    }
    .skip-link {
      position: absolute; left: 8px; top: -60px; z-index: 300; background: var(--accent); color: #fff;
      padding: 10px 16px; border-radius: 8px; font-weight: 600; text-decoration: none;
    }
    .skip-link:focus { top: 8px; }
    #main-content:focus { outline: none; }
    .site-footer {
      margin-top: 32px; padding: 16px 0 8px; border-top: 1px solid var(--border);
      color: var(--muted); font-size: 0.82em; display: flex; flex-wrap: wrap; gap: 6px 16px;
    }
    .site-footer a { color: var(--muted); }
    .site-footer a:hover { color: var(--accent); }

    /* Phones and touch screens: controls get a 44px target. */
    @media (max-width: 700px), (pointer: coarse) {
      /* Three rows of 44px links would cover a fifth of a phone screen if the bar stayed stuck to the top. */
      .topbar { position: static; }
      .topbar a { padding: 12px 12px; min-height: 44px; display: inline-flex; align-items: center; }
      .topbar #hardReload, .topbar #reportIssueBtn { width: 44px; height: 44px; }
      .report-issue-actions button { min-height: 44px; padding: 10px 16px; }
      body .pagination-row button, body .export-csv-btn, body .clear-all-btn, body .filter-toggle,
      body .export-badge, body .legend-toggle-all button { min-height: 44px; }
      body .pagination-row .page-size select, body .limit-row select, body .filter-bar select,
      body .filter-bar .search-field input { min-height: 44px; font-size: 16px; }
      body table.sortable thead th[data-sortable] { padding-top: 14px; padding-bottom: 14px; }
      body .legend-toggle-all button { min-width: 44px; }
      body .chip button { width: 28px; height: 28px; }
      body .info-tip { width: 28px; height: 28px; font-size: 13px; }
    }
  `;
  document.head.appendChild(style);

  // Every page has this exact heading at the very top -- previously only
  // the Papers page did, with every other page showing just its own name
  // (e.g. "Venues") as the sole <h1> instead. Injected here (not hand-added
  // per page) so it's guaranteed identical everywhere: each page keeps its
  // own page-name heading as an <h2> right after the nav bar instead.
  // The mark is inlined (not <img src="logo.svg">) so it recolors for free
  // if the CSS variables it might reference ever change, and so there's no
  // extra request; it's decorative (aria-hidden), the adjacent text already
  // names the site for screen readers.
  const brand = document.createElement('h1');
  brand.className = 'site-brand';
  brand.innerHTML = `<svg viewBox="0 0 100 100" aria-hidden="true">
    <defs><linearGradient id="brand-g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#4f46e5"/><stop offset="1" stop-color="#f59e0b"/>
    </linearGradient></defs>
    <polygon points="34,48 66,48 72,55 72,61 28,61 28,55" fill="url(#brand-g)"/>
    <polygon points="13,63 87,63 91,69 91,75 9,75 9,69" fill="url(#brand-g)"/>
    <circle cx="28" cy="81" r="9" fill="#ffffff" stroke="#4f46e5" stroke-width="2.5"/>
    <circle cx="28" cy="81" r="2.2" fill="#f59e0b"/>
    <circle cx="72" cy="81" r="9" fill="#ffffff" stroke="#4f46e5" stroke-width="2.5"/>
    <circle cx="72" cy="81" r="2.2" fill="#f59e0b"/>
    <circle cx="50" cy="42" r="7" fill="#ffffff"/>
    <circle cx="50" cy="42" r="7" fill="none" stroke="#f59e0b" stroke-width="2"/>
    <circle cx="50" cy="42" r="3" fill="#4f46e5"/>
  </svg><span>AV Atlas</span>`;

  const current = (location.pathname.split('/').pop() || 'index.html');
  const nav = document.createElement('nav');
  nav.className = 'topbar';
  nav.innerHTML = PAGES.map(p =>
    `<a href="${p.href}"${p.href === current ? ' class="active"' : ''}>${p.label}</a>`
  ).join('');

  // One line for the version and the data date, e.g. "Version 0.1.2 · data
  // as of 25 Sep 2026". Only the About page shows it; the nav bar carries no
  // version, so it reads the same in every release. The version goes up with
  // every published build; the date is stats.json's content_updated, the day
  // the papers or counts last changed (see content_hash in aggregate.py), so it can stay the same
  // across several versions. One line keeps the two from reading as
  // conflicting "last updated" dates. The month is spelled out by hand so it
  // reads the same in every browser locale. Just the version when the date
  // is missing or malformed (never the build date); '' without a version.
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  window.siteVersionText = function (version, contentUpdated) {
    if (!/^\d+\.\d+\.\d+$/.test(version || '')) return '';
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(contentUpdated || '');
    if (!m || !MONTHS[Number(m[2]) - 1]) return `Version ${version}`;
    return `Version ${version} \u00b7 data as of ${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}`;
  };

  // Full page reset. stats.json and the shared JS files are static files
  // behind GitHub Pages' CDN, so a plain refresh can serve a stale cached
  // copy after a redeploy; this clears any Cache API entries and reloads
  // with a cache-busting query param. It also drops every filter/sort/
  // search query param and clears the remembered dropdown state in
  // sessionStorage, so the page comes back exactly as it looks on a first
  // visit rather than keeping the controls where they were left.
  //
  // The one exception is a detail page's identity param -- author.html/
  // institution.html/venue.html key on ?name=, paper.html on ?title=.
  // That's not a filter, it's WHICH record the page is, so dropping it here
  // would turn a valid link into "Unknown author" (reported). Those
  // two keys are carried across the reset; everything else still goes.
  const IDENTITY_PARAMS = ['name', 'title'];
  const reloadBtn = document.createElement('button');
  reloadBtn.id = 'hardReload';
  reloadBtn.type = 'button';
  reloadBtn.title = 'Reset filters and reload, bypassing the browser cache';
  reloadBtn.setAttribute('aria-label', 'Reset filters and reload the page');
  reloadBtn.textContent = '↻';
  reloadBtn.addEventListener('click', async () => {
    reloadBtn.disabled = true;
    reloadBtn.textContent = '…';
    try {
      if (window.caches && caches.keys) {
        const keys = await caches.keys();
        await Promise.all(keys.map(k => caches.delete(k)));
      }
    } catch (e) {
      console.warn('Could not clear caches:', e && e.message);
    }
    try { sessionStorage.clear(); } catch (e) { /* ignore */ }
    const u = new URL(location.origin + location.pathname);
    const prev = new URLSearchParams(location.search);
    IDENTITY_PARAMS.forEach(k => {
      const val = prev.get(k);
      if (val) u.searchParams.set(k, val);
    });
    u.searchParams.set('v', Date.now().toString(36));
    location.replace(u.toString());
  });
  nav.appendChild(reloadBtn);

  // "Spot an error" -- a no-backend way to report a bad data point (wrong
  // affiliation, garbled title, mis-attributed citation, ...). Builds a
  // mailto: link (opens the visitor's own mail client, nothing sent from
  // here) pre-filled with the page they were on; the issue text and an
  // optional reply-to email are theirs to fill in. No server, no stored
  // data, no CI needed to stand this up -- appropriate for a static site
  // with no backend of its own.
  const reportBtn = document.createElement('button');
  reportBtn.id = 'reportIssueBtn';
  reportBtn.type = 'button';
  // Icon only (requested, was "🚩 Spot an error?") -- the accessible
  // name still comes through via title (and aria-label below), so a
  // screen reader still announces the full label even though sighted
  // users only see the flag.
  reportBtn.title = 'Report an error on this page';
  reportBtn.setAttribute('aria-label', 'Report an error on this page');
  reportBtn.textContent = '🚩';
  nav.appendChild(reportBtn);

  // Built via createElement, not an innerHTML template -- same reasoning as
  // renderSwatchLabel in filters.js, plus this form's own content (the
  // reader's issue text) would otherwise round-trip through innerHTML too.
  const overlay = document.createElement('div');
  overlay.className = 'report-issue-overlay';
  const modal = document.createElement('div');
  modal.className = 'report-issue-modal';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-label', 'Report an error');

  const heading = document.createElement('h3');
  heading.textContent = 'Spot an error?';
  modal.appendChild(heading);

  const intro = document.createElement('p');
  intro.textContent = "This opens your email client with a message pre-filled to the site maintainer, nothing is sent from here. Use it for a data correction, to claim or disown papers filed under your name, or for a privacy takedown request (e.g. to have your name, affiliation, or photo removed).";
  modal.appendChild(intro);

  const pageLabel = document.createElement('label');
  pageLabel.setAttribute('for', 'report-issue-page');
  pageLabel.textContent = 'Page';
  modal.appendChild(pageLabel);

  const pageField = document.createElement('input');
  pageField.id = 'report-issue-page';
  pageField.type = 'text';
  modal.appendChild(pageField);

  const detailLabel = document.createElement('label');
  detailLabel.setAttribute('for', 'report-issue-detail');
  detailLabel.textContent = "What's wrong?";
  modal.appendChild(detailLabel);

  const detailField = document.createElement('textarea');
  detailField.id = 'report-issue-detail';
  detailField.placeholder = 'e.g. this institution is wrong, this citation looks off, this title is garbled...';
  modal.appendChild(detailField);

  const emailLabel = document.createElement('label');
  emailLabel.setAttribute('for', 'report-issue-email');
  emailLabel.textContent = "Your email (optional, if you'd like a reply)";
  modal.appendChild(emailLabel);

  const emailField = document.createElement('input');
  emailField.id = 'report-issue-email';
  emailField.type = 'email';
  emailField.placeholder = 'you@example.com';
  modal.appendChild(emailField);

  // An optional ORCID, for the one correction this site cannot verify on its
  // own: "these papers are mine" / "these papers are not mine".
  //
  // Name-only matching merges people who share a name and splits people whose
  // name is spelled several ways, and no amount of crawling fixes that from
  // the outside -- the person themselves is the authority. An ORCID makes
  // such a claim checkable without this site needing accounts, logins, or any
  // stored personal data: the corpus already carries ORCIDs for some authors
  // (fetch_orcids.py), so a claim can be matched against what is on file.
  // Entirely optional, and only useful for identity corrections.
  const orcidLabel = document.createElement('label');
  orcidLabel.setAttribute('for', 'report-issue-orcid');
  orcidLabel.textContent = 'Your ORCID (optional, if this is about your own papers)';
  modal.appendChild(orcidLabel);

  const orcidField = document.createElement('input');
  orcidField.id = 'report-issue-orcid';
  orcidField.type = 'text';
  orcidField.placeholder = '0000-0000-0000-0000';
  modal.appendChild(orcidField);

  const actions = document.createElement('div');
  actions.className = 'report-issue-actions';
  const cancelBtn = document.createElement('button');
  cancelBtn.type = 'button';
  cancelBtn.className = 'cancel-btn';
  cancelBtn.textContent = 'Cancel';
  actions.appendChild(cancelBtn);
  const sendBtn = document.createElement('button');
  sendBtn.type = 'button';
  sendBtn.className = 'send-btn';
  sendBtn.textContent = 'Open email';
  actions.appendChild(sendBtn);
  modal.appendChild(actions);

  overlay.appendChild(modal);
  document.body.appendChild(overlay);

  function closeReportModal() {
    overlay.classList.remove('open');
    detailField.value = '';
    emailField.value = '';
    orcidField.value = '';
  }
  reportBtn.addEventListener('click', () => {
    pageField.value = location.href;
    overlay.classList.add('open');
    detailField.focus();
  });
  // Escape closes the dialog, Tab stays inside it, and focus goes back to
  // the flag button, so the form works from the keyboard alone.
  overlay.addEventListener('keydown', ev => {
    if (ev.key === 'Escape') {
      closeReportModal();
      reportBtn.focus();
    } else if (ev.key === 'Tab') {
      const items = [pageField, detailField, emailField, orcidField, cancelBtn, sendBtn];
      const first = items[0], last = items[items.length - 1];
      if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
      else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
    }
  });
  cancelBtn.addEventListener('click', () => { closeReportModal(); reportBtn.focus(); });
  overlay.addEventListener('click', ev => { if (ev.target === overlay) closeReportModal(); });
  sendBtn.addEventListener('click', () => {
    const detail = detailField.value.trim();
    const replyTo = emailField.value.trim();
    const orcid = orcidField.value.trim();
    const subject = `AV Atlas: issue on ${document.title || location.pathname}`;
    const bodyLines = [
      `Page: ${pageField.value.trim() || location.href}`,
      '',
      detail || '(no description entered)',
    ];
    if (orcid) bodyLines.push('', `ORCID: ${orcid}`);
    if (replyTo) bodyLines.push('', `Reply-to: ${replyTo}`);
    const mailto = `mailto:h.caesar@tudelft.nl?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(bodyLines.join('\n'))}`;
    window.location.href = mailto;
    closeReportModal();
  });

  const target = document.getElementById('topnav');
  if (target) {
    target.parentNode.insertBefore(brand, target);
    target.replaceWith(nav);
  } else {
    document.body.insertBefore(nav, document.body.firstChild.nextSibling);
    document.body.insertBefore(brand, nav);
  }

  // Landmarks. Pages are plain markup with the nav bar on top, so the page
  // content is gathered into one <main> here, once, instead of hand-editing
  // every page. A skip link jumps to it and a footer closes the page. The
  // movable content is every element after the nav bar except scripts,
  // styles and the report dialog.
  function addLandmarks() {
    const body = document.body;
    const kids = Array.prototype.slice.call(body.children);
    const navIdx = kids.indexOf(nav);
    if (navIdx < 0 || kids.some(k => k.id === 'main-content')) return;
    const movable = kids.slice(navIdx + 1).filter(k =>
      !/^(SCRIPT|STYLE|NOSCRIPT)$/.test(k.tagName) && k !== overlay);
    const main = document.createElement('main');
    main.id = 'main-content';
    main.setAttribute('tabindex', '-1');
    body.insertBefore(main, movable[0] || overlay);
    movable.forEach(k => main.appendChild(k));

    const skip = document.createElement('a');
    skip.className = 'skip-link';
    skip.href = '#main-content';
    skip.textContent = 'Skip to content';
    skip.addEventListener('click', ev => { ev.preventDefault(); main.focus(); main.scrollIntoView(); });
    body.insertBefore(skip, body.firstChild);

    const footer = document.createElement('footer');
    footer.className = 'site-footer';
    const about = document.createElement('a');
    about.href = 'about.html';
    about.textContent = 'About AV Atlas';
    footer.appendChild(about);
    const info = document.createElement('span');
    footer.appendChild(info);
    body.insertBefore(footer, overlay);
    // BUILD_INFO.json is a few hundred bytes, unlike stats.json.
    if (typeof fetch === 'function') {
      fetch('BUILD_INFO.json').then(r => (r.ok ? r.json() : null)).then(b => {
        if (b) info.textContent = window.siteVersionText(b.version, b.content_updated);
      }).catch(() => {});
    }
  }
  if (typeof document.querySelector === 'function') addLandmarks();


  // Page view counts (GoatCounter, site code av-atlas). It sets no cookies
  // and keeps only per-page totals, which is why it replaced Google
  // Analytics (see DECISIONS.md). Only on the production site -- the staging
  // preview lives on the same host under /av-atlas-staging/, so the path
  // prefix is what tells them apart. localhost and forks on other hosts send
  // nothing.
  //
  // The script is GoatCounter's frozen v5 build with the SRI hash they
  // publish at goatcounter.com/help/countjs-versions, so the browser refuses
  // to run it if the file on their CDN ever changes. Every page's CSP allows
  // the script host and the count endpoint; tests/nav.test.js checks that
  // both still match what's here.
  const GOATCOUNTER = {
    endpoint: 'https://av-atlas.goatcounter.com/count',
    script: 'https://gc.zgo.at/count.v5.js',
    integrity: 'sha384-atnOLvQb9t+jTSipvd75X2yginT4PjVbqDdlJAmxMm+wYElFmeR6EmLP5bYeoRVQ',
  };
  if (location.hostname === 'nightrome.github.io' &&
      location.pathname.startsWith('/av-atlas/')) {
    // Count a detail page per record (author.html?name=...), but drop filter,
    // sort and the reload button's ?v= cache-buster, which would otherwise
    // split one page into a separate entry for every combination.
    window.goatcounter = {
      path() {
        const prev = new URLSearchParams(location.search);
        const kept = new URLSearchParams();
        IDENTITY_PARAMS.forEach(k => {
          const val = prev.get(k);
          if (val) kept.set(k, val);
        });
        const query = kept.toString();
        return location.pathname + (query ? '?' + query : '');
      },
    };
    const s = document.createElement('script');
    s.async = true;
    s.src = GOATCOUNTER.script;
    s.integrity = GOATCOUNTER.integrity;
    s.crossOrigin = 'anonymous';
    s.setAttribute('data-goatcounter', GOATCOUNTER.endpoint);
    document.head.appendChild(s);
  }
})();
