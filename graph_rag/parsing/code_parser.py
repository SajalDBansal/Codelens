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
from .interfaces import extract_interface_tokens
from .tree_utils import hash_text as _hash_text
from .tree_utils import inclusive_end_line
from .tree_utils import node_text as _text


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

# Matches a normal identifier: a letter or underscore, then letters/digits/_.
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Words that appear inside a heritage clause but are not class names.
_HERITAGE_KEYWORDS = frozenset({
    "extends", "implements", "with", "public", "private", "protected",
    "virtual", "abstract", "override", "open", "final", "sealed",
})


def _last_identifier(text: str) -> Optional[str]:
    """
    Reduce a callee expression down to the bare name being called.

        "check"                 -> "check"
        "self.db.save"          -> "save"
        "std::fs::read_to_string" -> "read_to_string"
        "obj.items[0].run"      -> "run"

    We take the LAST identifier because that is the actual method/function
    name; everything before it is the path used to reach it. This is a
    simplification: two different classes can both have a `save` method, and
    this cannot tell them apart. That ambiguity is handled later, in
    storage/, by refusing to guess when a name is not unique.
    """
    matches = _IDENTIFIER_RE.findall(text)
    return matches[-1] if matches else None


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
    if "::" in raw_name:
        parts = [part for part in raw_name.split("::") if part]
        if len(parts) > 1:
            return (parts[:-1], parts[-1])
    return ([], raw_name)


def _constant_name(node, source: bytes) -> Optional[str]:
    """
    The name of a module-level constant or variable declaration.

    Grammars disagree about where it lives, so we try, in order: a "name"
    field, a "left" field (assignments), a `declarator` chain (Java/C#
    fields), and finally the first identifier-ish descendant (Kotlin and
    Scala expose no field at all).
    """
    for field_name in ("name", "left"):
        candidate = node.child_by_field_name(field_name)
        if candidate is not None:
            text = _text(candidate, source).strip()
            # `a, b = 1, 2` and `int x = 1, y = 2` produce a list, not a name.
            if text and "," not in text:
                names = _IDENTIFIER_RE.findall(text)
                if names:
                    return names[-1] if len(names) == 1 else names[0]

    declarator = node.child_by_field_name("declarator")
    depth = 0
    while declarator is not None and depth < 5:
        if declarator.type in ("identifier", "field_identifier", "variable_declarator"):
            names = _IDENTIFIER_RE.findall(_text(declarator, source))
            if names:
                return names[0]
        declarator = declarator.child_by_field_name("declarator")
        depth += 1

    # Kotlin wraps the name one level down in a `variable_declaration`.
    def first_identifier(current, depth_left: int) -> Optional[str]:
        if depth_left < 0:
            return None

        # A `variable_declarator` always wins over a bare identifier sibling.
        #
        # C# writes a field as `private Repository repo;`, which parses as
        # field_declaration -> variable_declaration -> [identifier "Repository",
        # variable_declarator "repo"]. Taking the first identifier-ish child
        # therefore named the field after its TYPE, so `private Repository repo`
        # became a constant called "Repository" that collided with the real
        # class of that name.
        for child in current.named_children:
            if child.type == "variable_declarator":
                found = first_identifier(child, depth_left)
                if found:
                    return found

        for child in current.named_children:
            if child.type in ("identifier", "simple_identifier", "constant",
                              "variable_name", "name", "field_identifier"):
                text = _text(child, source).strip().lstrip("$")
                if text:
                    return text
            if child.type in ("variable_declaration", "variable_list",
                              "init_declarator", "value_binding_pattern", "pattern"):
                found = first_identifier(child, depth_left - 1)
                if found:
                    return found
        return None

    return first_identifier(node, 3)


def _has_required_modifiers(node, source: bytes, config: LanguageConfig) -> bool:
    """
    Does this declaration carry every modifier its language needs to count
    as a constant?

    Java and C# use ONE node type for both a class constant and an ordinary
    instance field, so without this check `private final OrderRepository repo;`
    became a "constant" named `repo`. That is wrong twice over: it is state,
    not a constant, and the name it adds collides with every other `repo` in
    the graph, making real references to any of them ambiguous.

    We look only at the declaration head - the text before the first `=` -
    so a keyword appearing inside an initialiser string cannot be mistaken
    for a modifier.
    """
    if not config.constant_required_modifiers:
        return True

    head = _text(node, source).split("=", 1)[0]
    words = set(_IDENTIFIER_RE.findall(head))
    return all(modifier in words for modifier in config.constant_required_modifiers)


def _is_function_valued(node) -> bool:
    """
    Is this declaration really a function in disguise?

        const handleClick = () => { ... }

    is a `variable_declarator`, which is also our JS/TS constant node type.
    It already becomes a function entity via the arrow_function inside it,
    so emitting a constant for it as well would duplicate every arrow
    function in the codebase.
    """
    value = node.child_by_field_name("value")
    if value is None:
        return False
    return value.type in (
        "arrow_function", "function_expression", "function",
        "generator_function", "class",
    )


# In C and C++ the SAME node type is used to declare a type and to refer to
# one. `struct Repository *repo` in a parameter list is a struct_specifier
# just as `struct Repository { int id; }` is. Only the second is a
# definition; treating both as one produced a phantom duplicate entity for
# every place a struct was merely mentioned.
_BODY_REQUIRED_TYPES = frozenset({
    "struct_specifier", "union_specifier", "enum_specifier", "class_specifier",
})


def _is_real_definition(node) -> bool:
    """Is this node a definition, or only a reference to one?"""
    if node.type not in _BODY_REQUIRED_TYPES:
        return True
    return node.child_by_field_name("body") is not None


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
    # 0. A constructor is named after its class, which would put two entities
    #    of the same name in one file and make every `new Thing(...)` call
    #    ambiguous forever. Give it a name of its own instead.
    if node.type in config.constructor_types:
        return "<init>"

    # 1. The normal case: the node has a name field. Some node types name
    #    themselves through a different field - see LanguageConfig.name_fields
    #    for why a Rust `impl` block must be named after its type.
    field_name = config.name_fields.get(node.type, config.name_field)
    name_node = node.child_by_field_name(field_name)
    if name_node is not None:
        return _text(name_node, source)

    # 2. Anonymous function assigned to a variable or object property.
    parent = node.parent
    if parent is not None and parent.type in (
        "variable_declarator",      # const foo = () => {}
        "pair",                     # { foo: () => {} }
        "public_field_definition",  # class { foo = () => {} }
        "assignment_expression",    # foo = () => {}
    ):
        # `variable_declarator` uses "name"; `pair` uses "key";
        # `assignment_expression` uses "left". Try each in turn.
        for field_name in ("name", "key", "left"):
            candidate = parent.child_by_field_name(field_name)
            if candidate is not None:
                return _text(candidate, source)

    # 3. Names hidden under a `declarator` chain (C and C++).
    #
    #        int go(int x) { ... }
    #
    #    is a function_definition whose `declarator` is a function_declarator
    #    whose `declarator` is finally the identifier `go`. Follow the chain.
    declarator = node.child_by_field_name("declarator")
    depth = 0
    while declarator is not None and depth < 5:
        if declarator.type in ("identifier", "field_identifier", "type_identifier",
                               "qualified_identifier", "operator_name"):
            return _text(declarator, source)
        declarator = declarator.child_by_field_name("declarator")
        depth += 1

    # 4. Grammars that expose no name field at all (Kotlin). Take the first
    #    identifier-ish child, which is the declared name in practice.
    for child in node.named_children:
        if child.type in ("identifier", "simple_identifier", "type_identifier",
                          "constant", "word"):
            return _text(child, source)

    # 5. Truly anonymous (e.g. a callback passed inline). We return None and
    #    the caller skips creating an entity for it - an unnamed one-line
    #    callback is rarely worth its own node in the graph.
    return None


