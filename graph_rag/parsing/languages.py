"""
parsing/languages.py
====================

The per-language rule book.

WHY THIS FILE EXISTS
--------------------
tree-sitter gives us a syntax tree for any language, but every language's
grammar invents its own names for things. A function is:

    Python      function_definition
    JavaScript  function_declaration
    TypeScript  function_declaration  (plus arrow functions!)
    Go          function_declaration  AND  method_declaration
    Rust        function_item
    Java        method_declaration

If we hardcoded Python's names, running the parser over a Rust file would
find ZERO functions and report no error at all - it would just silently do
nothing, which is the worst kind of bug. So instead we write the names down
per language in one table and the parser reads from the table.

Adding a new language = adding one entry to LANGUAGE_CONFIGS plus one line
in EXTENSION_TO_LANGUAGE. No other file changes.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# `tree_sitter_language_pack` is the MAINTAINED package.
# Do NOT use `tree_sitter_languages` (no trailing "-pack"): that one is
# abandoned and does not build on Python 3.12+. Both expose get_parser(),
# so the only difference is the import line.
from tree_sitter_language_pack import get_parser


@dataclass
class LanguageConfig:
    """
    Everything the parser needs to know about one programming language.

    Each field is a tuple of tree-sitter node-type strings. The parser asks
    "is this node's type in this tuple?" and acts accordingly.
    """

    # Our own name for the language, stored on every node in LadybugDB.
    name: str

    # Node types that represent a callable thing (function/method/constructor).
    function_types: tuple[str, ...]

    # Node types that represent a type-like container (class/struct/interface).
    class_types: tuple[str, ...]

    # Node types that represent "one function calls another".
    call_types: tuple[str, ...] = ()

    # Which named field of a call node holds the thing being called.
    # Python: `call` has a field "function". Java: `method_invocation` has "name".
    call_function_field: str = "function"

    # Node types for import/use statements at the top of a file.
    import_types: tuple[str, ...] = ()

    # Which field of a class node lists its parent classes.
    # Python uses "superclasses", Java uses "superclass", TS uses none
    # (it hides them inside a class_heritage child instead).
    extends_field: Optional[str] = None

    # Which field of a function/class node holds its name.
    # Almost every grammar calls it "name", which is why this has a default.
    name_field: str = "name"

    # Node types that hold a doc comment we want to capture.
    # Python's docstring is an expression_statement->string as the first
    # child of the body; other languages use line/block comments above.
    comment_types: tuple[str, ...] = field(default_factory=tuple)

    # Per-call-node-type overrides for `call_function_field`.
    #
    # One field name is not enough for every language. PHP is the clearest
    # case: `function_call_expression` keeps the callee in "function", but
    # `member_call_expression` ($obj->method()) keeps it in "name" and puts
    # the OBJECT in "function". Using one field for both made every method
    # call resolve to the object's name instead of the method's, so
    # `$this->repo->find()` was recorded as a call to `repo`.
    call_function_fields: dict[str, str] = field(default_factory=dict)

    # Languages where an import is an ordinary function call, not a
    # statement of its own (Ruby's `require`). Listing the names here lets
    # us pick those calls out WITHOUT declaring every call an import - which
    # is what happened when import_types was set to ("call",) and turned
    # every `foo(bar)` in a Ruby file into an import line.
    import_call_names: tuple[str, ...] = ()

    # Per-node-type overrides for `name_field`.
    #
    # Rust is the case that forced this. `impl Priced for LineItem` is an
    # impl_item with no "name" field, so the fallback took the first
    # identifier - the TRAIT, "Priced". That collided with `trait Priced`,
    # making both entities ambiguous so neither could ever be linked, and it
    # filed the impl's methods under `Priced.total` when they belong to
    # LineItem. The "type" field names the type being implemented, which is
    # what the block is actually about.
    name_fields: dict[str, str] = field(default_factory=dict)

    # Node types that are an "implementation block" rather than a type
    # declaration - Rust's `impl`. They hold methods and are named after the
    # type they implement, so they must not also answer to that type's name
    # when a call is resolved. See the `impl` kind in code_parser.
    impl_types: tuple[str, ...] = ()

    # Node types that declare a CONSTRUCTOR.
    #
    # A constructor's grammar name is its class's name, which put two
    # entities called `OrderService` in one file: the class and its
    # constructor. Every `new OrderService(...)` then matched both, was
    # judged ambiguous, and got no edge - permanently. Constructors are
    # renamed `<init>` so the class owns its name outright and construction
    # links to the class, which is what a caller means by it.
    constructor_types: tuple[str, ...] = ()

    # Modifier keywords a declaration must ALL carry to count as a constant.
    #
    # Java and C# reuse one node type for "class constant" and "instance
    # field", so `private final OrderRepository repo;` became a constant
    # called `repo`. That is not a constant, it is state - and it added a
    # name to the graph that collides with every other `repo`. Requiring the
    # modifiers that actually mean "constant" separates the two.
    constant_required_modifiers: tuple[str, ...] = ()

    # Which field names the TYPE a definition belongs to, when the language
    # declares membership somewhere other than by nesting.
    #
    # WHY THIS FIELD HAD TO EXIST
    # ---------------------------
    # Everything else in this table assumes membership is expressed by
    # NESTING: a method is a method because it sits inside a class. Two of
    # the languages here do not work that way at all.
    #
    #   Go     func (e *Engine) Apply(...)   - a TOP-LEVEL declaration that
    #          names its type in a `receiver` field.
    #   Rust   impl LineItem { ... }         - a SEPARATE block that names
    #          the type it belongs to.
    #
    # The information was sitting in the syntax tree the whole time; there
    # was simply nowhere to record it. So a Go method came out as a free
    # function parented to the file's <module> node, and `Engine` ended up
    # with no outgoing edges at all - "what methods does Engine have?" was
    # not answerable from the graph, only by reading source text that
    # retrieval happened to include.
    receiver_field: Optional[str] = None

    # Which field of an `impl` block names the TRAIT being implemented.
    # `impl Priced for LineItem` is the Rust spelling of "LineItem implements
    # Priced", and without reading it the trait was an isolated node that
    # nothing in the graph pointed at.
    impl_trait_field: Optional[str] = None

    # Node types for a named module-level constant or variable.
    #
    # WHY THESE ARE ENTITIES NOW
    # --------------------------
    # `MAX_RETRIES = 3` used to live only inside the file's <module> node,
    # with no name of its own. Nothing could link to it, and asking "what is
    # MAX_RETRIES set to?" could only match the whole module blob. Giving
    # each one a real node with a real `name` makes it findable, citable,
    # and linkable from documentation.
    constant_types: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# The rule book itself
# ---------------------------------------------------------------------------

LANGUAGE_CONFIGS: dict[str, LanguageConfig] = {
    "python": LanguageConfig(
        name="python",
        function_types=("function_definition",),
        class_types=("class_definition",),
        call_types=("call",),
        call_function_field="function",
        import_types=("import_statement", "import_from_statement"),
        extends_field="superclasses",
        comment_types=("comment",),
        constant_types=("assignment",),
    ),
    "javascript": LanguageConfig(
        name="javascript",
        # `arrow_function` and `function_expression` matter a lot in JS/TS:
        #     const handler = () => { ... }
        # is extremely common and is NOT a `function_declaration`. If we only
        # looked for function_declaration we would miss most of a modern
        # React or Node codebase.
        function_types=(
            "function_declaration",
            "generator_function_declaration",
            "method_definition",
            "arrow_function",
            "function_expression",
        ),
        class_types=("class_declaration",),
        call_types=("call_expression", "new_expression"),
        call_function_field="function",
        import_types=("import_statement",),
        comment_types=("comment",),
        constant_types=("variable_declarator",),
    ),
    "typescript": LanguageConfig(
        name="typescript",
        function_types=(
            "function_declaration",
            "generator_function_declaration",
            "method_definition",
            "arrow_function",
            "function_expression",
            "function_signature",          # declarations in .d.ts files
        ),
        class_types=(
            "class_declaration",
            "interface_declaration",
            "abstract_class_declaration",
        ),
        call_types=("call_expression", "new_expression"),
        call_function_field="function",
        import_types=("import_statement",),
        comment_types=("comment",),
        constant_types=("variable_declarator",),
    ),
    "tsx": LanguageConfig(
        # tsx is a separate grammar from typescript because of JSX syntax,
        # but the node names are the same, so the config is a copy.
        name="tsx",
        function_types=(
            "function_declaration",
            "generator_function_declaration",
            "method_definition",
            "arrow_function",
            "function_expression",
        ),
        class_types=("class_declaration", "interface_declaration"),
        call_types=("call_expression", "new_expression"),
        call_function_field="function",
        import_types=("import_statement",),
        comment_types=("comment",),
        constant_types=("variable_declarator",),
    ),
    "go": LanguageConfig(
        name="go",
        # In Go a method is a TOP-LEVEL node with a "receiver", not something
        # nested inside a type. So Go methods will show up as top-level
        # entities, unlike Python methods which nest inside their class.
        function_types=("function_declaration", "method_declaration"),
        # `type_spec` and NOT `type_declaration`. This mattered a great deal:
        # `type_declaration` is only a wrapper and carries no "name" field, so
        # every Go struct and interface came back nameless, was treated as an
        # anonymous node, and was dropped. A Go codebase produced functions and
        # NO types at all - silently, with no error.
        class_types=("type_spec",),
        call_types=("call_expression",),
        call_function_field="function",
        import_types=("import_declaration",),
        comment_types=("comment",),
        constant_types=("const_spec", "var_spec"),
        # `func (e *Engine) Apply(...)` - the receiver names the owning type.
        receiver_field="receiver",
    ),
    "rust": LanguageConfig(
        name="rust",
        function_types=("function_item",),
        # Rust has no classes. structs, enums, traits and impl blocks are the
        # closest equivalents, so we treat all of them as "class"-like.
        class_types=("struct_item", "enum_item", "trait_item", "impl_item"),
        call_types=("call_expression", "macro_invocation"),
        call_function_field="function",
        import_types=("use_declaration",),
        comment_types=("line_comment", "block_comment"),
        constant_types=("const_item", "static_item"),
        # `impl Priced for LineItem` is about LineItem, not Priced.
        name_fields={"impl_item": "type"},
        impl_types=("impl_item",),
        # ...but it also SAYS that LineItem implements Priced, which is an
        # inheritance edge we were throwing away.
        impl_trait_field="trait",
    ),
    "java": LanguageConfig(
        name="java",
        function_types=("method_declaration", "constructor_declaration"),
        class_types=("class_declaration", "interface_declaration", "enum_declaration"),
        call_types=("method_invocation", "object_creation_expression"),
        # NOTE: Java's method_invocation puts the method name in the "name"
        # field, not "function". This is exactly the kind of per-language
        # difference this table exists to absorb.
        call_function_field="name",
        import_types=("import_declaration",),
        extends_field="superclass",
        comment_types=("line_comment", "block_comment"),
        constant_types=("field_declaration",),
        constructor_types=("constructor_declaration",),
        # `static final` is how Java spells "constant". A field with only
        # `final` is an immutable instance field, which is ordinary state.
        constant_required_modifiers=("static", "final"),
    ),
    "csharp": LanguageConfig(
        name="csharp",
        function_types=(
            "method_declaration", "constructor_declaration",
            "local_function_statement", "property_declaration",
        ),
        class_types=(
            "class_declaration", "interface_declaration",
            "struct_declaration", "record_declaration", "enum_declaration",
        ),
        call_types=("invocation_expression", "object_creation_expression"),
        call_function_field="function",
        import_types=("using_directive",),
        comment_types=("comment",),
        constant_types=("field_declaration",),
        constructor_types=("constructor_declaration",),
        # `const` only. `static readonly` is also a constant in C#, but
        # admitting `readonly` alone would let every injected dependency
        # (`private readonly IRepo repo;`) back in, which is the noise this
        # rule exists to remove. Missing a few real constants is the cheaper
        # error: they are still inside the class's own node.
        constant_required_modifiers=("const",),
    ),
    "ruby": LanguageConfig(
        name="ruby",
        function_types=("method", "singleton_method"),
        class_types=("class", "module", "singleton_class"),
        call_types=("call",),
        call_function_field="method",
        # Ruby has no import STATEMENT - `require` is an ordinary method call.
        # Declaring ("call",) an import type therefore made every single
        # method call in the file an import line, so a class full of ordinary
        # calls reported them all as imports. We match on the callee NAME
        # instead, which selects exactly the ones that really are imports.
        import_types=(),
        import_call_names=("require", "require_relative", "load"),
        comment_types=("comment",),
        constant_types=("assignment",),
    ),
    "php": LanguageConfig(
        name="php",
        function_types=("function_definition", "method_declaration"),
        class_types=("class_declaration", "interface_declaration", "trait_declaration"),
        call_types=("function_call_expression", "member_call_expression",
                    "nullsafe_member_call_expression", "scoped_call_expression",
                    "object_creation_expression"),
        call_function_field="function",
        # $this->repo->find() is a member_call_expression whose "function"
        # field is the OBJECT ($this->repo) and whose "name" field is the
        # method. Reading "function" recorded a call to `repo` instead of to
        # `find`, so essentially every method call in a PHP codebase pointed
        # at the wrong thing.
        call_function_fields={
            "member_call_expression": "name",
            "nullsafe_member_call_expression": "name",
            "scoped_call_expression": "name",
        },
        import_types=("namespace_use_declaration",),
        comment_types=("comment",),
        constant_types=("const_element",),
    ),
    "kotlin": LanguageConfig(
        name="kotlin",
        # Kotlin's grammar exposes no "name" field on these; the parser falls
        # back to the first identifier child. See _node_name in code_parser.
        function_types=("function_declaration",),
        class_types=("class_declaration", "object_declaration"),
        call_types=("call_expression",),
        call_function_field="function",
        import_types=("import_header",),
        comment_types=("line_comment", "multiline_comment"),
        constant_types=("property_declaration",),
    ),
    "swift": LanguageConfig(
        name="swift",
        function_types=("function_declaration", "init_declaration"),
        class_types=("class_declaration", "protocol_declaration"),
        call_types=("call_expression",),
        call_function_field="function",
        import_types=("import_declaration",),
        comment_types=("comment", "multiline_comment"),
        constant_types=("property_declaration",),
        constructor_types=("init_declaration",),
    ),
    "scala": LanguageConfig(
        name="scala",
        function_types=("function_definition",),
        class_types=("class_definition", "object_definition", "trait_definition"),
        call_types=("call_expression",),
        call_function_field="function",
        import_types=("import_declaration",),
        comment_types=("comment",),
        constant_types=("val_definition", "var_definition"),
    ),
    "c": LanguageConfig(
        name="c",
        # C names live under a `declarator` chain rather than a "name" field,
        # which _node_name unwraps.
        function_types=("function_definition",),
        class_types=("struct_specifier", "union_specifier", "enum_specifier"),
        call_types=("call_expression",),
        call_function_field="function",
        import_types=("preproc_include",),
        comment_types=("comment",),
        constant_types=("preproc_def",),
    ),
    "cpp": LanguageConfig(
        name="cpp",
        function_types=("function_definition",),
        class_types=("class_specifier", "struct_specifier", "union_specifier"),
        call_types=("call_expression",),
        call_function_field="function",
        import_types=("preproc_include",),
        comment_types=("comment",),
        constant_types=("preproc_def",),
    ),
    "bash": LanguageConfig(
        name="bash",
        function_types=("function_definition",),
        class_types=(),          # shell has no type declarations
        call_types=("command",),
        call_function_field="name",
        comment_types=("comment",),
        constant_types=("variable_assignment",),
    ),
    "lua": LanguageConfig(
        name="lua",
        function_types=("function_declaration",),
        class_types=(),
        call_types=("function_call",),
        call_function_field="name",
        comment_types=("comment",),
        constant_types=("assignment_statement",),
    ),
}


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

# File extension -> language name. This is the primary detection method
# because it is fast, reliable, and needs no file reading.
#
# IMPORTANT: detection happens PER FILE, not once per repository. Real
# codebases mix languages (a Go backend + a TypeScript frontend in one repo),
# so "this project is Python" is not a thing we can assume.
EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".cs": "csharp",
    ".rb": "ruby",
    ".rake": "ruby",
    ".php": "php",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".scala": "scala",
    ".sc": "scala",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".lua": "lua",
}


# ---------------------------------------------------------------------------
# NON-CODE files: documentation and configuration
# ---------------------------------------------------------------------------
#
# These are not code, but they answer a huge share of real questions. "How do
# I add a new field?" is usually answered by a README, and "where is the
# timeout set?" by a yaml file. Leaving them out means the system cannot see
# the half of the repository that explains itself.
#
# They are parsed differently - see parsing/doc_parser.py - because the unit
# worth embedding is a SECTION, not a function.

DOC_EXTENSION_TO_TYPE: dict[str, str] = {
    # Prose. These have headings, so we can split them into real sections.
    ".md": "markdown",
    ".mdx": "markdown",
    ".markdown": "markdown",
    ".rst": "rst",
    ".txt": "text",
    ".adoc": "text",

    # Configuration and data. Usually small and meaningfully a single unit,
    # so these are stored whole (or split on blank lines if very large).
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".jsonc": "json",
    ".json5": "json",
    ".toml": "toml",
    ".ini": "text",
    ".cfg": "text",
    ".conf": "text",
    ".properties": "text",
    ".env.example": "text",

    # Markup, styles and templates. These carry real answers - "where is the
    # submit button wired up?" lives in an .html or .vue file.
    ".html": "text",
    ".htm": "text",
    ".xml": "text",
    ".svg": "text",
    ".css": "text",
    ".scss": "text",
    ".sass": "text",
    ".less": "text",
    ".vue": "text",
    ".svelte": "text",
    ".astro": "text",
    ".hbs": "text",
    ".ejs": "text",
    ".jinja": "text",
    ".j2": "text",

    # Schemas and queries - often the single best description of a data model.
    ".sql": "text",
    ".graphql": "text",
    ".gql": "text",
    ".proto": "text",
    ".avsc": "json",
    ".prisma": "text",

    # Infrastructure as code.
    ".tf": "text",
    ".tfvars": "text",
    ".hcl": "text",
    ".nomad": "text",

    # Data. Indexed like anything else - the size cap is what keeps a giant
    # export out, not the extension.
    ".csv": "text",
    ".tsv": "text",
    ".ndjson": "text",
    ".jsonl": "text",

    # Notebooks are JSON on disk; indexing them as text is crude but means
    # the code and prose inside them are at least searchable.
    ".ipynb": "json",

    # Misc text.
    ".org": "text",
    ".tex": "text",
    ".r": "text",
    ".ps1": "text",
    ".bat": "text",
    ".patch": "text",
    ".diff": "text",
}

# Files with no extension that are still worth indexing, matched by exact name.
DOC_FILENAMES: dict[str, str] = {
    "Dockerfile": "text",
    "Containerfile": "text",
    "Makefile": "text",
    "Justfile": "text",
    "Rakefile": "ruby",
    "Gemfile": "ruby",
    "Procfile": "text",
    "Vagrantfile": "ruby",
    "Jenkinsfile": "text",
    "CODEOWNERS": "text",
    "README": "text",
    "LICENSE": "text",
    "CHANGELOG": "text",
    "AUTHORS": "text",
    ".gitignore": "text",
    ".dockerignore": "text",
    ".gitattributes": "text",
    ".editorconfig": "text",
    ".env.example": "text",
}

# Extensions that are definitely NOT text. Listed explicitly because the
# default for an unknown extension is now to INDEX it (see is_indexable), so
# this list plus the null-byte sniff in change_detection/ is what keeps
# binaries out.
BINARY_EXTENSIONS: set[str] = {
    # images
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tiff", ".avif",
    ".psd", ".ai", ".eps",
    # fonts
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    # audio / video
    ".mp3", ".mp4", ".wav", ".ogg", ".flac", ".avi", ".mov", ".webm", ".mkv",
    # archives
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war",
    # compiled / binary
    ".pyc", ".pyo", ".pyd", ".so", ".dll", ".dylib", ".exe", ".bin", ".o",
    ".a", ".lib", ".obj", ".class", ".wasm", ".node",
    # documents and data stores
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".db", ".sqlite", ".sqlite3", ".mdb",
    ".parquet", ".avro", ".orc", ".feather", ".npy", ".npz", ".pkl", ".pickle",
    ".h5", ".hdf5", ".onnx", ".pt", ".pth", ".ckpt", ".safetensors", ".bin",
}

# Never index these, even though their extension says we could.
#
# Lock files are enormous, machine-generated, and change constantly - they
# would dominate the embedding bill while answering no question anyone asks.
# Minified bundles are one giant unreadable line.
SKIPPED_FILENAMES: set[str] = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Cargo.lock",
    "composer.lock",
    "go.sum",
}

SKIPPED_SUFFIXES: tuple[str, ...] = (
    ".min.js",
    ".min.css",
    ".lock",
    ".map",
)


# Files that hold CREDENTIALS. These must never be indexed.
#
# THIS IS A SECURITY BOUNDARY, NOT A TIDINESS RULE.
# Indexing a file means its contents are sent to the OpenAI embeddings API
# and then stored in LadybugDB. A .env file swept up by accident would put your
# live database password and API keys in a third party's logs, and would
# then be retrievable by anyone who can ask this system a question.
#
# So the rule here is deliberately broad and deny-by-default: if a name
# looks like it holds a secret, we skip it, even at the cost of occasionally
# skipping something harmless.
SECRET_EXACT_NAMES: set[str] = {
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    ".npmrc", ".pypirc", ".netrc", ".htpasswd",
    "credentials", "credentials.json",
    "service-account.json",
}

SECRET_SUFFIXES: tuple[str, ...] = (
    ".pem", ".key", ".pfx", ".p12", ".keystore", ".jks", ".ppk",
)

# Template files are the deliberate exception: they exist to be shared and
# contain placeholders rather than real values.
SECRET_TEMPLATE_NAMES: set[str] = {
    ".env.example", ".env.sample", ".env.template", ".env.dist",
}

# Folders whose CONTENTS are secret, whatever the individual files are named.
#
# Checking filenames alone is not enough, and this was a real hole: a file at
# `config/secrets/prod.yaml` has a perfectly innocent name and was being
# indexed. So is `.aws/config`, which sits next to `.aws/credentials` and is
# just as sensitive. If any folder on the path is one of these, the file is
# secret no matter what it is called.
SECRET_DIRECTORY_NAMES: set[str] = {
    "secrets", "secret", "credentials",
    ".ssh", ".aws", ".gnupg", ".azure", ".kube", ".docker",
    "private_keys", "keys",
}


def looks_like_secret(file_path: str | Path) -> bool:
    """
    Does this file look like it holds credentials?

    Deliberately errs on the side of skipping. See SECRET_EXACT_NAMES above
    for why a false positive here is cheap and a false negative is not.
    """
    path = Path(file_path)
    name = path.name
    lower = name.lower()

    # Templates are safe and genuinely useful to index.
    if lower in SECRET_TEMPLATE_NAMES:
        return False

    # Anything inside a secrets folder is secret, whatever it is called.
    # We check EVERY folder on the path, not just the immediate parent, so
    # `config/secrets/prod/db.yaml` is caught as well as `secrets/db.yaml`.
    for part in path.parts[:-1]:
        if part.lower() in SECRET_DIRECTORY_NAMES:
            return True

    # `.env`, and every variant: .env.local, .env.production, .env.staging...
    if lower == ".env" or lower.startswith(".env."):
        return True

    if lower in SECRET_EXACT_NAMES:
        return True

    if any(lower.endswith(suffix) for suffix in SECRET_SUFFIXES):
        return True

    # secrets.yaml, secret.json, secrets.local.toml, ...
    if lower.startswith("secrets.") or lower.startswith("secret."):
        return True

    return False


def is_skipped(file_path: str | Path) -> bool:
    """
    Files we refuse to index no matter what their extension says.

    Two separate reasons to refuse:

    1. SECRETS - see looks_like_secret above. This is a security boundary.

    2. Machine-generated noise. This has to be checked for CODE too, not just
       documents: `bundle.min.js` has a .js extension and would otherwise be
       parsed as JavaScript, even though it is one generated line that
       answers no question and would blow the embedding budget on its own.
    """
    if looks_like_secret(file_path):
        return True

    name = Path(file_path).name
    if name in SKIPPED_FILENAMES:
        return True
    return any(name.endswith(suffix) for suffix in SKIPPED_SUFFIXES)


def detect_doc_type(file_path: str | Path) -> Optional[str]:
    """
    Work out whether this is a documentation/config file we should index.

    Returns a doc type ("markdown", "yaml", ...) or None to skip the file.
    Checked in this order: skip list, then exact filename, then extension.
    """
    path = Path(file_path)

    if is_skipped(path):
        return None

    # Files identified by their whole name rather than an extension.
    if path.name in DOC_FILENAMES:
        return DOC_FILENAMES[path.name]

    return DOC_EXTENSION_TO_TYPE.get(path.suffix.lower())


def is_binary_extension(file_path: str | Path) -> bool:
    """Is this extension one we know holds binary data?"""
    return Path(file_path).suffix.lower() in BINARY_EXTENSIONS


def looks_binary(data: bytes) -> bool:
    """
    Content check for a file whose extension we do not recognise.

    A single NUL byte in the first few KB is the classic, reliable signal:
    text files essentially never contain one, and binary formats almost
    always do near the start. We also refuse anything that will not decode
    as UTF-8, since we could not embed it as text anyway.
    """
    head = data[:8192]
    if b"\x00" in head:
        return True
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        # A multi-byte character can legitimately be cut in half at the 8192
        # boundary, so only judge it binary if a generous trim still fails.
        try:
            head[:-4].decode("utf-8")
        except UnicodeDecodeError:
            return True
    return False


def is_indexable(file_path: str | Path) -> bool:
    """
    Should this file be part of the graph at all?

    THE DEFAULT IS YES. This is the deliberate policy: index as much of the
    project as possible, and refuse only what we have a specific reason to
    refuse. An earlier version worked the other way round - an allowlist of
    known extensions - and quietly dropped every .sql, .graphql, .proto,
    .html, .tf and .csv file in the repository. Those hold real answers.

    So a file is refused only when it is:
      1. a secret (see looks_like_secret) - a hard security boundary;
      2. generated or vendored noise (lock files, minified bundles);
      3. a known binary format, which we could not embed as text.

    Anything else is indexed: as CODE if we have a grammar for it, as a
    DOCUMENT otherwise. Files with an unfamiliar extension get one more
    check on their actual bytes in change_detection/ before being accepted.

    change_detection/ calls this to decide what goes into the Merkle tree.
    Anything that returns False is invisible to the whole system: it does not
    affect any folder hash, so editing it never triggers re-indexing.
    """
    if is_skipped(file_path):
        return False
    if is_binary_extension(file_path):
        return False
    return True


def classify(file_path: str | Path) -> tuple[str | None, str | None]:
    """
    Decide how to read one file: (code_language, doc_type).

    Exactly one of the pair is set, or both are None when the file should be
    skipped. Unknown-but-textual extensions come back as ("text"), which is
    what makes the default-allow policy above actually reach the graph.
    """
    if not is_indexable(file_path):
        return (None, None)

    language = detect_language(file_path)
    if language is not None:
        return (language, None)

    doc_type = detect_doc_type(file_path)
    if doc_type is not None:
        return (None, doc_type)

    # Unrecognised extension, but nothing says it is binary or secret.
    # Treat it as plain text; change_detection/ sniffs the bytes to confirm.
    return (None, "text")


def detect_language(file_path: str | Path) -> Optional[str]:
    """
    Work out which language a file is written in, from its extension.

    Returns the language name (e.g. "python"), or None if we do not support
    this file type. Returning None is normal and expected - a repo is full of
    .md, .json, .png and .lock files we simply skip.
    """
    suffix = Path(file_path).suffix.lower()
    return EXTENSION_TO_LANGUAGE.get(suffix)


# tree-sitter parsers are relatively expensive to construct, and we parse
# thousands of files, so we build each one once and remember it here.
# (A dict used this way is called a "cache".)
_PARSER_CACHE: dict[str, object] = {}


def get_language_config(language: str) -> LanguageConfig:
    """Look up the rule book for a language. Raises KeyError if unsupported."""
    return LANGUAGE_CONFIGS[language]


def get_cached_parser(language: str):
    """
    Return a ready-to-use tree-sitter parser for this language.

    The first call builds it; every later call returns the same object.
    """
    if language not in _PARSER_CACHE:
        # `tsx` and `typescript` are separate grammars in the language pack,
        # and both names are accepted directly, so no translation is needed.
        _PARSER_CACHE[language] = get_parser(language)
    return _PARSER_CACHE[language]


# ---------------------------------------------------------------------------
# Language families, for resolving a call to the right definition
# ---------------------------------------------------------------------------
#
# When a call is resolved by name, we narrow the candidates to the caller's
# own language first - a Python `login` and a TypeScript `login` are
# different functions that happen to share a word.
#
# But "same language" is too strict, because we split some ecosystems into
# several grammars for parsing reasons that have nothing to do with how the
# code is actually wired together:
#
#   `.ts`, `.tsx` and `.js` are three tree-sitter grammars (tsx needs its own
#   because of JSX syntax) but ONE module system. A .tsx component importing
#   a function from a .ts file is completely ordinary, and refusing to link
#   it would leave real calls unconnected.
#
# So resolution compares FAMILIES, not raw language names.
LANGUAGE_FAMILIES: dict[str, str] = {
    "python": "python",
    "javascript": "js",
    "typescript": "js",
    "tsx": "js",
    "go": "go",
    "rust": "rust",
    "java": "jvm",
    "csharp": "csharp",
    "ruby": "ruby",
    "php": "php",
    # Kotlin, Scala and Java all run on the JVM and call into each other
    # freely, so they resolve against one another.
    "kotlin": "jvm",
    "scala": "jvm",
    "swift": "swift",
    # C and C++ share headers and link together, so they are one family.
    "c": "c",
    "cpp": "c",
    "bash": "bash",
    "lua": "lua",
}


def language_family(language: str | None) -> str | None:
    """
    The resolution family a language belongs to.

    Unknown languages fall back to their own name, so a language added to
    LANGUAGE_CONFIGS without a family entry still resolves against itself
    rather than against everything.
    """
    if language is None:
        return None
    return LANGUAGE_FAMILIES.get(language, language)


def supported_languages() -> list[str]:
    """The list of code languages we can parse. Used by --help output."""
    return sorted(LANGUAGE_CONFIGS.keys())


def supported_doc_types() -> list[str]:
    """The list of documentation/config types we can index."""
    return sorted(set(DOC_EXTENSION_TO_TYPE.values()))
