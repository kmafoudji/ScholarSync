from fastapi import FastAPI, Request, Depends, HTTPException, Form, UploadFile, File, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func, extract
from typing import Optional, List
import json, os, math, logging, re, io, csv
from datetime import datetime, timedelta
from urllib.parse import urlencode

from app.core.database import get_db, engine, SessionLocal
from app.core.config import settings
from app.core.auth import (
    hash_password, verify_password, create_token, decode_token,
    get_current_user, require_auth, require_super_admin, jeton_perime
)
from app.core import tasks
from app.core.flash import read_flash, redirect_flash, set_flash, COOKIE_NAME as FLASH_COOKIE
from app.core import schema as schema_bd
from app.core import html_riche
from app.core import citations
from app.core import logos
from app.core import permissions
from app.models import (
    Base, Parametre, Etablissement, ZoteroSource,
    Document, SyncLog, Utilisateur, NumerotationCompteur, AccesException
)

# Créer les tables, puis rattraper les colonnes ajoutées après coup.
# Tolérant à une base momentanément injoignable : au premier démarrage
# PostgreSQL met quelques secondes à accepter les connexions, et faire
# échouer l'import ferait mourir le worker — le reverse proxy renverrait
# un 502 sans rien expliquer. Ici l'application démarre quand même et
# /sante dit ce qui manque.
schema_bd.initialiser(engine, Base)

app = FastAPI(title="ScholarSync", docs_url="/api/docs")

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse as StarletteRedirect

class AdminAuthMiddleware(BaseHTTPMiddleware):
    """Contrôle d'accès de toute l'administration, en un seul endroit.

    Jusqu'ici le middleware vérifiait seulement qu'un jeton était signé ;
    le rôle n'était contrôlé que dans 7 routes sur 60. Un compte
    d'établissement pouvait donc changer l'identité du site, lancer la
    synchronisation des autres universités ou supprimer leur source
    Zotero. Un compte désactivé gardait aussi l'accès aux routes POST
    tant que son jeton n'avait pas expiré.

    Désormais, à chaque requête /admin : le compte est relu en base (actif,
    rôle à jour), puis la page est confrontée à la liste blanche de
    app/core/permissions.py. L'utilisateur est posé sur request.state
    pour que les routes limitent les données à son établissement.
    """
    async def dispatch(self, request, call_next):
        path = request.url.path
        if not path.startswith("/admin") or path in permissions.ROUTES_PUBLIQUES:
            return await call_next(request)

        token = request.cookies.get("scholarsync_session")
        payload = decode_token(token) if token else None
        # Un jeton de réinitialisation de mot de passe est signé avec la
        # même clé : il ne doit pas valoir session.
        if not payload or payload.get("type") == "reset":
            return StarletteRedirect(f"/admin/connexion?next={path}")

        db = SessionLocal()
        try:
            utilisateur = db.query(Utilisateur).filter(
                Utilisateur.email == payload.get("sub"),
                Utilisateur.actif == True,  # noqa: E712
            ).first()
            if utilisateur is not None:
                if jeton_perime(payload, utilisateur):
                    # Mot de passe changé depuis l'ouverture de la session
                    utilisateur = None
                else:
                    db.expunge(utilisateur)
        except Exception:
            # Base injoignable : la route affichera l'état dégradé.
            logging.getLogger("scholarsync").warning(
                "Contrôle d'accès : base injoignable", exc_info=True
            )
            utilisateur = None
        finally:
            db.close()

        if utilisateur is None:
            reponse = StarletteRedirect("/admin/connexion")
            reponse.delete_cookie("scholarsync_session")
            return reponse

        if not permissions.autorise(utilisateur, request.method, path):
            if path.startswith("/admin/sync/etat"):
                return JSONResponse({"detail": "Accès refusé"}, status_code=403)
            message = (
                "Votre compte est en consultation seule."
                if permissions.role_de(utilisateur) == permissions.LECTEUR
                else "Cette page est réservée au super administrateur."
            )
            return redirect_flash("/admin", message, "warning")

        request.state.utilisateur = utilisateur
        return await call_next(request)

class FlashMiddleware(BaseHTTPMiddleware):
    """Lit le message flash, le rend disponible au template, puis l'efface."""
    async def dispatch(self, request, call_next):
        request.state.flash = read_flash(request)
        response = await call_next(request)
        # Effacer seulement si le message vient d'être consommé et qu'aucune
        # nouvelle notification n'a été posée par la route courante.
        if request.state.flash and FLASH_COOKIE not in response.headers.get("set-cookie", ""):
            response.delete_cookie(FLASH_COOKIE, path="/")
        return response


app.add_middleware(FlashMiddleware)
app.add_middleware(AdminAuthMiddleware)

# Fichiers statiques
app.mount("/static", StaticFiles(directory="app/static"), name="static")


# Une sync sans progression depuis ce délai est considérée abandonnée
# (processus tué, conteneur redémarré) : sans cela un SyncLog resterait
# « en_cours » indéfiniment et le bandeau ne disparaîtrait jamais.
SYNC_DELAI_ABANDON = timedelta(minutes=30)


def sync_log_actif(db: Session, utilisateur=None) -> Optional[SyncLog]:
    """
    Synchronisation réellement en cours, d'après la base.

    L'ensemble en mémoire de app.core.tasks ne connaît que le processus
    courant : derrière plusieurs workers uvicorn, le worker qui répond
    n'est pas forcément celui qui synchronise. La base est le seul état
    partagé entre eux.
    """
    requete = db.query(SyncLog).filter(SyncLog.statut == "en_cours")
    if utilisateur is not None:
        requete = filtrer_logs(db, requete, utilisateur)
    log = requete.order_by(SyncLog.debut.desc()).first()
    if log is None:
        return None

    repere = log.fin or log.debut
    if repere and datetime.now(repere.tzinfo) - repere > SYNC_DELAI_ABANDON:
        return None
    return log


def _sync_en_cours() -> bool:
    if any(k.startswith("sync") for k in tasks.running_keys()):
        return True
    db = SessionLocal()
    try:
        return sync_log_actif(db) is not None
    except Exception:
        return False
    finally:
        db.close()


# ─── Templates ────────────────────────────────────────────────────
def flash_context(request: Request) -> dict:
    """Injecte la notification et l'état de sync dans TOUS les templates."""
    flash = getattr(request.state, "flash", None)
    return {
        "flash_message": flash["message"] if flash else None,
        "flash_type": flash["type"] if flash else None,
        # Une seule requête, et seulement sur les pages d'administration
        "sync_en_cours": (
            _sync_en_cours() if request.url.path.startswith("/admin") else False
        ),
    }


templates = Jinja2Templates(
    directory="app/templates", context_processors=[flash_context]
)

# ─── Filtres Jinja2 ───────────────────────────────────────────────
import json as _json

def format_number(value):
    try:
        return f"{int(value):,}".replace(",", " ")
    except:
        return value

def format_datetime(value):
    if not value:
        return "—"
    if isinstance(value, str):
        return value[:16].replace("T", " ")
    return value.strftime("%d/%m/%Y %H:%M")

def fromjson(value):
    if not value:
        return {}
    try:
        return _json.loads(value)
    except:
        return {}

def truncate(value, length=80):
    if value and len(value) > length:
        return value[:length] + "…"
    return value or ""

templates.env.filters["format_number"] = format_number
templates.env.filters["format_datetime"] = format_datetime
templates.env.filters["fromjson"] = fromjson
LIBELLES_STATUT = {
    "succes": "Succès",
    "erreur": "Erreur",
    "en_cours": "En cours",
}


def libelle_statut(valeur):
    """« en_cours » s'affichait tel quel, tiret bas compris."""
    if not valeur:
        return "—"
    return LIBELLES_STATUT.get(valeur, str(valeur).replace("_", " ").capitalize())


def duree_lisible(secondes):
    """3600 s se lisait « 3600s » : on veut « 1 h 0 min »."""
    try:
        secondes = int(secondes)
    except (TypeError, ValueError):
        return "—"
    if secondes < 60:
        return f"{secondes} s"
    if secondes < 3600:
        return f"{secondes // 60} min {secondes % 60} s"
    return f"{secondes // 3600} h {(secondes % 3600) // 60} min"


templates.env.filters["truncate"] = truncate
def url_facette(filtres: dict, cle: str, valeur, sort: str = "recent") -> str:
    """
    Adresse de la page avec cette valeur de facette basculée.

    Les facettes sont des liens, pas des éléments pilotés par script : la
    navigation au clavier, l'ouverture dans un nouvel onglet et le
    fonctionnement sans JavaScript en découlent gratuitement. Le script
    ne fait qu'intercepter le clic pour éviter le rechargement.
    """
    copie = {k: (list(v) if isinstance(v, list) else v) for k, v in filtres.items()}
    valeur = str(valeur)

    actuelles = copie.get(cle) or []
    if isinstance(actuelles, str):
        actuelles = [actuelles]
    actuelles = [str(v) for v in actuelles]

    if valeur in actuelles:
        actuelles = [v for v in actuelles if v != valeur]
    else:
        actuelles = actuelles + [valeur]

    if actuelles:
        copie[cle] = actuelles
    else:
        copie.pop(cle, None)

    # Changer de filtre remet à la première page : rester page 4 d'un
    # résultat qui n'en compte plus qu'une afficherait une liste vide.
    qs = construire_query_string(copie, sort)
    return f"/recherche?{qs}" if qs else "/recherche"


def facette_active(filtres: dict, cle: str, valeur) -> bool:
    actuelles = filtres.get(cle) or []
    if isinstance(actuelles, str):
        actuelles = [actuelles]
    return str(valeur) in [str(v) for v in actuelles]


# ─── Versionnage des fichiers statiques ───────────────────────────
# Sans marqueur de version, le navigateur garde la feuille de style
# précédente après un déploiement : le gabarit est à jour, le CSS non, et
# la page s'affiche à moitié cassée sans qu'aucune erreur ne le signale.
# Le marqueur est calculé une fois au démarrage, à partir de la taille et
# de la date du fichier — une reconstruction d'image change forcément la
# date, donc le marqueur.
_versions_statiques: dict = {}


def static_url(chemin: str) -> str:
    """Adresse d'un fichier statique, suffixée d'un marqueur de version."""
    if chemin not in _versions_statiques:
        marqueur = ""
        try:
            st = os.stat(os.path.join("app/static", chemin))
            marqueur = f"{int(st.st_mtime):x}{st.st_size:x}"[-10:]
        except OSError:
            # Fichier absent : on sert l'adresse nue plutôt que d'échouer
            logging.getLogger("scholarsync").warning(
                "Fichier statique introuvable : %s", chemin
            )
        _versions_statiques[chemin] = marqueur
    marqueur = _versions_statiques[chemin]
    return f"/static/{chemin}?v={marqueur}" if marqueur else f"/static/{chemin}"


templates.env.globals["static_url"] = static_url
templates.env.globals["url_facette"] = url_facette
templates.env.globals["facette_active"] = facette_active
templates.env.filters["libelle_statut"] = libelle_statut
templates.env.filters["duree_lisible"] = duree_lisible
templates.env.globals["now"] = datetime.now

# ─── Helpers ──────────────────────────────────────────────────────
def get_params(db: Session) -> dict:
    rows = db.query(Parametre).all()
    return {r.cle: r.valeur for r in rows}

def get_params_with_defaults(db: Session) -> dict:
    defaults = {
        "nom_outil": "ScholarSync",
        "slogan": "Mémoires et thèses académiques",
        "logo_url": "",
        "couleur_principale": "#1a3a5c",
        "couleur_secondaire": "#8b1a2e",
        "bande_decorative": "custom",
        "bande_couleurs": '["#1a3a5c","rgba(255,255,255,0.5)","#8b1a2e"]',
        "icones_filigrane": "academique",
        "apropos_fr": "",
        "apropos_en": "",
        "apropos_pt": "",
        "contact_json": "{}",
        "partenaires_json": "[]",
        "institution_nom": "",
        "pied_page_texte": "",
        "langues_actives": '["fr","en","pt"]',
        "sync_intervalle_min": "60",
    }
    stored = get_params(db)
    defaults.update(stored)
    return defaults

def get_stats(db: Session, etablissement_code: str = None) -> dict:
    q = db.query(Document)
    if etablissement_code:
        q = q.filter(Document.etablissement_code == etablissement_code)
    total = q.count()
    return {
        "total": total,
        "nb_theses": q.filter(Document.type == "these").count(),
        "nb_memoires": q.filter(Document.type == "memoire").count(),
        "nb_soutenus": q.filter(Document.statut == "soutenu").count(),
        "nb_en_preparation": q.filter(Document.statut == "en_preparation").count(),
        "nb_preparation": q.filter(Document.statut == "en_preparation").count(),
        "nb_etablissements": db.query(func.count(func.distinct(Document.etablissement_code))).scalar() or 0,
        "nouveaux_7j": 0,
    }

