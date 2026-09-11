"""Regression tests for the shared deployment-identity resolver.

Bug being locked: only the Prefect worker launcher pinned
``ASHARE_APPLICATION_REVISION``; the API resolved ``"unknown"``, so the
execution_version hashed at enqueue differed from the one rehashed by the
worker and every cockpit manual run failed the PREPARE version guard.
"""
from __future__ import annotations

import app.core.deployment_identity as deployment_identity
from app.services.runs.contracts import application_identity_from_environment


def test_explicit_environ_valid_revision_wins_and_lowercases():
    revision = deployment_identity.resolve_application_revision(
        {"ASHARE_APPLICATION_REVISION": "AbC1234"}
    )
    assert revision == "abc1234"


def test_explicit_empty_environ_is_pure_unknown(monkeypatch):
    def _forbidden():
        raise AssertionError("injected environ must not touch git")

    monkeypatch.setattr(deployment_identity, "_cached_git_head_revision", _forbidden)
    assert deployment_identity.resolve_application_revision({}) == "unknown"
    assert deployment_identity.resolve_application_revision(
        {"ASHARE_APPLICATION_REVISION": "not a revision!"}
    ) == "unknown"


def test_default_path_is_stable_and_mirrors_into_environ(monkeypatch):
    monkeypatch.delenv("ASHARE_APPLICATION_REVISION", raising=False)
    monkeypatch.setattr(
        deployment_identity, "_cached_git_head_revision", lambda: "deadbeefcafe1234"
    )
    first = deployment_identity.resolve_application_revision()
    second = deployment_identity.resolve_application_revision()
    assert first == second == "deadbeefcafe1234"
    # Spawned worker subprocesses inherit the same revision.
    assert __import__("os").environ["ASHARE_APPLICATION_REVISION"] == "deadbeefcafe1234"


def test_application_identity_default_path_matches_shared_revision(monkeypatch):
    monkeypatch.delenv("ASHARE_APPLICATION_REVISION", raising=False)
    monkeypatch.setattr(
        deployment_identity, "_cached_git_head_revision", lambda: "c" * 40
    )
    identity = application_identity_from_environment()
    assert identity.revision == "c" * 40
    assert identity.version == "development"


def test_application_version_explicit_environ():
    identity = application_identity_from_environment(
        {"ASHARE_APPLICATION_VERSION": "1.2.3"}
    )
    assert identity.version == "1.2.3"
    assert identity.revision == "unknown"
