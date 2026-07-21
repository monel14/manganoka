# Sécurité : JSON vs Pickle pour le cache

## 🚨 Problème de sécurité avec Pickle

### Vulnérabilité
`pickle` peut exécuter du code arbitraire lors de la désérialisation avec `pickle.loads()`. Si un attaquant accède à `cache.db` et insère des données malveillantes, il peut :
- Exécuter des commandes système
- Lire des fichiers sensibles
- Élever ses privilèges
- Compromettre complètement le serveur

### Exemple d'exploitation

```python
import pickle
import os

# Code malveillant qui s'exécutera lors du loads()
class Exploit:
    def __reduce__(self):
        # Cette commande s'exécutera automatiquement !
        return (os.system, ('rm -rf /tmp/test',))

# Sérialisation du payload malveillant
malicious_data = pickle.dumps(Exploit())

# Quelqu'un insère ça dans cache.db
# Quand l'app fait pickle.loads(malicious_data)...
# 💥 La commande s'exécute !
```

### Vecteurs d'attaque
1. **Injection SQL** dans une autre partie de l'app qui écrit dans cache.db
2. **Accès direct** au fichier cache.db (mauvaises permissions)
3. **Backup/restore** d'un cache.db corrompu
4. **SSRF** qui permet de manipuler le cache

## ✅ Solution : JSON

### Avantages de JSON
- ✅ **Sécurisé** : Ne peut pas exécuter de code
- ✅ **Lisible** : Peut être inspecté avec un éditeur de texte
- ✅ **Standard** : Format universel
- ✅ **Performant** : Parsing rapide avec le module natif
- ✅ **Interopérable** : Peut être lu par n'importe quel langage

### Limitations
- ❌ Ne supporte que les types de base (dict, list, str, int, float, bool, None)
- ❌ Ne peut pas sérialiser les objets Python personnalisés

Pour notre use case (cache de données scrappées) :
- ✅ On cache des dicts/lists/strings
- ✅ Pas besoin d'objets complexes
- ✅ JSON est parfaitement adapté

## 🔄 Migration effectuée

### Avant (DANGEREUX)
```python
import pickle

# BLOB stocke des données binaires pickle
conn.execute(
    "CREATE TABLE IF NOT EXISTS cache "
    "(key TEXT PRIMARY KEY, data BLOB, expires REAL)"
)

# ⚠️ VULNÉRABLE à l'exécution de code
data = pickle.loads(row[0])
pickle.dumps(data)
```

### Après (SÉCURISÉ)
```python
import json

# TEXT stocke du JSON lisible
conn.execute(
    "CREATE TABLE IF NOT EXISTS cache "
    "(key TEXT PRIMARY KEY, data TEXT, expires REAL)"
)

# ✅ SÉCURISÉ - Pas d'exécution de code possible
data = json.loads(row[0])
json.dumps(data, ensure_ascii=False)
```

## 📊 Comparaison

| Critère | Pickle | JSON |
|---------|--------|------|
| **Sécurité** | ❌ Dangereux | ✅ Sûr |
| **Lisibilité** | ❌ Binaire | ✅ Texte |
| **Performance** | ⚡ Rapide | ⚡ Rapide |
| **Taille** | 📦 Compact | 📦 Compact |
| **Types supportés** | ✅ Tous objets Python | ⚠️ Types de base uniquement |
| **Use case** | Objets complexes Python | **✅ Données API/Cache web** |

## 🛡️ Recommandations de sécurité

### 1. Permissions du fichier cache.db
```bash
# Restreindre l'accès
chmod 600 cache.db
chown www-data:www-data cache.db
```

### 2. Validation des données
```python
# Gestion des erreurs de parsing
try:
    return json.loads(row[0])
except json.JSONDecodeError:
    # Cache corrompu, on le recharge
    pass
```

### 3. Monitoring
- Surveiller la taille de cache.db
- Logger les erreurs de parsing JSON
- Alerter sur les modifications inattendues

## ✅ Tests de validation

```bash
# Vérifier que le cache JSON fonctionne
cd /home/ubuntu/app
.venv/bin/python -c "
import asyncio
from cache import cache

async def test():
    result = await cache.get_or_set(
        'test',
        60,
        lambda: {'secure': True, 'format': 'JSON'}
    )
    print(f'✅ {result}')

asyncio.run(test())
"
```

## 🎯 Conclusion

La migration de `pickle` vers `JSON` :
- ✅ **Élimine** une vulnérabilité critique d'exécution de code
- ✅ **Maintient** les performances du cache
- ✅ **Améliore** la lisibilité et la maintenabilité
- ✅ **Simplifie** le debugging (cache lisible)

Pour un cache de données web/API, **JSON est toujours le bon choix**.
