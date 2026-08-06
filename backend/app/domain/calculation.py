from __future__ import annotations

from dataclasses import dataclass
import re

from app.engine.canonical import CanonicalValue, canonical_fingerprint


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CalculationIdentityError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CalculationIdentity(CanonicalValue):
    calculation_kind: str
    schema_version: int
    request_fingerprint: str
    input_artifact_fingerprints: tuple[str, ...]
    code_revision: str

    def __post_init__(self) -> None:
        if not isinstance(self.calculation_kind, str) or not self.calculation_kind.strip():
            raise CalculationIdentityError("calculation_kind must be non-empty")
        if isinstance(self.schema_version, bool) or self.schema_version < 1:
            raise CalculationIdentityError("schema_version must be a positive integer")
        if not _SHA256.fullmatch(self.request_fingerprint):
            raise CalculationIdentityError("request_fingerprint must be lowercase sha256")
        fingerprints = tuple(sorted(self.input_artifact_fingerprints))
        if not fingerprints or any(not _SHA256.fullmatch(item) for item in fingerprints):
            raise CalculationIdentityError(
                "input_artifact_fingerprints must contain lowercase sha256 values"
            )
        if len(fingerprints) != len(set(fingerprints)):
            raise CalculationIdentityError("input artifact fingerprints must be unique")
        object.__setattr__(self, "input_artifact_fingerprints", fingerprints)
        if not isinstance(self.code_revision, str) or not self.code_revision.strip():
            raise CalculationIdentityError("code_revision must be non-empty")

    @property
    def calculation_fingerprint(self) -> str:
        return canonical_fingerprint(self.canonical_payload())


__all__ = ["CalculationIdentity", "CalculationIdentityError"]
