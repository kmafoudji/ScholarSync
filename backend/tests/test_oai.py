"""Moisson OAI-PMH : protocole et correspondance Dublin Core (app/sync/oai.py).

Un entrepôt simulé (httpx.MockTransport) répond comme Koha ou DSpace :
Identify, ListSets, ListRecords en deux pages, notices supprimées.

    cd backend && python -m pytest tests/
"""
import os
import sys
import xml.etree.ElementTree as ET

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.sync import oai  # noqa: E402

TETE = ('<?xml version="1.0"?><OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/" '
        'xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">')
FIN = "</OAI-PMH>"


def rec(ident, titre, type_, date="2021", statut=None, createur="DIOP Awa"):
    st = f' status="{statut}"' if statut else ""
    if statut == "deleted":
        return f"<record><header{st}><identifier>{ident}</identifier></header></record>"
    return (f"<record><header><identifier>{ident}</identifier><datestamp>2026-01-01</datestamp></header>"
            f"<metadata><oai_dc:dc><dc:title>{titre}</dc:title><dc:creator>{createur}</dc:creator>"
            f"<dc:contributor>FALL Moussa</dc:contributor><dc:date>{date}</dc:date>"
            f"<dc:type>{type_}</dc:type><dc:subject>eau</dc:subject><dc:subject>climat</dc:subject>"
            f"<dc:description>Résumé.</dc:description><dc:language>fre</dc:language>"
            f"<dc:identifier>https://depot.sn/{ident}</dc:identifier></oai_dc:dc></metadata></record>")


def entrepot(pages, identify=True, statut_http=200, corps=None):
    vus = []

    def gerer(request):
        p = dict(request.url.params)
        vus.append(p)
        if corps is not None:
            return httpx.Response(statut_http, content=corps)
        if p.get("verb") == "Identify":
            return httpx.Response(200, content=TETE + "<Identify><repositoryName>BU test</repositoryName>"
                                  "<granularity>YYYY-MM-DD</granularity><deletedRecord>persistent</deletedRecord>"
                                  "</Identify>" + FIN)
        if p.get("verb") == "ListSets":
            return httpx.Response(200, content=TETE + "<ListSets><set><setSpec>theses</setSpec>"
                                  "<setName>Thèses</setName></set></ListSets>" + FIN)
        page = 1 if p.get("resumptionToken") == "p2" else 0
        jeton = "<resumptionToken>p2</resumptionToken>" if page == 0 and len(pages) > 1 else "<resumptionToken/>"
        return httpx.Response(200, content=TETE + "<ListRecords>" + "".join(pages[page]) + jeton +
                              "</ListRecords>" + FIN)
    return httpx.MockTransport(gerer), vus


def test_identifier():
    t, _ = entrepot([[]])
    info = oai.identifier("https://bu.sn/oai", transport=t)
    assert info["nom"] == "BU test" and info["ensembles"][0]["spec"] == "theses"


def test_pages_et_parametres():
    t, vus = entrepot([[rec("a", "Un", "Thèse de doctorat")], [rec("b", "Deux", "Mémoire de master")]])
    with httpx.Client(transport=t) as c:
        lus = list(oai._enregistrements(c, "https://bu.sn/oai", "theses", "2026-01-01"))
    assert len(lus) == 2
    assert vus[0] == {"verb": "ListRecords", "metadataPrefix": "oai_dc", "set": "theses", "from": "2026-01-01"}
    assert vus[1] == {"verb": "ListRecords", "resumptionToken": "p2"}


def _dc(xml):
    return ET.fromstring(TETE + "<ListRecords>" + xml + "</ListRecords>" + FIN).find(
        ".//oai_dc:dc", oai.NS)


def test_correspondance_dublin_core():
    type_doc, champs = oai.notice_depuis_dc(_dc(rec("a", "Hydrologie", "info:eu-repo/semantics/doctoralThesis")))
    assert type_doc == "these" and champs["annee"] == 2021
    assert champs["directeur"] == "FALL Moussa" and champs["mots_cles"] == ["eau", "climat"]
    assert champs["url_document"] == "https://depot.sn/a" and champs["statut"] == "soutenu"
    assert oai.notice_depuis_dc(_dc(rec("b", "Riz", "masterThesis")))[0] == "memoire"


def test_notices_ecartees():
    assert oai.notice_depuis_dc(_dc(rec("c", "Un livre", "Livre"))) is None      # pas une thèse
    assert oai.notice_depuis_dc(_dc(rec("c", "Un livre", "Livre")), "these")[0] == "these"
    assert oai.notice_depuis_dc(_dc(rec("d", "Sans date", "Thèse", date="s.d."))) is None


def test_cle_stable_et_courte():
    assert oai.cle_oai("oai:koha.ucad.sn:123") == oai.cle_oai("oai:koha.ucad.sn:123")
    assert len(oai.cle_oai("x" * 500)) == 20


@pytest.mark.parametrize("corps,message", [
    (b"<html>page</html>", "pas du XML OAI"),
    (b'<!DOCTYPE x [<!ENTITY a "b">]><x/>', "DOCTYPE"),
    ((TETE + '<error code="badArgument">nope</error>' + FIN).encode(), "badArgument"),
])
def test_reponses_refusees(corps, message):
    t, _ = entrepot([[]], corps=corps)
    with pytest.raises(oai.ErreurOAI, match=message):
        oai.identifier("https://bu.sn/oai", transport=t)


def test_url_invalide():
    with pytest.raises(oai.ErreurOAI):
        oai.valider_url("ftp://bu.sn")
