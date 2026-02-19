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
from tree_sitter_language_pack import get_parser

@dataclass
class LanguageConfig:
    """
    Everything the parser needs to know about one programming language.

    Each field is a tuple of tree-sitter node-type strings. The parser asks
    "is this node's type in this tuple?" and acts accordingly.
    """
    name: str
    function_types: tuple[str, ...]
    class_types: tuple[str, ...]
    call_types: tuple[str, ...] = ()
    call_function_field: str = 'function'
    import_types: tuple[str, ...] = ()
    extends_field: Optional[str] = None
    name_field: str = 'name'
    comment_types: tuple[str, ...] = field(default_factory=tuple)
    call_function_fields: dict[str, str] = field(default_factory=dict)
    import_call_names: tuple[str, ...] = ()
    name_fields: dict[str, str] = field(default_factory=dict)
    impl_types: tuple[str, ...] = ()
    constructor_types: tuple[str, ...] = ()
    constant_required_modifiers: tuple[str, ...] = ()
    receiver_field: Optional[str] = None
    impl_trait_field: Optional[str] = None
    constant_types: tuple[str, ...] = ()
LANGUAGE_CONFIGS: dict[str, LanguageConfig] = {'python': LanguageConfig(name='python', function_types=('function_definition',), class_types=('class_definition',), call_types=('call',), call_function_field='function', import_types=('import_statement', 'import_from_statement'), extends_field='superclasses', comment_types=('comment',), constant_types=('assignment',)), 'javascript': LanguageConfig(name='javascript', function_types=('function_declaration', 'generator_function_declaration', 'method_definition', 'arrow_function', 'function_expression'), class_types=('class_declaration',), call_types=('call_expression', 'new_expression'), call_function_field='function', import_types=('import_statement',), comment_types=('comment',), constant_types=('variable_declarator',)), 'typescript': LanguageConfig(name='typescript', function_types=('function_declaration', 'generator_function_declaration', 'method_definition', 'arrow_function', 'function_expression', 'function_signature'), class_types=('class_declaration', 'interface_declaration', 'abstract_class_declaration'), call_types=('call_expression', 'new_expression'), call_function_field='function', import_types=('import_statement',), comment_types=('comment',), constant_types=('variable_declarator',)), 'go': LanguageConfig(name='go', function_types=('function_declaration', 'method_declaration'), class_types=('type_spec',), call_types=('call_expression',), call_function_field='function', import_types=('import_declaration',), comment_types=('comment',), constant_types=('const_spec', 'var_spec'), receiver_field='receiver'), 'rust': LanguageConfig(name='rust', function_types=('function_item',), class_types=('struct_item', 'enum_item', 'trait_item', 'impl_item'), call_types=('call_expression', 'macro_invocation'), call_function_field='function', import_types=('use_declaration',), comment_types=('line_comment', 'block_comment'), constant_types=('const_item', 'static_item'), name_fields={'impl_item': 'type'}, impl_types=('impl_item',), impl_trait_field='trait'), 'java': LanguageConfig(name='java', function_types=('method_declaration', 'constructor_declaration'), class_types=('class_declaration', 'interface_declaration', 'enum_declaration'), call_types=('method_invocation', 'object_creation_expression'), call_function_field='name', import_types=('import_declaration',), extends_field='superclass', comment_types=('line_comment', 'block_comment'), constant_types=('field_declaration',), constructor_types=('constructor_declaration',), constant_required_modifiers=('static', 'final'))}
EXTENSION_TO_LANGUAGE: dict[str, str] = {'.py': 'python', '.pyi': 'python', '.js': 'javascript', '.jsx': 'javascript', '.mjs': 'javascript', '.cjs': 'javascript', '.ts': 'typescript', '.mts': 'typescript', '.cts': 'typescript', '.go': 'go', '.rs': 'rust', '.java': 'java'}
DOC_EXTENSION_TO_TYPE: dict[str, str] = {'.md': 'markdown', '.mdx': 'markdown', '.markdown': 'markdown', '.rst': 'rst', '.txt': 'text', '.adoc': 'text', '.yaml': 'yaml', '.yml': 'yaml', '.json': 'json', '.jsonc': 'json', '.json5': 'json', '.toml': 'toml', '.ini': 'text', '.cfg': 'text', '.conf': 'text', '.properties': 'text', '.env.example': 'text', '.html': 'text', '.htm': 'text', '.xml': 'text', '.svg': 'text', '.css': 'text', '.scss': 'text', '.sass': 'text', '.less': 'text', '.vue': 'text', '.svelte': 'text', '.astro': 'text', '.hbs': 'text', '.ejs': 'text', '.jinja': 'text', '.j2': 'text', '.sql': 'text', '.graphql': 'text', '.gql': 'text', '.proto': 'text', '.avsc': 'json', '.prisma': 'text', '.tf': 'text', '.tfvars': 'text', '.hcl': 'text', '.nomad': 'text', '.csv': 'text', '.tsv': 'text', '.ndjson': 'text', '.jsonl': 'text', '.ipynb': 'json', '.org': 'text', '.tex': 'text', '.r': 'text', '.ps1': 'text', '.bat': 'text', '.patch': 'text', '.diff': 'text'}
DOC_FILENAMES: dict[str, str] = {'Dockerfile': 'text', 'Containerfile': 'text', 'Makefile': 'text', 'Justfile': 'text', 'Rakefile': 'ruby', 'Gemfile': 'ruby', 'Procfile': 'text', 'Vagrantfile': 'ruby', 'Jenkinsfile': 'text', 'CODEOWNERS': 'text', 'README': 'text', 'LICENSE': 'text', 'CHANGELOG': 'text', 'AUTHORS': 'text', '.gitignore': 'text', '.dockerignore': 'text', '.gitattributes': 'text', '.editorconfig': 'text', '.env.example': 'text'}
BINARY_EXTENSIONS: set[str] = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.webp', '.tiff', '.avif', '.psd', '.ai', '.eps', '.woff', '.woff2', '.ttf', '.otf', '.eot', '.mp3', '.mp4', '.wav', '.ogg', '.flac', '.avi', '.mov', '.webm', '.mkv', '.zip', '.tar', '.gz', '.bz2', '.xz', '.7z', '.rar', '.jar', '.war', '.pyc', '.pyo', '.pyd', '.so', '.dll', '.dylib', '.exe', '.bin', '.o', '.a', '.lib', '.obj', '.class', '.wasm', '.node', '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.db', '.sqlite', '.sqlite3', '.mdb', '.parquet', '.avro', '.orc', '.feather', '.npy', '.npz', '.pkl', '.pickle', '.h5', '.hdf5', '.onnx', '.pt', '.pth', '.ckpt', '.safetensors', '.bin'}
SKIPPED_FILENAMES: set[str] = {'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'poetry.lock', 'Cargo.lock', 'composer.lock', 'go.sum'}
SKIPPED_SUFFIXES: tuple[str, ...] = ('.min.js', '.min.css', '.lock', '.map')
SECRET_EXACT_NAMES: set[str] = {'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519', '.npmrc', '.pypirc', '.netrc', '.htpasswd', 'credentials', 'credentials.json', 'service-account.json'}
SECRET_SUFFIXES: tuple[str, ...] = ('.pem', '.key', '.pfx', '.p12', '.keystore', '.jks', '.ppk')
SECRET_TEMPLATE_NAMES: set[str] = {'.env.example', '.env.sample', '.env.template', '.env.dist'}
SECRET_DIRECTORY_NAMES: set[str] = {'secrets', 'secret', 'credentials', '.ssh', '.aws', '.gnupg', '.azure', '.kube', '.docker', 'private_keys', 'keys'}

def detect_doc_type(file_path: str | Path) -> Optional[str]:
    """
    Work out whether this is a documentation/config file we should index.

    Returns a doc type ("markdown", "yaml", ...) or None to skip the file.
    Checked in this order: skip list, then exact filename, then extension.
    """
    path = Path(file_path)
    if path.name in DOC_FILENAMES:
        return DOC_FILENAMES[path.name]
    return DOC_EXTENSION_TO_TYPE.get(path.suffix.lower())

def is_binary_extension(file_path: str | Path) -> bool:
    """Is this extension one we know holds binary data?"""
    return Path(file_path).suffix.lower() in BINARY_EXTENSIONS

def detect_language(file_path: str | Path) -> Optional[str]:
    """
    Work out which language a file is written in, from its extension.

    Returns the language name (e.g. "python"), or None if we do not support
    this file type. Returning None is normal and expected - a repo is full of
    .md, .json, .png and .lock files we simply skip.
    """
    suffix = Path(file_path).suffix.lower()
    return EXTENSION_TO_LANGUAGE.get(suffix)
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
        _PARSER_CACHE[language] = get_parser(language)
    return _PARSER_CACHE[language]
LANGUAGE_FAMILIES: dict[str, str] = {'python': 'python', 'javascript': 'js', 'typescript': 'js', 'tsx': 'js', 'go': 'go', 'rust': 'rust', 'java': 'jvm', 'csharp': 'csharp', 'ruby': 'ruby', 'php': 'php', 'kotlin': 'jvm', 'scala': 'jvm', 'swift': 'swift', 'c': 'c', 'cpp': 'c', 'bash': 'bash', 'lua': 'lua'}

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

def classify(path):
    if is_binary_extension(path):
        return (None, None)
    language = detect_language(path)
    return (language, None) if language else (None, detect_doc_type(path))

def is_indexable(path):
    return any(classify(path))