def _owner_type_name(node, source: bytes, config: LanguageConfig) -> Optional[str]:
    """
    The type this definition belongs to, when the language names it rather
    than nesting it.

        Go     func (e *Engine) Apply(...)   -> "Engine"
        Rust   impl LineItem { ... }         -> "LineItem"
        Rust   impl Priced for LineItem      -> "LineItem"

    Returns None for languages and node types that express membership by
    nesting, which is every other case in this file.
    """
    # Rust: the impl block is already NAMED after its type (see
    # LanguageConfig.name_fields), so the type name is its own name.
    if node.type in config.impl_types:
        return _node_name(node, source, config)

    if not config.receiver_field:
        return None

    receiver = node.child_by_field_name(config.receiver_field)
    if receiver is None:
        return None

    # A Go receiver is written `(e *Engine)` or `(e Engine)`. The last
    # identifier is the type in both spellings, and pointer stars, generics
    # and whitespace all fall away with it.
    return _last_identifier(_text(receiver, source))


def _receiver_identifier(text: str) -> Optional[str]:
    """
    The thing a call was made ON, as opposed to the thing being called.

        "login"            -> None          (a plain function call)
        "auth.login"       -> "auth"
        "self.db.save"     -> "db"
        "TaxCalculator.applyTax" -> "TaxCalculator"

    `_last_identifier` deliberately throws this away, because the NAME is
    what has to be matched against the graph. But the receiver is the piece
    that says WHICH of several same-named definitions was meant, and dropping
    it is why a subclass overriding a parent method makes both unlinkable.
    """
    matches = _IDENTIFIER_RE.findall(text)
    return matches[-2] if len(matches) >= 2 else None


# Fields on a CALL node that hold the object the call was made on, for the
# grammars that keep it apart from the callee instead of inside it.
#
# Python and JavaScript write the whole thing in one expression -
# `auth.login` - so the receiver falls out of the text. Java does not:
# `method_invocation` fields the callee as `name` ("login") and the object
# separately as `object` ("auth"), so reading the callee alone finds no
# receiver at all and Java produced no hints whatsoever. PHP is the same
# shape again, under a different field name.
_CALL_RECEIVER_FIELDS = ("object", "receiver", "instance", "function")


def _call_receiver(
    call_node, callee_text: str, called_name: str, source: bytes
) -> Optional[str]:
    """
    The object a call was made on, however the grammar chose to express it.

    Tries the callee expression first, since that is where most languages put
    it, then the call node's own receiver-ish fields. A field that just gives
    the called name back is rejected - that means the grammar fields the
    callee there and there is no receiver to find.
    """
    receiver = _receiver_identifier(callee_text)
    if receiver is not None:
        return receiver

    for field in _CALL_RECEIVER_FIELDS:
        node = call_node.child_by_field_name(field)
        if node is None:
            continue
        candidate = _last_identifier(_text(node, source))
        if candidate and candidate != called_name:
            return candidate

    return None


# The parameter-list container, as each grammar spells it.
_PARAMETER_FIELDS = ("parameters", "parameter_list", "formal_parameters")

# Fields that hold a parameter's own name. Rust and TypeScript bind a
# PATTERN rather than a plain name, which is the same thing for the simple
# case we can use.
_PARAM_NAME_FIELDS = ("name", "pattern", "declarator")

# The receiver's own name, which is never a real parameter to a caller.
_IMPLICIT_RECEIVERS = frozenset({"self", "this", "cls", "_"})

# Anything in a signature that makes the declared parameter count stop being
# the number of arguments a caller must pass: defaults, varargs, optional
# markers, destructuring.
_FLEXIBLE_ARITY_MARKERS = ("=", "*", "...", "?", "{", "[")


def _parameter_container(node):
    """The node holding this definition's parameter list, if it has one."""
    for field in _PARAMETER_FIELDS:
        container = node.child_by_field_name(field)
        if container is not None:
            return container
    return None


def _type_environment(node, source: bytes, config: LanguageConfig) -> dict[str, str]:
    """
    {parameter name: declared type name} for one definition.

        def handle_login(request, auth: AuthService)  -> {"auth": "AuthService"}
        public Invoice make(TaxCalculator calc)       -> {"calc": "TaxCalculator"}
        func run(c *Consumer)                         -> {"c": "Consumer"}

    WHY THIS IS WORTH THE TROUBLE
    -----------------------------
    It is the evidence that settles the commonest ambiguity in
    object-oriented code. When `AuthService.login` and `AdminAuthService.login`
    both answer to the name `login`, the old rule refused to link either and
    the whole auth module dropped out of the graph. The calling code was never
    ambiguous about it - it says `auth: AuthService` right there in the
    signature. This reads that.

    Written against tree-sitter FIELDS rather than per-language node types,
    with positional fallbacks, so it works on every grammar in the registry
    without eighteen special cases. Where it cannot work out a type it simply
    returns nothing for that parameter, and resolution falls back to the
    strict rule - so a grammar this does not understand loses nothing.
    """
    environment: dict[str, str] = {}

    containers = []
    parameters = _parameter_container(node)
    if parameters is not None:
        containers.append(parameters)

    # Go and Rust bind the receiver OUTSIDE the parameter list:
    # `func (c *Consumer) Handle(...)`. It is spelled exactly like a
    # parameter, so it parses the same way - and without it, `c.fulfil()`
    # inside that method has no type for `c` and gains no hint at all.
    if config.receiver_field:
        receiver = node.child_by_field_name(config.receiver_field)
        if receiver is not None:
            containers.append(receiver)

    for container in containers:
        environment.update(_bindings_in(container, source))

    return environment


def _bindings_in_node(declaration, source: bytes) -> dict[str, str]:
    """
    {name: type} read off ONE declaration - a parameter or a class field.

    The two are the same shape in every grammar here: something that fields
    its type as "type" and its name as "name", "pattern" or "declarator".
    Returning a dict rather than a pair keeps the "found nothing" case free
    of None handling at both call sites.
    """
    # The declared type. Reduced to its last identifier so that `*Engine`,
    # `&mut Engine`, `Optional[Engine]` and `: Engine` all give "Engine".
    type_node = declaration.child_by_field_name("type")
    if type_node is None:
        return {}
    type_name = _last_identifier(_text(type_node, source))
    if not type_name:
        return {}

    # The declaration's own name.
    name_node = None
    for field in _PARAM_NAME_FIELDS:
        name_node = declaration.child_by_field_name(field)
        if name_node is not None:
            break
    if name_node is not None:
        declared_name = _last_identifier(_text(name_node, source))
    else:
        # Grammars that do not field the name (Python's typed_parameter)
        # put it first, before the type.
        identifiers = _IDENTIFIER_RE.findall(_text(declaration, source))
        declared_name = identifiers[0] if identifiers else None

    if declared_name and declared_name != type_name:
        return {declared_name: type_name}
    return {}


