"""Execute high-level help() examples in fresh namespaces and disposable storage.

Backend examples use real optional stores when installed; release CI requires
those dependencies separately. No injected graph/program/facts fixtures or
doctest SKIP directives are allowed in these self-contained workflows.
"""

import doctest
import importlib
import inspect

import pytest


WORKFLOWS = [
    ("gestaltdb.kvstores", "LMDBStore", ("lmdb",)),
    ("gestaltdb.kvstores", "LevelDBStore", ("plyvel",)),
    ("gestaltdb.kvstores", "PyRexStore", ("pyrex",)),
    ("gestaltdb.graphdb", "GraphDB", ("lmdb",)),
    ("gestaltdb.graphdb", "GraphDB.ingest_arrow", ("lmdb", "pyarrow")),
    ("gestaltdb.graphdb", "GraphDB.read_view", ("lmdb",)),
    ("gestaltdb.graphdb", "GraphDB.run_rules", ("lmdb",)),
    ("gestaltdb.serializers", "JSONSerializer", ()),
    ("gestaltdb.sampling.engine", "SamplerEngine", ()),
    ("gestaltdb.sampling.snapshot", "SamplerSnapshot", ()),
    ("gestaltdb.ontology.compiler", "compile_ontology", ()),
    ("gestaltdb.reasoning.engine", "DatalogEngine", ()),
    ("gestaltdb.reasoning.typed", "GenericEngine", ()),
    ("gestaltdb.reasoning.typed", "GenericEngine.evaluate_stratified", ()),
    ("gestaltdb.reasoning.maintenance", "rematerialize", ()),
    ("gestaltdb.reasoning.temporal_closure", "evaluate_range", ()),
    ("gestaltdb.reasoning.demand", "answer", ()),
    ("gestaltdb.reasoning.validation", "validate", ()),
    ("gestaltdb.reasoning.adapters.graph", "PinnedGraphSource", ("lmdb",)),
    ("gestaltdb.reasoning.adapters.sessions", "start_session", ("lmdb",)),
    ("gestaltdb.reasoning.adapters.execution", "execute_closure", ()),
    ("gestaltdb.viz.api", "visualize_nodes_edges", ()),
]


@pytest.mark.parametrize("module_name,qualname,requirements", WORKFLOWS,
                         ids=[f"{module}.{name}" for module, name, _ in WORKFLOWS])
def test_public_workflow_docstring(module_name, qualname, requirements):
    for requirement in requirements:
        pytest.importorskip(requirement)
    target = importlib.import_module(module_name)
    for part in qualname.split("."):
        target = getattr(target, part)
    doc = inspect.getdoc(target)
    assert doc, qualname
    test = doctest.DocTestParser().get_doctest(
        doc, {}, f"{module_name}.{qualname}", inspect.getsourcefile(target), 0)
    assert test.examples, f"No executable example for {qualname}"
    assert not any(item.options.get(doctest.SKIP) for item in test.examples)
    failures = []
    result = doctest.DocTestRunner().run(test, out=failures.append)
    assert result.failed == 0, "".join(failures)
    assert result.attempted == len(test.examples)
