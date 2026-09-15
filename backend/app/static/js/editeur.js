/* Éditeur de texte riche pour la page « À propos ».
 *
 * Écrit sans bibliothèque : l'outil doit rester utilisable sur une
 * connexion qui ne joint aucun CDN, et une dépendance de plus serait une
 * police de plus à neutraliser — l'interface n'en emploie qu'une.
 *
 * Ce que le navigateur produit n'est jamais tenu pour sûr : le serveur
 * repasse le contenu par une liste blanche à l'enregistrement
 * (app/core/html_riche.py). Ce fichier soigne le confort de saisie, pas
 * la sécurité.
 */
(function () {
  'use strict';

  var SS = window.ScholarSync || {};

  var OUTILS = [
    { groupe: [
      { cmd: 'formatBlock', val: '<h2>', icone: 'h-2', titre: 'Titre de section' },
      { cmd: 'formatBlock', val: '<h3>', icone: 'h-3', titre: 'Sous-titre' },
      { cmd: 'formatBlock', val: '<p>',  icone: 'pilcrow', titre: 'Paragraphe' }
    ]},
    { groupe: [
      { cmd: 'bold',      icone: 'bold',      titre: 'Gras (Ctrl+B)' },
      { cmd: 'italic',    icone: 'italic',    titre: 'Italique (Ctrl+I)' },
      { cmd: 'underline', icone: 'underline', titre: 'Souligné (Ctrl+U)' }
    ]},
    { groupe: [
      { cmd: 'insertUnorderedList', icone: 'list',         titre: 'Liste à puces' },
      { cmd: 'insertOrderedList',   icone: 'list-numbers', titre: 'Liste numérotée' },
      { cmd: 'formatBlock', val: '<blockquote>', icone: 'quote', titre: 'Citation' }
    ]},
    { groupe: [
      { action: 'lien',    icone: 'link',          titre: 'Insérer un lien' },
      { cmd: 'unlink',     icone: 'unlink',        titre: 'Retirer le lien' },
      { action: 'ligne',   icone: 'separator',     titre: 'Trait de séparation' },
      { cmd: 'removeFormat', icone: 'clear-formatting', titre: 'Effacer la mise en forme' }
    ]},
    { groupe: [
      { cmd: 'undo', icone: 'arrow-back-up',    titre: 'Annuler (Ctrl+Z)' },
      { cmd: 'redo', icone: 'arrow-forward-up', titre: 'Rétablir (Ctrl+Y)' },
      { action: 'source', icone: 'code', titre: 'Afficher le code HTML' }
    ]}
  ];

  function bouton(o) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'editeur__outil';
    b.title = o.titre;
    // `title` ne suffit pas : un lecteur d'écran annoncerait un bouton
    // sans nom si l'icône est purement décorative.
    b.setAttribute('aria-label', o.titre);
    if (o.cmd) { b.dataset.cmd = o.cmd; if (o.val) b.dataset.val = o.val; }
    if (o.action) b.dataset.action = o.action;
    b.innerHTML = '<i class="ti ti-' + o.icone + '" aria-hidden="true"></i>';
    return b;
  }

  function monter(zone) {
    var champ = document.getElementById(zone.dataset.editeurPour);
    if (!champ) return;

    var barre = document.createElement('div');
    barre.className = 'editeur__barre';
    barre.setAttribute('role', 'toolbar');
    barre.setAttribute('aria-label', 'Mise en forme');

    OUTILS.forEach(function (g, i) {
      if (i) {
        var sep = document.createElement('span');
        sep.className = 'editeur__sep';
        sep.setAttribute('aria-hidden', 'true');
        barre.appendChild(sep);
      }
      g.groupe.forEach(function (o) { barre.appendChild(bouton(o)); });
    });

    var surface = document.createElement('div');
    // La même classe que la page publique : ce qu'on voit en écrivant
    // est ce que verra le lecteur.
    surface.className = 'editeur__surface contenu-riche';
    surface.contentEditable = 'true';
    surface.setAttribute('role', 'textbox');
    surface.setAttribute('aria-multiline', 'true');
    surface.setAttribute('aria-label', champ.dataset.editeurLibelle || 'Contenu');
    surface.innerHTML = champ.value || '<p></p>';

    // Le champ réel garde la valeur envoyée : la surface éditable n'a pas
    // de name et ne part jamais seule.
    champ.hidden = true;

    zone.appendChild(barre);
    zone.appendChild(surface);

    var enSource = false;

    function synchroniser() {
      if (!enSource) champ.value = surface.innerHTML;
    }

    // ── Exécution des commandes ────────────────────────────────
    barre.addEventListener('mousedown', function (e) {
      // Empêche la barre de voler le focus : sans cela, la sélection
      // dans le texte est perdue avant que la commande s'applique.
      if (e.target.closest('.editeur__outil')) e.preventDefault();
    });

    barre.addEventListener('click', function (e) {
      var b = e.target.closest('.editeur__outil');
      if (!b) return;
      surface.focus();

      if (b.dataset.action === 'lien')   return insererLien();
      if (b.dataset.action === 'ligne')  { document.execCommand('insertHorizontalRule'); synchroniser(); return; }
      if (b.dataset.action === 'source') return basculerSource(b);

      try {
        document.execCommand(b.dataset.cmd, false, b.dataset.val || null);
      } catch (err) { /* commande non supportée : sans effet */ }
      synchroniser();
      rafraichirEtats();
    });

    function insererLien() {
      var url = window.prompt('Adresse du lien (https://…)', 'https://');
      if (!url) return;
      // Le serveur refusera de toute façon les schémas exotiques ; le
      // dire tout de suite évite d'enregistrer un lien qui disparaîtra.
      if (!/^(https?:|mailto:|tel:|\/|#)/i.test(url.trim())) {
        if (SS.toast) SS.toast('Adresse refusée : utilisez https://, mailto: ou un chemin interne.', 'warning');
        return;
      }
      document.execCommand('createLink', false, url.trim());
      synchroniser();
    }

    function basculerSource(b) {
      enSource = !enSource;
      b.classList.toggle('is-actif', enSource);
      if (enSource) {
        champ.value = surface.innerHTML;
        champ.hidden = false;
        champ.style.minHeight = surface.offsetHeight + 'px';
        surface.hidden = true;
        barre.querySelectorAll('.editeur__outil').forEach(function (x) {
          if (x !== b) x.disabled = true;
        });
        champ.focus();
      } else {
        surface.innerHTML = champ.value;
        champ.hidden = true;
        surface.hidden = false;
        barre.querySelectorAll('.editeur__outil').forEach(function (x) { x.disabled = false; });
        surface.focus();
      }
    }

    // ── Collage ────────────────────────────────────────────────
    // Un collage depuis Word arrive chargé de balises <o:p>, de styles
    // en ligne et de polices imposées. Tout cela serait retiré à
    // l'enregistrement : autant ne pas laisser croire que c'est gardé.
    surface.addEventListener('paste', function (e) {
      e.preventDefault();
      var texte = (e.clipboardData || window.clipboardData).getData('text/plain');
      document.execCommand('insertText', false, texte);
      synchroniser();
      if (SS.toast && texte.length > 40) {
        SS.toast('Collé en texte simple — la mise en forme se réapplique avec la barre d’outils.', 'info');
      }
    });

    // ── États des boutons ──────────────────────────────────────
    function rafraichirEtats() {
      if (enSource) return;
      barre.querySelectorAll('[data-cmd]').forEach(function (b) {
        var actif = false;
        try { actif = document.queryCommandState(b.dataset.cmd); }
        catch (err) { actif = false; }
        b.classList.toggle('is-actif', !!actif);
        b.setAttribute('aria-pressed', actif ? 'true' : 'false');
      });
    }

    surface.addEventListener('input', synchroniser);
    surface.addEventListener('keyup', rafraichirEtats);
    surface.addEventListener('mouseup', rafraichirEtats);
    document.addEventListener('selectionchange', function () {
      if (document.activeElement === surface) rafraichirEtats();
    });

    // Filet de sécurité : si un chemin d'édition échappait aux
    // écouteurs, la soumission repart de l'état réel de la surface.
    var form = champ.form || zone.closest('form');
    if (form) form.addEventListener('submit', synchroniser);

    rafraichirEtats();
  }

  function init() {
    document.querySelectorAll('[data-editeur-pour]').forEach(monter);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
