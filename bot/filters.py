"""Decide whether a link is a real announcement worth sending.

Three modes, chosen from the site's category (or `filter:` in sites.yaml):

- broad   orientation/news sites (Tawjihnet, 9rayti…): they already publish only concours news,
          so any announcement-like title passes (the original keyword list).
- strict  school / faculty / university sites: they also publish events, thesis defences,
          exam calendars and notices for their own students. A title must mention admission
          vocabulary (concours, candidature, مباراة, ولوج…) and must not mention internal-life
          or event vocabulary. Staff recruitment and doctorate lists are skipped too.
- strict  with jobs allowed: ministries, portals and military/police recruitment sites, where
          "recrutement / توظيف" is exactly the concours people want.

Every mode also drops page-navigation links ("Page 2", "suivant ›", "M'inscrire"…).
"""
from __future__ import annotations

import re
import unicodedata

from .extractor import KeywordMatcher, link_path_text, normalize_text

BROAD_CATEGORIES = {"aggregator", "jobs"}
JOBS_ALLOWED_CATEGORIES = {"portal", "military_security"}

# A school-site title must contain one of these (title only, not the URL).
STRONG = [
    # French
    "concours", "preinscription", "pre-inscription", "inscription", "candidature", "candidat",
    "admission", "admis", "admissib", "preselection", "selection", "retenu", "convoque",
    "convocation", "resultat", "liste des", "liste d'attente", "liste principale",
    "liste complementaire", "acces", "test ecrit", "epreuve", "seuil", "bachelier",
    "appel a candidature", "cnc",
    # Arabic (normalised: no hamza, ة→ه, ى→ي)
    "مباراه", "مباريات", "ترشيح", "الترشيح", "المترشحين", "المترشحات", "المرشحين", "الترشيحات",
    "تسجيل", "التسجيل", "انتقاء", "الانتقاء", "الناجحين", "المقبولين", "ولوج", "لائحه", "لوائح",
    "نتائج", "النتائج", "استدعاء", "المدعوين", "الاختبار", "عتبه", "القبول",
]

# School-site titles with any of these are internal life or events, not admissions.
EXCLUDE = [
    # events / communication
    "soutenance", "conference", "colloque", "symposium", "seminaire", "workshop", "atelier",
    "congres", "appel a communication", "appel a contribution", "ceremonie", "remise des diplomes",
    "celebration", "fete", "journee d'etude", "journee scientifique", "cooperation", "delegation",
    "accord", "convention", "partenariat", "visite", "immersion", "championnat", "competition",
    "business game", "hackathon", "club", "prix ", "tournoi", "webinaire", "forum",
    "مناقشه", "اطروحه", "عيد", "حفل", "ندوه", "مؤتمر", "ملتقي", "يوم دراسي", "اتفاقيه", "جائزه",
    "تهنئه", "منتدي", "الرياده", "مناصب المسؤوليه",
    # internal student life
    "emploi du temps", "calendrier", "planning", "examen", "rattrapage", "session d'automne",
    "session de printemps", "session principale", "session normale", "deliberation", "notes",
    "reclamation", "changement de filiere", "transfert", "derogation", "ajourne", "wifi",
    "positionnement", "seance", "inaugurale", "reinscription", "aux etudiants", "aux etudiantes",
    "etudiants de", "semestre", "mobilite", "erasmus", "echange", "stage", "annales",
    "situation pedagogique", "nouveaux etudiants",
    "الي الطلبه", "لفائده الطلبه", "الطلبه الجدد", "السداسيه", "الامتحانات", "الامتحان",
    "الاستدراكيه", "الدوره العاديه", "استعمال الزمن", "الدروس", "التداريب", "اعاده التسجيل",
]

