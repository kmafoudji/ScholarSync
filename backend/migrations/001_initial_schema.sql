-- ScholarSync — Schéma PostgreSQL initial
-- Version : 1.0.0

-- Extensions
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================
-- TABLE : parametres
-- Personnalisation white-label de l'outil
-- ============================================================
CREATE TABLE parametres (
    cle     VARCHAR(50) PRIMARY KEY,
    valeur  TEXT,
    type    VARCHAR(20) NOT NULL DEFAULT 'text' -- text | image | color | json
);

INSERT INTO parametres (cle, valeur, type) VALUES
    ('nom_outil',          'ScholarSync',    'text'),
    ('slogan',             '',               'text'),
    ('logo_url',           '',               'image'),
    ('favicon_url',        '',               'image'),
    ('couleur_principale', '#1a3a5c',        'color'),
    ('couleur_secondaire', '#8b1a2e',        'color'),
    ('bande_decorative',   'custom',         'text'),   -- senegal | custom | none
    ('bande_couleurs',     '["#1a3a5c","rgba(255,255,255,0.5)","#8b1a2e"]', 'json'),
    ('icones_filigrane',   'academique',     'text'),   -- academique | recherche | minimal
    ('apropos_fr',         '',               'text'),
    ('apropos_en',         '',               'text'),
    ('apropos_pt',         '',               'text'),
    ('contact_json',       '{}',             'json'),
    ('partenaires_json',   '[]',             'json'),
    ('institution_nom',    '',               'text'),
    ('pied_page_texte',    '',               'text'),
    ('langues_actives',    '["fr","en","pt"]','json'),
    ('sync_intervalle_min','60',             'text');   -- minutes

