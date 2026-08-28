from __future__ import annotations

import inspect

import pytest

from braille_errata_relay.braille.errors import IncompatibleBaselineError, ProfileNotReadyError
from braille_errata_relay.braille.profile import (
    load_translation_profile,
    profile_sha256,
    require_bound_profile,
    require_compatible_profile,
)
from braille_errata_relay.domain.models import assert_no_production_control_fields
from braille_errata_relay.domain.ports import ProductionObserver


def test_profile_is_versioned_but_unbound_until_gate_zero() -> None:
    profile = load_translation_profile("config/translation_profiles/demo-ueb-40x25-v1.json")
    assert profile.profile_id == "demo-ueb-40x25-v1"
    assert profile.is_bound is False
    assert len(profile_sha256(profile)) == 64
    with pytest.raises(ProfileNotReadyError):
        require_bound_profile(profile)


def test_incompatible_baseline_fails_closed() -> None:
    with pytest.raises(IncompatibleBaselineError):
        require_compatible_profile(baseline_profile_sha256="a" * 64, candidate_profile_sha256="b" * 64)


def test_untrusted_payload_cannot_smuggle_production_control_fields() -> None:
    with pytest.raises(ValueError, match="production-control"):
        assert_no_production_control_fields({"summary": {"cancel": "cups"}})


def test_observer_protocol_has_no_mutation_surface() -> None:
    names = {
        name
        for name, member in inspect.getmembers(ProductionObserver)
        if callable(member) and not name.startswith("_")
    }
    assert names == {"latest_snapshot", "job_history"}
    assert not names.intersection({"print", "submit", "cancel", "hold", "release", "execute"})

