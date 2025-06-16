"""
Client HTTP asynchrone résilient avec annotations de type complètes.

Ce module implémente un client HTTP asynchrone utilisant httpx.AsyncClient
avec gestion de contexte par coroutine et communication via queue d'événements.
Toutes les fonctions et méthodes sont entièrement typées.
"""

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from typing import (
    Any, AsyncContextManager, AsyncGenerator, AsyncIterator, Awaitable,
    Callable, Dict, Final, List, Optional, Set, Tuple, Type, Union
)

import httpx
import httpcore

# Import des constantes
from async_client_constants import (
    DEFAULT_MAX_RETRIES, DEFAULT_BASE_DELAY, DEFAULT_MAX_DELAY,
    DEFAULT_BACKOFF_FACTOR, DEFAULT_CONNECT_TIMEOUT, DEFAULT_READ_TIMEOUT,
    DEFAULT_WRITE_TIMEOUT, DEFAULT_POOL_TIMEOUT, DEFAULT_MAX_CONNECTIONS,
    DEFAULT_MAX_KEEPALIVE_CONNECTIONS, DEFAULT_EVENT_QUEUE_SIZE,
    EVENT_QUEUE_TIMEOUT, JITTER_PERCENTAGE, EventType, ResultStatus,
    HttpMethod, SUPPORTED_HTTP_METHODS, DEFAULT_HEADERS, ErrorMessage,
    MAX_CONCURRENT_TASKS, RESULT_TTL
)

# Types personnalisés
RequestId = str
CoroutineId = str
HttpHeaders = Dict[str, str]
HttpParams = Dict[str, Union[str, int, float, bool]]
JsonData = Union[Dict[str, Any], List[Any], str, int, float, bool, None]
RequestKwargs = Dict[str, Any]

# Type pour les callbacks d'événements
EventCallback = Callable[['AsyncEvent'], Awaitable[None]]

logger: Final[logging.Logger] = logging.getLogger(__name__)


@dataclass(frozen=True)
class RequestContext:
    """
    Contexte de requête immutable pour chaque coroutine.
    
    Attributes:
        max_retries: Nombre maximum de tentatives de retry
        current_attempt: Tentative actuelle (0 = première tentative)
        base_delay: Délai de base entre les tentatives en secondes
        backoff_factor: Facteur multiplicateur pour le délai exponentiel
        max_delay: Délai maximum entre les tentatives
        request_id: Identifiant unique de la requête
        created_at: Timestamp de création du contexte
        metadata: Métadonnées additionnelles
    """
    max_retries: int = DEFAULT_MAX_RETRIES
    current_attempt: int = 0
    base_delay: float = DEFAULT_BASE_DELAY
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR
    max_delay: float = DEFAULT_MAX_DELAY
    request_id: RequestId = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def clone(self) -> 'RequestContext':
        """
        Clone le contexte pour passage sécurisé entre coroutines.
        
        Returns:
            Nouvelle instance de RequestContext identique
            
        Raises:
            RuntimeError: Si le clonage échoue
        """
        try:
            return deepcopy(self)
        except Exception as e:
            raise RuntimeError(ErrorMessage.CONTEXT_CLONE_FAILED) from e
    
    def increment_attempt(self) -> 'RequestContext':
        """
        Incrémente le compteur de tentatives et retourne un nouveau contexte.
        
        Returns:
            Nouveau contexte avec attempt + 1
        """
        return self.__class__(
            max_retries=self.max_retries,
            current_attempt=self.current_attempt + 1,
            base_delay=self.base_delay,
            backoff_factor=self.backoff_factor,
            max_delay=self.max_delay,
            request_id=self.request_id,
            created_at=self.created_at,
            metadata=self.metadata.copy()
        )
    
    def reset_attempts(self) -> 'RequestContext':
        """
        Remet à zéro le compteur de tentatives.
        
        Returns:
            Nouveau contexte avec attempt = 0
        """
        return self.__class__(
            max_retries=self.max_retries,
            current_attempt=0,
            base_delay=self.base_delay,
            backoff_factor=self.backoff_factor,
            max_delay=self.max_delay,
            request_id=self.request_id,
            created_at=self.created_at,
            metadata=self.metadata.copy()
        )
    
    def calculate_delay(self) -> float:
        """
        Calcule le délai d'attente pour la tentative courante.
        
        Returns:
            Délai en secondes avec jitter appliqué
        """
        if self.current_attempt == 0:
            return 0.0
            
        # Délai exponentiel de base
        delay: float = self.base_delay * (self.backoff_factor ** (self.current_attempt - 1))
        
        # Application du jitter pour éviter l'effet "thundering herd"
        jitter: float = delay * JITTER_PERCENTAGE * (0.5 - (time.time() % 1))
        final_delay: float = min(delay + jitter, self.max_delay)
        
        return max(0.0, final_delay)
    
    @property
    def has_retries_left(self) -> bool:
        """
        Vérifie s'il reste des tentatives disponibles.
        
        Returns:
            True s'il reste des tentatives
        """
        return self.current_attempt <= self.max_retries
    
    @property
    def elapsed_time(self) -> float:
        """
        Temps écoulé depuis la création du contexte.
        
        Returns:
            Temps en secondes
        """
        return time.time() - self.created_at


