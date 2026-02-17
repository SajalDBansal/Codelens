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
from graph_rag.parsing import parse_file
GO_SOURCE = 'package main\n\ntype Engine struct {\n\trules []int\n}\n\n// Apply runs every rule.\nfunc (e *Engine) Apply(base int) int {\n\treturn helper(base)\n}\n\nfunc helper(x int) int { return x }\n'

@pytest.fixture
def parsed_go(tmp_path):
    path = tmp_path / 'engine.go'
    path.write_text(GO_SOURCE, encoding='utf-8')
    return parse_file(path, 'engine.go')
RUST_SOURCE = 'pub struct LineItem {\n    pub cents: u64,\n}\n\npub trait Priced {\n    fn total(&self) -> u64;\n}\n\nimpl Priced for LineItem {\n    fn total(&self) -> u64 {\n        self.cents\n    }\n}\n'

@pytest.fixture
def parsed_rust(tmp_path):
    path = tmp_path / 'lib.rs'
    path.write_text(RUST_SOURCE, encoding='utf-8')
    return parse_file(path, 'lib.rs')

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
