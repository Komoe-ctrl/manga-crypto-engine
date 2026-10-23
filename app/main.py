"""Point d'entrée FastAPI du moteur d'analyse.

Le moteur ne fait que produire des signaux. Il n'a pas accès aux clés API
et ne passe aucun ordre : c'est le rôle exclusif de crypto-api.
"""

from fastapi import FastAPI

from app.config import settings

app = FastAPI(title="Veille — crypto-engine", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.env}
