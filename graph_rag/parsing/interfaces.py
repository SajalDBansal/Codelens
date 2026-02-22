"""
parsing/interfaces.py
=====================

"What does this code talk to that something in ANOTHER language might also
talk to?"

WHY THIS FILE EXISTS
--------------------
Call resolution narrows candidates to the caller's own language family, and
it is right to. A Python `login` and a TypeScript `login` are unrelated words,
and linking them because they spell the same would invent edges that are
simply false.

But that left the system with no mechanism at all for cross-language
relation, so a multi-language repository came out as several disconnected
graphs. Measured on the demo repository: 11 components and ZERO edges
crossing a language boundary. Asked how the services fit together, the model
had no edges to read, so it invented the integration story - "loosely
coupled, well-defined interfaces" - which is exactly the confident
fabrication the grounding prompt exists to prevent.

THE OBSERVATION THIS IS BUILT ON
--------------------------------
Cross-language coupling is never expressed through function NAMES. It is
expressed through shared literal VALUES:

    the TypeScript client fetches   "/api/cart"
    the Python handler routes       "/api/cart"

    the Go service reads            os.Getenv("DATABASE_URL")
    the compose file sets           DATABASE_URL: ...

Those strings are already sitting in the source. Extracting them, giving each
one a node, and linking every entity that references it turns the separate
per-language graphs into one connected graph - without ever guessing that two
functions are "the same" because they share a name.

WHAT WE DELIBERATELY DO NOT CLAIM
---------------------------------
The edge is called REFERENCES, not CALLS. A CALLS edge is a statement about
what the program DOES; this is a much weaker statement about coupling - "both
of these mention the same route". Keeping it a separate edge type means it can
enrich retrieval without contaminating the call graph, and the retriever can
say honestly *why* two pieces of code were shown together.

Only two kinds of token are extracted, both chosen for precision over recall:
routes and environment-variable keys. A wrong interface edge is cheap but not
free - it costs context budget - so it is better to find fewer, real seams
than to link everything that shares a common word.
"""

import re

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
#
# A quoted string that starts with "/" and continues with path-ish characters.
# Requires a letter after the slash, so "/" and "//" and "/ " are not routes.
_ROUTE_RE = re.compile(r"""['"`](/[A-Za-z0-9_][A-Za-z0-9_\-./{}:$]*)['"`]""")

# The SAME route, but written with the base URL interpolated in front of it:
#
#     `${API_BASE}/api/cart`      TypeScript / JavaScript template literal
#     f"{base}/api/cart"          Python f-string
#     `${base}/users/${id}`       base AND a path parameter
#
# WHY THIS SECOND PATTERN HAD TO EXIST
# ------------------------------------
# _ROUTE_RE requires the quote to be followed IMMEDIATELY by "/". That is
# true of the server side, where a route is written as a bare literal, and
# false of almost every client, which prefixes a configured base URL. So the
# pattern matched the Python handler and missed the TypeScript `fetch` that
# calls it - and the seam those two share is the entire point of this file.
#
# Measured on a five-language demo repository before this was added: ONE
# interface node in the whole graph, zero edges crossing a language boundary,
# and the code graph in five islands split exactly along language lines.
#
# Only an interpolation at the very START is allowed. Letting a route match
# anywhere inside a string would turn "and/or" into the route "/or" and
# "see docs/readme" into "/readme", which is exactly the kind of invented
# edge the rest of this file is careful to avoid.
_PREFIXED_ROUTE_RE = re.compile(
    r"""['"`]\s*\$?\{[^}]*\}(/[A-Za-z0-9_][A-Za-z0-9_\-./{}:$]*)['"`]"""
)

# Path PARAMETERS are spelled differently in every framework, and a route only
# links two services if both spellings collapse to the same token:
#
#     Express     /users/:id
#     FastAPI     /users/{id}
#     JS template /users/${id}
#
# All three become /users/* so the Python route and the TypeScript fetch that
# calls it land on the same node.
_PARAM_RE = re.compile(r"\$\{[^}]*\}|\{[^}]*\}|:[A-Za-z_][A-Za-z0-9_]*")