def _bindings_in(container, source: bytes) -> dict[str, str]:
    """{name: type} for every parameter-shaped child of `container`."""
    environment: dict[str, str] = {}
    for parameter in container.named_children:
        environment.update(_bindings_in_node(parameter, source))
    return environment


# Fields holding a definition's RETURN type, as each grammar spells it.
#
# Java is the awkward one: `method_declaration` fields its return type as
# plain "type", which is the same field name a PARAMETER uses for its own
# type - so this list may only ever be applied to a definition node, never
# to something inside one.
_RETURN_TYPE_FIELDS = ("return_type", "result", "type")

# Type names so common they say nothing about structure. They are almost
# never entities in the graph anyway, so this is mostly about not carrying
# thousands of references that can never resolve.
_PRIMITIVE_TYPE_NAMES = frozenset({
    "int", "str", "string", "bool", "boolean", "float", "double", "long",
    "short", "byte", "char", "void", "any", "unknown", "never", "object",
    "number", "bigint", "symbol", "null", "None", "nil", "undefined",
    "list", "dict", "set", "tuple", "map", "array", "Array", "List", "Dict",
    "Map", "Set", "Optional", "Union", "Promise", "Awaitable", "error",
    "Error", "Exception", "String", "Integer", "Boolean", "Object", "T",
    "self", "Self", "this", "var", "val", "let", "const", "func", "fn",
})


def _type_names_in(node, source: bytes) -> set[str]:
    """
    Every identifier inside a TYPE expression, primitives dropped.

    All of them rather than just the last, because a type is often a
    container: `list[LineItem]` genuinely references LineItem, and
    `Dict[str, Order]` references Order. Taking only the final identifier
    would work for those two by luck and fail on `Result<Order, ApiError>`,
    which references both.

    Anything that is not a type in the graph simply never resolves and is
    dropped later, so casting the net wide here costs nothing.
    """
    if node is None:
        return set()
    return {
        name
        for name in _IDENTIFIER_RE.findall(_text(node, source))
        if name not in _PRIMITIVE_TYPE_NAMES
    }


def _signature_type_names(node, source: bytes, config: LanguageConfig) -> set[str]:
    """
    The types named in one definition's signature - its parameters and its
    return type.

    WHY THESE DESERVE AN EDGE OF THEIR OWN
    --------------------------------------
    The graph had CALLS, CONTAINS and EXTENDS, and nothing else - so a type
    was only ever connected to the code using it if that code happened to
    CALL it. Measured on the demo repository, the TypeScript `Order` and
    `Session` interfaces had degree 1: their file's CONTAINS edge and nothing
    more, despite `Order` being the declared parameter type of `checkout` and
    of the `Cart` component that renders it.

    That is a real retrieval hole. Asked what `checkout` accepts or returns,
    retrieval could find the function and had no way to reach the shape of
    the data it works on - so the model either omitted it or guessed at the
    fields.
    """
    found: set[str] = set()

    container = _parameter_container(node)
    if container is not None:
        for parameter in container.named_children:
            found |= _type_names_in(parameter.child_by_field_name("type"), source)

    for field in _RETURN_TYPE_FIELDS:
        return_type = node.child_by_field_name(field)
        if return_type is not None:
            found |= _type_names_in(return_type, source)
            break

    return found


def _field_bindings(node, source: bytes, config: LanguageConfig) -> dict[str, str]:
    """
    {field name: declared type} for a class's own fields.

    THE GAP THIS CLOSES
    -------------------
    The type environment reads parameters, receivers and `self`. It did not
    read FIELDS, and a field is how object-oriented code most often holds a
    collaborator:

        public class InvoiceService {
            private final TaxCalculator taxCalculator;
            ...
            taxCalculator.applyTax(subtotal)      <- receiver is a field
        }

    Without this the receiver `taxCalculator` had no type, so the call got no
    hint - which is exactly why the Java service produced fewer hints than
    the Python one on the demo repository.

    Definitions are skipped explicitly. A method node also has a "type" field
    - its RETURN type - so walking the body without filtering would bind
    every method's name to whatever it returns.
    """
    body = node.child_by_field_name("body") or node
    definition_types = set(config.function_types) | set(config.class_types) | set(
        config.constructor_types
    )

    fields: dict[str, str] = {}
    for child in body.named_children:
        if child.type in definition_types:
            continue
        fields.update(_bindings_in_node(child, source))
    return fields


def _arity(node, source: bytes) -> tuple[Optional[int], bool]:
    """
    (declared parameter count, whether that count is flexible).

    The count excludes `self` / `this`, so it is what a CALLER passes rather
    than what the definition writes down.

    THE SECOND VALUE IS THE IMPORTANT ONE
    -------------------------------------
    Arity is the most dangerous of the three disambiguation signals, because
    a signature's declared count is often not the number of arguments a legal
    call carries: default values, varargs, optional parameters and
    destructuring all break the equality. Using it naively does not merely
    fail to help - it can eliminate the RIGHT candidate and leave exactly one
    wrong one, which turns a missing edge into a confident wrong edge. That
    is the one outcome this project's rules exist to prevent.

    So the flexible flag is detected with a deliberately broad, cheap text
    check, and any signature that trips it opts out of arity matching
    entirely. The check erring towards "flexible" is the safe direction: the
    worst case is that arity contributes nothing.
    """
    container = _parameter_container(node)
    if container is None:
        return (None, False)

    text = _text(container, source)
    flexible = any(marker in text for marker in _FLEXIBLE_ARITY_MARKERS)

    count = 0
    for parameter in container.named_children:
        identifiers = _IDENTIFIER_RE.findall(_text(parameter, source))
        if identifiers and identifiers[0] in _IMPLICIT_RECEIVERS:
            continue
        count += 1

    return (count, flexible)


def _impl_trait_name(node, source: bytes, config: LanguageConfig) -> Optional[str]:
    """
    The trait an `impl` block implements, if any.

    `impl Priced for LineItem` says LineItem implements Priced. That is an
    inheritance edge, and dropping it left every Rust trait as an isolated
    node with nothing pointing at it.
    """
    if not config.impl_trait_field or node.type not in config.impl_types:
        return None
    trait = node.child_by_field_name(config.impl_trait_field)
    if trait is None:
        return None
    return _last_identifier(_text(trait, source))


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
    # --- Python style: first string statement inside the body -------------
    if config.name == "python":
        body = node.child_by_field_name("body")
        if body is not None and body.named_child_count > 0:
            first = body.named_children[0]

            # Two shapes are possible depending on the grammar version: the
            # docstring can be a bare `string` node, or a `string` wrapped in
            # an `expression_statement`. We accept both rather than betting on
            # one - guessing wrong here silently loses every docstring, which
            # is the single most useful text we embed.
            string_node = None
            if first.type == "string":
                string_node = first
            elif first.type == "expression_statement" and first.named_child_count > 0:
                candidate = first.named_children[0]
                if candidate.type == "string":
                    string_node = candidate

            if string_node is not None:
                raw = _text(string_node, source)
                # Strip the surrounding quotes (""" or ''' or " or ').
                return raw.strip("\"'").strip()

    # --- Everyone else: contiguous comment lines directly above -----------
    comments: list[str] = []
    sibling = node.prev_sibling
    while sibling is not None and sibling.type in config.comment_types:
        # We walk upwards, so we insert at the front to keep reading order.
        comments.insert(0, _text(sibling, source))
        sibling = sibling.prev_sibling

    if comments:
        return "\n".join(comments).strip()

    return None


