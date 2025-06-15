# Client HTTP Asynchrone Résilient
author: Michaël Launay
date: 2025-06-12
subject: Resilient python asynchronous http client
mail:

# FR-fr

## Objectif
Réponse à : Ecrire un client Python basé sur httpx.AsyncClient qui gère une stratégie de retry, par exemple qui est résilient à 2 httpcore.ConnectError.  Les tests unitaires de ce client doivent vérifier si le bon nombre de retry est bien pris en compte.


[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Type checked: mypy](https://img.shields.io/badge/type%20checked-mypy-blue.svg)](http://mypy-lang.org/)

Un client HTTP asynchrone résilient basé sur `httpx.AsyncClient` avec stratégie de retry intelligente et gestion d'événements par queue asynchrone.

## Architecture

Ce client implémente une **stratégie basée sur le passage de contexte** où chaque **coroutine asynchrone** possède son propre contexte isolé et donc son compteur de tentatives indépendant.

### Principe de Fonctionnement

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Coroutine 1   │    │   Coroutine 2   │    │   Coroutine N   │
│                 │    │                 │    │                 │
│ RequestContext  │    │ RequestContext  │    │ RequestContext  │
│ ├─ max_retries  │    │ ├─ max_retries  │    │ ├─ max_retries  │
│ ├─ attempts: 0  │    │ ├─ attempts: 0  │    │ ├─ attempts: 0  │
│ └─ request_id   │    │ └─ request_id   │    │ └─ request_id   │
└─────────┬───────┘    └─────────┬───────┘    └─────────┬───────┘
          │                      │                      │
          └──────────────────────┼──────────────────────┘
                                 │
                    ┌─────────────▼─────────────┐
                    │     asyncio.Queue         │
                    │   (Communication)         │
                    │                           │
                    │ ┌─────────────────────┐   │
                    │ │ AsyncEvent(SUCCESS) │   │
                    │ │ AsyncEvent(RETRY)   │   │
                    │ │ AsyncEvent(ERROR)   │   │
                    │ └─────────────────────┘   │
                    └─────────────┬─────────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │   Event Loop Monitor      │
                    │   (Coroutine centrale)    │
                    │                           │
                    │ • Traite les événements   │
                    │ • Appelle les callbacks   │
                    │ • Met à jour les résultats│
                    └───────────────────────────┘
```

### Cycle de Vie d'une Requête

1. **Création du contexte** : Chaque coroutine reçoit un `RequestContext` immutable avec ses paramètres de retry
2. **Exécution de la requête** : Tentative de connexion HTTP via `httpx.AsyncClient`
3. **Gestion des erreurs** :
   - **Succès** -> Événement `SUCCESS` envoyé à la queue
   - **`httpcore.ConnectError`** -> Incrément du compteur, calcul du délai, retry automatique
   - **Autres erreurs** -> Pas de retry, événement `ERROR` immédiat
4. **Communication centralisée** : L'event loop monitor traite les événements et met à jour les résultats
5. **Arrêt automatique** : Quand le maximum de tentatives est atteint, la coroutine s'arrête et signale l'échec définitif

### Isolation par Contexte

Le **contexte est une dataclass immutable** (`@dataclass(frozen=True)`) dont les instances sont **systématiquement clonées** lors des passages entre les étapes :

```python
@dataclass(frozen=True)
class RequestContext:
    max_retries: int = 2
    current_attempt: int = 0
    base_delay: float = 1.0
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def clone(self) -> 'RequestContext':
        """Clone sécurisé pour passage entre coroutines."""
        return deepcopy(self)
    
    def increment_attempt(self) -> 'RequestContext':
        """Nouveau contexte avec tentative incrémentée."""
        return self.__class__(
            max_retries=self.max_retries,
            current_attempt=self.current_attempt + 1,
            # ... autres champs
        )
```

### Queue de Communication

Les **événements sont également clonés** dans la queue de communication pour éviter toute modification concurrente :

```python
@dataclass(frozen=True)
class AsyncEvent:
    event_type: str  # SUCCESS, RETRY, ERROR, TIMEOUT, CANCELLED
    context: RequestContext
    timestamp: float = field(default_factory=time.time)
    data: Optional[Any] = None
    error: Optional[Exception] = None
    
    def clone(self) -> 'AsyncEvent':
        """Clone sécurisé pour communication inter-coroutines."""
        return deepcopy(self)
```

## Installation

```bash
# Installation de base
pip install httpx

# Avec support HTTP/2 (recommandé)
pip install httpx[http2]

# Pour le développement et les tests
pip install httpx[http2] pytest pytest-asyncio pytest-cov
```

## Utilisation Rapide

### Exemple Simple

```python
import asyncio
from typed_async_client import simple_async_request

async def exemple_basique():
    # Requête avec retry automatique sur ConnectError
    response = await simple_async_request(
        "GET",
        "https://www.logikascium.com/",
        max_retries=2,
        timeout=10.0
    )
    
    print(f"Status: {response.status_code}")
    return response.json()

# Lancement
result = asyncio.run(exemple_basique())
```

### Client Avancé avec Contextes Personnalisés

```python
import asyncio
from typed_async_client import (
    create_async_resilient_client, 
    RequestContext, 
    EventType
)

async def exemple_avance():
    # Callback pour monitoring des retries
    async def on_retry(event):
        print(f"Retry {event.context.current_attempt}/{event.context.max_retries} "
              f"pour {event.context.request_id}")
    
    async with create_async_resilient_client() as client:
        # Ajouter le monitoring
        client.add_event_callback(EventType.RETRY, on_retry)
        
        # Contexte personnalisé pour cette requête
        context = RequestContext(
            max_retries=3,
            base_delay=0.5,
            request_id="api_critical_data",
            metadata={"priority": "high", "service": "user-api"}
        )
        
        # Lancer la requête (non-bloquante)
        task = await client.get(
            "https://api.example.com/critical-data",
            context=context,
            headers={"Authorization": "Bearer TOKEN"}
        )
        
        # Attendre le résultat
        response = await task
        
        # Récupérer les métriques de la requête
        result = client.get_result("api_critical_data")
        print(f"Succès après {result.attempts} tentatives en {result.total_time:.2f}s")

asyncio.run(exemple_avance())
```

### Requêtes Concurrentes avec Contextes Isolés

```python
import asyncio
from typed_async_client import create_async_resilient_client, RequestContext

async def requetes_paralleles():
    # Chaque service a ses propres paramètres de retry
    configurations = [
        ("https://api.users.com/list", RequestContext(
            request_id="users_service", 
            max_retries=2, 
            base_delay=0.5
        )),
        ("https://api.orders.com/recent", RequestContext(
            request_id="orders_service", 
            max_retries=4,  # Service moins fiable
            base_delay=1.0
        )),
        ("https://api.notifications.com/pending", RequestContext(
            request_id="notifications_service", 
            max_retries=1,  # Service rapide ou rien
            base_delay=0.2
        )),
    ]
    
    async with create_async_resilient_client() as client:
        # Lancer toutes les requêtes en parallèle
        tasks = []
        for url, context in configurations:
            task = await client.get(url, context=context)
            tasks.append((context.request_id, task))
        
        # Attendre toutes les réponses (même en cas d'erreur)
        results = await asyncio.gather(
            *[task for _, task in tasks], 
            return_exceptions=True
        )
        
        # Analyser les résultats individuels
        for (service_id, _), result in zip(tasks, results):
            if isinstance(result, Exception):
                prit(f"[X] {service_id}: {type(result).__name__}")
            else:
                print(f"[V] {service_id}: {result.status_code}")
        
        # Récupérer les métriques détaillées
        all_results = await client.wait_for_all_requests()
        for service_id, metrics in all_results.items():
            print(f"{service_id}: {metrics.attempts} tentatives, "
                  f"{metrics.total_time:.2f}s")

asyncio.run(requetes_paralleles())
```

## Fonctionnalités Principales

### **Stratégie de Retry Intelligente**
- Retry **uniquement** sur `httpcore.ConnectError` (erreurs de connexion)
- Backoff exponentiel avec jitter anti-thundering herd
- Pas de retry sur les erreurs HTTP (4xx, 5xx) ou timeouts

### **Isolation Complète par Contexte**
- Chaque coroutine a son propre `RequestContext` immutable
- Clonage systématique pour éviter les race conditions
- Compteurs de tentatives indépendants

### **Communication Asynchrone Centralisée**
- Queue `asyncio.Queue` pour événements inter-coroutines
- Event loop monitor dédié au traitement des événements
- Callbacks personnalisables pour monitoring

### **Types Complets et Sécurité**
- Annotations de type exhaustives (`typing`, `Final`)
- Dataclasses immutables (`frozen=True`)
- Validation automatique des paramètres

### Performance et Monitoring
- Support HTTP/2 natif via httpx
- Métriques détaillées par requête
- Gestion automatique des ressources

## Tests et Validation

### Structure des Tests

```bash
# Tests unitaires (vérifient le nombre exact de retries)
pytest comprehensive_tests.py::TestAsyncResilientClient::test_connect_error_max_retries_exceeded -v

# Tests de concurrence
pytest comprehensive_tests.py::TestAsyncResilientClient::test_concurrent_requests -v

# Tests d'intégration (nécessitent internet)
pytest comprehensive_tests.py::TestIntegration -v -m integration

# Couverture complète
pytest comprehensive_tests.py --cov=typed_async_client --cov-report=html
```

### Validation des Retries

Les tests vérifient précisément que :
- Une requête réussie = **0 retry**
- 1 `ConnectError` puis succès = **1 retry** 
- `max_retries=2` -> **3 tentatives maximum** (1 initiale + 2 retries)
- Les autres erreurs (timeout, 404, etc.) = **0 retry**

```python
@pytest.mark.asyncio
async def test_exact_retry_count():
    async with AsyncResilientClient() as client:
        context = RequestContext(max_retries=2, request_id="test")
        
        with patch.object(client._client, 'request') as mock:
            # Simuler : 2 échecs ConnectError + 1 succès
            mock.side_effect = [
                httpcore.ConnectError("Fail 1"),
                httpcore.ConnectError("Fail 2"), 
                MagicMock(status_code=200)  # Succès
            ]
            
            task = await client.get("https://test.com", context=context)
            response = await task
            
            # Vérifications exactes
            assert response.status_code == 200
            assert mock.call_count == 3  # 1 initiale + 2 retries
            
            result = client.get_result("test")
            assert result.attempts == 3  # Nombre exact vérifié
```

## Architecture Technique

### Communication Inter-Coroutines

```python
# 1. Coroutine de requête
async def _execute_request_coroutine(self, method, url, context, **kwargs):
    while context.has_retries_left:
        try:
            response = await self._client.request(method, url, **kwargs)
            # Succès -> Événement SUCCESS
            await self._put_event(AsyncEvent(EventType.SUCCESS, context, data=response))
            return response
        except httpcore.ConnectError as e:
            context = context.increment_attempt()  # Nouveau contexte
            if not context.has_retries_left:
                # Échec définitif -> Événement ERROR
                await self._put_event(AsyncEvent(EventType.ERROR, context, error=e))
                raise e
            # Retry -> Événement RETRY + délai
            await self._put_event(AsyncEvent(EventType.RETRY, context))
            await asyncio.sleep(context.calculate_delay())

# 2. Event loop monitor (coroutine centrale)
async def _event_monitor(self):
    while not self._is_closed:
        event = await self._event_queue.get()  # Récupération d'événement
        await self._process_event(event)       # Traitement centralisé
        self._event_queue.task_done()          # Marquage terminé
```

### Garanties d'Isolation

- **Immutabilité** : `@dataclass(frozen=True)` empêche la modification
- **Clonage systématique** : `deepcopy()` à chaque passage
- **Contextes indépendants** : Aucun partage d'état entre coroutines
- **Communication sécurisée** : Queue thread-safe d'asyncio

## Cas d'Usage Avancés

### Intégration FastAPI

```python
from fastapi import FastAPI
from typed_async_client import AsyncResilientClient, RequestContext

app = FastAPI()
http_client = AsyncResilientClient()

@app.on_event("startup")
async def startup():
    await http_client._initialize_client()

@app.get("/api/data/{data_id}")
async def get_external_data(data_id: str):
    context = RequestContext(
        request_id=f"external_api_{data_id}",
        max_retries=2,
        metadata={"endpoint": "external_api", "user_request": True}
    )
    
    task = await http_client.get(
        f"https://external-api.com/data/{data_id}",
        context=context
    )
    response = await task
    return response.json()
```

### Monitoring en Temps Réel

```python
from collections import defaultdict

class MetricsCollector:
    def __init__(self):
        self.stats = defaultdict(int)
    
    async def on_retry(self, event):
        self.stats['total_retries'] += 1
        print(f"🔄 Retry #{event.context.current_attempt} pour {event.context.request_id}")
    
    async def on_error(self, event):
        self.stats['total_errors'] += 1
        print(f"Échec définitif pour {event.context.request_id}")

# Utilisation
metrics = MetricsCollector()
async with create_async_resilient_client() as client:
    client.add_event_callback(EventType.RETRY, metrics.on_retry)
    client.add_event_callback(EventType.ERROR, metrics.on_error)
    # ... vos requêtes
```

## License

AGPL v2 License - voir le fichier [LICENSE](LICENSE) pour plus de détails.


