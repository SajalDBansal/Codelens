"""Conversion between Python models and SQLite node properties."""


def summary_to_properties(summary) -> dict:
    """Turn a FolderSummary into stored properties."""
    return {
        "id": summary.id,
        "path": summary.path,
        "text": summary.text,
        "entityCount": summary.entity_count,
        "contentHash": summary.content_hash,
        "embedding": summary.embedding,
    }


def record_to_summary(record):
    """Turn a row from storage back into a FolderSummary."""
    from ..models import FolderSummary

    return FolderSummary(
        id=record["id"],
        path=record["path"],
        text=record["text"],
        entity_count=record["entityCount"] or 0,
        content_hash=record["contentHash"] or "",
    )


# ---------------------------------------------------------------------------
# Translating between our Python models and stored properties
# ---------------------------------------------------------------------------

def entity_to_properties(entity) -> dict:
    """
    Turn a CodeEntity (snake_case Python) into a dict of stored properties
    (camelCase), ready to be handed to a query as a parameter.
    """
    return {
        # The names this entity referred to that we could not turn into an
        # edge while parsing its own file. Stored on the node so that LATER
        # runs can retry them - see GraphStore.get_pending_references.
        "unresolvedCalls": sorted(set(entity.unresolved_calls)),
        "unresolvedExtends": sorted(set(entity.unresolved_extends)),
        # Types named in a signature or a field whose definition is
        # in another file. Retried like the two above.
        "unresolvedTypes": sorted(set(entity.unresolved_types)),
        # Names that are nowhere in the graph - library and third-party
        # calls. Held apart from the list above because they must NOT be
        # retried on every run; see GraphStore.get_external_references.
        "externalCalls": sorted(set(entity.external_calls)),
        "externalTypes": sorted(set(entity.external_types)),
        "isRecursive": entity.is_recursive,
        "ownerName": entity.owner_name,
        # "which type was the receiver of this call declared to be", as a
        # list of "name>Type" strings.
        #
        # This schema stores hint maps as lists of strings. A ">" is the
        # separator because it cannot occur in an identifier in any language
        # in the registry, so splitting is unambiguous and needs no escaping.
        # Sorted so an unchanged entity produces an unchanged property and
        # does not look like a write when nothing moved.
        "callOwnerHints": sorted(
            f"{name}>{owner}" for name, owner in entity.call_owner_hints.items()
        ),
        # "and it was called with N arguments", same flattening.
        "callArityHints": sorted(
            f"{name}>{count}" for name, count in entity.call_arity_hints.items()
        ),
        # What a caller passes, and whether that number can be relied on.
        # See code_parser._arity for why the second value matters more.
        "paramCount": entity.param_count,
        "flexibleArity": entity.flexible_arity,
        "id": entity.id,
        "kind": entity.kind,
        "name": entity.name,
        "filePath": entity.file_path,
        "language": entity.language,
        "startLine": entity.start_line,
        "endLine": entity.end_line,
        "code": entity.code,
        "docstring": entity.docstring,
        "contentHash": entity.content_hash,
        "embedding": entity.embedding,
    }


def parse_hint_list(rows, cast=str) -> dict:
    """
    Turn a stored ["name>value", ...] list back into {name: value}.

    This schema serializes the two hint maps on a CodeEntity
    as lists of "key>value" strings on the way in. This is the way
    out. A row that does not split cleanly is skipped rather than raised on:
    these are an optimisation for edge resolution, and one malformed entry
    written by an older version of the indexer must not stop a run.
    """
    parsed: dict = {}
    for row in rows or ():
        name, separator, value = str(row).partition(">")
        if not separator or not name:
            continue
        try:
            parsed[name] = cast(value)
        except (TypeError, ValueError):
            continue
    return parsed


def record_to_entity(record) -> "object":
    """
    The reverse: turn a row from storage back into a CodeEntity.

    Imported here rather than at the top of the file to avoid a circular
    import (models does not import storage, and this keeps it that way).
    """
    from ..models import CodeEntity

    return CodeEntity(
        id=record["id"],
        kind=record["kind"],
        name=record["name"],
        file_path=record["filePath"],
        language=record["language"],
        start_line=record["startLine"],
        end_line=record["endLine"],
        code=record["code"],
        docstring=record["docstring"],
        content_hash=record["contentHash"] or "",
    )


def doc_to_properties(chunk) -> dict:
    """Turn a DocChunk (snake_case) into stored properties (camelCase)."""
    return {
        "id": chunk.id,
        "filePath": chunk.file_path,
        "docType": chunk.doc_type,
        "title": chunk.title,
        "headingPath": chunk.heading_path,
        "level": chunk.level,
        "startLine": chunk.start_line,
        "endLine": chunk.end_line,
        "text": chunk.text,
        "contentHash": chunk.content_hash,
        "embedding": chunk.embedding,
    }


def record_to_doc(record):
    """Turn a row from storage back into a DocChunk."""
    from ..models import DocChunk

    return DocChunk(
        id=record["id"],
        file_path=record["filePath"],
        doc_type=record["docType"],
        title=record["title"],
        heading_path=record["headingPath"],
        level=record["level"],
        start_line=record["startLine"],
        end_line=record["endLine"],
        text=record["text"],
        content_hash=record["contentHash"] or "",
    )


# Edges that start at a Doc node.
#   CONTAINS - a section contains a subsection (Doc -> Doc)
#   MENTIONS - a doc names a function in `backticks` (Doc -> CodeEntity).
#              This is the edge that stops documentation from being just
#              plain RAG: it connects prose to the code it describes, so
#              retrieving one can pull in the other.
ALLOWED_DOC_RELATIONSHIPS = {"CONTAINS", "MENTIONS"}


def interface_id(kind: str, value: str) -> str:
    """The stable node id for one interface token, e.g. "route:/api/cart"."""
    from ..parsing.interfaces import interface_id as _build

    return _build(kind, value)


# The only relationship types we are willing to create between code entities.
#
# Public write methods accept only the supported typed graph relationships.
ALLOWED_ENTITY_RELATIONSHIPS = {"CALLS", "CONTAINS", "EXTENDS", "USES_TYPE"}
