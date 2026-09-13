from fastapi import FastAPI, Request, Depends, HTTPException, Form, UploadFile, File, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func, extract
from typing import Optional, List
import json, os, math, logging
from datetime import datetime, timedelta

from app.core.database import get_db, engine, SessionLocal
from app.core.config import settings
from app.core.auth import (
    hash_password, verify_password, create_token, decode_token,
    get_current_user, require_auth, require_super_admin
)
from app.core import tasks
from app.core.flash import read_flash, redirect_flash, set_flash, COOKIE_NAME as FLASH_COOKIE
from app.core import schema as schema_bd
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
    PUBLIC_ADMIN_PATHS = {
        "/admin/connexion", "/admin/setup",
        "/admin/mot-de-passe-oublie", "/admin/reset-password",
        "/admin/deconnexion",
    }
    async def dispatch(self, request, call_next):
        path = request.url.path
        if path.startswith("/admin") and path not in self.PUBLIC_ADMIN_PATHS:
            token = request.cookies.get("scholarsync_session")
            if not token:
                return StarletteRedirect(f"/admin/connexion?next={path}")
            payload = decode_token(token)
            if not payload:
                return StarletteRedirect("/admin/connexion")
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


def sync_log_actif(db: Session) -> Optional[SyncLog]:
    """
    Synchronisation réellement en cours, d'après la base.

    L'ensemble en mémoire de app.core.tasks ne connaît que le processus
    courant : derrière plusieurs workers uvicorn, le worker qui répond
    n'est pas forcément celui qui synchronise. La base est le seul état
    partagé entre eux.
    """
    log = (
        db.query(SyncLog)
        .filter(SyncLog.statut == "en_cours")
        .order_by(SyncLog.debut.desc())
        .first()
    )
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

def construire_query_string(filtres: dict, sort: str = "recent") -> str:
    """Chaîne de requête conservant les valeurs multiples et l'encodage."""
    from urllib.parse import urlencode
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

    facettes = {
        "etablissements": _compter(db, Document.etablissement_code, filtres, "etablissement"),
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


def paginate(query, page: int, per_page: int = 20):
    total = query.count()
    pages = math.ceil(total / per_page) if total else 1
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
    return items, {"total": total, "pages": pages, "page": page, "per_page": per_page, "range": page_range}

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
                       sort: str, page: int) -> dict:
    """
    Contexte du catalogue, partagé par l'accueil et la recherche.

    Les deux routes construisaient auparavant le même contexte chacune de
    leur côté, avec un tri écrit en dur d'un côté seulement : toute
    évolution devait être reportée deux fois, et l'accueil affichait déjà
    un tri différent de celui annoncé par son propre sélecteur.
    """
    if sort not in TRIS:
        sort = TRI_DEFAUT

    query = appliquer_filtres(db.query(Document), filtres).order_by(*TRIS[sort])
    docs, pagination = paginate(query, page)

    return {
        "request": request,
        "params": get_params_with_defaults(db),
        "stats": get_stats(db),
        "facettes": get_facettes(db, filtres),
        "documents": docs,
        "pagination": pagination,
        "current_filters": filtres,
        "sort": sort,
        "query_string": construire_query_string(filtres, sort),
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
):
    filtres = {k: v for k, v in {
        "q": q.strip(), "type": type, "statut": statut,
        "etablissement": etablissement, "domaine": domaine,
        "annee": annee, "langue": langue, "sous_entite": sous_entite,
    }.items() if v}

    contexte = contexte_catalogue(request, db, filtres, sort, page)

    # Requête émise par le script de facettes : on ne renvoie que les
    # fragments qui changent, pas la page entière.
    if request.headers.get("X-Requested-With") == "facettes":
        return templates.TemplateResponse(
            "public/components/fragment_resultats.html", contexte
        )

    return templates.TemplateResponse("public/index.html", contexte)


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
    return templates.TemplateResponse("public/document.html", {
        "request": request, "params": params, "doc": doc,
        "active_nav": "", "lang": "fr",
    })

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

@app.get("/statistiques", response_class=HTMLResponse)
async def statistiques_page(request: Request, db: Session = Depends(get_db)):
    params = get_params_with_defaults(db)
    stats = get_stats(db)
    stats_annees = [
        {"annee": r[0],
         "theses": db.query(Document).filter(Document.annee == r[0], Document.type == "these").count(),
         "memoires": db.query(Document).filter(Document.annee == r[0], Document.type == "memoire").count()}
        for r in db.query(Document.annee).distinct().order_by(Document.annee).all()
    ]
    stats_domaines = [
        {"domaine": r[0], "count": r[1]}
        for r in db.query(Document.domaine, func.count().label("count"))
                   .filter(Document.domaine.isnot(None))
                   .group_by(Document.domaine).order_by(func.count().desc()).all()
    ]
    stats_etabs = [
        {"code": r[0], "count": r[1]}
        for r in db.query(Document.etablissement_code, func.count().label("count"))
                   .group_by(Document.etablissement_code)
                   .order_by(func.count().desc()).all()
    ]
    return templates.TemplateResponse("public/statistiques.html", {
        "request": request, "params": params, "stats": stats,
        "stats_annees": stats_annees, "stats_domaines": stats_domaines,
        "stats_etabs": stats_etabs, "active_nav": "statistiques", "lang": "fr",
    })

