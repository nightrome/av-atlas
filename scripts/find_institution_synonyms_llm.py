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
  --prepare-orgs      (local) group every institution of the corpus by its first word
                      (universities left out) -> --out file; then --run-orgs FILE asks
                      which of the names in a group are units of one company or
                      organization and should be one name, the way every Honda unit
                      is just "Honda"
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


ORG_BATCH = 70
ORG_PROMPT = (
    "Below are groups of institution names from author affiliations; a group shares its first word. Within each "
    "group, find names that are units, labs, subsidiaries, regional branches or alternate names of the SAME company "
    "or organization (like 'Honda R&D', 'Honda Research Institute USA' and 'Honda Motor', which are all just "
    "'Honda'). For each such name give the single plain name of the organization it belongs to: the bare company or "
    "organization name, copied from the group if it is there, otherwise the shared company name. Use '' for a name "
    "that is already the plain organization name, or that is a different organization: two unrelated organizations "
    "with the same first word, a separate joint venture ('Sony Honda Mobility'), a university, or a government "
    "agency of another country are NOT the same. Answer with JSON only: "
    '[{"i": 7, "same_as": "..."}, ...] listing only the lines that should be merged.\n\n')


UNIT_PROMPT = (
    "Each group below starts with a university (its first line, the one all others are compared with), followed by "
    "other institution names that mention its name or its abbreviation. Find the names that are a department, "
    "school, faculty, lab, institute or center that is part of THAT university, or just another spelling of it "
    "(for example 'MIT CSAIL' and 'MIT Media Lab' belong to MIT; 'Rutgers Robotics Lab' belongs to Rutgers). Do NOT "
    "merge a different university or a campus with its own name ('University of Michigan-Dearborn'), a company, a "
    "hospital, a government research institute, a joint venture between organizations, or an unrelated "
    "organization that only shares a word. Use the first line of the group, copied exactly, as the answer. Answer "
    'with JSON only: [{"i": 7, "same_as": "..."}, ...] listing only the lines that should be merged.\n\n')

_UNIT_GENERIC = set("""university universidad universite universita universitat universidade of the and de di
college institute technology polytechnic school state national royal new north south east west central""".split())


def prepare_units(out):
    import collections
    import re
    import unicodedata
    from aggregate import ACADEMIC_KEYWORD_RE, normalize_institution as normalize
    from fetch_abstracts_web import PAPERS_FILE
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    count = collections.Counter(normalize(x) for p in papers for a in p.get("authors_detail") or []
                                for x in a.get("affiliations") or [])

    def fold(s):
        return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    uni = re.compile(r"universit|universid|college|polytechn|institute of technology|hochschule|"
                     r"ETH|EPFL", re.I)
    unis = [n for n, k in count.items() if k >= 5 and uni.search(fold(n)) and len(n.split()) <= 7]
    folded = {n: fold(n) for n in count}
    groups = {}
    for u in unis:
        words = [w for w in re.findall(r"[A-Za-z0-9]+", fold(u)) if w.lower() not in _UNIT_GENERIC]
        keys = [" ".join(words)] if words else []
        initials = "".join(w[0] for w in re.findall(r"[A-Za-z]+", fold(u)) if w.lower() not in
                           {"of", "the", "and", "de", "di", "for"}).upper()
        pats = [re.compile(r"(?<![A-Za-z0-9])" + re.escape(k) + r"(?![A-Za-z0-9])", re.I) for k in keys
                if len(k) >= 4]
        if len(initials) >= 3:
            pats.append(re.compile(r"(?<![A-Za-z0-9])" + initials + r"(?![A-Za-z0-9])"))
        if not pats:
            continue
        cands = [n for n in count if n != u and folded[n] and not uni.search(folded[n]) and ACADEMIC_KEYWORD_RE.search(n) is None
                 and any(p.search(folded[n]) for p in pats)]
        if cands:
            cands.sort(key=lambda n: -count[n])
            groups[u] = {"new": [u] + cands[:40], "known": [], "counts": {n: count[n] for n in [u] + cands[:40]}}
    save(out, {"groups": groups})
    print(f"{len(groups)} universities, {sum(len(g['new']) - 1 for g in groups.values())} candidates -> {out}")


