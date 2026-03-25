"""
tests/test_exclusions.py
========================

Guards the rule that decides WHICH FILES GET INDEXED.

Why this file exists, and why it is the first test in the project:

Indexing a file means its contents are sent to the OpenAI API and stored in
the local index, where anyone who can query this system can retrieve them. So a bug in
the exclusion rules is not an inconvenience - it leaks credentials to a third
party. The rules are also easy to break by accident, because they live in
several places (an ignore list, a filename check, a size cap) and each new
language or document type touches them.

So: build a fake repository containing every kind of file we expect to
refuse, run the real Merkle tree walker over it, and assert on exactly what
comes back.

Run with:   pytest -v
(install pytest first:  pip install -e ".[dev]")
"""

import pytest

from graph_rag.change_detection import build_merkle_tree, collect_file_hashes
from graph_rag.parsing.languages import is_indexable, looks_like_secret

# ---------------------------------------------------------------------------
# The fake repository
# ---------------------------------------------------------------------------

# Files that MUST be indexed.
#
# The policy is DEFAULT-ALLOW: index everything except secrets, vendored or
# generated noise, and binaries. An earlier allowlist-of-extensions approach
# quietly dropped every .sql, .graphql, .proto, .tf, .html and .csv file in
# the repository - all of which hold real answers - so this list is
# deliberately broad, and includes file types with no tree-sitter grammar.
SHOULD_INDEX = {
    "src/artifacts.py": "def artifact_metadata():\n    return 1\n",
    "src/app.py": "def real():\n    return 1\n",
    "README.md": "# Title\n\nSome prose.\n",
    "docs/guide.md": "# Guide\n\nHow to use it.\n",
    "package.json": '{"name": "app"}',
    ".env.example": "API_KEY=placeholder\n",   # a template, not a secret

    # No grammar for these, but they are text and they answer real questions.
    "db/schema.sql": "CREATE TABLE users (id INT, email TEXT);",
    "api/schema.graphql": "type User { id: ID! email: String! }",
    "proto/user.proto": 'syntax = "proto3";\nmessage User { string id = 1; }',
    "infra/main.tf": 'resource "aws_s3_bucket" "b" { bucket = "x" }',
    "web/index.html": "<html><body><button id='go'>Go</button></body></html>",
    "web/style.css": ".button { color: red; }",
    "fixtures/users.csv": "id,email\n1,a@b.c\n",   # hand-written test data
    ".gitignore": "node_modules/\n",
    "Dockerfile": "FROM python:3.12\n",

    # Languages we now have grammars for.
    "svc/handler.rb": "def go(x)\n  x\nend\n",
    "svc/run.sh": "greet() {\n  echo hi\n}\n",
    "lib/core.c": "int go(int x){ return x; }\n",

    # An unfamiliar extension that is nonetheless plain text.
    "notes.whatever": "just some text nobody has a parser for",
}

# Files that MUST NOT be indexed, and the reason each one is refused.
SHOULD_SKIP = {
    # --- credentials: the security boundary ------------------------------
    ".env": "SECRET=hunter2",
    ".env.local": "SECRET=hunter2",
    ".env.production": "SECRET=hunter2",
    "secrets.yaml": "password: hunter2",
    "server.pem": "-----BEGIN KEY-----",
    "app.key": "-----BEGIN KEY-----",
    ".ssh/id_rsa": "ssh-rsa AAA",
    ".aws/credentials": "aws_secret_access_key = x",
    # An innocent NAME inside a secrets FOLDER. This one was a real bug:
    # filename-only checks let it straight through.
    "config/secrets/prod.yaml": "password: hunter2",
    "config/secrets/nested/db.yaml": "password: hunter2",

    # --- third-party code, at any depth ----------------------------------
    "node_modules/react/index.js": "module.exports = {}",
    "packages/ui/node_modules/lodash/index.js": "module.exports = {}",
    "vendor/github.com/x/lib.go": "package x",
    "venv/Lib/site-packages/requests.py": "def get(): pass",

    # --- build output and caches -----------------------------------------
    "dist/bundle.js": "console.log(1)",
    "build/out.js": "console.log(1)",
    "target/debug.rs": "fn main() {}",
    ".next/chunk.js": "1",
    "coverage/report.json": "{}",
    "artifacts/generated.py": "def generated(): return 1",
    "artifacts/README.md": "# Generated artifacts\nDo not index this.",
    "apps/api/artifacts/deep/output.json": '{"generated": true}',
    "__pycache__/app.cpython-311.pyc": "cached",
    ".git/HEAD": "ref: refs/heads/main",

    # --- generated / machine-written --------------------------------------
    "package-lock.json": '{"lockfileVersion": 3}',
    "yarn.lock": "# yarn lockfile",
    "src/vendor.min.js": "!function(){}();",

    # --- binary: cannot be embedded as text -------------------------------
    "assets/logo.png": "\x89PNG",
    "fonts/icons.woff2": "wOF2",
    "model/weights.safetensors": "binary",
}


