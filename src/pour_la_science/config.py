"""Configuration for the Pour la Science client."""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv


@dataclass(frozen=True)
class PourLaScienceConfig:
    email: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    archives_url: str = "https://www.pourlascience.fr/archives"
    timeout: float = 20.0

    @classmethod
    def from_env(cls) -> "PourLaScienceConfig":
        load_dotenv()
        return cls(
            email=os.getenv("POURLASCIENCE_EMAIL", "").strip(),
            password=os.getenv("POURLASCIENCE_PASSWORD", ""),
        )