# ---------------------------------------------------------------------------
# Lexical scope: the cheapest and strongest disambiguation rule we have
# ---------------------------------------------------------------------------

def split_entity_id(entity_id: str) -> tuple[str, str]:
    """
    "src/a.py::Outer.inner" -> ("src/a.py", "Outer.inner")

    The qualified half is the chain of definition names the entity sits
    inside, which is exactly what lexical scope is written in.
    """
    path, _, qualified = entity_id.partition("::")
    return path, qualified


def _parent_scope(qualified: str) -> str:
    """The qualified name of whatever directly encloses this one."""
    return qualified.rsplit(".", 1)[0] if "." in qualified else ""


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

    # --- 1. contained by the caller -----------------------------------
    prefix = f"{source_qualified}." if source_qualified else ""
    contained = [
        c for c in candidate_ids
        if prefix
        and split_entity_id(c)[0] == source_path
        and split_entity_id(c)[1].startswith(prefix)
    ]
    if contained:
        depth = min(split_entity_id(c)[1].count(".") for c in contained)
        shallowest = [c for c in contained if split_entity_id(c)[1].count(".") == depth]
        if len(shallowest) == 1:
            return shallowest[0]

    # --- 2. a sibling in the caller's own scope ------------------------
    parent = _parent_scope(source_qualified)
    siblings = [
        c for c in candidate_ids
        if split_entity_id(c)[0] == source_path
        and _parent_scope(split_entity_id(c)[1]) == parent
    ]
    if len(siblings) == 1:
        return siblings[0]

    return None


# ---------------------------------------------------------------------------
# Call collection
# ---------------------------------------------------------------------------

def _collect_call_sites(
    node, source: bytes, config: LanguageConfig, stop_types: set[str]
) -> list[tuple[str, Optional[str], Optional[int]]]:
    """
    Find every function call inside `node` as (name, receiver), but STOP at
    nested definitions.

    The receiver is the expression the call was made ON - `auth` in
    `auth.login()` - and it is None for a plain function call. It is what
    later tells `AuthService.login` apart from `AdminAuthService.login`.

    Why stop? Consider:

        def outer():
            helper_a()
            def inner():
                helper_b()

    `helper_b` is called by `inner`, not by `outer`. If we walked the whole
    subtree blindly we would wrongly say outer calls helper_b. So when we
    meet a nested function/class we do not descend into it - that node will
    be visited separately as its own entity and collect its own calls.
    """
    found: list[tuple[str, Optional[str], Optional[int]]] = []

    def walk(current, is_root: bool) -> None:
        # Stop at a nested definition (but not at the entity we started from).
        if not is_root and current.type in stop_types:
            return

        if current.type in config.call_types:
            # Get the part of the call that names what is being called.
            # Some languages use a different field per call node type - see
            # LanguageConfig.call_function_fields for why PHP needs this.
            field_name = config.call_function_fields.get(
                current.type, config.call_function_field
            )
            callee = current.child_by_field_name(field_name)
            if callee is None and current.named_child_count > 0:
                # Fallback for node shapes that do not use the expected field
                # (e.g. Rust's macro_invocation uses a "macro" field).
                callee = current.named_children[0]
            if callee is not None:
                callee_text = _text(callee, source)
                name = _last_identifier(callee_text)
                if name:
                    # How many arguments this particular call passes. Every
                    # grammar in the registry fields the argument list under
                    # "arguments"; where it does not, None means "unknown"
                    # and arity simply never contributes.
                    arguments = current.child_by_field_name("arguments")
                    argument_count = (
                        arguments.named_child_count if arguments is not None else None
                    )
                    found.append(
                        (
                            name,
                            _call_receiver(current, callee_text, name, source),
                            argument_count,
                        )
                    )

        for child in current.children:
            walk(child, is_root=False)

    walk(node, is_root=True)
    return found


def _collect_call_names(
    node, source: bytes, config: LanguageConfig, stop_types: set[str]
) -> list[str]:
    """Just the names, for the callers that do not care who they were called on."""
    return [name for name, _, _ in _collect_call_sites(node, source, config, stop_types)]


def _collect_parent_class_names(node, source: bytes, config: LanguageConfig) -> list[str]:
    """
    Find the classes a class inherits from, for EXTENDS edges.

    Python:  class Admin(User):          -> field "superclasses"
    Java:    class Admin extends User    -> field "superclass"
    TS:      class Admin extends User    -> a `class_heritage` child node

    Everything found is reduced to bare identifiers, so `List[User]` in a
    generic base class yields both "List" and "User"; the unresolvable ones
    simply never match anything in the graph and are dropped later.
    """
    names: list[str] = []
    seen: set[str] = set()

    def add_from(heritage) -> None:
        """
        Collect the type names inside a heritage node.

        We read the node's NAMED CHILDREN rather than running a regex over
        its text. Java's `superclass` node covers the words "extends B", so
        a regex over the text produced the keyword `extends` as though it
        were a parent class - a name that matches nothing, counted as a real
        missed edge in the report, and duplicated because the same node was
        visited by both branches below.
        """
        candidates = [
            child for child in heritage.named_children
            if child.type not in ("comment",)
        ]
        # A heritage node with no named children (some grammars keep it flat)
        # still has to be read as text, minus the keywords.
        if not candidates:
            for word in _IDENTIFIER_RE.findall(_text(heritage, source)):
                if word not in _HERITAGE_KEYWORDS and word not in seen:
                    seen.add(word)
                    names.append(word)
            return

        for child in candidates:
            for word in _IDENTIFIER_RE.findall(_text(child, source)):
                if word not in _HERITAGE_KEYWORDS and word not in seen:
                    seen.add(word)
                    names.append(word)

    visited: set[int] = set()

    # Grammars that expose a dedicated field.
    if config.extends_field:
        heritage = node.child_by_field_name(config.extends_field)
        if heritage is not None:
            visited.add(heritage.id)
            add_from(heritage)

    # TypeScript / JavaScript put it in a child node instead of a field.
    # `visited` stops a node that is BOTH the field and a matching child
    # (Java's `superclass`) from being counted twice.
    for child in node.children:
        if child.type in ("class_heritage", "extends_clause", "superclass",
                          "super_interfaces", "base_list") and child.id not in visited:
            visited.add(child.id)
            add_from(child)

    return names


# ---------------------------------------------------------------------------
# The main walk
# ---------------------------------------------------------------------------