def construire_query_string(filtres: dict, sort: str = "recent",
                            par_page: int = None) -> str:
    """Chaîne de requête conservant les valeurs multiples et l'encodage."""
    paires = []
    for cle, valeur in filtres.items():
        if cle == "q":
            if valeur:
                paires.append(("q", valeur))
        else:
            for v in (valeur if isinstance(valeur, list) else [valeur]):
                paires.append((cle, str(v)))
    if sort and sort != "recent":
        paires.append(("sort", sort))
    if par_page and par_page != PAR_PAGE_DEFAUT:
        paires.append(("par_page", str(par_page)))
    return urlencode(paires)


# Facettes à choix multiple : les valeurs arrivent sous forme de listes.
CHAMPS_FACETTES = {
    "type": Document.type,
    "statut": Document.statut,
    "langue": Document.langue,
    "annee": Document.annee,
    "etablissement": Document.etablissement_code,
    "domaine": Document.domaine,
    "sous_entite": Document.sous_entite_nom,
}


def _valeurs(filtres: dict, cle: str) -> list:
    """Normalise en liste : une facette accepte plusieurs valeurs."""
    v = filtres.get(cle)
    if not v:
        return []
    return [v] if isinstance(v, str) else list(v)


def appliquer_filtres(query, filtres: dict, sauf: str = None):
    """
    Applique les filtres à une requête.

    `sauf` exclut une facette : pour compter les options d'une facette, il
    faut appliquer tous les AUTRES filtres mais pas le sien, sinon
    sélectionner « Thèse » ramènerait le compte de « Mémoire » à zéro et
    l'utilisateur ne pourrait plus élargir sa recherche.
    """
    for cle, colonne in CHAMPS_FACETTES.items():
        if cle == sauf:
            continue
        valeurs = _valeurs(filtres, cle)
        if not valeurs:
            continue
        if cle == "annee":
            valeurs = [int(v) for v in valeurs if str(v).isdigit()]
            if not valeurs:
                continue
        query = query.filter(colonne.in_(valeurs))

    texte = filtres.get("q")
    if texte:
        motif = f"%{texte}%"
        query = query.filter(
            Document.titre.ilike(motif)
            | Document.auteur.ilike(motif)
            | Document.numero_national.ilike(motif)
            | Document.resume.ilike(motif)
        )
    return query


def _compter(db: Session, colonne, filtres: dict, cle: str, trier_par_compte=True):
    q = appliquer_filtres(db.query(colonne, func.count().label("n")), filtres, sauf=cle)
    q = q.filter(colonne.isnot(None)).group_by(colonne)
    q = q.order_by(func.count().desc()) if trier_par_compte else q.order_by(colonne.desc())
    return [{"valeur": r[0], "count": r[1]} for r in q.all()]


def get_facettes(db: Session, filtres: dict = None) -> dict:
    """
    Comptes de facettes contextuels.

    La version précédente construisait une requête filtrée puis ne s'en
    servait jamais : chaque compte était global. Sélectionner un
    établissement laissait les autres facettes afficher les totaux de tout
    le catalogue, ce qui rendait les nombres faux dès qu'un filtre était
    posé.
    """
    filtres = filtres or {}

    # Les établissements sont stockés par code : un lecteur cherche
    # « Gaston Berger », pas « UGB ». On rattache le nom pour l'affichage
    # et pour la recherche dans la facette.
    noms_etabs = {
        e.code: e.nom
        for e in db.query(Etablissement.code, Etablissement.nom).all()
    }
    etablissements = _compter(db, Document.etablissement_code, filtres, "etablissement")
    for o in etablissements:
        o["libelle"] = noms_etabs.get(o["valeur"], o["valeur"])

    facettes = {
        "etablissements": etablissements,
        "types":          _compter(db, Document.type, filtres, "type"),
        "statuts":        _compter(db, Document.statut, filtres, "statut"),
        "domaines":       _compter(db, Document.domaine, filtres, "domaine"),
        "langues":        _compter(db, Document.langue, filtres, "langue"),
        "annees":         _compter(db, Document.annee, filtres, "annee", trier_par_compte=False),
        "sous_entites":   [],
    }

    # Les sous-entités n'ont de sens qu'une fois un établissement choisi :
    # sinon la liste mélangerait les facultés de tous les établissements.
    if _valeurs(filtres, "etablissement"):
        facettes["sous_entites"] = _compter(
            db, Document.sous_entite_nom, filtres, "sous_entite"
        )

    # Total après application de tous les filtres, pour l'entrée « Tous ».
    facettes["total"] = appliquer_filtres(db.query(Document), filtres).count()
    return facettes


PAR_PAGE_POSSIBLES = (10, 20, 50, 100)
PAR_PAGE_DEFAUT = 20


def normaliser_par_page(valeur) -> int:
    """Une valeur hors liste viendrait d'une URL bricolée : on retombe sur
    la valeur par défaut plutôt que de laisser demander 100 000 lignes."""
    try:
        valeur = int(valeur)
    except (TypeError, ValueError):
        return PAR_PAGE_DEFAUT
    return valeur if valeur in PAR_PAGE_POSSIBLES else PAR_PAGE_DEFAUT


def paginate(query, page: int, per_page: int = PAR_PAGE_DEFAUT):
    total = query.count()
    pages = math.ceil(total / per_page) if total else 1
    # Une page hors bornes (lien périmé, filtre qui réduit le résultat)
    # doit ramener à une page existante, pas afficher une liste vide.
    page = max(1, min(int(page or 1), pages))
    items = query.offset((page - 1) * per_page).limit(per_page).all()
    page_range = []
    if pages <= 7:
        page_range = list(range(1, pages + 1))
    else:
        if page <= 4:
            page_range = list(range(1, 6)) + ["...", pages]
        elif page >= pages - 3:
            page_range = [1, "..."] + list(range(pages - 4, pages + 1))
        else:
            page_range = [1, "...", page - 1, page, page + 1, "...", pages]
    debut = (page - 1) * per_page + 1 if total else 0
    return items, {
        "total": total, "pages": pages, "page": page, "per_page": per_page,
        "range": page_range,
        # Position affichée en permanence : « 21–40 sur 340 » situe le
        # lecteur même quand il n'y a qu'une seule page.
        "debut": debut,
        "fin": min(page * per_page, total),
        "options_par_page": PAR_PAGE_POSSIBLES,
    }


# ─── TRI DES TABLEAUX D'ADMINISTRATION ────────────────────────────
#
# Le nom de colonne arrive de l'URL : il ne doit jamais toucher SQL
# directement. Chaque page déclare la liste des colonnes triables ; tout
# ce qui n'y figure pas retombe sur le tri par défaut. C'est aussi ce qui
# garantit qu'un lien périmé donne une page correcte plutôt qu'une erreur.

def appliquer_tri(query, tri, sens, colonnes: dict, defaut: str):
    """Ordonne `query` et renvoie (query, etat) où `etat` décrit le tri
    retenu pour que le gabarit puisse flécher la bonne colonne.

    `colonnes` associe une clé publique à une colonne SQLAlchemy, ou à un
    couple (colonne, sens_par_defaut) : une date se lit du plus récent au
    plus ancien, un nom de A à Z — le premier clic doit faire ce que le
    lecteur attend.
    """
    cle = tri if tri in colonnes else defaut
    entree = colonnes[cle]
    colonne, sens_naturel = entree if isinstance(entree, tuple) else (entree, "asc")

    # `sens` absent : on prend le sens naturel de la colonne. Présent mais
    # aberrant : on prend « asc » plutôt que de le refuser bruyamment.
    if sens not in ("asc", "desc"):
        sens = sens_naturel if tri in colonnes else sens_naturel

    ordre = colonne.asc() if sens == "asc" else colonne.desc()
    return query.order_by(ordre), {"cle": cle, "sens": sens}


def url_maj(request: Request, **modifs) -> str:
    """Adresse courante avec quelques paramètres changés.

    Sert aux en-têtes de tri et aux liens de pagination : ils doivent
    conserver la recherche et les filtres en cours, sinon changer de page
    revient à tout perdre. Un paramètre mis à None ou à la chaîne vide est
    retiré, ce qui permet de revenir à l'état par défaut sans le nommer.
    """
    params = dict(request.query_params)
    for cle, valeur in modifs.items():
        if valeur is None or valeur == "":
            params.pop(cle, None)
        else:
            params[cle] = str(valeur)
    chaine = urlencode(params)
    return f"{request.url.path}?{chaine}" if chaine else request.url.path


templates.env.globals["url_maj"] = url_maj
# La limite affichée dans les formulaires doit être celle réellement
# appliquée : deux valeurs qui divergent, c'est un refus incompris.
templates.env.globals["max_logo_mo"] = settings.MAX_LOGO_SIZE_MB

# ─── SANTÉ ────────────────────────────────────────────────────────
@app.get("/sante")
async def sante(db: Session = Depends(get_db)):
    """
    Sonde utilisée par le healthcheck Docker et le script de déploiement.
    Vérifie que la base répond : un conteneur qui démarre alors que
    PostgreSQL est injoignable ne doit pas être déclaré sain.
    """
    from sqlalchemy import text
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        return JSONResponse(
            {"statut": "degrade", "base": "injoignable",
             "detail": str(e)[:200],
             "piste": "Vérifiez POSTGRES_PASSWORD dans .env : après une "
                      "rotation de secrets sur une base existante, il doit "
                      "être changé des deux côtés (docs/deploiement.md)."},
            status_code=503,
        )

    if not schema_bd.etat["pret"]:
        # La base répond mais le schéma n'a pas pu être initialisé :
        # on retente maintenant plutôt que d'attendre un redémarrage.
        schema_bd.initialiser(engine, Base, tentatives=1)
        if not schema_bd.etat["pret"]:
            return JSONResponse(
                {"statut": "degrade", "base": "ok", "schema": "non initialisé",
                 "detail": schema_bd.etat["erreur"]},
                status_code=503,
            )

    return {"statut": "ok", "base": "ok", "schema": "ok"}


# ─── ROUTES PUBLIQUES ─────────────────────────────────────────────

TRIS = {
    "annee":  (Document.annee.desc(), Document.titre.asc()),
    "ancien": (Document.annee.asc(), Document.titre.asc()),
    "titre":  (Document.titre.asc(),),
    "recent": (Document.created_at.desc(),),
}

TRI_DEFAUT = "annee"


def contexte_catalogue(request: Request, db: Session, filtres: dict,
                       sort: str, page: int,
                       par_page: int = PAR_PAGE_DEFAUT) -> dict:
    """
    Contexte du catalogue, partagé par l'accueil et la recherche.

    Les deux routes construisaient auparavant le même contexte chacune de
    leur côté, avec un tri écrit en dur d'un côté seulement : toute
    évolution devait être reportée deux fois, et l'accueil affichait déjà
    un tri différent de celui annoncé par son propre sélecteur.
    """
    if sort not in TRIS:
        sort = TRI_DEFAUT

    par_page = normaliser_par_page(par_page)
    query = appliquer_filtres(db.query(Document), filtres).order_by(*TRIS[sort])
    docs, pagination = paginate(query, page, par_page)

    return {
        "request": request,
        "params": get_params_with_defaults(db),
        "stats": get_stats(db),
        "facettes": get_facettes(db, filtres),
        "documents": docs,
        "pagination": pagination,
        "current_filters": filtres,
        "sort": sort,
        "query_string": construire_query_string(filtres, sort, par_page),
        "nb_filtres_actifs": sum(
            len(v) for k, v in filtres.items() if k != "q"
        ) + (1 if filtres.get("q") else 0),
        "annees_recentes": [],
        "active_nav": "accueil",
        "lang": "fr",
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "public/index.html",
        contexte_catalogue(request, db, filtres={}, sort=TRI_DEFAUT, page=1),
    )

@app.get("/recherche", response_class=HTMLResponse)
async def recherche(
    request: Request, db: Session = Depends(get_db),
    q: str = "",
    # Query(...) en liste : chaque facette accepte plusieurs valeurs
    # (?type=these&type=memoire), ce que la version précédente ne
    # permettait pas — un clic remplaçait la sélection au lieu de s'y
    # ajouter.
    type: List[str] = Query(default=[]),
    statut: List[str] = Query(default=[]),
    etablissement: List[str] = Query(default=[]),
    domaine: List[str] = Query(default=[]),
    annee: List[str] = Query(default=[]),
    langue: List[str] = Query(default=[]),
    sous_entite: List[str] = Query(default=[]),
    # L'année est le repère de lecture de la liste : la trier par date
    # d'ajout afficherait une colonne d'années dans le désordre.
    sort: str = "annee", page: int = 1,
    par_page: int = PAR_PAGE_DEFAUT,
):
    filtres = {k: v for k, v in {
        "q": q.strip(), "type": type, "statut": statut,
        "etablissement": etablissement, "domaine": domaine,
        "annee": annee, "langue": langue, "sous_entite": sous_entite,
    }.items() if v}

    contexte = contexte_catalogue(request, db, filtres, sort, page, par_page)

    # Requête émise par le script de facettes : on ne renvoie que les
    # fragments qui changent, pas la page entière.
    if request.headers.get("X-Requested-With") == "facettes":
        return templates.TemplateResponse(
            "public/components/fragment_resultats.html", contexte
        )

    return templates.TemplateResponse("public/index.html", contexte)


