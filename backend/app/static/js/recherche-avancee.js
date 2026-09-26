/* ScholarSync — recherche avancée : bascule simple / avancée, ajout et
   retrait de critères. Les deux formulaires fonctionnent sans ce script
   (lignes rendues par le serveur) ; il n'ajoute que le confort. */
(function () {
  'use strict';

  var bloc = document.getElementById('recherche');
  var form = document.getElementById('rechercheAvancee');
  if (!bloc || !form) return;

  var lignes = document.getElementById('raLignes');
  var modele = document.getElementById('raModele');
  var MAX = 10;
  var compteur = lignes.querySelectorAll('.ra-ligne').length;

  function basculer(mode) {
    bloc.setAttribute('data-mode', mode);
    var cible = mode === 'avancee'
      ? form.querySelector('.ra-terme')
      : bloc.querySelector('.search-box input');
    if (cible) cible.focus();
  }

  function majBoutons() {
    var n = lignes.querySelectorAll('.ra-ligne').length;
    var ajouter = form.querySelector('[data-ra-ajouter]');
    if (ajouter) ajouter.hidden = n >= MAX;
  }

  function ajouterLigne(champ) {
    if (lignes.querySelectorAll('.ra-ligne').length >= MAX) return null;
    compteur += 1;
    var html = modele.innerHTML.replace(/ra-(champ|terme)-N/g, 'ra-$1-' + compteur);
    var tmp = document.createElement('div');
    tmp.innerHTML = html.trim();
    var ligne = tmp.firstElementChild;
    if (champ) ligne.querySelector('.ra-champ').value = champ;
    lignes.appendChild(ligne);
    majBoutons();
    return ligne;
  }

  bloc.addEventListener('click', function (e) {
    var bascule = e.target.closest('[data-bascule-recherche]');
    if (bascule) {
      e.preventDefault();
      basculer(bascule.getAttribute('data-bascule-recherche'));
      return;
    }
    if (e.target.closest('[data-ra-ajouter]')) {
      var ligne = ajouterLigne('tout');
      if (ligne) ligne.querySelector('.ra-terme').focus();
      return;
    }
    var retirer = e.target.closest('[data-ra-retirer]');
    if (retirer) {
      var l = retirer.closest('.ra-ligne');
      var suivante = l.nextElementSibling || l.previousElementSibling;
      l.remove();
      // Jamais zéro ligne : le formulaire deviendrait inutilisable
      if (!lignes.querySelector('.ra-ligne')) suivante = ajouterLigne('tout');
      if (suivante) suivante.querySelector('.ra-terme').focus();
      majBoutons();
      return;
    }
    if (e.target.closest('[data-ra-vider]')) {
      lignes.querySelectorAll('.ra-terme').forEach(function (i) { i.value = ''; });
      var et = form.querySelector('input[name="op"][value="et"]');
      if (et) et.checked = true;
      var premier = lignes.querySelector('.ra-terme');
      if (premier) premier.focus();
    }
  });

  // Une ligne vide n'a rien à faire dans l'adresse de la recherche
  form.addEventListener('submit', function () {
    lignes.querySelectorAll('.ra-ligne').forEach(function (l) {
      var terme = l.querySelector('.ra-terme');
      if (!terme.value.trim()) {
        terme.disabled = true;
        l.querySelector('.ra-champ').disabled = true;
      }
    });
    var et = form.querySelector('input[name="op"][value="et"]');
    if (et && et.checked) et.disabled = true;   // ET est la valeur par défaut
  });

  // Retour arrière du navigateur : réactiver ce que la soumission a désactivé
  window.addEventListener('pageshow', function () {
    form.querySelectorAll(':disabled').forEach(function (el) { el.disabled = false; });
  });

  majBoutons();
})();
