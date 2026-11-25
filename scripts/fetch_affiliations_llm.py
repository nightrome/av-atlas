#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Read author institutions off the first page of an arXiv PDF with a small LLM.

For AV papers that still have no institution but have an arXiv ID. Three
stages, so the slow part can run on another machine (night-desktop):

  --targets FILE   write the papers to look at (key, arXiv id, title, authors)
  --run FILE       for each target: download the PDF (one request per 3.5 s),
                   take the text of page 1 with pdftotext, ask `claude -p
                   --model haiku` for authors and institutions, check the
                   answer, store it. Needs only the standard library, pdftotext
                   and the claude CLI. Resumable: every paper that got an
                   answer is recorded as tried, so none is asked twice. A
                   download or CLI failure records nothing.
  --standardize FILE
                   reduce every distinct affiliation string in FILE to its parent
                   institution (the university or company, no department, address
                   or country) and a country code, 60 strings per claude call.
                   Run it where the claude CLI is. Resumable: strings already
                   mapped are never asked again.
  --apply FILE     fold the checked answers into papers_full.json, using the
                   standardized names (and normalize_institution from aggregate.py
                   so they land on the corpus's existing spellings)
  --list-new FILE  write the institutions that are new to the corpus, most papers
                   first, one name per line (--txt PATH)

An answer is kept only when it parses as JSON, names at least one author whose
last name is on the paper's known author list, and has institution strings of a
sensible length. Anything else is recorded as tried with the reason, and not
used. Results: data/affiliations_llm.json (--out).

Usage:
  python fetch_affiliations_llm.py --targets data/affiliations_llm_targets.json
  python fetch_affiliations_llm.py --run targets.json --out results.json --max-hours 6
  python fetch_affiliations_llm.py --apply data/affiliations_llm.json
Do not run --apply while another script is writing papers_full.json.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.request

PROMPT = ("Below is the first page of a research paper. List each author with their institution(s) as printed on "
          'the page. Answer with JSON only: [{"name": "...", "institutions": ["..."]}]. '
          "Use [] for institutions if none are printed.\n\n")
GAP = 3.5
MAX_CHARS = 6000
MIN_TEXT = 300
NAME_BATCH = 60
NAME_VERSION = 3 # bump when NAME_PROMPT changes in a way that makes earlier answers wrong
NAME_PROMPT = (
    "Each numbered line below is an author affiliation copied from a paper. For each, give the parent institution "
    "only: the university, company, research institute or government agency. Drop departments, schools, faculties, "
    "labs inside a university, street addresses, postal codes, cities, countries and footnote marks. Spell out "
    "abbreviations and drop a leading 'the' ('Massachusetts Institute of Technology', 'University of Michigan'). "
    "Give two spellings: \"name\" is the usual English name ('Delft University of Technology', 'Tsinghua "
    "University', 'ETH Zurich'); \"native\" is the institution's own official name in its own language when that "
    "is written in Latin script ('Politecnico di Milano', 'Universitat Autònoma de Barcelona', 'Technische "
    "Universität Dresden'), otherwise the same as \"name\". A company's research lab is just the company "
    "('Huawei'); 'Toyota Research Institute' stays as it is. Keep a campus when it is a distinct university "
    "('University of California, Berkeley'). Also give the ISO 3166-1 alpha-2 country code of the institution, "
    "from the line or from your knowledge, or '' if unsure. If a line is not an institution at all, use ''. "
    'Answer with JSON only: [{"i": 1, "name": "...", "native": "...", "country": "XX"}, ...] with one entry per '
    'line.\n\n')


def norm_title(t):
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "", t)


def last_names(authors):
    if isinstance(authors, str):
        authors = re.split(r",|;| and ", authors)
    out = set()
    for a in authors or []:
        parts = re.findall(r"[^\W\d_]+", unicodedata.normalize("NFKD", str(a)).encode("ascii", "ignore").decode().lower())
        if parts:
            out.add(parts[-1])
    return out


def parse_answer(text):
    text = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    try:
        data = json.loads(text)
    except ValueError:
        m = re.search(r"\[.*\]", text, re.S)
        try:
            data = json.loads(m.group(0)) if m else None
        except ValueError:
            data = None
    return data if isinstance(data, list) else None


def check_answer(data, known_authors):
    """(cleaned list or None, reason). Cleaned: [{"name", "affiliations"}]."""
    if data is None:
        return None, "not json"
    out = []
    for d in data:
        if not isinstance(d, dict) or not isinstance(d.get("name"), str) or not d["name"].strip():
            continue
        insts = [i.strip() for i in (d.get("institutions") or []) if isinstance(i, str) and 3 <= len(i.strip()) <= 250]
        out.append({"name": d["name"].strip(), "affiliations": insts})
    if not out:
        return None, "no authors"
    known = last_names(known_authors)
    if known and not (last_names([d["name"] for d in out]) & known):
        return None, "authors do not match"
    if not any(d["affiliations"] for d in out):
        return None, "no institutions"
    return out, "ok"


