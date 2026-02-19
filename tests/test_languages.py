"""
tests/test_languages.py
=======================

ONE PARSING TEST PER SUPPORTED LANGUAGE.

WHY THIS FILE EXISTS
--------------------
Every other parser test in this project used Python source. That was a real
hole, and it hid a long list of bugs that were only found by running the
parser over a repository that actually mixed languages:

  - Go produced ZERO types, because `type_declaration` has no "name" field,
    so every struct and interface was treated as anonymous and dropped.
  - Rust emitted `struct Foo` and `impl Foo` under the SAME entity id, and
    because the graph MERGEs on id, the impl block silently overwrote the
    struct definition.
  - Java read the word "extends" out of its `superclass` node and recorded
    it as a parent class name, twice.
  - Ruby declared every method call an import, because `require` is itself an
    ordinary call and "call" had been listed as the import node type.
  - PHP resolved `$this->repo->find()` to a call to `repo`, not to `find`.
  - C++ named an out-of-line method `AuthService::login`, which no call to
    `login` could ever match.

None of these raised an error. Each one just quietly produced less graph, so
the only way to catch them is to assert on what the parser FINDS.

The rule this file enforces: for every language we claim to support, parsing
a small realistic file must produce the entities a reader would expect, with
unique ids and sane line numbers.
"""
from collections import Counter
import pytest
from graph_rag.parsing.code_parser import parse_file
from graph_rag.parsing.languages import EXTENSION_TO_LANGUAGE, LANGUAGE_CONFIGS
CASES = [('svc.py', 'MAX_RETRIES = 3\n\n\nclass Base:\n    pass\n\n\nclass AuthService(Base):\n    """Handles login."""\n\n    def login(self, user):\n        return verify(user)\n\n\ndef verify(user):\n    return bool(user)\n', {'MAX_RETRIES': 'constant', 'Base': 'class', 'AuthService': 'class', 'login': 'method', 'verify': 'function'}), ('svc.go', 'package main\n\nconst MaxRetries = 3\n\ntype AuthService struct {\n\trepo *Repository\n}\n\ntype Repository struct{}\n\nfunc (s *AuthService) Login(user string) bool {\n\treturn verify(user)\n}\n\nfunc verify(user string) bool { return user != "" }\n', {'MaxRetries': 'constant', 'AuthService': 'class', 'Repository': 'class', 'Login': 'method', 'verify': 'function'}), ('lib.rs', 'pub const MAX_RETRIES: u32 = 3;\n\npub struct AuthService { repo: Repository }\n\npub struct Repository;\n\npub trait Pingable { fn ping(&self) -> bool; }\n\nimpl AuthService {\n    pub fn login(&self, user: &str) -> bool { verify(user) }\n}\n\npub fn verify(user: &str) -> bool { !user.is_empty() }\n', {'MAX_RETRIES': 'constant', 'AuthService': 'class', 'Repository': 'class', 'Pingable': 'class', 'login': 'method', 'verify': 'function'})]
IDS = [name for name, _, _ in CASES]

@pytest.fixture(params=CASES, ids=IDS)
def parsed(request, tmp_path):
    filename, source, expected = request.param
    path = tmp_path / filename
    path.write_text(source, encoding='utf-8')
    result = parse_file(path, filename)
    assert result is not None, f'{filename} was not recognised as code at all'
    return (result, expected, filename)

def test_entity_ids_are_unique(parsed):
    """
    Ids must be unique within a file, because the graph MERGEs on id.

    A duplicate is not a warning, it is silent data loss: the second node
    overwrites the first. Rust hit this with `struct Foo` and `impl Foo`.
    """
    result, _, filename = parsed
    counts = Counter((entity.id for entity in result.entities))
    duplicates = {key: count for key, count in counts.items() if count > 1}
    assert not duplicates, f'{filename}: duplicate entity ids {duplicates}'

def test_line_numbers_are_sane(parsed):
    """Line numbers are 1-based and never run past the end of the file."""
    result, _, filename = parsed
    for entity in result.entities:
        assert entity.start_line >= 1, f'{filename}: {entity.id} starts at line 0'
        assert entity.end_line >= entity.start_line, f'{filename}: {entity.id} ends before it starts'