@dataclass(frozen=True)
class AsyncEvent:
    """
    Événement pour communication inter-coroutines.
    
    Attributes:
        event_type: Type d'événement (EventType)
        context: Contexte de la requête associée
        timestamp: Horodatage de l'événement
        data: Données optionnelles (réponse HTTP, etc.)
        error: Exception optionnelle
        metadata: Métadonnées additionnelles
    """
    event_type: str
    context: RequestContext
    timestamp: float = field(default_factory=time.time)
    data: Optional[Any] = None
    error: Optional[Exception] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def clone(self) -> 'AsyncEvent':
        """
        Clone l'événement pour passage sécurisé.
        
        Returns:
            Nouvelle instance d'AsyncEvent
        """
        return deepcopy(self)
    
    @property
    def age(self) -> float:
        """
        Âge de l'événement en secondes.
        
        Returns:
            Temps écoulé depuis la création
        """
        return time.time() - self.timestamp


@dataclass
class RequestResult:
    """
    Résultat d'une requête HTTP.
    
    Attributes:
        request_id: Identifiant de la requête
        status: Statut du résultat (ResultStatus)
        response: Réponse HTTP si succès
        error: Exception si erreur
        attempts: Nombre de tentatives effectuées
        total_time: Temps total d'exécution
        created_at: Timestamp de création
    """
    request_id: RequestId
    status: str
    response: Optional[httpx.Response] = None
    error: Optional[Exception] = None
    attempts: int = 0
    total_time: float = 0.0
    created_at: float = field(default_factory=time.time)
    
    @property
    def is_success(self) -> bool:
        """Vérifie si le résultat est un succès."""
        return self.status == ResultStatus.SUCCESS
    
    @property
    def is_error(self) -> bool:
        """Vérifie si le résultat est une erreur."""
        return self.status == ResultStatus.ERROR
    
    @property
    def age(self) -> float:
        """Âge du résultat en secondes."""
        return time.time() - self.created_at


