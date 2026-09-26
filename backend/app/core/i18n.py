"""
Traduction de l'interface publique : français, anglais, portugais.

Le français sert de clé : les gabarits restent lisibles
(`{{ _("Rechercher") }}`) et une chaîne sans traduction s'affiche en
français plutôt qu'en identifiant technique. Les variables s'écrivent
entre accolades : `_("Page {n} sur {total}", n=2, total=9)`.

Le portugais retenu est celui de l'usage européen et africain
(Cap-Vert, Guinée-Bissau, Mozambique…), pays de l'espace CAMES ou
voisins, plutôt que la variante brésilienne.

L'administration reste en français : elle s'adresse aux équipes des
bibliothèques, pas au public international.

Les contenus eux-mêmes — titres, résumés, noms d'établissements,
domaines — ne sont pas traduits : ce sont des données, publiées dans la
langue du document. Exception : un slogan ou un nom d'institution resté
à sa valeur par défaut est traduit, puisqu'il figure au catalogue.
"""
from __future__ import annotations

import json

LANGUES = ("fr", "en", "pt")
LANGUE_DEFAUT = "fr"
COOKIE = "lang"

# français → (anglais, portugais)
TRADUCTIONS: dict[str, tuple[str, str]] = {
    # En-tête, navigation, pied de page
    "Aller au contenu": ("Skip to content", "Ir para o conteúdo"),
    "accueil": ("home", "início"),
    "Ouvrir le menu": ("Open menu", "Abrir o menu"),
    "Navigation principale": ("Main navigation", "Navegação principal"),
    "Thèses": ("Theses", "Teses"),
    "Mémoires": ("Master's theses", "Dissertações"),
    "Établissements": ("Institutions", "Instituições"),
    "À propos": ("About", "Sobre"),
    "Aide": ("Help", "Ajuda"),

    # Recherche avancée
    "Recherche avancée": ("Advanced search", "Pesquisa avançada"),
    "Recherche simple": ("Simple search", "Pesquisa simples"),
    "Opérateur entre les champs": ("Operator between fields", "Operador entre os campos"),
    "ET": ("AND", "E"),
    "OU": ("OR", "OU"),
    "ET : chaque critère doit être rempli. OU : un seul suffit.": (
        "AND: every criterion must match. OR: one is enough.",
        "E: todos os critérios devem ser cumpridos. OU: basta um."),
    "Champ": ("Field", "Campo"),
    "Texte à rechercher": ("Text to search for", "Texto a pesquisar"),
    "Retirer ce critère": ("Remove this criterion", "Retirar este critério"),
    "Réinitialiser les champs": ("Reset fields", "Repor os campos"),
    "Ajouter un critère": ("Add a criterion", "Adicionar um critério"),
    "Tous les champs": ("All fields", "Todos os campos"),
    "Titre": ("Title", "Título"),
    "Auteur": ("Author", "Autor"),
    "Discipline": ("Discipline", "Disciplina"),
    "Domaine REESAO": ("REESAO field", "Domínio REESAO"),

    # Page Aide (le contenu lui-même est dans templates/public/aide/<langue>.html)
    "Conseils de recherche": ("Search tips", "Dicas de pesquisa"),
    "Comment rechercher, consulter, citer et exporter les thèses et mémoires du catalogue.": (
        "How to search, view, cite and export the theses and dissertations in the catalogue.",
        "Como pesquisar, consultar, citar e exportar as teses e dissertações do catálogo."),
    "Sur cette page": ("On this page", "Nesta página"),
    "Qu’est-ce que ce catalogue ?": ("What is this catalogue?", "O que é este catálogo?"),
    "Rechercher un document": ("Searching for a document", "Pesquisar um documento"),
    "Affiner avec les filtres": ("Refining with filters", "Refinar com os filtros"),
    "Trier et parcourir les résultats": ("Sorting and browsing results", "Ordenar e percorrer os resultados"),
    "Lire la fiche d’un document": ("Reading a document record", "Ler a ficha de um documento"),
    "Le numéro national": ("The national number", "O número nacional"),
    "Citer et exporter": ("Citing and exporting", "Citar e exportar"),
    "Questions fréquentes": ("Frequently asked questions", "Perguntas frequentes"),
    "Besoin d’aide supplémentaire ?": ("Need more help?", "Precisa de mais ajuda?"),
    "Pour une question sur un document précis, adressez-vous à la bibliothèque de l’établissement qui l’a publié. Pour une question sur la plateforme elle-même :": (
        "For a question about a specific document, contact the library of the institution that published it. For a question about the platform itself:",
        "Para uma questão sobre um documento específico, contacte a biblioteca da instituição que o publicou. Para uma questão sobre a própria plataforma:"),
    "Voir la page À propos": ("See the About page", "Ver a página Sobre"),
    "Langue du site": ("Site language", "Idioma do site"),
    "Propulsé par ScholarSync": ("Powered by ScholarSync", "Com tecnologia ScholarSync"),
    "Administration": ("Administration", "Administração"),

    # Accueil
    "Mémoires et thèses académiques": ("Academic theses and dissertations", "Teses e dissertações académicas"),
    "Plateforme académique nationale": ("National academic platform", "Plataforma académica nacional"),
    "Accédez à la production scientifique académique": (
        "Explore academic research output", "Aceda à produção científica académica"),
    "Titre, auteur, mots-clés, numéro national…": (
        "Title, author, keywords, national number…", "Título, autor, palavras-chave, número nacional…"),
    "Rechercher": ("Search", "Pesquisar"),
    "Documents indexés": ("Indexed documents", "Documentos indexados"),
    "Établissements partenaires": ("Partner institutions", "Instituições parceiras"),
    "Travaux soutenus": ("Defended works", "Trabalhos defendidos"),
    "En préparation": ("In progress", "Em preparação"),

    # Facettes
    "Affiner": ("Refine", "Refinar"),
    "Affiner la recherche": ("Refine search", "Refinar a pesquisa"),
    "Tout effacer": ("Clear all", "Limpar tudo"),
    "{n} filtre(s) actif(s)": ("{n} active filter(s)", "{n} filtro(s) ativo(s)"),
    "Rechercher dans {quoi}": ("Search in {quoi}", "Pesquisar em {quoi}"),
    "Filtrer {quoi}…": ("Filter {quoi}…", "Filtrar {quoi}…"),
    "Voir les {n} autres": ("Show {n} more", "Ver mais {n}"),
    "Aucune valeur ne correspond.": ("No matching value.", "Nenhum valor corresponde."),
    "Aucun critère disponible : le catalogue ne contient encore aucun document.": (
        "No filters available: the catalogue has no documents yet.",
        "Nenhum critério disponível: o catálogo ainda não contém documentos."),
    "Établissement": ("Institution", "Instituição"),
    "École doctorale / Faculté": ("Doctoral school / Faculty", "Escola doutoral / Faculdade"),
    "École doctorale": ("Doctoral school", "Escola doutoral"),
    "Faculté": ("Faculty", "Faculdade"),
    "Entité": ("Unit", "Unidade"),
    "Type": ("Type", "Tipo"),
    "Statut": ("Status", "Estado"),
    "Domaine": ("Field", "Área"),
    "Année": ("Year", "Ano"),
    "Langue": ("Language", "Idioma"),
    "Thèse de doctorat": ("Doctoral thesis", "Tese de doutoramento"),
    "Mémoire de master": ("Master's thesis", "Dissertação de mestrado"),
    "Thèse": ("Thesis", "Tese"),
    "Mémoire": ("Master's thesis", "Dissertação"),
    "Soutenu": ("Defended", "Defendido"),

    # Résultats
    "Pagination des résultats": ("Results pagination", "Paginação dos resultados"),
    "Page précédente": ("Previous page", "Página anterior"),
    "Précédent": ("Previous", "Anterior"),
    "Page suivante": ("Next page", "Página seguinte"),
    "Suivant": ("Next", "Seguinte"),
    "Page {n}": ("Page {n}", "Página {n}"),
    "Page {n} sur {total}": ("Page {n} of {total}", "Página {n} de {total}"),
    "sur": ("of", "de"),
    "document": ("document", "documento"),
    "documents": ("documents", "documentos"),
    "Aucun document": ("No documents", "Nenhum documento"),
    "pour": ("for", "para"),
    "Par page": ("Per page", "Por página"),
    "Trier par": ("Sort by", "Ordenar por"),
    "Pertinence": ("Relevance", "Relevância"),
    "Année décroissante": ("Newest year first", "Ano mais recente"),
    "Année croissante": ("Oldest year first", "Ano mais antigo"),
    "Ajout le plus récent": ("Recently added", "Adicionados recentemente"),
    "Titre A–Z": ("Title A–Z", "Título A–Z"),
    "Exporter": ("Export", "Exportar"),
    "notice": ("record", "registo"),
    "notices": ("records", "registos"),
    "correspondant aux filtres en cours": ("matching the current filters", "correspondentes aos filtros atuais"),
    "du catalogue": ("in the catalogue", "do catálogo"),
    "Tableur (CSV)": ("Spreadsheet (CSV)", "Folha de cálculo (CSV)"),
    "Retirer le filtre": ("Remove filter", "Remover o filtro"),
    "Sous la direction de": ("Supervised by", "Sob orientação de"),
    "Accès restreint": ("Restricted access", "Acesso restrito"),
    "Aucun document trouvé": ("No documents found", "Nenhum documento encontrado"),
    "Essayez d'autres mots-clés, ou retirez un filtre pour élargir la recherche.": (
        "Try other keywords, or remove a filter to broaden your search.",
        "Experimente outras palavras-chave ou remova um filtro para alargar a pesquisa."),
    "Réinitialiser la recherche": ("Reset search", "Reiniciar a pesquisa"),

    # Fiche document
    "Retour aux résultats": ("Back to results", "Voltar aos resultados"),
    "Résumé": ("Abstract", "Resumo"),
    "Aucun résumé n'est disponible pour ce document.": (
        "No abstract is available for this document.", "Não há resumo disponível para este documento."),
    "Mots-clés": ("Keywords", "Palavras-chave"),
    "Jury": ("Committee", "Júri"),
    "Travaux proches": ("Related works", "Trabalhos relacionados"),
    "Voir tous les travaux en {domaine}": ("See all works in {domaine}", "Ver todos os trabalhos em {domaine}"),
    "Voir tous les travaux de {etab}": ("See all works from {etab}", "Ver todos os trabalhos de {etab}"),
    "Contactez {etab} pour consulter ce document.": (
        "Contact {etab} to consult this document.", "Contacte {etab} para consultar este documento."),
    "Consulter le document": ("View the document", "Consultar o documento"),
    "Redirige vers le dépôt de l'établissement": (
        "Opens the institution's repository", "Abre o repositório da instituição"),
    "Non disponible en ligne": ("Not available online", "Não disponível em linha"),
    "La notice est référencée, le texte intégral n'est pas encore déposé.": (
        "The record is listed; the full text has not been deposited yet.",
        "O registo está referenciado; o texto integral ainda não foi depositado."),
    "Numéro national": ("National number", "Número nacional"),
    "Numéro national copié :": ("National number copied:", "Número nacional copiado:"),
    "Attribué à la soutenance": ("Assigned upon defence", "Atribuído após a defesa"),
    "Copier": ("Copy", "Copiar"),
    "Citer ce document": ("Cite this document", "Citar este documento"),
    "Citation APA copiée.": ("APA citation copied.", "Citação APA copiada."),
    "Copier la citation": ("Copy citation", "Copiar a citação"),
    "Copie impossible — sélectionnez le texte à la main.": (
        "Copy failed — please select the text manually.",
        "Não foi possível copiar — selecione o texto manualmente."),
    "Informations": ("Details", "Informações"),
    "Direction": ("Supervisor", "Orientação"),
    "Recherches liées": ("Related searches", "Pesquisas relacionadas"),
    "Explorer": ("Explore", "Explorar"),
    "Tous les travaux de {etab}": ("All works from {etab}", "Todos os trabalhos de {etab}"),
    "Travaux de {annee}": ("Works from {annee}", "Trabalhos de {annee}"),

    # Établissements, À propos
    "{n} établissements indexés dans {outil}": (
        "{n} institutions indexed in {outil}", "{n} instituições indexadas em {outil}"),
    "docs": ("docs", "docs"),
    "Aucun établissement configuré": ("No institutions configured", "Nenhuma instituição configurada"),
    "Le contenu de cette page peut être configuré dans l'espace d'administration.": (
        "This page's content can be set up in the administration area.",
        "O conteúdo desta página pode ser configurado na área de administração."),
    "Contact": ("Contact", "Contacto"),

    # Pages d'erreur
    "Retour à l'accueil": ("Back to home", "Voltar ao início"),
    "Rechercher un document": ("Search for a document", "Pesquisar um documento"),
    "Page introuvable": ("Page not found", "Página não encontrada"),
    "Cette adresse ne correspond à aucune page. Le document a peut-être été retiré ou l'adresse mal recopiée.": (
        "This address does not match any page. The document may have been removed, or the address mistyped.",
        "Este endereço não corresponde a nenhuma página. O documento pode ter sido removido ou o endereço mal copiado."),
    "Accès refusé": ("Access denied", "Acesso negado"),
    "Vous n'avez pas les droits nécessaires pour consulter cette page.": (
        "You do not have permission to view this page.",
        "Não tem permissão para consultar esta página."),
    "Une erreur est survenue": ("An error occurred", "Ocorreu um erro"),
    "Document retiré": ("Document withdrawn", "Documento retirado"),
    "Ce document a été retiré du catalogue.": (
        "This document has been withdrawn from the catalogue.",
        "Este documento foi retirado do catálogo."),
    "Numéro national : {n}.": ("National number: {n}.", "Número nacional: {n}."),
    "Erreur interne": ("Internal error", "Erro interno"),
    "Le serveur a rencontré un problème. L'incident a été enregistré.": (
        "The server ran into a problem. The incident has been logged.",
        "O servidor encontrou um problema. O incidente foi registado."),
}

