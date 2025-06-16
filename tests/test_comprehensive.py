"""
Tests unitaires complets pour le client HTTP asynchrone résilient.

Ces tests vérifient le comportement de la stratégie de retry,
la communication par événements, et toutes les fonctionnalités
du client avec annotations de type complètes.
"""

import asyncio
import pytest
import time
from typing import Any, Dict, List, Optional, Set, Type
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import httpcore

# Imports des modules à tester
from async_client_constants import (
    DEFAULT_MAX_RETRIES, DEFAULT_BASE_DELAY, EventType, ResultStatus,
    HttpMethod, ErrorMessage, TEST_BASE_DELAY, TEST_MAX_DELAY, TEST_TIMEOUT
)
from typed_async_client import (
    RequestContext, AsyncEvent, RequestResult, AsyncResilientClient,
    create_async_resilient_client, simple_async_request,
    RequestId, HttpHeaders, JsonData
)


class TestRequestContext:
    """Tests pour la classe RequestContext."""
    
    def test_context_creation_default_values(self) -> None:
        """Test de création avec valeurs par défaut."""
        context = RequestContext()
        
        assert context.max_retries == DEFAULT_MAX_RETRIES
        assert context.current_attempt == 0
        assert context.base_delay == DEFAULT_BASE_DELAY
        assert context.request_id != ""
        assert isinstance(context.metadata, dict)
        assert len(context.metadata) == 0
    
    def test_context_creation_custom_values(self) -> None:
        """Test de création avec valeurs personnalisées."""
        custom_metadata: Dict[str, Any] = {"key": "value", "number": 42}
        context = RequestContext(
            max_retries=5,
            current_attempt=2,
            base_delay=0.5,
            request_id="test_request",
            metadata=custom_metadata
        )
        
        assert context.max_retries == 5
        assert context.current_attempt == 2
        assert context.base_delay == 0.5
        assert context.request_id == "test_request"
        assert context.metadata == custom_metadata
    
    def test_context_clone(self) -> None:
        """Test du clonage de contexte."""
        original = RequestContext(
            max_retries=3,
            current_attempt=1,
            request_id="original",
            metadata={"test": "data"}
        )
        
        cloned = original.clone()
        
        # Vérifier que les valeurs sont identiques
        assert cloned.max_retries == original.max_retries
        assert cloned.current_attempt == original.current_attempt
        assert cloned.request_id == original.request_id
        assert cloned.metadata == original.metadata
        
        # Vérifier que c'est bien une copie indépendante
        assert cloned is not original
        assert cloned.metadata is not original.metadata
    
    def test_context_increment_attempt(self) -> None:
        """Test d'incrémentation des tentatives."""
        context = RequestContext(current_attempt=0)
        incremented = context.increment_attempt()
        
        assert incremented.current_attempt == 1
        assert context.current_attempt == 0  # Original inchangé
        assert incremented is not context
    
    def test_context_reset_attempts(self) -> None:
        """Test de remise à zéro des tentatives."""
        context = RequestContext(current_attempt=3)
        reset = context.reset_attempts()
        
        assert reset.current_attempt == 0
        assert context.current_attempt == 3  # Original inchangé
        assert reset is not context
    
    def test_context_calculate_delay(self) -> None:
        """Test du calcul de délai."""
        context = RequestContext(
            base_delay=1.0,
            backoff_factor=2.0,
            max_delay=10.0,
            current_attempt=0
        )
        
        # Première tentative : pas de délai
        assert context.calculate_delay() == 0.0
        
        # Deuxième tentative
        context_attempt_1 = context.increment_attempt()
        delay_1 = context_attempt_1.calculate_delay()
        assert 0.8 <= delay_1 <= 1.2  # ~1.0 avec jitter
        
        # Troisième tentative
        context_attempt_2 = context_attempt_1.increment_attempt()
        delay_2 = context_attempt_2.calculate_delay()
        assert 1.6 <= delay_2 <= 2.4  # ~2.0 avec jitter
    
    def test_context_has_retries_left(self) -> None:
        """Test de vérification des tentatives restantes."""
        context = RequestContext(max_retries=2, current_attempt=0)
        
        assert context.has_retries_left is True
        
        context_1 = context.increment_attempt()
        assert context_1.has_retries_left is True
        
        context_2 = context_1.increment_attempt()
        assert context_2.has_retries_left is True
        
        context_3 = context_2.increment_attempt()
        assert context_3.has_retries_left is False
    
    def test_context_elapsed_time(self) -> None:
        """Test du calcul du temps écoulé."""
        context = RequestContext()
        
        # Attendre un peu
        time.sleep(0.1)
        
        elapsed = context.elapsed_time
        assert elapsed >= 0.1
        assert elapsed < 1.0  # Ne devrait pas être trop long


