"""
models.py
=========

The *shapes* of the data that moves through this project.

We use `pydantic.BaseModel` instead of plain dictionaries because pydantic
checks the types for us. If some code tries to build a CodeEntity with
`start_line="hello"`, pydantic raises an error immediately and tells you
exactly which field was wrong - instead of the mistake silently travelling
three modules away and blowing up somewhere confusing.

Think of each class below as a form with named boxes that must be filled in.
"""

from typing import Optional
from pydantic import BaseModel


class CodeEntity(BaseModel):
    """
    ONE piece of code we care about: a function, a method, or a class.

    This is the thing we embed and the thing we store as a node in LadybugDB.
    """

    # A stable, unique, human-readable id.
    # Format: "<relative file path>::<Outer>.<inner>"
    # Example: "src/auth/user_service.py::UserService.login"
    # It is deterministic: parsing the same unchanged file always produces the
    # same id, which is what lets us use MERGE (update-or-create) in LadybugDB
    # instead of creating duplicate nodes on every run.
    id: str

    # "function" | "method" | "class"
    # ("method" means a function that lives inside a class.)
    kind: str

    # Just the name, e.g. "login".
    name: str

    # Path relative to the repository root, using forward slashes on every OS,
    # e.g. "src/auth/user_service.py".
    file_path: str

    # "python" | "typescript" | "go" | ... - see parsing/languages.py.
    # Stored on the node so you can write Cypher like
    #   MATCH (e:CodeEntity {language: 'rust'})
    # without needing to join through the file.
    language: str

    # 1-based line numbers, so they match what your editor shows you.
    start_line: int
    end_line: int

    # The actual source text of this function/class.
    code: str

    # The docstring / leading comment, if the language and code have one.
    # This is often the single most useful text for search, because it says
    # what the code is FOR, which the code itself frequently does not.
    docstring: Optional[str] = None

    # sha256 of `code`. This is the "block hash" of the Merkle design:
    # if this value is unchanged since the last run, the code is byte-for-byte
    # identical, so we can skip re-embedding it (which costs money and time).
    content_hash: str = ""

    # The embedding vector. Empty until embedding/ fills it in.
    embedding: Optional[list[float]] = None

    # Names this entity referred to that could NOT be turned into an edge
    # while parsing its own file, because the target lives somewhere else.
    #
    # WHY THESE ARE STORED ON THE NODE
    # --------------------------------
    # They used to exist only in memory, for the length of one indexing run.
    # That made the call graph decay: if file A calls `helper` and `helper` is
    # only ADDED to file B later, A is unchanged, so A is never re-parsed, so
    # the edge A->B is never created. The graph drifted further from the truth
    # with every incremental run and only `--force` repaired it.
    #
    # Keeping the names on the node means every run can retry every pending
    # reference against the whole graph, so the call graph converges instead.
    unresolved_calls: list[str] = []
    unresolved_extends: list[str] = []

    # Types this entity NAMES - in its parameters, its return type, or (for a
    # class) its own fields - whose definition lives in another file.
    #
    # Kept apart from the two above because the rule that resolves them is
    # stricter: a type reference may only ever land on a `class`. A call may
    # legitimately target a class, since that is how most languages spell
    # "construct one", so the two cannot share a candidate set.
    unresolved_types: list[str] = []

    # Names that are nowhere in the graph at all - calls into the standard
    # library or a third-party package. Kept SEPARATE from unresolved_calls
    # because the two need completely different treatment: an ambiguous name
    # is worth retrying on every run, while `len` and `println` will never
    # resolve and retrying them forever is what made step 7a of the indexer
    # scale with repository size instead of with what changed.
    external_calls: list[str] = []

    # The same idea for type references: a name we looked for and found
    # nowhere in the graph, so it is a built-in or a library type
    # (`Promise`, `HttpServletRequest`, a destructured property name that was
    # never a type at all). Retrying these on every run is what made the
    # pending set scale with the repository instead of with what changed, so
    # they are only reconsidered on a run that actually wrote a matching name.
    external_types: list[str] = []

    # Does this entity call itself? Recorded as a fact rather than as a
    # self-edge, which would complicate every graph traversal for no gain.
    is_recursive: bool = False

    # The name of the type this definition belongs to, when the language says
    # so somewhere other than by nesting it - a Go receiver, a Rust `impl`.
    # The edge itself is created once the named type is found, which may not
    # be until a later run if the type lives in another file.
    owner_name: Optional[str] = None

    # "When this entity calls `login`, the receiver was declared to be an
    # AuthService" - stored as {"login": "AuthService"}.
    #
    # WHY THIS EXISTS
    # ---------------
    # Calls are recorded by BARE NAME, because `_last_identifier` throws the
    # receiver away: `auth.login(...)` becomes just "login". Cross-file
    # resolution then looks that name up across the whole graph and refuses
    # to link when more than one definition answers to it.
    #
    # That refusal is right in general and wrong in the commonest case in
    # object-oriented code: a subclass overriding a parent's method. Measured
    # on a five-language demo repo, `AuthService.login` and
    # `AdminAuthService.login` collided, the edge from `handle_login` was
    # dropped, and the ENTIRE auth module fell out of the graph as an
    # isolated island - nine entities that no traversal could reach.
    #
    # The receiver is not ambiguous at all, though. `handle_login` declares
    # its parameter as `auth: AuthService`, so which `login` is meant is
    # written down in the source. This carries that fact through to
    # resolution, where it narrows the candidates to the one the code names.
    #
    # It only ever NARROWS an existing candidate set, so it can recover
    # edges that were dropped but can never invent one that was not already
    # a candidate.
    call_owner_hints: dict[str, str] = {}

    # "When this entity calls `createInvoice`, it passed 2 arguments" -
    # stored as {"createInvoice": 2}.
    #
    # The last-resort tie-break, used only after language, scope, receiver
    # type and imports have all failed to single out one candidate. It is
    # the weakest of the signals and the easiest to get wrong, so a
    # definition whose own signature is flexible (defaults, varargs,
    # optional parameters) is never ruled out by it. See
    # parsing.code_parser._arity.
    call_arity_hints: dict[str, int] = {}

    # How many parameters this definition declares, and whether that count
    # can be trusted as an exact match.
    #
    # `param_count` excludes `self` / `this`, so it is what a caller actually
    # passes. `flexible_arity` is set when the signature has default values,
    # varargs, optional markers or destructuring - anything that means a call
    # with a different number of arguments is still legal. When it is set,
    # arity is never used to rule the candidate out.
    param_count: Optional[int] = None
    flexible_arity: bool = False


