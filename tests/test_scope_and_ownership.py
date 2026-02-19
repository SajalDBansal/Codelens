"""
tests/test_scope_and_ownership.py
=================================

Guards the two rules that decide WHICH definition a reference points at when
the name alone is not enough:

  - lexical scope   (`prefer_by_scope`)  - a nested helper's caller contains
                                           the very thing it is calling
  - declared owners (Go receivers,       - membership the grammar states
                     Rust `impl`)          rather than nests

Both were silent losses before. Scope ambiguity dropped real edges without
error, and a Go method came out as a free function so its type had no methods
at all in the graph. Neither failure raised anything, which is why they are
pinned here.
"""
import pytest
from graph_rag.parsing import parse_file, prefer_by_scope

def test_a_caller_prefers_the_helper_it_contains():
    """
    The case that cost this project 8 of its 21 ambiguous references.

    Six functions across the repository each define a local helper called
    `walk`. When `collect_folder_hashes` calls `walk` it can only mean its
    own, but the unique-match rule saw six candidates and gave up.
    """
    candidates = ['merkle.py::collect_folder_hashes.walk', 'merkle.py::collect_file_hashes.walk', 'merkle.py::diff_against_stored.walk', 'parser.py::_collect_calls.walk']
    chosen = prefer_by_scope(candidates, 'merkle.py::collect_folder_hashes')
    assert chosen == 'merkle.py::collect_folder_hashes.walk'

def test_a_sibling_in_the_same_scope_wins():
    """Two methods of one class refer to each other, not to a distant twin."""
    candidates = ['a.py::Service.save', 'b.py::Other.save']
    assert prefer_by_scope(candidates, 'a.py::Service.load') == 'a.py::Service.save'

def test_recursion_is_reported_as_the_caller_itself():
    """
    A self-call is a FACT about the code, not a missing edge.

    Returning the source id is how the rule says so; the caller records it as
    a property rather than creating a self-loop that every traversal would
    then have to step around.
    """
    candidates = ['m.py::build.recurse']
    assert prefer_by_scope(candidates, 'm.py::build.recurse') == 'm.py::build.recurse'

def test_scope_stays_silent_when_it_has_nothing_to_say():
    """
    Two unrelated files, neither containing the caller. Scope must return
    None so the later rules (imports, uniqueness) get their turn, rather than
    inventing a preference it cannot justify.
    """
    candidates = ['x.py::save', 'y.py::save']
    assert prefer_by_scope(candidates, 'z.py::caller') is None

def test_scope_never_crosses_a_file():
    """Containment is a within-file fact; a same-named nest elsewhere is not it."""
    candidates = ['other.py::caller.walk']
    assert prefer_by_scope(candidates, 'mine.py::caller') is None
GO_SOURCE = 'package main\n\ntype Engine struct {\n\trules []int\n}\n\n// Apply runs every rule.\nfunc (e *Engine) Apply(base int) int {\n\treturn helper(base)\n}\n\nfunc helper(x int) int { return x }\n'

@pytest.fixture
def parsed_go(tmp_path):
    path = tmp_path / 'engine.go'
    path.write_text(GO_SOURCE, encoding='utf-8')
    return parse_file(path, 'engine.go')

def test_a_go_method_is_a_method_not_a_function(parsed_go):
    """
    Go states membership in a receiver instead of nesting, so `Apply` used to
    come out as a free function and `Engine` had no methods in the graph at
    all. "What methods does Engine have?" was simply not answerable.
    """
    apply = next((e for e in parsed_go.entities if e.name == 'Apply'))
    assert apply.kind == 'method'
    assert apply.owner_name == 'Engine'
    assert apply.id == 'engine.go::Engine.Apply'

def test_a_go_method_hangs_off_its_type(parsed_go):
    """The edge itself, not just the label."""
    edges = {(r.source_id, r.target_id) for r in parsed_go.relationships if r.kind == 'CONTAINS'}
    assert ('engine.go::Engine', 'engine.go::Engine.Apply') in edges

def test_a_receiverless_go_function_stays_a_function(parsed_go):
    """The rule must not swallow ordinary package-level functions."""
    helper = next((e for e in parsed_go.entities if e.name == 'helper'))
    assert helper.kind == 'function'
    assert helper.owner_name is None
RUST_SOURCE = 'pub struct LineItem {\n    pub cents: u64,\n}\n\npub trait Priced {\n    fn total(&self) -> u64;\n}\n\nimpl Priced for LineItem {\n    fn total(&self) -> u64 {\n        self.cents\n    }\n}\n'

@pytest.fixture
def parsed_rust(tmp_path):
    path = tmp_path / 'lib.rs'
    path.write_text(RUST_SOURCE, encoding='utf-8')
    return parse_file(path, 'lib.rs')

def test_a_rust_impl_is_joined_to_its_struct(parsed_rust):
    """
    `struct LineItem` and `impl LineItem` are two nodes sharing a name, and
    nothing connected them - so the struct was an isolated node whose methods
    lived somewhere the graph could not reach from it.
    """
    edges = {(r.source_id, r.target_id) for r in parsed_rust.relationships if r.kind == 'CONTAINS'}
    assert ('lib.rs::LineItem', 'lib.rs::LineItem~impl_item') in edges

def test_a_rust_trait_implementation_is_an_edge(parsed_rust):
    """
    `impl Priced for LineItem` says LineItem implements Priced. The trait was
    parsed and then left with nothing pointing at it, so "what implements
    Priced?" had no answer.
    """
    extends = {(r.source_id, r.target_id) for r in parsed_rust.relationships if r.kind == 'EXTENDS'}
    assert ('lib.rs::LineItem~impl_item', 'lib.rs::Priced') in extends

def test_a_file_of_pure_definitions_still_gets_a_module_node(tmp_path):
    """
    THE CLEANER THE FILE, THE MORE LIKELY ITS ENTITIES WERE ORPHANED.

    Expansion refuses to travel through File nodes, so the <module> node is
    what gives a file's top-level definitions a parent inside the layer the
    graph walk can actually use. It was only created when there were leftover
    top-level LINES - which a well-factored file does not have - so such a
    file's definitions were reachable by a direct vector hit and by nothing
    else.
    """
    source = 'export function a() { return 1; }\nexport function b() { return 2; }\n'
    path = tmp_path / 'pure.ts'
    path.write_text(source, encoding='utf-8')
    parsed = parse_file(path, 'pure.ts')
    module = next((e for e in parsed.entities if e.kind == 'module'), None)
    assert module is not None, 'a file of pure definitions still needs an anchor'
    contained = {r.target_id for r in parsed.relationships if r.kind == 'CONTAINS'}
    assert 'pure.ts::a' in contained
    assert 'pure.ts::b' in contained

def test_the_outline_does_not_change_when_a_body_changes(tmp_path):
    """
    The fallback outline is built from names and kinds only, so editing the
    inside of a function must not change this node's hash and force a
    needless re-embed - the exact cost the Merkle design exists to avoid.
    """
    first = tmp_path / 'one.ts'
    first.write_text('export function a() { return 1; }\n', encoding='utf-8')
    before = next((e for e in parse_file(first, 'x.ts').entities if e.kind == 'module'))
    second = tmp_path / 'two.ts'
    second.write_text('export function a() { return 999; }\n', encoding='utf-8')
    after = next((e for e in parse_file(second, 'x.ts').entities if e.kind == 'module'))
    assert before.content_hash == after.content_hash
