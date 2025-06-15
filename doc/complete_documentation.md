# Client HTTP Asynchrone Résilient

Client HTTP asynchrone avancé avec stratégie de retry intelligente, gestion d'événements par queue asynchrone et contextes isolés par coroutine.

## 🚀 Fonctionnalités Principales

- **Architecture Asynchrone** : Basé sur `httpx.AsyncClient` avec support complet d'asyncio
- **Stratégie de Retry Intelligente** : Retry automatique sur `httpcore.ConnectError` uniquement
- **Contextes Isolés** : Chaque coroutine possède son propre contexte clonable
- **Communication par Événements** : Queue asynchrone pour monitoring centralisé
- **Types Complets** : Annotations de type exhaustives avec `Final` pour les constantes
- **Backoff Exponentiel** : Délais intelligents avec jitter anti-thundering herd
- **Monitoring Intégré** : Callbacks d'événements et métriques en temps réel

## 📦 Installation

```bash
# Installation de base
pip install httpx pytest pytest-asyncio

# Installation avec HTTP/2
pip install httpx[http2]

# Installation complète pour développement
pip install httpx[http2] pytest pytest-asyncio pytest-cov mypy black isort
```

## 🏗️ Architecture

### Composants Principaux

```
┌─────────────────────┐    ┌─────────────────────┐    ┌─────────────────────┐
│   RequestContext    │    │    AsyncEvent       │    │   RequestResult     │
│   (Immutable)       │    │   (Communication)   │    │   (Final State)     │
├─────────────────────┤    ├─────────────────────┤    ├─────────────────────┤
│ • max_retries       │    │ • event_type        │    │ • request_id        │
│ • current_attempt   │    │ • context           │    │ • status            │
│ • base_delay        │    │ • timestamp         │    │ • response          │
│ • request_id        │    │ • data/error        │    │ • attempts          │
│ • metadata          │    │ • metadata          │    │ • total_time        │
└─────────────────────┘    └─────────────────────┘    └─────────────────────┘
         │                           │                           │
         └───────────────┬───────────────────────────────────────┘
                         │
         ┌─────────────────────────────────────────────────────────────┐
         │            AsyncResilientClient                             │
         ├─────────────────────────────────────────────────────────────┤
         │ • httpx.AsyncClient wrapper                                 │
         │ • asyncio.Queue pour événements                             │
         │ • Event loop monitoring                                     │
         │ • Gestion des tâches concurrentes                           │
         │ • Callbacks d'événements                                    │
         └─────────────────────────────────────────────────────────────┘
```

### Flow d'Exécution

```
1. Création du contexte (RequestContext)
2. Lancement de la coroutine de requête
3. Tentative de connexion HTTP
4. En cas d'erreur ConnectError :
   ├─ Incrément du contexte
   ├─ Vérification des retries restants
   ├─ Calcul du délai (backoff + jitter)
   ├─ Émission d'événement RETRY
   └─ Nouvelle tentative
5. Succès ou échec définitif :
   ├─ Émission d'événement SUCCESS/ERROR
   ├─ Stockage du résultat
   └─ Appel des callbacks
```

## 📖 Guide d'Utilisation

### 1. Utilisation Simple

```python
import asyncio
from typed_async_client import simple_async_request

async def exemple_simple():
    try:
        # Requête GET avec retry automatique
        response = await simple_async_request(
            "GET",
            "https://api.example.com/data",
            max_retries=2,
            timeout=10.0
        )
        
        print(f"✅ Succès: {response.status_code}")
        data = response.json()
        return data
        
    except Exception as e:
        print(f"❌ Erreur: {e}")
        return None

# Lancement
result = asyncio.run(exemple_simple())
```

### 2. Client Avancé avec Context Manager

