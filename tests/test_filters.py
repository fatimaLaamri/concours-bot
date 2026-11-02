"""Filter test built from real titles the bot sent in its first week.

    python -m tests.test_filters
"""
from bot.config import Site
from bot.filters import AnnouncementFilter

school = Site(id="ensa_x", name="ENSA X", url="https://x.ac.ma/", category="ensa")
ministry = Site(id="men", name="Ministère", url="https://x.gov.ma/", category="portal")
news = Site(id="tawjihnet", name="Tawjihnet", url="https://t.net/", category="aggregator")

KEEP = [
    (school, "Préinscription en 1re année de Master et du Cycle Ingénieur – FST Tanger"),
    (school, "Résultat du concours d’accès à la 1ère année du cycle Master SDIA et réouverture des candidatures"),
    (school, "Liste des candidats convoqués pour passer le concours écrit d’accès à la 1ère année du cycle Master SDIA"),
    (school, "AVIS D’INSCRIPTION DES ÉTUDIANTS ISSUS DU CONCOURS NATIONAL COMMUN (CNC) ADMIS EN 1ÈRE ANNÉE"),
    (school, "RESULTATS FINAUX DU CONCOURS D'ENTREE 2026/2027 POUR LES FILIERES TECHNIQUES"),
    (school, "Avis - Liste d’attente 3 - Master Matériaux Avancés et Énergies Renouvelables"),
    (school, "إعلان بشأن تسجيل حاملي شهادة الأقسام التحضيرية و شهادة التقني العالي"),
    (school, "إعادة فتح باب الترشيح لولوج ماستر المقاربة البلاغية للخطابات"),
    (school, "إعلان عن مباراة ولوج سلك المهندس"),
    (ministry, "النتائج النهائية لمباريات ولوج السنة الأولى من سلك تحضير مباريات التبريز"),
    (ministry, "لوائح بأسماء المترشحات والمترشحين المدعوين لاجتياز الاختبارات الشفوية لمباريات توظيف أطر إدارية"),
    (news, "Résultats 2eme Session ENS Meknès Bac Licence Education Préscolaire S1 2026 2027"),
]

DROP = [
    # navigation
    (school, "suivant ›"), (school, "dernier »"), (school, "Page 2"), (school, "Current page 1"),
    (school, "Last page Last »"), (school, "Doc. utiles"), (school, "LICENCE"), (school, "Concours"),
    (school, "Conditions d’accès"), (ministry, "Démarrer ma candidature"), (ministry, "M'inscrire"),
    (school, "V5h9Lp-8D6oSguXIkju-Y1dOyNbE-dYrpdK-PUBMhpk=.html"), (school, "Télécharger l’appel (PDF)"),
    (news, "الباكالوريا المهنية"),
    # events
    (school, "Soutenance de thèse de doctorat de Mme. EL HAKIMI Ilham"),
    (school, "6ᵉ Conférence internationale de Settat sur les algèbres d’opérateurs (ICSOAA 2026)"),
    (school, "Cérémonie de remise des diplômes aux lauréats de l’Académie"),
    (school, "Appel à communication : 1ʳᵉ édition du Colloque International CIGED’26"),
    (school, "Appel à Candidature au Business Game Innov'Sphère CIVIS"),
    (school, "عيد الوحدة: عيد التلاحم الوطني والوفاء للعرش العلوي المجيد"),
    (ministry, "فتح باب الترشيح لجائزة « تميز للمرأة المغربية » في دورتها الحادية عشرة"),
    # internal student life
    (school, "Calendrier Universitaire 2026-2027 : Session d'Automne"),
    (school, "Avis aux étudiants de 6ème année de médecine : Séminaire de méthodologie"),
    (school, "AVIS AUX ÉTUDIANTS : PROCÉDURE DE CONNEXION AU WIFI CAMPUS CONNECTÉ"),
    (school, "Avis important aux étudiants - Demandes de transfert et d’inscription par dérogation"),
    (school, "Appel à candidatures – Mobilité à l’Université de Sienne (Italie) – Erasmus+ KA171"),
    (school, "إعلان لفائدة طلبة السداسية الخامسة التوقيت الميسر، بشان فتح باب التسجيل بمسلك القانون"),
    # staff recruitment and doctorate (school sites)
    (school, "Concours de recrutement des Maitres de Conférences Au profit de la Faculté de Médecine"),
    (school, "إعلان عن مباراة توظيف متصرف من الدرجة الثانية"),
    (school, "Liste des candidats retenus pour inscription en doctorat 2026–2027"),
    (school, "لائحة المترشحين المقبولين لاجتياز المقابلة لولوج سلك الدكتوراه"),
]


def main():
    f = AnnouncementFilter()
    bad = [("should KEEP", t) for s, t in KEEP if not f.accept(s, t, s.url + "a")]
    bad += [("should DROP", t) for s, t in DROP if f.accept(s, t, s.url + "a")]
    for why, t in bad:
        print(why, "|", t)
    assert not bad, f"{len(bad)} wrong decisions"
    assert not f.accept(Site(id="a", name="a", url="u", category="faculty", filter="strict"),
                        "Fermeture de l'administration le vendredi", "u")
    print(f"✓ {len(KEEP)} real announcements kept, {len(DROP)} noise links dropped")

    from bot.extractor import title_core as core
    same = [("calendrier de la session d'automne 2026-2027",
             "30 octobre 2026 calendrier de la session d'automne 2026-2027 Nouveau Important"),
            ("Test écrit Master en Neurpsychologie Clinique 2026… 21 Oct 26 /",
             "Test écrit Master en Neurpsychologie Clinique 2026"),
            ("01 نونبر 2026 لوائح المترشحين", "لوائح المترشحين")]
    different = [("Concours ENSA 2026-2027", "Concours ENSA 2027-2028"),
                 ("Résultats concours ENSA liste principale", "Résultats concours ENSA liste d'attente")]
    assert all(core(a) == core(b) for a, b in same)
    assert all(core(a) != core(b) for a, b in different)
    print("✓ same announcement with new dates/badges is a duplicate; next school year is not")
    print("\nALL FILTER TESTS PASSED")


if __name__ == "__main__":
    main()
