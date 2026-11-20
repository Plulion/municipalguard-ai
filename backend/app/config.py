import json
import os
from dataclasses import dataclass, field


def _parse_max_upload_mb() -> int:
    raw = os.getenv("MAX_UPLOAD_MB", "15")
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("MAX_UPLOAD_MB debe ser un entero positivo.") from exc
    if value <= 0 or value > 200:
        raise RuntimeError("MAX_UPLOAD_MB debe estar entre 1 y 200 MB.")
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./data/municipalguard_v030.db"))
    api_keys_json: str = field(default_factory=lambda: os.getenv("MGAI_API_KEYS_JSON", "{}"))
    demo_tenant_name: str = field(default_factory=lambda: os.getenv("DEMO_TENANT_NAME", "Municipalidad Demo"))
    tenants_json: str = field(default_factory=lambda: os.getenv("MGAI_TENANTS_JSON", "{}"))
    max_upload_mb: int = field(default_factory=_parse_max_upload_mb)
    auto_create_schema: bool = field(default_factory=lambda: os.getenv("MGAI_AUTO_CREATE_SCHEMA", "false").lower() == "true")
    artifacts_dir: str = field(default_factory=lambda: os.getenv("ARTIFACTS_DIR", "./data/artifacts"))
    ai_provider: str = field(default_factory=lambda: os.getenv("AI_PROVIDER", "local").strip().lower())
    ollama_url: str = field(default_factory=lambda: os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/"))
    ollama_model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "qwen2.5:7b"))
    ollama_vision_model: str = field(default_factory=lambda: os.getenv("OLLAMA_VISION_MODEL", "llama3.2-vision"))
    hybrid_api_url: str = field(default_factory=lambda: os.getenv("HYBRID_API_URL", "").rstrip("/"))
    hybrid_api_key: str = field(default_factory=lambda: os.getenv("HYBRID_API_KEY", ""))
    hybrid_model: str = field(default_factory=lambda: os.getenv("HYBRID_MODEL", "gpt-4o-mini"))
    pii_masking_enabled: bool = field(default_factory=lambda: os.getenv("PII_MASKING_ENABLED", "true").lower() == "true")
    ocr_min_confidence: float = field(default_factory=lambda: float(os.getenv("OCR_MIN_CONFIDENCE", "0.65")))

    def tenant_profiles(self) -> dict:
        try:
            data = json.loads(self.tenants_json)
            if not isinstance(data, dict):
                raise ValueError
            return data
        except Exception as exc:
            raise RuntimeError("MGAI_TENANTS_JSON debe ser un objeto JSON de perfiles institucionales.") from exc

    def api_keys(self) -> dict[str, str]:
        try:
            data = json.loads(self.api_keys_json)
            if not isinstance(data, dict) or not data:
                raise ValueError
            if not all(isinstance(k, str) and len(k) >= 16 and isinstance(v, str) and v for k, v in data.items()):
                raise ValueError
            return data
        except Exception as exc:
            raise RuntimeError(
                "MGAI_API_KEYS_JSON debe ser un JSON objeto con claves de al menos 16 caracteres y tenant IDs."
            ) from exc


settings = Settings()