# ─── ROUTES ADMIN ─────────────────────────────────────────────────

def get_current_user_mock():
    return {"role": "super_admin", "email": "admin@scholarsync.local", "nom": "Admin"}

@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    params = get_params_with_defaults(db)
    stats = get_stats(db)
    stats_annees = [
        {"annee": r[0],
         "theses": db.query(Document).filter(Document.annee == r[0], Document.type == "these").count(),
         "memoires": db.query(Document).filter(Document.annee == r[0], Document.type == "memoire").count()}
        for r in db.query(Document.annee).distinct().order_by(Document.annee).all()
    ]
    stats_domaines = [
        {"domaine": r[0] or "Non défini", "count": r[1]}
        for r in db.query(Document.domaine, func.count().label("count"))
                   .group_by(Document.domaine).order_by(func.count().desc()).limit(8).all()
    ]
    stats_etabs = [
        {"code": r[0], "count": r[1]}
        for r in db.query(Document.etablissement_code, func.count().label("count"))
                   .group_by(Document.etablissement_code)
                   .order_by(func.count().desc()).all()
    ]
    derniers_logs = db.query(SyncLog).order_by(SyncLog.debut.desc()).limit(5).all()
    for log in derniers_logs:
        src = db.query(ZoteroSource).filter(ZoteroSource.id == log.zotero_source_id).first()
        log.etablissement_code = src.etablissement.code if src else "—"
    docs_recents = db.query(Document).order_by(Document.created_at.desc()).limit(6).all()

    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request, "params": params, "stats": stats,
        "stats_annees": json.dumps(stats_annees),
        "stats_domaines": json.dumps(stats_domaines),
        "stats_etabs": json.dumps(stats_etabs),
        "derniers_logs": derniers_logs, "docs_recents": docs_recents,
        "current_user": require_auth(request, db),
        "active_nav": "dashboard", "sync_en_cours": False,
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
async def admin_sync_etat(db: Session = Depends(get_db)):
    """Avancement de la synchronisation, interrogé par le client toutes les 2 s."""
    log = sync_log_actif(db)
    en_cours = log is not None or any(
        k.startswith("sync") for k in tasks.running_keys()
    )
    if log is None:
        log = db.query(SyncLog).order_by(SyncLog.debut.desc()).first()

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


# Le type MIME décide de l'extension. L'ancienne version reprenait
# l'extension du nom de fichier envoyé par le client : celle-ci pouvait
# contenir « / » et « .. » (écriture hors du dossier prévu), ou valoir
# « html » — un fichier alors servi depuis la même origine que
# l'application, donc du script exécuté dans la session d'un visiteur.
EXTENSIONS_LOGO = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/svg+xml": "svg",
    "image/webp": "webp",
}

# Signatures de fichier : le type MIME est déclaré par le client, il ne
# prouve rien. On vérifie que le contenu correspond vraiment.
SIGNATURES_LOGO = {
    "png":  [b"\x89PNG\r\n\x1a\n"],
    "jpg":  [b"\xff\xd8\xff"],
    "webp": [b"RIFF"],
}


