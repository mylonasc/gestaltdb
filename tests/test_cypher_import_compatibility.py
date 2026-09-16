"""Compatibility coverage for the pre-package Cypher import paths."""

from gestaltdb import QueryResult as RootQueryResult
from gestaltdb.cypher import QueryResult as LegacyQueryResult
from gestaltdb.cypher import execute as legacy_execute
from gestaltdb.cypher import parse as legacy_parse
from gestaltdb.cypher import parse_ast as legacy_parse_ast
from gestaltdb.cypher import plan as legacy_plan
from gestaltdb.cypher_ast import Parameter as LegacyParameter
from gestaltdb.cypher_errors import CypherSemanticError as LegacySemanticError
from gestaltdb.cypher_expr import _cypher_equals as legacy_cypher_equals
from gestaltdb.cypher_functions import FunctionDef as LegacyFunctionDef
from gestaltdb.cypher_parser import CypherSemanticError as ParserSemanticError
from gestaltdb.cypher_plan import LogicalPlan as LegacyLogicalPlan
from gestaltdb.cypher_runtime import QueryContext as LegacyQueryContext
from gestaltdb.cypher_semantics import SymbolKind as LegacySymbolKind
from gestaltdb.cypher_write import WriteBatch as LegacyWriteBatch
from gestaltdb.query_engine.cypher import QueryResult
from gestaltdb.query_engine.cypher import execute, parse, parse_ast, plan
from gestaltdb.query_engine.cypher.ast import Parameter
from gestaltdb.query_engine.cypher.errors import CypherSemanticError
from gestaltdb.query_engine.cypher.expr import _cypher_equals
from gestaltdb.query_engine.cypher.functions import FunctionDef
from gestaltdb.query_engine.cypher.plan import LogicalPlan
from gestaltdb.query_engine.cypher.runtime import QueryContext
from gestaltdb.query_engine.cypher.semantics import SymbolKind
from gestaltdb.query_engine.cypher.write import WriteBatch


def test_public_cypher_imports_share_canonical_definitions():
    assert RootQueryResult is QueryResult
    assert LegacyQueryResult is QueryResult
    assert legacy_execute is execute
    assert legacy_parse is parse
    assert legacy_parse_ast is parse_ast
    assert legacy_plan is plan


def test_internal_cypher_imports_share_canonical_definitions():
    assert LegacyParameter is Parameter
    assert LegacySemanticError is CypherSemanticError
    assert ParserSemanticError is CypherSemanticError
    assert LegacyLogicalPlan is LogicalPlan
    assert LegacyQueryContext is QueryContext
    assert legacy_cypher_equals is _cypher_equals
    assert LegacyFunctionDef is FunctionDef
    assert LegacySymbolKind is SymbolKind
    assert LegacyWriteBatch is WriteBatch
