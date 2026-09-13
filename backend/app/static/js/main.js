/* ScholarSync — comportements communs au site public et à l'administration.
   Chargé sur toutes les pages ; admin.js ne couvre que le back-office. */
(function () {
  'use strict';

  /* ── Notifications ──────────────────────────────────────────────
     Une seule pile de toasts pour toute l'application. Les messages
     du serveur arrivent via #flash-data, ceux du client via
     window.ScholarSync.toast(). */

  var ICONES = {
    success: 'ti-circle-check',
    info: 'ti-info-circle',
    warning: 'ti-alert-triangle',
    danger: 'ti-alert-circle'
  };

  var DUREES = {
    success: 5000,
    info: 6000,
    warning: 8000,
    danger: 0 // une erreur reste affichée jusqu'à fermeture explicite
  };

  var zone = null;

  function zoneToasts() {
    if (!zone) {
      zone = document.createElement('div');
      zone.className = 'toast-zone';
      // polite : annoncé sans interrompre la tâche en cours du lecteur d'écran
      zone.setAttribute('aria-live', 'polite');
      zone.setAttribute('aria-atomic', 'false');
      document.body.appendChild(zone);
    }
    return zone;
  }

  function fermer(toast) {
    if (!toast || toast.dataset.closing) return;
    toast.dataset.closing = '1';
    toast.classList.add('toast--closing');
    setTimeout(function () {
      if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 200);
  }

  function toast(message, type, duree) {
    if (!message) return null;
    type = ICONES[type] ? type : 'success';

    var el = document.createElement('div');
    el.className = 'toast toast--' + type;
    el.setAttribute('role', type === 'danger' ? 'alert' : 'status');

    var icone = document.createElement('i');
    icone.className = 'ti ' + ICONES[type] + ' toast__icon';
    icone.setAttribute('aria-hidden', 'true');

    var corps = document.createElement('div');
    corps.className = 'toast__body';
    corps.textContent = message; // textContent : jamais d'injection HTML

    var bouton = document.createElement('button');
    bouton.className = 'toast__close';
    bouton.type = 'button';
    bouton.setAttribute('aria-label', 'Fermer la notification');
    bouton.innerHTML = '<i class="ti ti-x" aria-hidden="true"></i>';
    bouton.addEventListener('click', function () { fermer(el); });

    el.appendChild(icone);
    el.appendChild(corps);
    el.appendChild(bouton);
    zoneToasts().appendChild(el);

    var delai = typeof duree === 'number' ? duree : DUREES[type];
    if (delai > 0) {
      var minuteur = setTimeout(function () { fermer(el); }, delai);
      // Ne pas escamoter le message pendant que l'utilisateur le lit
      el.addEventListener('mouseenter', function () { clearTimeout(minuteur); });
      el.addEventListener('focusin', function () { clearTimeout(minuteur); });
    }
    return el;
  }

  function afficherFlashServeur() {
    var noeud = document.getElementById('flash-data');
    if (!noeud) return;
    try {
      var data = JSON.parse(noeud.textContent || '{}');
      if (data.message) toast(data.message, data.type);
    } catch (e) {
      /* données malformées : on n'affiche rien plutôt que de casser la page */
    }
  }

  /* ── Filtres et tri (site public) ─────────────────────────────── */

  function appliquerParametre(cle, valeur) {
    var url = new URL(window.location.href);
    if (valeur) {
      url.searchParams.set(cle, valeur);
    } else {
      url.searchParams.delete(cle);
    }
    url.searchParams.set('page', '1');
    marquerChargement();
    window.location.href = url.toString();
  }

  /* Repère visuel pendant la navigation : sans cela, un clic sur une
     facette semble sans effet le temps que la page se recharge. */
  function marquerChargement() {
    document.documentElement.classList.add('is-navigating');
  }

  /* ── Menu mobile du site public ───────────────────────────────── */

  function initMenuPublic() {
    var bouton = document.getElementById('menuTogglePublic');
    var nav = document.querySelector('.site-nav');
    if (!bouton || !nav) return;

    bouton.addEventListener('click', function (e) {
      e.stopPropagation();
      var ouvert = nav.classList.toggle('open');
      bouton.setAttribute('aria-expanded', ouvert ? 'true' : 'false');
    });

    document.addEventListener('click', function (e) {
      if (nav.classList.contains('open') &&
          !nav.contains(e.target) && e.target !== bouton) {
        nav.classList.remove('open');
        bouton.setAttribute('aria-expanded', 'false');
      }
    });

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && nav.classList.contains('open')) {
        nav.classList.remove('open');
        bouton.setAttribute('aria-expanded', 'false');
        bouton.focus();
      }
    });
  }

  /* ── Sélecteur de langue ──────────────────────────────────────── */

  function setLang(lang) {
    document.cookie = 'lang=' + encodeURIComponent(lang) +
                      ';path=/;max-age=31536000;samesite=lax';
    marquerChargement();
    window.location.reload();
  }

  /* ── Boutons et formulaires occupés ───────────────────────────── */

  function occuper(bouton) {
    if (!bouton || bouton.classList.contains('is-busy')) return;
    // Largeur figée : sans cela le bouton se rétrécit quand son texte
    // devient transparent et la mise en page saute.
    var largeur = bouton.getBoundingClientRect().width;
    if (largeur) bouton.style.minWidth = Math.ceil(largeur) + 'px';
    bouton.classList.add('is-busy');
    bouton.setAttribute('aria-busy', 'true');
  }

  function libererBouton(bouton) {
    if (!bouton) return;
    bouton.classList.remove('is-busy');
    bouton.removeAttribute('aria-busy');
    bouton.style.minWidth = '';
  }

  /* Tout formulaire non marqué data-no-busy affiche un état occupé à
     la soumission. Évite le double envoi et rend l'attente lisible. */
  function initFormulaires() {
    document.querySelectorAll('form').forEach(function (form) {
      if (form.hasAttribute('data-no-busy')) return;

      form.addEventListener('submit', function () {
        if (form.dataset.submitted) return;
        form.dataset.submitted = '1';

        var declencheur = form.querySelector(
          'button[type="submit"], input[type="submit"]'
        );
        occuper(declencheur);

        // Filet de sécurité : si la navigation échoue ou que
        // l'utilisateur revient par l'historique, on rend la main.
        setTimeout(function () {
          delete form.dataset.submitted;
          libererBouton(declencheur);
        }, 12000);
      });
    });

    // Retour arrière : le navigateur restitue la page depuis son cache
    // avec les boutons encore figés. On les réactive.
    window.addEventListener('pageshow', function (e) {
      if (!e.persisted) return;
      document.documentElement.classList.remove('is-navigating');
      document.querySelectorAll('form').forEach(function (f) {
        delete f.dataset.submitted;
      });
      document.querySelectorAll('.btn.is-busy').forEach(libererBouton);
    });
  }

  /* ── API publique ─────────────────────────────────────────────── */

  window.ScholarSync = {
    toast: toast,
    fermerToast: fermer,
    occuper: occuper,
    libererBouton: libererBouton,
    marquerChargement: marquerChargement
  };

  // Fonctions appelées en ligne depuis les gabarits Jinja
  window.setLang = setLang;
  window.applyFilter = appliquerParametre;
  window.applySort = function (valeur) { appliquerParametre('sort', valeur); };

  function init() {
    afficherFlashServeur();
    initMenuPublic();
    initFormulaires();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
