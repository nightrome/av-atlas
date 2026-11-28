// The paper map: every AV paper is a sphere on a canvas, and papers whose titles and abstracts read
// alike sit close together. It loads map/points.json lazily and is used in two ways:
//
//   Full map (Papers page, section.pm-root#map with the markup in index.html):
//     PaperMap.mount(sectionEl)        wire up the section; points load when it scrolls into view
//     PaperMap.setPapers(allPapers)    stats.all_papers, for the card and the author/institution search
//     PaperMap.setFilterMatches(list)  the papers matching the filter bar (null = no filter active);
//                                      they are highlighted and everything else fades
//
//   Locked map (author and institution pages):
//     PaperMap.mountLocked(el, { titles, papers, heading, intro, noun })
//                                      builds its own panel inside el once el is near the viewport and
//                                      only if at least 3 of the titles are on the map. The titles are
//                                      highlighted, everything else is faint, and the view starts
//                                      zoomed to them.
//
// Clicking a sphere selects it and opens a small card; clicking empty space closes the card.
(function () {
  'use strict';

  const STYLE = `
    .pm-root .controls-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 14px; margin-bottom: 12px; font-size: 0.85em; color: var(--muted); }
    .pm-root .controls-row select, .pm-root .controls-row input[type="text"] {
      background: var(--panel2); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 4px 8px; font-size: 0.95em; }
    .pm-root .controls-row input[type="text"] { width: 240px; max-width: 100%; }
    .pm-root .controls-row select { max-width: 100%; }
    .pm-root .controls-row button, .pm-root .zoombtns button { background: var(--panel2); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 4px 10px; cursor: pointer; }
    .pm-root .controls-row label.chk { display: inline-flex; align-items: center; gap: 5px; cursor: pointer; }
    .pm-root .hl-box { display: inline-flex; align-items: center; gap: 4px; max-width: 100%; }
    .pm-root .hl-box button { padding: 4px 8px; }
    .pm-root .hl-box button[hidden] { display: none; }
    .pm-root .topn { display: inline-flex; align-items: center; gap: 6px; }
    .pm-root .topn input[type="range"] { width: 120px; }
    .pm-root .topn output { min-width: 4.5em; color: var(--text); font-variant-numeric: tabular-nums; }
    .pm-root .pm-help { margin: 0 0 10px; font-size: 0.82em; color: var(--muted); }
    .pm-root .pm-help summary { cursor: pointer; display: inline-block; }
    .pm-root .pm-help p { margin: 6px 0 0; line-height: 1.5; }
    .pm-root .map-wrap { position: relative; border: 1px solid var(--border); border-radius: 8px; background: var(--panel2); overflow: hidden; }
    #pm-canvas { display: block; width: 100%; cursor: grab; touch-action: pan-y; }
    #pm-canvas.dragging { cursor: grabbing; }
    #pm-canvas.over { cursor: pointer; }
    #pm-canvas:focus-visible { outline: 3px solid var(--accent); outline-offset: -3px; }
    #pm-tip { position: absolute; pointer-events: none; display: none; max-width: 340px; background: var(--panel); color: var(--text);
      border: 1px solid var(--border); border-radius: 8px; padding: 6px 10px; font-size: 0.82em; line-height: 1.35; box-shadow: 0 2px 8px rgba(0,0,0,0.25); }
    #pm-tip .t { font-weight: 600; }
    #pm-tip .m { color: var(--muted); }
    #pm-card { position: absolute; display: none; width: 300px; max-width: calc(100% - 16px); box-sizing: border-box; background: var(--panel); color: var(--text);
      border: 1px solid var(--border); border-radius: 10px; padding: 10px 34px 10px 12px; font-size: 0.85em; line-height: 1.4; box-shadow: 0 4px 16px rgba(0,0,0,0.3); }
    #pm-card .t { font-weight: 600; margin-bottom: 4px; }
    #pm-card .m { color: var(--muted); }
    #pm-card a.open { display: inline-block; margin-top: 8px; font-weight: 600; }
    #pm-card button.x { position: absolute; top: 4px; right: 4px; width: 28px; height: 28px; border: 0; background: transparent; color: var(--muted); font-size: 1.3em; line-height: 1; cursor: pointer; border-radius: 6px; }
    #pm-card button.x:hover { color: var(--text); background: var(--panel2); }
    .pm-root .zoombtns { position: absolute; top: 8px; right: 8px; display: flex; flex-direction: column; gap: 4px; }
    .pm-root .zoombtns button { width: 34px; height: 34px; padding: 0; font-size: 1.15em; line-height: 1; box-shadow: 0 1px 4px rgba(0,0,0,0.2); }
    @media (pointer: coarse), (max-width: 700px) {
      .pm-root .zoombtns button { width: 44px; height: 44px; }
      .pm-root .controls-row button, .pm-root .controls-row select, .pm-root .controls-row input[type=text] { min-height: 44px; font-size: 16px; }
      .pm-root .controls-row .chk { min-height: 44px; display: inline-flex; align-items: center; }
      #pm-card button.x { width: 40px; height: 40px; top: 0; right: 0; }
      #pm-card a.open { padding: 8px 0; }
    }
    .pm-root canvas:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
    #pm-mini { position: absolute; right: 8px; bottom: 8px; border: 1px solid var(--border); border-radius: 6px; background: var(--panel);
      opacity: 0.92; cursor: pointer; touch-action: none; box-shadow: 0 1px 4px rgba(0,0,0,0.2); }
    #pm-hint { position: absolute; left: 8px; bottom: 8px; pointer-events: none; background: var(--panel); color: var(--muted); border: 1px solid var(--border);
      border-radius: 6px; padding: 3px 9px; font-size: 0.78em; opacity: 0; transition: opacity 0.4s; }
    #pm-hint.show { opacity: 0.95; }
    .pm-root .status { color: var(--muted); font-size: 0.82em; margin-top: 8px; }
    @media (max-width: 600px) {
      .pm-root .map-wrap { margin: 0 -8px; border-left: 0; border-right: 0; border-radius: 0; }
      .pm-root .hl-box { width: 100%; }
      .pm-root .controls-row input[type="text"] { flex: 1; min-width: 0; width: auto; }
    }`;

  const FALLBACK = ['#4f46e5', '#f59e0b', '#10b981', '#ef4444', '#06b6d4', '#a855f7', '#84cc16', '#ec4899',
                    '#0ea5e9', '#f97316', '#14b8a6', '#6366f1', '#eab308', '#22c55e', '#d946ef', '#64748b'];
  const catColor = i => FALLBACK[i % FALLBACK.length];
  const WORLD = 10000;               // points live in a 0..10000 square
  const K_MIN = 0.12, K_MAX = 60;    // zoom limits (1 = whole map fits the canvas)
  const CELL = 200, GRID_N = 52;     // hit-test grid
  const TOP_STEPS = [100, 250, 500, 1000, 2500, 5000, 10000];
  const SPHERE_MIN = 3.5;            // from this radius (px) up, dots are shaded spheres, below that flat
  const R_MAX = 16;                  // biggest sphere radius (px)

  // The state the URL carries (full map only): mz/mx/my (view), mcat (category), mq (author or institution text).
  const URL_KEYS = ['mz', 'mx', 'my', 'mcat', 'mq'];

  let started = false, mounted = false;
  let pendingPapers = null, pendingMatches = null, pendingMatchesSet = false;
  let api = null;
  let pointsPromise = null;

  function loadPoints() {
    if (!pointsPromise) {
      pointsPromise = fetch('map/points.json').then(r => { if (!r.ok) throw new Error(r.status); return r.json(); });
      pointsPromise.catch(() => { pointsPromise = null; });
    }
    return pointsPromise;
  }

  function addStyle() {
    if (document.getElementById('pm-style')) return;
    const style = document.createElement('style');
    style.id = 'pm-style';
    style.textContent = STYLE;
    document.head.appendChild(style);
  }

  // Run fn once el is near the viewport (or straight away without IntersectionObserver).
  function whenNear(el, margin, fn) {
    if (typeof IntersectionObserver === 'function') {
      const io = new IntersectionObserver(entries => {
        if (entries.some(e => e.isIntersecting)) { io.disconnect(); fn(); }
      }, { rootMargin: margin + 'px 0px' });
      io.observe(el);
    } else fn();
  }

  function mount(section) {
    if (mounted || !section || typeof section.querySelector !== 'function' || !section.querySelector('#pm-canvas')) return;
    mounted = true;
    addStyle();
    whenNear(section, 150, () => { if (started) return; started = true; start(section, {}); });
  }

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  const LOCKED_HTML = `
    <div class="controls-row">
      <button type="button" id="pm-reset" title="Zoom to these papers">Fit to papers</button>
      <button type="button" id="pm-whole" title="Show the whole map">Whole map</button>
    </div>
    <div class="map-wrap">
      <canvas id="pm-canvas" tabindex="0" role="img"></canvas>
      <div class="zoombtns">
        <button type="button" id="pm-zin" aria-label="Zoom in" title="Zoom in">+</button>
        <button type="button" id="pm-zout" aria-label="Zoom out" title="Zoom out">&minus;</button>
      </div>
      <canvas id="pm-mini" aria-hidden="true" title="Overview: click or drag to move the view"></canvas>
      <div id="pm-hint" aria-hidden="true"></div>
      <div id="pm-tip"></div>
    </div>
    <div class="status" id="pm-status" aria-live="polite"></div>`;

  // A map locked to one author's or institution's papers. `host` is an empty element in the page;
  // the panel is only added (and the points only fetched) when the host is near the viewport.
  function mountLocked(host, opts) {
    if (!host || !opts || !opts.titles || opts.titles.length < 3 || host.dataset.pmMounted) return;
    host.dataset.pmMounted = '1';
    addStyle();
    whenNear(host, 700, () => {
      loadPoints().then(D => {
        const want = new Set(opts.titles), locked = new Set();
        for (let i = 0; i < D.titles.length; i++) if (want.has(D.titles[i])) locked.add(i);
        if (locked.size < 3) return; // too few on the map to be worth a panel
        const panel = el('div', 'panel pm-root pm-locked');
        const row = el('div', 'panel-title-row');
        row.appendChild(el('h2', null, opts.heading || 'Paper map'));
        panel.appendChild(row);
        const intro = el('p', 'section-intro', opts.intro || 'Highlighted dots are the papers; faint dots are all other AV papers.');
        intro.style.cssText = 'color:var(--muted);font-size:0.88em;margin:0 0 10px;line-height:1.5;';
        panel.appendChild(intro);
        const body = el('div');
        body.innerHTML = LOCKED_HTML;
        panel.appendChild(body);
        body.querySelector('#pm-canvas').setAttribute('aria-label',
          'Map of autonomous vehicle papers. The highlighted dots are the papers listed on this page. Arrow keys pan, plus and minus zoom, zero fits the papers; Enter selects the dot nearest the middle.');
        host.appendChild(panel);
        start(panel, { locked, data: D, papers: opts.papers || [], noun: opts.noun || 'papers' });
      }).catch(() => { /* no map in this build: leave the page as it is */ });
    });
  }

  function start(section, opts) {
    const locked = opts.locked || null;
    const $ = id => section.querySelector('#' + id) || Object.assign(document.createElement('div'), { value: '' });
    const cv = $('pm-canvas'), ctx = cv.getContext('2d');
    const tip = $('pm-tip'), hintEl = $('pm-hint'), statusEl = $('pm-status');
    const mini = $('pm-mini'), mctx = mini.getContext('2d');
    const input = $('pm-highlight'), clearBtn = $('pm-hl-clear');
    const catSel = $('pm-cat'), labelChk = $('pm-labels');
    const topRange = $('pm-top'), topOut = $('pm-top-val');
    const wrap = cv.parentElement;
    statusEl.textContent = 'Loading the map…';

    // The card that opens when a paper is clicked.
    const card = el('div'); card.id = 'pm-card'; card.setAttribute('role', 'region'); card.setAttribute('aria-label', 'Selected paper');
    const cardX = el('button', 'x', '×'); cardX.type = 'button'; cardX.setAttribute('aria-label', 'Close'); cardX.title = 'Close';
    const cardT = el('div', 't'), cardM = el('div', 'm'), cardM2 = el('div', 'm');
    const cardLink = el('a', 'open', 'Open paper →'); cardLink.target = '_blank'; cardLink.rel = 'noopener';
    card.append(cardX, cardT, cardM, cardM2, cardLink);
    wrap.appendChild(card);

    const params = new URLSearchParams(location.search);
    let D = null, N = 0, W = 0, H = 0, dpr = 1, fit = 1, grid = null;
    let view = { k: 1, tx: 0, ty: 0 };
    let onlyCat = null, topN = Infinity, showLabels = true;
    let textSet = null, filterSet = null, hiSet = locked, hover = -1, selected = -1;
    let rank = null, drawOrder = null, regions = [], miniBase = null, miniSize = 120;
    let paperMeta = null, papers = opts.papers || pendingPapers;
    let colors = { panel: '#fff', text: '#111', accent: '#4f46e5' };
    const sprites = new Map();
    const narrow = () => W <= 600;
    const coarse = !!(window.matchMedia && matchMedia('(pointer: coarse)').matches);
    const sx = x => x * fit * view.k + view.tx;
    const sy = y => y * fit * view.k + view.ty;
    const label = i => (typeof categoryLabel === 'function' ? categoryLabel(D.cats[i]) : D.cats[i]);

    function readColors() {
      const cs = getComputedStyle(document.documentElement);
      colors = {
        panel: cs.getPropertyValue('--panel').trim() || '#fff',
        text: getComputedStyle(document.body).color || '#111',
        accent: cs.getPropertyValue('--accent').trim() || '#4f46e5',
      };
    }

    function visible(i) {
      return (onlyCat == null || D.cat[i] === onlyCat) && rank[i] < topN;
    }

    function clampView() {
      // Free panning, but never lose the whole map: at least a strip of it stays on screen.
      const s = WORLD * fit * view.k, m = Math.min(60, s);
      view.tx = Math.min(W - m, Math.max(m - s, view.tx));
      view.ty = Math.min(H - m, Math.max(m - s, view.ty));
    }
    function resize() {
      dpr = window.devicePixelRatio || 1;
      W = wrap.clientWidth;
      H = locked ? Math.max(340, Math.min(560, Math.round(W * 0.6))) : Math.max(420, Math.min(820, Math.round(W * 0.62)));
      cv.width = W * dpr; cv.height = H * dpr; cv.style.height = H + 'px';
      miniSize = narrow() ? 84 : 120;
      mini.width = miniSize * dpr; mini.height = miniSize * dpr;
      mini.style.width = miniSize + 'px'; mini.style.height = miniSize + 'px';
      fit = Math.min(W, H) / WORLD;
      sprites.clear();
      readColors(); miniBase = null; clampView(); draw();
    }
    function wholeView() {
      view.k = 1; view.tx = (W - WORLD * fit) / 2; view.ty = (H - WORLD * fit) / 2; viewChanged(); draw();
    }
    // Locked map: zoom to the bounding box of the highlighted papers, with some room around it.
    function fitLocked() {
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (const i of locked) {
        x0 = Math.min(x0, D.x[i]); x1 = Math.max(x1, D.x[i]);
        y0 = Math.min(y0, D.y[i]); y1 = Math.max(y1, D.y[i]);
      }
      const bw = (x1 - x0) * 1.3 + 500, bh = (y1 - y0) * 1.3 + 500;
      view.k = Math.max(0.6, Math.min(25, Math.min(W / (fit * bw), H / (fit * bh))));
      view.tx = W / 2 - (x0 + x1) / 2 * fit * view.k; view.ty = H / 2 - (y0 + y1) / 2 * fit * view.k;
      clampView(); draw();
    }
    const resetView = () => (locked ? fitLocked() : wholeView());

    // ---- topic labels: the dominant category per grid cell, merged into regions ----
    function computeRegions() {
      const G = 25, cs = WORLD / G, C = D.cats.length;
      const cnt = new Int32Array(G * G * C), tot = new Int32Array(G * G);
      const sumX = new Float64Array(G * G * C), sumY = new Float64Array(G * G * C);
      const cell = i => Math.min(G - 1, Math.max(0, Math.floor(D.y[i] / cs))) * G + Math.min(G - 1, Math.max(0, Math.floor(D.x[i] / cs)));
      for (let i = 0; i < N; i++) {
        const c = cell(i), k = c * C + D.cat[i];
        cnt[k]++; tot[c]++; sumX[k] += D.x[i]; sumY[k] += D.y[i];
      }
      const dom = new Int16Array(G * G).fill(-1);
      for (let c = 0; c < G * G; c++) {
        if (tot[c] < 4) continue;
        let b = 0;
        for (let k = 1; k < C; k++) if (cnt[c * C + k] > cnt[c * C + b]) b = k;
        if (cnt[c * C + b] / tot[c] >= 0.3) dom[c] = b;
      }
      const seen = new Uint8Array(G * G);
      regions = [];
      for (let c0 = 0; c0 < G * G; c0++) {
        if (dom[c0] < 0 || seen[c0]) continue;
        const cat = dom[c0], stack = [c0];
        let n = 0, x = 0, y = 0, cells = 0;
        seen[c0] = 1;
        while (stack.length) {
          const c = stack.pop(), r = Math.floor(c / G), q = c % G;
          const k = c * C + cat;
          n += cnt[k]; x += sumX[k]; y += sumY[k]; cells++;
          for (let dr = -1; dr <= 1; dr++) for (let dq = -1; dq <= 1; dq++) {
            const r2 = r + dr, q2 = q + dq;
            if (r2 < 0 || q2 < 0 || r2 >= G || q2 >= G) continue;
            const c2 = r2 * G + q2;
            if (!seen[c2] && dom[c2] === cat) { seen[c2] = 1; stack.push(c2); }
          }
        }
        if (n >= Math.max(80, N * 0.004)) regions.push({ cat, n, x: x / n, y: y / n });
      }
      regions.sort((a, b) => b.n - a.n);
      // A category split into several islands is named once, on its biggest, plus again on any
      // island that is nearly as big.
      const first = {};
      regions = regions.filter(r => {
        if (first[r.cat] == null) { first[r.cat] = r.n; return true; }
        return r.n >= 0.6 * first[r.cat];
      });
    }
    function drawLabels() {
      if (!showLabels || !regions.length || view.k > 9 || view.k < 0.4) return;
      const fade = view.k < 5 ? 1 : Math.max(0, (9 - view.k) / 4);
      const maxN = regions[0].n, boxes = [];
      const zoomScale = Math.min(1.5, Math.max(0.75, Math.pow(view.k, 0.25)));
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.lineJoin = 'round';
      ctx.lineWidth = 4; ctx.strokeStyle = colors.panel; ctx.fillStyle = colors.text;
      for (const r of regions) {
        if (onlyCat != null && r.cat !== onlyCat) continue;
        const fs = Math.round((11 + 8 * Math.sqrt(r.n / maxN)) * zoomScale * (narrow() ? 0.7 : 1));
        const text = label(r.cat);
        ctx.font = `600 ${fs}px system-ui, -apple-system, Segoe UI, sans-serif`;
        const w = ctx.measureText(text).width, x = sx(r.x), y = sy(r.y);
        if (x + w / 2 < 0 || x - w / 2 > W || y < -fs || y > H + fs) continue;
        const box = [x - w / 2 - 3, y - fs / 2 - 2, x + w / 2 + 3, y + fs / 2 + 2];
        if (boxes.some(b => box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1])) continue;
        boxes.push(box);
        ctx.globalAlpha = (locked ? 0.55 : 0.9) * fade;
        ctx.strokeText(text, x, y); ctx.fillText(text, x, y);
      }
      ctx.globalAlpha = 1;
    }

    // ---- drawing ----
    // Sphere radius in px: tiny when the whole map is in view, growing steadily as the user zooms in.
    function dotRadius() {
      const k = view.k;
      const r = k < 1 ? Math.max(0.8, 1.1 * Math.sqrt(k)) : Math.min(R_MAX, 1.1 * Math.pow(k, 0.85));
      return r + (narrow() ? 0.6 : 0);
    }
    // The highlighted papers of a locked map never get smaller than a few px, fewer papers get bigger spheres.
    const hiRadius = () => (locked ? Math.max(dotRadius(), locked.size > 300 ? 3 : locked.size > 80 ? 5 : 8) : dotRadius());
    // A shaded sphere is drawn once per colour and size, then stamped with drawImage.
    function sphere(color, r) {
      const key = color + '|' + r;
      let c = sprites.get(key);
      if (c) return c;
      const size = Math.ceil(2 * r + 3);
      c = document.createElement('canvas');
      c.width = size * dpr; c.height = size * dpr;
      const g = c.getContext('2d');
      g.setTransform(dpr, 0, 0, dpr, size * dpr / 2, size * dpr / 2);
      g.beginPath(); g.arc(0, 0, r, 0, Math.PI * 2); g.fillStyle = color; g.fill();
      let gr = g.createRadialGradient(-r * 0.4, -r * 0.4, r * 0.05, -r * 0.2, -r * 0.2, r * 1.05);
      gr.addColorStop(0, 'rgba(255,255,255,0.85)'); gr.addColorStop(0.45, 'rgba(255,255,255,0.12)'); gr.addColorStop(1, 'rgba(255,255,255,0)');
      g.fillStyle = gr; g.fill();
      gr = g.createRadialGradient(0, 0, r * 0.55, 0, 0, r);
      gr.addColorStop(0, 'rgba(0,0,0,0)'); gr.addColorStop(1, 'rgba(0,0,0,0.38)');
      g.fillStyle = gr; g.fill();
      g.beginPath(); g.arc(0, 0, r - 0.5, 0, Math.PI * 2);
      g.lineWidth = 1; g.strokeStyle = 'rgba(15,23,42,0.5)'; g.stroke();
      sprites.set(key, c);
      return c;
    }
    function stamp(i, x, y, r) {
      const rb = Math.round(r * 2) / 2, s = sphere(catColor(D.cat[i]), rb), size = s.width / dpr;
      ctx.drawImage(s, x - size / 2, y - size / 2, size, size);
    }
    const onScreen = (x, y, m) => x >= -m && y >= -m && x <= W + m && y <= H + m;

    function draw() {
      if (!D) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      const rf = dotRadius(), r = hiRadius(), dim = hiSet != null, shaded = r >= SPHERE_MIN, m = r + 4;
      // Cheapest first: everything that is not highlighted, as flat faint dots.
      // Drawing runs from the least to the most cited, so well-cited papers end up on top.
      for (let pass = 0; pass < (dim ? 2 : 1); pass++) {
        const top = dim && pass === 1;
        const flat = !top ? true : !shaded;
        ctx.globalAlpha = dim && pass === 0 ? (locked ? 0.22 : 0.13) : (top ? 1 : 0.9);
        let lastFill = null;
        for (let n = 0; n < N; n++) {
          const i = drawOrder[n];
          if (!visible(i)) continue;
          if (dim && (hiSet.has(i) ? 1 : 0) !== pass) continue;
          const x = sx(D.x[i]), y = sy(D.y[i]);
          if (!onScreen(x, y, m)) continue;
          if (!flat || (shaded && !dim)) { stamp(i, x, y, r); continue; }
          const fill = dim && pass === 0 ? '#9ca3af' : catColor(D.cat[i]);
          if (fill !== lastFill) { ctx.fillStyle = fill; lastFill = fill; }
          const rr = top ? Math.max(r + 1, 2.5) : rf;
          if (rr < 1.7) ctx.fillRect(x - rr, y - rr, rr * 2, rr * 2);
          else { ctx.beginPath(); ctx.arc(x, y, rr, 0, Math.PI * 2); ctx.fill(); }
        }
      }
      ctx.globalAlpha = 1;
      if (dim && hiSet.size > 0 && hiSet.size < 200 && r < 6) {
        // Few matches and small dots: a ring so they are easy to spot without zooming.
        ctx.strokeStyle = colors.accent; ctx.lineWidth = 2;
        for (const i of hiSet) {
          if (!visible(i)) continue;
          const x = sx(D.x[i]), y = sy(D.y[i]);
          if (!onScreen(x, y, 20)) continue;
          ctx.beginPath(); ctx.arc(x, y, r + 7, 0, Math.PI * 2); ctx.stroke();
        }
      }
      drawLabels();
      // Hovered and selected papers come to the front with a ring.
      for (const [i, ring, w] of [[hover, colors.text, 2], [selected, colors.accent, 3]]) {
        if (i < 0 || !visible(i)) continue;
        const x = sx(D.x[i]), y = sy(D.y[i]);
        if (shaded) stamp(i, x, y, r);
        else { ctx.fillStyle = catColor(D.cat[i]); ctx.beginPath(); ctx.arc(x, y, r + 1, 0, Math.PI * 2); ctx.fill(); }
        ctx.strokeStyle = ring; ctx.lineWidth = w;
        ctx.beginPath(); ctx.arc(x, y, Math.max(r, 3) + 3, 0, Math.PI * 2); ctx.stroke();
      }
      placeCard();
      drawMini();
    }

    // ---- minimap ----
    function drawMini() {
      const m = miniSize, f = m / WORLD;
      if (!miniBase) {
        miniBase = document.createElement('canvas');
        miniBase.width = m * dpr; miniBase.height = m * dpr;
        const b = miniBase.getContext('2d');
        b.setTransform(dpr, 0, 0, dpr, 0, 0);
        b.globalAlpha = 0.8;
        for (let i = 0; i < N; i++) {
          if (!visible(i)) continue;
          const on = !hiSet || hiSet.has(i);
          b.fillStyle = on ? catColor(D.cat[i]) : '#cbd5e1';
          const s = locked && on ? 3 : 1.4;
          b.fillRect(D.x[i] * f - (s - 1.4) / 2, D.y[i] * f - (s - 1.4) / 2, s, s);
        }
      }
      mctx.setTransform(1, 0, 0, 1, 0, 0);
      mctx.clearRect(0, 0, mini.width, mini.height);
      mctx.drawImage(miniBase, 0, 0);
      mctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const x0 = (0 - view.tx) / (fit * view.k) * f, y0 = (0 - view.ty) / (fit * view.k) * f;
      const w = W / (fit * view.k) * f, h = H / (fit * view.k) * f;
      mctx.lineWidth = 1.5; mctx.strokeStyle = colors.accent; mctx.fillStyle = 'rgba(79,70,229,0.12)';
      mctx.beginPath(); mctx.rect(x0, y0, w, h); mctx.fill(); mctx.stroke();
    }
    function miniTo(e) {
      const r = mini.getBoundingClientRect();
      const wx = (e.clientX - r.left) / miniSize * WORLD, wy = (e.clientY - r.top) / miniSize * WORLD;
      view.tx = W / 2 - wx * fit * view.k; view.ty = H / 2 - wy * fit * view.k;
      clampView(); hideTip(); viewChanged(); draw();
    }
    let miniDrag = false;
    mini.addEventListener('pointerdown', e => { miniDrag = true; mini.setPointerCapture(e.pointerId); miniTo(e); e.preventDefault(); });
    mini.addEventListener('pointermove', e => { if (miniDrag) miniTo(e); });
    mini.addEventListener('pointerup', () => { miniDrag = false; });
    mini.addEventListener('pointercancel', () => { miniDrag = false; });

    // ---- hit testing ----
    // A grid of 200-unit cells over the map. A click looks only at the cells around the pointer.
    function buildGrid() {
      grid = new Map();
      for (let i = 0; i < N; i++) {
        const key = Math.floor(D.x[i] / CELL) * GRID_N + Math.floor(D.y[i] / CELL);
        (grid.get(key) || grid.set(key, []).get(key)).push(i);
      }
    }
    // The paper under (px, py). A sphere the pointer is inside wins, and where spheres overlap the
    // one drawn on top (the most cited) wins. Otherwise the nearest dot within a forgiving reach:
    // 12 px for fingers and narrow screens, 9 px for a mouse.
    function nearest(px, py) {
      const wx = (px - view.tx) / (fit * view.k), wy = (py - view.ty) / (fit * view.k);
      const r = hiRadius(), reachPx = Math.max(r + 2, coarse || narrow() ? 12 : 9);
      const reach = reachPx / (fit * view.k);
      const span = Math.ceil(reach / CELL), cx = Math.floor(wx / CELL), cy = Math.floor(wy / CELL);
      if (span > 40) return -1; // zoomed far out: too many cells to scan, and dots are tiny anyway
      const inside = r / (fit * view.k);
      let best = -1, bd = Infinity, bestInside = -1, bestPos = -1;
      for (let a = cx - span; a <= cx + span; a++) for (let b = cy - span; b <= cy + span; b++) {
        const list = grid.get(a * GRID_N + b);
        if (!list) continue;
        for (const i of list) {
          if (!visible(i) || (locked && !locked.has(i))) continue;
          const dx = D.x[i] - wx, dy = D.y[i] - wy, d = dx * dx + dy * dy;
          if (d <= inside * inside) {
            if (drawPos[i] > bestPos) { bestPos = drawPos[i]; bestInside = i; }
          } else if (d < reach * reach && d < bd) { bd = d; best = i; }
        }
      }
      return bestInside >= 0 ? bestInside : best;
    }
    let drawPos = null;

    // ---- tooltip (mouse hover) and card (click) ----
    let lastPx = 0, lastPy = 0;
    function hideTip() { tip.style.display = 'none'; cv.classList.remove('over'); if (hover !== -1) { hover = -1; draw(); } }
    function metaOf(i) {
      if (!paperMeta) paperMeta = new Map((papers || []).map(p => [p.title, p]));
      return paperMeta.get(D.titles[i]);
    }
    function venueLine(i) {
      const p = metaOf(i);
      return p ? (p.venue || p.short_name || '') : '';
    }
    function showTip(px, py) {
      if (hover < 0 || hover === selected) { tip.style.display = 'none'; return; }
      const i = hover;
      tip.innerHTML = '';
      tip.append(el('div', 't', D.titles[i]));
      tip.append(el('div', 'm', [D.year[i] || null, label(D.cat[i]), `${D.cites[i].toLocaleString()} citations`].filter(Boolean).join(' · ')));
      tip.style.display = 'block';
      tip.style.left = Math.max(0, Math.min(px + 14, W - tip.offsetWidth - 4)) + 'px';
      tip.style.top = Math.max(0, Math.min(py + 14, H - tip.offsetHeight - 4)) + 'px';
    }
    function updateHover(px, py) {
      lastPx = px; lastPy = py;
      const i = D ? nearest(px, py) : -1;
      if (i !== hover) { hover = i; draw(); }
      cv.classList.toggle('over', i >= 0);
      showTip(px, py);
    }
    function select(i) {
      if (i === selected) return;
      selected = i;
      if (i >= 0) {
        tip.style.display = 'none';
        cardT.textContent = D.titles[i];
        cardM.textContent = [venueLine(i) || null, D.year[i] || null].filter(Boolean).join(' · ');
        cardM.style.display = cardM.textContent ? '' : 'none';
        cardM2.textContent = `${D.cites[i].toLocaleString()} citation${D.cites[i] === 1 ? '' : 's'} · ${label(D.cat[i])}`;
        cardLink.href = `paper.html?title=${encodeURIComponent(D.titles[i])}`;
        card.style.display = 'block';
      } else card.style.display = 'none';
      draw();
    }
    // The card sits beside the selected sphere (or at the top or bottom of a narrow map) and
    // follows it while the map is panned.
    function placeCard() {
      if (selected < 0) return;
      const x = sx(D.x[selected]), y = sy(D.y[selected]), cw = card.offsetWidth, ch = card.offsetHeight, gap = hiRadius() + 12;
      if (narrow()) {
        card.style.left = '8px'; card.style.right = '8px'; card.style.width = 'auto';
        if (y > H / 2) { card.style.top = '8px'; card.style.bottom = 'auto'; }
        else { card.style.bottom = (miniSize + 16) + 'px'; card.style.top = 'auto'; }
        return;
      }
      card.style.right = 'auto'; card.style.bottom = 'auto'; card.style.width = '';
      const left = x + gap + cw < W - 50 ? x + gap : x - gap - cw;
      card.style.left = Math.max(4, Math.min(left, W - cw - 4)) + 'px';
      card.style.top = Math.max(4, Math.min(y - ch / 2, H - ch - 4)) + 'px';
    }
    cardX.addEventListener('click', () => { select(-1); cv.focus(); });
    card.addEventListener('keydown', e => { if (e.key === 'Escape') { select(-1); cv.focus(); } });

    // ---- view changes ----
    let urlTimer = null;
    function viewChanged() { if (!locked) { clearTimeout(urlTimer); urlTimer = setTimeout(syncURL, 400); } }
    function zoomAt(factor, px, py) {
      const k2 = Math.max(K_MIN, Math.min(K_MAX, view.k * factor)), f = k2 / view.k;
      view.tx = px - (px - view.tx) * f; view.ty = py - (py - view.ty) * f; view.k = k2;
      clampView(); hideHint(true); viewChanged(); draw();
    }
    function panBy(dx, dy) { view.tx += dx; view.ty += dy; clampView(); viewChanged(); draw(); }

    let hintTimer = null, hintDone = false;
    function showHint() {
      if (hintDone) return;
      hintEl.textContent = coarse ? 'Pinch or use + and − to zoom' : 'Ctrl + scroll to zoom';
      hintEl.classList.add('show');
      clearTimeout(hintTimer); hintTimer = setTimeout(() => hideHint(false), 4000);
    }
    function hideHint(permanent) {
      hintEl.classList.remove('show'); clearTimeout(hintTimer);
      if (permanent) hintDone = true;
    }

    // Pointers: one finger or the mouse pans, two fingers pinch-zoom, a tap or click selects.
    const pointers = new Map();
    let drag = null, moved = false, pinch = null;
    const rectPos = e => { const r = cv.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    function pinchState() {
      const [a, b] = [...pointers.values()];
      return { d: Math.hypot(a.x - b.x, a.y - b.y) || 1, mx: (a.x + b.x) / 2, my: (a.y + b.y) / 2 };
    }
    cv.addEventListener('pointerdown', e => {
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      cv.setPointerCapture(e.pointerId);
      if (pointers.size === 1) {
        drag = { x: e.clientX, y: e.clientY, tx: view.tx, ty: view.ty }; moved = false; pinch = null;
        cv.classList.add('dragging');
      } else if (pointers.size === 2) {
        drag = null; moved = true; pinch = pinchState();
      }
    });
    cv.addEventListener('pointermove', e => {
      const [px, py] = rectPos(e);
      if (pointers.has(e.pointerId)) pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pinch && pointers.size >= 2) {
        const r = cv.getBoundingClientRect(), n = pinchState();
        view.tx += n.mx - pinch.mx; view.ty += n.my - pinch.my;
        zoomAt(n.d / pinch.d, n.mx - r.left, n.my - r.top);
        pinch = n; hideTip(); return;
      }
      if (drag) {
        const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
        if (Math.abs(dx) + Math.abs(dy) > 5) moved = true;
        if (moved) { view.tx = drag.tx + dx; view.ty = drag.ty + dy; clampView(); hideTip(); viewChanged(); draw(); }
        return;
      }
      if (e.pointerType === 'touch') return;
      updateHover(px, py);
    });
    function endPointer(e, cancelled) {
      pointers.delete(e.pointerId);
      if (pointers.size === 1) {
        const [p] = [...pointers.values()];
        drag = { x: p.x, y: p.y, tx: view.tx, ty: view.ty }; pinch = null; return;
      }
      if (pointers.size > 0) return;
      cv.classList.remove('dragging');
      const wasClick = drag && !moved && !cancelled;
      drag = null; pinch = null;
      if (wasClick && D) {
        const [px, py] = rectPos(e);
        select(nearest(px, py)); // -1 (empty space) closes the card
      }
    }
    cv.addEventListener('pointerup', e => endPointer(e, false));
    cv.addEventListener('pointercancel', e => endPointer(e, true));
    cv.addEventListener('pointerleave', e => { if (e.pointerType !== 'touch') hideTip(); });
    cv.addEventListener('wheel', e => {
      if (!(e.ctrlKey || e.metaKey)) { showHint(); return; } // a plain wheel scrolls the page
      e.preventDefault();
      const [px, py] = rectPos(e);
      zoomAt(Math.exp(-e.deltaY * 0.0015), px, py);
      updateHover(px, py);
    }, { passive: false });
    cv.addEventListener('keydown', e => {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const step = 80, cx = W / 2, cy = H / 2;
      let handled = true;
      switch (e.key) {
        case 'ArrowLeft': panBy(step, 0); break;
        case 'ArrowRight': panBy(-step, 0); break;
        case 'ArrowUp': panBy(0, step); break;
        case 'ArrowDown': panBy(0, -step); break;
        case '+': case '=': zoomAt(1.4, cx, cy); break;
        case '-': case '_': zoomAt(1 / 1.4, cx, cy); break;
        case '0': case 'Home': resetView(); break;
        case 'Escape': select(-1); break;
        case 'Enter': case ' ': {
          // The paper under the centre of the map, the same one that is ringed.
          if (D) { const i = nearest(cx, cy); if (i >= 0) select(i); }
          break;
        }
        default: handled = false;
      }
      if (handled) {
        e.preventDefault();
        // Keyboard users have no pointer, so the middle of the map stands in for it:
        // the dot nearest the centre is ringed, and Enter opens its card.
        if (e.key === 'Enter' || e.key === ' ' || e.key === 'Escape') return;
        hideTip();
        updateHover(cx, cy);
      }
    });
    $('pm-zin').addEventListener('click', () => { zoomAt(1.5, W / 2, H / 2); hideTip(); });
    $('pm-zout').addEventListener('click', () => { zoomAt(1 / 1.5, W / 2, H / 2); hideTip(); });
    $('pm-whole').addEventListener('click', () => { wholeView(); });

    // ---- highlight: filter-bar matches from the host, plus an author/institution text search ----
    function recomputeHi() {
      if (locked) return;
      if (!filterSet && !textSet) hiSet = null;
      else if (filterSet && textSet) hiSet = new Set([...textSet].filter(i => filterSet.has(i)));
      else hiSet = filterSet || textSet;
      miniBase = null; updateStatus(); draw();
    }
    function updateStatus() {
      if (locked) {
        statusEl.textContent = `${locked.size.toLocaleString()} ${opts.noun} highlighted of ${N.toLocaleString()} on the map.`;
        return;
      }
      let s = `${N.toLocaleString()} papers.`;
      if (onlyCat != null) s = `Only ${label(onlyCat)}. ` + s;
      if (topN !== Infinity) s = `Top ${topN.toLocaleString()} by citations. ` + s;
      if (hiSet) s = (hiSet.size ? `${hiSet.size.toLocaleString()} highlighted. ` : 'Nothing matches. ') + s;
      statusEl.textContent = s;
    }
    function setFilter(list) {
      if (!D) return;
      if (!list) { filterSet = null; return recomputeHi(); }
      const titles = new Set(list.map(p => p.title)), s = new Set();
      for (let i = 0; i < N; i++) if (titles.has(D.titles[i])) s.add(i);
      filterSet = s; recomputeHi();
    }
    let exactTimer = null;
    function runSearch() {
      const q = input.value.trim();
      clearBtn.hidden = input.value === '';
      if (q.length < 3) { textSet = null; return recomputeHi(); }
      const s = new Set();
      for (let i = 0; i < N; i++) {
        const p = metaOf(i);
        if (p && matchesSearchQuery([...(p.authors || []), ...(p.institutions || [])].join(' | '), q)) s.add(i);
      }
      textSet = s; recomputeHi();
    }
    input.addEventListener('input', () => {
      clearBtn.hidden = input.value === '';
      clearTimeout(exactTimer);
      exactTimer = setTimeout(() => { runSearch(); syncURL(); }, 250);
    });
    clearBtn.addEventListener('click', () => {
      clearTimeout(exactTimer); input.value = ''; clearBtn.hidden = true; textSet = null; recomputeHi(); syncURL(); input.focus();
    });

    // ---- the other options ----
    catSel.addEventListener('change', () => {
      const v = catSel.value; onlyCat = v === '' ? null : Number(v);
      if (selected >= 0 && !visible(selected)) select(-1);
      miniBase = null; hideTip(); updateStatus(); draw(); syncURL();
    });
    labelChk.addEventListener('change', () => { showLabels = labelChk.checked; draw(); });
    function topLabel() { return topN === Infinity ? 'All papers' : `Top ${topN.toLocaleString()}`; }
    topRange.addEventListener('input', () => {
      const v = Number(topRange.value);
      topN = v >= steps.length ? Infinity : steps[v];
      topOut.textContent = topLabel();
      if (selected >= 0 && !visible(selected)) select(-1);
      miniBase = null; hideTip(); updateStatus(); draw();
    });
    $('pm-reset').addEventListener('click', () => {
      if (locked) return fitLocked();
      onlyCat = null; catSel.value = ''; topN = Infinity; topRange.value = String(steps.length);
      topOut.textContent = topLabel(); input.value = ''; clearBtn.hidden = true; textSet = null;
      showLabels = true; labelChk.checked = true;
      recomputeHi(); wholeView(); syncURL();
    });
    window.addEventListener('resize', resize);
    let steps = TOP_STEPS;

    // ---- URL state (full map only; replaceState, other parameters and the hash are left alone) ----
    function syncURL() {
      if (!D || locked) return;
      const q = new URLSearchParams(location.search);
      URL_KEYS.forEach(k => q.delete(k));
      if (onlyCat != null) q.set('mcat', D.cats[onlyCat]);
      if (input.value.trim()) q.set('mq', input.value.trim());
      if (Math.abs(view.k - 1) > 0.001) {
        q.set('mz', view.k.toFixed(2));
        q.set('mx', Math.round((W / 2 - view.tx) / (fit * view.k)));
        q.set('my', Math.round((H / 2 - view.ty) / (fit * view.k)));
      }
      const s = q.toString();
      try { history.replaceState(null, '', location.pathname + (s ? '?' + s : '') + location.hash); } catch (e) { /* ignore */ }
    }

    // ---- load ----
    (opts.data ? Promise.resolve(opts.data) : loadPoints()).then(d => {
      D = d; N = d.titles.length;
      rank = new Int32Array(N);
      const order = Array.from({ length: N }, (_, i) => i).sort((a, b) => d.cites[b] - d.cites[a]);
      order.forEach((i, r) => { rank[i] = r; });
      // Least cited first, so the best-cited spheres are drawn last (on top).
      drawOrder = Int32Array.from(order).reverse();
      drawPos = new Int32Array(N);
      drawOrder.forEach((i, n) => { drawPos[i] = n; });
      steps = TOP_STEPS.filter(s => s < N);
      topRange.max = String(steps.length); topRange.value = String(steps.length);
      topOut.textContent = topLabel();
      d.cats.forEach((c, i) => {
        const o = document.createElement('option');
        o.value = String(i); o.textContent = label(i); catSel.appendChild(o);
      });
      buildGrid(); computeRegions();
      if (locked) {
        resize(); fitLocked();
        updateStatus(); draw();
        return;
      }
      const ci = d.cats.indexOf(params.get('mcat'));
      if (ci >= 0) { onlyCat = ci; catSel.value = String(ci); }
      resize(); wholeView();
      const z = parseFloat(params.get('mz')), zx = parseFloat(params.get('mx')), zy = parseFloat(params.get('my'));
      if (z > 0 && isFinite(zx) && isFinite(zy)) {
        view.k = Math.max(K_MIN, Math.min(K_MAX, z));
        view.tx = W / 2 - zx * fit * view.k; view.ty = H / 2 - zy * fit * view.k; clampView();
      }
      const q = params.get('mq');
      if (q) { input.value = q; clearBtn.hidden = false; runSearch(); }
      if (pendingMatchesSet) setFilter(pendingMatches);
      updateStatus(); draw();
      setTimeout(showHint, 600);
    }).catch(() => { statusEl.textContent = 'The paper map is not available in this build.'; });

    if (!locked) {
      api = {
        setFilter,
        setPapers() { paperMeta = null; papers = pendingPapers; if (D && input.value.trim()) runSearch(); },
      };
    }
  }

  let matchTimer = null;
  window.PaperMap = {
    mount,
    mountLocked,
    setPapers(list) { pendingPapers = list; if (api) api.setPapers(); },
    setFilterMatches(list) {
      pendingMatches = list; pendingMatchesSet = true;
      clearTimeout(matchTimer);
      matchTimer = setTimeout(() => { if (api) api.setFilter(pendingMatches); }, 200);
    },
  };
})();
