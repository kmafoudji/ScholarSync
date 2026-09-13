"""Assainissement du HTML saisi dans l'éditeur de la page « À propos ».

Le contenu est ensuite rendu avec le filtre `|safe` : il est donc inséré
tel quel dans la page publique. Sans filtrage, tout compte
d'administration pourrait y déposer un `<script>` exécuté chez chaque
visiteur — et un compte d'établissement n'a pas à disposer de ce
pouvoir-là. Le nettoyage se fait donc à l'enregistrement, côté serveur :
le contrôle côté navigateur ne protège de rien, il suffit d'envoyer la
requête POST directement.

Principe retenu : **liste blanche et réécriture**. On analyse le
document, on ne conserve que les balises et attributs explicitement
autorisés, puis on ré-émet un HTML propre. C'est plus sûr qu'une liste
noire ou qu'une expression régulière, qui cherchent à reconnaître les
formes dangereuses — exercice qu'on perd toujours, tant les variantes
d'encodage sont nombreuses.
"""

from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse

# Balises conservées : de quoi écrire une page de présentation — titres,
# paragraphes, listes, tableaux, liens, emphase. Rien qui exécute, rien
# qui charge une ressource tierce, rien qui positionne.
BALISES = {
    "p", "br", "strong", "b", "em", "i", "u", "s", "sub", "sup",
    "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li",
    "blockquote", "pre", "code",
    "a", "hr",
    "table", "thead", "tbody", "tr", "th", "td",
    "figure", "figcaption", "img",
    "div", "span",
}

# Ces balises sont supprimées *avec leur contenu* : le texte d'un
# <script> n'est pas du texte, c'est du code.
BALISES_MUETTES = {"script", "style", "iframe", "object", "embed",
                   "template", "noscript", "svg", "math"}

AUTOFERMANTES = {"br", "hr", "img"}

# Les attributs suivent la même logique : `href` pour naviguer, `alt`
# pour décrire. Aucun `style`, aucun `on*` — un gestionnaire d'événement
# est du script sous un autre nom.
ATTRIBUTS = {
    "a": {"href", "title", "target", "rel"},
    "img": {"src", "alt", "title", "width", "height"},
    "th": {"colspan", "rowspan", "scope"},
    "td": {"colspan", "rowspan"},
    "*": set(),
}

SCHEMAS_AUTORISES = {"http", "https", "mailto", "tel"}

# Profondeur maximale : un document profondément imbriqué n'apporte rien
# à une page de présentation et peut faire ramer le rendu du navigateur.
PROFONDEUR_MAX = 20
LONGUEUR_MAX = 200_000


def _url_sure(valeur: str) -> bool:
    """Autorise les liens relatifs et les schémas usuels.

    `javascript:alert(1)` et `data:text/html,...` sont les deux formes
    qui transforment un href en exécution de code : elles sont écartées
    par le fait même de n'accepter qu'une liste fermée de schémas.
    """
    valeur = (valeur or "").strip()
    if not valeur:
        return False
    # Les caractères de contrôle servent à masquer un schéma
    # (« java\tscript: ») : le navigateur les ignore, pas nous.
    if any(ord(c) < 32 for c in valeur):
        return False
    try:
        schema = (urlparse(valeur).scheme or "").lower()
    except ValueError:
        return False
    if not schema:          # relatif : /page, ../x, #ancre
        return True
    return schema in SCHEMAS_AUTORISES


class _Nettoyeur(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.morceaux = []
        self.pile = []
        self.muet = 0          # profondeur à l'intérieur d'une balise muette

    # ── balises ouvrantes ───────────────────────────────────────
    def handle_starttag(self, tag, attrs):
        if tag in BALISES_MUETTES:
            self.muet += 1
            return
        if self.muet or tag not in BALISES:
            return
        if len(self.pile) >= PROFONDEUR_MAX and tag not in AUTOFERMANTES:
            return

        permis = ATTRIBUTS.get(tag, ATTRIBUTS["*"])
        gardes = []
        for nom, valeur in attrs:
            nom = (nom or "").lower()
            if nom not in permis:
                continue
            valeur = valeur or ""
            if nom in ("href", "src") and not _url_sure(valeur):
                continue
            gardes.append((nom, valeur))

        # Un lien ouvert dans un nouvel onglet sans rel="noopener" donne
        # à la page cible la main sur la nôtre via window.opener.
        if tag == "a":
            noms = {n for n, _ in gardes}
            if "target" in noms:
                gardes = [(n, v) for n, v in gardes if n != "rel"]
                gardes.append(("rel", "noopener noreferrer"))

        rendu = "".join(
            f' {nom}="{escape(valeur, quote=True)}"' for nom, valeur in gardes
        )
        if tag in AUTOFERMANTES:
            self.morceaux.append(f"<{tag}{rendu}>")
        else:
            self.morceaux.append(f"<{tag}{rendu}>")
            self.pile.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag in AUTOFERMANTES:
            self.handle_starttag(tag, attrs)
        else:
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

    # ── balises fermantes ───────────────────────────────────────
    def handle_endtag(self, tag):
        if tag in BALISES_MUETTES:
            self.muet = max(0, self.muet - 1)
            return
        if self.muet or tag in AUTOFERMANTES or tag not in BALISES:
            return
        if tag not in self.pile:
            return          # fermeture orpheline : on l'ignore
        # Referme tout ce qui est resté ouvert au-dessus, pour ne jamais
        # produire un document déséquilibré qui déborderait sur le reste
        # de la page.
        while self.pile:
            ouverte = self.pile.pop()
            self.morceaux.append(f"</{ouverte}>")
            if ouverte == tag:
                break

    # ── texte ───────────────────────────────────────────────────
    def handle_data(self, data):
        if self.muet:
            return
        self.morceaux.append(escape(data, quote=False))

    # Commentaires et déclarations : rien à en tirer, et
    # « <!--[if IE]><script> » a longtemps servi à passer du code.
    def handle_comment(self, data): pass
    def handle_decl(self, decl): pass
    def handle_pi(self, data): pass
    def unknown_decl(self, data): pass

    def resultat(self) -> str:
        while self.pile:
            self.morceaux.append(f"</{self.pile.pop()}>")
        return "".join(self.morceaux)


def nettoyer(brut: str) -> str:
    """Renvoie une version du HTML débarrassée de tout ce qui exécute."""
    if not brut:
        return ""
    brut = brut[:LONGUEUR_MAX]
    n = _Nettoyeur()
    try:
        n.feed(brut)
        n.close()
    except Exception:
        # Un analyseur qui renonce ne doit pas laisser passer l'entrée
        # brute : en cas de doute, on ne garde que le texte.
        return escape(brut, quote=False)
    return n.resultat()


def en_texte(html_: str, limite: int = 300) -> str:
    """Version texte, pour une méta-description ou un aperçu."""
    class _Texte(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.bouts = []

        def handle_data(self, d):
            self.bouts.append(d)

    p = _Texte()
    try:
        p.feed(html_ or "")
        p.close()
    except Exception:
        return ""
    texte = " ".join("".join(p.bouts).split())
    return texte[:limite]
