"""Build the top-400 institution lists (by citations, by papers) with indicator flags.
Usage: py -3 dev/institution_audit_data.py  -> prints a table and writes the scratch json given as argv[1]."""
import json, re, sys, ast
from collections import defaultdict
s = json.load(open("data/stats.json", encoding="utf-8"))
cit = defaultdict(int); pap = defaultdict(int)
def L(x):
    if isinstance(x, str):
        try: return ast.literal_eval(x)
        except Exception: return []
    return x or []
n=0
for p in s["all_papers"]:
    if p.get("av_relevance") not in (None, "AV"): continue
    try: c = int(p.get("citations") or 0)
    except Exception: c = 0
    for i in set(L(p.get("institutions"))):
        cit[i] += c; pap[i] += 1
    n+=1
cnt = s["institution_countries"]; img = s["institution_images"]; sec = s["institution_sector"]
topc = [k for k,_ in sorted(cit.items(), key=lambda kv:-kv[1])[:400]]
topp = [k for k,_ in sorted(pap.items(), key=lambda kv:-kv[1])[:400]]
union = list(dict.fromkeys(topc+topp))
rows=[dict(name=k,cit=cit[k],pap=pap[k],country=cnt.get(k),logo=k in img,sector=sec.get(k),
           inc=k in topc, inp=k in topp) for k in union]
json.dump(rows, open(sys.argv[1] if len(sys.argv)>1 else "NUL","w",encoding="utf-8"), ensure_ascii=False)
print(n, len(cit), len(union))
for lab,f in (("country",lambda r:not r["country"]),("logo",lambda r:not r["logo"]),("sector",lambda r:not r["sector"])):
    print(lab, "missing:", sum(1 for r in rows if f(r)), "of", len(rows))