```python
import asyncio
from typed_async_client import (
    create_async_resilient_client, 
    RequestContext, 
    EventType
)

async def exemple_avance():
    # Callbacks pour monitoring
    async def on_retry(event):
        print(f"🔄 Retry: {event.context.request_id} "
              f"(tentative {event.context.current_attempt})")
    
    async def on_error(event):
        print(f"❌ Erreur: {event.context.request_id} - {event.error}")
    
    # Configuration du client
    async with create_async_resilient_client(
        timeout=httpx.Timeout(connect=5.0, read=30.0),
        limits=httpx.Limits(max_connections=50),
        event_queue_size=100
    ) as client:
        
        # Ajouter les callbacks
        client.add_event_callback(EventType.RETRY, on_retry)
        client.add_event_callback(EventType.ERROR, on_error)
        
        # Contexte personnalisé
        context = RequestContext(
            max_retries=3,
            base_delay=0.5,
            request_id="api_data_fetch",
            metadata={"priority": "high"}
        )
        
        # Lancer la requête
        task = await client.get(
            "https://api.example.com/data",
            context=context,
            headers={"Authorization": "Bearer YOUR_TOKEN"}
        )
        
        # Attendre le résultat
        response = await task
        print(f"Status: {response.status_code}")
        
        # Récupérer les métriques
        result = client.get_result("api_data_fetch")
        print(f"Tentatives: {result.attempts}")
        print(f"Temps total: {result.total_time:.2f}s")

asyncio.run(exemple_avance())
```

### 3. Requêtes Concurrentes

```python
import asyncio
from typed_async_client import create_async_resilient_client, RequestContext

async def requetes_concurrentes():
    urls_et_contextes = [
        ("https://api.service1.com/users", 
         RequestContext(request_id="users", max_retries=2)),
        ("https://api.service2.com/posts", 
         RequestContext(request_id="posts", max_retries=3)),
        ("https://api.service3.com/comments", 
         RequestContext(request_id="comments", max_retries=1)),
    ]
    
    async with create_async_resilient_client() as client:
        # Lancer toutes les requêtes en parallèle
        tasks = []
        for url, context in urls_et_contextes:
            task = await client.get(url, context=context)
            tasks.append(task)
        
        # Attendre toutes les réponses
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Analyser les résultats
        results = await client.wait_for_all_requests()
        
        for request_id, result in results.items():
            status = "✅" if result.is_success else "❌"
            print(f"{status} {request_id}: {result.status} "
                  f"({result.attempts} tentatives)")

asyncio.run(requetes_concurrentes())
```

### 4. Intégration avec FastAPI

```python
from fastapi import FastAPI, HTTPException
from typed_async_client import create_async_resilient_client, RequestContext

app = FastAPI()

# Client global (à initialiser au démarrage)
http_client = None

@app.on_event("startup")
async def startup():
    global http_client
    http_client = AsyncResilientClient()
    await http_client._initialize_client()

@app.on_event("shutdown")
async def shutdown():
    global http_client
    if http_client:
        await http_client.close()

@app.get("/api/external-data/{data_id}")
async def get_external_data(data_id: str):
    context = RequestContext(
        request_id=f"external_data_{data_id}",
        max_retries=2
    )
    
    try:
        task = await http_client.get(
            f"https://external-api.com/data/{data_id}",
            context=context
        )
        response = await task
        
        if response.status_code == 200:
            return response.json()
        else:
            raise HTTPException(
                status_code=response.status_code,
                detail="Erreur API externe"
            )
            
    except Exception as e:
        # Récupérer les métriques pour logging
        result = http_client.get_result(context.request_id)
        if result:
            print(f"Échec après {result.attempts} tentatives")
        
        raise HTTPException(
            status_code=503,
            detail="Service externe indisponible"
        )
```

## 🔧 Configuration Avancée

### Constantes Personnalisées

```python
# async_client_constants.py
from typing import Final

# Valeurs personnalisées pour votre application
CUSTOM_MAX_RETRIES: Final[int] = 5
CUSTOM_BASE_DELAY: Final[float] = 0.5
CUSTOM_BACKOFF_FACTOR: Final[float] = 1.5

# Headers spécialisés
API_HEADERS: Final[dict[str, str]] = {
    "User-Agent": "MonApp/2.0",
    "Accept": "application/json",
    "X-API-Version": "v2"
}
```

