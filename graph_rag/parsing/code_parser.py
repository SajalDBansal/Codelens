"""
parsing/code_parser.py
======================

The tree-sitter walker. This is the file that turns text into structure.

THE BIG PICTURE
---------------
tree-sitter reads source code and hands back a tree. For this Python code:

    class UserService:
        def login(self, name):
            return check(name)

the tree looks roughly like:

    module
      class_definition            name="UserService"
        block
          function_definition     name="login"
            block
              return_statement
                call              function="check"

Our job is to walk that tree and, whenever we land on a node whose type
means "function" or "class" (according to parsing/languages.py), record a
CodeEntity. While walking we also collect the `call` nodes so we can create
CALLS edges.

WHAT WE DELIBERATELY DO NOT DO
------------------------------
We do not try to fully resolve which function a call refers to. Doing that
properly means implementing each language's scoping and import rules, which
is a genuinely hard problem. Instead we record the call by NAME, and let
storage/ match names against everything already in the graph. We also count
how many calls we failed to resolve, so the limitation stays visible instead
of being quietly hidden.
"""
import re
from pathlib import Path
from typing import Optional
from ..models import CodeEntity, ParsedFile, Relationship
from .languages import LanguageConfig, detect_language, get_cached_parser, get_language_config
from .tree_utils import hash_text as _hash_text
from .tree_utils import inclusive_end_line
from .tree_utils import node_text as _text
_IDENTIFIER_RE = re.compile('[A-Za-z_][A-Za-z0-9_]*')
_HERITAGE_KEYWORDS = frozenset({'extends', 'implements', 'with', 'public', 'private', 'protected', 'virtual', 'abstract', 'override', 'open', 'final', 'sealed'})

def _split_qualified(raw_name: str) -> tuple[list[str], str]:
    """
    Split a name a grammar handed us fully qualified into (scope, bare name).

        "login"                 -> ([], "login")
        "AuthService::login"    -> (["AuthService"], "login")

    C++ defines methods out of line:

        bool AuthService::login(...) { ... }

    and the grammar reports the whole thing as the declarator name. Storing
    that verbatim gave the entity the NAME "AuthService::login", which no
    call to `login` could ever match - so every out-of-line C++ method was
    unreachable by name from the rest of the graph. We keep the qualifier as
    scope (so the id still says which class it belongs to) and let `name` be
    the bare method name, which is what call resolution compares against.
    """
    if '::' in raw_name:
        parts = [part for part in raw_name.split('::') if part]
        if len(parts) > 1:
            return (parts[:-1], parts[-1])
    return ([], raw_name)
_BODY_REQUIRED_TYPES = frozenset({'struct_specifier', 'union_specifier', 'enum_specifier', 'class_specifier'})

def _is_real_definition(node) -> bool:
    """Is this node a definition, or only a reference to one?"""
    if node.type not in _BODY_REQUIRED_TYPES:
        return True
    return node.child_by_field_name('body') is not None

def _node_name(node, source: bytes, config: LanguageConfig) -> Optional[str]:
    """
    Find the name of a function or class node.

    Most of the time the grammar gives us a "name" field and we are done.
    The interesting case is JavaScript/TypeScript arrow functions:

        const handleClick = () => { ... }

    The `arrow_function` node has NO name of its own - the name lives on the
    `variable_declarator` that wraps it. So when the direct lookup fails we
    look upwards at the parent for somewhere the name could be hiding.
    """
    if node.type in config.constructor_types:
        return '<init>'
    field_name = config.name_fields.get(node.type, config.name_field)
    name_node = node.child_by_field_name(field_name)
    if name_node is not None:
        return _text(name_node, source)
    parent = node.parent
    if parent is not None and parent.type in ('variable_declarator', 'pair', 'public_field_definition', 'assignment_expression'):
        for field_name in ('name', 'key', 'left'):
            candidate = parent.child_by_field_name(field_name)
            if candidate is not None:
                return _text(candidate, source)
    declarator = node.child_by_field_name('declarator')
    depth = 0
    while declarator is not None and depth < 5:
        if declarator.type in ('identifier', 'field_identifier', 'type_identifier', 'qualified_identifier', 'operator_name'):
            return _text(declarator, source)
        declarator = declarator.child_by_field_name('declarator')
        depth += 1
    for child in node.named_children:
        if child.type in ('identifier', 'simple_identifier', 'type_identifier', 'constant', 'word'):
            return _text(child, source)
    return None