# Plafond d'export public. Un catalogue national peut compter des
# dizaines de milliers de notices ; sans borne, une adresse sans filtre
# suffirait à mobiliser le serveur. Le plafond est annoncé dans le
# fichier produit plutôt que silencieux.
EXPORT_PUBLIC_MAX = 5000

FORMATS_EXPORT_PUBLIC = {
    "csv": ("text/csv", "csv"),
    "bib": ("application/x-bibtex", "bib"),
    "ris": ("application/x-research-info-systems", "ris"),
}


@app.get("/export")
async def export_public(
    request: Request, db: Session = Depends(get_db),
    format: str = "csv",
    q: str = "",
    type: List[str] = Query(default=[]),
    statut: List[str] = Query(default=[]),
    etablissement: List[str] = Query(default=[]),
    domaine: List[str] = Query(default=[]),
    annee: List[str] = Query(default=[]),
    langue: List[str] = Query(default=[]),
    sous_entite: List[str] = Query(default=[]),
    sort: str = "annee",
):
    """Exporte les résultats filtrés du catalogue public.

    Les paramètres sont exactement ceux de /recherche : le bouton
    d'export reprend la chaîne de requête affichée, si bien que le
    fichier contient ce que le lecteur a sous les yeux — et pas autre
    chose, ce qui serait la manière la plus sûre de le tromper.
    """
    if format not in FORMATS_EXPORT_PUBLIC:
        raise HTTPException(status_code=404)
    mime, extension = FORMATS_EXPORT_PUBLIC[format]

    filtres = {k: v for k, v in {
        "q": q.strip(), "type": type, "statut": statut,
        "etablissement": etablissement, "domaine": domaine,
        "annee": annee, "langue": langue, "sous_entite": sous_entite,
    }.items() if v}

    if sort not in TRIS:
        sort = TRI_DEFAUT
    requete = appliquer_filtres(db.query(Document), filtres).order_by(*TRIS[sort])
    total = requete.count()
    docs = requete.limit(EXPORT_PUBLIC_MAX).all()

    noms_etabs = {
        e.code: e.nom for e in db.query(Etablissement).all()
    }
    base = str(request.base_url).rstrip("/")
    horodatage = datetime.now().strftime("%Y%m%d")
    nom_fichier = f"catalogue-{horodatage}.{extension}"

    if format == "csv":
        tampon = io.StringIO()
        # Le point-virgule et le BOM sont ce qu'attend Excel en locale
        # française : sans eux, la feuille s'ouvre en une seule colonne
        # et les accents ressortent en mojibake.
        tampon.write("\ufeff")
        graveur = csv.writer(tampon, delimiter=";", quoting=csv.QUOTE_MINIMAL)
        graveur.writerow([
            "Numero national", "Titre", "Auteur", "Type", "Statut", "Annee",
            "Etablissement", "Ecole doctorale / Faculte", "Domaine", "Langue",
            "Direction", "Mots-cles", "Acces", "URL du document", "URL de la notice",
        ])
        for d in docs:
            graveur.writerow([
                d.numero_national, d.titre, d.auteur,
                "These de doctorat" if d.type == "these" else "Memoire de master",
                "Soutenu" if d.statut == "soutenu" else "En preparation",
                d.annee,
                noms_etabs.get(d.etablissement_code, d.etablissement_code),
                d.sous_entite_nom or "", d.domaine or "", d.langue or "",
                d.directeur or "",
                "; ".join(d.mots_cles) if d.mots_cles else "",
                "Public" if d.acces == "public" else "Restreint",
                d.url_document or "",
                f"{base}/document/{d.id}",
            ])
        contenu = tampon.getvalue()
    else:
        rendre = citations.bibtex if format == "bib" else citations.ris
        entete = (
            f"% {total} notice(s) correspondent à cette recherche"
            if format == "bib" else
            f"TY  - GEN\nTI  - Export ScholarSync ({total} notices)\nER  - "
        )
        morceaux = []
        if total > EXPORT_PUBLIC_MAX and format == "bib":
            morceaux.append(
                f"% Export limité aux {EXPORT_PUBLIC_MAX} premières notices "
                f"sur {total}. Affinez les filtres pour un export complet.\n"
            )
        for d in docs:
            morceaux.append(rendre(
                d, noms_etabs.get(d.etablissement_code, d.etablissement_code), base
            ))
        contenu = "\n".join(morceaux)

    return Response(
        content=contenu,
        media_type=f"{mime}; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{nom_fichier}"',
            # Le lecteur doit pouvoir constater que son export est
            # tronqué sans ouvrir le fichier.
            "X-Total-Notices": str(total),
            "X-Notices-Exportees": str(len(docs)),
        },
    )


@app.get("/theses", response_class=HTMLResponse)
async def theses(request: Request, db: Session = Depends(get_db), page: int = 1):
    return RedirectResponse(f"/recherche?type=these&page={page}", status_code=302)

@app.get("/memoires", response_class=HTMLResponse)
async def memoires(request: Request, db: Session = Depends(get_db), page: int = 1):
    return RedirectResponse(f"/recherche?type=memoire&page={page}", status_code=302)

@app.get("/document/{doc_id}", response_class=HTMLResponse)
async def detail_document(doc_id: str, request: Request, db: Session = Depends(get_db)):
    params = get_params_with_defaults(db)
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404)
    nom_etab = doc.etablissement.nom if doc.etablissement else doc.etablissement_code
    return templates.TemplateResponse("public/document.html", {
        "request": request, "params": params, "doc": doc,
        "citation_apa": citations.apa(doc, nom_etab),
        "voisins": documents_proches(db, doc),
        "active_nav": "", "lang": "fr",
    })


def documents_proches(db: Session, doc, limite: int = 4):
    """Travaux du même champ, pour prolonger la lecture.

    La colonne de lecture d'une fiche se termine souvent après le résumé
    et deux mots-clés, laissant la page à moitié vide — un catalogue qui
    s'arrête là oblige à repartir par la recherche pour trouver le
    travail voisin, qui est pourtant ce qu'on cherche ensuite.

    La parenté se lit par cercles concentriques, du plus proche au plus
    lointain : même école doctorale, puis même domaine, puis même
    établissement. On s'arrête dès qu'on a de quoi remplir la liste,
    sans jamais mélanger un travail sans rapport pour faire nombre.
    """
    trouves, vus = [], {doc.id}

    cercles = []
    if doc.sous_entite_nom:
        cercles.append(Document.sous_entite_nom == doc.sous_entite_nom)
    if doc.domaine:
        cercles.append(Document.domaine == doc.domaine)
    cercles.append(Document.etablissement_code == doc.etablissement_code)

    for condition in cercles:
        if len(trouves) >= limite:
            break
        lot = (
            db.query(Document)
            .filter(condition, Document.id.notin_(list(vus)))
            # Les travaux récents d'abord : dans un catalogue vivant,
            # c'est ce qui a le plus de chances d'intéresser.
            .order_by(Document.annee.desc(), Document.created_at.desc())
            .limit(limite - len(trouves))
            .all()
        )
        for d in lot:
            trouves.append(d)
            vus.add(d.id)

    return trouves


FORMATS_CITATION = {
    # extension : (fonction, type MIME)
    "bib": (citations.bibtex, "application/x-bibtex"),
    "ris": (citations.ris, "application/x-research-info-systems"),
}


@app.get("/document/{doc_id}/citation.{format}")
async def citation_document(
    doc_id: str, format: str, request: Request, db: Session = Depends(get_db)
):
    """Télécharge la notice au format demandé.

    Le format vient de l'URL : il est confronté à une liste fermée, sans
    quoi n'importe quelle chaîne atteindrait le nom du fichier proposé
    au téléchargement.
    """
    entree = FORMATS_CITATION.get(format.lower())
    if not entree:
        raise HTTPException(status_code=404)

    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404)

    rendre, mime = entree
    nom_etab = doc.etablissement.nom if doc.etablissement else doc.etablissement_code
    # L'URL publique de la notice entre dans le fichier : le lecteur qui
    # importe la référence doit pouvoir revenir à la source.
    base = str(request.base_url).rstrip("/")
    contenu = rendre(doc, nom_etab, base)

    nom = citations.nom_fichier(doc, format.lower())
    return Response(
        content=contenu,
        media_type=f"{mime}; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nom}"'},
    )

@app.get("/a-propos", response_class=HTMLResponse)
async def apropos(request: Request, db: Session = Depends(get_db)):
    params = get_params_with_defaults(db)
    return templates.TemplateResponse("public/apropos.html", {
        "request": request, "params": params, "active_nav": "apropos", "lang": "fr",
    })

@app.get("/etablissements", response_class=HTMLResponse)
async def etablissements_page(request: Request, db: Session = Depends(get_db)):
    params = get_params_with_defaults(db)
    etabs = db.query(Etablissement).filter(Etablissement.actif == True).all()
    etabs_stats = []
    for e in etabs:
        count = db.query(Document).filter(Document.etablissement_code == e.code).count()
        etabs_stats.append({"etablissement": e, "count": count})
    return templates.TemplateResponse("public/etablissements.html", {
        "request": request, "params": params, "etabs_stats": etabs_stats,
        "active_nav": "etablissements", "lang": "fr",
    })

@app.get("/statistiques")
async def statistiques_page():
    """Le menu Statistiques a été retiré de la navigation publique.

    Les chiffres qu'il portait — total, établissements, soutenus, en
    préparation — figurent en tête de la page d'accueil, et le détail par
    année, domaine et établissement se lit dans les facettes du
    catalogue : la page faisait doublon. Redirection permanente plutôt
    que 404, pour les liens déjà diffusés.
    """
    return RedirectResponse("/", status_code=308)


# ─── ROUTES ADMIN ─────────────────────────────────────────────────

def utilisateur_courant(request: Request, db: Session):
    """Compte connecté, déjà relu par AdminAuthMiddleware."""
    return getattr(request.state, "utilisateur", None) or require_auth(request, db)


def filtrer_documents(requete, utilisateur):
    """Limite une requête sur Document au périmètre de l'utilisateur."""
    code = permissions.perimetre(utilisateur)
    return requete if code is None else requete.filter(Document.etablissement_code == code)


def sources_visibles(db: Session, utilisateur):
    requete = db.query(ZoteroSource)
    code = permissions.perimetre(utilisateur)
    if code is not None:
        requete = requete.join(Etablissement).filter(Etablissement.code == code)
    return requete


def filtrer_logs(db: Session, requete, utilisateur):
    """Limite une requête sur SyncLog aux sources du périmètre."""
    if permissions.perimetre(utilisateur) is None:
        return requete
    ids = [s.id for s in sources_visibles(db, utilisateur).all()]
    return requete.filter(SyncLog.zotero_source_id.in_(ids or [-1]))



def get_current_user_mock():
    return {"role": "super_admin", "email": "admin@scholarsync.local", "nom": "Admin"}

@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    utilisateur = utilisateur_courant(request, db)
    code = permissions.perimetre(utilisateur)
    params = get_params_with_defaults(db)
    stats = get_stats(db, code)
    docs = lambda: filtrer_documents(db.query(Document), utilisateur)  # noqa: E731
    stats_annees = [
        {"annee": r[0],
         "theses": docs().filter(Document.annee == r[0], Document.type == "these").count(),
         "memoires": docs().filter(Document.annee == r[0], Document.type == "memoire").count()}
        for r in filtrer_documents(db.query(Document.annee), utilisateur)
                   .distinct().order_by(Document.annee).all()
    ]
    stats_domaines = [
        {"domaine": r[0] or "Non défini", "count": r[1]}
        for r in filtrer_documents(
                    db.query(Document.domaine, func.count().label("count")), utilisateur)
                   .group_by(Document.domaine).order_by(func.count().desc()).limit(8).all()
    ]
    stats_etabs = [
        {"code": r[0], "count": r[1]}
        for r in filtrer_documents(
                    db.query(Document.etablissement_code, func.count().label("count")),
                    utilisateur)
                   .group_by(Document.etablissement_code)
                   .order_by(func.count().desc()).all()
    ]
    derniers_logs = (
        filtrer_logs(db, db.query(SyncLog), utilisateur)
        .order_by(SyncLog.debut.desc()).limit(5).all()
    )
    for log in derniers_logs:
        src = db.query(ZoteroSource).filter(ZoteroSource.id == log.zotero_source_id).first()
        log.etablissement_code = src.etablissement.code if src else "—"
    docs_recents = docs().order_by(Document.created_at.desc()).limit(6).all()
    mon_etablissement = (
        db.query(Etablissement).filter(Etablissement.code == code).first()
        if code else None
    )

    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request, "params": params, "stats": stats,
        # Structures Python transmises telles quelles : le gabarit les
        # sérialise une seule fois, dans un bloc JSON. Elles étaient
        # passées déjà encodées par json.dumps puis ré-encodées par le
        # filtre tojson du gabarit — le script recevait une chaîne au lieu
        # d'un tableau, .map n'existait pas, et l'erreur interrompait le
        # dessin de tous les graphiques.
        "donnees_graphiques": {
            "annees": stats_annees,
            "domaines": stats_domaines,
            "etablissements": stats_etabs,
            "types": {
                "theses": stats.get("nb_theses", 0),
                "memoires": stats.get("nb_memoires", 0),
            },
        },
        "derniers_logs": derniers_logs, "docs_recents": docs_recents,
        "mon_etablissement": mon_etablissement,
        "current_user": utilisateur,
        "active_nav": "dashboard",
    })