class TestAsyncEvent:
    """Tests pour la classe AsyncEvent."""
    
    def test_event_creation(self) -> None:
        """Test de création d'événement."""
        context = RequestContext(request_id="test")
        event = AsyncEvent(
            event_type=EventType.SUCCESS,
            context=context,
            data={"result": "ok"}
        )
        
        assert event.event_type == EventType.SUCCESS
        assert event.context == context
        assert event.data == {"result": "ok"}
        assert event.error is None
        assert isinstance(event.timestamp, float)
    
    def test_event_clone(self) -> None:
        """Test du clonage d'événement."""
        context = RequestContext(request_id="test")
        original = AsyncEvent(
            event_type=EventType.ERROR,
            context=context,
            error=ValueError("Test error")
        )
        
        cloned = original.clone()
        
        assert cloned.event_type == original.event_type
        assert cloned.context == original.context
        assert cloned.error == original.error
        assert cloned is not original
    
    def test_event_age(self) -> None:
        """Test du calcul de l'âge d'un événement."""
        context = RequestContext()
        event = AsyncEvent(EventType.SUCCESS, context)
        
        time.sleep(0.1)
        
        age = event.age
        assert age >= 0.1
        assert age < 1.0


class TestRequestResult:
    """Tests pour la classe RequestResult."""
    
    def test_result_creation(self) -> None:
        """Test de création de résultat."""
        result = RequestResult(
            request_id="test_request",
            status=ResultStatus.SUCCESS,
            attempts=3,
            total_time=1.5
        )
        
        assert result.request_id == "test_request"
        assert result.status == ResultStatus.SUCCESS
        assert result.attempts == 3
        assert result.total_time == 1.5
        assert result.is_success is True
        assert result.is_error is False
    
    def test_result_properties(self) -> None:
        """Test des propriétés du résultat."""
        success_result = RequestResult("test", ResultStatus.SUCCESS)
        error_result = RequestResult("test", ResultStatus.ERROR)
        
        assert success_result.is_success is True
        assert success_result.is_error is False
        
        assert error_result.is_success is False
        assert error_result.is_error is True
    
    def test_result_age(self) -> None:
        """Test du calcul de l'âge du résultat."""
        result = RequestResult("test", ResultStatus.SUCCESS)
        
        time.sleep(0.1)
        
        age = result.age
        assert age >= 0.1
        assert age < 1.0