_CALL_RECEIVER_FIELDS = ('object', 'receiver', 'instance', 'function')
_PARAMETER_FIELDS = ('parameters', 'parameter_list', 'formal_parameters')
_PARAM_NAME_FIELDS = ('name', 'pattern', 'declarator')
_IMPLICIT_RECEIVERS = frozenset({'self', 'this', 'cls', '_'})
_FLEXIBLE_ARITY_MARKERS = ('=', '*', '...', '?', '{', '[')
_RETURN_TYPE_FIELDS = ('return_type', 'result', 'type')
_PRIMITIVE_TYPE_NAMES = frozenset({'int', 'str', 'string', 'bool', 'boolean', 'float', 'double', 'long', 'short', 'byte', 'char', 'void', 'any', 'unknown', 'never', 'object', 'number', 'bigint', 'symbol', 'null', 'None', 'nil', 'undefined', 'list', 'dict', 'set', 'tuple', 'map', 'array', 'Array', 'List', 'Dict', 'Map', 'Set', 'Optional', 'Union', 'Promise', 'Awaitable', 'error', 'Error', 'Exception', 'String', 'Integer', 'Boolean', 'Object', 'T', 'self', 'Self', 'this', 'var', 'val', 'let', 'const', 'func', 'fn'})

def _extract_docstring(node, source: bytes, config: LanguageConfig) -> Optional[str]:
    """
    Pull out the documentation attached to a function or class.

    This matters more than it looks: the docstring usually says what the code
    is FOR, which the code itself often does not. Embedding it alongside the
    code measurably improves search quality.

    Two different conventions to handle:
      - Python: the docstring is the first statement INSIDE the body.
      - Everyone else: comments sit ABOVE the definition.
    """
    if config.name == 'python':
        body = node.child_by_field_name('body')
        if body is not None and body.named_child_count > 0:
            first = body.named_children[0]
            string_node = None
            if first.type == 'string':
                string_node = first
            elif first.type == 'expression_statement' and first.named_child_count > 0:
                candidate = first.named_children[0]
                if candidate.type == 'string':
                    string_node = candidate
            if string_node is not None:
                raw = _text(string_node, source)
                return raw.strip('"\'').strip()
    comments: list[str] = []
    sibling = node.prev_sibling
    while sibling is not None and sibling.type in config.comment_types:
        comments.insert(0, _text(sibling, source))
        sibling = sibling.prev_sibling
    if comments:
        return '\n'.join(comments).strip()
    return None

def split_entity_id(entity_id: str) -> tuple[str, str]:
    """
    "src/a.py::Outer.inner" -> ("src/a.py", "Outer.inner")

    The qualified half is the chain of definition names the entity sits
    inside, which is exactly what lexical scope is written in.
    """
    path, _, qualified = entity_id.partition('::')
    return (path, qualified)

def _parent_scope(qualified: str) -> str:
    """The qualified name of whatever directly encloses this one."""
    return qualified.rsplit('.', 1)[0] if '.' in qualified else ''

def prefer_by_scope(candidate_ids: list[str], source_id: str) -> str | None:
    """
    Pick the candidate that LEXICAL SCOPE identifies, or None if scope is
    silent on the question.

    WHY THIS RULE COMES FIRST
    -------------------------
    Resolution used to go language family -> import visibility -> unique
    match. Scope was not in the chain at all, even though it is both the
    cheapest signal available and the only one that is a FACT rather than an
    inference: containment is something the parser observed, not something it
    guessed from a name.

    The cost of leaving it out was concentrated and measurable. Scanning this
    project's own source, 8 of the 21 references lost to ambiguity were
    nested helpers named `walk` or `visit`. When `collect_folder_hashes`
    calls `walk`, the `walk` defined INSIDE `collect_folder_hashes` is
    unambiguously the one it means - but six functions across the repository
    define a local helper by that name, so the unique-match test failed and
    the edge was dropped. This affects modern code disproportionately:
    closures, nested helpers and inline callbacks are exactly the shapes that
    resolve worst without it.

    The rule, in order:

      1. A candidate the caller CONTAINS. `f` calling something defined
         inside `f` can only mean that one. Where several are nested at
         different depths we take the shallowest, and only if it is alone at
         that depth.

      2. A candidate that is the caller's SIBLING - it shares the caller's
         immediate enclosing scope. Two helpers inside the same function, or
         two methods of one class, refer to each other far more often than to
         a same-named definition somewhere else entirely.

    Both steps are strictly narrowing, so this can only ADD edges that were
    previously lost - it can never redirect an edge that already resolved.

    Returning `source_id` itself is meaningful and deliberate: it says the
    call is RECURSIVE. The caller decides what to do with that (we record it
    as a property rather than a self-edge, which keeps traversal simple).
    """
    if not candidate_ids:
        return None
    source_path, source_qualified = split_entity_id(source_id)
    prefix = f'{source_qualified}.' if source_qualified else ''
    contained = [c for c in candidate_ids if prefix and split_entity_id(c)[0] == source_path and split_entity_id(c)[1].startswith(prefix)]
    if contained:
        depth = min((split_entity_id(c)[1].count('.') for c in contained))
        shallowest = [c for c in contained if split_entity_id(c)[1].count('.') == depth]
        if len(shallowest) == 1:
            return shallowest[0]
    parent = _parent_scope(source_qualified)
    siblings = [c for c in candidate_ids if split_entity_id(c)[0] == source_path and _parent_scope(split_entity_id(c)[1]) == parent]
    if len(siblings) == 1:
        return siblings[0]
    return None