@app.get("/admin/zotero", response_class=HTMLResponse)
async def admin_zotero(request: Request, db: Session = Depends(get_db)):
    params = get_params_with_defaults(db)
    sources = db.query(ZoteroSource).all()
    for src in sources:
        src.nb_documents = db.query(Document).filter(
            Document.zotero_source_id == src.id
        ).count()
    etabs_sans_source = db.query(Etablissement).filter(
        ~Etablissement.id.in_(
            db.query(ZoteroSource.etablissement_id)
        )
    ).all()
    sync_intervalle = int(params.get("sync_intervalle_min", "60"))
    return templates.TemplateResponse("admin/zotero.html", {
        "request": request, "params": params, "sources": sources,
        "etablissements_sans_source": etabs_sans_source,
        "sync_intervalle": sync_intervalle,
        "current_user": require_auth(request, db), "active_nav": "zotero",
    })

@app.post("/admin/zotero/ajouter")
async def admin_zotero_ajouter(
    request: Request, db: Session = Depends(get_db),
    etablissement_id: int = Form(...), zotero_type: str = Form(...),
    zotero_id: str = Form(...), api_key: str = Form(...), label: str = Form("")
):
    etab = db.query(Etablissement).filter(Etablissement.id == etablissement_id).first()
    if not etab:
        return redirect_flash("/admin/zotero", "Établissement introuvable.", "danger")
    if db.query(ZoteroSource).filter(
        ZoteroSource.etablissement_id == etablissement_id
    ).first():
        return redirect_flash(
            "/admin/zotero",
            f"{etab.code} possède déjà un compte Zotero. Modifiez-le ou supprimez-le d'abord.",
            "warning",
        )

    db.add(ZoteroSource(
        etablissement_id=etablissement_id, zotero_type=zotero_type,
        zotero_id=zotero_id.strip(), api_key=api_key.strip(), label=label.strip() or None
    ))
    try:
        db.commit()
    except Exception:
        db.rollback()
        return redirect_flash(
            "/admin/zotero", "Enregistrement impossible : vérifiez les informations saisies.", "danger"
        )
    return redirect_flash("/admin/zotero", f"Compte Zotero ajouté pour {etab.code}.", "success")

@app.post("/admin/zotero/{source_id}/modifier")
async def admin_zotero_modifier(
    source_id: int, request: Request, db: Session = Depends(get_db),
    zotero_type: str = Form(...), zotero_id: str = Form(...),
    label: str = Form(""), api_key: str = Form(""),
):
    """La clé API laissée vide signifie « ne pas changer » : elle n'est
    jamais réaffichée au formulaire."""
    require_super_admin(request, db)
    src = db.query(ZoteroSource).filter(ZoteroSource.id == source_id).first()
    if not src:
        return redirect_flash("/admin/zotero", "Source introuvable.", "danger")
    if zotero_type not in ("user", "group"):
        return redirect_flash("/admin/zotero", "Type de compte Zotero inconnu.", "danger")
    if not zotero_id.strip():
        return redirect_flash("/admin/zotero", "L'identifiant Zotero est obligatoire.", "danger")

    ancien_id = src.zotero_id
    src.zotero_type = zotero_type
    src.zotero_id = zotero_id.strip()
    src.label = label.strip() or None
    if api_key.strip():
        src.api_key = api_key.strip()

    # Changer de bibliothèque source invalide le curseur de version :
    # sans cette remise à zéro, la sync suivante ne verrait aucun document.
    if ancien_id != src.zotero_id:
        src.zotero_version = 0

    db.commit()
    libelle = src.etablissement.code if src.etablissement else f"source {src.id}"
    return redirect_flash("/admin/zotero", f"Compte Zotero de {libelle} mis à jour.", "success")


@app.post("/admin/zotero/{source_id}/toggle")
async def admin_zotero_toggle(source_id: int, db: Session = Depends(get_db)):
    src = db.query(ZoteroSource).filter(ZoteroSource.id == source_id).first()
    if not src:
        return redirect_flash("/admin/zotero", "Source introuvable.", "danger")
    src.actif = not src.actif
    db.commit()
    libelle = src.etablissement.code if src.etablissement else f"source {src.id}"
    etat = "activé" if src.actif else "désactivé"
    return redirect_flash("/admin/zotero", f"Compte {libelle} {etat}.", "success")

@app.post("/admin/zotero/{source_id}/tester")
async def admin_zotero_tester(source_id: int, db: Session = Depends(get_db)):
    src = db.query(ZoteroSource).filter(ZoteroSource.id == source_id).first()
    if not src:
        return JSONResponse({"ok": False, "message": "Source introuvable"})
    try:
        from pyzotero import zotero
        zot = zotero.Zotero(src.zotero_id, src.zotero_type, src.api_key)
        items = zot.top(limit=1)
        return JSONResponse({"ok": True, "message": f"Connexion réussie — bibliothèque accessible"})
    except Exception as e:
        return JSONResponse({"ok": False, "message": f"Échec : {str(e)[:100]}"})

@app.post("/admin/zotero/tester-nouveau")
async def admin_zotero_tester_nouveau(
    request: Request,
    zotero_type: str = Form(...), zotero_id: str = Form(...), api_key: str = Form(...)
):
    try:
        from pyzotero import zotero
        zot = zotero.Zotero(zotero_id, zotero_type, api_key)
        items = zot.top(limit=1)
        return JSONResponse({"ok": True, "message": "Connexion réussie"})
    except Exception as e:
        return JSONResponse({"ok": False, "message": f"Échec : {str(e)[:100]}"})

@app.post("/admin/sync/lancer")
async def admin_sync_lancer(db: Session = Depends(get_db)):
    from app.sync.engine import sync_all

    nb_sources = db.query(ZoteroSource).filter(ZoteroSource.actif == True).count()  # noqa: E712
    if not nb_sources:
        return redirect_flash(
            "/admin/sync",
            "Aucune source Zotero active. Activez au moins un compte avant de synchroniser.",
            "warning",
        )

    lance = tasks.run_in_background(sync_all, declenchement="manuel", key="sync")
    if not lance:
        return redirect_flash(
            "/admin/sync", "Une synchronisation est déjà en cours.", "info"
        )
    return redirect_flash(
        "/admin/sync",
        f"Synchronisation lancée sur {nb_sources} source(s). "
        "L'avancement s'affiche ci-dessous.",
        "success",
    )

@app.get("/admin/sync/etat")
async def admin_sync_etat(request: Request, db: Session = Depends(get_db)):
    """Avancement de la synchronisation, interrogé par le client toutes les 2 s.

    Limité au périmètre de l'utilisateur : un établissement voit sa
    propre synchronisation, pas celle des autres.
    """
    utilisateur = utilisateur_courant(request, db)
    log = sync_log_actif(db, utilisateur)
    if permissions.perimetre(utilisateur) is None:
        cles = {k for k in tasks.running_keys() if k.startswith("sync")}
    else:
        cles = {"sync"} | {f"sync:{s.id}" for s in sources_visibles(db, utilisateur)}
        cles &= set(tasks.running_keys())
    en_cours = log is not None or bool(cles)
    if log is None:
        log = (
            filtrer_logs(db, db.query(SyncLog), utilisateur)
            .order_by(SyncLog.debut.desc()).first()
        )

    if log is None:
        return JSONResponse({"en_cours": en_cours, "log": None})

    total = log.documents_total or 0
    traites = log.documents_traites or 0
    source = (
        db.query(ZoteroSource).filter(ZoteroSource.id == log.zotero_source_id).first()
    )

    return JSONResponse({
        "en_cours": en_cours,
        "log": {
            "id": log.id,
            "statut": log.statut,
            "etablissement": (
                source.etablissement.code if source and source.etablissement else "—"
            ),
            "total": total,
            "traites": traites,
            "pourcentage": round(traites / total * 100) if total else 0,
            "ajoutes": log.documents_ajoutes or 0,
            "modifies": log.documents_modifies or 0,
            "erreurs": log.documents_erreur or 0,
            "message_erreur": log.message_erreur,
            "debut": log.debut.isoformat() if log.debut else None,
            "fin": log.fin.isoformat() if log.fin else None,
        },
    })


# Formats de fichier que peut produire enregistrer_logo : sert à purger
# le logo précédent quand le format change.
EXTENSIONS_LOGO = ("png", "jpg", "webp", "svg")


# Le nom de base du fichier vient du code d'établissement, donc d'une
# saisie. Une valeur comme « ../../etc/cron.d/x » écrirait hors du
# dossier des téléversements : on n'accepte qu'un jeu fermé de
# caractères, et jamais un séparateur de chemin.
MOTIF_BASE_LOGO = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


async def enregistrer_logo(logo: UploadFile, base: str = "logo",
                           profil: str = "outil") -> dict:
    """Contrôle puis enregistre un logo.

    Renvoie {"url": …, "avertissements": [...]} ou {"erreur": …}.

    `base` nomme le fichier sans son extension : « logo » pour l'outil,
    « etab-UCAD » pour un établissement. `profil` fixe les exigences de
    qualité (voir app/core/logos.py) : un logo d'en-tête et une vignette
    d'établissement n'ont pas les mêmes proportions.
    """
    if not MOTIF_BASE_LOGO.match(base):
        return {"erreur": "Nom de fichier refusé."}

    # Lecture bornée : on ne charge pas en mémoire un fichier de 2 Go
    # pour constater ensuite qu'il dépasse 2 Mo.
    limite = settings.MAX_LOGO_SIZE_MB * 1024 * 1024
    contenu = await logo.read(limite + 1)
    try:
        valide = logos.controler_logo(
            contenu, logo.content_type, profil=profil,
            taille_max_mo=settings.MAX_LOGO_SIZE_MB,
        )
    except logos.LogoRefuse as refus:
        return {"erreur": str(refus)}

    dossier = f"app/{settings.UPLOAD_DIR}"
    chemin = f"{dossier}/{base}.{valide.extension}"
    try:
        os.makedirs(dossier, exist_ok=True)
        with open(chemin, "wb") as f:
            f.write(valide.contenu)
        # Purger les logos d'un autre format, sinon l'ancien fichier
        # resterait servi à côté du nouveau.
        for autre in set(EXTENSIONS_LOGO) - {valide.extension}:
            try:
                os.remove(f"{dossier}/{base}.{autre}")
            except FileNotFoundError:
                pass
    except PermissionError:
        logging.getLogger("scholarsync").error(
            "Écriture refusée dans %s", dossier, exc_info=True
        )
        return {"erreur": (
            "Le dossier des téléversements n'est pas accessible en écriture. "
            "Sur le serveur : chown -R 1000:1000 uploads (voir "
            "docs/deploiement.md)."
        )}
    except OSError as e:
        logging.getLogger("scholarsync").error("Écriture du logo : %s", e)
        return {"erreur": f"Enregistrement impossible : {str(e)[:120]}"}

    # Paramètre anti-cache : sans lui le navigateur garde l'ancien logo,
    # l'URL étant identique d'un téléversement à l'autre.
    return {
        "url": (f"/static/img/uploads/{base}.{valide.extension}"
                f"?v={int(datetime.now().timestamp())}"),
        "avertissements": valide.avertissements,
    }


def message_logo(prefixe: str, resultat: dict) -> tuple:
    """Message flash après un téléversement réussi, avertissements compris."""
    notes = resultat.get("avertissements") or []
    if notes:
        return f"{prefixe} " + " ".join(notes), "warning"
    return prefixe, "success"


@app.get("/admin/parametres/identite", response_class=HTMLResponse)
async def admin_parametres_identite(request: Request, db: Session = Depends(get_db)):
    params = get_params_with_defaults(db)
    return templates.TemplateResponse("admin/parametres_identite.html", {
        "request": request, "params": params,
        "profil_logo": logos.PROFILS["outil"],
        "current_user": require_auth(request, db), "active_nav": "identite",
    })

@app.post("/admin/parametres/identite")
async def admin_parametres_identite_save(
    request: Request, db: Session = Depends(get_db),
    nom_outil: str = Form(""), slogan: str = Form(""),
    institution_nom: str = Form(""), pied_page_texte: str = Form(""),
    couleur_principale: str = Form("#1a3a5c"), couleur_secondaire: str = Form("#8b1a2e"),
    bande_decorative: str = Form("custom"), icones_filigrane: str = Form("academique"),
    logo: Optional[UploadFile] = File(None)
):
    updates = {
        "nom_outil": nom_outil, "slogan": slogan,
        "institution_nom": institution_nom, "pied_page_texte": pied_page_texte,
        "couleur_principale": couleur_principale, "couleur_secondaire": couleur_secondaire,
        "bande_decorative": bande_decorative, "icones_filigrane": icones_filigrane,
    }
    if logo and logo.filename:
        resultat = await enregistrer_logo(logo, profil="outil")
        if resultat.get("erreur"):
            return redirect_flash(
                "/admin/parametres/identite", f"Logo refusé : {resultat['erreur']}", "danger"
            )
        updates["logo_url"] = resultat["url"]
    else:
        resultat = {}

    for cle, valeur in updates.items():
        row = db.query(Parametre).filter(Parametre.cle == cle).first()
        if row:
            row.valeur = valeur
        else:
            db.add(Parametre(cle=cle, valeur=valeur, type="text"))
    db.commit()
    message, niveau = message_logo("Identité visuelle enregistrée.", resultat)
    return redirect_flash("/admin/parametres/identite", message, niveau)