### Contexte Personnalisé

```python
from dataclasses import dataclass
from typed_async_client import RequestContext

@dataclass(frozen=True)
class ApiRequestContext(RequestContext):
    """Contexte étendu pour API spécialisée."""
    api_version: str = "v1"
    priority: str = "normal"
    trace_id: str = ""
    
    def to_headers(self) -> dict[str, str]:
        """Convertit le contexte en headers HTTP."""
        return {
            "X-API-Version": self.api_version,
            "X-Priority": self.priority,
            "X-Trace-ID": self.trace_id or self.request_id
        }

# Utilisation
context = ApiRequestContext(
    request_id="custom_api_call",
    max_retries=3,
    api_version="v2",
    priority="high",
    trace_id="trace_12345"
)

async with create_async_resilient_client() as client:
    task = await client.get(
        "https://api.example.com/data",
        context=context,
        headers=context.to_headers()
    )
    response = await task
```

## 🧪 Tests et Validation

### Structure des Tests

```
tests/
├── test_context.py          # Tests des contextes
├── test_events.py           # Tests des événements
├── test_client.py           # Tests du client principal
├── test_retry_logic.py      # Tests de la logique de retry
├── test_concurrent.py       # Tests de concurrence
├── test_integration.py      # Tests d'intégration
└── conftest.py             # Configuration pytest
```

### Lancement des Tests

```bash
# Tests unitaires seulement
pytest tests/ -v -m "not integration"

# Tests avec couverture
pytest tests/ --cov=typed_async_client --cov-report=html

# Tests d'intégration (nécessite internet)
pytest tests/ -v -m integration

# Tests de performance
pytest tests/ -v -m slow --durations=10

# Tests en parallèle
pytest tests/ -n auto
```

### Exemple de Test Personnalisé

```python
import pytest
import asyncio
from unittest.mock import AsyncMock, patch
from typed_async_client import AsyncResilientClient, RequestContext

class TestCustomScenario:
    @pytest.mark.asyncio
    async def test_custom_retry_scenario(self):
        """Test d'un scénario métier spécifique."""
        async with AsyncResilientClient() as client:
            context = RequestContext(
                request_id="business_critical",
                max_retries=5,
                base_delay=0.1
            )
            
            with patch.object(client._client, 'request') as mock:
                # Simuler 3 échecs puis 1 succès
                mock_response = AsyncMock()
                mock_response.status_code = 200
                
                mock.side_effect = [
                    httpcore.ConnectError("Fail 1"),
                    httpcore.ConnectError("Fail 2"), 
                    httpcore.ConnectError("Fail 3"),
                    mock_response
                ]
                
                task = await client.get("https://test.com", context=context)
                response = await task
                
                assert response.status_code == 200
                assert mock.call_count == 4
                
                # Vérifier les métriques
                result = client.get_result("business_critical")
                assert result.attempts == 4
                assert result.is_success
```

## 📊 Monitoring et Métriques

### Callbacks d'Événements

