from fastapi import FastAPI, Request, Depends, HTTPException, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func, extract
from typing import Optional
import json, os, math
from datetime import datetime

from app.core.database import get_db, engine
from app.core.config import settings
from app.models import (
    Base, Parametre, Etablissement, ZoteroSource,
    Document, SyncLog, Utilisateur, NumerotationCompteur, AccesException
)

# Créer les tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="ScholarSync", docs_url="/api/docs")

# Fichiers statiques
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Templates
templates = Jinja2Templates(directory="app/templates")

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
templates.env.filters["truncate"] = truncate
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

def get_facettes(db: Session, filters: dict = {}) -> dict:
    q = db.query(Document)
    for key in ["type", "statut", "langue", "annee"]:
        if filters.get(key):
            q = q.filter(getattr(Document, key) == filters[key])
    if filters.get("etablissement"):
        q = q.filter(Document.etablissement_code == filters["etablissement"])
    if filters.get("domaine"):
        q = q.filter(Document.domaine == filters["domaine"])

    etabs = db.query(Document.etablissement_code, func.count().label("count"))\
              .group_by(Document.etablissement_code).all()
    types = {r[0]: r[1] for r in db.query(Document.type, func.count()).group_by(Document.type).all()}
    statuts = {r[0]: r[1] for r in db.query(Document.statut, func.count()).group_by(Document.statut).all()}
    domaines = db.query(Document.domaine, func.count().label("count"))\
                 .filter(Document.domaine.isnot(None))\
                 .group_by(Document.domaine).order_by(func.count().desc()).all()
    annees = db.query(Document.annee, func.count().label("count"))\
               .group_by(Document.annee).order_by(Document.annee.desc()).all()
    langues = db.query(Document.langue, func.count().label("count"))\
                .filter(Document.langue.isnot(None))\
                .group_by(Document.langue).order_by(func.count().desc()).all()

    sous_entites = []
    if filters.get("etablissement"):
        sous_entites = db.query(Document.sous_entite_nom, func.count().label("count"))\
                         .filter(Document.etablissement_code == filters["etablissement"])\
                         .filter(Document.sous_entite_nom.isnot(None))\
                         .group_by(Document.sous_entite_nom)\
                         .order_by(func.count().desc()).all()

    return {
        "etablissements": [{"code": r[0], "count": r[1]} for r in etabs],
        "types": types,
        "statuts": statuts,
        "domaines": [{"nom": r[0], "count": r[1]} for r in domaines],
        "annees": [{"valeur": r[0], "count": r[1]} for r in annees],
        "langues": [{"nom": r[0], "count": r[1]} for r in langues],
        "sous_entites": [{"nom": r[0], "count": r[1]} for r in sous_entites],
    }

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

# ─── ROUTES PUBLIQUES ─────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, db: Session = Depends(get_db)):
    params = get_params_with_defaults(db)
    stats = get_stats(db)
    facettes = get_facettes(db)
    docs, pagination = paginate(
        db.query(Document).order_by(Document.created_at.desc()), 1
    )
    annees_recentes = [r[0] for r in db.query(Document.annee).distinct().order_by(Document.annee.desc()).limit(4).all()]
    return templates.TemplateResponse("public/index.html", {
        "request": request, "params": params, "stats": stats,
        "facettes": facettes, "documents": docs, "pagination": pagination,
        "current_filters": {}, "sort": "recent", "query_string": "",
        "annees_recentes": annees_recentes, "active_nav": "accueil", "lang": "fr",
    })

@app.get("/recherche", response_class=HTMLResponse)
async def recherche(
    request: Request, db: Session = Depends(get_db),
    q: str = "", type: str = "", statut: str = "", etablissement: str = "",
    domaine: str = "", annee: str = "", langue: str = "", sous_entite: str = "",
    sort: str = "recent", page: int = 1
):
    params = get_params_with_defaults(db)
    filters = {k: v for k, v in {
        "q": q, "type": type, "statut": statut, "etablissement": etablissement,
        "domaine": domaine, "annee": annee, "langue": langue, "sous_entite": sous_entite,
    }.items() if v}

    query = db.query(Document)
    if q:
        query = query.filter(
            Document.titre.ilike(f"%{q}%") |
            Document.auteur.ilike(f"%{q}%") |
            Document.numero_national.ilike(f"%{q}%") |
            Document.resume.ilike(f"%{q}%")
        )
    if type:  query = query.filter(Document.type == type)
    if statut: query = query.filter(Document.statut == statut)
    if etablissement: query = query.filter(Document.etablissement_code == etablissement)
    if domaine: query = query.filter(Document.domaine == domaine)
    if annee: query = query.filter(Document.annee == int(annee))
    if langue: query = query.filter(Document.langue == langue)
    if sous_entite: query = query.filter(Document.sous_entite_nom == sous_entite)

    if sort == "ancien": query = query.order_by(Document.annee.asc())
    elif sort == "titre": query = query.order_by(Document.titre.asc())
    else: query = query.order_by(Document.created_at.desc())

    docs, pagination = paginate(query, page)
    facettes = get_facettes(db, filters)
    stats = get_stats(db)

    qs_parts = [f"{k}={v}" for k, v in filters.items() if k != "page"] + [f"sort={sort}"]
    query_string = "&".join(qs_parts)

    return templates.TemplateResponse("public/index.html", {
        "request": request, "params": params, "stats": stats,
        "facettes": facettes, "documents": docs, "pagination": pagination,
        "current_filters": filters, "sort": sort, "query_string": query_string,
        "annees_recentes": [], "active_nav": "accueil", "lang": "fr",
    })