@app.post("/admin/parametres/sync-intervalle")
async def admin_sync_intervalle(minutes: int = Form(60), db: Session = Depends(get_db)):
    row = db.query(Parametre).filter(Parametre.cle == "sync_intervalle_min").first()
    if row:
        row.valeur = str(minutes)
    else:
        db.add(Parametre(cle="sync_intervalle_min", valeur=str(minutes), type="text"))
    db.commit()
    return redirect_flash(
        "/admin/zotero",
        f"Synchronisation automatique programmée toutes les {minutes} minutes.",
        "success",
    )

@app.get("/admin/documents", response_class=HTMLResponse)
async def admin_documents(
    request: Request, db: Session = Depends(get_db),
    q: str = "", page: int = 1, par_page: int = PAR_PAGE_DEFAUT,
    type: str = "", statut: str = "", etablissement: str = "",
    tri: str = "", sens: str = "",
):
    utilisateur = utilisateur_courant(request, db)
    params = get_params_with_defaults(db)

    query = filtrer_documents(db.query(Document), utilisateur)
    if q:
        motif = f"%{q.strip()}%"
        query = query.filter(
            Document.titre.ilike(motif)
            | Document.auteur.ilike(motif)
            | Document.numero_national.ilike(motif)
        )
    if type in ("these", "memoire"):
        query = query.filter(Document.type == type)
    if statut in ("soutenu", "en_preparation"):
        query = query.filter(Document.statut == statut)
    if etablissement:
        query = query.filter(Document.etablissement_code == etablissement)

    query, etat_tri = appliquer_tri(query, tri, sens, {
        "titre": Document.titre,
        "auteur": Document.auteur,
        "etablissement": Document.etablissement_code,
        "type": Document.type,
        "statut": Document.statut,
        "annee": (Document.annee, "desc"),
        "numero": Document.numero_national,
        "ajout": (Document.created_at, "desc"),
    }, defaut="ajout")

    par_page = normaliser_par_page(par_page)
    docs, pagination = paginate(query, page, par_page)

    return templates.TemplateResponse("admin/documents.html", {
        "request": request, "params": params, "documents": docs,
        "pagination": pagination, "tri": etat_tri, "q": q,
        "filtre_type": type, "filtre_statut": statut,
        "filtre_etablissement": etablissement,
        "etablissements": (
            db.query(Etablissement).order_by(Etablissement.code).all()
            if permissions.est_super_admin(utilisateur) else []
        ),
        "current_user": utilisateur, "active_nav": "documents",
    })

@app.get("/api/stats")
async def api_stats(db: Session = Depends(get_db)):
    return get_stats(db)

@app.get("/admin/etablissements", response_class=HTMLResponse)
async def admin_etablissements(
    request: Request, db: Session = Depends(get_db),
    page: int = 1, par_page: int = PAR_PAGE_DEFAUT, tri: str = "", sens: str = "",
):
    params = get_params_with_defaults(db)
    requete, etat_tri = appliquer_tri(db.query(Etablissement), tri, sens, {
        "code": Etablissement.code,
        "nom": Etablissement.nom,
        "ville": Etablissement.ville,
        "pays": Etablissement.pays,
        "actif": (Etablissement.actif, "desc"),
    }, defaut="nom")
    etabs, pagination = paginate(requete, page, normaliser_par_page(par_page))
    return templates.TemplateResponse("admin/etablissements.html", {
        "request": request, "params": params, "etablissements": etabs,
        "pagination": pagination, "tri": etat_tri,
        "profil_logo": logos.PROFILS["etablissement"],
        "current_user": require_auth(request, db), "active_nav": "etablissements",
    })

# Le code d'établissement nomme aussi son fichier de logo : il doit
# rester un identifiant, pas une chaîne libre.
MOTIF_CODE_ETAB = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,9}$")


@app.post("/admin/etablissements/ajouter")
async def admin_etablissements_ajouter(
    db: Session = Depends(get_db),
    code: str = Form(...), nom: str = Form(...), nom_court: str = Form(""),
    pays: str = Form("Sénégal"), ville: str = Form(""), url_site: str = Form(""),
    logo: UploadFile = File(None),
):
    code = code.strip().upper()
    if not code or not nom.strip():
        return redirect_flash(
            "/admin/etablissements", "Le code et le nom sont obligatoires.", "danger"
        )
    if not MOTIF_CODE_ETAB.match(code):
        return redirect_flash(
            "/admin/etablissements",
            "Le code ne peut contenir que des lettres, des chiffres, "
            "un tiret ou un tiret bas (10 caractères au plus).",
            "danger",
        )
    if db.query(Etablissement).filter(Etablissement.code == code).first():
        return redirect_flash(
            "/admin/etablissements", f"Le code {code} est déjà utilisé.", "warning"
        )

    # Le logo est enregistré avant la ligne en base : si le fichier est
    # refusé, rien n'est créé et l'administrateur corrige d'un seul
    # geste, plutôt que de retrouver un établissement à moitié saisi.
    logo_url = None
    if logo is not None and logo.filename:
        resultat = await enregistrer_logo(logo, base=f"etab-{code}", profil="etablissement")
        if "erreur" in resultat:
            return redirect_flash(
                "/admin/etablissements",
                f"Logo refusé : {resultat['erreur']} L'établissement n'a pas été créé.",
                "danger",
            )
        logo_url = resultat["url"]

    db.add(Etablissement(
        code=code, nom=nom.strip(), nom_court=nom_court.strip() or None,
        pays=pays, ville=ville.strip() or None, url_site=url_site.strip() or None,
        logo_url=logo_url,
    ))
    try:
        db.commit()
    except Exception:
        db.rollback()
        return redirect_flash(
            "/admin/etablissements", "Enregistrement impossible.", "danger"
        )
    message, niveau = message_logo(
        f"Établissement {code} ajouté.", resultat if logo_url else {}
    )
    return redirect_flash("/admin/etablissements", message, niveau)


async def _mettre_a_jour_etablissement(
    db: Session, etab: Etablissement, retour: str,
    nom: str, nom_court: str, pays: str, ville: str, url_site: str,
    logo: Optional[UploadFile], retirer_logo: Optional[str],
):
    """Enregistre la fiche d'un établissement — partagé entre la page
    des établissements (super admin) et « Mon établissement »."""
    if not nom.strip():
        return redirect_flash(retour, "Le nom est obligatoire.", "danger")
    url_site = url_site.strip()
    if url_site and not re.match(r"^https?://[^\s/$.?#].[^\s]*$", url_site, re.I):
        return redirect_flash(
            retour, "Le site web doit être une adresse complète (https://…).", "danger"
        )

    resultat = {}
    if logo is not None and logo.filename:
        resultat = await enregistrer_logo(
            logo, base=f"etab-{etab.code}", profil="etablissement"
        )
        if "erreur" in resultat:
            return redirect_flash(
                retour, f"Logo refusé : {resultat['erreur']} Rien n'a été modifié.",
                "danger",
            )
        etab.logo_url = resultat["url"]
    elif retirer_logo:
        etab.logo_url = None

    etab.nom = nom.strip()
    etab.nom_court = nom_court.strip() or None
    etab.pays = pays.strip() or etab.pays
    etab.ville = ville.strip() or None
    etab.url_site = url_site or None
    try:
        db.commit()
    except Exception:
        db.rollback()
        return redirect_flash(retour, "Enregistrement impossible.", "danger")
    message, niveau = message_logo(f"Établissement {etab.code} mis à jour.", resultat)
    return redirect_flash(retour, message, niveau)


@app.post("/admin/etablissements/{etab_id}/modifier")
async def admin_etablissements_modifier(
    etab_id: int, db: Session = Depends(get_db),
    nom: str = Form(...), nom_court: str = Form(""),
    pays: str = Form("Sénégal"), ville: str = Form(""), url_site: str = Form(""),
    logo: UploadFile = File(None), retirer_logo: str = Form(None),
):
    """Le code n'est pas modifiable : il identifie les documents
    synchronisés et nomme le fichier de logo. Le changer romprait les
    deux liens sans prévenir."""
    etab = db.query(Etablissement).filter(Etablissement.id == etab_id).first()
    if not etab:
        return redirect_flash(
            "/admin/etablissements", "Établissement introuvable.", "danger"
        )
    return await _mettre_a_jour_etablissement(
        db, etab, "/admin/etablissements",
        nom, nom_court, pays, ville, url_site, logo, retirer_logo,
    )


@app.get("/admin/mon-etablissement", response_class=HTMLResponse)
async def admin_mon_etablissement(request: Request, db: Session = Depends(get_db)):
    """Fiche de l'établissement du compte connecté : nom, site, logo.

    Chaque université tient sa propre fiche, sans passer par le super
    administrateur. Le code reste figé (il identifie ses documents).
    """
    utilisateur = utilisateur_courant(request, db)
    code = permissions.perimetre(utilisateur)
    if code is None:
        # Le super admin gère tous les établissements depuis leur page.
        return RedirectResponse("/admin/etablissements", status_code=303)
    etab = db.query(Etablissement).filter(Etablissement.code == code).first()
    source = sources_visibles(db, utilisateur).first()
    return templates.TemplateResponse("admin/mon_etablissement.html", {
        "request": request, "params": get_params_with_defaults(db),
        "etab": etab, "source": source,
        "nb_documents": filtrer_documents(db.query(Document), utilisateur).count(),
        "peut_modifier": permissions.peut_modifier(utilisateur),
        "profil_logo": logos.PROFILS["etablissement"],
        "max_logo_mo": settings.MAX_LOGO_SIZE_MB,
        "current_user": utilisateur, "active_nav": "mon_etablissement",
    })


@app.post("/admin/mon-etablissement")
async def admin_mon_etablissement_save(
    request: Request, db: Session = Depends(get_db),
    nom: str = Form(...), nom_court: str = Form(""),
    pays: str = Form(""), ville: str = Form(""), url_site: str = Form(""),
    logo: UploadFile = File(None), retirer_logo: str = Form(None),
):
    utilisateur = utilisateur_courant(request, db)
    code = permissions.perimetre(utilisateur)
    etab = db.query(Etablissement).filter(Etablissement.code == code).first() if code else None
    if etab is None:
        return redirect_flash(
            "/admin", "Aucun établissement n'est rattaché à votre compte.", "danger"
        )
    return await _mettre_a_jour_etablissement(
        db, etab, "/admin/mon-etablissement",
        nom, nom_court, pays, ville, url_site, logo, retirer_logo,
    )


@app.post("/admin/etablissements/{etab_id}/toggle")
async def admin_etablissements_toggle(etab_id: int, db: Session = Depends(get_db)):
    etab = db.query(Etablissement).filter(Etablissement.id == etab_id).first()
    if not etab:
        return redirect_flash("/admin/etablissements", "Établissement introuvable.", "danger")
    etab.actif = not etab.actif
    db.commit()
    etat = "activé" if etab.actif else "désactivé"
    return redirect_flash("/admin/etablissements", f"{etab.code} {etat}.", "success")

@app.get("/admin/sync", response_class=HTMLResponse)
async def admin_sync(
    request: Request, db: Session = Depends(get_db),
    page: int = 1, par_page: int = PAR_PAGE_DEFAUT, tri: str = "", sens: str = "",
):
    utilisateur = utilisateur_courant(request, db)
    params = get_params_with_defaults(db)

    # L'historique complet vit ici désormais : la page « Historique sync »
    # affichait la même liste sous un autre menu, ce qui obligeait à
    # deviner laquelle des deux faisait autorité.
    requete, etat_tri = appliquer_tri(
        filtrer_logs(db, db.query(SyncLog), utilisateur), tri, sens, {
        "debut": (SyncLog.debut, "desc"),
        "fin": (SyncLog.fin, "desc"),
        "declenchement": SyncLog.declenchement,
        "statut": SyncLog.statut,
        "ajoutes": (SyncLog.documents_ajoutes, "desc"),
        "modifies": (SyncLog.documents_modifies, "desc"),
        "erreurs": (SyncLog.documents_erreur, "desc"),
    }, defaut="debut")
    logs, pagination = paginate(requete, page, normaliser_par_page(par_page))

    # Une jointure suffirait, mais le nombre de lignes affichées est borné
    # par la pagination : la lisibilité prime ici sur la micro-optimisation.
    for log in logs:
        src = db.query(ZoteroSource).filter(ZoteroSource.id == log.zotero_source_id).first()
        log.etablissement_code = src.etablissement.code if src else "—"

    sources = sources_visibles(db, utilisateur).all()
    sync_intervalle = int(params.get("sync_intervalle_min", "60"))
    return templates.TemplateResponse("admin/sync.html", {
        "request": request, "params": params, "logs": logs, "sources": sources,
        "pagination": pagination, "tri": etat_tri,
        "nb_syncs_total": pagination["total"],
        "sync_intervalle": sync_intervalle,
        "super_admin": permissions.est_super_admin(utilisateur),
        "current_user": utilisateur, "active_nav": "sync",
    })

