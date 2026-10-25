"""
Convert the original JSON-like site list into sites.yaml.

- Fixes non-breaking spaces in the source file
- Groups entries by category (from the key prefix)
- Merges entries that point to the same URL into one site (so one site = one fetch)
- Marks the big aggregator sites so they are checked first

Usage:  python tools/import_list.py path/to/list.txt  > sites.yaml
"""
import re
import sys
from collections import OrderedDict

import yaml

CATEGORY_RULES = [
    ("aggregator", ("tawjihnet", "moutamadris", "9rayti", "supmaroc", "tawjih_info",
                    "orientation_ma", "alwadifa_maroc", "emploi_public", "tawjih")),
    ("portal", ("ministry_", "cursussup", "minhaty", "ofppt", "massar", "cpge_", "bts_")),
    ("ensa", ("ensa_",)),
    ("encg", ("encg_",)),
    ("fst", ("fst_",)),
    ("est", ("est_",)),
    ("ensam", ("ensam_",)),
    ("ens", ("ens_",)),
    ("medicine", ("fmp_", "fmd_")),
    ("health", ("ispits_", "isss_", "ensp_", "inas_")),
    ("architecture", ("ena_",)),
    ("military_security", ("recrutement_", "concours_dgsn", "protection_civile", "arm_",
                           "erssm_", "era_", "ern_", "garde_", "forces_armees")),
    ("teaching", ("crmef_",)),
    ("vocational", ("cmc_", "ifmeree_", "ifmia_", "itpm_", "ifmerp_", "itsag_", "isfr_", "cqpm_")),
    ("religious", ("dar_hadith", "m6_lectures", "quaraouiyine", "fac_")),
    ("university", ("univ_",)),
    ("private_university", ("uir_", "um6p_", "um6ss_", "uiass_", "ueuromed_", "aui_", "uic_",
                            "universiapolis_")),
    ("faculty", ("fs_", "fsa_", "fsjes_", "flsh_", "fp_", "cu_")),
]


def category_for(key: str) -> str:
    for cat, prefixes in CATEGORY_RULES:
        for p in prefixes:
            if key == p or key.startswith(p):
                return cat
    return "grande_ecole"


def norm(url: str) -> str:
    u = url.strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    return u.rstrip("/")


def main(path: str) -> None:
    text = open(path, encoding="utf-8").read().replace("\xa0", " ")
    rows = re.findall(
        r'"([^"]+)"\s*:\s*\{\s*"name"\s*:\s*"([^"]*)"\s*,\s*"url"\s*:\s*"([^"]*)"\s*\}', text
    )
    merged: "OrderedDict[str, dict]" = OrderedDict()
    for key, name, url in rows:
        n = norm(url)
        if n in merged:
            merged[n].setdefault("also", []).append(name)
            continue
        merged[n] = {"id": key, "name": name, "url": url, "category": category_for(key)}

    sites = list(merged.values())
    # aggregators first: they are the most valuable and most reliable
    sites.sort(key=lambda s: 0 if s["category"] == "aggregator" else 1)

    sys.stderr.write(f"{len(rows)} entries -> {len(sites)} unique sites\n")
    header = (
        "# Sites watched by the bot.\n"
        "# Fields per site:\n"
        "#   id, name, url, category      (required)\n"
        "#   enabled: false               -> skip this site\n"
        "#   rss: <url>                   -> use this RSS/Atom feed (most reliable)\n"
        "#   selector: '<css>'            -> only read links inside this CSS block (e.g. '.news a')\n"
        "#   keywords: false              -> notify every new link, not only keyword matches\n"
        "#   also: [names]                -> other schools that share this same URL\n\n"
    )
    out = yaml.safe_dump({"sites": sites}, allow_unicode=True, sort_keys=False, width=200)
    sys.stdout.write(header + out)


if __name__ == "__main__":
    main(sys.argv[1])