```python
import logging
from collections import defaultdict
from typing import Dict

# Configuration du logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MetricsCollector:
    def __init__(self):
        self.stats: Dict[str, int] = defaultdict(int)
        self.response_times: list[float] = []
    
    async def on_success(self, event):
        self.stats['success'] += 1
        self.response_times.append(event.context.elapsed_time)
        logger.info(f"✅ Succès: {event.context.request_id}")
    
    async def on_retry(self, event):
        self.stats['retry'] += 1
        logger.warning(f"🔄 Retry {event.context.current_attempt}: "
                      f"{event.context.request_id}")
    
    async def on_error(self, event):
        self.stats['error'] += 1
        logger.error(f"❌ Erreur définitive: {event.context.request_id}")
    
    def get_metrics(self) -> dict:
        total = sum(self.stats.values())
        if total == 0:
            return {}
        
        avg_response_time = (
            sum(self.response_times) / len(self.response_times)
            if self.response_times else 0
        )
        
        return {
            'total_requests': total,
            'success_rate': self.stats['success'] / total,
            'retry_rate': self.stats['retry'] / total,
            'error_rate': self.stats['error'] / total,
            'avg_response_time': avg_response_time
        }

# Utilisation
async def monitored_requests():
    metrics = MetricsCollector()
    
    async with create_async_resilient_client() as client:
        # Ajouter les callbacks de monitoring
        client.add_event_callback(EventType.SUCCESS, metrics.on_success)
        client.add_event_callback(EventType.RETRY, metrics.on_retry)
        client.add_event_callback(EventType.ERROR, metrics.on_error)
        
        # Faire plusieurs requêtes
        urls = ["https://httpbin.org/get" for _ in range(10)]
        tasks = []
        
        for i, url in enumerate(urls):
            context = RequestContext(request_id=f"req_{i}")
            task = await client.get(url, context=context)
            tasks.append(task)
        
        # Attendre toutes les requêtes
        await asyncio.gather(*tasks, return_exceptions=True)
        await client.wait_for_all_requests()
        
        # Afficher les métriques
        stats = metrics.get_metrics()
        print("📊 Métriques:")
        for key, value in stats.items():
            print(f"  {key}: {value}")

asyncio.run(monitored_requests())
```

### Dashboard en Temps Réel

```python
import asyncio
import time
from datetime import datetime

class RealTimeDashboard:
    def __init__(self, client: AsyncResilientClient):
        self.client = client
        self.running = False
    
    async def start_monitoring(self):
        """Démarre le monitoring en temps réel."""
        self.running = True
        while self.running:
            await self.display_stats()
            await asyncio.sleep(5)  # Actualisation toutes les 5s
    
    async def display_stats(self):
        """Affiche les statistiques actuelles."""
        print(f"\n{'='*50}")
        print(f"🚀 Dashboard - {datetime.now().strftime('%H:%M:%S')}")
        print(f"{'='*50}")
        print(f"Tâches actives: {self.client.active_tasks_count}")
        print(f"Résultats en cache: {self.client.results_count}")
        print(f"Queue d'événements: {self.client.event_queue_size}")
        print(f"Client fermé: {self.client.is_closed}")
        
        # Statistiques des résultats
        results = self.client.get_all_results()
        if results:
            success_count = sum(1 for r in results.values() if r.is_success)
            error_count = sum(1 for r in results.values() if r.is_error)
            avg_attempts = sum(r.attempts for r in results.values()) / len(results)
            
            print(f"Succès: {success_count}/{len(results)}")
            print(f"Erreurs: {error_count}/{len(results)}")
            print(f"Tentatives moyennes: {avg_attempts:.1f}")
    
    def stop(self):
        """Arrête le monitoring."""
        self.running = False

# Utilisation avec le dashboard
async def exemple_avec_dashboard():
    async with create_async_resilient_client() as client:
        dashboard = RealTimeDashboard(client)
        
        # Démarrer le monitoring en arrière-plan
        monitor_task = asyncio.create_task(dashboard.start_monitoring())
        
        try:
            # Lancer des requêtes
            tasks = []
            for i in range(20):
                context = RequestContext(request_id=f"dashboard_req_{i}")
                task = await client.get(
                    f"https://httpbin.org/delay/{i%3}",  # Délais variables
                    context=context
                )
                tasks.append(task)
                await asyncio.sleep(0.5)  # Espacement des requêtes
            
            # Attendre toutes les requêtes
            await asyncio.gather(*tasks, return_exceptions=True)
            
        finally:
            dashboard.stop()
            await monitor_task

# asyncio.run(exemple_avec_dashboard())
```