def _walk_tree(
    root,
    source: bytes,
    config: LanguageConfig,
    relative_path: str,
) -> tuple[
    list[CodeEntity],
    list[Relationship],
    list[tuple[str, str]],
    list[tuple[str, str]],
    list[tuple[str, str]],
    list[tuple[str, str]],
]:
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
    # (member_entity_id, owning_type_name) for Go receivers and Rust impls.
    pending_owners: list[tuple[str, str]] = []
    # (entity_id, type_name) - a type named in a definition's signature or
    # among a class's fields. Resolved to a USES_TYPE edge later, by the same
    # machinery and the same conservative rules as a call.
    pending_types: list[tuple[str, str]] = []

    # Every node type that starts a new definition. Used to know when to stop
    # collecting calls (see _collect_call_names).
    definition_types = set(config.function_types) | set(config.class_types)

    # A constant's initialiser can contain calls of its own
    # (`service = AuthService(repo)`), and those belong to the constant, not
    # to the enclosing module. Stopping at them keeps the two from
    # double-counting the same call.
    boundary_types = definition_types | set(config.constant_types)

    # Ids must be unique WITHIN a file, because the graph MERGEs on id and a
    # collision silently overwrites the earlier node.
    #
    # This is not hypothetical. In Rust both `struct AuthService` and
    # `impl AuthService` are type-like nodes named AuthService, so both
    # produced the id "lib.rs::AuthService" and the impl block overwrote the
    # struct definition - the struct's fields vanished from the graph with no
    # error anywhere. Same shape in C, where a struct can be declared and
    # defined in one file.
    used_ids: set[str] = set()

    def unique_id(base: str, node_type: str) -> str:
        """Return `base`, or a stable variant of it if base is taken."""
        if base not in used_ids:
            used_ids.add(base)
            return base
        # Qualify by node type first: "lib.rs::AuthService~impl_item" says
        # something real about which of the two nodes this is.
        typed = f"{base}~{node_type}"
        if typed not in used_ids:
            used_ids.add(typed)
            return typed
        counter = 2
        while f"{typed}~{counter}" in used_ids:
            counter += 1
        final = f"{typed}~{counter}"
        used_ids.add(final)
        return final

    def visit(
        node,
        scope: list[str],
        parent_id: Optional[str],
        inside_class: bool,
        inside_function: bool,
        # {field name: type} declared by the class we are inside, so a method
        # body can tell what `self.repo` or `this.taxCalculator` IS. Passed
        # down rather than looked up, because by the time we are inside the
        # method the class node is no longer in hand.
        field_types: Optional[dict[str, str]] = None,
    ) -> None:
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

        # ---- a named module-level or class-level constant ----------------
        if not is_definition and node.type in config.constant_types:
            if (
                not inside_function
                and not _is_function_valued(node)
                and _has_required_modifiers(node, source, config)
            ):
                const_name = _constant_name(node, source)
                if const_name:
                    const_id = unique_id(
                        f"{relative_path}::{'.'.join(scope + [const_name])}", node.type
                    )
                    code = _text(node, source)
                    entities.append(
                        CodeEntity(
                            id=const_id,
                            kind="constant",
                            name=const_name,
                            file_path=relative_path,
                            language=config.name,
                            start_line=node.start_point[0] + 1,
                            end_line=inclusive_end_line(node),
                            code=code,
                            docstring=_extract_docstring(node, source, config),
                            content_hash=_hash_text(code),
                        )
                    )
                    if parent_id is not None:
                        relationships.append(
                            Relationship(
                                source_id=parent_id, target_id=const_id, kind="CONTAINS"
                            )
                        )
                    for called_name in _collect_call_names(
                        node, source, config, boundary_types
                    ):
                        pending_calls.append((const_id, called_name))
                    # A constant holds no definitions worth descending into.
                    return

        if is_definition:
            raw_name = _node_name(node, source, config)

            if raw_name is None:
                # Anonymous function (an inline callback, say). We do not give
                # it a node of its own, but we still walk INTO it so that any
                # named function defined inside is not lost.
                for child in node.children:
                    visit(
                        child, scope, parent_id, inside_class, inside_function,
                        field_types,
                    )
                return

            # C++ defines methods out of line as `AuthService::login`. Keep
            # the qualifier as scope so the id still records the class, and
            # let `name` be the bare name that call resolution matches on.
            qualifier, name = _split_qualified(raw_name)

            # Languages that name their owning type instead of nesting inside
            # it: a Go receiver, a Rust `impl`. Treated as scope so the id
            # reads "pricing.go::Engine.Apply" rather than "pricing.go::Apply",
            # which is both what a reader expects and what makes the method
            # findable under its type.
            owner_name = _owner_type_name(node, source, config)
            owner_scope = (
                [owner_name]
                if owner_name and not scope and not qualifier
                and node.type not in config.impl_types
                else []
            )

            # Build the unique id: "path/to/file.py::Outer.inner"
            qualified_name = ".".join(scope + qualifier + owner_scope + [name])
            entity_id = unique_id(f"{relative_path}::{qualified_name}", node.type)

            is_class = node.type in config.class_types
            if is_class:
                # An `impl` block is a container, not a type of its own.
                #
                # `impl Priced for LineItem` is named after LineItem so its
                # methods are filed under the type they belong to - but it
                # must not then compete with `struct LineItem` as a call
                # target, or every reference to the type is ambiguous again.
                # Marking it `impl` keeps the methods correctly parented
                # while leaving exactly one entity answering to the name.
                kind = "impl" if node.type in config.impl_types else "class"
            elif inside_class or qualifier or owner_scope:
                # A function whose immediate parent is a class = a method.
                # An out-of-line C++ definition names its class explicitly,
                # and a Go method names it in its receiver.
                kind = "method"
            else:
                # Either top level, or a helper nested inside another
                # function. Both are functions, not methods.
                kind = "function"

            code = _text(node, source)

            # What each local name in this definition was DECLARED to be.
            #
            # Three sources, in increasing order of how much they are worth:
            # the parameters (and, in Go and Rust, the receiver), then the
            # implicit `self` / `this`, which is the enclosing type. Together
            # these are what lets `auth.login()` be resolved to the
            # `AuthService.login` its signature names, instead of being
            # abandoned as ambiguous because a subclass overrides it.
            # A class's own fields, computed once here and handed to every
            # method below it.
            own_fields = _field_bindings(node, source, config) if is_class else {}

            # Widest first, narrowest last: a parameter shadows a field of
            # the same name, which is what the language itself does.
            type_env = dict(field_types or {})
            type_env.update(_type_environment(node, source, config))
            enclosing_type = owner_name or (scope[-1] if inside_class and scope else None)
            if enclosing_type:
                for implicit in ("self", "this"):
                    type_env.setdefault(implicit, enclosing_type)

            param_count, flexible_arity = _arity(node, source)

            entities.append(
                CodeEntity(
                    id=entity_id,
                    kind=kind,
                    name=name,
                    file_path=relative_path,
                    language=config.name,
                    # tree-sitter rows are 0-based; editors are 1-based.
                    # The end needs more care than a simple +1 - see
                    # tree_utils.inclusive_end_line.
                    start_line=node.start_point[0] + 1,
                    end_line=inclusive_end_line(node),
                    code=code,
                    docstring=_extract_docstring(node, source, config),
                    content_hash=_hash_text(code),
                    owner_name=owner_name,
                    param_count=param_count,
                    flexible_arity=flexible_arity,
                )
            )

            # "The thing I am inside contains me."
            if parent_id is not None:
                relationships.append(
                    Relationship(source_id=parent_id, target_id=entity_id, kind="CONTAINS")
                )

            # Record the calls made directly by this entity.
            #
            # WHICH STOP SET, AND WHY IT MATTERS SO MUCH
            # ------------------------------------------
            # Only stop at constant nodes where a constant ENTITY actually
            # exists to own those calls. Constants are created for module and
            # class level declarations only (see the `not inside_function`
            # guard above) - never inside a function body.
            #
            # Using `boundary_types` everywhere therefore halted the walker at
            # every local assignment without creating anything to collect the
            # calls it contained, so this line:
            #
            #     user = self.repo.find_by_email(email)
            #
            # produced no edge and no unresolved reference either. The call
            # simply vanished. Measured across this project's own source that
            # silently discarded 41% of all call sites, and in Kotlin, Swift,
            # Scala and Lua - where a local binding uses the same node type as
            # a module constant - it discarded the entire call graph.
            makes_constants = is_class and not inside_function
            call_stop_types = boundary_types if makes_constants else definition_types

            # {called name: the type its receiver was declared to be}.
            #
            # A name called on TWO different receivers inside one definition
            # is dropped rather than guessed at: `a.save()` and `b.save()` in
            # the same function say nothing certain about which `save` a
            # single pending name refers to, and a hint that is wrong is far
            # worse than no hint, because it resolves confidently.
            owner_hints: dict[str, str] = {}
            conflicting: set[str] = set()

            arity_hints: dict[str, int] = {}
            conflicting_arity: set[str] = set()

            for called_name, receiver, argument_count in _collect_call_sites(
                node, source, config, call_stop_types
            ):
                pending_calls.append((entity_id, called_name))

                # How many arguments this name was called with. Same
                # conflict rule as the type hints: one name called two
                # different ways inside a single definition tells us nothing
                # reliable about a single pending reference.
                if argument_count is not None:
                    if (
                        called_name in arity_hints
                        and arity_hints[called_name] != argument_count
                    ):
                        conflicting_arity.add(called_name)
                    arity_hints[called_name] = argument_count

                if not receiver:
                    continue
                hint = type_env.get(receiver)
                if hint is None and receiver[:1].isupper():
                    # A capitalised receiver that is not a local name is
                    # almost always the type itself - `TaxCalculator.applyTax()`,
                    # a static call or a constructor. Worth using, and safe:
                    # if no candidate has that owner the hint is ignored
                    # rather than allowed to rule everything out.
                    hint = receiver
                if hint is None or hint == called_name:
                    continue

                if called_name in owner_hints and owner_hints[called_name] != hint:
                    conflicting.add(called_name)
                owner_hints[called_name] = hint

            for name in conflicting:
                owner_hints.pop(name, None)
            for name in conflicting_arity:
                arity_hints.pop(name, None)

            entities[-1].call_owner_hints = owner_hints
            entities[-1].call_arity_hints = arity_hints

            # Record inheritance.
            if is_class:
                for parent_name in _collect_parent_class_names(node, source, config):
                    pending_extends.append((entity_id, parent_name))
                # `impl Priced for LineItem` - the trait is not in any
                # heritage clause the generic collector knows about, it sits
                # in a field of its own.
                trait_name = _impl_trait_name(node, source, config)
                if trait_name:
                    pending_extends.append((entity_id, trait_name))

            # The types this definition names in its signature, plus - for a
            # class - the types of its own fields. Both are real structural
            # dependencies that no CALLS edge records.
            field_type_names = {
                value for value in own_fields.values()
                if value not in _PRIMITIVE_TYPE_NAMES
            }
            for type_name in _signature_type_names(node, source, config) | field_type_names:
                if type_name != name:
                    pending_types.append((entity_id, type_name))

            # A definition that named its owning type wants an edge FROM that
            # type TO itself. We can only make it here if the type is in this
            # same file; otherwise `owner_name` stays on the node and the
            # indexer links it once both ends exist.
            if owner_name:
                pending_owners.append((entity_id, owner_name))

            # Descend, now with this definition added to the scope.
            #
            # A class hands its own fields down to its methods; anything else
            # passes on whatever it was given, so a nested helper inside a
            # method can still see the enclosing class's fields.
            for child in node.children:
                visit(
                    child,
                    scope + qualifier + [name],
                    entity_id,
                    inside_class=is_class,
                    inside_function=inside_function or not is_class,
                    field_types=own_fields if is_class else field_types,
                )

        else:
            # Not a definition - just keep looking downwards, scope unchanged.
            for child in node.children:
                visit(
                    child, scope, parent_id, inside_class, inside_function,
                    field_types,
                )

    visit(root, scope=[], parent_id=None, inside_class=False, inside_function=False)
    return (entities, relationships, pending_calls, pending_extends,
            pending_owners, pending_types)