@app.get("/theses", response_class=HTMLResponse)
async def theses(request: Request, db: Session = Depends(get_db), page: int = 1):
    return RedirectResponse(f"/recherche?type=these&page={page}")

@app.get("/memoires", response_class=HTMLResponse)
async def memoires(request: Request, db: Session = Depends(get_db), page: int = 1):
    return RedirectResponse(f"/recherche?type=memoire&page={page}")

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
        "current_user": get_current_user_mock(),
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
        "current_user": get_current_user_mock(), "active_nav": "zotero",
    })

@app.post("/admin/zotero/ajouter")
async def admin_zotero_ajouter(
    request: Request, db: Session = Depends(get_db),
    etablissement_id: int = Form(...), zotero_type: str = Form(...),
    zotero_id: str = Form(...), api_key: str = Form(...), label: str = Form("")
):
    source = ZoteroSource(
        etablissement_id=etablissement_id, zotero_type=zotero_type,
        zotero_id=zotero_id, api_key=api_key, label=label or None
    )
    db.add(source)
    db.commit()
    return RedirectResponse("/admin/zotero", status_code=303)

@app.post("/admin/zotero/{source_id}/toggle")
async def admin_zotero_toggle(source_id: int, db: Session = Depends(get_db)):
    src = db.query(ZoteroSource).filter(ZoteroSource.id == source_id).first()
    if src:
        src.actif = not src.actif
        db.commit()
    return RedirectResponse("/admin/zotero", status_code=303)

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
    import asyncio
    asyncio.create_task(sync_all(db))
    return RedirectResponse("/admin/sync", status_code=303)

@app.get("/admin/parametres/identite", response_class=HTMLResponse)
async def admin_parametres_identite(request: Request, db: Session = Depends(get_db)):
    params = get_params_with_defaults(db)
    return templates.TemplateResponse("admin/parametres_identite.html", {
        "request": request, "params": params,
        "current_user": get_current_user_mock(), "active_nav": "identite",
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
        os.makedirs(f"app/{settings.UPLOAD_DIR}", exist_ok=True)
        ext = logo.filename.rsplit(".", 1)[-1]
        path = f"app/{settings.UPLOAD_DIR}/logo.{ext}"
        content = await logo.read()
        with open(path, "wb") as f:
            f.write(content)
        updates["logo_url"] = f"/static/img/uploads/logo.{ext}"

    for cle, valeur in updates.items():
        row = db.query(Parametre).filter(Parametre.cle == cle).first()
        if row:
            row.valeur = valeur
        else:
            db.add(Parametre(cle=cle, valeur=valeur, type="text"))
    db.commit()
    return RedirectResponse("/admin/parametres/identite", status_code=303)

@app.post("/admin/parametres/sync-intervalle")
async def admin_sync_intervalle(minutes: int = Form(60), db: Session = Depends(get_db)):
    row = db.query(Parametre).filter(Parametre.cle == "sync_intervalle_min").first()
    if row:
        row.valeur = str(minutes)
    else:
        db.add(Parametre(cle="sync_intervalle_min", valeur=str(minutes), type="text"))
    db.commit()
    return RedirectResponse("/admin/zotero", status_code=303)

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
        "current_user": get_current_user_mock(), "active_nav": "documents",
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
        "current_user": get_current_user_mock(), "active_nav": "etablissements",
    })

@app.post("/admin/etablissements/ajouter")
async def admin_etablissements_ajouter(
    db: Session = Depends(get_db),
    code: str = Form(...), nom: str = Form(...), nom_court: str = Form(""),
    pays: str = Form("Sénégal"), ville: str = Form(""), url_site: str = Form("")
):
    etab = Etablissement(code=code.upper(), nom=nom, nom_court=nom_court or None,
                         pays=pays, ville=ville or None, url_site=url_site or None)
    db.add(etab)
    db.commit()
    return RedirectResponse("/admin/etablissements", status_code=303)

@app.post("/admin/etablissements/{etab_id}/toggle")
async def admin_etablissements_toggle(etab_id: int, db: Session = Depends(get_db)):
    etab = db.query(Etablissement).filter(Etablissement.id == etab_id).first()
    if etab:
        etab.actif = not etab.actif
        db.commit()
    return RedirectResponse("/admin/etablissements", status_code=303)

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
        "current_user": get_current_user_mock(), "active_nav": "sync",
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
        "current_user": get_current_user_mock(), "active_nav": "sync-logs",
    })

