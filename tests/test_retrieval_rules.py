"""
tests/test_retrieval_rules.py
=============================

The judgement calls that decide what ends up in the prompt.

Everything tested here is pure logic with no database and no API behind it,
which is deliberate: these are the rules most likely to be quietly wrong, and
quick deterministic checks make these rules practical to re-check often.

Each test names the failure it exists to prevent.
"""
from graph_rag import config
from graph_rag.models import CodeEntity, RetrievedEntity
from graph_rag.parsing.doc_parser import _split_large_text
from graph_rag.pipeline.indexer import resolve_reference
from graph_rag.storage.graph_store import reciprocal_rank_fusion

def test_a_file_with_no_blank_lines_still_splits():
    """
    The splitter used to start a new chunk ONLY when standing on a blank
    line, so a file without any never split at all. A 68 KB yaml file of
    `key: value` lines came back as one 69,790-character chunk, which the
    embedder then truncated to 8,000 - 88% of the file silently discarded
    while every report said it was fully indexed.
    """
    text = '\n'.join((f'  key_{i}: value_{i}' for i in range(3000)))
    chunks = _split_large_text(text, start_line=1, max_chars=4000)
    assert len(chunks) > 1
    assert all((len(body) <= 4000 for body, _, _ in chunks))

def test_splitting_loses_no_content():
    """A split must rearrange the text, never drop any of it."""
    text = '\n'.join((f'line {i} with some content' for i in range(500)))
    chunks = _split_large_text(text, start_line=1, max_chars=1000)
    rejoined = '\n'.join((body for body, _, _ in chunks))
    assert rejoined.split() == text.split()

def test_a_single_enormous_line_is_cut_up():
    """
    Minified json and one-line csv exports are a single line longer than the
    whole budget. No break point will ever help, so it has to be cut.
    """
    text = 'x' * 30000
    chunks = _split_large_text(text, start_line=1, max_chars=4000)
    assert all((len(body) <= 4000 for body, _, _ in chunks))
    assert sum((len(body) for body, _, _ in chunks)) == 30000

def test_line_numbers_stay_in_order():
    text = '\n'.join((f'line {i}' for i in range(2000)))
    chunks = _split_large_text(text, start_line=1, max_chars=500)
    for body, start, end in chunks:
        assert 1 <= start <= end

def test_a_blank_line_is_not_a_break_point_when_it_is_too_early():
    """
    The blank line straight after a heading used to become the break point
    for the whole section, producing a useless 10-byte first chunk holding
    nothing but "# Title" - which we would then pay to embed.
    """
    text = '# Title\n\n' + 'prose that goes on and on. ' * 400
    chunks = _split_large_text(text, start_line=1, max_chars=2000)
    assert len(chunks[0][0]) > 100, 'first chunk is a stub, not real content'

def test_a_call_never_resolves_to_a_constant():
    """
    Once constants became entities, `some_dict.items()` reduced to the bare
    name `items` and matched a dataclass field called `items`, producing a
    CALLS edge pointing at a variable. Calls land on callable things.
    """
    candidates = [{'id': 'retriever.py::items', 'language': 'python', 'filePath': 'retriever.py', 'kind': 'constant'}]
    target, reason = resolve_reference(candidates, 'indexer.py::run', 'python')
    assert target is None
    assert reason == 'external'

def test_a_call_may_resolve_to_a_class():
    """Calling a class is how most languages spell "construct one"."""
    candidates = [{'id': 'models.py::Relationship', 'language': 'python', 'filePath': 'models.py', 'kind': 'class'}]
    target, reason = resolve_reference(candidates, 'indexer.py::run', 'python')
    assert reason == 'linked'
    assert target == 'models.py::Relationship'

def test_a_name_only_found_in_another_language_is_external_not_ambiguous():
    """
    A Python file calling `login` when the only `login` in the graph is
    TypeScript is calling something OUTSIDE the graph. Reporting that as
    "ambiguous" inflated the headline "real missed edges" figure with cases
    that were nothing of the kind.
    """
    candidates = [{'id': 'api.ts::login', 'language': 'typescript', 'filePath': 'api.ts', 'kind': 'function'}]
    target, reason = resolve_reference(candidates, 'app.py::verify', 'python')
    assert target is None
    assert reason == 'external'

def _candidate(entity_id, path, kind='function'):
    return {'id': entity_id, 'language': 'python', 'filePath': path, 'kind': kind}

def _entity(entity_id, code):
    return CodeEntity(id=entity_id, kind='function', name=entity_id.split('::')[-1], file_path=entity_id.split('::')[0], language='python', start_line=1, end_line=2, code=code, content_hash='h')
