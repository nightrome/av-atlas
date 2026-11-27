# Institution audit

Checked the top 400 institutions by citations and the top 400 by papers
(514 distinct names, counted over the 27,488 AV papers in `stats.json`) for a
missing country, logo or sector, suspicious names, spelling variants of the
same institution and units that belong to a parent. `dev/institution_audit_data.py`
rebuilds the list and the counts.

## What was fixed

- About 85 aliases added to `INSTITUTION_ALIASES` (the block marked "Institution audit"), five
  known splits added to `KNOWN_INSTITUTION_SPLITS`. About 90 institution rows disappeared (5,107 to 5,017).
- 48 countries added to `data/institution_countries.json` (none overwritten).
- Sector: about 120 research institutes and companies added to the academic/industry override
  lists, plus a rule that every Fraunhofer, Helmholtz and Leibniz institute is academic.
- Logos looked up with `fetch_institution_logos.py` (now covers the top N by citations as well
  as by papers). The wrong hits for Volvo, Volvo Cars, Scania and ENSTA were blanked by hand.
- Missing in the top 514: country 71 -> 11, sector 141 -> 27, logo 377 -> 88.

## Merged on purpose (say if any of these is wrong)

- Daimler and Daimler AG are now Mercedes-Benz (renamed in 2022, same company). Daimler Truck
  stays separate.
- National Laboratory of Pattern Recognition, CASIA and the "Chinese Academy of Sciences (CASIA)"
  spellings now roll up into Chinese Academy of Sciences, the same way the existing BNRist and
  THBI entries roll up into Tsinghua.
- "Beijing National Research Center for Information Science and Technology" is BNRist, so it is
  now Tsinghua University (849 citations moved).
- LG Electronics, LG AI Research and the LG America lab are one "LG" row.
- DAMO Academy goes into Alibaba, OpenGVLab into Shanghai AI Laboratory, S-Lab into Nanyang
  Technological University, ESAT-PSI into KU Leuven, GIPSA-lab into Universite Grenoble Alpes,
  HEUDIASYC into the Universite de Technologie de Compiegne, CEA LIST into CEA.
- `University of Maryland` is read as College Park, `University of Minnesota System` as
  University of Minnesota.

## Not changed, needs a decision

### Almost certainly OpenAlex mix-ups (one or a few papers, large citation counts)

| Name | Citations / papers | What it is | Suggested action |
|---|---|---|---|
| Vels University | 653 / 1 | Lift, Splat, Shoot (ECCV 2020) | Mis-linked; drop or map to the real affiliation of the author |
| Universitas Nurtanio | 450 / 1 | Frustum PointNets (CVPR 2018) | Same |
| United States Department of Veterans Affairs, Veterans Health Administration | 507 / 1 each | Argoverse 2 (NeurIPS 2023) | Same, both rows are one paper |
| Mountain View College | 176 / 1 | MultiPath++ (Waymo) | Probably Waymo, map to "Google + Waymo" |
| Moscow Institute of Thermal Technology | 397 / 6 | Always next to MIT, Uber, Fudan authors | Probably the MIT acronym, check one paper then alias to Massachusetts Institute of Technology |
| Shanghai Open University | 818 / 3 | DriveLM, Embodied Understanding of Driving Scenarios (Shanghai AI Lab papers) | Probably OpenDriveLab / Shanghai AI Laboratory |
| Bernstein Center for Computational Neuroscience | 396 / 3 | DriveLM (Tuebingen authors), two BMW/Freiburg papers | A unit of Tuebingen or Freiburg depending on the paper |
| ShangHai JiAi Genetics & IVF Institute | 228 / 4 | FUTR3D and others (Fudan authors) | Mis-link, probably Fudan |
| International Society for Ecological Economics | 139 / 2 | Multi-Agent Tensor Fusion for Contextual Trajectory Prediction | Mis-link (and its country is Norway) |
| International Foundation for Electoral Systems | 97 / 1 | Keep Your Eyes on the Lane | Mis-link |
| Kootenay Association for Science & Technology | 147 / 3 | | Check the papers |
| Michigan Department of Transportation | 302 / 5 | | Check, may be real |
| Sense Innovation and Research Center | 260 / 1 | GeoNet (CVPR 2018) | Probably SenseTime or a unit of it |
| PediaMed AI, Decision Systems, Dynamic Systems, Vision and Control Lab | 125-163 / 1-4 | | No country found, check the papers |

### Truncated or generic names