# File extensions that mean this "/..." string is a file path, not a route.
_NOT_A_ROUTE = (
    ".js", ".ts", ".py", ".go", ".rs", ".java", ".css", ".html", ".png",
    ".jpg", ".svg", ".json", ".md", ".txt", ".yml", ".yaml", ".lock",
    # Filesystem paths turn up as literals constantly - "/etc/hosts.conf",
    # "/usr/local/share/app.ini" - and they start with "/" exactly like a
    # route does. The extension is the only thing that tells them apart.
    ".conf", ".cfg", ".ini", ".toml", ".xml", ".sql", ".sh", ".env",
    ".pem", ".key", ".crt", ".log", ".csv", ".pdf", ".ico", ".woff",
)


# ---------------------------------------------------------------------------
# Environment variables
# ---------------------------------------------------------------------------
#
# The common spellings across the languages this project parses. All of them
# require an ALL_CAPS name of at least three characters, which is what keeps
# ordinary dictionary lookups out.
_ENV_PATTERNS = (
    # os.getenv("X"), os.environ["X"], os.environ.get("X"), System.getenv("X")
    re.compile(r"""(?:getenv|Getenv|environ\.get|environ|ENV)\s*[\(\[]\s*['"]([A-Z][A-Z0-9_]{2,})['"]"""),
    # process.env.X
    re.compile(r"""process\.env\.([A-Z][A-Z0-9_]{2,})"""),
    # process.env["X"]
    re.compile(r"""process\.env\[\s*['"]([A-Z][A-Z0-9_]{2,})['"]\s*\]"""),
    # std::env::var("X")
    re.compile(r"""env::var\s*\(\s*['"]([A-Z][A-Z0-9_]{2,})['"]"""),
)

# In a config or env file the key is DECLARED rather than read:
#     DATABASE_URL=postgres://...
#     DATABASE_URL: ${DATABASE_URL}
# Anchored to the start of a line so it cannot match a word inside prose.
_ENV_DECL_RE = re.compile(r"""^\s*-?\s*([A-Z][A-Z0-9_]{2,})\s*[:=]""", re.MULTILINE)

# Environment-shaped names that carry no coupling information because almost
# everything mentions them.
_ENV_STOPWORDS = frozenset({
    "TODO", "FIXME", "NOTE", "XXX", "HACK", "WARNING", "ERROR", "INFO",
    "DEBUG", "GET", "PUT", "POST", "HEAD", "PATCH", "DELETE", "OPTIONS",
    "TRUE", "FALSE", "NULL", "NONE", "AND", "NOT", "FOR", "THE",
})


# ---------------------------------------------------------------------------
# Queue topics
# ---------------------------------------------------------------------------
#
# A topic is the seam between a producer and a consumer that almost never
# share a language - the Python API publishes "orders.created", the Go worker
# subscribes to it. Nothing about the string itself says "topic", though:
# `orders.created` is shaped exactly like an attribute access, and matching
# every dotted lowercase literal would link `res.json` and `self.items` to
# each other across the whole repository.
#
# So the CONTEXT is what we match on, not the value. A dotted or dashed
# literal counts as a topic only when it is being assigned to a name that
# says what it is, or handed to a call that publishes or subscribes. That
# keeps the rule value-based (both ends still have to spell the topic the
# same way) while making it nearly impossible to fire by accident.
_TOPIC_VALUE = r"""([A-Za-z][A-Za-z0-9_]*(?:[.\-][A-Za-z0-9_]+)+)"""

# `OrdersTopic = "orders.created"`, `topic: "orders.created"`,
# `QUEUE_NAME = "orders.created"`, `routingKey = "orders.created"`
_TOPIC_DECL_RE = re.compile(
    r"""(?ix)
    \b\w*(?:topic|queue|channel|subject|stream|exchange|routing_?key|event_?name)\w*
    \s*[:=]\s*
    ['"`]""" + _TOPIC_VALUE + r"""['"`]"""
)

# `publish("orders.created", ...)`, `subscribe("orders.created")`,
# `producer.send("orders.created")`, `emit("user.signup")`
_TOPIC_CALL_RE = re.compile(
    r"""(?ix)
    \b(?:publish|subscribe|produce|consume|emit|enqueue|dispatch|send_to|sendTo)\w*
    \s*\(\s*
    ['"`]""" + _TOPIC_VALUE + r"""['"`]"""
)

