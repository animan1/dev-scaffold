from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReleaseHealth:
    status: str
    environment: str
    revision: str
    image_prefix: str
    deployed_at: str

    @classmethod
    def from_payload(cls, payload: dict[str, str]) -> ReleaseHealth:
        return cls(
            status=payload["status"],
            environment=payload["environment"],
            revision=payload["revision"],
            image_prefix=payload["imagePrefix"],
            deployed_at=payload["deployedAt"],
        )

    def payload(self) -> dict[str, str]:
        return {
            "status": self.status,
            "environment": self.environment,
            "revision": self.revision,
            "imagePrefix": self.image_prefix,
            "deployedAt": self.deployed_at,
        }
