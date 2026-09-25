#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Finds email addresses in scraped text and replaces each one with its domain.

Author blocks pulled out of PDFs and ar5iv pages are full of addresses
("... Tongji University, Shanghai, China x.y@tongji.edu.cn",
"{mkoren, mykel}@stanford.edu"). The domain is worth keeping: it often names
the institution more clearly than the text around it. The address itself is
not. This repo is public, and a tracked file full of scraped addresses is a
ready-made spam list. So every address becomes its domain:

    x.y@tongji.edu.cn                 -> tongji.edu.cn
    {mkoren, mykel}@stanford.edu      -> stanford.edu
    (a@umich.edu, b@umich.edu)        -> (umich.edu)

Used in three places:
  - the affiliation cache (institution_extraction_llm.py, looked up from
    fetch_affiliations_arxiv.py) is keyed by the stripped text, which is also
    what the LLM is shown;
  - aggregate.py strips abstracts and rejects institution names that still
    hold an address, so none reach stats.json or the site;
  - build_public_site.py runs scrub_tracked_files() before every build,
    because a fresh crawl can bring addresses back into data/venues/*.json.
scripts/tests/test_email_addresses.py checks that no tracked file under data/
contains one.

Usage: python scripts/email_addresses.py           # scrub every tracked data/ file
       python scripts/email_addresses.py --check   # only report, write nothing
"""
import argparse
import json
import re
import subprocess
import unicodedata
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

# An address has to end in a real top-level domain. That is what keeps metric
# notation in abstracts ("mAP@0.5", "Acc@0.25IoU", "PointASNL@Sem.KITTI",
# "nDCG@10.Moreover") from being read as an address. Matched in lower or
# upper case only, so a capitalised word after a full stop ("AP@0.5.In
# addition") isn't taken for the .in domain.
COUNTRY_TLDS = """
ac ad ae af ag ai al am ao aq ar as at au aw ax az ba bb bd be bf bg bh bi bj
bm bn bo br bs bt bw by bz ca cc cd cf cg ch ci ck cl cm cn co cr cu cv cw cx
cy cz de dj dk dm do dz ec ee eg er es et eu fi fj fk fm fo fr ga gd ge gf gg
gh gi gl gm gn gp gq gr gs gt gu gw gy hk hm hn hr ht hu id ie il im in io iq
ir is it je jm jo jp ke kg kh ki km kn kp kr kw ky kz la lb lc li lk lr ls lt
lu lv ly ma mc md me mg mh mk ml mm mn mo mp mq mr ms mt mu mv mw mx my mz na
nc ne nf ng ni nl no np nr nu nz om pa pe pf pg ph pk pl pm pn pr ps pt pw py
qa re ro rs ru rw sa sb sc sd se sg sh si sk sl sm sn so sr ss st su sv sx sy
sz tc td tf tg th tj tk tl tm tn to tr tt tv tw tz ua ug uk us uy uz va vc ve
vg vi vn vu wf ws ye yt za zm zw
""".split()
GENERIC_TLDS = """
com org net edu gov mil int info biz name pro aero coop museum mobi asia cat
jobs tel travel app dev tech technology cloud online site xyz science global
group honda
""".split()
TLDS = frozenset(COUNTRY_TLDS + GENERIC_TLDS)

_TLD_ALTS = "|".join(sorted({t for tld in TLDS for t in (tld, tld.upper())}, key=len, reverse=True))
DOMAIN = rf"(?:[A-Za-z0-9-]+\.)+(?:{_TLD_ALTS})(?![A-Za-z0-9-])"

# Zero-width spaces turn up around the "@" in some PDF text layers.
_ZW = r"[\u200b-\u200d\ufeff]"
# Characters of an ordinary local part. Accented Latin letters are in, CJK is
# not, so "清华大学zhang@..." keeps the university's name.
_LOCAL = r"A-Za-z0-9\u00c0-\u024f._%+\-"
# A LaTeX-style group of local parts sharing one domain: "{a, b}@x.edu",
# "[a, b] @x.edu", "(a,b)@x.edu", "{firstname}.{lastname}@x.edu".
_GROUP = r"\{[^{}@\n]{0,300}\}|\[[^\[\]@\n]{0,300}\]|\([^()@\n]{0,300}\)"

ADDRESS_RE = re.compile(
    rf"(?P<local>(?:{_GROUP})(?:\.(?:{_GROUP}))*[ \t]*"
    rf"|(?<![{_LOCAL}])[{_LOCAL}]+{_ZW}*"
    # "nls71 @pitt.edu": a space before the "@" only counts after a
    # lowercase token, so "Chalmers University of Technology @chalmers.se"
    # keeps "Technology".
    rf"|(?<![{_LOCAL}])[a-z0-9][a-z0-9._%+\-]*[ \t]+"
    rf")?{_ZW}*@{_ZW}*[ \t]?(?P<domain>{DOMAIN})")

# "{yuhaibao@air.,luoyz18@mails.}tsinghua.edu.cn": partial addresses inside
# a group, with the shared end of the domain written once after it.
PARTIAL_GROUP_RE = re.compile(rf"\{{[^{{}}\n]{{0,300}}@[^{{}}\n]{{0,300}}\}}\.?(?={DOMAIN})")

# An address cut off before its top-level domain or missing a dot
# ("name.lastname@tum", "jm.andrew.yu@gmailcom"). Only taken for a lowercase
# local part with a dot or underscore in it, which metric notation never has
# ("Recall@k", "mAP@0.5"). Stripped, but not counted by
# find_email_addresses(), since it isn't a working address.
TRUNCATED_RE = re.compile(
    rf"(?<![{_LOCAL}])[a-z0-9]+(?:[._][a-z0-9]+)+@(?P<domain>[a-z][a-z0-9-]*)(?![\w.@-])")

# The same domain once per author after the addresses are gone:
# "umich.edu, umich.edu, umich.edu" -> "umich.edu".
_REPEATED_DOMAIN_RE = re.compile(
    r"(?<![\w.-])((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})"
    r"(?:(?:\s*[,;|/]\s*|\s+)(?:and\s+)?\1(?![\w-]|\.\w))+")

# Mathematical alphanumeric symbols (a \tt font in some PDFs comes out as
# "𝚞𝚖𝚒𝚌𝚑.𝚎𝚍𝚞"), folded to plain letters before matching.
_MATH_ALNUM_RE = re.compile("[\U0001D400-\U0001D7FF]+")

# Quick pre-check for large texts: an "@" followed by a real domain, or by a
# math letter that might fold into one. The full ADDRESS_RE only runs on the
# text around each hit.
_AT_HINT_RE = re.compile(rf"@{_ZW}*(?:[ \t]?{DOMAIN}|[\U0001D400-\U0001D7FF])")


def _fold_math_letters(text):
    return _MATH_ALNUM_RE.sub(lambda m: unicodedata.normalize("NFKC", m.group(0)), text)


def _gap(text, start):
    # Keeps a word boundary where an address was glued onto the text before
    # it ("USA{a,b}@uga.edu" -> "USA uga.edu", not "USAuga.edu").
    return " " if start > 0 and text[start - 1].isalnum() else ""


def strip_email_addresses(text, keep_domain=True):
    """Returns text with every email address replaced by its domain, or
    dropped when keep_domain is False (for author-name fields, where a
    leftover domain would read as part of the last name). Text without an
    "@" comes back unchanged, and stripping the result again changes
    nothing."""
    if not text or "@" not in text:
        return text

    def to_domain(m):
        return _gap(m.string, m.start()) + m.group("domain") if keep_domain else ""

    stripped = _fold_math_letters(text)
    # Addresses glued together with no separator ("a@x.comb@x.com") only
    # come apart one per pass.
    for _ in range(10):
        new = PARTIAL_GROUP_RE.sub(lambda m: _gap(m.string, m.start()), stripped)
        new = ADDRESS_RE.sub(to_domain, new)
        new = TRUNCATED_RE.sub(to_domain, new)
        if new == stripped:
            break
        stripped = new
    if stripped == _fold_math_letters(text):
        return text
    if keep_domain:
        stripped = _REPEATED_DOMAIN_RE.sub(r"\1", stripped)
    return re.sub(r"[ \t]{2,}", " ", stripped).strip()


def find_email_addresses(text):
    """Every address in text. Fast enough for whole data files: the full
    pattern only runs around an "@" that is followed by a real domain. A
    bare "@domain" with no local part isn't an address and isn't
    reported."""
    if not text or "@" not in text:
        return []
    found = []
    for hit in _AT_HINT_RE.finditer(text):
        # 400 characters back covers the longest {a, b, ...} group. Folding
        # math letters keeps positions, one character for one.
        start = max(0, hit.start() - 400)
        window = _fold_math_letters(text[start:hit.start() + 300])
        at = hit.start() - start
        for m in ADDRESS_RE.finditer(window):
            if m.start() <= at < m.end():
                if m.group("local"):
                    found.append(m.group(0))
                break
            if m.start() > at:
                break
    return found


def scrub(value, keep_domain=True):
    """strip_email_addresses() over every string (keys included) in a JSON
    value. A raw "authors" string is a list of names, so addresses there are
    dropped rather than turned into a domain.

    Dict keys that only differed in their addresses become one key (in the
    affiliation cache, "FZI ... {grimm, zipfl}@fzi.de" and "FZI ...
    bogdoll@fzi.de"). Its value is the one of the key that had nothing to
    strip if there is one, otherwise the most common value, and the first
    of those on a tie."""
    if isinstance(value, str):
        return strip_email_addresses(value, keep_domain)
    if isinstance(value, list):
        return [scrub(v, keep_domain) for v in value]
    if isinstance(value, dict):
        groups = {}
        for key, v in value.items():
            groups.setdefault(strip_email_addresses(key), []).append((key, v))
        out = {}
        for new_key, items in groups.items():
            clean = [v for key, v in items if key == new_key]
            if clean:
                chosen = clean[0]
            else:
                votes = Counter(json.dumps(v, sort_keys=True) for _, v in items)
                chosen = max(items, key=lambda item: votes[json.dumps(item[1], sort_keys=True)])[1]
            out[new_key] = scrub(chosen, keep_domain=keep_domain and new_key != "authors")
        return out
    return value


# The layouts json.dumps() writes across this repo's data files.
_JSON_LAYOUTS = (
    dict(indent=2, ensure_ascii=False),
    dict(indent=1, ensure_ascii=False, sort_keys=True),
    dict(indent=2, ensure_ascii=False, sort_keys=True),
    dict(ensure_ascii=False),
)


def scrub_json_text(raw):
    """Scrubs a JSON document given as text. Returns None when there was
    nothing to strip, otherwise the new text in the same layout as the input
    (indent, key order, trailing newline, line endings), so a rewrite only
    touches the lines that changed. A layout none of the writers here
    produce falls back to the usual indent=2."""
    if "@" not in raw or not _AT_HINT_RE.search(raw):
        return None
    crlf = "\r\n" in raw
    text = raw.replace("\r\n", "\n")
    obj = json.loads(text)
    layout, tail = _JSON_LAYOUTS[0], "\n" if text.endswith("\n") else ""
    for candidate in _JSON_LAYOUTS:
        dumped = json.dumps(obj, **candidate)
        if text in (dumped, dumped + "\n"):
            layout, tail = candidate, text[len(dumped):]
            break
    new = json.dumps(scrub(obj), **layout) + tail
    if new == text:
        return None
    return new.replace("\n", "\r\n") if crlf else new


def tracked_data_files():
    """The JSON files under data/ that git tracks. The gitignored ones
    (papers_full.json, stats.json, the shards) never reach the repo, and
    aggregate.py keeps addresses out of what the site publishes. Raises
    OSError or CalledProcessError outside a git checkout."""
    out = subprocess.run(["git", "ls-files", "-z", "data"], cwd=BASE, capture_output=True, check=True)
    return [BASE / p for p in out.stdout.decode("utf-8").split("\0") if p.endswith(".json")]


def scrub_tracked_files(write=True, files=None):
    """Strips addresses from every tracked data/ JSON file (or just
    `files`), in place unless write is False. Returns the files that had
    any."""
    changed = []
    for path in tracked_data_files() if files is None else files:
        # Bytes, not read_text(), so CRLF line endings survive the rewrite.
        new = scrub_json_text(path.read_bytes().decode("utf-8"))
        if new is None:
            continue
        changed.append(path)
        if write:
            path.write_bytes(new.encode("utf-8"))
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="only report files with addresses, write nothing")
    args = parser.parse_args()
    try:
        files = tracked_data_files()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Not scrubbing email addresses: can't list the tracked data files ({exc}).")
        return
    changed = scrub_tracked_files(write=not args.check, files=files)
    for path in changed:
        verb = "has" if args.check else "stripped"
        print(f"  {verb} email addresses: {path.relative_to(BASE).as_posix()}")
    print(f"{len(changed)} tracked data file(s) with email addresses"
          f"{'' if args.check else ' scrubbed'}.")


if __name__ == "__main__":
    main()