class AsyncResilientClient:
    """
    Client HTTP asynchrone résilient avec gestion de contexte par coroutine.
    
    Ce client gère automatiquement les retries sur ConnectError et communique
    via une queue d'événements asynchrone pour un monitoring centralisé.
    """
    
    def __init__(
        self,
        timeout: Optional[httpx.Timeout] = None,
        limits: Optional[httpx.Limits] = None,
        headers: Optional[HttpHeaders] = None,
        event_queue_size: int = DEFAULT_EVENT_QUEUE_SIZE,
        **httpx_kwargs: Any
    ) -> None:
        """
        Initialise le client asynchrone résilient.
        
        Args:
            timeout: Configuration des timeouts httpx
            limits: Limites de connexion httpx
            headers: Headers par défaut
            event_queue_size: Taille de la queue d'événements
            **httpx_kwargs: Arguments supplémentaires pour httpx.AsyncClient
        """
        # Configuration du client httpx
        self._timeout: httpx.Timeout = timeout or httpx.Timeout(
            connect=DEFAULT_CONNECT_TIMEOUT,
            read=DEFAULT_READ_TIMEOUT,
            write=DEFAULT_WRITE_TIMEOUT,
            pool=DEFAULT_POOL_TIMEOUT
        )
        
        self._limits: httpx.Limits = limits or httpx.Limits(
            max_connections=DEFAULT_MAX_CONNECTIONS,
            max_keepalive_connections=DEFAULT_MAX_KEEPALIVE_CONNECTIONS
        )
        
        self._default_headers: HttpHeaders = {**DEFAULT_HEADERS}
        if headers:
            self._default_headers.update(headers)
        
        self._httpx_kwargs: RequestKwargs = httpx_kwargs
        
        # État interne
        self._client: Optional[httpx.AsyncClient] = None
        self._event_queue: asyncio.Queue[AsyncEvent] = asyncio.Queue(maxsize=event_queue_size)
        self._active_tasks: Set[asyncio.Task[Any]] = set()
        self._results: Dict[RequestId, RequestResult] = {}
        self._monitor_task: Optional[asyncio.Task[None]] = None
        self._is_closed: bool = False
        
        # Callbacks pour les événements
        self._event_callbacks: Dict[str, List[EventCallback]] = {
            EventType.SUCCESS: [],
            EventType.ERROR: [],
            EventType.RETRY: [],
            EventType.TIMEOUT: [],
            EventType.CANCELLED: []
        }
    
    async def __aenter__(self) -> 'AsyncResilientClient':
        """Entrée du context manager asynchrone."""
        await self._initialize_client()
        return self
    
    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[Any]
    ) -> None:
        """Sortie du context manager asynchrone."""
        await self.close()
    
    async def _initialize_client(self) -> None:
        """Initialise le client HTTP et démarre le monitoring."""
        if self._client is not None:
            return
        
        # Créer le client httpx
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            limits=self._limits,
            headers=self._default_headers,
            **self._httpx_kwargs
        )
        
        # Démarrer la coroutine de monitoring des événements
        self._monitor_task = asyncio.create_task(self._event_monitor())
        self._is_closed = False
        
        logger.debug("Client HTTP asynchrone initialisé")
    
    async def close(self) -> None:
        """
        Ferme proprement le client et toutes les ressources associées.
        
        Annule toutes les tâches actives et ferme le client HTTP.
        """
        if self._is_closed:
            return
        
        self._is_closed = True
        
        # Annuler toutes les tâches actives
        for task in self._active_tasks.copy():
            if not task.done():
                task.cancel()
        
        # Attendre l'arrêt de toutes les tâches
        if self._active_tasks:
            await asyncio.gather(*self._active_tasks, return_exceptions=True)
        
        # Arrêter le monitoring
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        
        # Fermer le client HTTP
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        
        logger.debug("Client HTTP asynchrone fermé")
    
    async def _event_monitor(self) -> None:
        """
        Coroutine de monitoring des événements.
        
        Traite les événements de la queue et appelle les callbacks appropriés.
        """
        try:
            while not self._is_closed:
                try:
                    # Attendre un événement avec timeout
                    event: AsyncEvent = await asyncio.wait_for(
                        self._event_queue.get(),
                        timeout=EVENT_QUEUE_TIMEOUT
                    )
                    
                    # Traiter l'événement
                    await self._process_event(event)
                    
                    # Marquer la tâche comme terminée
                    self._event_queue.task_done()
                    
                except asyncio.TimeoutError:
                    # Timeout normal, continuer la boucle
                    continue
                    
        except asyncio.CancelledError:
            logger.debug("Monitoring des événements annulé")
        except Exception as e:
            logger.error(f"Erreur dans le monitoring d'événements: {e}")
    
    async def _process_event(self, event: AsyncEvent) -> None:
        """
        Traite un événement de la queue.
        
        Args:
            event: Événement à traiter
        """
        # Traitement selon le type d'événement
        if event.event_type == EventType.SUCCESS:
            await self._handle_success_event(event)
        elif event.event_type == EventType.ERROR:
            await self._handle_error_event(event)
        elif event.event_type == EventType.RETRY:
            await self._handle_retry_event(event)
        elif event.event_type == EventType.TIMEOUT:
            await self._handle_timeout_event(event)
        elif event.event_type == EventType.CANCELLED:
            await self._handle_cancelled_event(event)
        
        # Appeler les callbacks personnalisés
        callbacks: List[EventCallback] = self._event_callbacks.get(event.event_type, [])
        for callback in callbacks:
            try:
                await callback(event)
            except Exception as e:
                logger.error(f"Erreur dans callback d'événement {event.event_type}: {e}")
    
    async def _handle_success_event(self, event: AsyncEvent) -> None:
        """Traite les événements de succès."""
        result = RequestResult(
            request_id=event.context.request_id,
            status=ResultStatus.SUCCESS,
            response=event.data,
            attempts=event.context.current_attempt + 1,
            total_time=event.context.elapsed_time
        )
        self._results[event.context.request_id] = result
        
        logger.debug(
            f"Succès pour {event.context.request_id} "
            f"après {event.context.current_attempt + 1} tentatives"
        )
    
    async def _handle_error_event(self, event: AsyncEvent) -> None:
        """Traite les événements d'erreur."""
        result = RequestResult(
            request_id=event.context.request_id,
            status=ResultStatus.ERROR,
            error=event.error,
            attempts=event.context.current_attempt + 1,
            total_time=event.context.elapsed_time
        )
        self._results[event.context.request_id] = result
        
        logger.warning(
            f"Erreur définitive pour {event.context.request_id}: {event.error}"
        )
    
    async def _handle_retry_event(self, event: AsyncEvent) -> None:
        """Traite les événements de retry."""
        logger.debug(
            f"Retry {event.context.current_attempt}/{event.context.max_retries} "
            f"pour {event.context.request_id}"
        )
    
    async def _handle_timeout_event(self, event: AsyncEvent) -> None:
        """Traite les événements de timeout."""
        result = RequestResult(
            request_id=event.context.request_id,
            status=ResultStatus.TIMEOUT,
            error=event.error,
            attempts=event.context.current_attempt + 1,
            total_time=event.context.elapsed_time
        )
        self._results[event.context.request_id] = result
        
        logger.warning(f"Timeout pour {event.context.request_id}")
    
    async def _handle_cancelled_event(self, event: AsyncEvent) -> None:
        """Traite les événements d'annulation."""
        result = RequestResult(
            request_id=event.context.request_id,
            status=ResultStatus.CANCELLED,
            attempts=event.context.current_attempt,
            total_time=event.context.elapsed_time
        )
        self._results[event.context.request_id] = result
        
        logger.debug(f"Requête annulée: {event.context.request_id}")
    
    async def _execute_request_coroutine(
        self,
        method: str,
        url: str,
        context: RequestContext,
        **kwargs: Any
    ) -> httpx.Response:
        """
        Coroutine individuelle pour exécuter une requête avec retry automatique.
        
        Args:
            method: Méthode HTTP
            url: URL de destination
            context: Contexte de la requête
            **kwargs: Arguments supplémentaires pour httpx
            
        Returns:
            Réponse HTTP en cas de succès
            
        Raises:
            httpcore.ConnectError: Si toutes les tentatives échouent
            Exception: Pour les autres types d'erreurs
        """
        if self._client is None:
            raise RuntimeError(ErrorMessage.CLIENT_NOT_INITIALIZED)
        
        current_context: RequestContext = context.clone()
        
        while current_context.has_retries_left:
            try:
                # Signaler une tentative si ce n'est pas la première
                if current_context.current_attempt > 0:
                    retry_event = AsyncEvent(
                        event_type=EventType.RETRY,
                        context=current_context.clone()
                    )
                    await self._put_event(retry_event)
                
                # Exécuter la requête
                response: httpx.Response = await self._client.request(
                    method, url, **kwargs
                )
                
                # Succès : signaler et retourner
                success_event = AsyncEvent(
                    event_type=EventType.SUCCESS,
                    context=current_context.clone(),
                    data=response
                )
                await self._put_event(success_event)
                
                return response
                
            except httpcore.ConnectError as e:
                current_context = current_context.increment_attempt()
                
                # Vérifier si on a épuisé les tentatives
                if not current_context.has_retries_left:
                    # Signaler l'erreur définitive
                    error_event = AsyncEvent(
                        event_type=EventType.ERROR,
                        context=current_context.clone(),
                        error=e
                    )
                    await self._put_event(error_event)
                    raise e
                
                # Attendre avant le prochain essai
                delay: float = current_context.calculate_delay()
                if delay > 0:
                    await asyncio.sleep(delay)
                
            except asyncio.TimeoutError as e:
                # Timeout : signaler et relancer
                timeout_event = AsyncEvent(
                    event_type=EventType.TIMEOUT,
                    context=current_context.clone(),
                    error=e
                )
                await self._put_event(timeout_event)
                raise e
                
            except Exception as e:
                # Autres erreurs : pas de retry, signaler immédiatement
                error_event = AsyncEvent(
                    event_type=EventType.ERROR,
                    context=current_context.clone(),
                    error=e
                )
                await self._put_event(error_event)
                raise e
        
        # Cette ligne ne devrait jamais être atteinte
        raise RuntimeError("État incohérent dans _execute_request_coroutine")
    
    async def _put_event(self, event: AsyncEvent) -> None:
        """
        Place un événement dans la queue de manière sécurisée.
        
        Args:
            event: Événement à placer dans la queue
            
        Raises:
            RuntimeError: Si la queue est pleine
        """
        try:
            self._event_queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.error(ErrorMessage.EVENT_QUEUE_FULL)
            # En cas de queue pleine, on peut soit bloquer soit ignorer
            # Ici on choisit de bloquer un court instant
            try:
                await asyncio.wait_for(
                    self._event_queue.put(event),
                    timeout=1.0
                )
            except asyncio.TimeoutError:
                raise RuntimeError(ErrorMessage.EVENT_QUEUE_FULL)
    
    def add_event_callback(self, event_type: str, callback: EventCallback) -> None:
        """
        Ajoute un callback pour un type d'événement.
        
        Args:
            event_type: Type d'événement (EventType)
            callback: Fonction callback asynchrone
        """
        if event_type in self._event_callbacks:
            self._event_callbacks[event_type].append(callback)
        else:
            logger.warning(f"Type d'événement inconnu: {event_type}")
    
    def remove_event_callback(self, event_type: str, callback: EventCallback) -> bool:
        """
        Supprime un callback pour un type d'événement.
        
        Args:
            event_type: Type d'événement
            callback: Fonction callback à supprimer
            
        Returns:
            True si le callback a été supprimé, False sinon
        """
        if event_type in self._event_callbacks:
            try:
                self._event_callbacks[event_type].remove(callback)
                return True
            except ValueError:
                return False
        return False
    
    async def request_async(
        self,
        method: str,
        url: str,
        context: Optional[RequestContext] = None,
        **kwargs: Any
    ) -> asyncio.Task[httpx.Response]:
        """
        Lance une coroutine de requête de manière asynchrone.
        
        Args:
            method: Méthode HTTP (GET, POST, etc.)
            url: URL de destination
            context: Contexte de requête optionnel
            **kwargs: Arguments supplémentaires pour httpx
            
        Returns:
            Task asyncio représentant la requête en cours
            
        Raises:
            ValueError: Si la méthode HTTP n'est pas supportée
            RuntimeError: Si le client n'est pas initialisé
            RuntimeError: Si le nombre maximum de tâches est atteint
        """
        if self._is_closed:
            raise RuntimeError("Le client est fermé")
        
        # Validation de la méthode HTTP
        if method.upper() not in SUPPORTED_HTTP_METHODS:
            raise ValueError(ErrorMessage.INVALID_HTTP_METHOD.format(method=method))
        
        # Vérification du nombre de tâches concurrentes
        if len(self._active_tasks) >= MAX_CONCURRENT_TASKS:
            raise RuntimeError(f"Nombre maximum de tâches concurrentes atteint: {MAX_CONCURRENT_TASKS}")
        
        # Créer un contexte par défaut si nécessaire
        if context is None:
            context = RequestContext(request_id=f"req_{len(self._active_tasks)}_{int(time.time())}")
        
        # S'assurer que le client est initialisé
        await self._initialize_client()
        
        # Créer et démarrer la coroutine
        task: asyncio.Task[httpx.Response] = asyncio.create_task(
            self._execute_request_coroutine(method.upper(), url, context, **kwargs)
        )
        
        # Ajouter aux tâches actives
        self._active_tasks.add(task)
        
        # Nettoyer automatiquement quand la tâche se termine
        def cleanup_task(finished_task: asyncio.Task[httpx.Response]) -> None:
            self._active_tasks.discard(finished_task)
            
            # Gérer les annulations
            if finished_task.cancelled():
                cancelled_event = AsyncEvent(
                    event_type=EventType.CANCELLED,
                    context=context,  # type: ignore
                    error=asyncio.CancelledError("Tâche annulée")
                )
                asyncio.create_task(self._put_event(cancelled_event))
        
        task.add_done_callback(cleanup_task)
        
        logger.debug(f"Tâche créée pour {context.request_id}: {method} {url}")
        return task
    
    async def get(
        self,
        url: str,
        context: Optional[RequestContext] = None,
        params: Optional[HttpParams] = None,
        headers: Optional[HttpHeaders] = None,
        **kwargs: Any
    ) -> asyncio.Task[httpx.Response]:
        """
        Effectue une requête GET asynchrone.
        
        Args:
            url: URL de destination
            context: Contexte de requête optionnel
            params: Paramètres de query string
            headers: Headers HTTP additionnels
            **kwargs: Arguments supplémentaires pour httpx
            
        Returns:
            Task asyncio représentant la requête GET
        """
        return await self.request_async(
            HttpMethod.GET, url, context,
            params=params, headers=headers, **kwargs
        )
    
    async def post(
        self,
        url: str,
        context: Optional[RequestContext] = None,
        data: Optional[Any] = None,
        json: Optional[JsonData] = None,
        headers: Optional[HttpHeaders] = None,
        **kwargs: Any
    ) -> asyncio.Task[httpx.Response]:
        """
        Effectue une requête POST asynchrone.
        
        Args:
            url: URL de destination
            context: Contexte de requête optionnel
            data: Données à envoyer (form data)
            json: Données JSON à envoyer
            headers: Headers HTTP additionnels
            **kwargs: Arguments supplémentaires pour httpx
            
        Returns:
            Task asyncio représentant la requête POST
        """
        return await self.request_async(
            HttpMethod.POST, url, context,
            data=data, json=json, headers=headers, **kwargs
        )
    
    async def put(
        self,
        url: str,
        context: Optional[RequestContext] = None,
        data: Optional[Any] = None,
        json: Optional[JsonData] = None,
        headers: Optional[HttpHeaders] = None,
        **kwargs: Any
    ) -> asyncio.Task[httpx.Response]:
        """
        Effectue une requête PUT asynchrone.
        
        Args:
            url: URL de destination
            context: Contexte de requête optionnel
            data: Données à envoyer
            json: Données JSON à envoyer
            headers: Headers HTTP additionnels
            **kwargs: Arguments supplémentaires pour httpx
            
        Returns:
            Task asyncio représentant la requête PUT
        """
        return await self.request_async(
            HttpMethod.PUT, url, context,
            data=data, json=json, headers=headers, **kwargs
        )
    
    async def delete(
        self,
        url: str,
        context: Optional[RequestContext] = None,
        headers: Optional[HttpHeaders] = None,
        **kwargs: Any
    ) -> asyncio.Task[httpx.Response]:
        """
        Effectue une requête DELETE asynchrone.
        
        Args:
            url: URL de destination
            context: Contexte de requête optionnel
            headers: Headers HTTP additionnels
            **kwargs: Arguments supplémentaires pour httpx
            
        Returns:
            Task asyncio représentant la requête DELETE
        """
        return await self.request_async(
            HttpMethod.DELETE, url, context,
            headers=headers, **kwargs
        )
    
    async def patch(
        self,
        url: str,
        context: Optional[RequestContext] = None,
        data: Optional[Any] = None,
        json: Optional[JsonData] = None,
        headers: Optional[HttpHeaders] = None,
        **kwargs: Any
    ) -> asyncio.Task[httpx.Response]:
        """
        Effectue une requête PATCH asynchrone.
        
        Args:
            url: URL de destination
            context: Contexte de requête optionnel
            data: Données à envoyer
            json: Données JSON à envoyer
            headers: Headers HTTP additionnels
            **kwargs: Arguments supplémentaires pour httpx
            
        Returns:
            Task asyncio représentant la requête PATCH
        """
        return await self.request_async(
            HttpMethod.PATCH, url, context,
            data=data, json=json, headers=headers, **kwargs
        )
    
    async def head(
        self,
        url: str,
        context: Optional[RequestContext] = None,
        headers: Optional[HttpHeaders] = None,
        **kwargs: Any
    ) -> asyncio.Task[httpx.Response]:
        """
        Effectue une requête HEAD asynchrone.
        
        Args:
            url: URL de destination
            context: Contexte de requête optionnel
            headers: Headers HTTP additionnels
            **kwargs: Arguments supplémentaires pour httpx
            
        Returns:
            Task asyncio représentant la requête HEAD
        """
        return await self.request_async(
            HttpMethod.HEAD, url, context,
            headers=headers, **kwargs
        )
    
    async def options(
        self,
        url: str,
        context: Optional[RequestContext] = None,
        headers: Optional[HttpHeaders] = None,
        **kwargs: Any
    ) -> asyncio.Task[httpx.Response]:
        """
        Effectue une requête OPTIONS asynchrone.
        
        Args:
            url: URL de destination
            context: Contexte de requête optionnel
            headers: Headers HTTP additionnels
            **kwargs: Arguments supplémentaires pour httpx
            
        Returns:
            Task asyncio représentant la requête OPTIONS
        """
        return await self.request_async(
            HttpMethod.OPTIONS, url, context,
            headers=headers, **kwargs
        )
    
    async def wait_for_all_requests(self) -> Dict[RequestId, RequestResult]:
        """
        Attend que toutes les coroutines actives se terminent.
        
        Returns:
            Dictionnaire des résultats indexés par request_id
        """
        # Attendre toutes les tâches actives
        if self._active_tasks:
            await asyncio.gather(*self._active_tasks, return_exceptions=True)
        
        # Attendre que tous les événements soient traités
        await self._event_queue.join()
        
        return self._results.copy()
    
    async def wait_for_request(
        self,
        request_id: RequestId,
        timeout: Optional[float] = None
    ) -> Optional[RequestResult]:
        """
        Attend le résultat d'une requête spécifique.
        
        Args:
            request_id: Identifiant de la requête
            timeout: Timeout en secondes (None = pas de timeout)
            
        Returns:
            Résultat de la requête ou None si timeout
            
        Raises:
            asyncio.TimeoutError: Si le timeout est dépassé
        """
        start_time: float = time.time()
        
        while True:
            # Vérifier si le résultat est disponible
            if request_id in self._results:
                return self._results[request_id]
            
            # Vérifier le timeout
            if timeout is not None and (time.time() - start_time) >= timeout:
                raise asyncio.TimeoutError(f"Timeout en attente de {request_id}")
            
            # Attendre un court instant avant de revérifier
            await asyncio.sleep(0.1)
    
    def get_result(self, request_id: RequestId) -> Optional[RequestResult]:
        """
        Récupère le résultat d'une requête de manière synchrone.
        
        Args:
            request_id: Identifiant de la requête
            
        Returns:
            Résultat de la requête ou None si non trouvé
        """
        return self._results.get(request_id)
    
    def get_all_results(self) -> Dict[RequestId, RequestResult]:
        """
        Récupère tous les résultats disponibles.
        
        Returns:
            Dictionnaire de tous les résultats
        """
        return self._results.copy()
    
    def clear_old_results(self, max_age: float = RESULT_TTL) -> int:
        """
        Nettoie les anciens résultats en mémoire.
        
        Args:
            max_age: Âge maximum des résultats en secondes
            
        Returns:
            Nombre de résultats supprimés
        """
        current_time: float = time.time()
        old_results: List[RequestId] = [
            request_id for request_id, result in self._results.items()
            if (current_time - result.created_at) > max_age
        ]
        
        for request_id in old_results:
            del self._results[request_id]
        
        logger.debug(f"Nettoyage de {len(old_results)} anciens résultats")
        return len(old_results)
    
    @property
    def active_tasks_count(self) -> int:
        """Nombre de tâches actuellement actives."""
        return len(self._active_tasks)
    
    @property
    def results_count(self) -> int:
        """Nombre de résultats en mémoire."""
        return len(self._results)
    
    @property
    def is_closed(self) -> bool:
        """Indique si le client est fermé."""
        return self._is_closed
    
    @property
    def event_queue_size(self) -> int:
        """Taille actuelle de la queue d'événements."""
        return self._event_queue.qsize()


