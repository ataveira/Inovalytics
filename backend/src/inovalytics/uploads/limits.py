"""Limites de upload (valores vêm de `Settings`, variáveis UPLOAD_*)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

HEADER_BYTES = 1024 * 1024  # a inspeção nunca olha além de 1 MiB
READ_CHUNK = 64 * 1024


@dataclass(frozen=True)
class UploadLimits:
    max_bytes: int = 20 * 1024 * 1024
    max_files: int = 20
    max_dimension: int = 16384
    max_pixels: int = 100_000_000

    @classmethod
    def from_settings(cls, settings: Any) -> UploadLimits:
        return cls(
            max_bytes=settings.upload_max_bytes,
            max_files=settings.upload_max_files,
            max_dimension=settings.upload_max_dimension,
            max_pixels=settings.upload_max_pixels,
        )
