"""Built-in engine defaults.

The CLI loads user settings from config.json and credentials from the OS
keyring, then applies them for one command. Importing this module never
reads a configuration file or an application environment variable.
"""

# ---------------------------------------------------------------------------
# Local LadybugDB settings
# ---------------------------------------------------------------------------
LADYBUG_BUFFER_MB = 256
LADYBUG_THREADS = 2


# ---------------------------------------------------------------------------
# OpenAI ("ChatGPT") settings
# ---------------------------------------------------------------------------

# The API key. There is no sensible default - if it is missing the program
# stops with a clear message (see `validate()` at the bottom of this file).
OPENAI_API_KEY = ""

# The codelens CLI applies saved user settings for each command,
# then restores these built-in defaults for direct library callers.
# The embedding model and dimensions must match the selected graph.
EMBEDDING_MODEL = "text-embedding-3-small"

# How many numbers are in one embedding vector. This MUST match the model:
#   text-embedding-3-small -> 1536
#   text-embedding-3-large -> 3072
# Ladybug stores fixed-width arrays. A model/dimension change requires a
# new database path and a complete re-index.
EMBEDDING_DIM = 1536

# Model used for answers and folder summaries, independently of embeddings.
# Keep the previously tested default; codelens config can select another model.
CHAT_MODEL = "gpt-4.1-nano"


# ---------------------------------------------------------------------------
# Indexing behaviour
# ---------------------------------------------------------------------------

# Folders we never walk into, matched by exact name at EVERY depth (so a    
# nested packages/ui/node_modules is caught as well as a top-level one).
#
# These hold generated output or third-party code. Indexing them would bury
# your own code under tens of thousands of irrelevant nodes and cost real
# money to embed - a single node_modules can be larger than the entire
# project that depends on it.
IGNORED_DIRECTORIES = {
    # --- version control -------------------------------------------------
    ".git", ".svn", ".hg", ".codelens",

    # --- Python ----------------------------------------------------------
    ".venv", "venv", "env", "__pycache__", "site-packages",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".nox",
    "htmlcov",

    # --- JavaScript / TypeScript -----------------------------------------
    "node_modules", "bower_components",
    ".next", ".nuxt", ".svelte-kit", ".angular", ".expo",
    ".turbo", ".yarn", ".parcel-cache", ".nyc_output",

    # --- vendored third-party code ---------------------------------------
    # `vendor/` is the Go and PHP convention for "someone else's code,
    # checked in". It is third-party by definition, so it belongs here for
    # exactly the same reason node_modules does.
    "vendor",

    # --- build output ----------------------------------------------------
    "dist", "build", "out", "target",          # target = Rust / Java
    ".gradle", ".dart_tool", "DerivedData", "Pods",

    # --- test and coverage artefacts -------------------------------------
    # `coverage` and `__snapshots__` are machine-generated. `fixtures` and
    # `testdata` are NOT ignored: they are written by hand, they are what the
    # tests actually assert against, and "what shape is the test data?" is a
    # question people genuinely ask.
    "coverage", "__snapshots__", "artifacts",

    # --- infrastructure state --------------------------------------------
    ".terraform", ".serverless",

    # --- editor ----------------------------------------------------------
    ".idea", ".vscode", ".cache",
}

# Add extra excluded folder names with config set --option extra_ignored_directories=JSON.

# Folder names ending in any of these are also ignored. Kept separate from
# IGNORED_DIRECTORIES because these are suffix matches, not exact names -
# "graph_rag_reviewer.egg-info" is build metadata whose exact name depends on
# the project.
IGNORED_DIRECTORY_SUFFIXES = (
    ".egg-info",
    ".dist-info",
)

# Skip any single CODE file bigger than this. Huge source files are usually
# generated (bundled JS, compiled output) rather than written by a person.
MAX_FILE_SIZE_BYTES = 1_000_000  # 1 MB

