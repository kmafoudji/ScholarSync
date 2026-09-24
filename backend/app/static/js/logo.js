/* Contrôle d'un logo AVANT l'envoi.
 *
 * Le serveur reste juge (app/core/logos.py) : ce script ne fait que
 * prévenir tôt. Il affiche l'aperçu, mesure l'image — marges
 * transparentes déduites, comme le fera le serveur — et bloque l'envoi
 * d'un fichier qui serait refusé, avec la raison et la correction.
 *
 * Balisage attendu :
 *   <div data-logo-champ data-min-court data-min-long data-ratio-max data-max-mo>
 *     … [data-logo-apercu] (un ou plusieurs) …
 *     <input type="file"> … [data-logo-retour]
 *   </div>
 */
(function () {
  'use strict';

  var TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/svg+xml'];

  function retour(champ, niveau, lignes) {
    var zone = champ.querySelector('[data-logo-retour]');
    if (!zone) return;
    zone.className = 'logo-televersement__retour logo-televersement__retour--' + niveau;
    zone.textContent = '';
    lignes.forEach(function (texte) {
      var p = document.createElement('p');
      p.textContent = texte;
      zone.appendChild(p);
    });
  }

  function apercu(champ, url) {
    champ.querySelectorAll('[data-logo-apercu]').forEach(function (cadre) {
      cadre.textContent = '';
      var img = document.createElement('img');
      img.src = url;
      img.alt = 'Aperçu du logo choisi';
      cadre.appendChild(img);
    });
  }

  /* Dimensions utiles : sans les marges transparentes. */
  function mesurer(img) {
    var l = img.naturalWidth, h = img.naturalHeight;
    var echelle = Math.min(1, 1000 / Math.max(l, h));
    var cl = Math.max(1, Math.round(l * echelle));
    var ch = Math.max(1, Math.round(h * echelle));
    var canvas = document.createElement('canvas');
    canvas.width = cl; canvas.height = ch;
    var ctx = canvas.getContext('2d');
    ctx.drawImage(img, 0, 0, cl, ch);
    var px;
    try { px = ctx.getImageData(0, 0, cl, ch).data; } catch (e) {
      return { l: l, h: h, rogne: false, transparent: false };
    }
    var minX = cl, minY = ch, maxX = -1, maxY = -1, transparent = false;
    for (var y = 0; y < ch; y++) {
      for (var x = 0; x < cl; x++) {
        var a = px[(y * cl + x) * 4 + 3];
        if (a < 255) transparent = true;
        if (a > 0) {
          if (x < minX) minX = x;
          if (x > maxX) maxX = x;
          if (y < minY) minY = y;
          if (y > maxY) maxY = y;
        }
      }
    }
    if (maxX < 0) return { l: 0, h: 0, rogne: true, transparent: true, vide: true };
    var rl = Math.round((maxX - minX + 1) / echelle);
    var rh = Math.round((maxY - minY + 1) / echelle);
    return {
      l: Math.min(l, rl), h: Math.min(h, rh),
      rogne: rl < l - 1 || rh < h - 1,
      transparent: transparent
    };
  }

  function controler(champ, input) {
    input.setCustomValidity('');
    var fichier = input.files && input.files[0];
    if (!fichier) { retour(champ, 'neutre', []); return; }

    var minCourt = +champ.dataset.minCourt || 0;
    var minLong = +champ.dataset.minLong || 0;
    var ratioMax = +champ.dataset.ratioMax || 99;
    var maxMo = +champ.dataset.maxMo || 2;

    function refuser(message) {
      input.setCustomValidity(message);
      retour(champ, 'erreur', [message]);
    }

    if (TYPES.indexOf(fichier.type) === -1) {
      return refuser('Format non accepté : choisissez un PNG, un SVG, un WebP ou un JPEG.');
    }
    if (fichier.size > maxMo * 1024 * 1024) {
      return refuser('Le fichier pèse ' + (fichier.size / 1048576).toFixed(1) +
                     ' Mo : la limite est de ' + maxMo + ' Mo.');
    }

    var url = URL.createObjectURL(fichier);
    apercu(champ, url);

    if (fichier.type === 'image/svg+xml') {
      retour(champ, 'ok', ['SVG vectoriel : net à toutes les tailles. ' +
                           'Il sera vérifié à l’enregistrement.']);
      return;
    }

    var img = new Image();
    img.onload = function () {
      var m = mesurer(img);
      if (m.vide) return refuser('L’image est entièrement transparente.');
      var court = Math.min(m.l, m.h), long = Math.max(m.l, m.h);
      var dims = m.l + ' × ' + m.h + ' px' + (m.rogne ? ' (marges transparentes retirées)' : '');
      if (court < minCourt || long < minLong) {
        return refuser('Image trop petite : ' + dims + '. Il faut au moins ' + minCourt +
                       ' px sur le petit côté et ' + minLong + ' px sur le grand, ' +
                       'sinon le logo sera flou. Exportez-le en plus grand, ou en SVG.');
      }
      if (long / court > ratioMax) {
        return refuser('Proportions trop allongées : ' + dims + ' (' +
                       (long / court).toFixed(1) + ':1, maximum ' + ratioMax + ':1).');
      }
      var lignes = ['Image de ' + dims + ' : qualité suffisante.'];
      var niveau = 'ok';
      if (court < minCourt * 2) {
        lignes.push('Acceptée, mais une version deux fois plus grande serait plus nette.');
        niveau = 'avertissement';
      }
      if (!m.transparent) {
        lignes.push('Pas de fond transparent : un PNG transparent s’intégrera mieux.');
        niveau = 'avertissement';
      }
      retour(champ, niveau, lignes);
    };
    img.onerror = function () {
      refuser('Ce fichier ne s’ouvre pas comme une image : il est peut-être corrompu.');
    };
    img.src = url;
  }

  function init() {
    document.querySelectorAll('[data-logo-champ]').forEach(function (champ) {
      var input = champ.querySelector('input[type="file"]');
      if (!input) return;
      input.addEventListener('change', function () { controler(champ, input); });
      /* Un cocher « Retirer le logo » et un fichier choisi se contredisent. */
      var retirer = champ.querySelector('input[name="retirer_logo"]');
      if (retirer) {
        retirer.addEventListener('change', function () {
          if (retirer.checked) { input.value = ''; controler(champ, input); }
        });
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
