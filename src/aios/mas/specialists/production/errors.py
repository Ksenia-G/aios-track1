"""Production Controller error hierarchy."""

from __future__ import annotations


class ProductionControllerError(ValueError):
    """Base class for deterministic Production Controller failures."""


class InputValidationError(ProductionControllerError):
    """The ProductionEnvelope is outside the controller contract."""


class EvidenceBuildError(ProductionControllerError):
    """Evidence cannot be made unique, grounded, or non-prescriptive."""


class StrictOutputError(ProductionControllerError):
    """A provider response does not match the strict JSON schema."""


class VocabularyValidationError(ProductionControllerError):
    """Parsed output violates the frozen production vocabulary."""


class ProposalCompilationError(ProductionControllerError):
    """The complete model response cannot be compiled atomically."""