# Skip any DOCUMENT or CONFIG file bigger than this - a much tighter limit,
# and the reason is worth understanding.
#
# `.json`, `.yaml` and `.csv` are used for two completely different things:
# small hand-written configuration (worth indexing) and large machine data
# dumps, fixtures and exports (worthless to index, and enormous). Nothing in
# the file extension tells the two apart - but the SIZE does. A package.json
# is a couple of kilobytes; an exported users.json is hundreds.
#
# The default is 100 KB, NOT the 1 MB that code gets, and the difference is
# deliberate. A hand-written config is never large: a package.json is a couple
# of kilobytes, a settings.yaml less. Anything substantially bigger is a data
# dump, and indexing one is actively harmful in three ways at once:
#
#   - it is sent to the OpenAI API and stored in the local graph, so a users.json full
#     of real people becomes retrievable by anyone who can query this system;
#   - it is split into dozens of chunks that swamp the document index, so
#     ordinary questions start retrieving fragments of the dump;
#   - you pay to embed every one of them.
#
# Measured on a 236 KB test export: 60 chunks, 81% of every Doc node in the
# graph, and it was retrieved into the answer for 4 of 5 unrelated questions.
#
# Raise it only if you genuinely want large reference data searchable:
#     codelens config set --option max_doc_file_size_bytes=1000000
MAX_DOC_FILE_SIZE_BYTES = 100_000  # 100 KB

# How many chunks any ONE document may contribute to the index.
#
# The size cap above is the first line of defence; this is the second, and it
# catches the case the size cap cannot: a file that is under the limit but
# still mostly repetition. No hand-written document has fifty distinct
# sections worth retrieving separately, so a file that produces more than
# this is a dump wearing a config file's extension. We keep the first N
# chunks - which is where a real document's summary and structure live - and
# drop the rest.
MAX_DOC_CHUNKS_PER_FILE = 12

# The same cap, but for PROSE documents (.md, .rst, .txt).
#
# The 12-chunk cap above exists to stop a machine-generated data dump - a
# users.json, an exported csv - from flooding the document index. That is a
# real problem and the cap is the right answer to it. But it was applied
# uniformly, including to genuine hand-written documentation, where it
# silently discarded section 13 onwards. A long architecture document is
# exactly the kind of file whose later sections carry the deep detail, and
# losing them without a word is the worst kind of recall hole.
#
# Prose cannot be a data dump - it has headings, an author, and a structure -
# so it gets a much higher ceiling. The tight cap stays where it belongs: on
# the data-shaped types that motivated it.
MAX_PROSE_CHUNKS_PER_FILE = 80

# Document types treated as prose for the cap above.
PROSE_DOC_TYPES = {"markdown", "rst", "text"}


# ---------------------------------------------------------------------------
# Cross-language linking
# ---------------------------------------------------------------------------

# Whether to extract "interface tokens" and link the code that shares them.
#
# WHY THIS EXISTS - the gap it closes
# -----------------------------------
# Call resolution narrows candidates to the caller's own language family, and
# it is right to: a Python `login` and a TypeScript `login` are unrelated
# words, and matching them would invent edges. But that left the system with
# NO mechanism at all for cross-language relation, so a multi-language
# repository came out as several disconnected graphs - measured on the demo
# repo, 11 components and ZERO edges crossing a language boundary. Asked how
# the services fit together, the model had no edges to read and invented the
# integration story instead.
#
# The fix starts from an observation: cross-language coupling is never
# expressed through function NAMES. It is expressed through shared literal
# VALUES - route paths, env-var keys, queue topics, table names. Those strings
# are already sitting in the source. We extract them, give each one a node,
# and link every entity that references it.
#
# The edge is deliberately called REFERENCES and kept separate from CALLS. A
# CALLS edge is a claim about what the program DOES; this is a weaker claim
# about coupling, and labelling it honestly keeps it from contaminating the
# call graph.
EXTRACT_INTERFACES = True