def _outermost(entities: list[CodeEntity]) -> list[CodeEntity]:
    """
    The entities that are not nested inside any other entity in the list.

    A class and its methods all appear in `entities`; only the class is
    "outermost", and that is what a reader of an outline wants to see.
    """
    real = [e for e in entities if e.kind != "module"]
    return [
        e for e in sorted(real, key=lambda e: e.start_line)
        if not any(
            other is not e
            and other.start_line <= e.start_line
            and other.end_line >= e.end_line
            for other in real
        )
    ]


def _file_outline(relative_path: str, entities: list[CodeEntity]) -> str:
    """
    A short "what is in this file" summary, used for files whose every line is
    already inside some definition.

    Built from names and kinds only - never from bodies - so it stays stable
    under any edit that does not add or remove a top-level definition, and so
    it never triggers a re-embed of this node on an unrelated change.
    """
    outermost = _outermost(entities)
    if not outermost:
        return ""

    lines = [f"{relative_path} - file outline"]
    lines.extend(f"{entity.kind} {entity.name}" for entity in outermost)
    return "\n".join(lines)


def _build_module_entity(
    root,
    source: bytes,
    config: LanguageConfig,
    relative_path: str,
    entities: list[CodeEntity],
) -> Optional[CodeEntity]:
    """
    Capture the code that is NOT inside any function or class.

    WHY THIS EXISTS
    ---------------
    Entities are functions and classes, so everything at the top level of a
    file - imports, constants, configuration dictionaries, route tables,
    module-level wiring - belonged to no node at all and was therefore
    completely invisible to retrieval. Measured on a demo project, that was
    a third of all code lines missing:

        ROUTES = {"/login": "handle_login", ...}     <- unreachable
        MAX_CART_ITEMS = 50                          <- unreachable
        auth_service = AuthService(repository)       <- unreachable

    Ask "where are the routes defined?" and the honest answer was that the
    system could not see them.

    So each file also gets ONE `module` entity holding its top-level lines.
    Nested definition bodies are left out (they have their own nodes) and
    replaced with a short marker, so the module node stays a readable
    summary of the file's shape rather than a duplicate of the whole file.
    """
    # Lines already covered by a real entity.
    covered: set[int] = set()
    for entity in entities:
        covered.update(range(entity.start_line, entity.end_line + 1))

    lines = source.decode("utf-8", errors="replace").splitlines()

    kept: list[str] = []
    first_line = None
    last_line = None
    skip_start: Optional[int] = None

    def outermost_names(first: int, last: int) -> list[str]:
        """The top-level definitions occupying lines `first`..`last`."""
        inside = [
            e for e in entities
            if e.kind != "module" and e.start_line >= first and e.end_line <= last
        ]
        # A class and its methods all fall inside the run; name only the
        # outermost, which is what a reader is looking for.
        return [
            e.name for e in sorted(inside, key=lambda e: e.start_line)
            if not any(
                other is not e
                and other.start_line <= e.start_line
                and other.end_line >= e.end_line
                for other in inside
            )
        ]

    def close_skip(previous_line: int) -> None:
        """Replace a run of already-covered lines with a marker."""
        if skip_start is None:
            return
        # The marker names the DEFINITIONS it stands for, not their line
        # numbers.
        #
        # It used to read "... (lines 19-42 defined above)". That tied this
        # node's text to the exact line count of every definition in the file,
        # so editing the body of any one function shifted the numbers, changed
        # this node's content hash, and forced a re-embed of the module node -
        # on every edit anywhere in the file, which is precisely what the
        # block-level Merkle hash exists to prevent.
        #
        # Names are both stable under those shifts and more useful to follow
        # back than a line range, because they say what was left out.
        names = outermost_names(skip_start, previous_line)
        label = ", ".join(names) if names else "definitions"
        kept.append(f"    ... ({label} defined above)")

    for number, text in enumerate(lines, start=1):
        if number in covered:
            # Collapse each run of definition lines into one marker, so the
            # module node reads like an outline instead of a wall of code.
            if skip_start is None:
                skip_start = number
            continue

        if skip_start is not None:
            close_skip(number - 1)
            skip_start = None

        if text.strip():
            if first_line is None:
                first_line = number
            last_line = number
        kept.append(text)

    if skip_start is not None:
        close_skip(len(lines))

    text = "\n".join(kept).strip()
    # A module node made of nothing but "defined above" markers says nothing
    # about the file's contents - but the file still needs a node.
    meaningful = [
        line for line in text.splitlines()
        if line.strip() and not line.strip().startswith("...")
    ]

    if not meaningful:
        # EVERY LINE BELONGS TO A DEFINITION, so there is no leftover code for
        # this node to hold. We build an OUTLINE rather than giving up.
        #
        # WHY GIVING UP WAS A BUG
        # -----------------------
        # The CodeEntity graph and the File/Folder graph are separate layers
        # on purpose: expansion refuses to travel through File nodes, because
        # one hop through a File hands back every entity in it. The module
        # node is the CodeEntity-layer stand-in for the file - it is what
        # gives a file's top-level definitions a parent INSIDE the layer that
        # expansion is allowed to walk.
        #
        # Returning None here skipped creating it, and so the caller's "link
        # everything uncontained to the module" step never ran. Every
        # top-level definition in such a file became an ORPHAN: findable by a
        # direct vector hit and by nothing else, invisible to every graph
        # walk. Measured on the demo repository, `format.ts::truncate` and
        # `format.ts::CURRENCY` were isolated for exactly this reason.
        #
        # And note WHICH files took this branch: the ones made entirely of
        # definitions, with no loose wiring left at the top. That is what a
        # well-factored file looks like. The cleaner the file, the more likely
        # its entities were orphaned - the worst correlation a bug can have.
        #
        # The outline is built from names and kinds only, never from bodies,
        # so editing any function leaves this node's hash untouched and it is
        # not re-embedded. It also answers "what is in this file?" directly.
        text = _file_outline(relative_path, entities)
        if not text:
            return None  # a file with no entities and no leftover code at all
        first_line = 1
        last_line = max(len(lines), 1)

    module_name = relative_path.rsplit("/", 1)[-1]

    return CodeEntity(
        id=f"{relative_path}::<module>",
        kind="module",
        name=module_name,
        file_path=relative_path,
        language=config.name,
        start_line=first_line or 1,
        end_line=last_line or first_line or 1,
        code=text,
        docstring=None,
        content_hash=_hash_text(text),
    )