@app.get("/admin/sync-logs")
async def admin_sync_logs(request: Request):
    """L'historique a rejoint la page Synchronisation : un même contenu
    sous deux menus obligeait à deviner lequel faisait autorité.

    La route survit en redirection permanente, pour les favoris et les
    liens déjà envoyés par courriel. La chaîne de requête est conservée :
    un lien vers une page de l'historique reste un lien vers cette page.
    """
    suite = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(f"/admin/sync{suite}", status_code=308)

# Libellé long pour les listes déroulantes, libellé court pour les
# étiquettes de tableau où la place manque.
ROLES = {
    "super_admin": "Super administrateur",
    "admin_etablissement": "Administrateur d\u2019\u00e9tablissement",
    "lecteur": "Lecteur",
}

ROLES_COURTS = {
    "super_admin": "Super admin",
    "admin_etablissement": "Admin \u00e9tablissement",
    "lecteur": "Lecteur",
}

LONGUEUR_MDP_MIN = 10


def maintenant_utc() -> datetime:
    from datetime import timezone
    return datetime.now(timezone.utc)


def erreur_mot_de_passe(mot_de_passe: str, email: str = "") -> Optional[str]:
    """Message d'erreur si le mot de passe est trop faible, sinon None."""
    if len(mot_de_passe) < LONGUEUR_MDP_MIN:
        return f"Le mot de passe doit faire au moins {LONGUEUR_MDP_MIN} caractères."
    if mot_de_passe.strip() != mot_de_passe or not mot_de_passe.strip():
        return "Le mot de passe ne doit pas commencer ni finir par une espace."
    if len(set(mot_de_passe)) < 4:
        return "Le mot de passe est trop répétitif."
    identifiant = email.split("@")[0].lower()
    if identifiant and len(identifiant) >= 4 and identifiant in mot_de_passe.lower():
        return "Le mot de passe ne doit pas contenir votre identifiant de courriel."
    return None


def _compte_super_admins_actifs(db: Session, sauf_id=None) -> int:
    q = db.query(Utilisateur).filter(
        Utilisateur.role == "super_admin", Utilisateur.actif == True  # noqa: E712
    )
    if sauf_id is not None:
        q = q.filter(Utilisateur.id != sauf_id)
    return q.count()


@app.post("/admin/utilisateurs/ajouter")
async def admin_utilisateurs_ajouter(
    request: Request, db: Session = Depends(get_db),
    email: str = Form(...), prenom: str = Form(""), nom: str = Form(""),
    role: str = Form(...), etablissement_code: str = Form(""),
    mot_de_passe: str = Form(...),
):
    require_super_admin(request, db)
    email = email.strip().lower()

    if role not in ROLES:
        return redirect_flash("/admin/utilisateurs", "Rôle inconnu.", "danger")
    if len(mot_de_passe) < LONGUEUR_MDP_MIN:
        return redirect_flash(
            "/admin/utilisateurs",
            f"Le mot de passe doit faire au moins {LONGUEUR_MDP_MIN} caractères.",
            "danger",
        )
    if db.query(Utilisateur).filter(Utilisateur.email == email).first():
        return redirect_flash(
            "/admin/utilisateurs", f"Un compte existe déjà pour {email}.", "warning"
        )
    if role == "admin_etablissement" and not etablissement_code:
        return redirect_flash(
            "/admin/utilisateurs",
            "Un administrateur d\u2019établissement doit être rattaché à un établissement.",
            "danger",
        )

    db.add(Utilisateur(
        email=email,
        mot_de_passe_hash=hash_password(mot_de_passe),
        prenom=prenom.strip() or None,
        nom=nom.strip() or None,
        role=role,
        etablissement_code=etablissement_code or None,
        actif=True,
    ))
    try:
        db.commit()
    except Exception:
        db.rollback()
        return redirect_flash("/admin/utilisateurs", "Création impossible.", "danger")
    return redirect_flash("/admin/utilisateurs", f"Compte créé pour {email}.", "success")


@app.post("/admin/utilisateurs/{user_id}/modifier")
async def admin_utilisateurs_modifier(
    user_id: str, request: Request, db: Session = Depends(get_db),
    prenom: str = Form(""), nom: str = Form(""),
    role: str = Form(...), etablissement_code: str = Form(""),
):
    courant = require_super_admin(request, db)
    user = db.query(Utilisateur).filter(Utilisateur.id == user_id).first()
    if not user:
        return redirect_flash("/admin/utilisateurs", "Compte introuvable.", "danger")
    if role not in ROLES:
        return redirect_flash("/admin/utilisateurs", "Rôle inconnu.", "danger")

    # Ne jamais laisser disparaître le dernier super administrateur :
    # plus personne ne pourrait alors administrer la plateforme.
    if (user.role == "super_admin" and role != "super_admin"
            and _compte_super_admins_actifs(db, sauf_id=user.id) == 0):
        return redirect_flash(
            "/admin/utilisateurs",
            "Impossible : ce compte est le dernier super administrateur actif.",
            "danger",
        )
    if role == "admin_etablissement" and not etablissement_code:
        return redirect_flash(
            "/admin/utilisateurs",
            "Un administrateur d\u2019établissement doit être rattaché à un établissement.",
            "danger",
        )

    user.prenom = prenom.strip() or None
    user.nom = nom.strip() or None
    user.role = role
    user.etablissement_code = etablissement_code or None
    db.commit()

    suffixe = " (vos droits changeront à la prochaine connexion)" if user.id == courant.id else ""
    return redirect_flash(
        "/admin/utilisateurs", f"Compte {user.email} mis à jour{suffixe}.", "success"
    )


@app.post("/admin/utilisateurs/{user_id}/toggle")
async def admin_utilisateurs_toggle(
    user_id: str, request: Request, db: Session = Depends(get_db)
):
    courant = require_super_admin(request, db)
    user = db.query(Utilisateur).filter(Utilisateur.id == user_id).first()
    if not user:
        return redirect_flash("/admin/utilisateurs", "Compte introuvable.", "danger")
    if user.id == courant.id:
        return redirect_flash(
            "/admin/utilisateurs",
            "Vous ne pouvez pas désactiver votre propre compte.",
            "warning",
        )
    if (user.actif and user.role == "super_admin"
            and _compte_super_admins_actifs(db, sauf_id=user.id) == 0):
        return redirect_flash(
            "/admin/utilisateurs",
            "Impossible : ce compte est le dernier super administrateur actif.",
            "danger",
        )

    user.actif = not user.actif
    db.commit()
    etat = "réactivé" if user.actif else "désactivé"
    return redirect_flash("/admin/utilisateurs", f"Compte {user.email} {etat}.", "success")


@app.post("/admin/utilisateurs/{user_id}/mot-de-passe")
async def admin_utilisateurs_mot_de_passe(
    user_id: str, request: Request, db: Session = Depends(get_db),
    mot_de_passe: str = Form(...),
):
    require_super_admin(request, db)
    user = db.query(Utilisateur).filter(Utilisateur.id == user_id).first()
    if not user:
        return redirect_flash("/admin/utilisateurs", "Compte introuvable.", "danger")
    if len(mot_de_passe) < LONGUEUR_MDP_MIN:
        return redirect_flash(
            "/admin/utilisateurs",
            f"Le mot de passe doit faire au moins {LONGUEUR_MDP_MIN} caractères.",
            "danger",
        )

    user.mot_de_passe_hash = hash_password(mot_de_passe)
    db.commit()
    return redirect_flash(
        "/admin/utilisateurs",
        f"Mot de passe de {user.email} réinitialisé. Transmettez-le par un canal sûr.",
        "success",
    )


@app.post("/admin/utilisateurs/{user_id}/supprimer")
async def admin_utilisateurs_supprimer(
    user_id: str, request: Request, db: Session = Depends(get_db)
):
    courant = require_super_admin(request, db)
    user = db.query(Utilisateur).filter(Utilisateur.id == user_id).first()
    if not user:
        return redirect_flash("/admin/utilisateurs", "Compte introuvable.", "danger")
    if user.id == courant.id:
        return redirect_flash(
            "/admin/utilisateurs", "Vous ne pouvez pas supprimer votre propre compte.", "warning"
        )
    if (user.role == "super_admin"
            and _compte_super_admins_actifs(db, sauf_id=user.id) == 0):
        return redirect_flash(
            "/admin/utilisateurs",
            "Impossible : ce compte est le dernier super administrateur actif.",
            "danger",
        )

    email = user.email
    db.delete(user)
    db.commit()
    return redirect_flash("/admin/utilisateurs", f"Compte {email} supprimé.", "success")


@app.get("/admin/utilisateurs", response_class=HTMLResponse)
async def admin_utilisateurs(
    request: Request, db: Session = Depends(get_db), q: str = "", role: str = "",
    page: int = 1, par_page: int = PAR_PAGE_DEFAUT, tri: str = "", sens: str = "",
):
    require_super_admin(request, db)
    params = get_params_with_defaults(db)

    requete = db.query(Utilisateur)
    if q:
        motif = f"%{q.strip()}%"
        requete = requete.filter(
            (Utilisateur.email.ilike(motif))
            | (Utilisateur.nom.ilike(motif))
            | (Utilisateur.prenom.ilike(motif))
        )
    if role in ROLES:
        requete = requete.filter(Utilisateur.role == role)

    requete, etat_tri = appliquer_tri(requete, tri, sens, {
        "nom": Utilisateur.nom,
        "email": Utilisateur.email,
        "role": Utilisateur.role,
        "etablissement": Utilisateur.etablissement_code,
        "creation": (Utilisateur.created_at, "desc"),
        "connexion": (Utilisateur.derniere_connexion, "desc"),
    }, defaut="creation")
    users, pagination = paginate(requete, page, normaliser_par_page(par_page))

    # Les compteurs de tête portent sur l'ensemble des comptes, pas sur la
    # page affichée : ils étaient calculés dans le gabarit à partir de la
    # liste rendue, ce qui devenait faux dès la première pagination.
    nb_comptes = db.query(Utilisateur).count()
    nb_actifs = db.query(Utilisateur).filter(
        Utilisateur.actif == True  # noqa: E712
    ).count()
    nb_etabs_couverts = (
        db.query(Utilisateur.etablissement_code)
        .filter(Utilisateur.etablissement_code.isnot(None))
        .distinct().count()
    )

    etabs = (
        db.query(Etablissement)
        .filter(Etablissement.actif == True)  # noqa: E712
        .order_by(Etablissement.code)
        .all()
    )
    return templates.TemplateResponse("admin/utilisateurs.html", {
        "request": request, "params": params, "utilisateurs": users, "etablissements": etabs,
        "pagination": pagination, "tri": etat_tri,
        "nb_comptes": nb_comptes, "nb_actifs": nb_actifs,
        "nb_etabs_couverts": nb_etabs_couverts,
        "roles": ROLES, "roles_courts": ROLES_COURTS, "q": q, "role_filtre": role,
        "nb_super_admins": _compte_super_admins_actifs(db),
        "longueur_mdp_min": LONGUEUR_MDP_MIN,
        "current_user": require_auth(request, db), "active_nav": "utilisateurs",
    })

@app.get("/admin/mon-compte", response_class=HTMLResponse)
async def admin_mon_compte(request: Request, db: Session = Depends(get_db)):
    """Compte personnel : identité, courriel, mot de passe — tout rôle."""
    utilisateur = utilisateur_courant(request, db)
    etab = None
    if utilisateur.etablissement_code:
        etab = db.query(Etablissement).filter(
            Etablissement.code == utilisateur.etablissement_code
        ).first()
    return templates.TemplateResponse("admin/mon_compte.html", {
        "request": request, "params": get_params_with_defaults(db),
        "utilisateur": utilisateur, "etab": etab,
        "role_libelle": ROLES.get(permissions.role_de(utilisateur), utilisateur.role),
        "longueur_mdp_min": LONGUEUR_MDP_MIN,
        "current_user": utilisateur, "active_nav": "mon_compte",
    })


@app.post("/admin/mon-compte")
async def admin_mon_compte_save(
    request: Request, db: Session = Depends(get_db),
    prenom: str = Form(""), nom: str = Form(""),
):
    """Prénom et nom seulement. Le courriel est l'identifiant du compte :
    il n'est modifiable que par le super administrateur (page
    Utilisateurs). Un champ « email » envoyé ici est ignoré."""
    session_user = utilisateur_courant(request, db)
    # L'objet du middleware est détaché de la session : on relit le compte.
    user = db.query(Utilisateur).filter(Utilisateur.id == session_user.id).first()
    user.prenom = prenom.strip()[:100] or None
    user.nom = nom.strip()[:100] or None
    try:
        db.commit()
    except Exception:
        db.rollback()
        return redirect_flash("/admin/mon-compte", "Enregistrement impossible.", "danger")
    return redirect_flash("/admin/mon-compte", "Informations enregistrées.", "success")