# Dotted literals that are shaped like a topic but are something else
# entirely. Version numbers and MIME types are the two that turn up most.
_TOPIC_STOPWORDS = frozenset({
    "utf-8", "utf-16", "us-ascii", "iso-8859-1",
    "application.json", "text.plain",
})


# ---------------------------------------------------------------------------
# Database tables
# ---------------------------------------------------------------------------
#
# Two services that read and write the same table are coupled just as tightly
# as two that call the same route, and a shared database is the commonest way
# a polyglot system integrates without anyone writing it down.
#
# The table name is only extracted from inside a string that is DEMONSTRABLY
# SQL - it has to contain SELECT, INSERT INTO, UPDATE, DELETE FROM or CREATE
# TABLE. Matching a bare `FROM x` against source text would read Python's
# `from api.models.order import Order` as a query against a table called
# `api.models.order`, which is why the SQL-ness has to be established first
# and the table pulled out of that string second.
_SQL_STRING_RE = re.compile(
    r"""['"`]([^'"`]*?\b(?:SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM|CREATE\s+TABLE)\b[^'"`]*?)['"`]""",
    re.IGNORECASE | re.DOTALL,
)

# Inside that SQL, the token after FROM / JOIN / INTO / UPDATE / TABLE.
# Quoted and bracketed forms ("orders", `orders`, [orders]) are all stripped
# so the Postgres and SQL Server spellings land on the same node.
_SQL_TABLE_RE = re.compile(
    r"""(?ix)
    \b(?:FROM|JOIN|INSERT\s+INTO|INTO|UPDATE|CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?)\s+
    ["'`\[]?([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)["'`\]]?
    """
)

# SQL keywords that can directly follow FROM/JOIN and are not table names.
_SQL_NOT_A_TABLE = frozenset({
    "select", "where", "values", "set", "dual", "lateral", "unnest",
    "only", "table", "if", "not", "exists",
})


# ---------------------------------------------------------------------------
# gRPC services
# ---------------------------------------------------------------------------
#
# A .proto file is the one artefact in a polyglot repository that is
# EXPLICITLY a shared contract - it exists so that several languages can
# generate clients against the same definition. Both halves are extracted:
#
#     service OrderService {        ->  grpc:OrderService
#       rpc Checkout (...) ...      ->  grpc:OrderService.Checkout
#
# and, from ordinary source, the wire path a gRPC call actually carries:
#
#     "/shop.OrderService/Checkout" ->  grpc:OrderService.Checkout
#
# The wire path is a genuine shared literal, which is what makes it safe to
# match on. The generated STUB NAMES are deliberately not matched: linking a
# Go `NewOrderServiceClient` to a Java `OrderServiceGrpc` would be a guess
# about names, and this file only ever links on shared values.
_PROTO_SERVICE_RE = re.compile(r"""^\s*service\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{""", re.MULTILINE)
_PROTO_RPC_RE = re.compile(r"""^\s*rpc\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(""", re.MULTILINE)
_GRPC_PATH_RE = re.compile(
    r"""['"`]/(?:[A-Za-z_][A-Za-z0-9_]*\.)*([A-Za-z_][A-Za-z0-9_]*)/([A-Za-z_][A-Za-z0-9_]*)['"`]"""
)


def _extract_topics(text: str) -> set[tuple[str, str]]:
    """Queue / topic names, found by the context they are written in."""
    found: set[tuple[str, str]] = set()
    for pattern in (_TOPIC_DECL_RE, _TOPIC_CALL_RE):
        for match in pattern.finditer(text):
            value = match.group(1)
            if value.lower() in _TOPIC_STOPWORDS:
                continue
            # A bare "1.0" or "v2.1" is a version, not a topic. The value
            # regex already demands a leading letter, so this only has to
            # rule out the case where every later segment is digits.
            if all(part.isdigit() for part in re.split(r"[.\-]", value)[1:]):
                continue
            found.add(("topic", value))
    return found


def _extract_tables(text: str) -> set[tuple[str, str]]:
    """Table names, but only out of strings that are demonstrably SQL."""
    found: set[tuple[str, str]] = set()
    for sql_match in _SQL_STRING_RE.finditer(text):
        for table_match in _SQL_TABLE_RE.finditer(sql_match.group(1)):
            name = table_match.group(1)
            if name.lower() in _SQL_NOT_A_TABLE:
                continue
            # `schema.orders` and `orders` are the same table written two
            # ways; keep the table itself so both spellings meet on one node.
            found.add(("table", name.split(".")[-1].lower()))
    return found