-- ============================================================
-- TABLE : etablissements
-- Institutions partenaires
-- ============================================================
CREATE TABLE etablissements (
    id              SERIAL PRIMARY KEY,
    code            VARCHAR(10) UNIQUE NOT NULL,  -- UCAD, UGB, UADB...
    code_numero     VARCHAR(2) UNIQUE,            -- UC : code dans le numéro national
    nom             TEXT NOT NULL,
    nom_court       TEXT,
    pays            VARCHAR(100) DEFAULT 'Sénégal',
    ville           TEXT,
    url_site        TEXT,
    logo_url        TEXT,
    actif           BOOLEAN DEFAULT true,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- TABLE : zotero_sources
-- Comptes Zotero autorisés (1 par établissement)
-- ============================================================
CREATE TABLE zotero_sources (
    id                  SERIAL PRIMARY KEY,
    etablissement_id    INTEGER NOT NULL REFERENCES etablissements(id),
    zotero_type         VARCHAR(10) NOT NULL CHECK (zotero_type IN ('user','group')),
    zotero_id           VARCHAR(50) NOT NULL,
    api_key             TEXT NOT NULL,
    label               TEXT,                          -- nom descriptif
    actif               BOOLEAN DEFAULT true,
    derniere_sync       TIMESTAMPTZ,
    zotero_version      INTEGER DEFAULT 0,             -- cursor ?since=
    created_at          TIMESTAMPTZ DEFAULT now(),
    UNIQUE(etablissement_id)                           -- 1 source par établissement
);

-- ============================================================
-- TABLE : numerotation_compteurs
-- Compteurs par (etablissement_code, type, annee)
-- ============================================================
CREATE TABLE numerotation_compteurs (
    etablissement_code  VARCHAR(10) NOT NULL,
    type                VARCHAR(10) NOT NULL CHECK (type IN ('these','memoire')),
    annee               SMALLINT NOT NULL,
    compteur            INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (etablissement_code, type, annee)
);

-- ============================================================
-- TABLE : documents
-- Mémoires et thèses synchronisés
-- ============================================================
CREATE TABLE documents (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    numero_national     VARCHAR(20) UNIQUE,          -- attribué à la soutenance
    -- Format : SN-UC-T-S-2024-0012-47 (17 chars avec tirets)

    -- Identification principale
    titre               TEXT NOT NULL,
    auteur              TEXT NOT NULL,
    type                VARCHAR(10) NOT NULL CHECK (type IN ('these','memoire')),
    statut              VARCHAR(20) NOT NULL CHECK (statut IN ('soutenu','en_preparation')),
    langue              VARCHAR(30) DEFAULT 'français',
    annee               SMALLINT NOT NULL,
    domaine             TEXT,                          -- tag CAMES depuis Zotero

    -- Établissement et sous-entité (facettes)
    etablissement_code  VARCHAR(10) NOT NULL,
    sous_entite_nom     TEXT,                          -- nom brut depuis Zotero
    sous_entite_type    VARCHAR(20) CHECK (sous_entite_type IN ('ecole_doctorale','faculte')),

    -- Contrôle d'accès
    acces               VARCHAR(20) DEFAULT 'public' CHECK (acces IN ('public','restreint')),

    -- Métadonnées académiques
    directeur           TEXT,
    jury                JSONB,
    resume              TEXT,
    mots_cles           TEXT[],
    url_document        TEXT,                          -- lien vers dépôt université

    -- Zotero
    zotero_source_id    INTEGER REFERENCES zotero_sources(id),
    zotero_item_key     VARCHAR(20),
    zotero_version      INTEGER,

    -- Horodatage
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now(),
    synced_at           TIMESTAMPTZ,

    FOREIGN KEY (etablissement_code) REFERENCES etablissements(code)
);

-- Index pour les recherches et facettes
CREATE INDEX idx_documents_etablissement ON documents(etablissement_code);
CREATE INDEX idx_documents_type ON documents(type);
CREATE INDEX idx_documents_statut ON documents(statut);
CREATE INDEX idx_documents_annee ON documents(annee);
CREATE INDEX idx_documents_domaine ON documents(domaine);
CREATE INDEX idx_documents_langue ON documents(langue);
CREATE INDEX idx_documents_sous_entite ON documents(etablissement_code, sous_entite_nom);
CREATE INDEX idx_documents_zotero_key ON documents(zotero_item_key);

-- ============================================================
-- TABLE : acces_exceptions
-- Contrôle d'accès granulaire par collection/document
-- ============================================================
CREATE TABLE acces_exceptions (
    id                  SERIAL PRIMARY KEY,
    niveau              VARCHAR(20) NOT NULL CHECK (niveau IN ('etablissement','collection','sous_collection','document')),
    reference           TEXT NOT NULL,                 -- code, nom collection, ou UUID document
    acces               VARCHAR(20) NOT NULL CHECK (acces IN ('public','restreint')),
    created_at          TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- TABLE : sync_logs
-- Historique des synchronisations
-- ============================================================
CREATE TABLE sync_logs (
    id                  SERIAL PRIMARY KEY,
    zotero_source_id    INTEGER REFERENCES zotero_sources(id),
    declenchement       VARCHAR(20) DEFAULT 'auto' CHECK (declenchement IN ('auto','manuel')),
    statut              VARCHAR(20) NOT NULL CHECK (statut IN ('en_cours','succes','erreur')),
    documents_ajoutes   INTEGER DEFAULT 0,
    documents_modifies  INTEGER DEFAULT 0,
    documents_erreur    INTEGER DEFAULT 0,
    documents_supprimes INTEGER DEFAULT 0,
    message_erreur      TEXT,
    debut               TIMESTAMPTZ DEFAULT now(),
    fin                 TIMESTAMPTZ
);

-- ============================================================
-- TABLE : utilisateurs
-- Comptes admin et gestionnaires BU
-- ============================================================
CREATE TABLE utilisateurs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email               TEXT UNIQUE NOT NULL,
    mot_de_passe_hash   TEXT NOT NULL,
    nom                 TEXT,
    prenom              TEXT,
    role                VARCHAR(20) NOT NULL,
    etablissement_code  VARCHAR(10) REFERENCES etablissements(code),
    actif               BOOLEAN DEFAULT true,
    derniere_connexion  TIMESTAMPTZ,
    mdp_modifie_le      TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT utilisateurs_role_valide CHECK (
        role IN ('super_admin','admin_etablissement','lecteur')
    ),
    CONSTRAINT bu_needs_etablissement CHECK (
        role <> 'admin_etablissement' OR etablissement_code IS NOT NULL
    )
);