@app.get("/admin/utilisateurs", response_class=HTMLResponse)
async def admin_utilisateurs(request: Request, db: Session = Depends(get_db)):
    params = get_params_with_defaults(db)
    users = db.query(Utilisateur).order_by(Utilisateur.created_at.desc()).all()
    etabs = db.query(Etablissement).filter(Etablissement.actif == True).all()
    return templates.TemplateResponse("admin/utilisateurs.html", {
        "request": request, "params": params, "utilisateurs": users, "etablissements": etabs,
        "current_user": get_current_user_mock(), "active_nav": "utilisateurs",
    })

@app.get("/admin/acces", response_class=HTMLResponse)
async def admin_acces(request: Request, db: Session = Depends(get_db)):
    params = get_params_with_defaults(db)
    exceptions = db.query(AccesException).order_by(AccesException.created_at.desc()).all()
    etabs = db.query(Etablissement).filter(Etablissement.actif == True).all()
    return templates.TemplateResponse("admin/acces.html", {
        "request": request, "params": params, "exceptions": exceptions, "etablissements": etabs,
        "current_user": get_current_user_mock(), "active_nav": "acces",
    })

@app.get("/admin/exports", response_class=HTMLResponse)
async def admin_exports(request: Request, db: Session = Depends(get_db)):
    params = get_params_with_defaults(db)
    etabs = db.query(Etablissement).filter(Etablissement.actif == True).all()
    return templates.TemplateResponse("admin/exports.html", {
        "request": request, "params": params, "etablissements": etabs,
        "current_user": get_current_user_mock(), "active_nav": "exports",
    })

@app.get("/admin/parametres/contenu", response_class=HTMLResponse)
async def admin_parametres_contenu(request: Request, db: Session = Depends(get_db)):
    params = get_params_with_defaults(db)
    return templates.TemplateResponse("admin/parametres_contenu.html", {
        "request": request, "params": params,
        "current_user": get_current_user_mock(), "active_nav": "contenu",
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
    return RedirectResponse("/admin/parametres/contenu", status_code=303)

@app.get("/admin/parametres/partenaires", response_class=HTMLResponse)
async def admin_parametres_partenaires(request: Request, db: Session = Depends(get_db)):
    params = get_params_with_defaults(db)
    return templates.TemplateResponse("admin/parametres_partenaires.html", {
        "request": request, "params": params,
        "current_user": get_current_user_mock(), "active_nav": "partenaires",
    })

@app.get("/admin/parametres/general", response_class=HTMLResponse)
async def admin_parametres_general(request: Request, db: Session = Depends(get_db)):
    params = get_params_with_defaults(db)
    return templates.TemplateResponse("admin/parametres_general.html", {
        "request": request, "params": params,
        "current_user": get_current_user_mock(), "active_nav": "general",
    })

@app.post("/admin/documents/{doc_id}/toggle-acces")
async def admin_document_toggle_acces(doc_id: str, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if doc:
        doc.acces = "restreint" if doc.acces == "public" else "public"
        db.commit()
    return RedirectResponse("/admin/documents", status_code=303)

@app.post("/admin/zotero/{source_id}/supprimer")
async def admin_zotero_supprimer(source_id: int, db: Session = Depends(get_db)):
    src = db.query(ZoteroSource).filter(ZoteroSource.id == source_id).first()
    if src:
        db.delete(src)
        db.commit()
    return RedirectResponse("/admin/zotero", status_code=303)

@app.post("/admin/sync/lancer/{source_id}")
async def admin_sync_lancer_source(source_id: int, db: Session = Depends(get_db)):
    from app.sync.engine import sync_source
    import asyncio
    src = db.query(ZoteroSource).filter(ZoteroSource.id == source_id).first()
    if src:
        asyncio.create_task(sync_source(db, src))
    return RedirectResponse("/admin/sync", status_code=303)

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
    return RedirectResponse("/admin/acces", status_code=303)

@app.post("/admin/acces/{ex_id}/supprimer")
async def admin_acces_supprimer(ex_id: int, db: Session = Depends(get_db)):
    ex = db.query(AccesException).filter(AccesException.id == ex_id).first()
    if ex:
        db.delete(ex)
        db.commit()
    return RedirectResponse("/admin/acces", status_code=303)

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
    return RedirectResponse("/admin/parametres/partenaires", status_code=303)

@app.post("/admin/parametres/partenaires/supprimer")
async def admin_partenaires_supprimer(db: Session = Depends(get_db), nom: str = Form(...)):
    import json
    row = db.query(Parametre).filter(Parametre.cle == "partenaires_json").first()
    if row:
        partenaires = json.loads(row.valeur)
        partenaires = [p for p in partenaires if p["nom"] != nom]
        row.valeur = json.dumps(partenaires)
        db.commit()
    return RedirectResponse("/admin/parametres/partenaires", status_code=303)

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
    return RedirectResponse("/admin/parametres/general", status_code=303)