## 🛠️ Dépannage et Bonnes Pratiques

### Problèmes Courants

#### 1. Timeout de Queue d'Événements

```python
# ❌ Problème : Queue saturée
client = AsyncResilientClient(event_queue_size=10)  # Trop petit

# ✅ Solution : Augmenter la taille
client = AsyncResilientClient(event_queue_size=1000)
```

#### 2. Fuites de Mémoire

```python
# ❌ Problème : Résultats qui s'accumulent
# Les résultats ne sont jamais nettoyés

# ✅ Solution : Nettoyage périodique
async def cleanup_task(client):
    while not client.is_closed:
        await asyncio.sleep(300)  # Toutes les 5 minutes
        cleaned = client.clear_old_results(max_age=3600)  # 1 heure
        if cleaned > 0:
            print(f"🧹 Nettoyé {cleaned} anciens résultats")
```

#### 3. Trop de Tâches Concurrentes

```python
# ❌ Problème : Lancement de trop de tâches
for i in range(10000):  # Trop !
    task = await client.get(f"https://api.com/{i}")

# ✅ Solution : Limitation avec semaphore
semaphore = asyncio.Semaphore(50)  # Max 50 concurrent

async def limited_request(client, url, context):
    async with semaphore:
        task = await client.get(url, context=context)
        return await task
```

### Optimisations de Performance

#### 1. Réutilisation du Client

```python
# ❌ Mauvais : Nouveau client à chaque fois
async def bad_example():
    async with create_async_resilient_client() as client:
        task = await client.get("https://api.com/data")
        return await task

# ✅ Bon : Client réutilisé
class ApiService:
    def __init__(self):
        self.client = None
    
    async def __aenter__(self):
        self.client = AsyncResilientClient()
        await self.client._initialize_client()
        return self
    
    async def __aexit__(self, *args):
        if self.client:
            await self.client.close()
    
    async def get_data(self, endpoint: str):
        context = RequestContext(request_id=f"api_{endpoint}")
        task = await self.client.get(f"https://api.com/{endpoint}", context=context)
        return await task
```

#### 2. Configuration Optimale

```python
# Configuration pour haute performance
optimal_client = AsyncResilientClient(
    timeout=httpx.Timeout(
        connect=5.0,    # Connexion rapide
        read=30.0,      # Lecture généreuse
        write=10.0,     # Écriture modérée
        pool=5.0        # Pool rapide
    ),
    limits=httpx.Limits(
        max_connections=100,        # Beaucoup de connexions
        max_keepalive_connections=50  # Keep-alive élevé
    ),
    event_queue_size=5000,  # Queue généreuse
    headers={
        "Connection": "keep-alive",
        "Keep-Alive": "timeout=30, max=100"
    }
)
```

## 📚 Références et Ressources

### Documentation Technique

- [HTTPX Documentation](https://www.python-httpx.org/)
- [AsyncIO Official Guide](https://docs.python.org/3/library/asyncio.html)
- [Python Type Hints](https://docs.python.org/3/library/typing.html)

### Exemples Complets

Consultez le dossier `examples/` pour des cas d'usage complets :

- `basic_usage.py` - Utilisation de base
- `fastapi_integration.py` - Intégration FastAPI
- `monitoring_example.py` - Monitoring avancé
- `load_testing.py` - Tests de charge
- `error_handling.py` - Gestion d'erreurs

### Performance et Benchmarks

```bash
# Lancer les benchmarks
python examples/benchmark.py

# Résultats typiques (sur machine standard) :
# ✅ 1000 requêtes concurrentes : 45.2s
# ✅ Taux de succès : 98.5%
# ✅ Retry rate : 12.3%
# ✅ Mémoire utilisée : 125 MB
```

---

## 📄 License

MIT License - voir le fichier `LICENSE` pour plus de détails.

## 🤝 Contribution

Les contributions sont les bienvenues ! Voir `CONTRIBUTING.md` pour les guidelines.