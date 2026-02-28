"""
tests/test_evidence_resolution.py
=================================

The rules that let an ambiguous call name be settled by evidence, instead of
being abandoned.

THE FAILURE THESE EXIST TO PREVENT
----------------------------------
A call is recorded by bare NAME, and cross-file resolution refused to link
whenever more than one definition answered to that name. In general that is
the right call - a wrong edge silently sends retrieval to unrelated code.

But it is wrong in the commonest situation in object-oriented code: a
subclass overriding a parent's method. `AuthService.login` and
`AdminAuthService.login` collided, the edge from `handle_login` was dropped,
and on a five-language demo repository the ENTIRE auth module - nine
entities - became an isolated island that no traversal could reach.

The calling code was never ambiguous about it. It says `auth: AuthService`
in its own signature. These tests pin the rules that read that, and - just
as importantly - the boundaries that stop them being used to guess.
"""

import pytest

from graph_rag.pipeline.indexer import (
    LINKED_REASONS,
    RESOLUTION_CONFIDENCE,
    resolve_reference,
)


def _candidate(
    entity_id,
    path,
    language="python",
    kind="function",
    owner=None,
    params=None,
    flexible=False,
):
    return {
        "id": entity_id,
        "filePath": path,
        "language": language,
        "kind": kind,
        "ownerName": owner,
        "paramCount": params,
        "flexibleArity": flexible,
    }


# The exact shape of the real failure: a base class and a subclass that
# overrides its method, both visible to the caller.
_OVERRIDE_CANDIDATES = [
    _candidate("auth.py::AuthService.login", "auth.py", kind="method"),
    _candidate("auth.py::AdminAuthService.login", "auth.py", kind="method"),
]


# ---------------------------------------------------------------------------
# The receiver's declared type
# ---------------------------------------------------------------------------

def test_an_override_collision_is_settled_by_the_declared_receiver_type():
    """
    THE BUG THIS PINS. `handle_login(request, auth: AuthService)` calling
    `auth.login(...)` means the AuthService one - and losing this edge cost
    an entire module its place in the graph.
    """
    target, reason = resolve_reference(
        _OVERRIDE_CANDIDATES,
        source_id="main.py::handle_login",
        source_language="python",
        owner_hint="AuthService",
    )
    assert reason == "receiver-type"
    assert target == "auth.py::AuthService.login"


def test_the_subclass_is_chosen_when_the_signature_names_the_subclass():
    """The rule reads the annotation; it does not prefer base classes."""
    target, reason = resolve_reference(
        _OVERRIDE_CANDIDATES,
        source_id="main.py::handle_admin",
        source_language="python",
        owner_hint="AdminAuthService",
    )
    assert reason == "receiver-type"
    assert target == "auth.py::AdminAuthService.login"


def test_a_stored_owner_name_is_matched_as_well_as_an_id():
    """
    Go and Rust record the owning type in `ownerName` rather than nesting it
    in the id, so both spellings have to be understood.
    """
    candidates = [
        _candidate("a.go::Consumer.Handle", "a.go", "go", "method", owner="Consumer"),
        _candidate("b.go::Producer.Handle", "b.go", "go", "method", owner="Producer"),
    ]
    target, reason = resolve_reference(
        candidates,
        source_id="c.go::run",
        source_language="go",
        owner_hint="Consumer",
    )
    assert reason == "receiver-type"
    assert target == "a.go::Consumer.Handle"


def test_a_hint_matching_nothing_is_discarded_rather_than_ruling_all_out():
    """
    A capitalised receiver is not always a type - `Math.max()` names a
    namespace. Evidence that fits no candidate is evidence about something
    OUTSIDE the graph, so it must not be allowed to eliminate every
    candidate and turn a resolvable name into a dead one.
    """
    target, reason = resolve_reference(
        _OVERRIDE_CANDIDATES,
        source_id="main.py::handler",
        source_language="python",
        owner_hint="SomethingElse",
    )
    # Still ambiguous - which is what it was before - and NOT a wrong link.
    assert reason == "ambiguous"
    assert target is None


def test_a_hint_never_overturns_a_name_that_was_already_unique():
    """
    The extra evidence may only ever NARROW. A single candidate is decided
    before any of it is consulted, so a wrong annotation cannot break a link
    that was never in doubt.
    """
    target, reason = resolve_reference(
        [_candidate("db/repo.py::save", "db/repo.py")],
        source_id="api/service.py::store",
        source_language="python",
        owner_hint="CompletelyUnrelated",
    )
    assert reason == "linked"
    assert target == "db/repo.py::save"