async def enregistrer_logo(logo: UploadFile) -> dict:
    """Valide puis enregistre le logo. Renvoie {"url": …} ou {"erreur": …}."""
    extension = EXTENSIONS_LOGO.get((logo.content_type or "").lower())
    if not extension:
        acceptes = ", ".join(sorted({e.upper() for e in EXTENSIONS_LOGO.values()}))
        return {"erreur": f"Format non accepté. Formats possibles : {acceptes}."}

    contenu = await logo.read()

    taille_max = settings.MAX_LOGO_SIZE_MB * 1024 * 1024
    if len(contenu) > taille_max:
        return {"erreur": (
            f"Le fichier pèse {len(contenu) / 1024 / 1024:.1f} Mo, "
            f"au-delà de la limite de {settings.MAX_LOGO_SIZE_MB} Mo."
        )}
    if not contenu:
        return {"erreur": "Le fichier est vide."}

    signatures = SIGNATURES_LOGO.get(extension)
    if signatures and not any(contenu.startswith(sig) for sig in signatures):
        return {"erreur": (
            "Le contenu du fichier ne correspond pas à un "
            f"{extension.upper()}. Vérifiez le fichier envoyé."
        )}
    if extension == "svg":
        # Un SVG est du XML exécutable par le navigateur : il est servi
        # depuis l'origine de l'application, donc un <script> à
        # l'intérieur s'exécuterait dans la session d'un visiteur.
        debut = contenu[:4096].lower()
        if b"<script" in debut or b"javascript:" in debut or b"onload=" in debut:
            return {"erreur": "Ce SVG contient du script : il a été refusé."}

    dossier = f"app/{settings.UPLOAD_DIR}"
    chemin = f"{dossier}/logo.{extension}"
    try:
        os.makedirs(dossier, exist_ok=True)
        with open(chemin, "wb") as f:
            f.write(contenu)
        # Purger les logos d'un autre format, sinon l'ancien fichier
        # resterait servi à côté du nouveau.
        for autre in set(EXTENSIONS_LOGO.values()) - {extension}:
            try:
                os.remove(f"{dossier}/logo.{autre}")
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
    return {"url": f"/static/img/uploads/logo.{extension}?v={int(datetime.now().timestamp())}"}