def accepted_org(lines, answer):
    """lines: [(group, name)] numbered from 1. Target must be in the same group or a leading part of the name."""
    members = {}
    for g, n in lines:
        members.setdefault(g, set()).add(n)
    out = {}
    for d in answer:
        if not isinstance(d, dict) or not isinstance(d.get("i"), int) or not 1 <= d["i"] <= len(lines):
            continue
        g, name = lines[d["i"] - 1]
        t = d.get("same_as")
        if isinstance(t, str) and t and t != name and (t in members[g] or name.startswith(t + " ")):
            out[name] = t
    return out


def run_orgs(state, out, ask_llm=None, prompt=ORG_PROMPT):
    ask_llm = ask_llm or ask
    state.setdefault("done", {})
    lines = [(g, n) for g, grp in sorted(state["groups"].items()) for n in grp["new"] if n not in state["done"]]
    i = 0
    while i < len(lines):
        # whole groups per call
        j, last = i, None
        while j < len(lines) and (j - i < ORG_BATCH or lines[j][0] == last):
            last = lines[j][0]
            j += 1
        batch = lines[i:j]
        text, prev = [], None
        for n, (g, name) in enumerate(batch, 1):
            if g != prev:
                text.append(f"--- group: {g}")
                prev = g
            text.append(f"{n}. {name} ({state['groups'][g]['counts'].get(name, 0)} papers)")
        try:
            got = accepted_org(batch, parse_json_list(ask_llm(prompt + "\n".join(text))))
        except Exception as e:  # noqa: BLE001 -- nothing is recorded, the names come up again
            print(f"  {batch[0][0]}: {str(e)[:100]}", flush=True)
            i = j
            continue
        for g, name in batch:
            state["done"][name] = got.get(name, "")
        save(out, state)
        print(f"{batch[0][0]}..{batch[-1][0]}: {len(batch)} names, {len(got)} merges", flush=True)
        i = j
    save(out, state)


_ORG_STOP = set("""the of and authors author university universidad universite universita universitat universidade
universiteit national institute institut school department dept college center centre academy state technical
technology royal federal chinese swiss german new san north south east west central research laboratory laboratoire lab
key ministry faculty hospital city polytechnic computer intelligent china usa germany korea japan france italy spain
india canada australia europe european international advanced applied systems engineering science sciences electrical
electronic electronics information robotics automation vehicle vehicles transport transportation""".split())


def prepare_orgs(out):
    import collections
    import re
    import unicodedata
    from aggregate import ACADEMIC_KEYWORD_RE, normalize_institution as normalize
    from fetch_abstracts_web import PAPERS_FILE
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    count = collections.Counter(normalize(x) for p in papers for a in p.get("authors_detail") or []
                                for x in a.get("affiliations") or [])
    groups = collections.defaultdict(list)
    for name, k in count.items():
        if k < 2 or not name or ACADEMIC_KEYWORD_RE.search(name):
            continue
        tok = re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", name.split()[0]).encode("ascii", "ignore")
                     .decode().lower())
        if len(tok) >= 3 and tok not in _ORG_STOP:
            groups[tok].append(name)
    groups = {t: v for t, v in groups.items() if len(v) >= 2}
    save(out, {"groups": {t: {"new": sorted(v, key=lambda n: -count[n])[:60], "known": [],
                              "counts": {n: count[n] for n in v}} for t, v in groups.items()}})
    print(f"{len(groups)} groups, {sum(len(v) for v in groups.values())} names -> {out}")


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
    ap.add_argument("--prepare-orgs", action="store_true")
    ap.add_argument("--run-orgs")
    ap.add_argument("--prepare-units", action="store_true")
    ap.add_argument("--run-units")
    ap.add_argument("--out", default="synonyms.json")
    args = ap.parse_args(argv)
    if args.prepare:
        prepare(args.prepare, args.out)
    elif args.prepare_orgs:
        prepare_orgs(args.out)
    elif args.prepare_units:
        prepare_units(args.out)
    elif args.run_units:
        state = json.load(open(args.out, encoding="utf-8")) if os.path.exists(args.out) else json.load(
            open(args.run_units, encoding="utf-8"))
        run_orgs(state, args.out, prompt=UNIT_PROMPT)
    elif args.run_orgs:
        state = json.load(open(args.out, encoding="utf-8")) if os.path.exists(args.out) else json.load(
            open(args.run_orgs, encoding="utf-8"))
        run_orgs(state, args.out)
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
