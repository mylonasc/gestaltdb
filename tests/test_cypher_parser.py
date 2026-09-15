import pytest

from gestaltdb.cypher_ast import (
    AndExpression,
    ArithmeticExpression,
    ComparisonExpression,
    NotExpression,
    OrExpression,
    Parameter,
    PropertyRef,
    StringPredicate,
    Variable,
    XorExpression,
)
from gestaltdb.cypher_parser import CypherSemanticError, CypherSyntaxError, parse


def test_parser_ignores_comments_and_keywords_inside_strings():
    parsed = parse(
        '/* lead */ MATCH (n) // node\n'
        'WHERE n.note = "RETURN OR MATCH" RETURN n.id'
    )

    assert parsed.where == ComparisonExpression(
        PropertyRef("n", "note"), "=", "RETURN OR MATCH"
    )


def test_parser_supports_unicode_and_backtick_escaped_names():
    parsed = parse("MATCH (`na``me`:Δrug) RETURN `na``me`.`first name`")

    assert parsed.variable == "na`me"
    assert parsed.label == "Δrug"
    assert parsed.projections == ("na`me.first name",)
    assert parsed.projection_expressions == (PropertyRef("na`me", "first name"),)


def test_parser_preserves_and_shape_with_parenthesized_predicates():
    parsed = parse('MATCH (n) WHERE (n.age >= 18) AND n.kind = "person" RETURN n')

    assert isinstance(parsed.where, AndExpression)
    assert parsed.where.expressions == (
        ComparisonExpression(PropertyRef("n", "age"), ">=", 18),
        ComparisonExpression(PropertyRef("n", "kind"), "=", "person"),
    )


def test_parser_builds_boolean_and_string_expression_ast():
    parsed = parse(
        'MATCH (n) WHERE NOT n.disabled = true OR n.name STARTS WITH "A" '
        'XOR n.name ENDS WITH "z" RETURN n'
    )

    assert isinstance(parsed.where, OrExpression)
    assert isinstance(parsed.where.expressions[0], NotExpression)
    assert isinstance(parsed.where.expressions[1], XorExpression)
    assert parsed.where.expressions[1].expressions[0] == StringPredicate(
        PropertyRef("n", "name"), "STARTS WITH", "A"
    )


def test_parser_builds_arithmetic_expression_to_expression_comparison():
    parsed = parse("MATCH (n) WHERE n.score * 2 + 1 >= n.minimum - -3 RETURN n")

    assert isinstance(parsed.where, ComparisonExpression)
    assert isinstance(parsed.where.left, ArithmeticExpression)
    assert isinstance(parsed.where.right, ArithmeticExpression)


@pytest.mark.parametrize("operator", ["CONTAINS", "=~"])
def test_parser_supports_additional_string_operators(operator):
    parsed = parse(f'MATCH (n) WHERE n.name {operator} "A.*" RETURN n')

    if operator == "CONTAINS":
        assert parsed.where == StringPredicate(PropertyRef("n", "name"), operator, "A.*")
    else:
        assert parsed.where == ComparisonExpression(PropertyRef("n", "name"), operator, "A.*")


def test_parser_represents_multiple_inline_properties_and_preserves_first():
    parsed = parse('MATCH (n:Person {name: "Ada", age: 36, active: true}) RETURN n')

    assert parsed.property_name == "name"
    assert parsed.property_value == "Ada"
    assert parsed.properties == (("name", "Ada"), ("age", 36), ("active", True))


def test_parser_supports_parameter_pagination_without_changing_integer_values():
    parameterized = parse("MATCH (n) RETURN n SKIP $offset LIMIT $count")
    literal = parse("MATCH (n) RETURN n SKIP 2 LIMIT 5")

    assert parameterized.skip == Parameter("offset")
    assert parameterized.limit == Parameter("count")
    assert literal.skip == 2
    assert literal.limit == 5


def test_parser_rejects_variable_length_relationship_with_location():
    query = "MATCH (a)-[:T*1..3]->(b) RETURN b"

    with pytest.raises(CypherSyntaxError) as caught:
        parse(query)

    assert "Unsupported Cypher query" in str(caught.value)
    assert caught.value.line == 1
    assert caught.value.column > 1
    assert caught.value.source == query


def test_semantic_errors_include_unbound_variable_and_location():
    query = "MATCH (n)\nWHERE missing.age > 1 RETURN n"

    with pytest.raises(CypherSemanticError) as caught:
        parse(query)

    assert "unbound variable" in str(caught.value)
    assert caught.value.line == 2
    assert caught.value.column == 7
    assert caught.value.offset == query.index("missing")


def test_parser_supports_labels_inside_relationship_patterns():
    parsed = parse("MATCH (a:Label)-[:T]->(b:Target {active: true}) RETURN b")

    assert parsed.clauses[0].source.labels == ("Label",)
    assert parsed.clauses[0].hops[0].target.labels == ("Target",)
    assert parsed.clauses[0].hops[0].target.properties == (("active", True),)


def test_projection_and_order_fields_remain_runtime_compatible_strings():
    parsed = parse("MATCH (n) RETURN n.id AS id ORDER BY n.age DESC")

    assert parsed.projections == ("n.id",)
    assert parsed.projection_expressions == (PropertyRef("n", "id"),)
    assert parsed.order_by[0].expression == "n.age"
    assert parsed.order_by[0].expression_ast == PropertyRef("n", "age")
    assert parsed.order_by[0].descending is True


def test_variable_expression_ast_is_used_for_return_variable():
    parsed = parse("MATCH (n) RETURN n")

    assert parsed.projection_expressions == (Variable("n"),)


def test_parser_rejects_duplicate_projection_names():
    with pytest.raises(CypherSemanticError, match="duplicate column names"):
        parse("MATCH (n) RETURN n.id AS value, n.name AS value")


@pytest.mark.parametrize(
    "query",
    [
        "MATCH ()-->(b) RETURN b",
        "MATCH (a)-[]->() RETURN a",
        "MATCH (a)-[r]->(b) RETURN r",
        "MATCH (a)--(b) RETURN a, b",
        "MATCH (a)<--(b) RETURN a, b",
        "MATCH (a)-[:A]->(b)-[:B]->(c) RETURN c",
        "MATCH (a)-[:A]->(b), (b)-[:B]->(c) RETURN c",
    ],
)
def test_parser_supports_general_fixed_length_patterns(query):
    assert parse(query).clauses


def test_return_star_excludes_anonymous_pattern_elements():
    parsed = parse("MATCH (a)-->() RETURN *")

    assert parsed.returns == ("a",)


def test_parser_rejects_variable_reused_for_node_and_relationship():
    with pytest.raises(CypherSemanticError, match="both a node and a relationship"):
        parse("MATCH (x)-[x:T]->() RETURN x")
