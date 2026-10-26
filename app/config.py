"""Configuration du moteur, lue depuis les variables d'environnement (.env)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "development"
    port: int = 8001

    # Redis : état du moteur et publication des signaux
    redis_url: str = "redis://localhost:6379"
    signals_stream: str = "signals"

    # Plateforme utilisée pour les données de marché PUBLIQUES (aucune clé API ici)
    market_exchange: str = "binance"
    scan_interval_seconds: int = 60

    # Scan : active la boucle d'analyse au démarrage (les tests la coupent)
    scanner_enabled: bool = True
    # Nombre de cryptos les plus liquides analysées (SPEC module 1 : 20)
    universe_size: int = 20
    # Liste fixe de symboles de base séparés par des virgules (« BTC,ETH ») ; vide : top par volume
    symbols: str = ""
    # Bougies analysées et nombre de bougies lues (EMA 50 a besoin de ~60 bougies pour se
    # stabiliser)
    signal_timeframe: str = "1h"
    candles_limit: int = 200

    # Bougies consécutives qui doivent donner la même position avant de publier (anti va-et-vient,
    # voir DECISIONS)
    confirm_candles: int = 3

    # Un signal n'est publié que si son score atteint ce seuil (l'API ignore de toute façon sous
    # 0,6)
    emit_min_score: float = 0.6
    # Premier passage sur un symbole : enregistre l'état sans publier, pour ne pas tout acheter au
    # démarrage
    emit_on_first_scan: bool = False
    # Publier aussi les HOLD (bruyant : l'API les enregistre sans agir)
    publish_hold: bool = False
    # Taille approximative maximale du stream `signals`
    stream_maxlen: int = 10_000


settings = Settings()
