"""Validation engine (interface-first).

This module will later host dry-run/staging/simulation validators, but starts
as a small deterministic interface.
"""

from incident_agent.validation.engine import validate_plan

__all__ = ["validate_plan"]

