"""Versioned local metadata, typed edges, and persistent BM25 postings."""

SCHEMA_VERSION = 1

DDL = """
CREATE TABLE IF NOT EXISTS index_meta (
    id INTEGER PRIMARY KEY CHECK(id=1), model TEXT NOT NULL, dimensions INTEGER NOT NULL,
    schema_version INTEGER NOT NULL, repo_root TEXT
);
CREATE TABLE IF NOT EXISTS blobs (hash TEXT PRIMARY KEY, content BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS nodes (
    label TEXT NOT NULL, id TEXT NOT NULL, file_path TEXT, name TEXT, kind TEXT,
    language TEXT, path TEXT, content_hash TEXT, data TEXT NOT NULL DEFAULT '{}',
    embedding_input_hash TEXT, embedding_hash TEXT, vector_segment TEXT, vector_row INTEGER,
    source_hash TEXT REFERENCES blobs(hash), start_char INTEGER, end_char INTEGER,
    text_hash TEXT REFERENCES blobs(hash), body_hash TEXT,
    PRIMARY KEY(label, id)
);
CREATE INDEX IF NOT EXISTS nodes_file ON nodes(label, file_path);
CREATE INDEX IF NOT EXISTS nodes_name ON nodes(label, name, id);
CREATE INDEX IF NOT EXISTS nodes_path ON nodes(label, path);
CREATE TABLE IF NOT EXISTS edges (
    source_label TEXT NOT NULL, source TEXT NOT NULL,
    target_label TEXT NOT NULL, target TEXT NOT NULL, kind TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0, resolved_by TEXT,
    PRIMARY KEY(source_label, source, target_label, target, kind),
    FOREIGN KEY(source_label, source) REFERENCES nodes(label, id) ON DELETE CASCADE,
    FOREIGN KEY(target_label, target) REFERENCES nodes(label, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS edges_target ON edges(target_label, target, kind);
CREATE INDEX IF NOT EXISTS edges_kind ON edges(kind, source_label, target_label);
CREATE TABLE IF NOT EXISTS keyword_docs (
    key INTEGER PRIMARY KEY,
    label TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL, length INTEGER NOT NULL,
    UNIQUE(label, id),
    FOREIGN KEY(label, id) REFERENCES nodes(label, id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS keyword_terms (
    doc_key INTEGER NOT NULL REFERENCES keyword_docs(key) ON DELETE CASCADE,
    token TEXT NOT NULL, frequency INTEGER NOT NULL,
    PRIMARY KEY(token, doc_key)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS keyword_document ON keyword_terms(doc_key);
"""