@app.post("/admin/mon-compte/mot-de-passe")
async def admin_mon_compte_mot_de_passe(
    request: Request, db: Session = Depends(get_db),
    mot_de_passe_actuel: str = Form(...), nouveau: str = Form(...),
    confirmation: str = Form(...),
):
    retour = "/admin/mon-compte"
    session_user = utilisateur_courant(request, db)
    user = db.query(Utilisateur).filter(Utilisateur.id == session_user.id).first()

    if not verify_password(mot_de_passe_actuel, user.mot_de_passe_hash):
        return redirect_flash(retour, "Le mot de passe actuel est incorrect.", "danger")
    if nouveau != confirmation:
        return redirect_flash(retour, "Les deux saisies du nouveau mot de passe diffèrent.", "danger")
    if nouveau == mot_de_passe_actuel:
        return redirect_flash(retour, "Le nouveau mot de passe doit être différent de l'actuel.", "danger")
    erreur = erreur_mot_de_passe(nouveau, user.email)
    if erreur:
        return redirect_flash(retour, erreur, "danger")

    user.mot_de_passe_hash = hash_password(nouveau)
    user.mdp_modifie_le = maintenant_utc()
    db.commit()

    reponse = redirect_flash(
        retour,
        "Mot de passe modifié. Vos autres sessions ouvertes ont été fermées.",
        "success",
    )
    # Les jetons émis avant le changement sont désormais refusés,
    # y compris celui de cette session : on en émet un nouveau.
    poser_session(reponse, user)
    return reponse


@app.get("/admin/acces", response_class=HTMLResponse)
async def admin_acces(
    request: Request, db: Session = Depends(get_db),
    page: int = 1, par_page: int = PAR_PAGE_DEFAUT, tri: str = "", sens: str = "",
):
    params = get_params_with_defaults(db)
    requete, etat_tri = appliquer_tri(db.query(AccesException), tri, sens, {
        "niveau": AccesException.niveau,
        "reference": AccesException.reference,
        "acces": AccesException.acces,
        "creation": (AccesException.created_at, "desc"),
    }, defaut="creation")
    exceptions, pagination = paginate(requete, page, normaliser_par_page(par_page))
    etabs = db.query(Etablissement).filter(Etablissement.actif == True).all()

    # État en cours par établissement : sans lui, les deux boutons du bloc
    # « Accès rapide » se ressemblaient et rien ne disait lequel était
    # appliqué. On ne peut pas choisir sans voir où l'on en est.
    regles_etab = {
        r.reference: r.acces
        for r in db.query(AccesException).filter(
            AccesException.niveau == "etablissement"
        ).all()
    }

    return templates.TemplateResponse("admin/acces.html", {
        "request": request, "params": params, "exceptions": exceptions, "etablissements": etabs,
        "regles_etab": regles_etab,
        "pagination": pagination, "tri": etat_tri,
        "current_user": require_auth(request, db), "active_nav": "acces",
    })

@app.get("/admin/exports", response_class=HTMLResponse)
async def admin_exports(request: Request, db: Session = Depends(get_db)):
    utilisateur = utilisateur_courant(request, db)
    params = get_params_with_defaults(db)
    etabs = db.query(Etablissement).filter(Etablissement.actif == True)  # noqa: E712
    code = permissions.perimetre(utilisateur)
    if code is not None:
        etabs = etabs.filter(Etablissement.code == code)
    return templates.TemplateResponse("admin/exports.html", {
        "request": request, "params": params, "etablissements": etabs.all(),
        "super_admin": code is None,
        "current_user": utilisateur, "active_nav": "exports",
    })

@app.get("/admin/parametres/contenu", response_class=HTMLResponse)
async def admin_parametres_contenu(request: Request, db: Session = Depends(get_db)):
    params = get_params_with_defaults(db)
    return templates.TemplateResponse("admin/parametres_contenu.html", {
        "request": request, "params": params,
        "current_user": require_auth(request, db), "active_nav": "contenu",
    })

@app.post("/admin/parametres/contenu")
async def admin_parametres_contenu_save(
    db: Session = Depends(get_db),
    apropos_fr: str = Form(""), apropos_en: str = Form(""), apropos_pt: str = Form(""),
    contact_nom: str = Form(""), contact_adresse: str = Form(""),
    contact_tel: str = Form(""), contact_email: str = Form("")
):
    contact = json.dumps({"nom": contact_nom, "adresse": contact_adresse,
                          "tel": contact_tel, "email": contact_email})

    # Le contenu est rendu avec |safe sur la page publique : il doit être
    # filtré ici, à l'entrée. Le nettoyage côté navigateur ne protège de
    # rien — il suffit d'envoyer ce POST à la main pour le contourner.
    for cle, valeur in [("apropos_fr", html_riche.nettoyer(apropos_fr)),
                        ("apropos_en", html_riche.nettoyer(apropos_en)),
                        ("apropos_pt", html_riche.nettoyer(apropos_pt)),
                        ("contact_json", contact)]:
        row = db.query(Parametre).filter(Parametre.cle == cle).first()
        if row: row.valeur = valeur
        else: db.add(Parametre(cle=cle, valeur=valeur, type="text"))
    db.commit()
    return redirect_flash(
        "/admin/parametres/contenu", "Contenu de la page À propos enregistré.", "success"
    )

@app.get("/admin/parametres/partenaires", response_class=HTMLResponse)
async def admin_parametres_partenaires(request: Request, db: Session = Depends(get_db)):
    params = get_params_with_defaults(db)
    return templates.TemplateResponse("admin/parametres_partenaires.html", {
        "request": request, "params": params,
        "current_user": require_auth(request, db), "active_nav": "partenaires",
    })

@app.get("/admin/parametres/general", response_class=HTMLResponse)
async def admin_parametres_general(request: Request, db: Session = Depends(get_db)):
    params = get_params_with_defaults(db)
    return templates.TemplateResponse("admin/parametres_general.html", {
        "request": request, "params": params,
        "current_user": require_auth(request, db), "active_nav": "general",
    })

@app.post("/admin/documents/{doc_id}/toggle-acces")
async def admin_document_toggle_acces(
    doc_id: str, request: Request, db: Session = Depends(get_db)
):
    utilisateur = utilisateur_courant(request, db)
    doc = filtrer_documents(db.query(Document), utilisateur).filter(
        Document.id == doc_id
    ).first()
    if not doc:
        return redirect_flash("/admin/documents", "Document introuvable.", "danger")
    doc.acces = "restreint" if doc.acces == "public" else "public"
    db.commit()
    return redirect_flash(
        "/admin/documents",
        f"« {doc.titre[:60]} » est désormais {doc.acces}.",
        "success",
    )

@app.post("/admin/zotero/{source_id}/supprimer")
async def admin_zotero_supprimer(source_id: int, db: Session = Depends(get_db)):
    src = db.query(ZoteroSource).filter(ZoteroSource.id == source_id).first()
    if not src:
        return redirect_flash("/admin/zotero", "Source introuvable.", "danger")
    libelle = src.etablissement.code if src.etablissement else f"source {src.id}"
    try:
        db.delete(src)
        db.commit()
    except Exception:
        db.rollback()
        return redirect_flash(
            "/admin/zotero",
            f"Suppression impossible : des documents sont encore rattachés à {libelle}.",
            "danger",
        )
    return redirect_flash("/admin/zotero", f"Compte Zotero de {libelle} supprimé.", "success")

@app.post("/admin/sync/lancer/{source_id}")
async def admin_sync_lancer_source(
    source_id: int, request: Request, db: Session = Depends(get_db)
):
    from app.sync.engine import sync_source

    utilisateur = utilisateur_courant(request, db)
    # Recherche dans le périmètre : la source d'un autre établissement
    # est « introuvable », sans confirmer qu'elle existe.
    src = sources_visibles(db, utilisateur).filter(ZoteroSource.id == source_id).first()
    if not src:
        return redirect_flash("/admin/sync", "Source introuvable.", "danger")
    if not src.actif:
        return redirect_flash(
            "/admin/sync",
            "Cette source est désactivée. Réactivez-la avant de la synchroniser.",
            "warning",
        )

    libelle = src.etablissement.code if src.etablissement else f"source {src.id}"

    def _sync_une(db_bg, source_id: int):
        source = db_bg.query(ZoteroSource).filter(ZoteroSource.id == source_id).first()
        if source:
            sync_source(db_bg, source, declenchement="manuel")

    lance = tasks.run_in_background(_sync_une, src.id, key=f"sync:{src.id}")
    if not lance:
        return redirect_flash(
            "/admin/sync", f"La synchronisation de {libelle} est déjà en cours.", "info"
        )
    return redirect_flash("/admin/sync", f"Synchronisation de {libelle} lancée.", "success")

@app.post("/admin/acces/definir")
async def admin_acces_definir(
    db: Session = Depends(get_db),
    niveau: str = Form(...), reference: str = Form(...), acces: str = Form(...)
):
    existing = db.query(AccesException).filter(
        AccesException.niveau == niveau,
        AccesException.reference == reference
    ).first()
    if existing:
        existing.acces = acces
    else:
        db.add(AccesException(niveau=niveau, reference=reference, acces=acces))
    db.commit()
    return redirect_flash(
        "/admin/acces", f"Règle d'accès enregistrée pour « {reference} ».", "success"
    )

@app.post("/admin/acces/{ex_id}/supprimer")
async def admin_acces_supprimer(ex_id: int, db: Session = Depends(get_db)):
    ex = db.query(AccesException).filter(AccesException.id == ex_id).first()
    if not ex:
        return redirect_flash("/admin/acces", "Règle introuvable.", "danger")
    reference = ex.reference
    db.delete(ex)
    db.commit()
    return redirect_flash("/admin/acces", f"Règle sur « {reference} » supprimée.", "success")