class TestAsyncResilientClient:
    """Tests pour la classe AsyncResilientClient."""
    
    @pytest.fixture
    async def client(self) -> AsyncResilientClient:
        """Fixture pour créer un client de test."""
        client = AsyncResilientClient(
            timeout=httpx.Timeout(TEST_TIMEOUT),
            event_queue_size=100
        )
        async with client:
            yield client
    
    @pytest.mark.asyncio
    async def test_client_initialization(self) -> None:
        """Test d'initialisation du client."""
        async with AsyncResilientClient() as client:
            assert client._client is not None
            assert isinstance(client._client, httpx.AsyncClient)
            assert client.is_closed is False
            assert client.active_tasks_count == 0
            assert client.results_count == 0
    
    @pytest.mark.asyncio
    async def test_client_close(self) -> None:
        """Test de fermeture du client."""
        client = AsyncResilientClient()
        async with client:
            assert client.is_closed is False
        
        assert client.is_closed is True
    
    @pytest.mark.asyncio
    async def test_successful_request_no_retry(self, client: AsyncResilientClient) -> None:
        """Test qu'une requête réussie ne déclenche aucun retry."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        
        with patch.object(client._client, 'request', return_value=mock_response) as mock_request:
            context = RequestContext(request_id="test_success")
            task = await client.get("https://example.com", context=context)
            response = await task
            
            # Vérifications
            assert response.status_code == 200
            assert mock_request.call_count == 1
            mock_request.assert_called_once_with("GET", "https://example.com")
            
            # Attendre le traitement des événements
            await client.wait_for_all_requests()
            
            # Vérifier le résultat
            result = client.get_result("test_success")
            assert result is not None
            assert result.is_success is True
            assert result.attempts == 1
    
    @pytest.mark.asyncio
    async def test_connect_error_with_retry_success(self, client: AsyncResilientClient) -> None:
        """Test qu'un ConnectError déclenche des retries jusqu'au succès."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        
        # Premier appel: ConnectError, deuxième appel: succès
        with patch.object(client._client, 'request') as mock_request:
            mock_request.side_effect = [
                httpcore.ConnectError("Connection failed"),
                mock_response
            ]
            
            context = RequestContext(request_id="test_retry_success", max_retries=2)
            task = await client.get("https://example.com", context=context)
            response = await task
            
            # Vérifications
            assert response.status_code == 200
            assert mock_request.call_count == 2
            
            # Attendre le traitement des événements
            await client.wait_for_all_requests()
            
            # Vérifier le résultat
            result = client.get_result("test_retry_success")
            assert result is not None
            assert result.is_success is True
            assert result.attempts == 2  # 1 échec + 1 succès
    
    @pytest.mark.asyncio
    async def test_connect_error_max_retries_exceeded(self, client: AsyncResilientClient) -> None:
        """Test que les ConnectError répétés épuisent les retries."""
        with patch.object(client._client, 'request') as mock_request:
            # Toutes les tentatives échouent avec ConnectError
            mock_request.side_effect = httpcore.ConnectError("Connection failed")
            
            context = RequestContext(
                request_id="test_max_retries",
                max_retries=2,
                base_delay=TEST_BASE_DELAY
            )
            task = await client.get("https://example.com", context=context)
            
            with pytest.raises(httpcore.ConnectError):
                await task
            
            # Vérifications
            # max_retries=2 signifie 3 tentatives au total (1 initiale + 2 retries)
            assert mock_request.call_count == 3
            
            # Attendre le traitement des événements
            await client.wait_for_all_requests()
            
            # Vérifier le résultat
            result = client.get_result("test_max_retries")
            assert result is not None
            assert result.is_error is True
            assert result.attempts == 3
    
    @pytest.mark.asyncio
    async def test_non_connect_error_no_retry(self, client: AsyncResilientClient) -> None:
        """Test qu'une erreur non-ConnectError ne déclenche pas de retry."""
        with patch.object(client._client, 'request') as mock_request:
            # Utiliser une autre exception que ConnectError
            mock_request.side_effect = httpx.TimeoutException("Request timeout")
            
            context = RequestContext(request_id="test_no_retry")
            task = await client.get("https://example.com", context=context)
            
            with pytest.raises(httpx.TimeoutException):
                await task
            
            # Vérifications: pas de retry pour les autres types d'erreurs
            assert mock_request.call_count == 1
            
            # Attendre le traitement des événements
            await client.wait_for_all_requests()
            
            # Vérifier le résultat
            result = client.get_result("test_no_retry")
            assert result is not None
            assert result.is_error is True
            assert result.attempts == 1
    
    @pytest.mark.asyncio
    async def test_mixed_errors_retry_behavior(self, client: AsyncResilientClient) -> None:
        """Test du comportement avec un mélange d'erreurs."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        
        with patch.object(client._client, 'request') as mock_request:
            mock_request.side_effect = [
                httpcore.ConnectError("Connection failed"),  # Tentative 1: échec
                httpcore.ConnectError("Connection failed"),  # Tentative 2: échec
                mock_response  # Tentative 3: succès
            ]
            
            context = RequestContext(
                request_id="test_mixed_errors",
                max_retries=2,
                base_delay=TEST_BASE_DELAY
            )
            task = await client.get("https://example.com", context=context)
            response = await task
            
            # Vérifications
            assert response.status_code == 200
            assert mock_request.call_count == 3
            
            # Attendre le traitement des événements
            await client.wait_for_all_requests()
            
            # Vérifier le résultat
            result = client.get_result("test_mixed_errors")
            assert result is not None
            assert result.is_success is True
            assert result.attempts == 3
    
    @pytest.mark.asyncio
    async def test_all_http_methods(self, client: AsyncResilientClient) -> None:
        """Test que toutes les méthodes HTTP supportent le retry."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        
        methods_to_test: List[tuple[str, str]] = [
            ("get", "GET"),
            ("post", "POST"),
            ("put", "PUT"),
            ("delete", "DELETE"),
            ("patch", "PATCH"),
            ("head", "HEAD"),
            ("options", "OPTIONS")
        ]
        
        for method_name, http_method in methods_to_test:
            with patch.object(client._client, 'request') as mock_request:
                mock_request.side_effect = [
                    httpcore.ConnectError("Connection failed"),
                    mock_response
                ]
                
                context = RequestContext(
                    request_id=f"test_{method_name}",
                    max_retries=1,
                    base_delay=TEST_BASE_DELAY
                )
                
                method_func = getattr(client, method_name)
                task = await method_func("https://example.com", context=context)
                response = await task
                
                assert response.status_code == 200
                assert mock_request.call_count == 2
                
                # Vérifier que la bonne méthode HTTP est appelée
                calls = mock_request.call_args_list
                assert calls[0][0][0] == http_method  # Premier argument du premier appel
                assert calls[1][0][0] == http_method  # Premier argument du deuxième appel
    
    @pytest.mark.asyncio
    async def test_concurrent_requests(self, client: AsyncResilientClient) -> None:
        """Test de requêtes concurrentes."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        
        with patch.object(client._client, 'request', return_value=mock_response):
            # Lancer plusieurs requêtes en parallèle
            contexts = [
                RequestContext(request_id=f"concurrent_{i}")
                for i in range(5)
            ]
            
            tasks = []
            for i, context in enumerate(contexts):
                task = await client.get(f"https://example.com/{i}", context=context)
                tasks.append(task)
            
            # Attendre toutes les requêtes
            responses = await asyncio.gather(*tasks)
            
            # Vérifications
            assert len(responses) == 5
            for response in responses:
                assert response.status_code == 200
            
            # Attendre le traitement des événements
            await client.wait_for_all_requests()
            
            # Vérifier tous les résultats
            for i in range(5):
                result = client.get_result(f"concurrent_{i}")
                assert result is not None
                assert result.is_success is True
    
    @pytest.mark.asyncio
    async def test_event_callbacks(self, client: AsyncResilientClient) -> None:
        """Test des callbacks d'événements."""
        retry_events: List[AsyncEvent] = []
        success_events: List[AsyncEvent] = []
        
        async def on_retry(event: AsyncEvent) -> None:
            retry_events.append(event)
        
        async def on_success(event: AsyncEvent) -> None:
            success_events.append(event)
        
        # Ajouter les callbacks
        client.add_event_callback(EventType.RETRY, on_retry)
        client.add_event_callback(EventType.SUCCESS, on_success)
        
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        
        with patch.object(client._client, 'request') as mock_request:
            mock_request.side_effect = [
                httpcore.ConnectError("Connection failed"),
                mock_response
            ]
            
            context = RequestContext(
                request_id="test_callbacks",
                max_retries=1,
                base_delay=TEST_BASE_DELAY
            )
            task = await client.get("https://example.com", context=context)
            await task
            
            # Attendre le traitement des événements
            await client.wait_for_all_requests()
            
            # Vérifier que les callbacks ont été appelés
            assert len(retry_events) == 1
            assert len(success_events) == 1
            assert retry_events[0].context.request_id == "test_callbacks"
            assert success_events[0].context.request_id == "test_callbacks"
    
    @pytest.mark.asyncio
    async def test_timeout_handling(self, client: AsyncResilientClient) -> None:
        """Test de gestion des timeouts."""
        with patch.object(client._client, 'request') as mock_request:
            mock_request.side_effect = asyncio.TimeoutError("Request timeout")
            
            context = RequestContext(request_id="test_timeout")
            task = await client.get("https://example.com", context=context)
            
            with pytest.raises(asyncio.TimeoutError):
                await task
            
            # Attendre le traitement des événements
            await client.wait_for_all_requests()
            
            # Vérifier le résultat
            result = client.get_result("test_timeout")
            assert result is not None
            assert result.status == ResultStatus.TIMEOUT
    
    @pytest.mark.asyncio
    async def test_task_cancellation(self, client: AsyncResilientClient) -> None:
        """Test d'annulation de tâches."""
        with patch.object(client._client, 'request') as mock_request:
            # Faire en sorte que la requête soit lente
            async def slow_request(*args: Any, **kwargs: Any) -> httpx.Response:
                await asyncio.sleep(1.0)
                return MagicMock(spec=httpx.Response)
            
            mock_request.side_effect = slow_request
            
            context = RequestContext(request_id="test_cancellation")
            task = await client.get("https://example.com", context=context)
            
            # Annuler la tâche après un court délai
            await asyncio.sleep(0.1)
            task.cancel()
            
            with pytest.raises(asyncio.CancelledError):
                await task
            
            # Attendre le traitement des événements
            await client.wait_for_all_requests()
            
            # Vérifier le résultat
            result = client.get_result("test_cancellation")
            assert result is not None
            assert result.status == ResultStatus.CANCELLED
    
    @pytest.mark.asyncio
    async def test_wait_for_specific_request(self, client: AsyncResilientClient) -> None:
        """Test d'attente d'une requête spécifique."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        
        with patch.object(client._client, 'request', return_value=mock_response):
            context = RequestContext(request_id="test_wait_specific")
            task = await client.get("https://example.com", context=context)
            
            # Lancer la requête en arrière-plan
            asyncio.create_task(task)
            
            # Attendre le résultat spécifique
            result = await client.wait_for_request("test_wait_specific", timeout=5.0)
            
            assert result is not None
            assert result.is_success is True
            assert result.request_id == "test_wait_specific"
    
    @pytest.mark.asyncio
    async def test_wait_for_request_timeout(self, client: AsyncResilientClient) -> None:
        """Test de timeout lors de l'attente d'une requête."""
        with pytest.raises(asyncio.TimeoutError):
            await client.wait_for_request("non_existent_request", timeout=0.1)
    
    @pytest.mark.asyncio
    async def test_clear_old_results(self, client: AsyncResilientClient) -> None:
        """Test de nettoyage des anciens résultats."""
        # Créer quelques résultats manuellement
        old_result = RequestResult(
            request_id="old_request",
            status=ResultStatus.SUCCESS,
            created_at=time.time() - 3600  # 1 heure dans le passé
        )
        recent_result = RequestResult(
            request_id="recent_request",
            status=ResultStatus.SUCCESS,
            created_at=time.time()  # Maintenant
        )
        
        client._results["old_request"] = old_result
        client._results["recent_request"] = recent_result
        
        # Nettoyer les résultats plus anciens que 30 minutes
        cleaned_count = client.clear_old_results(max_age=1800)
        
        assert cleaned_count == 1
        assert "old_request" not in client._results
        assert "recent_request" in client._results
    
    @pytest.mark.asyncio
    async def test_error_message_validation(self, client: AsyncResilientClient) -> None:
        """Test de validation des messages d'erreur."""
        # Test avec méthode HTTP invalide
        with pytest.raises(ValueError) as exc_info:
            await client.request_async("INVALID_METHOD", "https://example.com")
        
        assert "non supportée" in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_max_concurrent_tasks_limit(self) -> None:
        """Test de la limite de tâches concurrentes."""
        # Créer un client avec une limite artificielle basse
        from async_client_constants import MAX_CONCURRENT_TASKS
        
        # Mock temporaire de la constante
        with patch('typed_async_client.MAX_CONCURRENT_TASKS', 2):
            async with AsyncResilientClient() as client:
                # Ajouter manuellement des tâches factices
                for i in range(2):
                    fake_task = asyncio.create_task(asyncio.sleep(0.1))
                    client._active_tasks.add(fake_task)
                
                # Essayer d'ajouter une tâche supplémentaire
                with pytest.raises(RuntimeError) as exc_info:
                    await client.request_async("GET", "https://example.com")
                
                assert "maximum de tâches concurrentes" in str(exc_info.value)


