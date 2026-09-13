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
    if (!e.target.matches('#catalogue .sort-select, .sort-select')) return;
    var url = new URL(window.location.href);
    url.searchParams.set('sort', e.target.value);
    url.searchParams.delete('page');
    charger(url.pathname + url.search, true);
  });

  // Boutons Précédent / Suivant du navigateur
  window.addEventListener('popstate', function () {
    charger(window.location.pathname + window.location.search, false);
  });

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