@pytest.fixture
def fake_repo(tmp_path):
    """
    Build the fake repository on disk.

    `tmp_path` is a pytest built-in: a fresh empty directory per test, thrown
    away afterwards. Nothing here touches your real project.
    """
    for relative_path, content in {**SHOULD_INDEX, **SHOULD_SKIP}.items():
        file_path = tmp_path / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")

    # A file with a NUL byte in it, wearing a text-looking extension.
    # The extension says nothing, so only a look at the actual bytes can
    # catch this - which is what makes default-allow safe.
    sneaky = tmp_path / "data" / "blob.dat"
    sneaky.parent.mkdir(parents=True, exist_ok=True)
    sneaky.write_bytes(b"text-ish header\x00\x01\x02 binary payload")

    # A huge .json. Small structured files are configuration and worth
    # indexing; a multi-megabyte export is a data dump. Only the size tells
    # them apart, so the size cap is what has to catch this.
    big = tmp_path / "data" / "users.json"
    big.write_text('{"rows": [' + '{"a":1},' * 200000 + "]}", encoding="utf-8")

    return tmp_path


def indexed_files(repo) -> set[str]:
    """Run the real walker and return the set of paths it picked up."""
    return set(collect_file_hashes(build_merkle_tree(repo)))


# ---------------------------------------------------------------------------
# The tests
# ---------------------------------------------------------------------------

def test_real_source_and_docs_are_indexed(fake_repo):
    """The files we DO want must all be there - exclusions can overreach too."""
    found = indexed_files(fake_repo)
    missing = set(SHOULD_INDEX) - found
    assert not missing, f"these should have been indexed but were not: {sorted(missing)}"


@pytest.mark.parametrize("relative_path", sorted(SHOULD_SKIP))
def test_junk_and_secrets_are_not_indexed(fake_repo, relative_path):
    """
    One test per excluded file, so a failure names the exact file that leaked
    instead of just saying "a set was not equal to another set".
    """
    assert relative_path not in indexed_files(fake_repo)


def test_large_data_file_is_skipped(fake_repo):
    """
    A multi-megabyte .json is a data dump, not configuration.

    package.json (tiny) is indexed; data/users.json (way over the cap) is
    not. Same extension - only the size separates them.
    """
    found = indexed_files(fake_repo)
    assert "package.json" in found
    assert "data/users.json" not in found


def test_binary_content_is_caught_even_with_a_texty_extension(fake_repo):
    """
    `.dat` is not on any list, so it is accepted optimistically by extension
    and must be rejected by the null-byte check on its actual bytes.

    This content sniff is what makes default-allow safe.
    """
    assert "data/blob.dat" not in indexed_files(fake_repo)


def test_unknown_text_extensions_are_indexed(fake_repo):
    """
    The whole point of default-allow: a file type nobody anticipated still
    gets indexed, as long as it is text and not a secret.
    """
    assert "notes.whatever" in indexed_files(fake_repo)


def test_nothing_unexpected_is_indexed(fake_repo):
    """
    The strict version: the indexed set is EXACTLY what we listed.

    A loose "no secrets" check would still pass if some new file type started
    being swept up. This catches that.
    """
    assert indexed_files(fake_repo) == set(SHOULD_INDEX)


def test_repo_location_does_not_affect_exclusions(tmp_path):
    """
    A project stored under a folder called `keys` must still index normally.

    The secret check looks at every folder on the path, so it has to run on
    the repo-RELATIVE path. If it ran on the absolute path, the machine's
    directory layout above the repo root would silently exclude everything.
    """
    repo = tmp_path / "keys" / "myproject"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("def hello(): pass\n", encoding="utf-8")
    (repo / "README.md").write_text("# Hi\n\nProse.\n", encoding="utf-8")
    (repo / ".env").write_text("SECRET=leak\n", encoding="utf-8")

    assert indexed_files(repo) == {"src/app.py", "README.md"}


@pytest.mark.parametrize(
    "path",
    [
        ".env", ".env.local", ".env.production", ".env.staging",
        "secrets.yaml", "secret.json", "secrets.local.toml",
        "server.pem", "app.key", "cert.p12", "store.jks",
        "id_rsa", "id_ed25519", ".npmrc", ".pypirc", ".netrc",
        "config/secrets/anything.yaml", ".ssh/known_hosts", ".aws/config",
    ],
)
def test_looks_like_secret_denies_by_default(path):
    """The secret check on its own, without the file system in the way."""
    assert looks_like_secret(path)
    assert not is_indexable(path)


@pytest.mark.parametrize(
    "path",
    [".env.example", ".env.sample", ".env.template", "README.md", "src/app.py"],
)
def test_templates_and_normal_files_are_allowed(path):
    """
    Deny-by-default must not swallow the templates.

    `.env.example` exists to be shared and holds placeholders, so it is a
    deliberate exception - and a useful one, since it documents every setting.
    """
    assert not looks_like_secret(path)