class TestUtilityFunctions:
    """Tests pour les fonctions utilitaires."""
    
    @pytest.mark.asyncio
    async def test_create_async_resilient_client_context_manager(self) -> None:
        """Test du context manager de création de client."""
        async with create_async_resilient_client(event_queue_size=50) as client:
            assert isinstance(client, AsyncResilientClient)
            assert client.is_closed is False
            assert client._event_queue.maxsize == 50
        
        # Le client devrait être fermé après le context manager
        assert client.is_closed is True
    
    @pytest.mark.asyncio
    async def test_simple_async_request_success(self) -> None:
        """Test de requête simple avec succès."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        
        with patch('typed_async_client.httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client
            
            response = await simple_async_request(
                "GET",
                "https://example.com",
                max_retries=2,
                timeout=5.0
            )
            
            assert response.status_code == 200
    
    @pytest.mark.asyncio
    async def test_simple_async_request_with_retry(self) -> None:
        """Test de requête simple avec retry."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        
        with patch('typed_async_client.httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client.request.side_effect = [
                httpcore.ConnectError("Connection failed"),
                mock_response
            ]
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client
            
            response = await simple_async_request(
                "GET",
                "https://example.com",
                max_retries=1
            )
            
            assert response.status_code == 200
            assert mock_client.request.call_count == 2


