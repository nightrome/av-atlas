// The paper map section of the Papers page: every AV paper is a dot, and papers whose titles and
// abstracts read alike sit close together. index.html holds the markup (section#map); this file
// loads map/points.json when the section scrolls into view and draws it on a canvas.
//
// Host API (all optional, all safe to call before the map has loaded):
//   PaperMap.mount(sectionEl)        wire up the section; points load lazily
//   PaperMap.setPapers(allPapers)    stats.all_papers, for the tooltip and the author/institution search
//   PaperMap.setFilterMatches(list)  the papers matching the filter bar (null = no filter active);
//                                    they are highlighted and everything else fades
(function () {
  'use strict';

  const STYLE = `
    #map .controls-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 14px; margin-bottom: 12px; font-size: 0.85em; color: var(--muted); }
    #map .controls-row select, #map .controls-row input[type="text"] {
      background: var(--panel2); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 4px 8px; font-size: 0.95em; }
    #map .controls-row input[type="text"] { width: 240px; max-width: 100%; }
    #map .controls-row button, #map .zoombtns button { background: var(--panel2); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 4px 10px; cursor: pointer; }
    #map .controls-row label.chk { display: inline-flex; align-items: center; gap: 5px; cursor: pointer; }
    #map .hl-box { display: inline-flex; align-items: center; gap: 4px; max-width: 100%; }
    #map .hl-box button { padding: 4px 8px; }
    #map .hl-box button[hidden] { display: none; }
    #map .topn { display: inline-flex; align-items: center; gap: 6px; }
    #map .topn input[type="range"] { width: 120px; }
    #map .topn output { min-width: 4.5em; color: var(--text); font-variant-numeric: tabular-nums; }
    #map .map-wrap { position: relative; border: 1px solid var(--border); border-radius: 8px; background: var(--panel2); overflow: hidden; }
    #pm-canvas { display: block; width: 100%; cursor: grab; touch-action: pan-y; }
    #pm-canvas.dragging { cursor: grabbing; }
    #pm-canvas:focus-visible { outline: 3px solid var(--accent); outline-offset: -3px; }
    #pm-tip { position: absolute; pointer-events: none; display: none; max-width: 340px; background: var(--panel); color: var(--text);
      border: 1px solid var(--border); border-radius: 8px; padding: 6px 10px; font-size: 0.82em; line-height: 1.35; box-shadow: 0 2px 8px rgba(0,0,0,0.25); }
    #pm-tip .t { font-weight: 600; }
    #pm-tip .m { color: var(--muted); }
    #map .zoombtns { position: absolute; top: 8px; right: 8px; display: flex; flex-direction: column; gap: 4px; }
    #map .zoombtns button { width: 34px; height: 34px; padding: 0; font-size: 1.15em; line-height: 1; box-shadow: 0 1px 4px rgba(0,0,0,0.2); }
    #pm-mini { position: absolute; right: 8px; bottom: 8px; border: 1px solid var(--border); border-radius: 6px; background: var(--panel);
      opacity: 0.92; cursor: pointer; touch-action: none; box-shadow: 0 1px 4px rgba(0,0,0,0.2); }
    #pm-hint { position: absolute; left: 8px; bottom: 8px; pointer-events: none; background: var(--panel); color: var(--muted); border: 1px solid var(--border);
      border-radius: 6px; padding: 3px 9px; font-size: 0.78em; opacity: 0; transition: opacity 0.4s; }
    #pm-hint.show { opacity: 0.95; }
    #map .status { color: var(--muted); font-size: 0.82em; margin-top: 8px; }
    @media (max-width: 600px) {
      #map .map-wrap { margin: 0 -8px; border-left: 0; border-right: 0; border-radius: 0; }
      #map .hl-box { width: 100%; }
      #map .controls-row input[type="text"] { flex: 1; min-width: 0; width: auto; }
    }`;

  const FALLBACK = ['#4f46e5', '#f59e0b', '#10b981', '#ef4444', '#06b6d4', '#a855f7', '#84cc16', '#ec4899',
                    '#0ea5e9', '#f97316', '#14b8a6', '#6366f1', '#eab308', '#22c55e', '#d946ef', '#64748b'];
  const catColor = i => FALLBACK[i % FALLBACK.length];
  const WORLD = 10000;               // points live in a 0..10000 square
  const K_MIN = 0.12, K_MAX = 60;    // zoom limits (1 = whole map fits the canvas)
  const CELL = 200, GRID_N = 52;     // hit-test grid
  const TOP_STEPS = [100, 250, 500, 1000, 2500, 5000, 10000];

  // The state the URL carries: mz/mx/my (view), mcat (category), mq (author or institution text).
  const URL_KEYS = ['mz', 'mx', 'my', 'mcat', 'mq'];

  let started = false, mounted = false;
  let pendingPapers = null, pendingMatches = null, pendingMatchesSet = false;
  let api = null;

  function mount(section) {
    if (mounted || !section || typeof section.querySelector !== 'function' || !section.querySelector('#pm-canvas')) return;
    mounted = true;
    const style = document.createElement('style');
    style.textContent = STYLE;
    document.head.appendChild(style);
    const begin = () => { if (started) return; started = true; start(section); };
    if (typeof IntersectionObserver === 'function') {
      const io = new IntersectionObserver(entries => {
        if (entries.some(e => e.isIntersecting)) { io.disconnect(); begin(); }
      }, { rootMargin: '400px 0px' });
      io.observe(section);
    } else begin();
  }

  function start(section) {
    const $ = id => section.querySelector('#' + id);
    const cv = $('pm-canvas'), ctx = cv.getContext('2d');
    const tip = $('pm-tip'), hintEl = $('pm-hint'), statusEl = $('pm-status');
    const mini = $('pm-mini'), mctx = mini.getContext('2d');
    const input = $('pm-highlight'), clearBtn = $('pm-hl-clear');
    const catSel = $('pm-cat'), sizeChk = $('pm-size'), labelChk = $('pm-labels');
    const topRange = $('pm-top'), topOut = $('pm-top-val');
    statusEl.textContent = 'Loading the map…';

    const params = new URLSearchParams(location.search);
    let D = null, N = 0, W = 0, H = 0, dpr = 1, fit = 1, grid = null;
    let view = { k: 1, tx: 0, ty: 0 };
    let onlyCat = null, topN = Infinity, sized = false, showLabels = true;
    let textSet = null, filterSet = null, hiSet = null, hover = -1;
    let rank = null, sizeF = null, regions = [], miniBase = null, miniSize = 120;
    let paperMeta = null, papers = null;
    let colors = { panel: '#fff', text: '#111', accent: '#4f46e5' };
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
      W = cv.parentElement.clientWidth;
      H = Math.max(420, Math.min(820, Math.round(W * 0.62)));
      cv.width = W * dpr; cv.height = H * dpr; cv.style.height = H + 'px';
      miniSize = narrow() ? 84 : 120;
      mini.width = miniSize * dpr; mini.height = miniSize * dpr;
      mini.style.width = miniSize + 'px'; mini.style.height = miniSize + 'px';
      fit = Math.min(W, H) / WORLD;
      readColors(); miniBase = null; clampView(); draw();
    }
    function resetView() {
      view.k = 1; view.tx = (W - WORLD * fit) / 2; view.ty = (H - WORLD * fit) / 2; viewChanged(); draw();
    }

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
        ctx.globalAlpha = 0.9 * fade;
        ctx.strokeText(text, x, y); ctx.fillText(text, x, y);
      }
      ctx.globalAlpha = 1;
    }

    // ---- drawing ----
    function baseSize() {
      return Math.max(1.6, Math.min(5, 1.4 + Math.log2(Math.max(view.k, 1)) * 0.7)) * (view.k < 1 ? Math.max(0.6, view.k + 0.1) : 1) + (narrow() ? 0.8 : 0);
    }
    function dotSize(i, base) { return sized ? base * sizeF[i] : base; }

    function draw() {
      if (!D) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      const base = baseSize(), dim = hiSet != null;
      for (let pass = 0; pass < (dim ? 2 : 1); pass++) {
        const top = dim && pass === 1;
        ctx.globalAlpha = dim && pass === 0 ? 0.13 : (top ? 1 : (sized ? 0.7 : 0.85));
        for (let i = 0; i < N; i++) {
          if (!visible(i)) continue;
          if (dim && (hiSet.has(i) ? 1 : 0) !== pass) continue;
          const x = sx(D.x[i]), y = sy(D.y[i]);
          if (x < -20 || y < -20 || x > W + 20 || y > H + 20) continue;
          const s = dotSize(i, base);
          ctx.fillStyle = dim && pass === 0 ? '#9ca3af' : catColor(D.cat[i]);
          if (top) {
            ctx.beginPath(); ctx.arc(x, y, s / 2 + 2, 0, Math.PI * 2); ctx.fill();
            ctx.lineWidth = 1.3; ctx.strokeStyle = 'rgba(17,24,39,0.95)'; ctx.stroke();
          } else if (sized) {
            ctx.beginPath(); ctx.arc(x, y, s / 2, 0, Math.PI * 2); ctx.fill();
          } else {
            ctx.fillRect(x - s / 2, y - s / 2, s, s);
          }
        }
      }
      ctx.globalAlpha = 1;
      if (dim && hiSet.size > 0 && hiSet.size < 200) {
        // Few matches: a big ring so they are easy to spot without zooming.
        ctx.strokeStyle = colors.accent; ctx.lineWidth = 2;
        for (const i of hiSet) {
          if (!visible(i)) continue;
          const x = sx(D.x[i]), y = sy(D.y[i]);
          if (x < -20 || y < -20 || x > W + 20 || y > H + 20) continue;
          ctx.beginPath(); ctx.arc(x, y, dotSize(i, base) / 2 + 8, 0, Math.PI * 2); ctx.stroke();
        }
      }
      drawLabels();
      if (hover >= 0) {
        ctx.strokeStyle = colors.text; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(sx(D.x[hover]), sy(D.y[hover]), dotSize(hover, base) / 2 + 4, 0, Math.PI * 2); ctx.stroke();
      }
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
          b.fillStyle = hiSet && !hiSet.has(i) ? '#cbd5e1' : catColor(D.cat[i]);
          b.fillRect(D.x[i] * f, D.y[i] * f, 1.4, 1.4);
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
    function buildGrid() {
      grid = new Map();
      for (let i = 0; i < N; i++) {
        const key = Math.floor(D.x[i] / CELL) * GRID_N + Math.floor(D.y[i] / CELL);
        (grid.get(key) || grid.set(key, []).get(key)).push(i);
      }
    }
    function nearest(px, py) {
      const wx = (px - view.tx) / (fit * view.k), wy = (py - view.ty) / (fit * view.k);
      const r = (narrow() ? 14 : 9) / (fit * view.k);
      const span = Math.ceil(r / CELL), cx = Math.floor(wx / CELL), cy = Math.floor(wy / CELL);
      if (span > 40) return -1; // zoomed far out: too many cells to scan, and dots are tiny anyway
      let best = -1, bd = r * r;
      for (let a = cx - span; a <= cx + span; a++) for (let b = cy - span; b <= cy + span; b++) {
        const list = grid.get(a * GRID_N + b);
        if (!list) continue;
        for (const i of list) {
          if (!visible(i)) continue;
          const dx = D.x[i] - wx, dy = D.y[i] - wy, d = dx * dx + dy * dy;
          if (d < bd) { bd = d; best = i; }
        }
      }
      return best;
    }

    // ---- tooltip ----
    let lastPx = 0, lastPy = 0;
    function hideTip() { tip.style.display = 'none'; if (hover !== -1) { hover = -1; draw(); } }
    function showTip(px, py) {
      if (hover < 0) { tip.style.display = 'none'; return; }
      const i = hover;
      tip.innerHTML = '';
      const t = document.createElement('div'); t.className = 't'; t.textContent = D.titles[i];
      const m = document.createElement('div'); m.className = 'm';
      m.textContent = [D.year[i] || null, label(D.cat[i]), `${D.cites[i].toLocaleString()} citations`].filter(Boolean).join(' · ');
      tip.append(t, m);
      const p = metaMap().get(D.titles[i]);
      if (p) {
        const v = p.venue || p.short_name, a = (p.authors || [])[0];
        const m2 = document.createElement('div'); m2.className = 'm';
        m2.textContent = [v, a ? (p.authors.length > 1 ? `${a} et al.` : a) : null].filter(Boolean).join(' · ');
        if (m2.textContent) tip.append(m2);
      }
      tip.style.display = 'block';
      tip.style.left = Math.max(0, Math.min(px + 14, W - tip.offsetWidth - 4)) + 'px';
      tip.style.top = Math.max(0, Math.min(py + 14, H - tip.offsetHeight - 4)) + 'px';
    }
    function updateHover(px, py) {
      lastPx = px; lastPy = py;
      const i = D ? nearest(px, py) : -1;
      if (i !== hover) { hover = i; draw(); }
      showTip(px, py);
    }
    function metaMap() {
      if (!paperMeta) paperMeta = new Map((papers || []).map(p => [p.title, p]));
      return paperMeta;
    }

    // ---- view changes ----
    let urlTimer = null;
    function viewChanged() { clearTimeout(urlTimer); urlTimer = setTimeout(syncURL, 400); }
    function zoomAt(factor, px, py) {
      const k2 = Math.max(K_MIN, Math.min(K_MAX, view.k * factor)), f = k2 / view.k;
      view.tx = px - (px - view.tx) * f; view.ty = py - (py - view.ty) * f; view.k = k2;
      clampView(); hideHint(true); viewChanged(); draw();
    }
    function panBy(dx, dy) { view.tx += dx; view.ty += dy; clampView(); viewChanged(); draw(); }

    let hintTimer = null, hintDone = false;
    function showHint() {
      if (hintDone) return;
      hintEl.textContent = coarse ? 'Pinch or use + and - to zoom' : 'Ctrl + scroll to zoom';
      hintEl.classList.add('show');
      clearTimeout(hintTimer); hintTimer = setTimeout(() => hideHint(false), 4000);
    }
    function hideHint(permanent) {
      hintEl.classList.remove('show'); clearTimeout(hintTimer);
      if (permanent) hintDone = true;
    }

    // Pointers: one finger or the mouse pans, two fingers pinch-zoom.
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
        if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
        view.tx = drag.tx + dx; view.ty = drag.ty + dy; clampView(); hideTip(); viewChanged(); draw(); return;
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
        const [px, py] = rectPos(e), i = nearest(px, py);
        if (i >= 0) {
          hideTip();
          window.open(`paper.html?title=${encodeURIComponent(D.titles[i])}`, '_blank', 'noopener');
        }
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
        default: handled = false;
      }
      if (handled) { e.preventDefault(); hideTip(); }
    });
    $('pm-zin').addEventListener('click', () => { zoomAt(1.5, W / 2, H / 2); hideTip(); });
    $('pm-zout').addEventListener('click', () => { zoomAt(1 / 1.5, W / 2, H / 2); hideTip(); });

    // ---- highlight: filter-bar matches from the host, plus an author/institution text search ----
    function recomputeHi() {
      if (!filterSet && !textSet) hiSet = null;
      else if (filterSet && textSet) hiSet = new Set([...textSet].filter(i => filterSet.has(i)));
      else hiSet = filterSet || textSet;
      miniBase = null; updateStatus(); draw();
    }
    function updateStatus() {
      let s = `${N.toLocaleString()} papers on the map.`;
      if (onlyCat != null) s = `Showing only ${label(onlyCat)}. ` + s;
      if (topN !== Infinity) s = `Showing the ${topN.toLocaleString()} most cited. ` + s;
      if (hiSet) s = (hiSet.size ? `${hiSet.size.toLocaleString()} papers matching the ${filterSet ? 'filters' : 'search'} highlighted. ` : 'No papers on the map match. ') + s;
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
        const p = metaMap().get(D.titles[i]);
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
      miniBase = null; hideTip(); updateStatus(); draw(); syncURL();
    });
    sizeChk.addEventListener('change', () => { sized = sizeChk.checked; draw(); });
    labelChk.addEventListener('change', () => { showLabels = labelChk.checked; draw(); });
    function topLabel() { return topN === Infinity ? 'All papers' : `Top ${topN.toLocaleString()}`; }
    topRange.addEventListener('input', () => {
      const v = Number(topRange.value);
      topN = v >= steps.length ? Infinity : steps[v];
      topOut.textContent = topLabel();
      miniBase = null; hideTip(); updateStatus(); draw();
    });
    $('pm-reset').addEventListener('click', () => {
      onlyCat = null; catSel.value = ''; topN = Infinity; topRange.value = String(steps.length);
      topOut.textContent = topLabel(); input.value = ''; clearBtn.hidden = true; textSet = null;
      sized = false; sizeChk.checked = false; showLabels = true; labelChk.checked = true;
      recomputeHi(); resetView(); syncURL();
    });
    window.addEventListener('resize', resize);
    let steps = TOP_STEPS;

    // ---- URL state (replaceState, other parameters and the hash are left alone) ----
    function syncURL() {
      if (!D) return;
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
    fetch('map/points.json').then(r => { if (!r.ok) throw new Error(r.status); return r.json(); }).then(d => {
      D = d; N = d.titles.length;
      rank = new Int32Array(N);
      const order = Array.from({ length: N }, (_, i) => i).sort((a, b) => d.cites[b] - d.cites[a]);
      order.forEach((i, r) => { rank[i] = r; });
      sizeF = new Float32Array(N);
      for (let i = 0; i < N; i++) sizeF[i] = Math.min(3.5, 0.8 + 0.3 * Math.log2(1 + d.cites[i]));
      steps = TOP_STEPS.filter(s => s < N);
      topRange.max = String(steps.length); topRange.value = String(steps.length);
      topOut.textContent = topLabel();
      d.cats.forEach((c, i) => {
        const o = document.createElement('option');
        o.value = String(i); o.textContent = label(i); catSel.appendChild(o);
      });
      const ci = d.cats.indexOf(params.get('mcat'));
      if (ci >= 0) { onlyCat = ci; catSel.value = String(ci); }
      buildGrid(); computeRegions(); resize(); resetView();
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

    api = {
      setFilter,
      setPapers() { paperMeta = null; papers = pendingPapers; if (D && input.value.trim()) runSearch(); },
    };
    papers = pendingPapers;
  }

  let matchTimer = null;
  window.PaperMap = {
    mount,
    setPapers(list) { pendingPapers = list; if (api) api.setPapers(); },
    setFilterMatches(list) {
      pendingMatches = list; pendingMatchesSet = true;
      clearTimeout(matchTimer);
      matchTimer = setTimeout(() => { if (api) api.setFilter(pendingMatches); }, 200);
    },
  };
})();
