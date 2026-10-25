"""Sealbook settings on top of stk's: models, routes, agents, speech, research,
anchors, blobs and maps. Each is read from the environment, so `.env` sets them."""

import os
from pathlib import Path

import stk

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)


class Config(stk.Config):
    PROJECT_ROOT = PROJECT_ROOT
    APP_NAME = os.environ.get("APP_NAME", "Sealbook")
    APP_TAGLINE = os.environ.get("APP_TAGLINE", "Evidence you can stand behind.")
    APP_DESCRIPTION = os.environ.get(
        "APP_DESCRIPTION",
        "An evidence ledger: every fact signed, sourced and verifiable offline.",
    )
    SECURITY_TOTP_ISSUER = APP_NAME
    # GET starts an MCP client's OAuth authorization flow; it is not a page.
    STK_SMOKE_SKIP = ["/oauth/authorize"]

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
    # YouTube: subtitles read for the leading results of one send, in one time budget;
    # a capture's download stops at its own limit.
    RESEARCH_YOUTUBE_SUBTITLES = int(os.environ.get("RESEARCH_YOUTUBE_SUBTITLES", "5"))
    RESEARCH_YOUTUBE_SUBTITLE_SECONDS = float(
        os.environ.get("RESEARCH_YOUTUBE_SUBTITLE_SECONDS", "30")
    )
    RESEARCH_YOUTUBE_SECONDS = int(os.environ.get("RESEARCH_YOUTUBE_SECONDS", "900"))

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

    # Seconds one local model call may take. A laptop reads a register page at a few
    # tokens a second; the run lease grows with it (stk.ledger.queue).
    ROUTE_LOCAL_TIMEOUT = int(os.environ.get("ROUTE_LOCAL_TIMEOUT", "120"))

    # The model each route serves for OCR and extraction (MODEL_LOCAL,
    # MODEL_CLOUD_ZDR, MODEL_CLOUD). Ingest queues work on the most private route
    # the policy allows that has a model; none means the file waits, unprocessed.
    RUN_MODELS = {
        r: os.environ[f"MODEL_{r.upper().replace('-', '_')}"]
        for r in ("local", "cloud-zdr", "cloud")
        if os.environ.get(f"MODEL_{r.upper().replace('-', '_')}")
    }

    # Proactive agents (intake brief, linker) read across a case, sensitive sources
    # included, so by default they run on the local route (ROUTE_LOCAL_BASE_URL, an
    # OpenAI-compatible server such as Ollama). AGENT_ROUTE=cloud-zdr sends them to
    # OpenRouter instead; the policy table must also allow that route for each agent
    # tool, so a setting alone never moves case data off the machine. Empty
    # AGENT_MODEL turns them off.
    AGENT_ROUTE = os.environ.get("AGENT_ROUTE", "local")
    AGENT_MODEL = os.environ.get("AGENT_MODEL", "qwen3.5:9b")

    # Package export signs its cover with c2patool (ES256 cert chain and key, PEM).
    C2PA_SIGN_CERT = os.environ.get("C2PA_SIGN_CERT")
    C2PA_SIGN_KEY = os.environ.get("C2PA_SIGN_KEY")