def _walk_tree(root, source: bytes, config: LanguageConfig, relative_path: str) -> tuple[list[CodeEntity], list[Relationship], list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]]]:
    """
    Walk the whole syntax tree once and collect everything we need.

    Returns four things:
      entities          the functions/classes we found
      relationships     the CONTAINS edges (which we can resolve immediately,
                        because both ends are in this same file)
      pending_calls     (caller_entity_id, called_name) - the target is only a
                        NAME so far, because the function being called may
                        live in a different file we have not read yet
      pending_extends   (class_entity_id, parent_class_name) - same idea
      pending_owners    (member_entity_id, owning_type_name) - for languages
                        that name the owning type instead of nesting inside
                        it (a Go receiver, a Rust `impl` block)
      pending_types     (entity_id, type_name) - a type named in a
                        signature or a class field, for a USES_TYPE edge
    """
    entities: list[CodeEntity] = []
    relationships: list[Relationship] = []
    pending_calls: list[tuple[str, str]] = []
    pending_extends: list[tuple[str, str]] = []
    pending_owners: list[tuple[str, str]] = []
    pending_types: list[tuple[str, str]] = []
    definition_types = set(config.function_types) | set(config.class_types)
    boundary_types = definition_types | set(config.constant_types)
    used_ids: set[str] = set()

    def unique_id(base: str, node_type: str) -> str:
        """Return `base`, or a stable variant of it if base is taken."""
        if base not in used_ids:
            used_ids.add(base)
            return base
        typed = f'{base}~{node_type}'
        if typed not in used_ids:
            used_ids.add(typed)
            return typed
        counter = 2
        while f'{typed}~{counter}' in used_ids:
            counter += 1
        final = f'{typed}~{counter}'
        used_ids.add(final)
        return final

    def visit(node, scope: list[str], parent_id: Optional[str], inside_class: bool, inside_function: bool, field_types: Optional[dict[str, str]]=None) -> None:
        """
        Look at one node.

        `scope`          the names of the definitions we are currently inside,
                         e.g. ["UserService"] while we are inside that class.
        `parent_id`      the entity id of the definition we are directly inside,
                         used to create the CONTAINS edge.
        `inside_class`   whether the thing we are directly inside is a CLASS.
                         This is what separates a method from a plain function
                         nested inside another function - both are "in a scope",
                         but only one of them is a method.
        `inside_function` whether we are anywhere inside a function body. Used
                         to keep local variables out of the graph: only
                         module-level and class-level constants are worth a
                         node of their own.
        """
        is_definition = node.type in definition_types and _is_real_definition(node)
        if is_definition:
            raw_name = _node_name(node, source, config)
            if raw_name is None:
                for child in node.children:
                    visit(child, scope, parent_id, inside_class, inside_function, field_types)
                return
            qualifier, name = _split_qualified(raw_name)
            owner_name = None
            owner_scope = [owner_name] if owner_name and (not scope) and (not qualifier) and (node.type not in config.impl_types) else []
            qualified_name = '.'.join(scope + qualifier + owner_scope + [name])
            entity_id = unique_id(f'{relative_path}::{qualified_name}', node.type)
            is_class = node.type in config.class_types
            if is_class:
                kind = 'impl' if node.type in config.impl_types else 'class'
            elif inside_class or qualifier or owner_scope:
                kind = 'method'
            else:
                kind = 'function'
            code = _text(node, source)
            own_fields = {} if is_class else {}
            type_env = dict(field_types or {})
            type_env.update({})
            enclosing_type = owner_name or (scope[-1] if inside_class and scope else None)
            if enclosing_type:
                for implicit in ('self', 'this'):
                    type_env.setdefault(implicit, enclosing_type)
            param_count, flexible_arity = (None, False)
            entities.append(CodeEntity(id=entity_id, kind=kind, name=name, file_path=relative_path, language=config.name, start_line=node.start_point[0] + 1, end_line=inclusive_end_line(node), code=code, docstring=_extract_docstring(node, source, config), content_hash=_hash_text(code), owner_name=owner_name, param_count=param_count, flexible_arity=flexible_arity))
            if parent_id is not None:
                relationships.append(Relationship(source_id=parent_id, target_id=entity_id, kind='CONTAINS'))
            makes_constants = is_class and (not inside_function)
            call_stop_types = boundary_types if makes_constants else definition_types
            owner_hints: dict[str, str] = {}
            conflicting: set[str] = set()
            arity_hints: dict[str, int] = {}
            conflicting_arity: set[str] = set()
            for name in conflicting:
                owner_hints.pop(name, None)
            for name in conflicting_arity:
                arity_hints.pop(name, None)
            entities[-1].call_owner_hints = owner_hints
            entities[-1].call_arity_hints = arity_hints
            field_type_names = {value for value in own_fields.values() if value not in _PRIMITIVE_TYPE_NAMES}
            if owner_name:
                pending_owners.append((entity_id, owner_name))
            for child in node.children:
                visit(child, scope + qualifier + [name], entity_id, inside_class=is_class, inside_function=inside_function or not is_class, field_types=own_fields if is_class else field_types)
        else:
            for child in node.children:
                visit(child, scope, parent_id, inside_class, inside_function, field_types)
    visit(root, scope=[], parent_id=None, inside_class=False, inside_function=False)
    return (entities, relationships, pending_calls, pending_extends, pending_owners, pending_types)

