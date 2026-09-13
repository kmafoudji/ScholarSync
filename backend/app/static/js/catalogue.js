/* ScholarSync — filtrage du catalogue sans rechargement.
   Les facettes sont des liens ordinaires : sans ce script, tout continue
   de fonctionner, simplement avec un rechargement complet. */
(function () {
  'use strict';

  var catalogue = document.getElementById('catalogue');
  if (!catalogue || !window.fetch || !window.history || !history.pushState) return;

  var enCours = null;   // AbortController de la requête courante

  function marquerOccupe(occupe) {
    catalogue.classList.toggle('is-loading', occupe);
    catalogue.setAttribute('aria-busy', occupe ? 'true' : 'false');
  }

  function annoncer(texte) {
    var region = document.getElementById('annonce-resultats');
    if (region) region.textContent = texte;
  }

  function charger(url, ajouterHistorique) {
    // Une requête plus récente rend la précédente caduque : sans cette
    // annulation, deux clics rapprochés peuvent s'afficher dans le
    // désordre et montrer le résultat du premier.
    if (enCours) enCours.abort();
    var controleur = new AbortController();
    enCours = controleur;

    marquerOccupe(true);

    fetch(url, {
      headers: { 'X-Requested-With': 'facettes' },
      signal: controleur.signal
    })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.text();
      })
      .then(function (html) {
        catalogue.innerHTML = html;
        if (ajouterHistorique) history.pushState({ url: url }, '', url);

        refleterEtatFiltres();
        appliquerEtatsMemorises();
        var compte = catalogue.querySelector('.results-count');
        annoncer(compte ? compte.textContent.trim() : 'Résultats mis à jour.');

        // Ramener la vue en haut des résultats, sans sauter brutalement
        var haut = catalogue.getBoundingClientRect().top + window.scrollY - 16;
        if (window.scrollY > haut) {
          window.scrollTo({ top: haut, behavior: 'smooth' });
        }
      })
      .catch(function (e) {
        if (e.name === 'AbortError') return;   // remplacée, pas échouée
        // Repli : on laisse le navigateur faire la navigation classique
        window.location.href = url;
      })
      .then(function () {
        if (enCours === controleur) {
          enCours = null;
          marquerOccupe(false);
        }
      });
  }

  // Délégation : le contenu est remplacé à chaque filtrage, donc écouter
  // sur le conteneur plutôt que sur chaque lien.
  document.addEventListener('click', function (e) {
    var lien = e.target.closest(
      '#catalogue .facette-item, #catalogue .page-btn, ' +
      '#catalogue .facettes__reset, .search-filters .filter-chip'
    );
    if (!lien || !lien.getAttribute('href')) return;
    // Laisser passer ouverture dans un nouvel onglet, téléchargement, etc.
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;

    e.preventDefault();
    charger(lien.getAttribute('href'), true);
  });

  // Tri
  document.addEventListener('change', function (e) {
    if (!e.target.matches('.sort-select')) return;
    var url = new URL(window.location.href);
    var nom = e.target.name || 'sort';
    url.searchParams.set(nom, e.target.value);
    // Changer de tri ou de volume invalide la page courante : rester
    // page 7 après être passé à 100 par page mènerait dans le vide.
    url.searchParams.delete('page');
    // C'est /recherche qui porte les paramètres du catalogue ; l'accueil
    // n'en accepte aucun. Sans cette bascule, le réglage partait dans
    // l'URL de l'accueil et restait sans effet — exactement comme les
    // liens de facettes, qui pointent déjà vers /recherche.
    charger('/recherche' + url.search, true);
  });

  // Boutons Précédent / Suivant du navigateur
  window.addEventListener('popstate', function () {
    charger(window.location.pathname + window.location.search, false);
  });

  /* ── Facettes : plier, déplier, chercher ──────────────────────
     L'état plié/déplié est mémorisé par groupe : sur un catalogue à sept
     facettes, avoir à replier les mêmes à chaque recherche est pénible.
     localStorage peut être indisponible (navigation privée, site data
     bloqué) — tout est enveloppé, et l'absence de mémoire ne retire
     aucune fonction. */

  var CLE_MEMOIRE = 'scholarsync.facettes.replies';

  function groupesReplies() {
    try {
      return JSON.parse(localStorage.getItem(CLE_MEMOIRE) || '[]');
    } catch (e) { return []; }
  }

  function memoriser(liste) {
    try { localStorage.setItem(CLE_MEMOIRE, JSON.stringify(liste)); }
    catch (e) { /* sans mémoire, l'état vaut pour la session seulement */ }
  }

  function appliquerEtatsMemorises() {
    var replies = groupesReplies();
    document.querySelectorAll('.facette-groupe').forEach(function (g) {
      if (replies.indexOf(g.dataset.groupe) === -1) return;
      g.classList.add('est-replie');
      var b = g.querySelector('.facette-bascule');
      if (b) b.setAttribute('aria-expanded', 'false');
    });
  }

  function basculerGroupe(bouton) {
    var groupe = bouton.closest('.facette-groupe');
    if (!groupe) return;
    var replie = groupe.classList.toggle('est-replie');
    bouton.setAttribute('aria-expanded', replie ? 'false' : 'true');

    var liste = groupesReplies();
    var i = liste.indexOf(groupe.dataset.groupe);
    if (replie && i === -1) liste.push(groupe.dataset.groupe);
    if (!replie && i !== -1) liste.splice(i, 1);
    memoriser(liste);
  }

  /* Recherche dans les valeurs d'une facette. Filtre ce qui est déjà
     affiché : le serveur a renvoyé toutes les valeurs du groupe, il n'y
     a donc aucun aller-retour à faire. */
  function chercherDansGroupe(champ) {
    var cle = champ.getAttribute('data-chercher-dans');
    var groupe = champ.closest('.facette-groupe');
    var terme = champ.value.trim().toLowerCase();
    var visibles = 0;

    groupe.querySelectorAll('.facette-item').forEach(function (a) {
      var correspond = !terme || (a.dataset.valeur || '').indexOf(terme) !== -1;
      // Une recherche en cours montre toutes les correspondances, y
      // compris au-delà du seuil d'affichage initial.
      a.hidden = !correspond;
      if (correspond) visibles++;
    });

    var plus = groupe.querySelector('.facette-plus');
    if (plus) plus.hidden = !!terme || groupe.classList.contains('est-deplie');
    var aucun = groupe.querySelector('.facette-aucun');
    if (aucun) aucun.hidden = visibles > 0;

    if (!terme) reduireGroupe(groupe);
  }

  /* Sans terme de recherche, on revient aux N premières valeurs, en
     gardant visibles celles qui sont retenues. */
  function reduireGroupe(groupe) {
    if (groupe.classList.contains('est-deplie')) return;
    var plus = groupe.querySelector('.facette-plus');
    if (!plus) return;
    var seuil = groupe.querySelectorAll('.facette-item').length
              - parseInt(plus.dataset.restant, 10);
    groupe.querySelectorAll('.facette-item').forEach(function (a, i) {
      a.hidden = i >= seuil && !a.classList.contains('active');
    });
    plus.hidden = false;
  }

  function toutAfficher(bouton) {
    var groupe = bouton.closest('.facette-groupe');
    groupe.classList.add('est-deplie');
    groupe.querySelectorAll('.facette-item').forEach(function (a) { a.hidden = false; });
    bouton.hidden = true;
  }

  document.addEventListener('click', function (e) {
    var bascule = e.target.closest('.facette-bascule');
    if (bascule) { basculerGroupe(bascule); return; }
    var plus = e.target.closest('.facette-plus');
    if (plus) { toutAfficher(plus); return; }
  });

  document.addEventListener('input', function (e) {
    if (e.target.matches('[data-chercher-dans]')) chercherDansGroupe(e.target);
  });

  // Échap vide le champ de recherche d'une facette
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && e.target.matches('[data-chercher-dans]')) {
      e.target.value = '';
      chercherDansGroupe(e.target);
    }
  });

  appliquerEtatsMemorises();

  /* Repli des facettes sur petit écran. L'état vit sur #catalogue, qui
     survit au remplacement du contenu — le bouton, lui, est recréé à
     chaque filtrage, d'où la délégation. */
  document.addEventListener('click', function (e) {
    if (!e.target.closest('#filtresBascule')) return;
    var ouvert = catalogue.classList.toggle('filtres-ouverts');
    var bouton = document.getElementById('filtresBascule');
    if (bouton) bouton.setAttribute('aria-expanded', ouvert ? 'true' : 'false');
  });

  // Après un filtrage, refléter l'état sur le bouton fraîchement rendu
  function refleterEtatFiltres() {
    var bouton = document.getElementById('filtresBascule');
    if (bouton) {
      bouton.setAttribute(
        'aria-expanded',
        catalogue.classList.contains('filtres-ouverts') ? 'true' : 'false'
      );
    }
  }

  // Zone d'annonce pour les lecteurs d'écran : sans elle, un changement
  // de résultats sans rechargement passe totalement inaperçu.
  if (!document.getElementById('annonce-resultats')) {
    var region = document.createElement('div');
    region.id = 'annonce-resultats';
    region.className = 'sr-only';
    region.setAttribute('aria-live', 'polite');
    document.body.appendChild(region);
  }
})();