_INDEX = {"en": 0, "pt": 1}


def traduire(texte: str, langue: str = LANGUE_DEFAUT, **valeurs) -> str:
    """Traduit `texte` (français) dans `langue`, puis remplace les {variables}."""
    if langue in _INDEX and texte in TRADUCTIONS:
        texte_traduit = TRADUCTIONS[texte][_INDEX[langue]] or texte
    else:
        texte_traduit = texte
    if valeurs:
        try:
            return texte_traduit.format(**valeurs)
        except (KeyError, IndexError, ValueError):
            return texte_traduit
    return texte_traduit


def langues_actives(params: dict) -> list[str]:
    """Langues cochées dans Paramètres généraux ; le français toujours."""
    try:
        choix = json.loads((params or {}).get("langues_actives") or "[]")
    except (TypeError, ValueError):
        choix = []
    actives = [l for l in LANGUES if l in choix]
    return actives if LANGUE_DEFAUT in actives else [LANGUE_DEFAUT] + actives


def langue_de(request, params: dict | None = None) -> str:
    """Langue d'affichage : ?lang=, puis le cookie, sinon le français."""
    actives = langues_actives(params) if params is not None else list(LANGUES)
    demande = (request.query_params.get("lang") or "").lower()
    if demande in actives:
        return demande
    cookie = (request.cookies.get(COOKIE) or "").lower()
    if cookie in actives:
        return cookie
    # Pas de bascule automatique sur la langue du navigateur : un portail
    # national s'ouvre dans sa langue principale, et le visiteur choisit.
    return LANGUE_DEFAUT