class Relationship(BaseModel):
    """
    ONE edge in the graph: "this entity is connected to that entity".

    Edges are the whole reason this project is graph RAG rather than ordinary
    RAG. Plain RAG finds a function that looks relevant; edges let us also
    hand the LLM that function's callers, its callees, and the class it
    belongs to - the context a human reviewer would naturally go look up.
    """

    # The id of the entity the edge starts at.
    source_id: str

    # The id of the entity the edge points to.
    target_id: str

    # "CALLS"     - source calls target (the highest-value edge)
    # "CONTAINS"  - a class contains a method, a file contains a class
    # "EXTENDS"   - a class inherits from another class (or implements an
    #               interface - the same relationship for our purposes)
    # "IMPORTS"   - a file imports another file
    # "USES_TYPE" - source names target as a parameter type, a return type,
    #               or the type of one of its fields. The structural
    #               dependency that no call records: `checkout(order: Order)`
    #               depends on Order without ever calling it.
    kind: str

    # How sure we are that this edge is real, 0..1.
    #
    # Not every edge is equally trustworthy, and flattening that into a
    # yes/no loses information retrieval could use. A call resolved inside
    # one file is a certainty; one resolved across files because exactly one
    # definition in the repository answered to the name is very likely; one
    # settled by reading a declared parameter type is likely but rests on the
    # annotation being accurate.
    #
    # Retrieval multiplies a neighbour's relevance by this, so a
    # lower-confidence edge still contributes context but loses to a certain
    # one when the character budget is tight. It is deliberately NOT a
    # threshold: an edge either exists or it does not, and the decision to
    # create one at all is still made by the strict rules in
    # pipeline.indexer.resolve_reference.
    confidence: float = 1.0

    # Which rule created this edge - "same-file", "unique-name",
    # "receiver-type", "arity" or "import-scope". Stored on the edge so that
    # a surprising link can be explained rather than argued about, and so
    # the run summary can report how much each rule is actually earning.
    resolved_by: str = "same-file"


class ParsedFile(BaseModel):
    """
    Everything we learned from reading ONE source file.

    parsing/ produces this; pipeline/ consumes it. It exists so the parser can
    hand back entities and edges together as a single, obviously-related unit.
    """

    file_path: str          # relative path, forward slashes
    language: str           # detected from the file extension
    content_hash: str       # sha256 of the whole file's bytes
    entities: list[CodeEntity]
    relationships: list[Relationship]

    # Raw import lines, e.g. "from .db import GraphStore".
    # Kept as text because turning an import into a real file path is a
    # per-language guessing game - see parsing/code_parser.py.
    imports: list[str] = []

    # Interface tokens each entity mentions, as (entity_id, kind, value).
    # These are the shared literal values - route paths, env-var keys - that
    # couple code ACROSS language boundaries, which names never do. See
    # parsing/interfaces.py for why this is the only cross-language signal
    # worth trusting.
    interface_refs: list[tuple[str, str, str]] = []

    # Calls and inheritance the parser could NOT resolve inside this one file,
    # as (source_entity_id, target_name, edge_kind) triples.
    # The target is only a NAME here because the function being called often
    # lives in a different file. storage/ retries these against the whole
    # graph, where it can see every file at once.
    unresolved_references: list[tuple[str, str, str]] = []