# Fonction utilitaire pour créer un client avec context manager
@asynccontextmanager
async def create_async_resilient_client(
    **kwargs: Any
) -> AsyncGenerator[AsyncResilientClient, None]:
    """
    Context manager pour créer un client asynchrone résilient.
    
    Args:
        **kwargs: Arguments pour AsyncResilientClient
        
    Yields:
        Instance du client asynchrone
        
    Example:
        async with create_async_resilient_client(max_retries=3) as client:
            task = await client.get("https://api.example.com/data")
            response = await task
    """
    client: AsyncResilientClient = AsyncResilientClient(**kwargs)
    try:
        async with client:
            yield client
    finally:
        # Le cleanup est géré par __aexit__
        pass


# Fonction utilitaire pour les requêtes simples
async def simple_async_request(
    method: str,
    url: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
    timeout: Optional[float] = None,
    **kwargs: Any
) -> httpx.Response:
    """
    Effectue une requête HTTP simple avec retry automatique.
    
    Args:
        method: Méthode HTTP
        url: URL de destination
        max_retries: Nombre maximum de retries
        timeout: Timeout en secondes
        **kwargs: Arguments supplémentaires pour httpx
        
    Returns:
        Réponse HTTP
        
    Raises:
        httpcore.ConnectError: Si toutes les tentatives échouent
        Exception: Pour les autres erreurs HTTP
        
    Example:
        response = await simple_async_request("GET", "https://api.example.com")
    """
    httpx_timeout: Optional[httpx.Timeout] = None
    if timeout is not None:
        httpx_timeout = httpx.Timeout(timeout)
    
    context: RequestContext = RequestContext(
        max_retries=max_retries,
        request_id=f"simple_{int(time.time())}"
    )
    
    async with create_async_resilient_client(timeout=httpx_timeout) as client:
        task: asyncio.Task[httpx.Response] = await client.request_async(
            method, url, context, **kwargs
        )
        return await task


