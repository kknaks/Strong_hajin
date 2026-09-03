from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import os


class RuntimeProfile(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


@dataclass(frozen=True, slots=True)
class Settings:
    profile: RuntimeProfile
    database_url: str

    @property
    def developer_auth_enabled(self) -> bool:
        return self.profile in {RuntimeProfile.DEVELOPMENT, RuntimeProfile.TEST}

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            profile=RuntimeProfile(os.getenv("AX_PROFILE", RuntimeProfile.DEVELOPMENT)),
            database_url=os.getenv(
                "DATABASE_URL", "postgresql+psycopg://ax:ax@localhost:5432/ax_demo"
            ),
        )

