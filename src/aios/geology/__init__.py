"""Геологическая структура, выведенная из самой колоды."""

from aios.geology.regions import (
    RegionDerivationError,
    WellRegionMap,
    derive_well_regions,
    geology_for_deck,
    parse_cell_array,
    parse_grid_dimensions,
)

__all__ = [
    "RegionDerivationError",
    "WellRegionMap",
    "derive_well_regions",
    "geology_for_deck",
    "parse_cell_array",
    "parse_grid_dimensions",
]
