#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stamps data/labeling_candidates.json into a self-contained HTML labeling
tool (data embedded inline so it works by just opening the file -- no
server, no fetch()/CORS issue with file:// URLs). One card at a time: title,
venue/year, abstract with matched AV terms highlighted, three buttons
(Core / Adjacent / Skip). Progress is saved to localStorage as you go, so
closing the tab mid-session doesn't lose anything; an Export button
downloads labels.json when done (or partway through -- only labeled items
are included).

Writes av-atlas/dev/label_relevance.html (gitignored -- it's a generated
dev tool, not a page anyone should ever deploy).

Usage: python build_labeling_tool.py
"""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CANDIDATES_FILE = BASE / "data" / "labeling_candidates.json"
OUT_FILE = BASE / "dev" / "label_relevance.html"

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>AV relevance — labeling tool</title>
<style>
  :root {
    --bg: #0b0f19; --panel: #141a29; --panel2: #1b2333; --border: #26304a;
    --text: #e6ebf5; --muted: #8b95ac; --accent: #5eead4; --accent2: #818cf8;
    --av: #4ade80; --non-av: #f97373;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; background: var(--bg); color: var(--text);
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    display: flex; flex-direction: column; align-items: center; padding: 28px 16px 60px;
  }
  .wrap { width: 100%; max-width: 720px; }
  .topbar {
    display: flex; justify-content: space-between; align-items: baseline;
    margin-bottom: 18px; gap: 12px; flex-wrap: wrap;
  }
  h1 { font-size: 18px; margin: 0; }
  .progress { font-size: 13px; color: var(--muted); font-variant-numeric: tabular-nums; }
  .bar { height: 4px; background: var(--panel2); border-radius: 2px; overflow: hidden; margin-bottom: 22px; }
  .bar-fill { height: 100%; background: var(--accent); transition: width .15s; }

  .card {
    background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
    padding: 22px 24px; margin-bottom: 18px;
  }
  .meta { font-size: 12px; color: var(--muted); margin-bottom: 6px; }
  .title { font-size: 16px; font-weight: 700; margin: 0 0 12px; line-height: 1.35; }
  .abstract { font-size: 13.5px; line-height: 1.6; color: var(--text); }
  .abstract mark { background: rgba(94, 234, 212, 0.22); color: var(--text); border-radius: 3px; padding: 0 2px; }
  .terms { margin-top: 12px; font-size: 11.5px; color: var(--muted); }
  .terms code { background: var(--panel2); padding: 1px 5px; border-radius: 4px; margin-right: 4px; }

  .actions { display: flex; gap: 10px; margin-top: 20px; }
  button {
    flex: 1; padding: 12px 14px; border-radius: 8px; border: 1px solid var(--border);
    background: var(--panel2); color: var(--text); font-size: 13.5px; font-weight: 700;
    cursor: pointer; transition: transform .08s, border-color .12s;
  }
  button:hover { transform: translateY(-1px); }
  button:active { transform: translateY(0); }
  #btnAV { border-color: var(--av); color: var(--av); }
  #btnNonAV { border-color: var(--non-av); color: var(--non-av); }
  #btnSkip { color: var(--muted); flex: 0.5; }
  .hint { text-align: center; font-size: 11px; color: var(--muted); margin-top: 10px; }

  .toolbar { display: flex; gap: 10px; margin-top: 8px; }
  .toolbar button { flex: none; padding: 8px 14px; font-size: 12.5px; }
  #btnExport { border-color: var(--accent2); color: var(--accent2); }
  #btnReset { color: var(--muted); }

  .done { text-align: center; padding: 60px 20px; color: var(--muted); }
  .done strong { color: var(--text); }
</style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <h1>Is this paper AV-relevant?</h1>
    <span class="progress" id="progress"></span>
  </div>
  <div class="bar"><div class="bar-fill" id="barFill"></div></div>

  <div id="cardArea"></div>

  <div class="toolbar">
    <button id="btnExport">Export labels.json</button>
    <button id="btnReset">Reset all progress</button>
  </div>
</div>

<script>
const CANDIDATES = __CANDIDATES_JSON__;
const STORAGE_KEY = "av_relevance_labels_v1";

function loadLabels() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {}; }
  catch (e) { return {}; }
}
function saveLabels(labels) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(labels));
}

let labels = loadLabels();
let order = CANDIDATES.map(c => c.id).filter(id => !(id in labels));

function highlight(abstract, terms) {
  let html = abstract || "";
  const sorted = [...terms].sort((a, b) => b.length - a.length);
  sorted.forEach(t => {
    const re = new RegExp("(" + t.replace(/[.*+?^${}()|[\\]\\\\]/g, "\\\\$&") + ")", "ig");
    html = html.replace(re, "<mark>$1</mark>");
  });
  return html;
}

function render() {
  const area = document.getElementById("cardArea");
  const total = CANDIDATES.length;
  const done = total - order.length;
  document.getElementById("progress").textContent = done + " / " + total + " labeled";
  document.getElementById("barFill").style.width = (100 * done / total) + "%";

  if (!order.length) {
    area.innerHTML = '<div class="done"><strong>All done.</strong><br>Click "Export labels.json" below to save your work.</div>';
    return;
  }

  const c = CANDIDATES.find(x => x.id === order[0]);
  area.innerHTML = `
    <div class="card">
      <div class="meta">${c.venue || "?"} ${c.year || ""} · current heuristic weight: ${c.current_weight}</div>
      <p class="title">${c.title}</p>
      <p class="abstract">${highlight(c.abstract, c.matched_terms)}</p>
      <div class="terms">matched terms: ${c.matched_terms.map(t => "<code>" + t + "</code>").join(" ")}</div>
      <div class="actions">
        <button id="btnAV" title="C">✓ Core — genuinely about AVs</button>
        <button id="btnNonAV" title="A">✗ Adjacent — not really about AVs</button>
        <button id="btnSkip" title="S">Skip</button>
      </div>
      <p class="hint">keyboard: A = AV, N = non-AV, S = skip</p>
    </div>
  `;
  document.getElementById("btnAV").onclick = () => label("AV");
  document.getElementById("btnNonAV").onclick = () => label("non-AV");
  document.getElementById("btnSkip").onclick = () => skip();
}

function label(value) {
  const id = order.shift();
  labels[id] = value;
  saveLabels(labels);
  render();
}
function skip() {
  order.push(order.shift());
  render();
}

document.addEventListener("keydown", e => {
  if (!order.length) return;
  if (e.key.toLowerCase() === "c") label("AV");
  else if (e.key.toLowerCase() === "a") label("non-AV");
  else if (e.key.toLowerCase() === "s") skip();
});

document.getElementById("btnExport").onclick = () => {
  const out = CANDIDATES
    .filter(c => c.id in labels)
    .map(c => ({ id: c.id, title: c.title, current_weight: c.current_weight, label: labels[c.id] }));
  const blob = new Blob([JSON.stringify(out, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "relevance_labels.json";
  a.click();
};
document.getElementById("btnReset").onclick = () => {
  if (!confirm("Discard all labeling progress on this device?")) return;
  localStorage.removeItem(STORAGE_KEY);
  labels = {};
  order = CANDIDATES.map(c => c.id);
  render();
};

render();
</script>
</body>
</html>
"""


def main():
    candidates = json.loads(CANDIDATES_FILE.read_text(encoding="utf-8"))
    html = TEMPLATE.replace("__CANDIDATES_JSON__", json.dumps(candidates, ensure_ascii=False))
    # dev/ isn't tracked wholesale (the generated tool inside it is
    # gitignored), so a fresh clone may not have the directory yet.
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(html, encoding="utf-8", newline="\n")
    print(f"Wrote {OUT_FILE} with {len(candidates)} candidates embedded")


if __name__ == "__main__":
    main()
