# Checklist de déploiement

## ✅ Validations effectuées

### Code
- [x] Syntaxe Python validée (py_compile)
- [x] Imports fonctionnels
- [x] Application démarre sans erreur
- [x] Tests unitaires passent

### Dépendances
- [x] `httpx>=0.27.0` installé
- [x] Toutes les dépendances à jour
- [x] Pas de dépendances manquantes

### Sécurité
- [x] JSON au lieu de pickle (pas d'exécution de code)
- [x] Validation stricte des domaines (SSRF protection)
- [x] Limite de taille d'images (20 Mo)
- [x] Validation Content-Type
- [x] Streaming (pas de DoS RAM)

### Performance
- [x] Architecture async complète
- [x] Cache optimisé (pas de double chargement)
- [x] Requêtes S3 optimisées (-50%)
- [x] Normalisation des recherches

---

## 🚀 Étapes de déploiement

### 1. Backup
```bash
# Sauvegarder la base de données
cp cache.db cache.db.backup

# Sauvegarder l'ancien code
tar -czf backup-$(date +%Y%m%d).tar.gz *.py routes/ scraper/
```

### 2. Installation des dépendances
```bash
cd /home/ubuntu/app
.venv/bin/pip install -r requirements.txt
```

### 3. Migration du cache (optionnel)
```bash
# Si vous voulez nettoyer l'ancien cache pickle
rm cache.db
# Le cache sera recréé automatiquement en JSON
```

### 4. Vérification
```bash
# Tester l'import
.venv/bin/python -c "from main import app; print('✅ OK')"

# Tester le client async
.venv/bin/python -c "
import asyncio
from scraper.client import get_html, close_http_client

async def test():
    html = await get_html('/lastupdates.php')
    print(f'✅ Test OK: {len(html)} caractères')
    await close_http_client()

asyncio.run(test())
"
```

### 5. Redémarrage
```bash
# Selon votre gestionnaire de processus

# Avec systemd
sudo systemctl restart manga-reader

# Avec supervisor
sudo supervisorctl restart manga-reader

# Avec passenger (actuel)
touch tmp/restart.txt
```

### 6. Monitoring post-déploiement
```bash
# Vérifier les logs
tail -f logs/application.log

# Vérifier les erreurs
grep ERROR logs/application.log | tail -20

# Tester les endpoints
curl https://manganoka.xyz/
curl https://manganoka.xyz/search?q=naruto
```

---

## 🔍 Points de vigilance

### Performance
- [ ] Temps de réponse < 500ms pour les pages en cache
- [ ] Temps de réponse < 2s pour les nouvelles pages
- [ ] Utilisation CPU stable
- [ ] Utilisation RAM stable (pas de memory leak)

### Logs à surveiller
```bash
# Erreurs de téléchargement d'images
grep "Image trop grande" logs/application.log

# Domaines bloqués (potentielles attaques)
grep "Domaine non autorisé" logs/application.log

# Erreurs S3
grep "Échec upload S3" logs/application.log
```

### Métriques à surveiller
- Taux de hit du cache S3
- Taux de hit du cache local
- Nombre d'images > 20 Mo bloquées
- Nombre de domaines non autorisés bloqués

---

## 🛠️ Rollback (si problème)

### Rollback rapide
```bash
# Restaurer l'ancien code
tar -xzf backup-YYYYMMDD.tar.gz

# Redémarrer
touch tmp/restart.txt  # ou systemctl restart
```

### Rollback du cache
```bash
# Si problème avec le cache JSON
cp cache.db.backup cache.db
```

---

## 📊 Benchmarks (optionnel)

### Test de performance
```bash
# Exécuter le benchmark
.venv/bin/python benchmark_async.py
```

### Test de charge (avec Apache Bench)
```bash
# Test simple
ab -n 1000 -c 10 https://manganoka.xyz/

# Test recherche
ab -n 500 -c 5 "https://manganoka.xyz/search?q=naruto"
```

---

## 🔐 Sécurité post-déploiement

### Permissions fichiers
```bash
# Cache DB
chmod 600 cache.db
chown www-data:www-data cache.db

# Dossier cache images
chmod 755 static/img_cache
chown -R www-data:www-data static/img_cache

# .env (secrets)
chmod 600 .env
```

### Variables d'environnement sensibles
```bash
# Vérifier que les secrets ne sont pas exposés
grep -r "N0C_SECRET_KEY" --exclude-dir=.git --exclude=".env"
# Ne devrait retourner que .env et images.py (qui le charge)
```

---

## ✅ Checklist finale

Avant de considérer le déploiement réussi :

- [ ] Application démarre sans erreur
- [ ] Page d'accueil charge correctement
- [ ] Recherche fonctionne
- [ ] Lecture de chapitres fonctionne
- [ ] Images se chargent correctement
- [ ] Logs ne montrent pas d'erreurs critiques
- [ ] Utilisation RAM stable après 1h
- [ ] Cache S3 fonctionne
- [ ] Cache local fonctionne (fallback)
- [ ] Limites de taille respectées
- [ ] Domaines non autorisés bloqués

---

## 📞 Support

### Logs importants
```bash
# Logs généraux
tail -f logs/application.log

# Logs erreurs uniquement
tail -f logs/application.log | grep ERROR

# Logs images spécifiquement
tail -f logs/application.log | grep "Image"
```

### Debug interactif
```python
# Shell Python avec contexte
.venv/bin/python

>>> from main import app
>>> from services.image_cache import ImageCacheService
>>> # Debug...
```

---

## 🎉 Déploiement réussi !

Si tous les points sont validés :
- ✅ Application fonctionnelle
- ✅ Performance améliorée
- ✅ Sécurité renforcée
- ✅ Architecture modulaire

**L'application est prête pour la production !** 🚀
