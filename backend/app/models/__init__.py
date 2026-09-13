from app.core.database import Base
from sqlalchemy import Column, String, Text, Boolean, Integer, SmallInteger, DateTime, JSON, ARRAY, ForeignKey, CheckConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid

class Parametre(Base):
    __tablename__ = "parametres"
    cle    = Column(String(50), primary_key=True)
    valeur = Column(Text)
    type   = Column(String(20), default="text")

class Etablissement(Base):
    __tablename__ = "etablissements"
    id        = Column(Integer, primary_key=True, autoincrement=True)
    code      = Column(String(10), unique=True, nullable=False)
    nom       = Column(Text, nullable=False)
    nom_court = Column(Text)
    pays      = Column(String(100), default="Sénégal")
    ville     = Column(Text)
    url_site  = Column(Text)
    logo_url  = Column(Text)
    actif     = Column(Boolean, default=True)
    created_at= Column(DateTime(timezone=True), server_default=func.now())

    zotero_source = relationship("ZoteroSource", back_populates="etablissement", uselist=False)
    utilisateurs  = relationship("Utilisateur", back_populates="etablissement")

class ZoteroSource(Base):
    __tablename__ = "zotero_sources"
    id                = Column(Integer, primary_key=True, autoincrement=True)
    etablissement_id  = Column(Integer, ForeignKey("etablissements.id"), nullable=False, unique=True)
    zotero_type       = Column(String(10), nullable=False)
    zotero_id         = Column(String(50), nullable=False)
    api_key           = Column(Text, nullable=False)
    label             = Column(Text)
    actif             = Column(Boolean, default=True)
    derniere_sync     = Column(DateTime(timezone=True))
    zotero_version    = Column(Integer, default=0)
    created_at        = Column(DateTime(timezone=True), server_default=func.now())

    etablissement = relationship("Etablissement", back_populates="zotero_source")
    sync_logs     = relationship("SyncLog", back_populates="zotero_source")

class NumerotationCompteur(Base):
    __tablename__ = "numerotation_compteurs"
    etablissement_code = Column(String(10), primary_key=True)
    type               = Column(String(10), primary_key=True)
    annee              = Column(SmallInteger, primary_key=True)
    compteur           = Column(Integer, default=0)

class Document(Base):
    __tablename__ = "documents"
    id                 = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    numero_national    = Column(String(20), unique=True, nullable=False)
    titre              = Column(Text, nullable=False)
    auteur             = Column(Text, nullable=False)
    type               = Column(String(10), nullable=False)
    statut             = Column(String(20), nullable=False)
    langue             = Column(String(30), default="français")
    annee              = Column(SmallInteger, nullable=False)
    domaine            = Column(Text)
    etablissement_code = Column(String(10), ForeignKey("etablissements.code"), nullable=False)
    sous_entite_nom    = Column(Text)
    sous_entite_type   = Column(String(20))
    acces              = Column(String(20), default="public")
    directeur          = Column(Text)
    jury               = Column(JSON)
    resume             = Column(Text)
    mots_cles          = Column(ARRAY(Text))
    url_document       = Column(Text)
    zotero_source_id   = Column(Integer, ForeignKey("zotero_sources.id"))
    zotero_item_key    = Column(String(20))
    zotero_version     = Column(Integer)
    created_at         = Column(DateTime(timezone=True), server_default=func.now())
    updated_at         = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    synced_at          = Column(DateTime(timezone=True))

    etablissement = relationship("Etablissement")

class AccesException(Base):
    __tablename__ = "acces_exceptions"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    niveau     = Column(String(20), nullable=False)
    reference  = Column(Text, nullable=False)
    acces      = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class SyncLog(Base):
    __tablename__ = "sync_logs"
    id                  = Column(Integer, primary_key=True, autoincrement=True)
    zotero_source_id    = Column(Integer, ForeignKey("zotero_sources.id"))
    declenchement       = Column(String(20), default="auto")
    statut              = Column(String(20), nullable=False)
    documents_ajoutes   = Column(Integer, default=0)
    documents_modifies  = Column(Integer, default=0)
    documents_erreur    = Column(Integer, default=0)
    documents_total     = Column(Integer, default=0)
    documents_traites   = Column(Integer, default=0)
    message_erreur      = Column(Text)
    debut               = Column(DateTime(timezone=True), server_default=func.now())
    fin                 = Column(DateTime(timezone=True))

    zotero_source = relationship("ZoteroSource", back_populates="sync_logs")

class Utilisateur(Base):
    __tablename__ = "utilisateurs"
    id                 = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email              = Column(Text, unique=True, nullable=False)
    mot_de_passe_hash  = Column(Text, nullable=False)
    nom                = Column(Text)
    prenom             = Column(Text)
    role               = Column(String(20), nullable=False)
    etablissement_code = Column(String(10), ForeignKey("etablissements.code"))
    actif              = Column(Boolean, default=True)
    derniere_connexion = Column(DateTime(timezone=True))
    created_at         = Column(DateTime(timezone=True), server_default=func.now())

    etablissement = relationship("Etablissement", back_populates="utilisateurs")