@app.get("/admin/parametres/identite", response_class=HTMLResponse)
async def admin_parametres_identite(request: Request, db: Session = Depends(get_db)):
    params = get_params_with_defaults(db)
    return templates.TemplateResponse("admin/parametres_identite.html", {
        "request": request, "params": params,
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
        resultat = await enregistrer_logo(logo)
        if resultat.get("erreur"):
            return redirect_flash(
                "/admin/parametres/identite", resultat["erreur"], "danger"
            )
        updates["logo_url"] = resultat["url"]

    for cle, valeur in updates.items():
        row = db.query(Parametre).filter(Parametre.cle == cle).first()
        if row:
            row.valeur = valeur
        else:
            db.add(Parametre(cle=cle, valeur=valeur, type="text"))
    db.commit()
    return redirect_flash(
        "/admin/parametres/identite", "Identité visuelle enregistrée.", "success"
    )

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
    q: str = "", page: int = 1
):
    params = get_params_with_defaults(db)
    query = db.query(Document)
    if q:
        query = query.filter(
            Document.titre.ilike(f"%{q}%") | Document.auteur.ilike(f"%{q}%")
        )
    docs, pagination = paginate(query.order_by(Document.created_at.desc()), page, 25)
    return templates.TemplateResponse("admin/documents.html", {
        "request": request, "params": params, "documents": docs,
        "pagination": pagination, "q": q,
        "current_user": require_auth(request, db), "active_nav": "documents",
    })

@app.get("/api/stats")
async def api_stats(db: Session = Depends(get_db)):
    return get_stats(db)

@app.get("/admin/etablissements", response_class=HTMLResponse)
async def admin_etablissements(request: Request, db: Session = Depends(get_db)):
    params = get_params_with_defaults(db)
    etabs = db.query(Etablissement).order_by(Etablissement.nom).all()
    return templates.TemplateResponse("admin/etablissements.html", {
        "request": request, "params": params, "etablissements": etabs,
        "current_user": require_auth(request, db), "active_nav": "etablissements",
    })

@app.post("/admin/etablissements/ajouter")
async def admin_etablissements_ajouter(
    db: Session = Depends(get_db),
    code: str = Form(...), nom: str = Form(...), nom_court: str = Form(""),
    pays: str = Form("Sénégal"), ville: str = Form(""), url_site: str = Form("")
):
    code = code.strip().upper()
    if not code or not nom.strip():
        return redirect_flash(
            "/admin/etablissements", "Le code et le nom sont obligatoires.", "danger"
        )
    if db.query(Etablissement).filter(Etablissement.code == code).first():
        return redirect_flash(
            "/admin/etablissements", f"Le code {code} est déjà utilisé.", "warning"
        )

    db.add(Etablissement(
        code=code, nom=nom.strip(), nom_court=nom_court.strip() or None,
        pays=pays, ville=ville.strip() or None, url_site=url_site.strip() or None,
    ))
    try:
        db.commit()
    except Exception:
        db.rollback()
        return redirect_flash(
            "/admin/etablissements", "Enregistrement impossible.", "danger"
        )
    return redirect_flash(
        "/admin/etablissements", f"Établissement {code} ajouté.", "success"
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
async def admin_sync(request: Request, db: Session = Depends(get_db)):
    params = get_params_with_defaults(db)
    logs = db.query(SyncLog).order_by(SyncLog.debut.desc()).limit(20).all()
    for log in logs:
        src = db.query(ZoteroSource).filter(ZoteroSource.id == log.zotero_source_id).first()
        log.etablissement_code = src.etablissement.code if src else "—"
    sources = db.query(ZoteroSource).all()
    sync_intervalle = int(params.get("sync_intervalle_min", "60"))
    return templates.TemplateResponse("admin/sync.html", {
        "request": request, "params": params, "logs": logs, "sources": sources,
        "sync_intervalle": sync_intervalle,
        "current_user": require_auth(request, db), "active_nav": "sync",
    })

@app.get("/admin/sync-logs", response_class=HTMLResponse)
async def admin_sync_logs(request: Request, db: Session = Depends(get_db)):
    params = get_params_with_defaults(db)
    logs = db.query(SyncLog).order_by(SyncLog.debut.desc()).limit(50).all()
    for log in logs:
        src = db.query(ZoteroSource).filter(ZoteroSource.id == log.zotero_source_id).first()
        log.etablissement_code = src.etablissement.code if src else "—"
    return templates.TemplateResponse("admin/sync_logs.html", {
        "request": request, "params": params, "logs": logs,
        "current_user": require_auth(request, db), "active_nav": "sync-logs",
    })

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
    request: Request, db: Session = Depends(get_db), q: str = "", role: str = ""
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

    users = requete.order_by(Utilisateur.created_at.desc()).all()
    etabs = (
        db.query(Etablissement)
        .filter(Etablissement.actif == True)  # noqa: E712
        .order_by(Etablissement.code)
        .all()
    )
    return templates.TemplateResponse("admin/utilisateurs.html", {
        "request": request, "params": params, "utilisateurs": users, "etablissements": etabs,
        "roles": ROLES, "roles_courts": ROLES_COURTS, "q": q, "role_filtre": role,
        "nb_super_admins": _compte_super_admins_actifs(db),
        "longueur_mdp_min": LONGUEUR_MDP_MIN,
        "current_user": require_auth(request, db), "active_nav": "utilisateurs",
    })

@app.get("/admin/acces", response_class=HTMLResponse)
async def admin_acces(request: Request, db: Session = Depends(get_db)):
    params = get_params_with_defaults(db)
    exceptions = db.query(AccesException).order_by(AccesException.created_at.desc()).all()
    etabs = db.query(Etablissement).filter(Etablissement.actif == True).all()
    return templates.TemplateResponse("admin/acces.html", {
        "request": request, "params": params, "exceptions": exceptions, "etablissements": etabs,
        "current_user": require_auth(request, db), "active_nav": "acces",
    })

@app.get("/admin/exports", response_class=HTMLResponse)
async def admin_exports(request: Request, db: Session = Depends(get_db)):
    params = get_params_with_defaults(db)
    etabs = db.query(Etablissement).filter(Etablissement.actif == True).all()
    return templates.TemplateResponse("admin/exports.html", {
        "request": request, "params": params, "etablissements": etabs,
        "current_user": require_auth(request, db), "active_nav": "exports",
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
    import json
    contact = json.dumps({"nom": contact_nom, "adresse": contact_adresse,
                          "tel": contact_tel, "email": contact_email})
    for cle, valeur in [("apropos_fr", apropos_fr), ("apropos_en", apropos_en),
                        ("apropos_pt", apropos_pt), ("contact_json", contact)]:
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
async def admin_document_toggle_acces(doc_id: str, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == doc_id).first()
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
async def admin_sync_lancer_source(source_id: int, db: Session = Depends(get_db)):
    from app.sync.engine import sync_source

    src = db.query(ZoteroSource).filter(ZoteroSource.id == source_id).first()
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
    db: Session = Depends(get_db),
    format: str = "csv", etablissement: str = "", type: str = ""
):
    from fastapi.responses import StreamingResponse
    import csv, io
    query = db.query(Document)
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
async def admin_exports_rapport(db: Session = Depends(get_db)):
    stats = get_stats(db)
    from fastapi.responses import HTMLResponse
    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
    <style>body{{font-family:Arial,sans-serif;padding:2rem;}}
    h1{{color:#1a3a5c;}} table{{width:100%;border-collapse:collapse;}}
    th{{background:#1a3a5c;color:white;padding:8px;}} td{{padding:8px;border:1px solid #ddd;}}
    </style></head><body>
    <h1>Rapport ScholarSync</h1>
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
    token = create_token({"sub": user.email, "role": user.role}, expires_minutes=expires)

    redirect_url = next if next.startswith("/admin") else "/admin"
    response = RedirectResponse(redirect_url, status_code=303)
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
    user = db.query(Utilisateur).filter(Utilisateur.email == payload.get("sub")).first()
    if user:
        user.mot_de_passe_hash = hash_password(password)
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
