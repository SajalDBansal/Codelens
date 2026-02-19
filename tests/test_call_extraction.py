"""
Guards on which CALLS the parser actually sees.

WHY THIS FILE EXISTS
--------------------
The call graph is what makes this project graph RAG rather than ordinary RAG,
and it is the easiest thing in the system to break silently: a missed call
raises nothing, logs nothing, and leaves a graph that still looks healthy.

It broke exactly that way. The walker used a stop set that included constant
node types, so it halted at every assignment - but constant ENTITIES are only
created outside function bodies, so a call written as

    user = repo.find_by_email(email)

was collected by nobody. It was not linked, and it was not even recorded as an
unresolved reference. It simply disappeared. That discarded 41% of all call
sites in this project's own source, and in Kotlin, Swift, Scala and Lua - where
a local binding uses the same grammar node as a module constant - it discarded
the entire call graph.

The suite passed throughout, because the only call test in it was written as
`return helper(value)`, which is the one shape that still worked.

So: every language family gets a case with the call on the RIGHT-HAND SIDE OF
AN ASSIGNMENT, which is how most real code calls most real functions.
"""
import pytest
from graph_rag.parsing.code_parser import parse_file

def _calls_from(parsed, caller_name: str) -> set[str]:
    """
    Every name `caller_name` calls, however it was recorded.

    Resolved same-file calls become relationships; everything else is left as
    an unresolved reference for the graph to match later. A call must appear
    in one of the two - vanishing from both is the bug this file guards.
    """
    caller_ids = {e.id for e in parsed.entities if e.name == caller_name}
    assert caller_ids, f'no entity named {caller_name!r} was parsed at all'
    names: set[str] = set()
    for rel in parsed.relationships:
        if rel.kind == 'CALLS' and rel.source_id in caller_ids:
            names.add(rel.target_id.split('::')[-1].split('.')[-1])
    for source_id, name, kind in parsed.unresolved_references:
        if kind == 'CALLS' and source_id in caller_ids:
            names.add(name)
    return names
ASSIGNMENT_CASES = [('m.py', 'def outer(v):\n    result = helper(v)\n    return result\n\ndef helper(x):\n    return x\n', 'outer', 'helper'), ('m.ts', 'function outer(v: number) {\n  const result = helper(v);\n  return result;\n}\nfunction helper(x: number) { return x; }\n', 'outer', 'helper'), ('m.js', 'function outer(v) {\n  const result = helper(v);\n  return result;\n}\nfunction helper(x) { return x; }\n', 'outer', 'helper'), ('m.go', 'package p\n\nfunc outer(v int) int {\n\tresult := helper(v)\n\treturn result\n}\n\nfunc helper(x int) int { return x }\n', 'outer', 'helper'), ('M.java', 'public class M {\n    int outer(int v) {\n        int result = helper(v);\n        return result;\n    }\n    int helper(int x) { return x; }\n}\n', 'outer', 'helper'), ('m.rs', 'pub fn outer(v: u32) -> u32 {\n    let result = helper(v);\n    result\n}\n\npub fn helper(x: u32) -> u32 { x }\n', 'outer', 'helper')]

@pytest.mark.parametrize('filename,source,caller,callee', ASSIGNMENT_CASES, ids=[case[0] for case in ASSIGNMENT_CASES])
def test_call_on_the_right_hand_side_of_an_assignment_is_seen(tmp_path, filename, source, caller, callee):
    """
    `result = helper(v)` must produce a call, in every language.

    This is the single most common way code calls code. Losing it costs most
    of the call graph, and it is invisible without a test that looks for it.
    """
    path = tmp_path / filename
    path.write_text(source, encoding='utf-8')
    parsed = parse_file(path, filename)
    assert parsed is not None, f'{filename}: parser returned nothing'
    assert callee in _calls_from(parsed, caller), f'{filename}: the call to {callee!r} on the right-hand side of an assignment was dropped. {caller!r} was recorded as calling {sorted(_calls_from(parsed, caller))}'

def test_call_in_a_return_statement_still_works(tmp_path):
    """The shape that always worked must keep working."""
    path = tmp_path / 'r.py'
    path.write_text('def outer(v):\n    return helper(v)\n\ndef helper(x):\n    return x\n', encoding='utf-8')
    parsed = parse_file(path, 'r.py')
    assert 'helper' in _calls_from(parsed, 'outer')

def test_a_module_level_constant_still_owns_its_own_calls(tmp_path):
    """
    The stop set exists for a real reason - keep proving it works.

    `SERVICE = build()` at module level creates a constant entity, and that
    call belongs to the constant, not to the module node. Both claiming it
    would double count the same call.
    """
    path = tmp_path / 'c.py'
    path.write_text('SERVICE = build()\n\ndef build():\n    return 1\n', encoding='utf-8')
    parsed = parse_file(path, 'c.py')
    assert 'build' in _calls_from(parsed, 'SERVICE')
    module_calls = [(source_id, name) for source_id, name, kind in parsed.unresolved_references if kind == 'CALLS' and source_id.endswith('::<module>')]
    module_rels = [rel for rel in parsed.relationships if rel.kind == 'CALLS' and rel.source_id.endswith('::<module>')]
    assert not module_calls and (not module_rels), "the module node also claimed the constant's call - it is counted twice"

def test_a_nested_function_keeps_its_own_calls(tmp_path):
    """
    Stopping at nested DEFINITIONS is still correct: `inner` calls
    `deep`, `outer` does not.
    """
    path = tmp_path / 'n.py'
    path.write_text('def outer():\n    a = shallow()\n    def inner():\n        b = deep()\n        return b\n    return inner\n\ndef shallow():\n    return 1\n\ndef deep():\n    return 2\n', encoding='utf-8')
    parsed = parse_file(path, 'n.py')
    outer_calls = _calls_from(parsed, 'outer')
    assert 'shallow' in outer_calls
    assert 'deep' not in outer_calls, 'outer wrongly claimed the nested call'
    assert 'deep' in _calls_from(parsed, 'inner')