# Staff recruitment (lecturers, administrators): job sites already cover these.
STAFF = [
    "recrutement", "recrute", "maitre de conferences", "maitres de conferences", "professeur",
    "enseignant", "habilitation", "administrateur", "techniciens 3", "technicien 3",
    "توظيف", "استاذ محاضر", "اساتذه محاضرين", "اساتذه التعليم العالي", "متصرف",
]

DOCTORATE = ["doctorat", "doctoral", "these", "الدكتوراه", "دكتوراه"]

# Navigation / call-to-action links that are never announcements.
JUNK_EXACT = {
    "concours", "accueil", "contact", "contactez-nous", "faq", "m'inscrire", "se connecter",
    "connexion", "inscription", "candidature", "demarrer ma candidature", "deposer ma candidature",
    "licence", "master", "doctorat", "formations", "formation", "admission", "admissions",
    "conditions d'acces", "doc. utiles", "documents utiles", "telechargements", "actualites",
    "annonces", "avis", "evenements", "galerie", "الرئيسيه", "اتصل بنا", "من نحن", "المباريات",
    "الاعلانات", "المستجدات",
}
PAGINATION = re.compile(
    r"^(?:(?:current|last|next|first|previous|prev)\s+page\b.*|page\s*\d+.*|\d+|"
    r"suivant|precedent|dernier|premier|next|last|previous|first|الصفحه.*|التالي|السابق)$")
SYMBOLS = re.compile(r"[«»‹›<>|•·→←\-–—()\[\]{}:;!?.,\"'“”‘’…]+")


def _norm(s: str) -> str:
    return normalize_text(s).replace("’", "'")


def _compile(words: list[str]) -> re.Pattern:
    """Latin words must start at a word boundary ("acces" must not match inside another
    word); Arabic words match anywhere because of attached prefixes (ال، و، ب…)."""
    parts = []
    for w in sorted({_norm(w) for w in words}, key=len, reverse=True):
        p = re.escape(w)
        if re.match(r"[a-z]", w):
            p = r"(?<![a-z])" + p
        if w == "admis":
            p += r"(?!trat)"  # admis, admissible… but not administration
        parts.append(p)
    return re.compile("|".join(parts))


class AnnouncementFilter:
    def __init__(self, extra_keywords: list[str] | None = None, include_doctorate: bool = False):
        self.broad = KeywordMatcher(extra_keywords)
        self.strong = _compile(STRONG + (extra_keywords or []))
        self.exclude = _compile(EXCLUDE)
        self.staff = _compile(STAFF)
        self.doctorate = _compile(DOCTORATE)
        self.include_doctorate = include_doctorate

    @staticmethod
    def mode_for(site) -> str:
        if getattr(site, "filter", None):
            return site.filter
        if not site.keywords:
            return "all"
        if site.category in BROAD_CATEGORIES:
            return "broad"
        if site.category in JOBS_ALLOWED_CATEGORIES:
            return "strict_jobs"
        return "strict"

    @staticmethod
    def is_junk(title: str) -> bool:
        t = _norm(title)
        bare = SYMBOLS.sub(" ", t).strip()
        bare = re.sub(r"\s+", " ", bare)
        if not bare or t in JUNK_EXACT or bare in JUNK_EXACT or PAGINATION.match(bare):
            return True
        if " " not in title.strip() and (len(title) > 25 or re.search(r"[.=_]", title)):
            return True  # looks like a file name / encoded id, not a title
        if t.startswith(("telecharger", "download", "cliquez", "تحميل")):
            return True
        return len(bare.split()) < 3

    def accept(self, site, title: str, url: str) -> bool:
        mode = self.mode_for(site)
        if mode == "all":
            return not self.is_junk(title)
        if self.is_junk(title):
            return False
        if mode == "broad":
            return self.broad.matches(title, link_path_text(url))
        t = _norm(title)
        if not self.strong.search(t):
            return False
        if self.exclude.search(t):
            return False
        if mode != "strict_jobs" and self.staff.search(t):
            return False
        if not self.include_doctorate and self.doctorate.search(t):
            return False
        return True
