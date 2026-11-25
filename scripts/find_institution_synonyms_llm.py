#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Find synonyms among the institutions the PDF first-page pass brought in.

The new institution names are grouped by country, and a small LLM is asked, per
country, which of them are just another spelling (an abbreviation, a native or
English name, a rename, a lab of the same company) of an institution the corpus
already has in that country, or of another new name in the same group.

  --prepare RESULTS   (local) group the new names by country with the corpus's
                      existing institutions of that country -> --out file
  --run FILE          (where the claude CLI is) ask haiku, 40 new names per call,
                      resumable: names already answered are never asked again
  --apply FILE        (local) write the accepted synonyms into
                      data/institution_aliases_llm.json as {variant: canonical},
                      which aggregate.py applies when it builds the site

A synonym is accepted only when its target is a name that really exists (an
existing corpus institution or another new name) and is not the name itself.

Usage:
  python find_institution_synonyms_llm.py --prepare data/affiliations_llm.json --out syn_in.json
  python find_institution_synonyms_llm.py --run syn_in.json --out syn_out.json
  python find_institution_synonyms_llm.py --apply syn_out.json
"""
import argparse
import json
import os
import subprocess
import sys

BATCH = 40
PROMPT = (
    "Below are two lists of institutions in one country. NEW holds names to check. KNOWN holds institutions we "
    "already have. For each NEW name, say whether it is just another name for an institution in KNOWN, or for an "
    "earlier name in NEW: an abbreviation, the same name in another language, a renamed or merged institution, a "
    "formal versus short name, or a research lab or branch that is part of the same company. Be strict: two "
    "different universities in the same city are NOT synonyms, a university and a separate institute are NOT "
    "synonyms, and a company and its unrelated competitor are NOT synonyms. Copy the matching name exactly as "
    "written in the lists. Use '' when there is no match. Answer with JSON only: "
    '[{"i": 1, "same_as": "..."}, ...] with one entry per NEW name.\n\n')


def parse_json_list(text):
    import re
    text = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    try:
        data = json.loads(text)
    except ValueError:
        m = re.search(r"\[.*\]", text, re.S)
        try:
            data = json.loads(m.group(0)) if m else None
        except ValueError:
            data = None
    return data if isinstance(data, list) else []


def accepted(new, answer, known, news):
    """{name: target} for the answers whose target exists and is not the name itself."""
    out = {}
    for d in answer:
        if not isinstance(d, dict) or not isinstance(d.get("i"), int) or not 1 <= d["i"] <= len(new):
            continue
        name, target = new[d["i"] - 1], d.get("same_as")
        if isinstance(target, str) and target and target != name and (target in known or target in news):
            out[name] = target
    return out


def ask(prompt):
    r = subprocess.run(["claude", "-p", "--model", "haiku", prompt], capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"claude exited {r.returncode}: {r.stderr[:150]}")
    return r.stdout


def save(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, path)


def run(state, out, ask_llm=ask):
    state.setdefault("done", {})        # name -> "" or the target
    for code, grp in sorted(state["groups"].items(), key=lambda kv: -len(kv[1]["new"])):
        todo = [n for n in grp["new"] if n not in state["done"]]
        for i in range(0, len(todo), BATCH):
            batch = todo[i:i + BATCH]
            prompt = (PROMPT + "NEW:\n" + "\n".join(f"{n}. {x}" for n, x in enumerate(batch, 1))
                      + "\n\nKNOWN:\n" + "\n".join(grp["known"]))
            try:
                got = accepted(batch, parse_json_list(ask_llm(prompt)), set(grp["known"]), set(grp["new"]))
            except Exception as e:  # noqa: BLE001 -- nothing is recorded, the names come up again
                print(f"  {code}: {str(e)[:100]}", flush=True)
                continue
            for n in batch:
                state["done"][n] = got.get(n, "")
            save(out, state)
            print(f"{code}: {len(batch)} names, {len(got)} synonyms", flush=True)
    save(out, state)


def prepare(results, out):
    from aggregate import normalize_institution as normalize
    from fetch_abstracts_web import DATA_DIR, PAPERS_FILE
    from fetch_affiliations_llm import corpus_names, name_key, norm_title, standard_affiliations
    state = json.load(open(results, encoding="utf-8"))
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    # What the corpus had without this pass: the papers it did not touch (the pass may already be applied).
    known = corpus_names([p for p in papers if norm_title(p.get("title")) not in state["results"]], normalize)
    country_of = {}
    for m in state["names"].values():
        if m["country"]:
            for spelling in (m["name"], m.get("native") or m["name"]):
                country_of.setdefault(normalize(spelling), m["country"])
    new = {}
    for authors in state["results"].values():
        for a in authors:
            for name in standard_affiliations(state, a["affiliations"], normalize, known)[0]:
                if name_key(name) not in known:
                    new.setdefault(name, country_of.get(name, "??"))
    existing = json.loads((DATA_DIR / "institution_countries.json").read_text(encoding="utf-8"))
    # Also every corpus institution seen on an author with exactly one affiliation and one country code.
    seen_n, seen_code = {}, {}
    for p in papers:
        if norm_title(p.get("title")) in state["results"]:
            continue
        for a in p.get("authors_detail") or []:
            aff, cc = a.get("affiliations") or [], a.get("countries") or []
            if len(aff) == 1 and len(cc) == 1:
                name = normalize(aff[0])
                seen_n[name] = seen_n.get(name, 0) + 1
                seen_code.setdefault(name, cc[0])
    for name, n in seen_n.items():
        if n >= 2:
            existing.setdefault(name, seen_code[name])
    groups = {}
    for name, code in new.items():
        groups.setdefault(code, {"new": [], "known": []})["new"].append(name)
    for name, code in existing.items():
        if code in groups:
            groups[code]["known"].append(name)
    for g in groups.values():
        g["new"].sort()
        g["known"].sort()
    save(out, {"groups": groups})
    print(f"{len(new)} new names in {len(groups)} countries -> {out}")


def apply(path):
    from atomic_write import write_json_atomic
    from fetch_abstracts_web import DATA_DIR
    state = json.load(open(path, encoding="utf-8"))
    alias_file = DATA_DIR / "institution_aliases_llm.json"
    aliases = json.loads(alias_file.read_text(encoding="utf-8")) if alias_file.exists() else {}
    n = 0
    for name, target in state["done"].items():
        if target and name not in aliases:
            aliases[name] = target
            n += 1
    # a target that is itself an alias goes straight to the end of the chain
    for k, v in list(aliases.items()):
        seen = {k}
        while v in aliases and v not in seen:
            seen.add(v)
            v = aliases[v]
        aliases[k] = v
    write_json_atomic(alias_file, dict(sorted(aliases.items())))
    print(f"{n} aliases added to {alias_file.name} ({len(aliases)} in total)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prepare")
    ap.add_argument("--run")
    ap.add_argument("--apply")
    ap.add_argument("--out", default="synonyms.json")
    args = ap.parse_args(argv)
    if args.prepare:
        prepare(args.prepare, args.out)
    elif args.run:
        state = json.load(open(args.out, encoding="utf-8")) if os.path.exists(args.out) else json.load(
            open(args.run, encoding="utf-8"))
        run(state, args.out)
    elif args.apply:
        apply(args.apply)
    else:
        ap.error("one of --prepare, --run, --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
