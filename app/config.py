"""Configuration du moteur, lue depuis les variables d'environnement (.env)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "development"
    port: int = 8001

    # Redis : lecture du cache de prix, publication des signaux
    redis_url: str = "redis://localhost:6379"
    signals_stream: str = "signals"

    # Plateforme utilisée pour les données de marché PUBLIQUES (aucune clé API ici)
    market_exchange: str = "binance"
    scan_interval_seconds: int = 60


settings = Settings()
