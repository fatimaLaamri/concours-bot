"""Turn a fetched page (HTML or RSS/Atom) into a list of announcement candidates."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Keywords (French / Arabic / English + school acronyms). Matching ignores case,
# French accents and Arabic hamza/diacritics, so "Préinscription" == "preinscription"
# and "أعلان" == "اعلان".
# ---------------------------------------------------------------------------
KEYWORDS = [
    # French
    "concours", "inscription", "preinscription", "pre-inscription", "candidature", "candidat",
    "admission", "selection", "preselection", "presélection", "avis", "appel", "annonce",
    "communique", "liste", "laureat", "resultat", "convoque", "convocation", "oral", "ecrit",
    "entretien", "recrutement", "seuil", "date limite", "delai", "master", "licence", "doctorat",
    "cycle", "bac", "bachelier", "bourse", "acces", "integration", "passerelle", "affichage",
    "calendrier", "planning", "rentree", "orientation",
    # Arabic
    "مباراة", "مباريات", "مباراه", "التسجيل", "تسجيل", "الترشيح", "ترشيح", "المترشحين", "الترشيحات",
    "اعلان", "إعلان", "بلاغ", "لائحة", "لوائح", "النتائج", "نتائج", "الناجحين", "استدعاء",
    "الانتقاء", "انتقاء", "القبول", "ولوج", "توظيف", "منحة", "المنحة", "الباكالوريا", "البكالوريا",
    "باكالوريا", "عتبة", "آخر أجل", "اجل", "الماستر", "الاجازة", "الدكتوراه", "سلك", "الشفوي",
    "الكتابي", "الدخول", "مسطرة", "التوجيه",
    # English
    "apply", "application", "admissions", "results", "deadline", "registration", "scholarship",
    # School / program acronyms (as whole words)
    "ensa", "encg", "fst", "est", "ensam", "ens", "fmp", "fmd", "cpge", "bts", "ispits", "crmef",
    "ofppt", "iav", "emi", "ehtp", "inpt", "insea", "iscae", "isic", "ena", "dut", "deust", "lp",
    "cursussup", "minhaty", "tafem", "medecine", "pharmacie", "dentaire", "infirmier",
]

GENERIC_LINK_TEXT = {
    "lire la suite", "lire plus", "en savoir plus", "voir plus", "plus", "suite", "details",
    "detail", "read more", "more", "telecharger", "download", "cliquez ici", "ici", "click here",
    "اقرأ المزيد", "المزيد", "التفاصيل", "اضغط هنا", "هنا", "تحميل", "اطلع", "voir", "consulter",
}

SKIP_HOSTS = ("facebook.com", "fb.com", "twitter.com", "x.com", "instagram.com", "youtube.com",
              "youtu.be", "linkedin.com", "tiktok.com", "wa.me", "whatsapp.com", "t.me",
              "maps.google", "goo.gl", "play.google.com", "apps.apple.com", "snapchat.com",
              "pinterest.com", "telegram.me")
SKIP_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".css", ".js", ".ico", ".mp4",
            ".mp3", ".zip", ".rar")
FILE_EXT = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx")
EXTERNAL_OK_HOSTS = ("drive.google.com", "docs.google.com", "forms.gle")
SECOND_LEVEL = {"ac.ma", "gov.ma", "org.ma", "co.ma", "net.ma", "press.ma"}
TRACKING_PARAMS = re.compile(r"^(utm_|fbclid|gclid|mc_|ref$|share$)")


def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("ـ", "")  # tatweel
    s = s.replace("ة", "ه").replace("ى", "ي")  # common spelling variants people type
    return re.sub(r"\s+", " ", s).strip().lower()


GENERIC_LINK_TEXT = {normalize_text(t) for t in GENERIC_LINK_TEXT}


class KeywordMatcher:
    """Long keywords match anywhere (case/accent-insensitive). Short acronyms like EST, ENS,
    BTS match only as whole words, and only in UPPERCASE in titles (so the French verb "est"
    doesn't count) or anywhere in the URL slug."""

    def __init__(self, extra: list[str] | None = None):
        words = [normalize_text(k) for k in KEYWORDS + (extra or [])]
        short = sorted({w for w in words if re.fullmatch(r"[a-z]{2,5}", w)})
        long_ = sorted({w for w in words if w not in short}, key=len, reverse=True)
        alt = "|".join(map(re.escape, short))
        self._short_title = re.compile(r"(?<![A-Za-z])(" + alt.upper() + r")(?![A-Za-z])")
        self._short_slug = re.compile(r"(?<![a-z])(" + alt + r")(?![a-z])")
        self._long = re.compile("|".join(map(re.escape, long_)))

    def matches(self, title: str, slug: str = "") -> bool:
        if self._long.search(normalize_text(f"{title} {slug}")):
            return True
        plain_title = "".join(ch for ch in unicodedata.normalize("NFKD", title)
                              if not unicodedata.combining(ch))
        return bool(self._short_title.search(plain_title) or self._short_slug.search(slug.lower()))


@dataclass
class Item:
    url: str
    title: str
    key: str


def base_domain(host: str) -> str:
    host = host.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in SECOND_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def url_key(url: str) -> str:
    p = urlsplit(url)
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    query = urlencode(sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                             if not TRACKING_PARAMS.match(k.lower())))
    path = unquote(p.path).rstrip("/") or "/"
    return urlunsplit(("", host, path, query, "")).lstrip("/")


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _title_for(a) -> str:
    text = _clean(a.get_text(" "))
    if len(text) < 6 or normalize_text(text) in GENERIC_LINK_TEXT:
        alt = _clean(a.get("title"))
        if len(alt) >= 6:
            return alt[:250]
        # "Lire la suite" style link: take text from the surrounding news block
        node = a
        for _ in range(4):
            node = node.parent
            if node is None:
                break
            heading = node.find(["h1", "h2", "h3", "h4", "h5", "strong", "b"])
            if heading and len(_clean(heading.get_text(" "))) >= 6:
                return _clean(heading.get_text(" "))[:250]
            ptxt = _clean(node.get_text(" "))
            if 12 <= len(ptxt) <= 300:
                return ptxt[:250]
    return text[:250]