class DocChunk(BaseModel):
    """
    ONE piece of a non-code file: a section of a README, a whole config file.

    This is the documentation equivalent of a CodeEntity - the unit we embed
    and store as a node.

    WHY A "SECTION" AND NOT A FIXED-SIZE CHUNK
    ------------------------------------------
    Ordinary RAG chops documents into fixed windows (say 1000 characters),
    which happily cuts a sentence - or a code example - in half. A markdown
    heading and everything under it is a unit the AUTHOR already decided
    belongs together, so it is a far better thing to embed. tree-sitter's
    markdown grammar hands us exactly that, nested by heading depth.
    """

    # "README.md::Setup > Install" - the file, then the heading trail.
    # Deterministic like CodeEntity.id, for the same MERGE-not-CREATE reason.
    id: str

    file_path: str

    # "markdown" | "rst" | "yaml" | "json" | "toml" | "text"
    doc_type: str

    # The heading text, or the file name for a whole-file chunk.
    title: str

    # The full trail of headings above this one, e.g. "Setup > Install".
    # Embedded as part of the breadcrumb so a section called "Install" still
    # carries the context of what is being installed.
    heading_path: str

    # Heading depth: 1 for `#`, 2 for `##`. 0 means "the whole file",
    # which is what we use for config files that have no headings.
    level: int

    start_line: int
    end_line: int

    # The actual text of the section, including its heading line.
    text: str

    # sha256 of `text` - the block hash, same role as CodeEntity.content_hash.
    content_hash: str = ""

    embedding: Optional[list[float]] = None


class ParsedDocument(BaseModel):
    """Everything we learned from reading ONE non-code file."""

    file_path: str
    doc_type: str
    content_hash: str
    chunks: list[DocChunk]

    # CONTAINS edges between nested sections. Resolvable immediately, because
    # both ends are in this same file.
    relationships: list[Relationship] = []

    # Interface tokens each chunk mentions, as (chunk_id, kind, value).
    # A compose file or a .env.example DECLARES the variables that code
    # elsewhere reads, so this is what connects configuration to its
    # consumers.
    interface_refs: list[tuple[str, str, str]] = []

    # Names this document mentions in `backticks`, as (chunk_id, name).
    # Left unresolved here for the same reason calls are: the function being
    # named almost always lives in a different file. pipeline/ matches these
    # against the whole graph and links only on a unique match.
    mentions: list[tuple[str, str]] = []


class FolderSummary(BaseModel):
    """
    A short description of ONE folder, written by the LLM at index time.

    WHY THIS IS A NODE TYPE OF ITS OWN
    ----------------------------------
    Everything else in the graph is LOCAL - a function, a section, a constant.
    That makes "where is X?" easy and "what is all of this?" impossible: a
    question whose answer is a synthesis over many nodes has nothing to match
    against, so it retrieves a few arbitrary local hits and the model invents
    the rest.

    A summary node is the smallest thing that fixes that. It is a real,
    retrievable piece of text that says what a folder is for, so broad
    questions land on evidence instead of on a guess.
    """

    # "summary::src/auth" - deterministic, like every other id here.
    id: str

    # Repo-relative folder path. "" is the repository root.
    path: str

    # The generated prose.
    text: str

    # How many real entities the folder held when this was written. Used to
    # rank a subsystem's summary above a folder holding two helpers.
    entity_count: int = 0

    # The folder's MERKLE hash. If it is unchanged then nothing inside the
    # folder changed at any depth, so the summary is still true and we do not
    # pay to regenerate it.
    content_hash: str = ""

    embedding: Optional[list[float]] = None


class RetrievedSummary(BaseModel):
    """A FolderSummary that came back from a search."""

    summary: FolderSummary
    score: float = 0.0
    reason: str = ""


class RetrievedDoc(BaseModel):
    """A DocChunk that came back from a search, plus why."""

    chunk: DocChunk

    # 0 = found directly by vector search.
    # 1 = pulled in because it mentions a code entity we matched.
    hop: int

    score: float = 0.0
    reason: str = ""


class RetrievedEntity(BaseModel):
    """
    A CodeEntity that came back from a search, plus WHY it came back.

    retrieval/ produces these. The two extra fields let us build a sensible
    prompt: seeds go first, far-away nodes get dropped first when we run out
    of room in the context budget.
    """

    entity: CodeEntity

    # 0 for a "seed" (found directly by vector search),
    # 1 for a direct neighbour of a seed, 2 for a neighbour-of-a-neighbour.
    hop: int

    # Cosine similarity from the vector search (only meaningful for hop 0).
    score: float = 0.0

    # A short human-readable note, e.g. "called by UserService.login".
    # Printed into the prompt so the LLM understands how the pieces connect.
    reason: str = ""
