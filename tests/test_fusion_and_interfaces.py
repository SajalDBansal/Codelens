"""
tests/test_fusion_and_interfaces.py
===================================

Two things that were losing answers silently.

FUSION. Reciprocal Rank Fusion keeps ranks and throws scores away, which also
throws away how CONFIDENT each search was - and it rewards agreement between
the two lists very heavily. In a small or single-domain codebase almost every
keyword hit is also in the vector top-N, so agreement becomes nearly free and
stops carrying information, while still outvoting position. Measured on the
demo repository, the question "how do I format a price for display?" put the
correct answer at vector rank 1 with similarity 0.767 and fusion ranked it
SIXTH - one slot past top_k=5, so it never reached the model at all.

INTERFACES. Call resolution refuses to link across languages, correctly, which
left a multi-language repository as several disconnected graphs with no edges
between them. Shared literal values - route paths, env-var keys - are the
honest way across, because both ends really do name the same string.
"""
import pytest
from graph_rag import config

class FakeEntity:
    """The minimum `_hybrid` needs: an id, and a kind so modules are visible."""

    def __init__(self, entity_id, kind='function'):
        self.id = entity_id
        self.kind = kind

def by_id(item):
    return item.id
_ONE_SERVICE_PROTO = '\nservice OrderService {\n  rpc Checkout (Req) returns (Rep);\n}\n'

def test_prose_gets_a_far_higher_chunk_ceiling_than_data():
    """
    The cap was written to stop machine-generated dumps flooding the index,
    then applied to hand-written documentation too - silently discarding
    section 13 onwards of a long architecture document, which is exactly
    where the deep detail lives.
    """
    assert config.MAX_PROSE_CHUNKS_PER_FILE > config.MAX_DOC_CHUNKS_PER_FILE
    assert 'markdown' in config.PROSE_DOC_TYPES
    assert 'json' not in config.PROSE_DOC_TYPES