def page_text(pdf_bytes):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "p.pdf")
        with open(path, "wb") as f:
            f.write(pdf_bytes)
        r = subprocess.run(["pdftotext", "-f", "1", "-l", "1", path, "-"], capture_output=True, text=True, timeout=60)
    return r.stdout[:MAX_CHARS]


def download(arxiv_id):
    req = urllib.request.Request(f"https://arxiv.org/pdf/{arxiv_id}", headers={"User-Agent": "research-script"})
    return urllib.request.urlopen(req, timeout=60).read()


def ask(text):
    r = subprocess.run(["claude", "-p", "--model", "haiku", PROMPT + text], capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"claude exited {r.returncode}: {r.stderr[:150]}")
    return r.stdout


def save(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, path)


def run(targets, state, out, max_seconds, sleep=time.sleep, clock=time.monotonic,
        download=download, ask=ask, page_text=page_text):
    started, fails = clock(), 0
    todo = [t for t in targets if t["key"] not in state["tried"]]
    print(f"{len(todo)} papers to look at, {len(state['tried'])} already tried", flush=True)
    for i, t in enumerate(todo, 1):
        if max_seconds and clock() - started > max_seconds:
            print("Time budget used up; stopping.", flush=True)
            break
        t0 = clock()
        try:
            text = page_text(download(t["arxiv_id"]))
            if len(text.strip()) < MIN_TEXT:
                state["tried"][t["key"]] = "no text"
            else:
                data, why = check_answer(parse_answer(ask(text)), t.get("authors"))
                state["tried"][t["key"]] = why
                if data:
                    state["results"][t["key"]] = data
            fails = 0
        except Exception as e:  # noqa: BLE001 -- nothing is recorded, the paper comes up again
            fails += 1
            print(f"  {t['arxiv_id']}: {str(e)[:100]}", flush=True)
            sleep(min(30 * 2 ** (fails - 1), 1800))
            if fails >= 8:
                print("Too many failures in a row; stopping.", flush=True)
                break
            continue
        if i % 20 == 0:
            save(out, state)
            ok = sum(v == "ok" for v in state["tried"].values())
            print(f"  {i}/{len(todo)} done, {ok} of {len(state['tried'])} tried gave institutions", flush=True)
        sleep(max(0, GAP - (clock() - t0)))
    save(out, state)
    return state


def parse_names(text, batch):
    """{raw: {"name", "country"}} for the entries of one answer that are usable."""
    data = parse_answer(text) or []
    out = {}
    for d in data:
        if not isinstance(d, dict) or not isinstance(d.get("i"), int) or not 1 <= d["i"] <= len(batch):
            continue
        name = d["name"].strip() if isinstance(d.get("name"), str) else None
        country = d["country"].strip().upper() if isinstance(d.get("country"), str) else ""
        native = d["native"].strip() if isinstance(d.get("native"), str) else ""
        if name is None or len(name) > 120 or len(native) > 120 or (country and not re.fullmatch(r"[A-Z]{2}", country)):
            continue
        out[batch[d["i"] - 1]] = {"name": name, "native": native or name, "country": country}
    return out


def ask_raw(prompt):
    r = subprocess.run(["claude", "-p", "--model", "haiku", prompt], capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"claude exited {r.returncode}: {r.stderr[:150]}")
    return r.stdout


def standardize(state, out, max_seconds=None, ask_names=ask_raw, sleep=time.sleep, clock=time.monotonic):
    if state.get("names_version") != NAME_VERSION:
        state["names"], state["names_version"] = {}, NAME_VERSION
    state.setdefault("names", {})
    raws = sorted({x for au in state["results"].values() for a in au for x in a["affiliations"]
                   if x not in state["names"]})
    print(f"{len(raws)} affiliation strings to standardize, {len(state['names'])} already done", flush=True)
    started, fails = clock(), 0
    for i in range(0, len(raws), NAME_BATCH):
        if max_seconds and clock() - started > max_seconds:
            break
        batch = raws[i:i + NAME_BATCH]
        prompt = NAME_PROMPT + "\n".join(f"{n}. {r}" for n, r in enumerate(batch, 1))
        try:
            got = parse_names(ask_names(prompt), batch)
            fails = 0
        except Exception as e:  # noqa: BLE001 -- nothing is recorded, the strings come up again
            fails += 1
            print(f"  batch {i // NAME_BATCH}: {str(e)[:100]}", flush=True)
            sleep(min(30 * 2 ** (fails - 1), 600))
            if fails >= 6:
                break
            continue
        state["names"].update(got)
        save(out, state)
        print(f"  {min(i + NAME_BATCH, len(raws))}/{len(raws)} strings, {len(got)} of {len(batch)} usable", flush=True)
    save(out, state)
    return state