- `University of` (100 / 7): cut off, three of the papers are Wayve/Valeo papers.
- `Hong Kong` (92 / 31): a place, not an institution. It always sits next to a real Hong Kong
  university, so the row could be dropped.
- `Brasov` (163 / 1): probably Transilvania University of Brasov, left alone.
- `AIR` (215 / 7), `IIIS` (102 / 8): acronyms (Institute for AI Industry Research and
  Institute for Interdisciplinary Information Sciences, both Tsinghua). Probably Tsinghua University
  but not certain, so left apart.
- `ParisTech` (152 / 8): a consortium, not one school.
- `Fraunhofer Institute` (124 / 1): which institute is unknown.

### Two institutions in one name (still unsplit)

- `Chinese University of Hong Kong SenseTime Group` (160 / 1) and `HKUST HKUST(GZ)` (1 / 1):
  splits are registered but did not match the stored string, so they still show as one row.
- `CUHK-SenseTime Joint Laboratory` (202 / 6), `PKU-SenseTime Machine Vision Joint Lab`: joint labs.
  Could count for both partners.
- `Tsinghua University and Shanghai Qi Zhi Institute`, `TU Berlin and BIFOLD Berlin`,
  `Stanford University and Max Planck Institute for Informatics` (1 paper each).

### Units that belong to a parent (not rolled up, owner call)

- `MoE Key Lab of Artificial Intelligence` (462 / 16): Shanghai Jiao Tong University.
- `State Key Laboratory of Computer-Aided Design and Computer Graphics` (307 / 18): Zhejiang University.
- `OpenDriveLab` (668 / 15): Shanghai AI Laboratory and the University of Hong Kong. Left as its own row.
- `National Engineering Laboratory for Deep Learning Technology and Applications` (489 / 18): Baidu.
- `Shenzhen Institute of Advanced Technology` (153 / 45), `Shenyang Institute of Automation`,
  `Shandong Institute of Automation`: Chinese Academy of Sciences institutes.
- `Pazhou Laboratory`, `Shanghai Qi Zhi Institute`, `Shanghai Innovation Institute`,
  `State Key Laboratory of Multimodal AI Systems` (Nanjing University):
  independent or joint labs, left as they are.
- `YouTu Lab` (312 / 3): Tencent. There is no Tencent row yet to merge it into.
- `An-Institut Technische Universitat Munchen` (24 / 5), `TUM CREATE` (1 / 5): affiliated with TUM
  but separate legal entities.
- `University of California` (2,739 / 167): the bare name is the system, not one campus; the
  campuses are separate rows. `University of California System` (13 / 2) goes with it.
- `Purdue University in Indianapolis` (9 / 10): kept apart from Purdue West Lafayette.
- `Chinese University of Hong Kong, Shenzhen` (335 / 50): the existing alias merges the form with
  brackets into CUHK but not this one. Same university, so the two should probably be treated the
  same way.
- Fraunhofer: 25+ institutes appear as their own rows (IOSB, IVI, IKS, IIS, ...). They are now
  all academic, but have not been rolled up into one Fraunhofer row.
- `University of Munich` (2 / 1): LMU Munich most likely; the bare name is ambiguous, left alone.
- `Meta` had France (the Paris FAIR lab) as its country; it now has the US. Companies with labs
  in several countries only use their country when an author has none of their own.
- `Google + Waymo` (5,999 / 157) is one merged row by the existing owner decision.
- `Inria` is now spelled INRIA everywhere, the spelling the LLM alias file already used.

### Still missing a logo

The remaining logo gaps are mostly small institutes, labs and companies that have no
Wikipedia or Wikidata logo (Chinese state key labs, TIER IV, Zenseact, Fortiss and similar).
`py -3 dev/institution_audit_data.py out.json` lists them.

## Countries page: how much credit is lost

Country credit comes from each author's own OpenAlex country code, plus the one country in
`institution_countries.json` for the author's institution (companies only fill in for an author
who has no country of their own).

- 27,488 AV papers. 4,251 of them have no institution at all and 3,996 (14.5 percent) end up with
  no country, so they count for no country on the Countries page.
- Only 371 of those 3,996 papers (1.4 percent of all papers) have at least one institution, so
  those are the ones a missing institution country can actually lose. The audit added countries for
  the ones with the most papers (Samsung Electronics, Woven by Toyota, QCraft and others).
  The rest of the loss is papers with no affiliation data, which needs the affiliation crawlers,
  not this file.
- A further 4,045 papers have some institution with no country but do get a country from another
  author, so they are counted, but a country could be missing from them (for example a Korean
  author at an institution with no country on a paper that also has a US author).