def _extract_grpc(text: str, is_proto: bool = False) -> set[tuple[str, str]]:
    """
    gRPC service and method names.

    In a .proto file the service declaration is the contract, and every rpc
    inside it belongs to the service declared above it - so the file is read
    in order and each rpc is attributed to the most recent `service`.
    """
    found: set[tuple[str, str]] = set()

    if is_proto:
        # Walk services and rpcs together, in source order, so an rpc is
        # filed under the service it is actually nested in.
        marks = [(m.start(), "service", m.group(1)) for m in _PROTO_SERVICE_RE.finditer(text)]
        marks += [(m.start(), "rpc", m.group(1)) for m in _PROTO_RPC_RE.finditer(text)]
        marks.sort()
        current: str | None = None
        for _, kind, name in marks:
            if kind == "service":
                current = name
                found.add(("grpc", name))
            elif current is not None:
                found.add(("grpc", f"{current}.{name}"))

    # The wire path, which appears in generated clients and in interceptor,
    # routing and test code in every language.
    for match in _GRPC_PATH_RE.finditer(text):
        service, method = match.group(1), match.group(2)
        found.add(("grpc", service))
        found.add(("grpc", f"{service}.{method}"))

    return found


def _normalise_route(raw: str) -> str | None:
    """Reduce a route to the form both ends of the wire will agree on."""
    route = _PARAM_RE.sub("*", raw)
    route = route.rstrip("/") or "/"

    lowered = route.lower()
    if lowered.endswith(_NOT_A_ROUTE):
        return None
    # A bare "/" or "/*" says nothing about who talks to whom.
    if route in ("/", "/*"):
        return None
    # Needs at least one real word character to be a route rather than noise.
    if not re.search(r"[A-Za-z]", route):
        return None
    return route


def extract_interface_tokens(
    text: str,
    is_config: bool = False,
    is_proto: bool = False,
) -> set[tuple[str, str]]:
    """
    Find the interface tokens in one piece of source or configuration.

    Returns a set of (kind, value) pairs, where kind is one of:

        "route"   an HTTP path both a client and a server spell out
        "env"     an environment-variable key
        "topic"   a queue / stream name a producer and a consumer share
        "table"   a database table two services both read or write
        "grpc"    a service or service.method from a .proto contract

    Every one of them is a shared literal VALUE rather than a shared NAME,
    which is the rule that keeps this from inventing edges: two functions
    called `login` in different languages are unrelated, but two files that
    both write the string "orders.created" genuinely are talking to each
    other.

    `is_config` also accepts bare `KEY: value` / `KEY=value` declarations,
    which are how a compose file or a .env.example names the same variables
    the code reads. That is what connects configuration to its consumers.

    `is_proto` says this text is a .proto file, so `service` and `rpc`
    declarations in it are the contract itself and not incidental words.
    """
    found: set[tuple[str, str]] = set()

    # Routes, written bare ("/api/cart") and with a base URL interpolated in
    # front of them (`${API_BASE}/api/cart`). The second form is how nearly
    # every client spells it, so both patterns are needed to connect the two
    # ends of the same call.
    for pattern in (_ROUTE_RE, _PREFIXED_ROUTE_RE):
        for match in pattern.finditer(text):
            route = _normalise_route(match.group(1))
            if route:
                found.add(("route", route))

    found |= _extract_topics(text)
    found |= _extract_tables(text)
    found |= _extract_grpc(text, is_proto=is_proto)

    for pattern in _ENV_PATTERNS:
        for match in pattern.finditer(text):
            name = match.group(1)
            if name not in _ENV_STOPWORDS:
                found.add(("env", name))

    if is_config:
        for match in _ENV_DECL_RE.finditer(text):
            name = match.group(1)
            if name not in _ENV_STOPWORDS:
                found.add(("env", name))

    return found


def interface_id(kind: str, value: str) -> str:
    """The stable node id for one interface token, e.g. "route:/api/cart"."""
    return f"{kind}:{value}"