def name_key(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def corpus_names(papers, normalize):
    return {name_key(normalize(x)) for p in papers for a in p.get("authors_detail") or []
            for x in a.get("affiliations") or []}


def standard_affiliations(state, raws, normalize=lambda x: x, known=frozenset()):
    """(names, country codes) for the raw strings of one author, standardized and de-duplicated.
    Of the English and the native spelling the one the corpus already has wins; English otherwise."""
    names, codes = [], []
    for raw in raws:
        m = state.get("names", {}).get(raw)
        if not m or not m["name"]:
            continue
        spellings = [normalize(m["name"]), normalize(m.get("native") or m["name"])]
        name = next((s for s in spellings if name_key(s) in known), spellings[0])
        if name and name not in names:
            names.append(name)
        if m["country"] and m["country"] not in codes:
            codes.append(m["country"])
    return names, codes


def make_targets(path):
    from fetch_abstracts_web import PAPERS_FILE
    from fetch_affiliations_openalex import has_affiliation
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    out = []
    for p in papers:
        if p.get("av_relevance") != "AV" or has_affiliation(p):
            continue
        m = re.search(r"arxiv\.org/(?:abs|pdf)/([^\s?#]+?)(?:v\d+)?(?:\.pdf)?$", p.get("arxiv_url") or "", re.I)
        if m:
            authors = p.get("authors")
            out.append({"key": norm_title(p.get("title")), "arxiv_id": m.group(1), "title": p.get("title"),
                        "authors": authors if isinstance(authors, str) else list(authors or [])})
    out.sort(key=lambda t: t["arxiv_id"], reverse=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"{len(out)} targets written to {path}")


def apply(path):
    from atomic_write import write_json_atomic
    from fetch_abstracts_web import PAPERS_FILE
    from fetch_affiliations_openalex import has_affiliation, merge_details
    from aggregate import normalize_institution as normalize
    state = json.loads(open(path, encoding="utf-8").read())
    if not state.get("names"):
        sys.exit("Run --standardize first: there are no standardized names in " + path)
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    known = corpus_names(papers, normalize)
    n = 0
    for p in papers:
        if p.get("av_relevance") != "AV" or has_affiliation(p):
            continue
        found = state["results"].get(norm_title(p.get("title")))
        if not found:
            continue
        found = [dict(name=d["name"], affiliations=na, countries=co)
                 for d in found for na, co in [standard_affiliations(state, d["affiliations"], normalize, known)]]
        if not any(d["affiliations"] for d in found):
            continue
        detail, changed = merge_details(p.get("authors_detail"), found)
        if changed:
            p["authors_detail"] = detail
            p["authors_detail_source"] = p.get("authors_detail_source") or "pdf-llm"
            n += 1
    write_json_atomic(PAPERS_FILE, papers)
    print(f"Added institutions to {n} papers in papers_full.json.")


def list_new(path, txt):
    from fetch_abstracts_web import PAPERS_FILE
    from aggregate import normalize_institution as normalize
    state = json.loads(open(path, encoding="utf-8").read())
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    known = corpus_names(papers, normalize)
    count, spelling = {}, {}
    for authors in state["results"].values():
        seen = set()
        for a in authors:
            seen.update(standard_affiliations(state, a["affiliations"], normalize, known)[0])
        for name in seen:
            k = name_key(name)
            if k and k not in known:
                count[k] = count.get(k, 0) + 1
                spelling.setdefault(k, name)
    rows = sorted(count, key=lambda k: (-count[k], spelling[k].lower()))
    with open(txt, "w", encoding="utf-8") as f:
        f.write("\n".join(spelling[k] for k in rows) + "\n")
    print(f"{len(rows)} institutions new to the corpus written to {txt}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--targets")
    ap.add_argument("--run")
    ap.add_argument("--apply")
    ap.add_argument("--standardize")
    ap.add_argument("--list-new")
    ap.add_argument("--txt", default="new_institutions_llm.txt")
    ap.add_argument("--out", default="affiliations_llm.json")
    ap.add_argument("--max-hours", type=float, default=None)
    args = ap.parse_args(argv)
    if args.targets:
        make_targets(args.targets)
    elif args.apply:
        apply(args.apply)
    elif args.list_new:
        list_new(args.list_new, args.txt)
    elif args.standardize:
        state = json.load(open(args.standardize, encoding="utf-8"))
        standardize(state, args.standardize, (args.max_hours or 0) * 3600 or None)
    elif args.run:
        targets = json.load(open(args.run, encoding="utf-8"))
        state = json.load(open(args.out, encoding="utf-8")) if os.path.exists(args.out) else {}
        state.setdefault("results", {})
        state.setdefault("tried", {})
        run(targets, state, args.out, (args.max_hours or 0) * 3600 or None)
    else:
        ap.error("one of --targets, --run, --standardize, --apply, --list-new")
    return 0


if __name__ == "__main__":
    sys.exit(main())
