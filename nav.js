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
    .site-brand { display: flex; align-items: center; gap: 9px; margin: 0 0 16px; }
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

  // Same cache-bypassing hard reload as the ski-resort app's ↻ button --
  // stats.json and the shared JS files are static files behind GitHub Pages'
  // CDN, so a plain refresh can still serve a stale cached copy after a
  // redeploy; this clears any Cache API entries and reloads with a
  // cache-busting query param to force a fresh fetch.
  const reloadBtn = document.createElement('button');
  reloadBtn.id = 'hardReload';
  reloadBtn.title = 'Reload, bypassing the browser cache';
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
    const u = new URL(location.href);
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
  // Icon only (user-requested, was "🚩 Spot an error?") -- the accessible
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
  modal.setAttribute('aria-label', 'Report an error');

  const heading = document.createElement('h3');
  heading.textContent = 'Spot an error?';
  modal.appendChild(heading);

  const intro = document.createElement('p');
  intro.textContent = "This opens your email client with a message pre-filled to the site maintainer, nothing is sent from here. Use it for a data correction, or for a privacy takedown request (e.g. to have your name, affiliation, or photo removed).";
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
  }
  reportBtn.addEventListener('click', () => {
    pageField.value = location.href;
    overlay.classList.add('open');
  });
  cancelBtn.addEventListener('click', closeReportModal);
  overlay.addEventListener('click', ev => { if (ev.target === overlay) closeReportModal(); });
  sendBtn.addEventListener('click', () => {
    const detail = detailField.value.trim();
    const replyTo = emailField.value.trim();
    const subject = `AV Atlas: issue on ${document.title || location.pathname}`;
    const bodyLines = [
      `Page: ${pageField.value.trim() || location.href}`,
      '',
      detail || '(no description entered)',
    ];
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
})();
