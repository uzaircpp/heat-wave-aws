"""Runtime settings, read once from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Tiny .env loader so we do not need python-dotenv. Existing env vars win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if value:  # an empty line like DATABASE_URL= means "use the default"
            os.environ.setdefault(key.strip(), value)


_load_dotenv(ROOT / ".env")


def _load_aws_secret() -> None:
    """On AWS, DATABASE_URL, OFFICER_TOKEN and other secrets come from AWS Secrets Manager."""
    sid = os.environ.get("APP_SECRET_ID") or os.environ.get("AWS_SECRET_ID")
    if not sid:
        return
    try:
        from .aws import load_secret
        for key, value in load_secret(sid).items():
            os.environ.setdefault(key, str(value))
    except Exception as e:  # noqa: BLE001 - start anyway; health checks will show what is missing
        print(f"Could not read secret {sid}: {e}")


_load_aws_secret()


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str = os.environ.get("DATABASE_URL") or f"sqlite:///{(ROOT / 'data' / 'urbanshield.db').as_posix()}"
    data_dir: Path = Path(os.environ.get("DATA_DIR", str(ROOT / "data")))
    upload_dir: Path = Path(os.environ.get("UPLOAD_DIR", str(ROOT / "data" / "uploads")))
    web_dir: Path = Path(os.environ.get("WEB_DIR", str(ROOT / "web")))

    # Officer actions (assigning reports, broadcasting) need this bearer token.
    officer_token: str = os.environ.get("OFFICER_TOKEN", "")

    # External services. All of these are free and need no key, but have fair-use limits.
    open_meteo_url: str = os.environ.get("OPEN_METEO_URL", "https://api.open-meteo.com/v1")
    open_meteo_archive_url: str = os.environ.get("OPEN_METEO_ARCHIVE_URL", "https://archive-api.open-meteo.com/v1")
    open_meteo_api_key: str = os.environ.get("OPEN_METEO_API_KEY", "")  # only for the paid commercial tier
    overpass_url: str = os.environ.get("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
    osrm_url: str = os.environ.get("OSRM_URL", "https://router.project-osrm.org")
    nominatim_url: str = os.environ.get("NOMINATIM_URL", "https://nominatim.openstreetmap.org")
    sachet_rss_url: str = os.environ.get(
        "SACHET_RSS_URL", "https://sachet.ndma.gov.in/cap_public_website/rss/rss_india.xml"
    )
    contact_email: str = os.environ.get("CONTACT_EMAIL", "")  # sent in User-Agent, required by OSM policies

    # Optional SMS / WhatsApp through Twilio. Leave empty to disable.
    twilio_sid: str = os.environ.get("TWILIO_ACCOUNT_SID", "")
    twilio_token: str = os.environ.get("TWILIO_AUTH_TOKEN", "")
    twilio_from: str = os.environ.get("TWILIO_FROM", "")
    twilio_whatsapp_from: str = os.environ.get("TWILIO_WHATSAPP_FROM", "")

    # Refresh cadence for background jobs, in seconds.
    weather_refresh_s: int = int(os.environ.get("WEATHER_REFRESH_S", "900"))
    alerts_refresh_s: int = int(os.environ.get("ALERTS_REFRESH_S", "600"))
    run_background_jobs: bool = _bool("RUN_BACKGROUND_JOBS", True)
    push_check_s: int = int(os.environ.get("PUSH_CHECK_S", "300"))

    # City frame. Delhi by default; another city needs its own wards file.
    city_name: str = os.environ.get("CITY_NAME", "Delhi")
    timezone: str = os.environ.get("CITY_TZ", "Asia/Kolkata")
    bbox: tuple[float, float, float, float] = field(
        default_factory=lambda: tuple(
            float(x) for x in os.environ.get("CITY_BBOX", "76.83,28.40,77.35,28.89").split(",")
        )  # min_lon, min_lat, max_lon, max_lat
    )
    weather_grid: int = int(os.environ.get("WEATHER_GRID", "6"))  # N x N forecast points over the bbox

    cors_origins: tuple[str, ...] = tuple(
        o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()
    )
    max_upload_mb: int = int(os.environ.get("MAX_UPLOAD_MB", "8"))

    @property
    def user_agent(self) -> str:
        contact = f" ({self.contact_email})" if self.contact_email else ""
        return f"Rahat/1.0{contact}"


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.upload_dir.mkdir(parents=True, exist_ok=True)