def test_omitting_the_evidence_reproduces_the_old_behaviour_exactly():
    """
    A graph indexed before these properties existed has no hints stored, and
    must keep resolving exactly as it did rather than degrading.
    """
    assert resolve_reference(
        _OVERRIDE_CANDIDATES,
        source_id="main.py::handle_login",
        source_language="python",
    ) == (None, "ambiguous")


# ---------------------------------------------------------------------------
# Arity, the last resort
# ---------------------------------------------------------------------------

def test_argument_count_separates_two_overloads_of_one_name():
    candidates = [
        _candidate("Svc.java::Svc.make", "Svc.java", "java", "method",
                   owner="Svc", params=1),
        _candidate("Svc.java::Other.make", "Svc.java", "java", "method",
                   owner="Other", params=2),
    ]
    target, reason = resolve_reference(
        candidates,
        source_id="App.java::App.run",
        source_language="java",
        call_arity=2,
    )
    assert reason == "arity"
    assert target == "Svc.java::Other.make"


def test_a_flexible_signature_is_never_ruled_out_by_arity():
    """
    THE DANGEROUS CASE, AND WHY THE FLEXIBLE FLAG EXISTS.

    `def save(self, a, b=None, *rest)` legally accepts one argument. If arity
    were applied naively it would eliminate the RIGHT candidate and leave
    exactly one WRONG one - turning a missing edge, which is recoverable,
    into a confident wrong edge, which is not.
    """
    candidates = [
        _candidate("a.py::A.save", "a.py", kind="method", owner="A",
                   params=3, flexible=True),
        _candidate("b.py::B.save", "b.py", kind="method", owner="B", params=9),
    ]
    target, reason = resolve_reference(
        candidates,
        source_id="c.py::run",
        source_language="python",
        call_arity=1,
    )
    # The flexible one survives on its own, so it is chosen - not eliminated
    # for declaring three parameters.
    assert reason == "arity"
    assert target == "a.py::A.save"


def test_an_unknown_parameter_count_never_eliminates_a_candidate():
    """A grammar we could not read an arity from must not lose by default."""
    candidates = [
        _candidate("a.py::A.save", "a.py", kind="method", owner="A", params=None),
        _candidate("b.py::B.save", "b.py", kind="method", owner="B", params=None),
    ]
    target, reason = resolve_reference(
        candidates,
        source_id="c.py::run",
        source_language="python",
        call_arity=2,
    )
    assert reason == "ambiguous"
    assert target is None


def test_arity_matching_nothing_is_ignored_rather_than_ruling_all_out():
    candidates = [
        _candidate("a.py::A.save", "a.py", kind="method", owner="A", params=1),
        _candidate("b.py::B.save", "b.py", kind="method", owner="B", params=2),
    ]
    target, reason = resolve_reference(
        candidates,
        source_id="c.py::run",
        source_language="python",
        call_arity=7,
    )
    assert reason == "ambiguous"
    assert target is None


# ---------------------------------------------------------------------------
# The order the evidence is applied in
# ---------------------------------------------------------------------------

def test_the_receiver_type_is_consulted_before_arity():
    """
    Receiver type is the stronger signal, so it must decide first. Here the
    two disagree: arity alone would pick B, the declared type says A.
    """
    candidates = [
        _candidate("a.py::A.save", "a.py", kind="method", owner="A", params=1),
        _candidate("b.py::B.save", "b.py", kind="method", owner="B", params=2),
    ]
    target, reason = resolve_reference(
        candidates,
        source_id="c.py::run",
        source_language="python",
        owner_hint="A",
        call_arity=2,
    )
    assert reason == "receiver-type"
    assert target == "a.py::A.save"


def test_language_family_still_wins_over_every_new_signal():
    """
    A Python `login` and a TypeScript `login` are unrelated words, and no
    amount of matching receiver types may join them.
    """
    candidates = [
        _candidate("web/api.ts::login", "web/api.ts", "typescript",
                   owner="AuthService"),
    ]
    target, reason = resolve_reference(
        candidates,
        source_id="api/main.py::handle_login",
        source_language="python",
        owner_hint="AuthService",
    )
    assert reason == "external"
    assert target is None


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("reason", sorted(LINKED_REASONS))
def test_every_linking_reason_has_a_confidence(reason):
    assert reason in RESOLUTION_CONFIDENCE


def test_weaker_evidence_carries_lower_confidence():
    """
    The ordering is the whole point: retrieval multiplies a neighbour's
    relevance by this, so an edge resolved by counting arguments loses to a
    certainty when the context budget is tight.
    """
    assert (
        RESOLUTION_CONFIDENCE["same-file"]
        > RESOLUTION_CONFIDENCE["linked"]
        > RESOLUTION_CONFIDENCE["receiver-type"]
        > RESOLUTION_CONFIDENCE["arity"]
    )