def _accept_link(href: str, page_url: str) -> str | None:
    href = (href or "").strip()
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return None
    url = urljoin(page_url, href)
    p = urlsplit(url)
    if p.scheme not in ("http", "https"):
        return None
    host = p.netloc.lower()
    if any(h in host for h in SKIP_HOSTS):
        return None
    path = p.path.lower()
    if path.endswith(SKIP_EXT):
        return None
    same_site = base_domain(host) == base_domain(urlsplit(page_url).netloc)
    if not same_site and not path.endswith(FILE_EXT) and not any(h in host for h in EXTERNAL_OK_HOSTS):
        return None
    # drop the bare homepage link
    if same_site and p.path.strip("/") == "" and not p.query:
        return None
    return urlunsplit((p.scheme, p.netloc, p.path, p.query, ""))


def extract_html(content: bytes, page_url: str, selector: str | None = None,
                 limit: int = 500) -> tuple[list[Item], list[str]]:
    """Returns (items, discovered_feed_urls)."""
    soup = BeautifulSoup(content, "lxml")

    feeds = []
    for link in soup.find_all("link", attrs={"rel": True, "href": True}):
        rel = " ".join(link.get("rel")).lower()
        typ = (link.get("type") or "").lower()
        if "alternate" in rel and ("rss" in typ or "atom" in typ):
            href = urljoin(page_url, link["href"])
            low = (href + " " + (link.get("title") or "")).lower()
            if "comment" not in low and "commentaire" not in low:
                feeds.append(href)

    for junk in soup.find_all(["footer", "script", "style", "noscript", "form"]):
        junk.decompose()

    roots = soup.select(selector) if selector else [soup]
    anchors = []
    for root in roots:
        if root.name == "a":
            anchors.append(root)
        anchors.extend(root.find_all("a", href=True))

    best: dict[str, Item] = {}
    for a in anchors:
        url = _accept_link(a.get("href"), page_url)
        if not url:
            continue
        title = _title_for(a)
        if not title:
            continue
        k = url_key(url)
        cur = best.get(k)
        if cur is None or (len(title) > len(cur.title) and normalize_text(title) not in GENERIC_LINK_TEXT):
            best[k] = Item(url=url, title=title, key=k)
        if len(best) >= limit:
            break
    return list(best.values()), feeds


def extract_feed(content: bytes) -> list[Item]:
    soup = BeautifulSoup(content, "xml")
    items: list[Item] = []
    for node in soup.find_all(["item", "entry"]):
        title = _clean(node.title.get_text(" ")) if node.title else ""
        link = ""
        if node.find("link"):
            ln = node.find("link")
            link = ln.get("href") or _clean(ln.get_text())
        if not link and node.find("guid"):
            link = _clean(node.find("guid").get_text())
        if link.startswith("http") and title:
            items.append(Item(url=link, title=title[:250], key=url_key(link)))
    return items


def looks_like_feed(content: bytes, content_type: str) -> bool:
    head = content[:500].lower()
    return ("xml" in content_type.lower() and b"<html" not in head) or b"<rss" in head or b"<feed" in head


def link_path_text(url: str) -> str:
    """URL path as words, so slugs like /concours-ensa-2027 count for keyword matching."""
    p = urlsplit(url)
    return re.sub(r"[-_/.+]+", " ", unquote(p.path + " " + p.query))