def parse_file(absolute_path: Path, relative_path: str) -> Optional[ParsedFile]:
    """
    Read one source file and return everything we learned from it.

    `absolute_path` is where the file actually is on disk.
    `relative_path` is how we name it in the graph (repo-root relative,
    always with forward slashes, so the same repo produces the same ids on
    Windows and on Linux).

    Returns None if this is not a file type we support - which is normal and
    happens constantly (README.md, package-lock.json, images, ...).
    """
    language = detect_language(absolute_path)
    if language is None:
        return None
    config = get_language_config(language)
    parser = get_cached_parser(language)
    source = absolute_path.read_bytes()
    tree = parser.parse(source)
    entities, relationships, pending_calls, pending_extends, pending_owners, pending_types = _walk_tree(tree.root_node, source, config, relative_path)
    types_by_name: dict[str, list[str]] = {}
    for entity in entities:
        if entity.kind == 'class':
            types_by_name.setdefault(entity.name, []).append(entity.id)
    owned_ids: set[str] = set()
    for member_id, owner_name in pending_owners:
        owners = [owner_id for owner_id in types_by_name.get(owner_name, []) if owner_id != member_id]
        if len(owners) == 1:
            relationships.append(Relationship(source_id=owners[0], target_id=member_id, kind='CONTAINS'))
            owned_ids.add(member_id)
    module_entity = None
    name_to_ids: dict[str, list[str]] = {}
    for entity in entities:
        if entity.kind in ('constant', 'module'):
            continue
        name_to_ids.setdefault(entity.name, []).append(entity.id)
    unresolved: list[tuple[str, str, str]] = []
    recursive_ids: set[str] = set()

    def _resolve_in_file(source_id: str, wanted: str) -> str | None:
        """One match in this file, or the one lexical scope points at."""
        candidates = name_to_ids.get(wanted, [])
        if len(candidates) == 1:
            return candidates[0]
        return prefer_by_scope(candidates, source_id)
    types_only = {name: [e.id for e in entities if e.kind == 'class' and e.name == name] for _, name in pending_types}
    seen_type_refs: set[tuple[str, str]] = set()
    for source_id, type_name in pending_types:
        if (source_id, type_name) in seen_type_refs:
            continue
        seen_type_refs.add((source_id, type_name))
        matches = [i for i in types_only.get(type_name, []) if i != source_id]
        if len(matches) == 1:
            relationships.append(Relationship(source_id=source_id, target_id=matches[0], kind='USES_TYPE'))
        elif not matches:
            unresolved.append((source_id, type_name, 'USES_TYPE'))
    by_kind: dict[str, dict[str, list[str]]] = {'CALLS': {}, 'EXTENDS': {}, 'USES_TYPE': {}}
    for source_id, name, kind in unresolved:
        by_kind[kind].setdefault(source_id, []).append(name)
    for entity in entities:
        entity.unresolved_calls = by_kind['CALLS'].get(entity.id, [])
        entity.unresolved_extends = by_kind['EXTENDS'].get(entity.id, [])
        entity.unresolved_types = by_kind['USES_TYPE'].get(entity.id, [])
        entity.is_recursive = entity.id in recursive_ids
    interface_refs: list[tuple[str, str, str]] = []
    parsed = ParsedFile(file_path=relative_path, language=language, content_hash=_hash_text(source.decode('utf-8', errors='replace')), entities=entities, relationships=relationships, imports=[], unresolved_references=unresolved, interface_refs=interface_refs)
    return parsed