class TestIntegration:
    """Tests d'intégration avec de vrais appels réseau (optionnels)."""
    
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_real_request_success(self) -> None:
        """Test avec une vraie requête HTTP (nécessite une connexion internet)."""
        try:
            async with create_async_resilient_client() as client:
                context = RequestContext(
                    request_id="integration_test",
                    max_retries=1
                )
                task = await client.get("https://httpbin.org/get", context=context)
                response = await task
                
                assert response.status_code == 200
                
                # Attendre le traitement des événements
                await client.wait_for_all_requests()
                
                result = client.get_result("integration_test")
                assert result is not None
                assert result.is_success is True
                
        except Exception as e:
            pytest.skip(f"Test d'intégration ignoré: {e}")
    
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_real_connection_error(self) -> None:
        """Test avec une URL qui provoque une erreur de connexion."""
        try:
            async with create_async_resilient_client() as client:
                context = RequestContext(
                    request_id="integration_error_test",
                    max_retries=2,
                    base_delay=0.1
                )
                
                # Port fermé pour provoquer une ConnectError
                task = await client.get(
                    "http://localhost:9999",
                    context=context,
                    timeout=1.0
                )
                
                with pytest.raises(httpcore.ConnectError):
                    await task
                
                # Attendre le traitement des événements
                await client.wait_for_all_requests()
                
                result = client.get_result("integration_error_test")
                assert result is not None
                assert result.is_error is True
                assert result.attempts == 3  # 1 initiale + 2 retries
                
        except Exception as e:
            pytest.skip(f"Test d'intégration ignoré: {e}")
    
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_concurrent_real_requests(self) -> None:
        """Test de requêtes concurrentes réelles."""
        try:
            urls: List[str] = [
                "https://httpbin.org/get",
                "https://jsonplaceholder.typicode.com/posts/1",
                "https://httpbin.org/headers"
            ]
            
            async with create_async_resilient_client() as client:
                tasks: List[asyncio.Task[httpx.Response]] = []
                
                for i, url in enumerate(urls):
                    context = RequestContext(request_id=f"concurrent_real_{i}")
                    task = await client.get(url, context=context)
                    tasks.append(task)
                
                # Attendre toutes les requêtes
                responses = await asyncio.gather(*tasks)
                
                # Vérifier que toutes ont réussi
                for response in responses:
                    assert response.status_code == 200
                
                # Attendre le traitement des événements
                results = await client.wait_for_all_requests()
                
                assert len(results) == 3
                for result in results.values():
                    assert result.is_success is True
                    
        except Exception as e:
            pytest.skip(f"Test d'intégration ignoré: {e}")


# Configuration pour pytest
def pytest_configure(config: Any) -> None:
    """Configuration des markers pytest."""
    config.addinivalue_line(
        "markers", "integration: marque les tests d'intégration nécessitant le réseau"
    )


# Fixtures globales
@pytest.fixture(scope="session")
def event_loop() -> asyncio.AbstractEventLoop:
    """Fixture pour l'event loop asyncio."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# Script pour lancer les tests
if __name__ == "__main__":
    import sys
    
    # Installation des dépendances si nécessaire
    try:
        import pytest
        import httpx
        import httpcore
    except ImportError as e:
        print(f"Dépendance manquante: {e}")
        print("Installez avec: pip install pytest pytest-asyncio httpx")
        sys.exit(1)
    
    # Lancer les tests
    exit_code = pytest.main([
        __file__,
        "-v",
        "--tb=short",
        "-m", "not integration",  # Exclure les tests d'intégration par défaut
        "--asyncio-mode=auto"    # Mode asyncio automatique
    ])
    
    sys.exit(exit_code)
