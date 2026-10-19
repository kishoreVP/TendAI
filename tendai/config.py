"""All configuration comes from environment variables (optionally a .env file)."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

DATABASE_URL = os.getenv("TENDAI_DATABASE_URL", f"sqlite:///{ROOT / 'storage' / 'tendai.db'}")
STORAGE_DIR = Path(os.getenv("TENDAI_STORAGE_DIR", ROOT / "storage"))
MAX_UPLOAD_MB = int(os.getenv("TENDAI_MAX_UPLOAD_MB", "50"))

# Inference. Providers: "none" (rules only), "groq", "local".
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("TENDAI_GROQ_MODEL", "openai/gpt-oss-120b")
LOCAL_LLM_URL = os.getenv("TENDAI_LOCAL_LLM_URL", "http://localhost:8080/v1")  # llama.cpp llama-server
LOCAL_LLM_MODEL = os.getenv("TENDAI_LOCAL_LLM_MODEL", "qwen2.5-3b-instruct-q4_k_m")
LLM_TIMEOUT_S = float(os.getenv("TENDAI_LLM_TIMEOUT_S", "60"))
# Per-task provider routing, e.g. {"requirement_extraction": "groq", "evidence_mapping": "local"}
TASK_ROUTING = json.loads(os.getenv("TENDAI_TASK_ROUTING", "{}"))
DEFAULT_PROVIDER = os.getenv("TENDAI_DEFAULT_PROVIDER", "none")
# Bidder documents are confidential: they go to a cloud API only when this is explicitly true.
ALLOW_CLOUD_FOR_BIDDER_DOCS = os.getenv("TENDAI_ALLOW_CLOUD_FOR_BIDDER_DOCS", "false").lower() == "true"
