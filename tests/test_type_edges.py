"""
tests/test_type_edges.py
========================

`USES_TYPE` - the structural dependency no call records.

THE HOLE THIS FILLS
-------------------
The graph had CALLS, CONTAINS and EXTENDS and nothing else, so a type was
only ever connected to the code using it if that code happened to CALL it.
Measured on a five-language demo repository, the TypeScript `Order` and
`Session` interfaces had **degree 1** - their file's CONTAINS edge and
nothing more - even though `Order` is the declared parameter type of the
function that checks an order out and of the component that renders it.

That is a retrieval hole, not a cosmetic one. Asked what `checkout` accepts,
retrieval found the function and had no way to reach the shape of the data
it works on, so the model either omitted the fields or guessed them.

Three sources feed the edge: parameter types, return types, and - for a
class - the types of its own fields.
"""

import pytest

from graph_rag.parsing import parse_file


def _parse(tmp_path, filename, source):
    path = tmp_path / filename
    path.write_text(source, encoding="utf-8")
    parsed = parse_file(path, filename)
    assert parsed is not None, f"{filename}: parser returned nothing"
    return parsed


def _type_edges(parsed):
    return {
        (r.source_id.split("::")[-1], r.target_id.split("::")[-1])
        for r in parsed.relationships
        if r.kind == "USES_TYPE"
    }


def _pending_types(parsed, name):
    for entity in parsed.entities:
        if entity.name == name:
            return set(entity.unresolved_types)
    raise AssertionError(f"no entity named {name!r}")


# ---------------------------------------------------------------------------
# Same-file: the edge is made immediately
# ---------------------------------------------------------------------------

def test_a_parameter_type_links_the_function_to_the_type(tmp_path):
    parsed = _parse(
        tmp_path,
        "api.ts",
        "export interface Order { orderId: string; }\n"
        "export async function checkout(order: Order): Promise<number> {\n"
        "  return 1;\n"
        "}\n",
    )
    assert ("checkout", "Order") in _type_edges(parsed)


def test_a_return_type_links_the_function_to_the_type(tmp_path):
    parsed = _parse(
        tmp_path,
        "api.ts",
        "export interface Session { token: string; }\n"
        "export async function login(email: string): Promise<Session> {\n"
        "  return { token: 'x' };\n"
        "}\n",
    )
    assert ("login", "Session") in _type_edges(parsed)


def test_a_go_return_type_links_to_its_struct(tmp_path):
    parsed = _parse(
        tmp_path,
        "c.go",
        "package internal\n"
        "\n"
        "type Consumer struct{}\n"
        "\n"
        "func NewConsumer() *Consumer {\n"
        "    return &Consumer{}\n"
        "}\n",
    )
    assert ("NewConsumer", "Consumer") in _type_edges(parsed)


def test_a_type_inside_a_container_is_still_found(tmp_path):
    """
    `list[LineItem]` genuinely depends on LineItem. Every identifier in a
    type expression is taken, not just the last, because `Result<Order,
    ApiError>` references both and taking one would be arbitrary.
    """
    parsed = _parse(
        tmp_path,
        "order.py",
        "class LineItem:\n"
        "    pass\n"
        "\n"
        "def total(items: list[LineItem]) -> int:\n"
        "    return 0\n",
    )
    assert ("total", "LineItem") in _type_edges(parsed)


def test_a_class_field_type_links_the_class_to_the_type(tmp_path):
    """
    THE FIELD CASE.

    A field is how object-oriented code most often holds a collaborator, and
    it was invisible: the type environment read parameters, receivers and
    `self`, and stopped there.
    """
    parsed = _parse(
        tmp_path,
        "Svc.java",
        "public class TaxCalculator {}\n"
        "public class InvoiceService {\n"
        "    private final TaxCalculator taxCalculator;\n"
        "}\n",
    )
    assert ("InvoiceService", "TaxCalculator") in _type_edges(parsed)


def test_a_field_type_also_gives_its_methods_a_receiver_hint(tmp_path):
    """
    The other half of the same fix. `taxCalculator.applyTax(...)` inside a
    method has a receiver whose type is declared on the CLASS, not in the
    method's own signature - so without field bindings the call got no hint
    and stayed ambiguous.
    """
    parsed = _parse(
        tmp_path,
        "Svc.java",
        "public class InvoiceService {\n"
        "    private final TaxCalculator taxCalculator;\n"
        "    public int make(int cents) {\n"
        "        return taxCalculator.applyTax(cents);\n"
        "    }\n"
        "}\n",
    )
    hints = next(e for e in parsed.entities if e.name == "make").call_owner_hints
    assert hints.get("applyTax") == "TaxCalculator"


# ---------------------------------------------------------------------------
# Cross-file: handed to the repository-wide pass
# ---------------------------------------------------------------------------

def test_a_type_from_another_file_is_left_pending(tmp_path):
    """
    A type is usually declared once and imported everywhere, so the
    cross-file case is the common one - it must be handed on, not dropped.
    """
    parsed = _parse(
        tmp_path,
        "main.py",
        "from api.models.order import Order\n"
        "\n"
        "def handle(order: Order):\n"
        "    return order\n",
    )
    assert "Order" in _pending_types(parsed, "handle")


# ---------------------------------------------------------------------------
# The boundaries - what must NOT become an edge
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("annotation", ["int", "str", "bool", "float", "None"])
def test_primitives_are_never_type_references(tmp_path, annotation):
    """
    Built-ins are not entities, so a reference to one can never resolve. They
    would sit in the pending set for ever, retried on every run - which is
    exactly the cost the external/ambiguous split exists to avoid.
    """
    parsed = _parse(
        tmp_path,
        "p.py",
        f"def f(value: {annotation}):\n    return value\n",
    )
    assert _pending_types(parsed, "f") == set()


def test_a_type_reference_never_links_to_a_function_of_the_same_name(tmp_path):
    """
    THE RULE THAT IS STRICTER THAN THE ONE FOR CALLS.

    A call may legitimately target a class - that is how most languages spell
    "construct one". A parameter annotated `Order` means the TYPE, so if a
    function of that name also exists, linking to it would be plainly wrong.
    """
    parsed = _parse(
        tmp_path,
        "p.py",
        "def Order():\n"
        "    return 1\n"
        "\n"
        "def handle(o: Order):\n"
        "    return o\n",
    )
    assert _type_edges(parsed) == set()


def test_a_definition_does_not_link_to_itself(tmp_path):
    """A recursive type must not become a self-loop."""
    parsed = _parse(
        tmp_path,
        "p.py",
        "class Node:\n"
        "    def child(self, other: Node) -> Node:\n"
        "        return other\n",
    )
    assert ("Node", "Node") not in _type_edges(parsed)


def test_type_edges_do_not_disturb_the_call_graph(tmp_path):
    """
    USES_TYPE is additive. The CALLS edges that were already correct must be
    exactly as they were.
    """
    parsed = _parse(
        tmp_path,
        "p.py",
        "class Order:\n"
        "    pass\n"
        "\n"
        "def helper(x):\n"
        "    return x\n"
        "\n"
        "def outer(order: Order):\n"
        "    return helper(order)\n",
    )
    calls = {
        (r.source_id.split("::")[-1], r.target_id.split("::")[-1])
        for r in parsed.relationships
        if r.kind == "CALLS"
    }
    assert ("outer", "helper") in calls
    assert ("outer", "Order") in _type_edges(parsed)
    assert ("outer", "Order") not in calls
