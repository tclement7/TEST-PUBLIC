"""Outil local d'aide au contrôle des ratios réglementaires OPCVM.

Cet outil est une aide au contrôle : ses résultats ne constituent pas une validation
réglementaire définitive et doivent être revus par un contrôleur.
"""

__version__ = "0.1.0"
# Version du schéma de calcul : à incrémenter dès qu'un calculateur change de comportement.
ENGINE_VERSION = f"{__version__}+calc1"

DISCLAIMER = (
    "Outil d'aide au contrôle : les résultats présentés ne constituent pas une validation "
    "réglementaire définitive et doivent être revus par un contrôleur."
)