@app.get("/admin/exports/documents")
async def admin_exports_documents(
    request: Request, db: Session = Depends(get_db),
    format: str = "csv", etablissement: str = "", type: str = ""
):
    query = filtrer_documents(db.query(Document), utilisateur_courant(request, db))
    if etablissement: query = query.filter(Document.etablissement_code == etablissement)
    if type: query = query.filter(Document.type == type)
    docs = query.order_by(Document.annee.desc()).all()

    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Numéro national","Titre","Auteur","Type","Statut","Année","Établissement","Domaine","Langue","URL"])
        for d in docs:
            writer.writerow([d.numero_national, d.titre, d.auteur, d.type, d.statut,
                            d.annee, d.etablissement_code, d.domaine or "", d.langue or "", d.url_document or ""])
        output.seek(0)
        return StreamingResponse(io.BytesIO(output.getvalue().encode("utf-8-sig")),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=scholarsync-documents.csv"})

    elif format == "xlsx":
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Documents"
        headers = ["Numéro national","Titre","Auteur","Type","Statut","Année","Établissement","Sous-entité","Domaine","Langue","Directeur","URL"]
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1a3a5c")
            cell.alignment = Alignment(horizontal="center")
        for row, d in enumerate(docs, 2):
            ws.append([d.numero_national, d.titre, d.auteur, d.type, d.statut,
                      d.annee, d.etablissement_code, d.sous_entite_nom or "",
                      d.domaine or "", d.langue or "", d.directeur or "", d.url_document or ""])
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        return StreamingResponse(output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=scholarsync-documents.xlsx"})

    return JSONResponse({"error": "Format non supporté"}, status_code=400)

@app.get("/admin/exports/rapport")
async def admin_exports_rapport(request: Request, db: Session = Depends(get_db)):
    code = permissions.perimetre(utilisateur_courant(request, db))
    stats = get_stats(db, code)
    from fastapi.responses import HTMLResponse
    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
    <style>body{{font-family:Arial,sans-serif;padding:2rem;}}
    h1{{color:#1a3a5c;}} table{{width:100%;border-collapse:collapse;}}
    th{{background:#1a3a5c;color:white;padding:8px;}} td{{padding:8px;border:1px solid #ddd;}}
    </style></head><body>
    <h1>Rapport ScholarSync{(" — " + code) if code and code != permissions.AUCUN else ""}</h1>
    <p>Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')}</p>
    <h2>Statistiques générales</h2>
    <table><tr><th>Indicateur</th><th>Valeur</th></tr>
    <tr><td>Total documents</td><td>{stats['total']}</td></tr>
    <tr><td>Thèses</td><td>{stats['nb_theses']}</td></tr>
    <tr><td>Mémoires</td><td>{stats['nb_memoires']}</td></tr>
    <tr><td>Soutenus</td><td>{stats['nb_soutenus']}</td></tr>
    <tr><td>En préparation</td><td>{stats['nb_preparation']}</td></tr>
    <tr><td>Établissements</td><td>{stats['nb_etablissements']}</td></tr>
    </table></body></html>"""
    return HTMLResponse(content=html)

@app.post("/admin/parametres/partenaires/ajouter")
async def admin_partenaires_ajouter(
    db: Session = Depends(get_db),
    nom: str = Form(...), url: str = Form(""), logo: str = Form("")
):
    import json
    row = db.query(Parametre).filter(Parametre.cle == "partenaires_json").first()
    partenaires = json.loads(row.valeur) if row and row.valeur else []
    partenaires.append({"nom": nom, "url": url or None, "logo": logo or None})
    if row: row.valeur = json.dumps(partenaires)
    else: db.add(Parametre(cle="partenaires_json", valeur=json.dumps(partenaires), type="json"))
    db.commit()
    return redirect_flash(
        "/admin/parametres/partenaires", f"Partenaire « {nom} » ajouté.", "success"
    )

@app.post("/admin/parametres/partenaires/supprimer")
async def admin_partenaires_supprimer(db: Session = Depends(get_db), nom: str = Form(...)):
    import json
    row = db.query(Parametre).filter(Parametre.cle == "partenaires_json").first()
    if row:
        partenaires = json.loads(row.valeur)
        partenaires = [p for p in partenaires if p["nom"] != nom]
        row.valeur = json.dumps(partenaires)
        db.commit()
    return redirect_flash(
        "/admin/parametres/partenaires", f"Partenaire « {nom} » retiré.", "success"
    )

@app.post("/admin/parametres/general")
async def admin_parametres_general_save(
    request: Request, db: Session = Depends(get_db),
    sync_intervalle_min: str = Form("60"),
    pied_page_texte: str = Form(""), institution_nom: str = Form(""),
    langue_fr: str = Form(None), langue_en: str = Form(None), langue_pt: str = Form(None)
):
    import json
    langues = [l for l, v in [("fr", langue_fr), ("en", langue_en), ("pt", langue_pt)] if v]
    if not langues: langues = ["fr"]
    updates = {
        "sync_intervalle_min": sync_intervalle_min,
        "pied_page_texte": pied_page_texte,
        "institution_nom": institution_nom,
        "langues_actives": json.dumps(langues),
    }
    for cle, valeur in updates.items():
        row = db.query(Parametre).filter(Parametre.cle == cle).first()
        if row: row.valeur = valeur
        else: db.add(Parametre(cle=cle, valeur=valeur, type="text"))
    db.commit()
    return redirect_flash(
        "/admin/parametres/general", "Paramètres généraux enregistrés.", "success"
    )


# ─── AUTH ─────────────────────────────────────────────────────────

@app.get("/admin/connexion", response_class=HTMLResponse)
async def admin_connexion_get(
    request: Request, db: Session = Depends(get_db),
    next: str = "/admin", erreur: str = None, message: str = None
):
    user = get_current_user(request, db)
    if user:
        return RedirectResponse("/admin", status_code=302)
    params = get_params_with_defaults(db)
    return templates.TemplateResponse("admin/connexion.html", {
        "request": request, "params": params,
        "erreur": erreur, "message": message, "next": next, "email_prefill": None,
    })

@app.post("/admin/connexion")
async def admin_connexion_post(
    request: Request, db: Session = Depends(get_db),
    email: str = Form(...), password: str = Form(...),
    next: str = Form("/admin"), remember: str = Form(None)
):
    params = get_params_with_defaults(db)
    user = db.query(Utilisateur).filter(
        Utilisateur.email == email.lower().strip(),
        Utilisateur.actif == True
    ).first()

    if not user or not verify_password(password, user.mot_de_passe_hash):
        return templates.TemplateResponse("admin/connexion.html", {
            "request": request, "params": params,
            "erreur": "Email ou mot de passe incorrect",
            "next": next, "email_prefill": email, "message": None,
        })

    user.derniere_connexion = datetime.now()
    db.commit()

    expires = 60 * 24 * 30 if remember else settings.ACCESS_TOKEN_EXPIRE_MINUTES
    # « //site.tld » commence aussi par « / » : on n'accepte qu'un
    # chemin de l'administration, jamais une adresse externe.
    redirect_url = next if next.startswith("/admin") and not next.startswith("//") else "/admin"
    response = RedirectResponse(redirect_url, status_code=303)
    poser_session(response, user, expires)
    return response


def poser_session(response, user, expires_minutes: int = None):
    """Émet le cookie de session. Réutilisé après un changement de
    courriel ou de mot de passe, pour ne pas déconnecter l'auteur."""
    expires = expires_minutes or settings.ACCESS_TOKEN_EXPIRE_MINUTES
    token = create_token({"sub": user.email, "role": user.role}, expires_minutes=expires)
    response.set_cookie(
        key="scholarsync_session",
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=expires * 60
    )
    return response

@app.get("/admin/deconnexion")
async def admin_deconnexion():
    response = RedirectResponse("/admin/connexion", status_code=302)
    response.delete_cookie("scholarsync_session")
    return response

@app.get("/admin/mot-de-passe-oublie", response_class=HTMLResponse)
async def admin_mdp_oublie_get(request: Request, db: Session = Depends(get_db)):
    params = get_params_with_defaults(db)
    return templates.TemplateResponse("admin/mot_de_passe_oublie.html", {
        "request": request, "params": params, "message": None, "erreur": None,
    })

@app.post("/admin/mot-de-passe-oublie")
async def admin_mdp_oublie_post(
    request: Request, db: Session = Depends(get_db), email: str = Form(...)
):
    params = get_params_with_defaults(db)
    user = db.query(Utilisateur).filter(Utilisateur.email == email.lower().strip()).first()
    message = "Si cet email existe, un lien de réinitialisation a été envoyé."
    if user:
        token = create_token({"sub": user.email, "type": "reset"}, expires_minutes=60)
        reset_url = f"{request.base_url}admin/reset-password?token={token}"
        from app.core.auth import send_reset_email
        send_reset_email(user.email, reset_url, params)
    return templates.TemplateResponse("admin/mot_de_passe_oublie.html", {
        "request": request, "params": params, "message": message, "erreur": None,
    })

@app.get("/admin/reset-password", response_class=HTMLResponse)
async def admin_reset_get(request: Request, db: Session = Depends(get_db), token: str = ""):
    params = get_params_with_defaults(db)
    payload = decode_token(token)
    if not payload or payload.get("type") != "reset":
        return templates.TemplateResponse("admin/reset_password.html", {
            "request": request, "params": params,
            "erreur": "Lien invalide ou expiré.", "token": "",
        })
    return templates.TemplateResponse("admin/reset_password.html", {
        "request": request, "params": params, "erreur": None, "token": token,
    })

@app.post("/admin/reset-password")
async def admin_reset_post(
    request: Request, db: Session = Depends(get_db),
    token: str = Form(...), password: str = Form(...), password2: str = Form(...)
):
    params = get_params_with_defaults(db)
    payload = decode_token(token)
    if not payload or payload.get("type") != "reset":
        return templates.TemplateResponse("admin/reset_password.html", {
            "request": request, "params": params,
            "erreur": "Lien invalide ou expiré.", "token": "",
        })
    if password != password2:
        return templates.TemplateResponse("admin/reset_password.html", {
            "request": request, "params": params,
            "erreur": "Les mots de passe ne correspondent pas.", "token": token,
        })
    # La longueur minimale n'était pas vérifiée ici : le lien de
    # réinitialisation permettait de choisir un mot de passe d'un caractère.
    erreur = erreur_mot_de_passe(password, payload.get("sub") or "")
    if erreur:
        return templates.TemplateResponse("admin/reset_password.html", {
            "request": request, "params": params, "erreur": erreur, "token": token,
        })
    user = db.query(Utilisateur).filter(Utilisateur.email == payload.get("sub")).first()
    # Lien à usage unique : une fois le mot de passe changé, le même lien
    # (resté dans la boîte de courriel) ne sert plus.
    if user and jeton_perime(payload, user):
        return templates.TemplateResponse("admin/reset_password.html", {
            "request": request, "params": params,
            "erreur": "Ce lien a déjà servi. Demandez-en un nouveau.", "token": "",
        })
    if user:
        user.mot_de_passe_hash = hash_password(password)
        user.mdp_modifie_le = maintenant_utc()
        db.commit()
    return RedirectResponse("/admin/connexion?message=Mot+de+passe+modifié", status_code=303)

# ─── CRÉATION DU PREMIER ADMIN (si aucun utilisateur) ─────────────

@app.get("/admin/setup", response_class=HTMLResponse)
async def admin_setup_get(request: Request, db: Session = Depends(get_db)):
    if db.query(Utilisateur).count() > 0:
        return RedirectResponse("/admin/connexion", status_code=302)
    params = get_params_with_defaults(db)
    return templates.TemplateResponse("admin/setup.html", {
        "request": request, "params": params, "erreur": None,
    })

@app.post("/admin/setup")
async def admin_setup_post(
    request: Request, db: Session = Depends(get_db),
    email: str = Form(...), password: str = Form(...),
    nom: str = Form(""), prenom: str = Form("")
):
    if db.query(Utilisateur).count() > 0:
        return RedirectResponse("/admin/connexion", status_code=302)
    user = Utilisateur(
        email=email.lower().strip(),
        mot_de_passe_hash=hash_password(password),
        nom=nom, prenom=prenom,
        role="super_admin", actif=True
    )
    db.add(user)
    db.commit()
    return RedirectResponse("/admin/connexion?message=Compte+créé", status_code=303)

@app.post("/admin/parametres/smtp")
async def admin_smtp_save(
    db: Session = Depends(get_db),
    smtp_host: str = Form(""), smtp_port: str = Form("587"),
    smtp_user: str = Form(""), smtp_password: str = Form(""),
    smtp_from_name: str = Form(""), smtp_from_email: str = Form("")
):
    updates = {
        "smtp_host": smtp_host, "smtp_port": smtp_port,
        "smtp_user": smtp_user, "smtp_from_name": smtp_from_name,
        "smtp_from_email": smtp_from_email,
    }
    if smtp_password:
        updates["smtp_password"] = smtp_password
    for cle, valeur in updates.items():
        row = db.query(Parametre).filter(Parametre.cle == cle).first()
        if row: row.valeur = valeur
        else: db.add(Parametre(cle=cle, valeur=valeur, type="text"))
    db.commit()
    return redirect_flash(
        "/admin/parametres/general", "Configuration SMTP enregistrée.", "success"
    )

@app.post("/admin/parametres/smtp/tester")
async def admin_smtp_tester(db: Session = Depends(get_db)):
    params = get_params_with_defaults(db)
    try:
        import smtplib
        from email.mime.text import MIMEText
        host = params.get("smtp_host", "")
        port = int(params.get("smtp_port", "587"))
        user = params.get("smtp_user", "")
        password = params.get("smtp_password", "")
        if not host or not user:
            return JSONResponse({"ok": False, "message": "Configurez d'abord le serveur SMTP"})
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.ehlo()
            if port == 587:
                server.starttls()
            if password:
                server.login(user, password)
        return JSONResponse({"ok": True, "message": f"Connexion réussie à {host}:{port}"})
    except Exception as e:
        return JSONResponse({"ok": False, "message": f"Échec : {str(e)[:100]}"})


# ─── PAGES D'ERREUR ───────────────────────────────────────────────
from fastapi.exceptions import HTTPException as FastAPIHTTPException
from starlette.exceptions import HTTPException as StarletteHTTPException


def _erreur_params() -> dict:
    """Paramètres d'affichage, sans faire échouer la page d'erreur elle-même."""
    db = SessionLocal()
    try:
        return get_params_with_defaults(db)
    except Exception:
        return {"nom_outil": "ScholarSync", "couleur_principale": "#1a3a5c",
                "couleur_secondaire": "#8b1a2e", "logo_url": ""}
    finally:
        db.close()


@app.exception_handler(StarletteHTTPException)
async def handler_http(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith(("/api/", "/admin/sync/etat")):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    if exc.status_code == 404:
        titre, message = "Page introuvable", (
            "Cette adresse ne correspond à aucune page. "
            "Le document a peut-être été retiré ou l'adresse mal recopiée."
        )
    elif exc.status_code == 403:
        titre, message = "Accès refusé", (
            "Vous n'avez pas les droits nécessaires pour consulter cette page."
        )
    else:
        titre, message = "Une erreur est survenue", str(exc.detail or "")

    return templates.TemplateResponse(
        "erreur.html",
        {"request": request, "params": _erreur_params(), "code": exc.status_code,
         "titre": titre, "message": message},
        status_code=exc.status_code,
    )


@app.exception_handler(Exception)
async def handler_500(request: Request, exc: Exception):
    logging.getLogger("scholarsync").exception(
        "Erreur non gérée sur %s", request.url.path
    )
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Erreur interne"}, status_code=500)
    return templates.TemplateResponse(
        "erreur.html",
        {"request": request, "params": _erreur_params(), "code": 500,
         "titre": "Erreur interne",
         "message": "Le serveur a rencontré un problème. L'incident a été enregistré."},
        status_code=500,
    )