def _collect_top_level_calls(
    root, source: bytes, config: LanguageConfig
) -> list[str]:
    """
    Calls made at the top level of a file, outside any function or class.

    These are how a module wires itself together:

        auth_service = AuthService(repository)

    Attributing that call to the module node connects the file's setup code
    to the classes it instantiates, which is exactly the link someone asking
    "where is AuthService constructed?" needs.
    """
    boundary_types = (
        set(config.function_types)
        | set(config.class_types)
        | set(config.constant_types)
    )
    return _collect_call_names(root, source, config, boundary_types)


def _collect_imports(root, source: bytes, config: LanguageConfig) -> list[str]:
    """
    Grab the raw text of every import statement at the top of the file.

    We keep them as plain text rather than resolving them to files here,
    because turning "from .db import GraphStore" into an actual path is
    language-specific guesswork. storage/ does a best-effort match later.
    """
    imports: list[str] = []

    def walk(node) -> None:
        if node.type in config.import_types:
            imports.append(_text(node, source).strip())
            return  # no need to look inside an import statement

        # Languages where an import IS a call: Ruby's `require "json"`.
        # We check the callee name so that only real imports are collected.
        # Treating every `call` node as an import (which is what listing
        # "call" in import_types did) made every ordinary method call in a
        # Ruby file show up as an import line.
        if config.import_call_names and node.type in config.call_types:
            field_name = config.call_function_fields.get(
                node.type, config.call_function_field
            )
            callee = node.child_by_field_name(field_name)
            if callee is not None:
                name = _last_identifier(_text(callee, source))
                if name in config.import_call_names:
                    imports.append(_text(node, source).strip())
                    return

        for child in node.children:
            walk(child)

    walk(root)
    return imports


