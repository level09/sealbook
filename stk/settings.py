import importlib.util
import os
from datetime import timedelta

from dotenv import load_dotenv

# Detect optional dependencies (installed via --extra full)
REDIS_AVAILABLE = importlib.util.find_spec("redis") is not None

os_env = os.environ
load_dotenv()

# Session configuration - computed at module level
_redis_url = os.environ.get("REDIS_URL") or os.environ.get("REDIS_SESSION")
if REDIS_AVAILABLE and _redis_url:
    _SESSION_TYPE = "redis"
else:
    # No server-side sessions — Quart's built-in cookie sessions will be used
    _SESSION_TYPE = None


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY")
    if not SECRET_KEY:
        raise ValueError("SECRET_KEY environment variable is required")
    # Product name and sign-in copy; apps set these instead of editing templates
    APP_NAME = os.environ.get("APP_NAME", "Sealbook")
    APP_TAGLINE = os.environ.get("APP_TAGLINE", "Evidence you can stand behind.")
    APP_DESCRIPTION = os.environ.get(
        "APP_DESCRIPTION",
        "An evidence ledger: every fact signed, sourced and verifiable offline.",
    )
    APP_DIR = os.path.abspath(os.path.dirname(__file__))  # This directory
    PROJECT_ROOT = os.path.abspath(os.path.join(APP_DIR, os.pardir))

    # Database - default to async SQLite in instance folder
    _default_db = (
        f"sqlite+aiosqlite:///{os.path.join(PROJECT_ROOT, 'instance', 'stk.db')}"
    )
    SQLALCHEMY_DATABASE_URI = os.environ.get("SQLALCHEMY_DATABASE_URI", _default_db)
    BLOB_DIR = os.environ.get(
        "BLOB_DIR", os.path.join(PROJECT_ROOT, "instance", "blobs")
    )
    EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL")
    EMBEDDING_URL = os.environ.get("EMBEDDING_URL")
    EMBEDDING_REVISION = os.environ.get("EMBEDDING_REVISION", "")
    INDEX_BATCH_CHUNKS = int(os.environ.get("INDEX_BATCH_CHUNKS", "32"))
    PDF_BATCH_PAGES = int(os.environ.get("PDF_BATCH_PAGES", "10"))
    MEDIA_BATCH_SEGMENTS = int(os.environ.get("MEDIA_BATCH_SEGMENTS", "8"))
    PDQ_DISTANCE = int(os.environ.get("PDQ_DISTANCE", "31"))
    PDQ_QUALITY = int(os.environ.get("PDQ_QUALITY", "50"))
    MEDIA_CANDIDATES = int(os.environ.get("MEDIA_CANDIDATES", "20"))
    # Findings each discover run may leave open per kind and investigation.
    FINDINGS_PER_KIND = int(os.environ.get("FINDINGS_PER_KIND", "20"))
    FINGERPRINT_MAX_SECONDS = int(os.environ.get("FINGERPRINT_MAX_SECONDS", "3600"))

    # Local speech to text (faster-whisper). Weights are fetched once with
    # `stk speech-model`; workers only read the cache, so no audio leaves the machine.
    # Empty turns transcription off.
    SPEECH_MODEL = os.environ.get("SPEECH_MODEL", "large-v3-turbo")
    SPEECH_DEVICE = os.environ.get("SPEECH_DEVICE", "auto")
    # "auto" is int8 on CPU and float16 on CUDA.
    SPEECH_COMPUTE_TYPE = os.environ.get("SPEECH_COMPUTE_TYPE", "auto")
    SPEECH_MODEL_DIR = os.environ.get(
        "SPEECH_MODEL_DIR", os.path.join(PROJECT_ROOT, "instance", "models", "speech")
    )
    # Offline basemap extract written by `stk basemap` and served to signed-in readers.
    BASEMAP_PATH = os.environ.get(
        "BASEMAP_PATH",
        os.path.join(PROJECT_ROOT, "instance", "basemap", "syria.pmtiles"),
    )
    # Seconds of media decoded per run; a run also stops early to stay inside its lease.
    SPEECH_CHUNK_SECONDS = int(os.environ.get("SPEECH_CHUNK_SECONDS", "600"))

    # Web research (stk.research). Each service is off until its key is set; queries
    # reveal names to the provider, so nothing is sent without both a person and a key.
    BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY")
    BRAVE_BASE_URL = os.environ.get(
        "BRAVE_BASE_URL", "https://api.search.brave.com/res/v1"
    )
    RESEARCH_QUERIES_PER_HOUR = int(os.environ.get("RESEARCH_QUERIES_PER_HOUR", "60"))
    RESEARCH_CAPTURES_PER_HOUR = int(os.environ.get("RESEARCH_CAPTURES_PER_HOUR", "30"))
    # Brave's plans limit requests per second; queries in one send are spaced by this.
    RESEARCH_SEARCH_INTERVAL = float(os.environ.get("RESEARCH_SEARCH_INTERVAL", "1.1"))
    RESEARCH_RESULTS = int(os.environ.get("RESEARCH_RESULTS", "20"))
    RESEARCH_MEDIA_PER_PAGE = int(os.environ.get("RESEARCH_MEDIA_PER_PAGE", "8"))
    RESEARCH_MEDIA_MAX_BYTES = int(
        os.environ.get("RESEARCH_MEDIA_MAX_BYTES", str(500 * 1024 * 1024))
    )
    RESEARCH_CANDIDATE_SECONDS = int(
        os.environ.get("RESEARCH_CANDIDATE_SECONDS", "1800")
    )

    STK_PUBLIC_URL = os.environ.get("STK_PUBLIC_URL")

    # security
    SECURITY_REGISTERABLE = (
        os.environ.get("SECURITY_REGISTERABLE", "False").lower() == "true"
    )
    SECURITY_RECOVERABLE = False
    SECURITY_CONFIRMABLE = False
    SECURITY_CHANGEABLE = True
    SECURITY_TRACKABLE = True
    SECURITY_PASSWORD_HASH = "argon2"
    SECURITY_PASSWORD_SALT = os.environ.get("SECURITY_PASSWORD_SALT")
    if not SECURITY_PASSWORD_SALT:
        raise ValueError("SECURITY_PASSWORD_SALT environment variable is required")
    # quart-security handles email normalization internally

    SECURITY_POST_LOGIN_VIEW = "/dashboard"
    SECURITY_POST_CONFIRM_VIEW = "/dashboard"
    SECURITY_POST_REGISTER_VIEW = "/login"

    SECURITY_MULTI_FACTOR_RECOVERY_CODES = True
    SECURITY_MULTI_FACTOR_RECOVERY_CODES_N = 3

    SECURITY_TWO_FACTOR_ENABLED_METHODS = ["authenticator"]
    SECURITY_TWO_FACTOR = True
    _totp_keys = os.environ.get("SECURITY_TOTP_ENCRYPTION_KEYS")
    SECURITY_TOTP_ENCRYPTION_KEYS = _totp_keys.split(",") if _totp_keys else None
    SECURITY_API_ENABLED_METHODS = ["session"]

    SECURITY_FRESHNESS = timedelta(minutes=60)
    SECURITY_FRESHNESS_GRACE_PERIOD = timedelta(minutes=60)
    SECURITY_PASSWORD_LENGTH_MIN = 12

    SECURITY_TOTP_ISSUER = APP_NAME

    SECURITY_WAN_RP_ID = os.environ.get("SECURITY_WAN_RP_ID")
    SECURITY_WAN_EXPECTED_ORIGIN = os.environ.get("SECURITY_WAN_EXPECTED_ORIGIN")
    SECURITY_WEBAUTHN = True
    SECURITY_WAN_ALLOW_AS_FIRST_FACTOR = True
    SECURITY_WAN_ALLOW_AS_MULTI_FACTOR = True

    # Session configuration (quart-session for Redis, Quart cookie sessions otherwise)
    SESSION_TYPE = _SESSION_TYPE
    SESSION_URI = _redis_url  # quart-session auto-creates async redis from URI
    SESSION_KEY_PREFIX = "session:"
    # quart-session's signer emits a bytes cookie value that modern werkzeug
    # rejects (500 on login); random session ids don't need signing
    SESSION_USE_SIGNER = False
    PERMANENT_SESSION_LIFETIME = 3600
    SESSION_COOKIE_SECURE = (
        os.environ.get("SESSION_COOKIE_SECURE", "True").lower() == "true"
    )
    SECURITY_COOKIE_SECURE = SESSION_COOKIE_SECURE
    SESSION_COOKIE_HTTPONLY = (
        os.environ.get("SESSION_COOKIE_HTTPONLY", "True").lower() == "true"
    )
    SESSION_COOKIE_SAMESITE = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax")

    # Session management
    DISABLE_MULTIPLE_SESSIONS = (
        os.environ.get("DISABLE_MULTIPLE_SESSIONS", "False").lower() == "true"
    )
    # Test-only browser session handoff for agent-driven development.
    STK_ENV = os.environ.get("STK_ENV", "production")
    STK_ENABLE_AGENT_LOGIN = (
        os.environ.get("STK_ENABLE_AGENT_LOGIN", "False").lower() == "true"
        or os.environ.get("STK_ENABLE_AGENT_LOGIN") == "1"
    )
    STK_AGENT_LOGIN_MAX_TTL_SECONDS = int(
        os.environ.get("STK_AGENT_LOGIN_MAX_TTL_SECONDS", "60")
    )

    # Email settings (used by aiosmtplib via AsyncMailUtil)
    MAIL_SERVER = os.environ.get("MAIL_SERVER")
    MAIL_PORT = 465
    MAIL_USE_SSL = True
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
    SECURITY_EMAIL_SENDER = os.environ.get("SECURITY_EMAIL_SENDER", "info@domain.com")
    SECURITY_SEND_PASSWORD_CHANGE_EMAIL = False

    # Google OAuth Settings
    GOOGLE_AUTH_ENABLED = (
        os.environ.get("GOOGLE_AUTH_ENABLED", "False").lower() == "true"
    )
    GOOGLE_OAUTH_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    GOOGLE_OAUTH_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    GOOGLE_OAUTH_REDIRECT_URI = os.environ.get(
        "GOOGLE_OAUTH_REDIRECT_URI"
    )  # Let the OAuth handler construct it dynamically
    OAUTHLIB_INSECURE_TRANSPORT = os.environ.get("OAUTHLIB_INSECURE_TRANSPORT", "0")

    # GitHub OAuth Settings
    GITHUB_AUTH_ENABLED = (
        os.environ.get("GITHUB_AUTH_ENABLED", "False").lower() == "true"
    )
    GITHUB_OAUTH_CLIENT_ID = os.environ.get("GITHUB_OAUTH_CLIENT_ID")
    GITHUB_OAUTH_CLIENT_SECRET = os.environ.get("GITHUB_OAUTH_CLIENT_SECRET")

    # Anchoring (stk anchor). Public services; synthetic digests only in dev.
    TSA_URL = os.environ.get("TSA_URL", "https://freetsa.org/tsr")
    OTS_CALENDARS = os.environ.get(
        "OTS_CALENDARS",
        "https://a.pool.opentimestamps.org,https://b.pool.opentimestamps.org",
    ).split(",")
    PARTNER_ROOT_DIRS = [
        d for d in os.environ.get("PARTNER_ROOT_DIRS", "").split(",") if d
    ]
    # SHA-256 (hex, comma-separated) of the TSA signer certificates verify-anchors
    # trusts. Unset, every TSA receipt is reported as "TSA signer not pinned".
    TSA_CERT_SHA256 = [
        f.strip().lower().replace(":", "")
        for f in os.environ.get("TSA_CERT_SHA256", "").split(",")
        if f.strip()
    ]
    ANCHOR_INTERVAL_SECONDS = int(os.environ.get("ANCHOR_INTERVAL_SECONDS", "3600"))

    # The model each route serves for OCR and extraction (MODEL_LOCAL,
    # MODEL_CLOUD_ZDR, MODEL_CLOUD). Ingest queues work on the most private route
    # the policy allows that has a model; none means the file waits, unprocessed.
    RUN_MODELS = {
        r: os.environ[f"MODEL_{r.upper().replace('-', '_')}"]
        for r in ("local", "cloud-zdr", "cloud")
        if os.environ.get(f"MODEL_{r.upper().replace('-', '_')}")
    }

    # Proactive agents (intake brief, linker) read across a case, sensitive sources
    # included, so they run on the local route only (ROUTE_LOCAL_BASE_URL, an
    # OpenAI-compatible server such as Ollama). Empty turns them off.
    AGENT_MODEL = os.environ.get("AGENT_MODEL", "qwen3.5:9b")

    # Package export signs its cover with c2patool (ES256 cert chain and key, PEM).
    C2PA_SIGN_CERT = os.environ.get("C2PA_SIGN_CERT")
    C2PA_SIGN_KEY = os.environ.get("C2PA_SIGN_KEY")
