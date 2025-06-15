"""
Constantes pour le client HTTP asynchrone résilient.

Ce module définit toutes les constantes utilisées par le client asynchrone,
avec typage strict utilisant Final pour l'immutabilité.
"""

from typing import Final

# === CONSTANTES DE CONFIGURATION ===

# Valeurs par défaut pour les retries
DEFAULT_MAX_RETRIES: Final[int] = 2
DEFAULT_BASE_DELAY: Final[float] = 1.0
DEFAULT_MAX_DELAY: Final[float] = 30.0
DEFAULT_BACKOFF_FACTOR: Final[float] = 2.0

# Timeouts par défaut (en secondes)
DEFAULT_CONNECT_TIMEOUT: Final[float] = 10.0
DEFAULT_READ_TIMEOUT: Final[float] = 30.0
DEFAULT_WRITE_TIMEOUT: Final[float] = 10.0
DEFAULT_POOL_TIMEOUT: Final[float] = 10.0

# Limites de connexion
DEFAULT_MAX_CONNECTIONS: Final[int] = 100
DEFAULT_MAX_KEEPALIVE_CONNECTIONS: Final[int] = 20

# Configuration de la queue d'événements
DEFAULT_EVENT_QUEUE_SIZE: Final[int] = 1000
EVENT_QUEUE_TIMEOUT: Final[float] = 5.0

# Jitter pour éviter l'effet "thundering herd" (en pourcentage)
JITTER_PERCENTAGE: Final[float] = 0.2

# === TYPES D'ÉVÉNEMENTS ===

class EventType:
    """Types d'événements pour la communication inter-coroutines."""
    SUCCESS: Final[str] = "success"
    ERROR: Final[str] = "error"
    RETRY: Final[str] = "retry"
    TIMEOUT: Final[str] = "timeout"
    CANCELLED: Final[str] = "cancelled"

# === STATUTS DE RÉSULTAT ===

class ResultStatus:
    """Statuts possibles pour les résultats de requête."""
    SUCCESS: Final[str] = "success"
    ERROR: Final[str] = "error"
    TIMEOUT: Final[str] = "timeout"
    CANCELLED: Final[str] = "cancelled"
    PENDING: Final[str] = "pending"

# === MÉTHODES HTTP SUPPORTÉES ===

class HttpMethod:
    """Méthodes HTTP supportées par le client."""
    GET: Final[str] = "GET"
    POST: Final[str] = "POST"
    PUT: Final[str] = "PUT"
    DELETE: Final[str] = "DELETE"
    PATCH: Final[str] = "PATCH"
    HEAD: Final[str] = "HEAD"
    OPTIONS: Final[str] = "OPTIONS"

# Liste des méthodes supportées
SUPPORTED_HTTP_METHODS: Final[tuple[str, ...]] = (
    HttpMethod.GET,
    HttpMethod.POST,
    HttpMethod.PUT,
    HttpMethod.DELETE,
    HttpMethod.PATCH,
    HttpMethod.HEAD,
    HttpMethod.OPTIONS,
)

# === HEADERS PAR DÉFAUT ===

DEFAULT_USER_AGENT: Final[str] = "AsyncResilientClient/1.0"
DEFAULT_ACCEPT: Final[str] = "application/json, text/plain, */*"
DEFAULT_ACCEPT_ENCODING: Final[str] = "gzip, deflate"

# Headers par défaut
DEFAULT_HEADERS: Final[dict[str, str]] = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": DEFAULT_ACCEPT,
    "Accept-Encoding": DEFAULT_ACCEPT_ENCODING,
}

# === MESSAGES D'ERREUR ===

class ErrorMessage:
    """Messages d'erreur standardisés."""
    CLIENT_NOT_INITIALIZED: Final[str] = "Le client HTTP n'est pas initialisé"
    INVALID_HTTP_METHOD: Final[str] = "Méthode HTTP non supportée: {method}"
    MAX_RETRIES_EXCEEDED: Final[str] = "Nombre maximum de tentatives dépassé: {attempts}/{max_retries}"
    REQUEST_TIMEOUT: Final[str] = "Timeout de requête dépassé: {timeout}s"
    CONTEXT_CLONE_FAILED: Final[str] = "Échec du clonage du contexte"
    EVENT_QUEUE_FULL: Final[str] = "Queue d'événements saturée"
    INVALID_URL: Final[str] = "URL invalide: {url}"
    CONNECTION_ERROR: Final[str] = "Erreur de connexion: {error}"

# === PATTERNS DE VALIDATION ===

# Pattern pour la validation des URLs
URL_PATTERN: Final[str] = r"^https?://(?:[-\w.])+(?::[0-9]+)?(?:/(?:[\w/_.])*(?:\?(?:[\w&=%.])*)?(?:#(?:[\w.])*)?)?$"

# Pattern pour les IDs de requête
REQUEST_ID_PATTERN: Final[str] = r"^[a-zA-Z0-9_-]+$"

# === LIMITES DE PERFORMANCE ===

# Nombre maximum de tâches concurrentes
MAX_CONCURRENT_TASKS: Final[int] = 1000

# Taille maximale du pool de résultats en mémoire
MAX_RESULTS_POOL_SIZE: Final[int] = 10000

# Durée maximale de vie d'un résultat en cache (en secondes)
RESULT_TTL: Final[int] = 3600  # 1 heure

# === NIVEAUX DE LOG ===

class LogLevel:
    """Niveaux de logging pour le client."""
    DEBUG: Final[str] = "DEBUG"
    INFO: Final[str] = "INFO"
    WARNING: Final[str] = "WARNING"
    ERROR: Final[str] = "ERROR"
    CRITICAL: Final[str] = "CRITICAL"

# === MÉTRIQUES ET MONITORING ===

# Intervalles de reporting des métriques (en secondes)
METRICS_REPORT_INTERVAL: Final[float] = 60.0

# Seuils d'alerte
WARNING_RETRY_THRESHOLD: Final[float] = 0.3  # 30% de requêtes en retry
ERROR_RATE_THRESHOLD: Final[float] = 0.1     # 10% d'erreurs
LATENCY_WARNING_THRESHOLD: Final[float] = 5.0  # 5 secondes

# === CONFIGURATION DE DÉVELOPPEMENT ===

# Mode debug (pour les tests)
DEBUG_MODE: Final[bool] = False

# Délais réduits pour les tests
TEST_BASE_DELAY: Final[float] = 0.01
TEST_MAX_DELAY: Final[float] = 0.1
TEST_TIMEOUT: Final[float] = 1.0

# === VALIDATION DES CONSTANTES ===

def validate_constants() -> None:
    """Valide la cohérence des constantes définies."""
    assert DEFAULT_BASE_DELAY > 0, "Le délai de base doit être positif"
    assert DEFAULT_MAX_DELAY >= DEFAULT_BASE_DELAY, "Le délai maximum doit être >= au délai de base"
    assert DEFAULT_BACKOFF_FACTOR >= 1.0, "Le facteur de backoff doit être >= 1.0"
    assert DEFAULT_MAX_RETRIES >= 0, "Le nombre de retries doit être >= 0"
    assert 0.0 <= JITTER_PERCENTAGE <= 1.0, "Le jitter doit être entre 0 et 1"
    assert DEFAULT_EVENT_QUEUE_SIZE > 0, "La taille de la queue doit être positive"
    assert MAX_CONCURRENT_TASKS > 0, "Le nombre max de tâches doit être positif"

# Validation automatique à l'import
validate_constants()