"""
tests/test_call_hints.py
========================

The PARSER half of evidence-based call resolution.

`tests/test_evidence_resolution.py` pins the rules that USE the evidence.
This file pins that the evidence is actually read out of real source, in
every language shape that matters - because a rule with nothing to read is
just as broken as a rule that is wrong, and fails just as silently.

What has to be extracted, per definition:

  call_owner_hints   {called name: the type its receiver was DECLARED to be}
  call_arity_hints   {called name: how many arguments the call passed}
  param_count        what a caller passes to THIS definition
  flexible_arity     whether that count can be relied on at all
"""
import pytest
from graph_rag.parsing import parse_file

def _entity(parsed, name):
    for entity in parsed.entities:
        if entity.name == name:
            return entity
    raise AssertionError(f'no entity named {name!r}; got {sorted((e.name for e in parsed.entities))}')

def _parse(tmp_path, filename, source):
    path = tmp_path / filename
    path.write_text(source, encoding='utf-8')
    parsed = parse_file(path, filename)
    assert parsed is not None, f'{filename}: parser returned nothing'
    return parsed

def test_a_python_annotation_names_the_receiver_type(tmp_path):
    """
    THE CASE THE WHOLE FEATURE EXISTS FOR.

    `auth: AuthService` is the fact that tells `auth.login()` apart from a
    subclass's override of the same name.
    """
    parsed = _parse(tmp_path, 'main.py', "def handle_login(request, auth: AuthService):\n    return auth.login(request['email'], request['password'])\n")
    assert _entity(parsed, 'handle_login').call_owner_hints == {'login': 'AuthService'}

def test_a_typescript_annotation_names_the_receiver_type(tmp_path):
    parsed = _parse(tmp_path, 'app.ts', 'export function run(svc: OrderService) {\n  return svc.checkout();\n}\n')
    assert _entity(parsed, 'run').call_owner_hints == {'checkout': 'OrderService'}

def test_a_java_parameter_type_names_the_receiver_type(tmp_path):
    parsed = _parse(tmp_path, 'App.java', 'public class App {\n    public int run(TaxCalculator calc) {\n        return calc.applyTax(100);\n    }\n}\n')
    assert _entity(parsed, 'run').call_owner_hints == {'applyTax': 'TaxCalculator'}

def test_self_resolves_to_the_enclosing_class(tmp_path):
    """
    `self` is the receiver in most method bodies, and its type is never
    written down - it is the class the method sits in.
    """
    parsed = _parse(tmp_path, 'auth.py', 'class AuthService:\n    def login(self, email):\n        return self.issue_token(email)\n\n    def issue_token(self, user_id):\n        return user_id\n')
    assert _entity(parsed, 'login').call_owner_hints == {'issue_token': 'AuthService'}

def test_a_capitalised_receiver_is_taken_as_the_type_itself(tmp_path):
    """
    `TaxCalculator.applyTax()` - a static call or a constructor. Safe to use
    because a hint matching no candidate is discarded by the resolver rather
    than allowed to rule everything out.
    """
    parsed = _parse(tmp_path, 'billing.py', 'def total(amount):\n    return TaxCalculator.apply(amount)\n')
    assert _entity(parsed, 'total').call_owner_hints == {'apply': 'TaxCalculator'}

def test_a_plain_function_call_produces_no_hint(tmp_path):
    """No receiver, nothing to say about which definition was meant."""
    parsed = _parse(tmp_path, 'p.py', 'def outer(v):\n    return helper(v)\n')
    assert _entity(parsed, 'outer').call_owner_hints == {}

def test_a_lowercase_unknown_receiver_produces_no_hint(tmp_path):
    """
    `auth.login()` where `auth` is an imported MODULE, not a typed
    parameter. Guessing "auth" is a type would invent a hint that fits
    nothing, and the safe answer is silence.
    """
    parsed = _parse(tmp_path, 'p.py', 'import auth\n\ndef handler(request):\n    return auth.login(request)\n')
    assert _entity(parsed, 'handler').call_owner_hints == {}

def test_one_name_called_on_two_receivers_is_dropped(tmp_path):
    """
    THE CONFLICT RULE.

    A pending reference is a single bare NAME. If `save` was called on both
    a Repo and a Cache inside one function, no single hint is true of it -
    and a hint that is wrong is far worse than no hint, because it resolves
    confidently instead of declining.
    """
    parsed = _parse(tmp_path, 'p.py', 'def sync(repo: Repo, cache: Cache):\n    repo.save()\n    cache.save()\n')
    assert 'save' not in _entity(parsed, 'sync').call_owner_hints

def test_a_receiver_named_after_the_method_produces_no_hint(tmp_path):
    """A hint equal to the called name says nothing and is discarded."""
    parsed = _parse(tmp_path, 'p.py', 'def go(login: login):\n    return login.login()\n')
    assert _entity(parsed, 'go').call_owner_hints == {}

def test_parameter_count_excludes_the_implicit_receiver(tmp_path):
    """
    `self` is not something a caller passes, so counting it would make every
    method's arity wrong by one.
    """
    parsed = _parse(tmp_path, 'p.py', 'class A:\n    def two(self, a, b):\n        return a\n')
    entity = _entity(parsed, 'two')
    assert entity.param_count == 2
    assert entity.flexible_arity is False

@pytest.mark.parametrize('signature', ['def f(a, b=1):', 'def f(a, *rest):', 'def f(a, **options):'])
def test_a_signature_that_accepts_a_varying_count_is_marked_flexible(tmp_path, signature):
    """
    THE SAFETY FLAG.

    A declared count is only the number of arguments a legal call carries
    when nothing in the signature bends. Marking these flexible is what stops
    arity eliminating the RIGHT candidate and leaving a confident wrong one.
    """
    parsed = _parse(tmp_path, 'p.py', f'{signature}\n    return a\n')
    assert _entity(parsed, 'f').flexible_arity is True

def test_the_argument_count_of_each_call_is_recorded(tmp_path):
    parsed = _parse(tmp_path, 'p.py', 'def outer():\n    return helper(1, 2, 3)\n')
    assert _entity(parsed, 'outer').call_arity_hints == {'helper': 3}

def test_one_name_called_with_two_different_counts_is_dropped(tmp_path):
    """Same conflict rule as the type hints, for the same reason."""
    parsed = _parse(tmp_path, 'p.py', 'def outer():\n    helper(1)\n    helper(1, 2)\n')
    assert 'helper' not in _entity(parsed, 'outer').call_arity_hints

def test_calls_are_still_collected_with_the_receiver_captured(tmp_path):
    """
    The receiver capture changed the shape `_collect_call_sites` returns.
    The names themselves - which every CALLS edge depends on - must be
    untouched.
    """
    parsed = _parse(tmp_path, 'p.py', 'def outer(svc: Svc):\n    a = helper(1)\n    b = svc.method()\n    return a, b\n')
    entity = _entity(parsed, 'outer')
    recorded = set(entity.unresolved_calls) | set(entity.external_calls)
    assert {'helper', 'method'} <= recorded