# An interface token shared by more than this many entities is dropped as
# noise. A route referenced by three files is a real seam; a string like "/"
# or "id" referenced by two hundred is not, and linking them all would make
# the graph denser without making it more informative.
MAX_INTERFACE_FANOUT = 12


# ---------------------------------------------------------------------------
# Folder summaries (the answer to broad, whole-repository questions)
# ---------------------------------------------------------------------------

# Whether to generate one short LLM summary per folder at index time.
#
# WHY THIS EXISTS
# ---------------
# Retrieval here is entirely LOCAL: every answer starts at a point match and
# walks outwards. Nothing in the graph represents "the auth subsystem" or
# "this repository", so a question whose answer is a SYNTHESIS across many
# nodes has nothing to retrieve. The system returns a handful of arbitrary
# local matches and the model fills the gap by guessing - which is exactly
# what happened when the demo repo was asked what its services were and how
# they fit together.
#
# A summary node per folder gives those questions something real to match.
# They are regenerated only when the folder's Merkle hash changes, so they
# cost one cheap LLM call per CHANGED folder rather than per run.
GENERATE_SUMMARIES = True

# Folders with fewer entities than this are not worth summarising on their
# own - their parent already covers them.
MIN_ENTITIES_FOR_SUMMARY = 3

# How many folder summaries a question retrieves.
DEFAULT_SUMMARY_TOP_K = 2


# How big one chunk of a heading-less file may get before it is split.
# Splitting happens on blank lines, so a chunk never cuts through a line.
MAX_DOC_CHUNK_CHARS = 4000

# The OpenAI embedding endpoint accepts many texts in one request. Batching
# is much faster and cheaper than one request per function.
EMBEDDING_BATCH_SIZE = 64

# A single code block longer than this gets truncated before embedding.
# (Embedding models have a token limit; ~4 characters is roughly 1 token.)
MAX_EMBED_CHARS = 8000


# ---------------------------------------------------------------------------
# Retrieval behaviour (used by the "ask" entry point)
# ---------------------------------------------------------------------------

# How many nodes the vector search returns as starting points ("seeds").
DEFAULT_TOP_K = 5

# How far to walk outwards in the graph from each seed.
# 1 = direct neighbours only. 2 = neighbours of neighbours.
# We cap this at 2 on purpose: at 3+ hops the number of nodes explodes and
# the context fills up with code that has nothing to do with the question.
DEFAULT_HOPS = 1

# The rough size limit of the code we paste into the LLM prompt, in characters.
# ~4 characters per token, so 60000 characters is roughly 15000 tokens.
MAX_CONTEXT_CHARS = 60000

# How many DOCUMENT chunks the vector search returns, on top of the code
# blocks above. Docs and code get separate searches so they cannot crowd each
# other out: a README paragraph about login should not push the actual login
# function out of the results, nor the reverse.
DEFAULT_DOC_TOP_K = 3

# How many code entities a single `backtick` name in a document may link to.
#
# Documentation mentions do NOT use the strict unique-match rule that calls
# use, because a MENTIONS edge only claims "this prose talks about this code".
# Requiring uniqueness produced zero edges on any repository with more than
# one language, since names like `login` or `AuthService` exist in all of
# them. See pipeline.indexer.resolve_mention.
MAX_MENTION_TARGETS = 3

# How many entities sharing one name we will even look at when resolving.
#
# There has to be a cap - a name like `get` or `run` can have hundreds of
# definitions in a large repository, and dragging them all back for every
# pending reference would be slow for no benefit.
#
# The danger is what the cap does SILENTLY. Candidates come back ordered by
# id, which is essentially alphabetical by file path - an ordering chosen for
# determinism, then reused as if it were a ranking. Truncating to the first
# 25 therefore kept an arbitrary, path-biased sample, and the genuinely
# correct target might not be in it at all. The import-visibility rule would
# then narrow that sample to exactly one candidate and link it - confidently,
# to the wrong definition.
#
# So the cap is now much higher, and the count of TOTAL matches comes back
# alongside the sample. When the total exceeds the cap we decline to resolve
# at all rather than guess from a biased sample: a missing edge is recoverable,
# a wrong one quietly sends retrieval to unrelated code.
MAX_NAME_CANDIDATES = 200

