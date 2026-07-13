"""Load and access server-controlled task profiles."""

from __future__ import annotations

import json
from pathlib import Path

from orchestrator.models.task_profile import (
    TaskProfile,
    TaskProfileError,
)


class TaskProfileRegistryError(RuntimeError):
    """Raised when the profile registry cannot be loaded."""


class TaskProfileRegistry:
    """Immutable task-profile registry loaded from JSON."""

    def __init__(
        self,
        profile_file: str | Path,
    ) -> None:
        self.profile_file = (
            Path(profile_file).expanduser().resolve()
        )
        self._profiles = self._load()

    def get(
        self,
        profile_id: str,
    ) -> TaskProfile:
        """Return one profile or raise a descriptive error."""

        profile = self._profiles.get(profile_id)

        if profile is None:
            raise TaskProfileRegistryError(
                f"Unknown task profile: {profile_id}"
            )

        return profile

    def list_profiles(self) -> list[TaskProfile]:
        """Return all profiles in stable ID order."""

        return [
            self._profiles[key]
            for key in sorted(self._profiles)
        ]

    def _load(self) -> dict[str, TaskProfile]:
        if not self.profile_file.is_file():
            raise TaskProfileRegistryError(
                f"Task-profile file does not exist: "
                f"{self.profile_file}"
            )

        try:
            with self.profile_file.open(
                "r",
                encoding="utf-8",
            ) as file:
                document = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            raise TaskProfileRegistryError(
                f"Cannot read task-profile file: "
                f"{self.profile_file}"
            ) from exc

        raw_profiles = document.get("profiles")

        if not isinstance(raw_profiles, list):
            raise TaskProfileRegistryError(
                "'profiles' must be a JSON list."
            )

        profiles: dict[str, TaskProfile] = {}

        try:
            for item in raw_profiles:
                if not isinstance(item, dict):
                    raise TaskProfileRegistryError(
                        "Every task profile must be an object."
                    )

                profile = TaskProfile.from_dict(item)

                if profile.profile_id in profiles:
                    raise TaskProfileRegistryError(
                        f"Duplicate task profile: "
                        f"{profile.profile_id}"
                    )

                profiles[profile.profile_id] = profile

        except (
            KeyError,
            TypeError,
            ValueError,
            TaskProfileError,
        ) as exc:
            raise TaskProfileRegistryError(
                f"Invalid task-profile configuration: {exc}"
            ) from exc

        if not profiles:
            raise TaskProfileRegistryError(
                "At least one task profile is required."
            )

        return profiles
