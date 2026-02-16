"""
tests/test_graph_structure.py
=============================

Guards the shape of the graph: that entities come out with the right kinds
and line numbers, and that edges connect the things they claim to.

The most important tests here are the ones around `resolve_reference`. That
one function decides which definition a call by NAME actually points at, and
it is the single most consequential piece of judgement in the indexer:

  - too strict, and real calls go unlinked, leaving the graph in disconnected
    islands that graph traversal cannot cross;
  - too loose, and it invents edges between unrelated code, which silently
    sends retrieval off to the wrong file and poisons answers.

Both failure modes are quiet. Neither raises an error. Hence the tests.
"""
import pytest
from graph_rag.parsing import language_family, parse_file

def candidate(entity_id, language):
    """Shorthand for one row as storage.resolve_names would return it."""
    return {'id': entity_id, 'language': language, 'filePath': entity_id.split('::')[0]}
PY_SOURCE = '"""Module docstring."""\nCONSTANT = 3\n\n\nclass Service:\n    """A service."""\n\n    def run(self, value):\n        """Do the thing."""\n        return helper(value)\n\n\ndef helper(value):\n    """A free function."""\n    return value * CONSTANT\n'

@pytest.fixture
def parsed_python(tmp_path):
    path = tmp_path / 'svc.py'
    path.write_text(PY_SOURCE, encoding='utf-8')
    return parse_file(path, 'svc.py')

def test_entity_ids_are_qualified(parsed_python):
    ids = {e.id for e in parsed_python.entities}
    assert 'svc.py::Service.run' in ids
    assert 'svc.py::helper' in ids

def test_docstrings_are_captured(parsed_python):
    """
    Docstrings say what code is FOR, which the code itself often does not,
    so losing them measurably degrades search.
    """
    docs = {e.name: e.docstring for e in parsed_python.entities}
    assert docs['run'] == 'Do the thing.'
    assert docs['helper'] == 'A free function.'

def test_line_numbers_are_1_based_and_within_the_file(parsed_python):
    """Line numbers are cited in answers, so they must match the editor."""
    total = len(PY_SOURCE.splitlines())
    for entity in parsed_python.entities:
        assert 1 <= entity.start_line <= entity.end_line <= total

def test_class_contains_its_method(parsed_python):
    edges = {(r.source_id, r.target_id, r.kind) for r in parsed_python.relationships}
    assert ('svc.py::Service', 'svc.py::Service.run', 'CONTAINS') in edges
MD_SOURCE = '# Title\n\nIntro prose about the project.\n\n## Setup\n\nHow to set it up, calling `helper` along the way.\n\n### Install\n\nInstallation detail.\n\n## Usage\n\nHow to use it.\n'

@pytest.fixture
def parsed_markdown(tmp_path):
    path = tmp_path / 'README.md'
    path.write_text(MD_SOURCE, encoding='utf-8')
    return parse_document(path, 'README.md')
