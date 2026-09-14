"""Challenge invocation-local population proofs at disk, worker and writer boundaries."""

# Future Library
from __future__ import annotations

# Standard Library
import asyncio
import hashlib
import json
import os
import socket

from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

# Third Party Library
import httpx
import pytest

from pydantic_ai import Agent
from pydantic_ai.usage import RunUsage

# Package Library
from kgfeg.config import Settings
from kgfeg.kgs import lp_checkpoints, lp_dispatch, lp_generation, lp_requests
from kgfeg.kgs.lp_dispatch import LPDispatchGate, LPDispatchIntegrityError
from kgfeg.kgs.utils import KGDirs
from tests.kgfeg.kgs import test_lp_checker as _checker
from tests.kgfeg.kgs import test_lp_dispatch_concurrency as _sdk
from tests.kgfeg.kgs import test_lp_generation as _fixtures
from tests.kgfeg.kgs import test_lp_orchestration as _old

_FILES = _fixtures._FILES


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forbid external transport and resolve only a synthetic model.

    Parameters
    ----------
    monkeypatch
        Restoring offline bindings.
    """
    reject = Mock(side_effect=AssertionError("Population tests are offline."))
    monkeypatch.setattr(name="connect", target=socket.socket, value=reject)
    monkeypatch.setattr(name="connect_ex", target=socket.socket, value=reject)
    monkeypatch.setattr(name="create_connection", target=socket, value=reject)
    monkeypatch.setattr(name="LLM_KG_MODEL", target=Settings, value="openai:gpt-5.2")


def _edit(*, kind: str, path: Path) -> None:
    """Change actual bytes without trusting file metadata or stored digests.

    Parameters
    ----------
    kind
        Independently selected corruption.
    path
        Synthetic input artifact.
    """
    original = path.read_bytes()
    stat = path.stat()
    if kind == "missing":
        path.unlink()
        return
    if kind == "duplicate":
        changed = original + original.splitlines(keepends=True)[0]
    elif kind == "reorder":
        lines = original.splitlines(keepends=True)
        changed = b"".join(reversed(lines)) if len(lines) > 1 else b" " + original
    elif kind == "same_size_restored_time":
        offset = original.index(b'"') + 1
        changed = original[:offset] + b"Z" + original[offset + 1 :]
    elif kind == "truncate":
        changed = original[:-3]
    else:
        changed = b"invalid\n"
    path.write_bytes(changed)
    if kind == "same_size_restored_time":
        os.utime(ns=(stat.st_atime_ns, stat.st_mtime_ns), path=path)
        assert path.stat().st_size == stat.st_size
        assert path.stat().st_mtime_ns == stat.st_mtime_ns


def _population(root: Path) -> lp_requests.LPRequestPopulation:
    """Materialize independently constructed bounded synthetic request inputs.

    Parameters
    ----------
    root
        Isolated fixture directory.

    Returns
    -------
    LPRequestPopulation
        Fully constructed input population.
    """
    bundle = _fixtures._bundle(4)
    for item in bundle.items:
        item.metadata["audit_probe"] = {
            "nullable": None,
            "nested": [1, "one", {"a": 1, "b": 2}],
        }
    return lp_requests.write_lp_generation_request_artifacts(
        as_lc_bundle=bundle,
        doc_key=_fixtures._DOC_KEY,
        kg_config=_fixtures._config(),
        kg_dirs=KGDirs(root=root),
    )


@pytest.mark.parametrize(argnames="kind", argvalues=["disk", "live"])
@pytest.mark.parametrize(argnames="provider", argvalues=_sdk._PROVIDERS)
def test_actual_sdk_transport_rechecks_retained_proof_after_hooks(
    kind: str, monkeypatch: pytest.MonkeyPatch, provider: str, tmp_path: Path
) -> None:
    """Actual transport startup rejects material changed after earlier admission.

    Parameters
    ----------
    kind
        Actual file or live-record mutation in the final HTTP hook.
    monkeypatch
        Keep the SDK and routing real while injecting socket-free transport.
    provider
        Existing supported provider adapter.
    tmp_path
        Complete validated input directory.
    """
    population = _population(tmp_path)
    verifier = lp_requests.LPRequestPopulationVerifier(
        population=population, root=tmp_path
    )
    gate = LPDispatchGate(
        check_material=partial(verifier.verify, population=population, root=tmp_path)
    )
    gate.check()
    model = _sdk._model(provider)
    agent = Agent(model=model, retries=0)
    original_client = httpx.AsyncClient
    transport = Mock(
        side_effect=AssertionError("Changed material reached actual transport.")
    )
    clients: list[httpx.AsyncClient] = []
    snapshots: list[dict[str, bytes]] = []

    async def _change(request: httpx.Request) -> None:
        """Edit material after SDK request preparation but before transport entry.

        Parameters
        ----------
        request
            Prepared SDK request, intentionally left unchanged.
        """
        del request
        if kind == "disk":
            _edit(kind="same_size_restored_time", path=tmp_path / _FILES[0])
        else:
            population.requests[0].sfis[0].context.audit_context["late"] = float("inf")
        snapshots.append(_old._snapshot(tmp_path))

    def _client(**kwargs: Any) -> httpx.AsyncClient:
        """Append an adversarial hook to the real client's original hook chain.

        Parameters
        ----------
        kwargs
            Original private-client arguments.

        Returns
        -------
        AsyncClient
            Real offline client retaining routing and dispatch wrappers.
        """
        kwargs["event_hooks"]["request"].append(_change)
        client = original_client(transport=httpx.MockTransport(transport), **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(
        name="httpx", target=lp_dispatch, value=SimpleNamespace(AsyncClient=_client)
    )
    usage = RunUsage()
    with asyncio.Runner() as runner:
        runner.get_loop()
        with pytest.raises(expected_exception=LPDispatchIntegrityError):
            lp_dispatch.run_lp_agent_attempt(
                agent=agent,
                dispatch_guard=gate.check,
                usage=usage,
                user_prompt="Offline integrity probe",
            )
        runner.run(model.client.close())
    assert snapshots
    assert _old._snapshot(tmp_path) == snapshots[-1]
    transport.assert_not_called()
    assert all(client.is_closed for client in clients)
    assert usage.requests == 0


@pytest.mark.parametrize(argnames="filename", argvalues=_FILES)
@pytest.mark.parametrize(
    argnames="kind",
    argvalues=[
        "corrupt",
        "duplicate",
        "missing",
        "reorder",
        "same_size_restored_time",
        "truncate",
    ],
)
@pytest.mark.parametrize(argnames="phase", argvalues=["cached", "initial"])
def test_all_artifact_changes_reject_without_repair(
    filename: str, kind: str, phase: str, tmp_path: Path
) -> None:
    """Initial validation and retained proofs both reject changed actual bytes.

    Parameters
    ----------
    filename
        Each complete population artifact.
    kind
        Corruption independent of implementation hashes.
    phase
        Initial proof creation or repeated boundary verification.
    tmp_path
        Isolated material directory.
    """
    population = _population(tmp_path)
    verifier = (
        lp_requests.LPRequestPopulationVerifier(population=population, root=tmp_path)
        if phase == "cached"
        else None
    )
    _edit(kind=kind, path=tmp_path / filename)
    before = _old._snapshot(tmp_path)
    with pytest.raises(expected_exception=(ValueError, OSError)):
        if verifier is None:
            lp_requests.LPRequestPopulationVerifier(
                population=population, root=tmp_path
            )
        else:
            verifier.verify(population=population, root=tmp_path)
    assert _old._snapshot(tmp_path) == before


@pytest.mark.parametrize(argnames="capacity", argvalues=[1, 4])
@pytest.mark.parametrize(
    argnames="boundary", argvalues=["before_transport", "returning_call"]
)
def test_changed_worker_evidence_closes_dispatch_and_publication(
    boundary: str, capacity: int, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Nested worker changes cannot reach transport or become reusable results.

    Parameters
    ----------
    boundary
        Change before the final guard or while the fake call is active.
    capacity
        Serial or default bounded admission.
    monkeypatch
        Replace only the provider call boundary.
    tmp_path
        Temporary production invocation.
    """
    harness = _old._Harness(root=tmp_path)
    harness.config.learning_progressions.max_concurrent_requests = capacity
    dispatched: list[int] = []
    snapshot: dict[str, bytes] = {}
    live: list[lp_requests.LPRequestPopulation] = []
    original_verifier = lp_requests.LPRequestPopulationVerifier

    def _bind(*, population: lp_requests.LPRequestPopulation, root: Path) -> Any:
        """Retain the authoritative population without substituting its validation.

        Parameters
        ----------
        population
            Live invocation material, distinct from isolated worker copies.
        root
            Complete canonical artifact directory.

        Returns
        -------
        Any
            Real validated proof.
        """
        live.append(population)
        return original_verifier(population=population, root=root)

    monkeypatch.setattr(
        name="LPRequestPopulationVerifier", target=lp_generation, value=_bind
    )

    def _call(**kwargs: Any) -> Any:
        """Mutate the actual worker-owned record at the specified boundary.

        Parameters
        ----------
        kwargs
            Original worker call arguments.

        Returns
        -------
        Any
            Synthetic draft only when transport was admitted.
        """
        gate = kwargs["dispatch_guard"].__self__
        request = kwargs["request"]
        with gate.coordinate():
            if boundary == "returning_call":
                kwargs["dispatch_guard"]()
                dispatched.append(request.request_index)
            draft = _checker._draft(request)
            live[0].requests[0].sfis[0].context.audit_context["injected"] = [
                float("nan")
            ]
            snapshot.update(_old._snapshot(tmp_path))
            if boundary == "before_transport":
                kwargs["dispatch_guard"]()
                dispatched.append(request.request_index)
            return draft

    monkeypatch.setattr(
        name="generate_learning_progressions_for_request",
        target=lp_generation,
        value=_call,
    )
    with pytest.raises(expected_exception=ValueError):
        harness._run()
    assert len(dispatched) == int(boundary == "returning_call")
    assert _old._snapshot(tmp_path) == snapshot
    assert not _old._rows(tmp_path / _old._DRAFT)


