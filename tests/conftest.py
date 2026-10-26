import os

# Les tests ne lancent jamais la boucle d'analyse (réseau, Redis) : à poser avant l'import de
# l'application.
os.environ["SCANNER_ENABLED"] = "false"
