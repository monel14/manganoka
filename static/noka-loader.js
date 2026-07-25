/* ==========================================
   NOKA LOADER — indicateur de chargement global
   Affiche la lanterne Noka qui se balance :
   - à la navigation entre pages (clics sur liens internes)
   - accessible via window.NokaLoader.show() / .hide()
   ========================================== */
(function () {
    var MESSAGES = [
        'Noka feuillette les pages',
        'Noka allume sa lanterne',
        'Noka cherche le bon rayon',
        'Chargement'
    ];

    function buildOverlay() {
        var overlay = document.createElement('div');
        overlay.className = 'noka-loader-overlay';
        overlay.setAttribute('role', 'status');
        overlay.setAttribute('aria-live', 'polite');

        var img = document.createElement('img');
        img.src = '/static/noka_loader.svg';
        img.alt = 'Chargement en cours';

        var text = document.createElement('div');
        text.className = 'loader-text';
        text.textContent = MESSAGES[Math.floor(Math.random() * MESSAGES.length)];

        overlay.appendChild(img);
        overlay.appendChild(text);
        return overlay;
    }

    var overlay = null;
    var showTimer = null;

    function ensureOverlay() {
        if (!overlay) {
            overlay = buildOverlay();
            document.body.appendChild(overlay);
        }
        return overlay;
    }

    var NokaLoader = {
        // delayMs évite le flash sur les navigations rapides (<250 ms)
        show: function (delayMs) {
            var d = typeof delayMs === 'number' ? delayMs : 250;
            clearTimeout(showTimer);
            showTimer = setTimeout(function () {
                ensureOverlay().classList.add('visible');
            }, d);
        },
        hide: function () {
            clearTimeout(showTimer);
            if (overlay) overlay.classList.remove('visible');
        }
    };
    window.NokaLoader = NokaLoader;

    function isInternalNavLink(a) {
        if (!a || a.target === '_blank' || a.hasAttribute('download')) return false;
        var href = a.getAttribute('href');
        if (!href || href.charAt(0) === '#' ||
            href.indexOf('javascript:') === 0 || href.indexOf('mailto:') === 0) return false;
        return a.host === window.location.host;
    }

    function init() {
        // Navigation entre pages
        document.addEventListener('click', function (e) {
            if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
            var a = e.target.closest ? e.target.closest('a') : null;
            if (isInternalNavLink(a)) NokaLoader.show();
        });

        // Soumission des formulaires (recherche)
        document.addEventListener('submit', function (e) {
            var form = e.target;
            if (form && form.method !== 'dialog') NokaLoader.show();
        });

        // Cacher le loader si la page revient du bfcache (bouton retour)
        window.addEventListener('pageshow', function (e) {
            if (e.persisted) NokaLoader.hide();
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
