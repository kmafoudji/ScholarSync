/* ScholarSync — back-office.
   Ce fichier était référencé par admin/base.html sans exister : chaque page
   d'administration produisait un 404. */
(function () {
  'use strict';

  var SS = window.ScholarSync || {};
  var toast = SS.toast || function () {};

  /* ── Tiroir de navigation ─────────────────────────────────────── */

  function initSidebar() {
    var sidebar = document.getElementById('adminSidebar');
    var bouton = document.getElementById('menuToggle');
    if (!sidebar || !bouton) return;

    var scrim = null;

    function ouvrir() {
      sidebar.classList.add('open');
      bouton.setAttribute('aria-expanded', 'true');
      scrim = document.createElement('div');
      scrim.className = 'scrim';
      scrim.addEventListener('click', fermer);
      document.body.appendChild(scrim);
      requestAnimationFrame(function () { scrim.classList.add('is-visible'); });
      // Le premier lien reçoit le focus : navigation clavier cohérente
      var premier = sidebar.querySelector('a');
      if (premier) premier.focus();
    }

    function fermer() {
      sidebar.classList.remove('open');
      bouton.setAttribute('aria-expanded', 'false');
      if (scrim) {
        scrim.classList.remove('is-visible');
        var mort = scrim;
        scrim = null;
        setTimeout(function () {
          if (mort.parentNode) mort.parentNode.removeChild(mort);
        }, 200);
      }
    }

    bouton.setAttribute('aria-expanded', 'false');
    bouton.setAttribute('aria-controls', 'adminSidebar');
    bouton.addEventListener('click', function (e) {
      e.stopPropagation();
      if (sidebar.classList.contains('open')) fermer(); else ouvrir();
    });

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && sidebar.classList.contains('open')) {
        fermer();
        bouton.focus();
      }
    });

    // Repasser en grand écran doit refermer proprement le tiroir
    window.addEventListener('resize', function () {
      if (window.innerWidth > 768 && sidebar.classList.contains('open')) fermer();
    });
  }

  /* ── Tableaux : défilement horizontal plutôt que page déformée ── */

  function initTableaux() {
    document.querySelectorAll('.admin-content table').forEach(function (table) {
      if (table.closest('.table-wrap')) return;
      var wrap = document.createElement('div');
      wrap.className = 'table-wrap';
      wrap.setAttribute('tabindex', '0');       // défilement au clavier
      wrap.setAttribute('role', 'region');
      wrap.setAttribute('aria-label', 'Tableau de données, défilement horizontal');
      table.parentNode.insertBefore(wrap, table);
      wrap.appendChild(table);
    });
  }

  /* ── Confirmation avant action destructive ────────────────────── */

  function initConfirmations() {
    document.querySelectorAll('[data-confirm]').forEach(function (el) {
      var form = el.tagName === 'FORM' ? el : el.closest('form');
      if (!form) return;
      form.addEventListener('submit', function (e) {
        if (form.dataset.confirmed) return;
        e.preventDefault();
        if (window.confirm(el.getAttribute('data-confirm'))) {
          form.dataset.confirmed = '1';
          form.submit();
        } else {
          // L'utilisateur annule : rendre la main au bouton
          delete form.dataset.submitted;
          var b = form.querySelector('button[type="submit"]');
          if (SS.libererBouton) SS.libererBouton(b);
        }
      });
    });
  }

  /* ── Test de connexion (Zotero, SMTP) ─────────────────────────── */

  function initBoutonsTest() {
    document.querySelectorAll('[data-test-url]').forEach(function (bouton) {
      bouton.addEventListener('click', function (e) {
        e.preventDefault();
        if (SS.occuper) SS.occuper(bouton);

        fetch(bouton.getAttribute('data-test-url'), {
          method: bouton.getAttribute('data-test-method') || 'POST',
          headers: { 'X-Requested-With': 'fetch' }
        })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            toast(
              data.message || (data.ok ? 'Connexion réussie.' : 'Échec de la connexion.'),
              data.ok ? 'success' : 'danger'
            );
          })
          .catch(function () {
            toast('Le test n’a pas abouti : serveur injoignable.', 'danger');
          })
          .then(function () {
            if (SS.libererBouton) SS.libererBouton(bouton);
          });
      });
    });
  }

  /* ── Suivi de la synchronisation ──────────────────────────────────
     La sync tourne en tâche de fond côté serveur. Sans retour visuel,
     l'administrateur ne sait pas si quelque chose se passe. On interroge
     /admin/sync/etat et on redessine la barre d'avancement. */

  var INTERVALLE = 2000;
  var suivi = null;

  function initSuiviSync() {
    var bloc = document.getElementById('syncLive');
    if (!bloc) return;

    var barre = bloc.querySelector('.progress__bar');
    var texte = bloc.querySelector('.sync-live__text');
    var actifAuChargement = bloc.dataset.actif === '1';

    function rendre(data) {
      var log = data.log;

      if (!data.en_cours) {
        arreter();
        bloc.hidden = true;
        if (actifAuChargement && log) {
          if (log.statut === 'succes') {
            toast(
              'Synchronisation terminée : ' + log.ajoutes + ' ajout(s), ' +
              log.modifies + ' mise(s) à jour' +
              (log.supprimes ? ', ' + log.supprimes + ' retrait(s)' : '') +
              (log.erreurs ? ', ' + log.erreurs + ' erreur(s)' : '') + '.',
              log.erreurs ? 'warning' : 'success'
            );
          } else if (log.statut === 'erreur') {
            toast(
              'Synchronisation interrompue : ' +
              (log.message_erreur || 'erreur inconnue'),
              'danger'
            );
          }
          // Recharger pour afficher les documents fraîchement importés
          setTimeout(function () { window.location.reload(); }, 1200);
        }
        return;
      }

      bloc.hidden = false;
      actifAuChargement = true;
      if (!log) return;

      if (log.total > 0) {
        barre.classList.remove('progress__bar--indeterminate');
        barre.style.width = log.pourcentage + '%';
        barre.parentNode.setAttribute('aria-valuenow', String(log.pourcentage));
        texte.textContent =
          'Synchronisation de ' + log.etablissement + ' — ' +
          log.traites + ' / ' + log.total + ' documents (' +
          log.ajoutes + ' ajout(s), ' + log.modifies + ' mise(s) à jour)';
      } else {
        // Zotero n'a pas encore renvoyé le nombre total d'items
        barre.classList.add('progress__bar--indeterminate');
        texte.textContent =
          'Synchronisation de ' + log.etablissement +
          ' — récupération du catalogue Zotero…';
      }
    }

    function interroger() {
      fetch('/admin/sync/etat', { headers: { 'X-Requested-With': 'fetch' } })
        .then(function (r) { return r.json(); })
        .then(rendre)
        .catch(function () { /* coupure passagère : on retentera */ });
    }

    function arreter() {
      if (suivi) { clearInterval(suivi); suivi = null; }
    }

    interroger();
    suivi = setInterval(interroger, INTERVALLE);

    // Inutile d'interroger le serveur quand l'onglet est en arrière-plan
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) {
        arreter();
      } else if (!suivi) {
        interroger();
        suivi = setInterval(interroger, INTERVALLE);
      }
    });
  }

  /* ── Squelettes : retirés dès que la page est prête ───────────── */

  function retirerSquelettes() {
    document.querySelectorAll('[data-skeleton]').forEach(function (el) {
      el.removeAttribute('data-skeleton');
    });
  }

  /* ── Dialogues ────────────────────────────────────────────────── */

  function initDialogues() {
    document.querySelectorAll('[data-dialog-open]').forEach(function (bouton) {
      bouton.addEventListener('click', function () {
        var dlg = document.getElementById(bouton.getAttribute('data-dialog-open'));
        if (!dlg) return;
        if (typeof dlg.showModal === 'function') {
          dlg.showModal();
        } else {
          dlg.setAttribute('open', '');   // très anciens navigateurs
        }
        var premier = dlg.querySelector(
          'input:not([type=hidden]), select, textarea'
        );
        if (premier) premier.focus();
      });
    });

    document.querySelectorAll('dialog').forEach(function (dlg) {
      dlg.querySelectorAll('[data-dialog-close]').forEach(function (b) {
        b.addEventListener('click', function () { dlg.close(); });
      });

      // Clic sur le fond : le <dialog> occupe tout l'écran, on distingue
      // le fond du panneau en comparant la position du clic à sa boîte.
      dlg.addEventListener('click', function (e) {
        if (e.target !== dlg) return;
        var r = dlg.getBoundingClientRect();
        var dehors = e.clientX < r.left || e.clientX > r.right ||
                     e.clientY < r.top || e.clientY > r.bottom;
        if (dehors) dlg.close();
      });

      // Rendre la main aux boutons si l'utilisateur ferme après un envoi
      dlg.addEventListener('close', function () {
        dlg.querySelectorAll('form').forEach(function (f) {
          delete f.dataset.submitted;
        });
        dlg.querySelectorAll('.btn.is-busy').forEach(function (b) {
          if (SS.libererBouton) SS.libererBouton(b);
        });
      });
    });
  }

  /* Le rattachement à un établissement n'a de sens que pour un
     administrateur d'établissement : le champ se grise sinon. */
  function initChampsConditionnels() {
    document.querySelectorAll('[data-role-select]').forEach(function (select) {
      var groupe = select.closest('form').querySelector('[data-etab-group]');
      if (!groupe) return;
      var champ = groupe.querySelector('select');

      function appliquer() {
        var requis = select.value === 'admin_etablissement';
        groupe.style.opacity = requis ? '1' : '.55';
        if (champ) {
          champ.required = requis;
          if (select.value === 'super_admin') champ.value = '';
        }
      }
      select.addEventListener('change', appliquer);
      appliquer();
    });
  }

  /* Un <select> de filtre ou de pagination soumet son formulaire dès
     qu'il change : le bouton « OK » d'à côté ne servait qu'à rattraper
     l'absence de script, il reste dans un <noscript>. */
  function initSelectsAutoSoumis() {
    document.querySelectorAll('[data-soumettre-au-changement]').forEach(function (select) {
      select.addEventListener('change', function () {
        var form = select.form || select.closest('form');
        if (form) form.submit();
      });
    });
  }

  function init() {
    initSelectsAutoSoumis();
    initDialogues();
    initChampsConditionnels();
    initSidebar();
    initTableaux();
    initConfirmations();
    initBoutonsTest();
    initSuiviSync();
    retirerSquelettes();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