def test_each_new_invocation_revalidates_before_reuse(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A completed run cannot carry its retained proof into a later invocation.

    Parameters
    ----------
    monkeypatch
        Observe full reader calls without replacing validation.
    tmp_path
        Reusable synthetic completed directory.
    """
    harness = _old._Harness(count=2, root=tmp_path)
    _old._install(harness=harness, monkeypatch=monkeypatch)
    harness._run()
    reads = Mock(wraps=lp_requests.read_lp_request_population)
    monkeypatch.setattr(
        name="read_lp_request_population", target=lp_requests, value=reads
    )
    harness._run()
    assert reads.call_count >= 1
    assert harness.calls == [("draft", 0), ("verdict", 0)]
    _edit(kind="same_size_restored_time", path=tmp_path / _FILES[2])
    before = _old._snapshot(tmp_path)
    with pytest.raises(expected_exception=ValueError):
        harness._run()
    assert harness.calls == [("draft", 0), ("verdict", 0)]
    assert _old._snapshot(tmp_path) == before


@pytest.mark.parametrize(argnames="kind", argvalues=["disk", "live"])
def test_initial_proof_cannot_absorb_change_after_reader_validation(
    kind: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Snapshot setup rechecks material changed while its full reader returned.

    Parameters
    ----------
    kind
        Disk or nested live mutation during proof initialization.
    monkeypatch
        Wrap the complete reader without weakening its validations.
    tmp_path
        Isolated complete input population.
    """
    population = _population(tmp_path)
    original = lp_requests.read_lp_request_population
    snapshots: list[dict[str, bytes]] = []

    def _read(*, expected: lp_requests.LPRequestPopulation, root: Path) -> Any:
        """Introduce a change only after the real full validation succeeds.

        Parameters
        ----------
        expected
            Authoritative independently constructed population.
        root
            Canonical material directory.

        Returns
        -------
        Any
            Real validated reader output.
        """
        validated = original(expected=expected, root=root)
        if kind == "disk":
            _edit(kind="same_size_restored_time", path=root / _FILES[2])
        else:
            expected.requests[0].sfis[0].context.audit_context["new"] = {
                "value": float("nan")
            }
        snapshots.append(_old._snapshot(root))
        return validated

    monkeypatch.setattr(
        name="read_lp_request_population", target=lp_requests, value=_read
    )
    with pytest.raises(expected_exception=ValueError):
        lp_requests.LPRequestPopulationVerifier(population=population, root=tmp_path)
    assert _old._snapshot(tmp_path) == snapshots[-1]


@pytest.mark.parametrize(
    argnames="mutation",
    argvalues=[
        "candidate",
        "manifest",
        "nomination",
        "request_order",
        "summary",
        "text",
    ],
)
def test_live_population_material_changes_reject(mutation: str, tmp_path: Path) -> None:
    """Every live population layer remains covered by the retained material proof.

    Parameters
    ----------
    mutation
        Material layer mutated after complete validation.
    tmp_path
        Valid canonical artifact directory.
    """
    population = _population(tmp_path)
    verifier = lp_requests.LPRequestPopulationVerifier(
        population=population, root=tmp_path
    )
    if mutation == "candidate":
        population.candidates.candidates[0].warnings += ("changed",)
    elif mutation == "manifest":
        population.manifest.artifact_byte_hashes[_FILES[0]] = "0" * 64
    elif mutation == "nomination":
        population.requests[0].pairs[0].nomination.evidence.text += " changed"
    elif mutation == "request_order":
        object.__setattr__(population, "requests", tuple(reversed(population.requests)))
    elif mutation == "summary":
        population.candidates.summary.config_content_hash = "0" * 64
    else:
        population.requests[0].sfis[0].context.description.text += "changed"
    with pytest.raises(expected_exception=ValueError, match="in-memory"):
        verifier.verify(population=population, root=tmp_path)


@pytest.mark.parametrize(
    argnames="kind", argvalues=["dictionary_value", "list_order", "list_value"]
)
def test_nested_container_edits_reject(kind: str, tmp_path: Path) -> None:
    """Nested list order and dictionary values participate in live integrity.

    Parameters
    ----------
    kind
        Semantic JSON mutation beneath nested audit evidence.
    tmp_path
        Isolated input snapshot.
    """
    population = _population(tmp_path)
    verifier = lp_requests.LPRequestPopulationVerifier(
        population=population, root=tmp_path
    )
    nested = next(iter(population.requests[0].sfis[0].context.audit_context.values()))[
        "nested"
    ]
    if kind == "dictionary_value":
        nested[2]["a"] = "1"
    elif kind == "list_order":
        nested.reverse()
    else:
        nested[0] = True
    with pytest.raises(expected_exception=ValueError, match="in-memory"):
        verifier.verify(population=population, root=tmp_path)


@pytest.mark.parametrize(
    argnames="value",
    argvalues=[
        float("nan"),
        float("inf"),
        -float("inf"),
        0,
        "0",
        False,
        [],
        {},
        [None],
        {"nested": [1]},
        object(),
    ],
)
def test_nested_null_mutations_never_alias_validated_material(
    tmp_path: Path, value: Any
) -> None:
    """Invalid non-finite values and changed JSON types cannot alias original null.

    Parameters
    ----------
    tmp_path
        Isolated validated input files.
    value
        Changed nested value, including unsupported serialization.
    """
    population = _population(tmp_path)
    context = population.requests[0].sfis[0].context.audit_context
    assert context, "The fixture must retain real nested audit context."
    nested = next(iter(context.values()))
    assert isinstance(nested, dict)
    assert nested["nullable"] is None
    verifier = lp_requests.LPRequestPopulationVerifier(
        population=population, root=tmp_path
    )
    nested["nullable"] = value
    with pytest.raises(expected_exception=(ValueError, TypeError)):
        verifier.verify(population=population, root=tmp_path)


@pytest.mark.parametrize(argnames="phase", argvalues=["cached", "initial"])
def test_resealed_manifest_does_not_authorize_modified_population(
    phase: str, tmp_path: Path
) -> None:
    """Self-consistent disk hashes cannot override the authoritative input population.

    Parameters
    ----------
    phase
        Before or after validated snapshot creation.
    tmp_path
        Isolated tampered input directory.
    """
    population = _population(tmp_path)
    verifier = (
        lp_requests.LPRequestPopulationVerifier(population=population, root=tmp_path)
        if phase == "cached"
        else None
    )
    path = tmp_path / _FILES[2]
    _edit(kind="same_size_restored_time", path=path)
    manifest_path = tmp_path / _FILES[3]
    manifest = json.loads(manifest_path.read_bytes())
    manifest["artifact_byte_hashes"][_FILES[2]] = hashlib.sha256(
        path.read_bytes()
    ).hexdigest()
    manifest_path.write_bytes(_old._bytes(manifest))
    before = _old._snapshot(tmp_path)
    with pytest.raises(expected_exception=ValueError):
        if verifier is None:
            lp_requests.LPRequestPopulationVerifier(
                population=population, root=tmp_path
            )
        else:
            verifier.verify(population=population, root=tmp_path)
    assert _old._snapshot(tmp_path) == before


def test_transaction_publication_rechecks_changed_population(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A material change after transaction publication stops remaining replacements.

    Parameters
    ----------
    monkeypatch
        Inject a file edit after the real atomic transaction write.
    tmp_path
        Fresh synthetic invocation.
    """
    harness = _old._Harness(root=tmp_path)
    _old._install(harness=harness, monkeypatch=monkeypatch)
    original = lp_checkpoints._atomic_write
    snapshot: dict[str, bytes] = {}

    def _write(*, path: Path, payload: bytes) -> None:
        """Corrupt request bytes after a durable transaction has been published.

        Parameters
        ----------
        path
            Real destination.
        payload
            Unchanged real transaction bytes.
        """
        original(path=path, payload=payload)
        if path.name == _old._JOURNAL:
            _edit(kind="same_size_restored_time", path=tmp_path / _FILES[2])
            snapshot.update(_old._snapshot(tmp_path))

    monkeypatch.setattr(name="_atomic_write", target=lp_checkpoints, value=_write)
    with pytest.raises(expected_exception=ValueError, match="artifact"):
        harness._run()
    assert harness.calls == []
    assert _old._snapshot(tmp_path) == snapshot


@pytest.mark.parametrize(argnames="phase", argvalues=["cached", "initial"])
def test_valid_alternative_population_cannot_replace_bound_authority(
    phase: str, tmp_path: Path
) -> None:
    """Even fully valid replacement artifacts cannot grant their own input authority.

    Parameters
    ----------
    phase
        Before or after invocation-local proof creation.
    tmp_path
        Isolated original and independently valid replacement populations.
    """
    root = tmp_path / "original"
    population = _population(root)
    verifier = (
        lp_requests.LPRequestPopulationVerifier(population=population, root=root)
        if phase == "cached"
        else None
    )
    changed = _fixtures._bundle(4)
    changed.items[0].description += " Changed authoritative evidence."
    replacement_root = tmp_path / "replacement"
    replacement = lp_requests.write_lp_generation_request_artifacts(
        as_lc_bundle=changed,
        doc_key=_fixtures._DOC_KEY,
        kg_config=_fixtures._config(),
        kg_dirs=KGDirs(root=replacement_root),
    )
    for filename in _FILES:
        (root / filename).write_bytes((replacement_root / filename).read_bytes())
    # The replacement passes full independent input reconciliation against its own
    # upstream authority; rejection below must come from the original binding.
    lp_requests.read_lp_request_population(expected=replacement, root=root)
    before = _old._snapshot(root)
    with pytest.raises(expected_exception=ValueError):
        if verifier is None:
            lp_requests.LPRequestPopulationVerifier(population=population, root=root)
        else:
            verifier.verify(population=population, root=root)
    assert _old._snapshot(root) == before