# Ignore vector-search hits below this similarity.
#
# Without a floor, a question with no good answer in the codebase still gets
# five "seeds" - the least-bad matches in the graph - and each one drags in
# its neighbours. The prompt then fills with confidently-formatted, unrelated
# code, which is the state most likely to produce a wrong answer. Returning
# little or nothing is the honest outcome, and the prompt already tells the
# model to say when the context does not contain the answer.
#
# THE SCALE THIS IS MEASURED ON - read before changing it
# -------------------------------------------------------
# This preserves the original retrieval contract: cosine normalized into 0..1:
#
#     score = (1 + cosine) / 2
#
# so 0.5 means "orthogonal / unrelated", not 0. Two texts have to be actively
# opposed to score below 0.5, which never happens between a question and real
# source code. A threshold set in raw-cosine terms is therefore below the
# lowest value the index can ever return, and silently filters nothing:
# the previous default of 0.25 corresponded to a cosine of -0.5.
#
# Measured on a 80-entity test graph:
#     an on-topic question       top hits  0.68 - 0.71
#     a deliberately absurd one  top hits  0.57 - 0.60, floor 0.49
#
# 0.62 sits in the gap. Calibrate it for your own repository by asking two or
# three questions the codebase genuinely cannot answer and putting the
# threshold above the best score they return. Note that keyword hits carry no
# similarity score and are never filtered here, so questions that name an
# identifier still work even when the wording is unlike the code.
MIN_SIMILARITY = 0.62

# Cap the source text of any ONE retrieved block pasted into the prompt.
#
# `code` is stored untruncated, so a single generated file or a very long
# function could otherwise consume the entire context budget on its own.
MAX_BLOCK_CHARS = 6000

# Weighting between the two searches that make up hybrid retrieval. Both
# lists are merged with Reciprocal Rank Fusion; these multiply each list's
# contribution. Raise KEYWORD if your questions name identifiers directly;
# raise VECTOR if they are phrased in plain English.
HYBRID_VECTOR_WEIGHT = 1.0
HYBRID_KEYWORD_WEIGHT = 1.0

# How many of the TOP VECTOR HITS are guaranteed a seat, whatever the fusion
# maths says.
#
# WHY THIS EXISTS - the bug it fixes
# ----------------------------------
# Reciprocal Rank Fusion keeps only RANKS and throws the scores away, which is
# the right call when fusing two incomparable scales. But it also throws away
# how CONFIDENT each search was, and it rewards agreement between the two
# lists very heavily. With RRF_K = 60 and lists ~15 long, the gap between
# rank 1 and rank 15 is 1/61 vs 1/75 - about 19% - while appearing in both
# lists is worth roughly 100%. So agreement outweighs position about five to
# one.
#
# In a small or homogeneous codebase almost every keyword hit is ALSO in the
# vector top-N, so "agreement" is nearly free and stops being a signal at all.
# Measured on this project's demo repo, the question "how do I format a price
# for display?" put the correct answer (`formatPrice`) at vector rank 1 with
# similarity 0.767 - and RRF ranked it SIXTH, one slot past top_k=5, so it
# never reached the model at all. The five items above it were weaker matches
# that merely appeared in both lists.
#
# Reserving the first couple of vector slots is a safety rail: a search that
# is very sure about its top answer cannot be argued out of it by agreement
# between two mediocre ones. Measured effect on a 12-question ground-truth
# set: seed recall 67% -> 92%, overall recall 92% -> 100%.
#
# Set to 0 to restore pure RRF.
HYBRID_RESERVED_VECTOR_SLOTS = 2