# Exemple d'utilisation complète
async def example_usage() -> None:
    """Exemple d'utilisation du client asynchrone résilient."""
    
    # Callback personnalisé pour les événements de retry
    async def on_retry(event: AsyncEvent) -> None:
        print(f"🔄 Retry détecté: {event.context.request_id} "
              f"(tentative {event.context.current_attempt})")
    
    # Callback pour les erreurs
    async def on_error(event: AsyncEvent) -> None:
        print(f"❌ Erreur: {event.context.request_id} - {event.error}")
    
    # Configuration du client
    timeout: httpx.Timeout = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)
    limits: httpx.Limits = httpx.Limits(max_connections=50)
    
    async with create_async_resilient_client(
        timeout=timeout,
        limits=limits,
        event_queue_size=100
    ) as client:
        
        # Ajouter les callbacks
        client.add_event_callback(EventType.RETRY, on_retry)
        client.add_event_callback(EventType.ERROR, on_error)
        
        # Contextes personnalisés pour différentes requêtes
        contexts: List[RequestContext] = [
            RequestContext(max_retries=2, request_id="api_users", base_delay=0.5),
            RequestContext(max_retries=3, request_id="api_posts", base_delay=1.0),
            RequestContext(max_retries=1, request_id="api_comments", base_delay=0.2)
        ]
        
        urls: List[str] = [
            "https://jsonplaceholder.typicode.com/users",
            "https://jsonplaceholder.typicode.com/posts",
            "https://jsonplaceholder.typicode.com/comments"
        ]
        
        # Lancer toutes les requêtes en parallèle
        tasks: List[asyncio.Task[httpx.Response]] = []
        for url, ctx in zip(urls, contexts):
            task = await client.get(url, context=ctx)
            tasks.append(task)
        
        # Ajouter une requête POST
        post_task = await client.post(
            "https://jsonplaceholder.typicode.com/posts",
            context=RequestContext(request_id="create_post"),
            json={"title": "Test", "body": "Contenu de test", "userId": 1}
        )
        tasks.append(post_task)
        
        print(f"Tâches actives: {client.active_tasks_count}")
        
        # Attendre toutes les requêtes
        results: Dict[RequestId, RequestResult] = await client.wait_for_all_requests()
        
        # Analyser les résultats
        print("Résultats:")
        for request_id, result in results.items():
            status_emoji = "✅" if result.is_success else "❌"
            print(f"{status_emoji} {request_id}: {result.status} "
                  f"({result.attempts} tentatives, {result.total_time:.2f}s)")
            
            if result.is_success and result.response:
                print(f"   Status HTTP: {result.response.status_code}")
        
        # Nettoyer les anciens résultats
        cleaned = client.clear_old_results(max_age=60.0)
        print(f"{cleaned} anciens résultats nettoyés")


# Exemple d'utilisation simple
async def simple_example() -> None:
    """Exemple simple d'utilisation."""
    try:
        # Requête GET simple avec retry automatique
        response: httpx.Response = await simple_async_request(
            "GET",
            "https://httpbin.org/get",
            max_retries=2,
            timeout=10.0
        )
        
        print(f"✅ Succès: {response.status_code}")
        data: JsonData = response.json()
        print(f"📦 Données: {data}")
        
    except httpcore.ConnectError as e:
        print(f"❌ Erreur de connexion: {e}")
    except Exception as e:
        print(f"❌ Autre erreur: {e}")


if __name__ == "__main__":
    # Lancer l'exemple complet
    print("🚀 Lancement de l'exemple complet...")
    asyncio.run(example_usage())
    
    print("\n" + "="*50 + "\n")
    
    # Lancer l'exemple simple
    print("🚀 Lancement de l'exemple simple...")
    asyncio.run(simple_example())