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
from graph_rag.parsing.interfaces import extract_interface_tokens, interface_id

class FakeEntity:
    """The minimum `_hybrid` needs: an id, and a kind so modules are visible."""

    def __init__(self, entity_id, kind='function'):
        self.id = entity_id
        self.kind = kind

def by_id(item):
    return item.id

def test_prose_does_not_have_env_declarations_read_out_of_it():
    """Only config formats get the bare `KEY:` rule; prose would be all noise."""
    prose = 'TODO: fix this\nNOTE: also this\n'
    assert extract_interface_tokens(prose, is_config=False) == set()

@pytest.mark.parametrize('source', ['require("./styles.css")', 'import x from "/utils/helpers.ts"', 'const p = "/"', 'open("/data/dump.json")'])
def test_file_paths_and_bare_slashes_are_not_routes(source):
    """
    A wrong interface edge is not free - it costs context budget and can
    connect two things that have nothing to do with each other. Precision
    matters more than recall here.
    """
    routes = {v for kind, v in extract_interface_tokens(source) if kind == 'route'}
    assert routes == set()

def test_common_words_shaped_like_variables_are_ignored():
    for noise in ('TODO', 'GET', 'POST', 'TRUE', 'DEBUG'):
        assert ('env', noise) not in extract_interface_tokens(f'{noise}: something\n', is_config=True)

def test_interface_ids_are_stable_and_readable():
    assert interface_id('route', '/api/cart') == 'route:/api/cart'
    assert interface_id('env', 'DATABASE_URL') == 'env:DATABASE_URL'

def test_a_client_route_behind_a_base_url_is_found():
    """
    THE BUG THIS PINS.

    A server writes a route as a bare literal; a client almost always writes
    it with a configured base URL interpolated in front. The original pattern
    required the quote to be followed immediately by "/", so it matched every
    server and no client - and the two ends of one call are exactly what this
    mechanism exists to join.

    Measured before the fix, on a five-language repo: one interface node in
    the whole graph and zero edges across a language boundary.
    """
    server = extract_interface_tokens('ROUTES = {"/api/orders/checkout": h}')
    ts_client = extract_interface_tokens("await fetch(`${API_BASE}/api/orders/checkout`, {method: 'POST'})")
    py_client = extract_interface_tokens('requests.get(f"{base}/api/orders/checkout")')
    assert ('route', '/api/orders/checkout') in server
    assert ('route', '/api/orders/checkout') in ts_client
    assert ('route', '/api/orders/checkout') in py_client

def test_an_interpolated_base_and_an_interpolated_parameter_agree():
    """`${BASE}/users/${id}` must land on the same node as `/users/:id`."""
    client = extract_interface_tokens('axios.get(`${BASE}/users/${id}`)')
    server = extract_interface_tokens('app.get("/users/:id", h)')
    assert ('route', '/users/*') in client
    assert ('route', '/users/*') in server

@pytest.mark.parametrize('source', ['msg = "read the docs and/or the runbook"', 'label = "input/output"'])
def test_an_interpolation_elsewhere_does_not_invent_a_route(source):
    """
    Only an interpolation at the very START of the string may precede a
    route. Allowing a route to match anywhere inside a string turns "and/or"
    into the route "/or", which is the invented edge this file exists to
    avoid.
    """
    assert {v for k, v in extract_interface_tokens(source) if k == 'route'} == set()

def test_a_topic_connects_a_producer_and_a_consumer_in_two_languages():
    """
    The Python API publishes "orders.created" and the Go worker subscribes to
    it. Nothing about the string says "topic", so the surrounding CONTEXT is
    what is matched - but the VALUE is still what has to agree, which is what
    keeps this from linking things that merely look alike.
    """
    producer = extract_interface_tokens('topic = "orders.created"')
    consumer = extract_interface_tokens('const OrdersTopic = "orders.created"')
    call = extract_interface_tokens('producer.publish("orders.created", body)')
    assert ('topic', 'orders.created') in producer
    assert ('topic', 'orders.created') in consumer
    assert ('topic', 'orders.created') in call

@pytest.mark.parametrize('source', ['return res.json()', 'from api.models.order import Order', 'self.items.append(x)', 'VERSION = "1.0.2"', 'headers = {"Content-Type": "application/json"}'])
def test_dotted_names_that_are_not_topics_are_left_alone(source):
    """
    `orders.created` is shaped exactly like an attribute access. Matching
    every dotted literal would link `res.json` to everything in the
    repository, so only a topic-ish context counts.
    """
    assert {v for k, v in extract_interface_tokens(source) if k == 'topic'} == set()

def test_two_services_reading_one_table_share_a_token():
    reader = extract_interface_tokens('db.query("SELECT id FROM orders WHERE id = ?")')
    writer = extract_interface_tokens('conn.exec("INSERT INTO orders (id) VALUES (?)")')
    assert ('table', 'orders') in reader
    assert ('table', 'orders') in writer

def test_a_join_names_both_tables():
    found = extract_interface_tokens('cur.execute("SELECT * FROM users u JOIN orders o ON u.id = o.user_id")')
    assert ('table', 'users') in found
    assert ('table', 'orders') in found

def test_a_python_import_is_not_read_as_a_table():
    """
    THE REASON TABLES ARE ONLY READ OUT OF DEMONSTRABLE SQL.

    Matching a bare `FROM x` against source text reads
    `from api.models.order import Order` as a query against a table. The
    string has to prove it is SQL first.
    """
    assert extract_interface_tokens('from api.models.order import Order') == set()

def test_a_schema_qualified_table_matches_the_bare_one():
    """`public.orders` and `orders` are one table written two ways."""
    qualified = extract_interface_tokens('db.query("SELECT * FROM public.orders")')
    bare = extract_interface_tokens('db.query("SELECT * FROM orders")')
    assert ('table', 'orders') in qualified
    assert qualified == bare

def test_a_proto_file_yields_its_services_and_their_methods():
    proto = '\n    service OrderService {\n      rpc Checkout (CheckoutRequest) returns (CheckoutReply);\n      rpc Cancel (CancelRequest) returns (CancelReply);\n    }\n    service PaymentService {\n      rpc Charge (ChargeRequest) returns (ChargeReply);\n    }\n    '
    found = extract_interface_tokens(proto, is_proto=True)
    assert ('grpc', 'OrderService') in found
    assert ('grpc', 'OrderService.Checkout') in found
    assert ('grpc', 'OrderService.Cancel') in found
    assert ('grpc', 'PaymentService.Charge') in found
    assert ('grpc', 'OrderService.Charge') not in found
_ONE_SERVICE_PROTO = '\nservice OrderService {\n  rpc Checkout (Req) returns (Rep);\n}\n'

def test_a_grpc_wire_path_matches_the_proto_declaration():
    """
    The wire path is a genuine shared literal, so a client that carries it
    lands on the same node as the .proto that declares it.
    """
    declared = extract_interface_tokens(_ONE_SERVICE_PROTO, is_proto=True)
    called = extract_interface_tokens('invoke("/shop.OrderService/Checkout", req)')
    assert ('grpc', 'OrderService.Checkout') in declared
    assert ('grpc', 'OrderService.Checkout') in called

def test_service_and_rpc_words_in_prose_are_not_a_contract():
    """Only a real .proto file gets its declarations read out."""
    assert {v for k, v in extract_interface_tokens(_ONE_SERVICE_PROTO) if k == 'grpc'} == set()

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
