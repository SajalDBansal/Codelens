"""
Guards on the things that quietly cost the graph its precision.

Each test here pins a defect found by auditing a real indexing run. They all
share a shape: nothing raised, nothing logged, and the graph looked healthy -
it was just wrong, or needlessly expensive, in a way only a measurement showed.
"""
import json
from graph_rag import config
from graph_rag.parsing.code_parser import parse_file
from graph_rag.parsing.doc_parser import parse_document
MODULE_BEFORE = 'import os\n\ndef alpha():\n    return 1\n\ndef beta():\n    return 2\n'
MODULE_AFTER = 'import os\n\ndef alpha():\n    x = 1\n    y = x\n    return y\n\ndef beta():\n    return 2\n'

def _module_entity(tmp_path, name, source):
    path = tmp_path / name
    path.write_text(source, encoding='utf-8')
    parsed = parse_file(path, name)
    return next((e for e in parsed.entities if e.kind == 'module'))

def test_module_hash_survives_an_edit_inside_a_function(tmp_path):
    """
    Editing one function's body must not force the module node to re-embed.

    The elision markers used to name line ranges - "(lines 3-4 defined
    above)" - so every edit that changed a line count anywhere in the file
    rewrote this node's text and changed its content hash. That is a paid
    embedding call on every single edit, and it defeats the whole point of
    hashing blocks separately.
    """
    before = _module_entity(tmp_path, 'a.py', MODULE_BEFORE)
    after = _module_entity(tmp_path, 'b.py', MODULE_AFTER)
    assert before.code == after.code
    assert before.content_hash == after.content_hash

def test_module_outline_says_what_was_left_out(tmp_path):
    """
    The marker still has to be followable - it names the definitions it
    stands in for, which is both stable and more useful than a line range.
    """
    module = _module_entity(tmp_path, 'a.py', MODULE_BEFORE)
    assert 'import os' in module.code
    assert 'return 1' not in module.code
    assert 'alpha' in module.code and 'beta' in module.code
    assert 'defined above' in module.code
JAVA_WITH_CONSTRUCTOR = 'public class OrderService {\n    public static final int MAX_ITEMS = 50;\n    private final OrderRepository repo;\n\n    public OrderService(OrderRepository repo) {\n        this.repo = repo;\n    }\n\n    public String place(String user) {\n        return repo.save(user);\n    }\n}\n'

def test_java_constructor_does_not_take_its_class_name(tmp_path):
    """
    A constructor named after its class put two `OrderService` entities in
    one file, so every `new OrderService(...)` was judged ambiguous and got
    no edge - permanently, on every run.
    """
    path = tmp_path / 'OrderService.java'
    path.write_text(JAVA_WITH_CONSTRUCTOR, encoding='utf-8')
    parsed = parse_file(path, 'OrderService.java')
    named_order_service = [e for e in parsed.entities if e.name == 'OrderService']
    assert len(named_order_service) == 1, f"more than one entity answers to 'OrderService': {[e.id for e in named_order_service]}"
    assert named_order_service[0].kind == 'class'
    assert any((e.name == '<init>' for e in parsed.entities))

def test_java_instance_field_is_not_a_constant(tmp_path):
    """
    `private final OrderRepository repo;` is state, not a constant. Emitting
    it as one mislabelled it and added a colliding `repo` to the graph.
    """
    path = tmp_path / 'OrderService.java'
    path.write_text(JAVA_WITH_CONSTRUCTOR, encoding='utf-8')
    parsed = parse_file(path, 'OrderService.java')
    kinds = {e.name: e.kind for e in parsed.entities}
    assert 'repo' not in kinds
    assert kinds.get('MAX_ITEMS') == 'constant', 'a real static final constant was lost'
RUST_IMPL = 'pub struct LineItem { pub cents: u64 }\n\npub trait Priced { fn total(&self) -> u64; }\n\nimpl Priced for LineItem {\n    fn total(&self) -> u64 { self.cents }\n}\n'

def test_rust_impl_is_named_for_its_type_and_does_not_shadow_it(tmp_path):
    """
    `impl Priced for LineItem` is about LineItem: its method belongs to
    LineItem, not to the trait. But it must not then compete with
    `struct LineItem` as a call target either, so it gets its own kind.
    """
    path = tmp_path / 'p.rs'
    path.write_text(RUST_IMPL, encoding='utf-8')
    parsed = parse_file(path, 'p.rs')
    by_id = {e.id: e for e in parsed.entities}
    assert 'p.rs::LineItem.total' in by_id, f"the impl's method is filed wrongly: {sorted(by_id)}"
    named_line_item = [e for e in parsed.entities if e.name == 'LineItem']
    kinds = sorted((e.kind for e in named_line_item))
    assert kinds == ['class', 'impl'], kinds
    callable_kinds = [k for k in kinds if k not in ('constant', 'module', 'impl')]
    assert len(callable_kinds) == 1

def _candidate(entity_id, path, language='python', kind='function'):
    return {'id': entity_id, 'filePath': path, 'language': language, 'kind': kind}

def test_a_data_dump_cannot_flood_the_document_index(tmp_path):
    """
    A 4,000-record export produced 60 chunks - 81% of every Doc node in a
    real graph - and those fragments were then retrieved into the answers for
    completely unrelated questions.
    """
    rows = [{'id': i, 'email': f'u{i}@example.com'} for i in range(4000)]
    path = tmp_path / 'users.json'
    path.write_text(json.dumps(rows), encoding='utf-8')
    parsed = parse_document(path, 'users.json')
    assert parsed is not None
    assert len(parsed.chunks) <= config.MAX_DOC_CHUNKS_PER_FILE
    kept = {chunk.id for chunk in parsed.chunks}
    for rel in parsed.relationships:
        assert rel.source_id in kept and rel.target_id in kept
    for chunk_id, _name in parsed.mentions:
        assert chunk_id in kept

def test_an_ordinary_document_is_untouched(tmp_path):
    """The cap must not trim a normal README."""
    path = tmp_path / 'README.md'
    path.write_text('# Title\n\nIntro.\n\n## One\n\nBody one.\n\n## Two\n\nBody two.\n', encoding='utf-8')
    parsed = parse_document(path, 'README.md')
    assert 1 < len(parsed.chunks) <= config.MAX_DOC_CHUNKS_PER_FILE