# The `k` constant in Reciprocal Rank Fusion.
#
# 60 is the value from the original paper, which was tuned for TREC runs a
# THOUSAND results deep. On the ~15-item lists this system fuses, a k that
# large flattens the difference between rank 1 and rank 15 almost to nothing,
# which is half of the problem described above. Lowering it makes position
# matter again. It is exposed here so it can be tuned per repository.
RRF_K = 20

# How much to damp a `<module>` node's KEYWORD score.
#
# A module node holds everything at the top of a file - imports, constants,
# wiring - so it is a bag of many unrelated tokens and matches almost any
# query touching that file. That makes it a "hub document" that wins keyword
# rankings on sheer token count rather than relevance: measured on the demo
# repo, `api.ts::<module>` scored 1.571 against a question about formatting
# prices, more than double any real answer.
#
# It is deliberately a penalty and not an exclusion, because "what is in this
# file?" is a real question and the module node is the right answer to it.
# 1.0 disables the damping.
MODULE_KEYWORD_PENALTY = 0.35

# The most children a single `<module>` seed may contribute to the expansion.
#
# A module node CONTAINS every top-level definition in its file, which is
# co-location, not a semantic relationship. Expanding it without a cap hands
# back the whole file as "neighbours": on the demo repo, 9 of 19 retrieved
# blocks arrived this way and crowded out genuinely related code.
MAX_MODULE_EXPANSION = 3

# A RELATIVE similarity floor, applied alongside MIN_SIMILARITY.
#
# MIN_SIMILARITY is an absolute threshold, and an absolute threshold has to be
# hand-calibrated for every repository - the value that is right for one is
# silently wrong for the next. This is the self-calibrating half: drop any hit
# more than this far below the BEST hit for the same question. It needs no
# tuning because it is measured against the question's own best answer.
#
# Set to 0 to disable and rely on the absolute floor alone.
RELATIVE_SIMILARITY_MARGIN = 0.08

# How much a neighbour's own relevance to the question counts, relative to how
# many hops away it is.
#
# Neighbours used to be ordered by hop alone - a GRAPH DISTANCE, not a
# relevance measure - so the context budget was spent on topology instead of
# usefulness. A seed's twenty siblings all sit at hop 1 next to the one
# function that actually answers the question, and whichever the database
# returned first won. Scoring them against the question costs nothing extra:
# the embeddings are already in LadybugDB and the question vector is already
# computed.
NEIGHBOUR_RELEVANCE_WEIGHT = 1.0

# Per-edge-type weighting for that neighbour ranking. A call is a much
# stronger statement about relatedness than sitting in the same file.
EDGE_TYPE_WEIGHTS: dict[str, float] = {
    "CALLS": 1.0,
    "EXTENDS": 0.9,
    "REFERENCES": 0.8,
    # A declared type is a real dependency, but a weaker statement about
    # relatedness than a call: knowing that `checkout` takes an `Order` is
    # useful context, and less useful than knowing what `checkout` does.
    #
    # It also has to sit below CALLS because a widely-used type is a HUB - a
    # `Config` referenced by two hundred signatures would otherwise flood the
    # expansion of anything near it, the same way `<module>` nodes did.
    "USES_TYPE": 0.55,
    "CONTAINS": 0.6,
}


def validate() -> None:
    """
    Check that the settings we cannot invent a default for are actually set.

    Called once at the start of each entry point so the program fails
    immediately with a readable message, instead of failing later with a
    confusing error from deep inside a library.
    """
    if LADYBUG_BUFFER_MB < 32 or LADYBUG_THREADS < 1 or EMBEDDING_DIM < 1:
        raise SystemExit("Use LADYBUG_BUFFER_MB >= 32, LADYBUG_THREADS >= 1, EMBEDDING_DIM >= 1")
    missing = []
    if not OPENAI_API_KEY:
        missing.append("OPENAI_API_KEY")

    if missing:
        raise SystemExit(
            "Missing required settings: "
            + ", ".join(missing)
            + "\nRun codelens init. Library callers can use cli.settings.engine_settings()."
        )
