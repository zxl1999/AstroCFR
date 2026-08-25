"""Reusable AstroCFR modules for crowded-field candidate recovery."""

from .quality_flags import BIT_DEFINITION, build_quality_bitmask

__version__ = "1.6.2"

__all__ = ["BIT_DEFINITION", "build_quality_bitmask"]
