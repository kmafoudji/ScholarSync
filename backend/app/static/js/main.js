// ScholarSync — JS principal

// Sélecteur de langue
function setLang(lang) {
  document.cookie = `lang=${lang};path=/;max-age=31536000`;
  window.location.reload();
}

// Appliquer un filtre de facette
function applyFilter(key, value) {
  const url = new URL(window.location.href);
  if (value) {
    url.searchParams.set(key, value);
  } else {
    url.searchParams.delete(key);
  }
  url.searchParams.set('page', '1');
  window.location.href = url.toString();
}

// Tri des résultats
function applySort(value) {
  const url = new URL(window.location.href);
  url.searchParams.set('sort', value);
  url.searchParams.set('page', '1');
  window.location.href = url.toString();
}

// Icône ti-spin pour les loaders
document.querySelectorAll('.ti-spin').forEach(el => {
  el.style.animation = 'spin 1s linear infinite';
});

// Style animation spin
const style = document.createElement('style');
style.textContent = '@keyframes spin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}';
document.head.appendChild(style);

// Menu mobile
const menuToggle = document.getElementById('menuToggle');
if (menuToggle) {
  menuToggle.style.display = 'block';
}

// Fermer sidebar admin en cliquant à l'extérieur (mobile)
document.addEventListener('click', (e) => {
  const sidebar = document.getElementById('adminSidebar');
  if (sidebar && sidebar.classList.contains('open') &&
      !sidebar.contains(e.target) && e.target !== menuToggle) {
    sidebar.classList.remove('open');
  }
});