# ---------------------------------------------------------------------------
# The one function the rest of the project calls
# ---------------------------------------------------------------------------

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

    # Read as BYTES ("rb"), not text. tree-sitter wants bytes, and reading
    # bytes also means a file with a strange encoding cannot crash us here.
    source = absolute_path.read_bytes()

    tree = parser.parse(source)

    (entities, relationships, pending_calls, pending_extends, pending_owners,
     pending_types) = _walk_tree(tree.root_node, source, config, relative_path)

    # ---- link members to the type they named -------------------------
    #
    # Go and Rust declare membership by NAMING the owning type rather than by
    # nesting inside it, so the edge cannot come out of the tree walk. We can
    # make it here whenever the type is in this same file, which it usually
    # is; when it is not, `owner_name` stays on the entity and the indexer
    # links it once both ends are in the graph.
    #
    # The target must be a real TYPE. In Rust `impl LineItem` and
    # `struct LineItem` share a name, so an unfiltered lookup would happily
    # link the impl block to itself and leave the struct as disconnected as
    # it was before.
    types_by_name: dict[str, list[str]] = {}
    for entity in entities:
        if entity.kind == "class":
            types_by_name.setdefault(entity.name, []).append(entity.id)

    owned_ids: set[str] = set()
    for member_id, owner_name in pending_owners:
        owners = [
            owner_id for owner_id in types_by_name.get(owner_name, [])
            if owner_id != member_id
        ]
        if len(owners) == 1:
            relationships.append(
                Relationship(source_id=owners[0], target_id=member_id, kind="CONTAINS")
            )
            owned_ids.add(member_id)

    # Add the module node: everything at the top level that is not inside a
    # function or class. Without it, imports, constants and wiring code are
    # in no node at all and cannot be retrieved.
    module_entity = _build_module_entity(
        tree.root_node, source, config, relative_path, entities
    )
    if module_entity is not None:
        # Link the module to the top-level definitions in its file.
        #
        # The File node already contains them, so this is not the only path
        # between them - but File nodes are not part of the CodeEntity graph
        # walk, so without this edge the module node is unreachable from the
        # code. That matters for retrieval: landing on `checkout` should be
        # able to reach the module node and answer "what is MAX_CART_ITEMS?",
        # because the constant lives there and nowhere else.
        contained = {r.target_id for r in relationships if r.kind == "CONTAINS"}
        for entity in entities:
            if entity.id not in contained:
                relationships.append(
                    Relationship(
                        source_id=module_entity.id, target_id=entity.id, kind="CONTAINS"
                    )
                )

        entities.append(module_entity)

        # Top-level calls belong to the module, not to any function.
        for called_name in _collect_top_level_calls(tree.root_node, source, config):
            pending_calls.append((module_entity.id, called_name))

    # Resolve calls and inheritance WITHIN this file first.
    # Same-file resolution is the easy, reliable case, so we do it here and
    # leave the harder cross-file case to storage/, which can see the whole
    # graph. (This is the "start narrow, expand later" principle.)
    # Only CALLABLE entities are candidates for a call or an inheritance
    # edge. A constant and a whole-file module node are neither.
    #
    # Without this filter, `entry.is_dir()` in merkle.py reduced to the bare
    # name `is_dir` and matched the TreeNode dataclass field of that name,
    # producing a CALLS edge that pointed at a variable. Cross-file
    # resolution rules the same kinds out - see pipeline.indexer.
    # resolve_reference - and both paths have to agree or the bug simply
    # moves from one to the other.
    name_to_ids: dict[str, list[str]] = {}
    for entity in entities:
        if entity.kind in ("constant", "module"):
            continue
        name_to_ids.setdefault(entity.name, []).append(entity.id)

    unresolved: list[tuple[str, str, str]] = []  # (source_id, name, edge kind)

    # Entities that call themselves. Recorded as a property rather than a
    # self-edge: "is this recursive?" is a real fact about the code that was
    # simply lost before (the unique-match rule refuses to link an entity to
    # itself), but a self-loop in the graph would complicate every traversal
    # for no retrieval benefit.
    recursive_ids: set[str] = set()

    def _resolve_in_file(source_id: str, wanted: str) -> str | None:
        """One match in this file, or the one lexical scope points at."""
        candidates = name_to_ids.get(wanted, [])
        if len(candidates) == 1:
            return candidates[0]
        # Several candidates share the name. Scope usually knows which is
        # meant - see prefer_by_scope for the measured cost of not asking.
        return prefer_by_scope(candidates, source_id)

    for source_id, called_name in pending_calls:
        target = _resolve_in_file(source_id, called_name)
        if target == source_id:
            # A recursive call. Not an edge, but not a miss either - so it
            # must not be reported as an unresolved reference and retried
            # against the whole repository on every future run.
            recursive_ids.add(source_id)
        elif target is not None:
            relationships.append(
                Relationship(source_id=source_id, target_id=target, kind="CALLS")
            )
        else:
            # Zero matches (probably defined elsewhere) or genuinely
            # ambiguous. Either way, hand it to storage/ to try repo-wide.
            unresolved.append((source_id, called_name, "CALLS"))

    for source_id, parent_name in pending_extends:
        target = _resolve_in_file(source_id, parent_name)
        if target is not None and target != source_id:
            relationships.append(
                Relationship(source_id=source_id, target_id=target, kind="EXTENDS")
            )
        else:
            unresolved.append((source_id, parent_name, "EXTENDS"))

    # A type reference can only ever land on a TYPE.
    #
    # This is a stricter rule than the one calls use, and it has to be: a
    # parameter annotated `Order` means the class, and if a FUNCTION named
    # `Order` also exists in the file, linking to it would be plainly wrong.
    # Calls can legitimately target a class (that is how most languages spell
    # "construct one"), so the two rules cannot share a candidate set.
    types_only = {
        name: [e.id for e in entities if e.kind == "class" and e.name == name]
        for _, name in pending_types
    }
    seen_type_refs: set[tuple[str, str]] = set()
    for source_id, type_name in pending_types:
        if (source_id, type_name) in seen_type_refs:
            continue
        seen_type_refs.add((source_id, type_name))

        matches = [i for i in types_only.get(type_name, []) if i != source_id]
        if len(matches) == 1:
            relationships.append(
                Relationship(
                    source_id=source_id, target_id=matches[0], kind="USES_TYPE"
                )
            )
        elif not matches:
            # Not in this file. Almost always an imported type, so hand it to
            # the repository-wide pass. An ambiguous same-file match falls
            # through deliberately: two classes of one name in one file is
            # not something we should guess about.
            unresolved.append((source_id, type_name, "USES_TYPE"))

    # Hang each entity's own pending references on the entity itself, so they
    # are persisted with the node and can be retried on every later run even
    # when this file never changes again.
    by_kind: dict[str, dict[str, list[str]]] = {
        "CALLS": {}, "EXTENDS": {}, "USES_TYPE": {},
    }
    for source_id, name, kind in unresolved:
        by_kind[kind].setdefault(source_id, []).append(name)

    for entity in entities:
        entity.unresolved_calls = by_kind["CALLS"].get(entity.id, [])
        entity.unresolved_extends = by_kind["EXTENDS"].get(entity.id, [])
        entity.unresolved_types = by_kind["USES_TYPE"].get(entity.id, [])
        entity.is_recursive = entity.id in recursive_ids

    # The literal values this file shares with the rest of the system.
    #
    # Read off each entity's own source text rather than the whole file, so
    # the edge lands on the function that actually makes the call rather than
    # on the file in general - which is what makes "who else touches this
    # route?" answerable at the right granularity.
    interface_refs: list[tuple[str, str, str]] = []
    for entity in entities:
        for kind, value in extract_interface_tokens(entity.code):
            interface_refs.append((entity.id, kind, value))

    parsed = ParsedFile(
        file_path=relative_path,
        language=language,
        content_hash=_hash_text(source.decode("utf-8", errors="replace")),
        entities=entities,
        relationships=relationships,
        imports=_collect_imports(tree.root_node, source, config),
        unresolved_references=unresolved,
        interface_refs=interface_refs,
    )

    return parsed
