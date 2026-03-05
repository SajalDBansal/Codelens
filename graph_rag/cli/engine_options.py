"""Validated advanced settings saved under `engine` in the user config file."""

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, field_validator

from .. import config
from .state import Record


class EngineOptions(Record):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)
    ladybug_buffer_mb: int = Field(
        default=config.LADYBUG_BUFFER_MB,
        ge=32,
        description="Database buffer budget in MB; minimum 32.",
    )
    ladybug_threads: int = Field(
        default=config.LADYBUG_THREADS,
        ge=1,
        description="Worker threads used by the embedded database.",
    )
    max_file_size_bytes: int = Field(
        default=config.MAX_FILE_SIZE_BYTES,
        ge=1,
        description="Largest source file to index, in bytes.",
    )
    max_doc_file_size_bytes: int = Field(
        default=config.MAX_DOC_FILE_SIZE_BYTES,
        ge=1,
        description="Largest documentation/config file to index, in bytes.",
    )
    max_doc_chunks_per_file: int = Field(
        default=config.MAX_DOC_CHUNKS_PER_FILE,
        ge=1,
        description="Maximum chunks per structured document.",
    )
    max_prose_chunks_per_file: int = Field(
        default=config.MAX_PROSE_CHUNKS_PER_FILE,
        ge=1,
        description="Maximum chunks per prose document.",
    )
    extract_interfaces: bool = Field(
        default=config.EXTRACT_INTERFACES,
        description="Extract shared routes, events, and interface references.",
    )
    max_interface_fanout: int = Field(
        default=config.MAX_INTERFACE_FANOUT,
        ge=1,
        description="Maximum entities linked through a shared interface.",
    )
    generate_summaries: bool = Field(
        default=config.GENERATE_SUMMARIES,
        description="Generate folder summaries using the reasoning model.",
    )
    min_entities_for_summary: int = Field(
        default=config.MIN_ENTITIES_FOR_SUMMARY,
        ge=1,
        description="Minimum entities before generating a folder summary.",
    )
    default_summary_top_k: int = Field(
        default=config.DEFAULT_SUMMARY_TOP_K,
        ge=0,
        description="Folder summaries to retrieve; zero disables summary search.",
    )
    max_doc_chunk_chars: int = Field(
        default=config.MAX_DOC_CHUNK_CHARS,
        ge=1,
        description="Maximum characters in a documentation chunk.",
    )
    embedding_batch_size: int = Field(
        default=config.EMBEDDING_BATCH_SIZE,
        ge=1,
        description="Maximum items per embedding request.",
    )
    max_embed_chars: int = Field(
        default=config.MAX_EMBED_CHARS,
        ge=1,
        description="Maximum characters embedded for one code entity.",
    )
    default_top_k: int = Field(
        default=config.DEFAULT_TOP_K, ge=1, description="Number of initial code search results."
    )
    default_hops: Literal[1, 2] = Field(
        default=config.DEFAULT_HOPS, description="Graph expansion depth: 1 or 2."
    )
    max_context_chars: int = Field(
        default=config.MAX_CONTEXT_CHARS,
        ge=1,
        description="Maximum characters in retrieved answer context.",
    )
    default_doc_top_k: int = Field(
        default=config.DEFAULT_DOC_TOP_K,
        ge=0,
        description="Documents to retrieve; zero disables document search.",
    )
    max_mention_targets: int = Field(
        default=config.MAX_MENTION_TARGETS,
        ge=1,
        description="Maximum code targets for one document mention.",
    )
    max_name_candidates: int = Field(
        default=config.MAX_NAME_CANDIDATES,
        ge=1,
        description="Maximum candidates considered when resolving a name.",
    )
    min_similarity: float = Field(
        default=config.MIN_SIMILARITY,
        ge=0,
        le=1,
        description="Minimum cosine similarity for semantic search, from 0 to 1.",
    )
    max_block_chars: int = Field(
        default=config.MAX_BLOCK_CHARS,
        ge=1,
        description="Maximum characters per retrieved code block.",
    )
    hybrid_vector_weight: float = Field(
        default=config.HYBRID_VECTOR_WEIGHT,
        ge=0,
        description="Weight of semantic matches in rank fusion.",
    )
    hybrid_keyword_weight: float = Field(
        default=config.HYBRID_KEYWORD_WEIGHT,
        ge=0,
        description="Weight of keyword matches in rank fusion.",
    )
    hybrid_reserved_vector_slots: int = Field(
        default=config.HYBRID_RESERVED_VECTOR_SLOTS,
        ge=0,
        description="Slots reserved for semantic matches.",
    )
    rrf_k: int = Field(
        default=config.RRF_K, ge=1, description="Reciprocal rank fusion smoothing constant."
    )
    module_keyword_penalty: float = Field(
        default=config.MODULE_KEYWORD_PENALTY,
        ge=0,
        le=1,
        description="Keyword multiplier for module blocks, from 0 to 1.",
    )
    max_module_expansion: int = Field(
        default=config.MAX_MODULE_EXPANSION,
        ge=0,
        description="Maximum symbols expanded from a matched module.",
    )
    relative_similarity_margin: float = Field(
        default=config.RELATIVE_SIMILARITY_MARGIN,
        ge=0,
        le=1,
        description="Allowed similarity gap below the best seed.",
    )
    neighbour_relevance_weight: float = Field(
        default=config.NEIGHBOUR_RELEVANCE_WEIGHT,
        ge=0,
        description="Weight of a graph neighbour's own question relevance.",
    )
    extra_ignored_directories: list[str] = Field(
        default_factory=list, description="Additional excluded folder names, as a JSON array."
    )
    ignored_directories: list[str] = Field(
        default=sorted(config.IGNORED_DIRECTORIES),
        description="Excluded folder names; .codelens must remain excluded.",
    )
    ignored_directory_suffixes: list[str] = Field(
        default=list(config.IGNORED_DIRECTORY_SUFFIXES),
        description="Excluded folder suffixes, as a JSON array.",
    )
    prose_doc_types: list[str] = Field(
        default=sorted(config.PROSE_DOC_TYPES),
        description="Types using the prose chunk limit: markdown, rst, text.",
    )
    edge_type_weights: dict[str, Annotated[float, Field(ge=0, allow_inf_nan=False)]] = Field(
        default=dict(config.EDGE_TYPE_WEIGHTS),
        description="Graph expansion weights for each supported relationship.",
    )

    @field_validator(
        "extra_ignored_directories", "ignored_directories", "ignored_directory_suffixes"
    )
    @classmethod
    def directory_names(cls, values):
        if any(not item or item in (".", "..") or "/" in item or "\\" in item for item in values):
            raise ValueError("Use nonempty folder names or suffixes, not paths.")
        return list(dict.fromkeys(values))

    @field_validator("ignored_directories")
    @classmethod
    def preserve_graph_exclusion(cls, values):
        if ".codelens" not in values:
            raise ValueError("Keep .codelens excluded so the index never scans its own storage.")
        return values

    @field_validator("prose_doc_types")
    @classmethod
    def document_types(cls, values):
        if any(item not in {"markdown", "rst", "text"} for item in values):
            raise ValueError("Prose document types are markdown, rst, and text.")
        return list(dict.fromkeys(values))

    @field_validator("edge_type_weights")
    @classmethod
    def relationship_names(cls, values):
        if set(values) != set(config.EDGE_TYPE_WEIGHTS):
            raise ValueError("Keep all supported edge names; edit their numeric weights.")
        return values

    def overrides(self) -> dict:
        values = self.model_dump(exclude={"extra_ignored_directories"})
        result = {key.upper(): value for key, value in values.items()}
        result["IGNORED_DIRECTORIES"] = set(
            self.ignored_directories + self.extra_ignored_directories
        )
        result["IGNORED_DIRECTORY_SUFFIXES"] = tuple(self.ignored_directory_suffixes)
        result["PROSE_DOC_TYPES"] = set(self.prose_doc_types)
        return result
