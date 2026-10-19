"""Isolated ontology solver comparisons; optional solvers are benchmark-only.

Run directly with ``python -m benchmarks.ontology`` so each fresh worker can
measure imports before loading GestaltDB. No benchmark imports tests or modifies
the production reasoner. Legacy timings intentionally include its persistence API.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
import importlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
import zipfile


BACKENDS = ("native", "native-proofs", "clingo", "owlrl", "claims-native", "legacy-lmdb")
WORKLOADS = ("hierarchy", "recursive", "duplicates", "dense")
TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
OWL = "http://www.w3.org/2002/07/owl#"


def _id(value):
    return "urn:onto-bench:" + str(value)


@dataclass(frozen=True)
class Workload:
    ontology: object
    rows: tuple[tuple[str, str, str], ...]
    expected: frozenset[tuple[str, str, str]]
    schema: tuple[tuple[str, str, str], ...]
    individuals: frozenset[str]
    classes: frozenset[str]
    properties: frozenset[str]


class PreflightLimit(ValueError):
    """Analytical input/output size would exceed the requested safety cap."""


def make_workload(name, size, *, max_input=100_000, max_facts=100_000):
    from gestaltdb.ontology.model import Domain, Ontology, SubClassOf, SubPropertyOf, Transitive

    if name not in WORKLOADS or isinstance(size, bool) or not isinstance(size, int) or not 2 <= size <= 10_000:
        raise ValueError("known workload and integer size between 2 and 10000 required")
    counts = {
        "hierarchy": (size, 5 * size),
        "recursive": (size - 1, size - 1 + sum((i + 1).bit_length() - 1 for i in range(1, size))),
        "duplicates": (8 * size, 3 * size),
        "dense": (size - 1, size * (size - 1) // 2),
    }
    inputs, outputs = counts[name]
    if inputs > max_input or outputs > max_facts:
        raise PreflightLimit(f"analytical input={inputs}, closure={outputs} exceeds caps {max_input}/{max_facts}")
    nodes = [_id(f"n{i}") for i in range(size)]
    p, q = _id("p"), _id("ancestor")
    classes, properties, schema, axioms, rows, expected = set(), set(), [], [], [], set()
    if name == "hierarchy":
        labels = [_id(f"C{i}") for i in range(5)]
        classes.update(labels)
        for left, right in zip(labels, labels[1:]):
            axioms.append(SubClassOf(left, right))
            schema.append((left, RDFS + "subClassOf", right))
        rows = [(node, TYPE, labels[0]) for node in nodes]
        expected = {(node, TYPE, label) for node in nodes for label in labels}
    elif name == "duplicates":
        c, d, target = _id("C"), _id("D"), _id("organization")
        nodes.append(target)
        classes.update((c, d))
        properties.add(p)
        axioms = [Domain(p, c), SubClassOf(c, d)]
        schema = [(p, RDFS + "domain", c), (c, RDFS + "subClassOf", d)]
        rows = [(node, p, target) for node in nodes[:-1] for _ in range(8)]
        expected = set(rows) | {(node, TYPE, label) for node in nodes[:-1] for label in (c, d)}
    elif name == "recursive":
        properties.update((p, q))
        axioms = [SubPropertyOf(p, q), Transitive(q)]
        schema = [(p, RDFS + "subPropertyOf", q), (q, TYPE, OWL + "TransitiveProperty")]
        rows = [(nodes[(i - 1) // 2], p, nodes[i]) for i in range(1, size)]
        expected = set(rows)
        for child in range(1, size):
            ancestor = child
            while ancestor:
                ancestor = (ancestor - 1) // 2
                expected.add((nodes[ancestor], q, nodes[child]))
    else:
        properties.add(p)
        axioms = [Transitive(p)]
        schema = [(p, TYPE, OWL + "TransitiveProperty")]
        rows = [(nodes[i], p, nodes[i + 1]) for i in range(size - 1)]
        expected = {(nodes[i], p, nodes[j]) for i in range(size) for j in range(i + 1, size)}
    assert len(rows) == inputs and len(expected) == outputs
    return Workload(Ontology(f"benchmark-{name}", axioms), tuple(rows), frozenset(expected),
                    tuple(schema), frozenset(nodes), frozenset(classes), frozenset(properties))


def _digest(facts):
    return hashlib.sha256(json.dumps(sorted(facts), separators=(",", ":")).encode()).hexdigest()


def _timed(metrics, name, operation):
    start = time.perf_counter()
    value = operation()
    metrics[name] = time.perf_counter() - start
    return value


def _peak_rss():
    try:
        import resource
    except ImportError:
        return None
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def _clingo_text(program, rows):
    from gestaltdb.reasoning.model import Variable

    terms = {term for row in rows for term in row}
    for rule in program.rules:
        for atom in (*rule.body, rule.head):
            terms.add(atom.predicate)
            terms.update(term for term in (atom.subject, atom.object) if isinstance(term, str))
    names = sorted(terms)
    ids = {name: index for index, name in enumerate(names)}
    lines = [f"t({ids[s]},{ids[p]},{ids[o]})." for s, p, o in rows]
    for rule in program.rules:
        variables = {}

        def render(atom):
            def term(value):
                if isinstance(value, Variable):
                    return variables.setdefault(value, f"V{len(variables)}")
                return str(ids[value])
            return f"t({term(atom.subject)},{ids[atom.predicate]},{term(atom.object)})"

        head = render(rule.head)
        lines.append(head + " :- " + ",".join(render(atom) for atom in rule.body) + ".")
    lines.append("#show t/3.")
    return "\n".join(lines), names


def _legacy_term(term):
    from gestaltdb.reasoning.model import Variable

    if isinstance(term, Variable):
        return "?" + term.name
    if term.startswith("?"):
        raise ValueError("legacy adapter cannot preserve question-mark-prefixed constants")
    return term


def _dependency_files(names):
    result = {}
    for name in names:
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        files = [distribution.locate_file(file) for file in distribution.files or ()]
        result[name] = {"version": distribution.version,
                        "installed_file_bytes": sum(path.stat().st_size for path in files if path.is_file())}
    return result


def _run_worker(spec):
    metrics = {}
    backend = spec["backend"]
    graph = None
    baseline_rss = None
    try:
        def common_import():
            import gestaltdb
            import gestaltdb.ontology.compiler
            import gestaltdb.reasoning.engine

        _timed(metrics, "common_import_s", common_import)
        from gestaltdb.ontology.compiler import compile_ontology
        from gestaltdb.reasoning.engine import DatalogEngine
        from gestaltdb.reasoning.model import Fact, ReasoningLimitError, ReasoningLimits
        from gestaltdb.reasoning.protocols import Evidence

        dependencies = {"clingo": ("clingo",), "owlrl": ("rdflib", "owlrl"),
                        "legacy-lmdb": ("lmdb",), "claims-native": ("lmdb",)}
        _timed(metrics, "backend_import_s", lambda: [importlib.import_module(module) for module in dependencies.get(backend, ())])
        baseline_rss = _peak_rss()
        workload = _timed(metrics, "fixture_s", lambda: make_workload(
            spec["workload"], spec["size"], max_input=spec["max_input"], max_facts=spec["max_facts"],
        ))
        start = time.perf_counter()
        compiled = _timed(metrics, "compile_s", lambda: compile_ontology(workload.ontology))
        metrics.update(load_s=0.0, ground_s=0.0, proof_query_s=None, proofs_truncated=None)
        with ExitStack() as resources:
            directory = resources.enter_context(tempfile.TemporaryDirectory(prefix="onto-benchmark-", dir=spec["temp_root"]))
            if backend.startswith("native"):
                engine = _timed(metrics, "init_s", DatalogEngine)
                facts = _timed(metrics, "conversion_s", lambda: [
                    Evidence(Fact(*row), f"evidence:{i}") for i, row in enumerate(workload.rows)
                ])
                limits = ReasoningLimits(max_input_facts=spec["max_input"], max_facts=spec["max_facts"],
                                         max_iterations=1000, max_candidate_rows=spec["max_candidates"],
                                         max_proof_nodes=spec["max_facts"], max_proof_edges=4 * spec["max_facts"])
                result = _timed(metrics, "execution_s", lambda: engine.evaluate(
                    compiled.program, facts, limits=limits, collect_proofs=backend == "native-proofs",
                ))
                decoded = _timed(metrics, "decode_s", lambda: {(f.subject, f.predicate, f.object) for f in result.facts})
                kind = "index_build_and_fixpoint_single_witness" if backend == "native-proofs" else "index_build_and_fixpoint"
            elif backend == "clingo":
                import clingo

                engine = _timed(metrics, "init_s", lambda: clingo.Control(["--warn=none"]))
                text, names = _timed(metrics, "conversion_s", lambda: _clingo_text(compiled.program, workload.rows))
                _timed(metrics, "load_s", lambda: engine.add("base", [], text))
                _timed(metrics, "ground_s", lambda: engine.ground([("base", [])]))
                models = []
                outcome = _timed(metrics, "execution_s", lambda: engine.solve(on_model=lambda model: models.append(model.symbols(shown=True))))
                if not outcome.satisfiable or len(models) != 1:
                    raise ValueError("positive program must have exactly one stable model")
                decoded = _timed(metrics, "decode_s", lambda: {
                    tuple(names[value.number] for value in symbol.arguments) for symbol in models[0]
                })
                kind = "positive_grounding_and_stable_model_no_proofs"
            elif backend == "owlrl":
                import owlrl
                import rdflib

                engine = _timed(metrics, "init_s", lambda: owlrl.DeductiveClosure(
                    owlrl.OWLRL_Semantics, axiomatic_triples=False, datatype_axioms=False,
                ))

                def rdf_graph():
                    value = rdflib.Graph()
                    for row in (*workload.rows, *workload.schema):
                        value.add(tuple(rdflib.URIRef(term) for term in row))
                    for label in workload.classes:
                        value.add((rdflib.URIRef(label), rdflib.RDF.type, rdflib.OWL.Class))
                    for prop in workload.properties:
                        value.add((rdflib.URIRef(prop), rdflib.RDF.type, rdflib.OWL.ObjectProperty))
                    return value

                data = _timed(metrics, "conversion_s", rdf_graph)
                _timed(metrics, "execution_s", lambda: engine.expand(data))
                decoded = _timed(metrics, "decode_s", lambda: {
                    (str(s), str(p), str(o)) for s, p, o in data
                    if str(s) in workload.individuals and (
                        (str(p) == TYPE and str(o) in workload.classes)
                        or (str(p) in workload.properties and str(o) in workload.individuals)
                    )
                })
                kind = "owlrl_closure_with_extra_vocabulary_no_proofs"
            elif backend in ("legacy-lmdb", "claims-native"):
                from gestaltdb.epistemic import Claim
                from gestaltdb.graphdb import GraphDB
                from gestaltdb.temporal import TemporalInstant, TemporalInterval
                from gestaltdb.versioning import ClaimVersionWrite

                graph = _timed(metrics, "init_s", lambda: GraphDB.create(
                    Path(directory) / "database", backend="lmdb", serializer="json",
                    backend_options={"map_size": 512 * 1024 * 1024},
                ))
                resources.callback(graph.close)

                def descriptors():
                    writes = [ClaimVersionWrite.assertion(Claim(s, p, o, "positive", f"evidence:{i}"),
                                                         TemporalInterval(TemporalInstant(0), TemporalInstant(100)))
                              for i, (s, p, o) in enumerate(workload.rows)]
                    rules = [(rule.rule_id, [tuple(_legacy_term(term) for term in (a.subject, a.predicate, a.object))
                                            for a in rule.body],
                              tuple(_legacy_term(term) for term in (rule.head.subject, rule.head.predicate, rule.head.object)))
                             for rule in compiled.program.rules] if backend == "legacy-lmdb" else []
                    return writes, rules

                writes, rules = _timed(metrics, "conversion_s", descriptors)

                def load():
                    graph.commit_versions(writes)
                    for rule_id, when, then in rules:
                        graph.create_rule(rule_id, when=when, then=then)

                _timed(metrics, "load_s", load)
                if backend == "legacy-lmdb":
                    result = _timed(metrics, "execution_s", lambda: graph.run_rules(
                        as_of=50, max_iterations=1000, max_derivations=spec["max_facts"],
                        max_justifications=spec["max_justifications"],
                    ))
                    decoded = _timed(metrics, "decode_s", lambda: {
                        (v.claim.subject, v.claim.predicate, v.claim.object)
                        for v in graph.iter_claims_as_of(valid_time=50, world="default", polarity="positive", object_kind="entity")
                    })
                    metrics["justifications"] = result.justification_count
                    kind = "extract_fixpoint_all_justifications_and_temporal_publication"
                else:
                    from gestaltdb.reasoning.adapters.claims import (
                        ClaimMapping, ClaimReasoningResult, ClaimSelection, ClaimSourceLimits, PinnedClaimSource,
                    )

                    selection, mapping = ClaimSelection(), ClaimMapping()

                    def pin():
                        view = resources.enter_context(graph.read_view(valid_time=50))
                        return resources.enter_context(PinnedClaimSource(
                            view, world="default", ontology_revision=compiled.revision_id,
                            selection=selection, mapping=mapping,
                            limits=ClaimSourceLimits(spec["max_input"]),
                        ))

                    source = _timed(metrics, "pin_verify_s", pin)
                    selected = _timed(metrics, "extraction_s", lambda: list(source.iter_evidence()))
                    result = _timed(metrics, "execution_s", lambda: DatalogEngine().evaluate(
                        compiled.program, selected, limits=ReasoningLimits(
                            max_input_facts=spec["max_input"], max_facts=spec["max_facts"],
                            max_iterations=1000, max_candidate_rows=spec["max_candidates"],
                        ),
                    ))

                    def verify_result():
                        source.verify_source()
                        return ClaimReasoningResult(source.context, source.provenance, compiled, selection, mapping, result)

                    scoped = _timed(metrics, "postverify_s", verify_result)
                    decoded = _timed(metrics, "decode_s", lambda: {(f.subject, f.predicate, f.object) for f in scoped.facts})
                    metrics["read_reasoning_s"] = sum(metrics[key] for key in (
                        "pin_verify_s", "extraction_s", "execution_s", "postverify_s", "decode_s",
                    ))
                    kind = "staged_verified_claim_extraction_fixpoint_and_context_result"
                metrics["database_logical_bytes"] = sum(path.stat().st_size for path in Path(directory).rglob("*") if path.is_file())
            else:
                raise ValueError("unknown backend")
            metrics["pipeline_s"] = time.perf_counter() - start
            if backend == "native-proofs":
                metrics["proofs_truncated"] = result.proofs.truncated
                base_rows = set(workload.rows)
                root = next((fact for fact in reversed(result.facts) if (fact.subject, fact.predicate, fact.object) not in base_rows), None)
                if root is not None:
                    explanation = _timed(metrics, "proof_query_s", lambda: result.explain(root, max_nodes=spec["max_facts"]))
                    metrics["proof_query_nodes"] = len(explanation.nodes)
            actual_digest = _timed(metrics, "validation_s", lambda: _digest(decoded))
            expected_digest = _digest(workload.expected)
            if decoded != workload.expected:
                raise ValueError(f"closure mismatch: actual={len(decoded)}, expected={len(workload.expected)}")
            return {"status": "ok", "metrics": metrics, "execution_kind": kind,
                    "input_rows": len(workload.rows), "unique_input_facts": len(set(workload.rows)),
                    "closure_facts": len(decoded), "closure_digest": actual_digest, "expected_digest": expected_digest,
                    "rss_peak_bytes": _peak_rss(), "rss_import_high_water_bytes": baseline_rss}
    except ModuleNotFoundError as exc:
        optional = {"clingo", "owlrl", "rdflib", "lmdb"}
        return {"status": "unavailable" if exc.name in optional else "error", "reason": str(exc), "metrics": metrics}
    except PreflightLimit as exc:
        return {"status": "limited", "limit_stage": "analytical_preflight", "reason": str(exc), "metrics": metrics}
    except Exception as exc:
        from gestaltdb.reasoning.model import ReasoningLimitError
        from gestaltdb.rules import RuleEvaluationLimitError

        return {"status": "limited" if isinstance(exc, (ReasoningLimitError, RuleEvaluationLimitError)) else "error",
                "reason": f"{type(exc).__name__}: {exc}", "metrics": metrics, "rss_peak_bytes": _peak_rss()}


def footprint(wheel=None):
    import gestaltdb

    root = Path(gestaltdb.__file__).parent
    files = [*root.joinpath("reasoning").rglob("*.py"), *root.joinpath("ontology").rglob("*.py")]
    result = {
        "feature_python_files": len(files), "feature_source_bytes": sum(path.stat().st_size for path in files),
        "feature_sources_sha256": hashlib.sha256(json.dumps([
            (str(path.relative_to(root)), hashlib.sha256(path.read_bytes()).hexdigest()) for path in sorted(files)
        ], separators=(",", ":")).encode()).hexdigest(),
        "common_existing_dependencies": _dependency_files(("numpy", "lark")),
        "clingo_runtime_chain": _dependency_files(("clingo", "cffi", "pycparser")),
        "owlrl_runtime_chain": _dependency_files(("owlrl", "rdflib", "pyparsing", "isodate")),
        "legacy_backend": _dependency_files(("lmdb",)),
    }
    if wheel:
        with zipfile.ZipFile(wheel) as archive:
            entries = [entry for entry in archive.infolist() if entry.filename.startswith(("gestaltdb/reasoning/", "gestaltdb/ontology/"))]
            result["wheel"] = {"name": Path(wheel).name, "artifact_bytes": Path(wheel).stat().st_size,
                               "feature_uncompressed_bytes": sum(entry.file_size for entry in entries),
                               "feature_compressed_bytes": sum(entry.compress_size for entry in entries)}
    return result


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backends", nargs="+", choices=BACKENDS, default=list(BACKENDS))
    parser.add_argument("--workloads", nargs="+", choices=WORKLOADS, default=list(WORKLOADS))
    parser.add_argument("--sizes", nargs="+", type=int, default=[16, 48])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--max-input", type=int, default=100_000)
    parser.add_argument("--max-facts", type=int, default=100_000)
    parser.add_argument("--max-candidates", type=int, default=2_000_000)
    parser.add_argument("--max-justifications", type=int, default=100_000)
    parser.add_argument("--temp-root", default=tempfile.gettempdir())
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    return parser


def run_benchmark(args):
    if (args.repeats < 1 or not math.isfinite(args.timeout) or args.timeout <= 0
            or not args.sizes or not args.backends or not args.workloads
            or any(not 2 <= size <= 10_000 for size in args.sizes)):
        raise ValueError("positive repeat/timeout values and sizes between 2 and 10000 required")
    if any(getattr(args, name) < 1 for name in ("max_input", "max_facts", "max_candidates", "max_justifications")):
        raise ValueError("resource counters must be positive")
    rows = []
    for workload in args.workloads:
        for size in args.sizes:
            for backend in args.backends:
                for repeat in range(args.repeats):
                    spec = {name: getattr(args, name) for name in ("max_input", "max_facts", "max_candidates", "max_justifications", "temp_root")}
                    spec.update(backend=backend, workload=workload, size=size, repeat=repeat)
                    start = time.perf_counter()
                    try:
                        # The parent owns cleanup even if a timed-out worker is killed.
                        with tempfile.TemporaryDirectory(prefix="onto-run-", dir=args.temp_root) as directory:
                            spec["temp_root"] = directory
                            process = subprocess.run([sys.executable, "-m", "benchmarks.ontology", "--worker", json.dumps(spec)],
                                                     capture_output=True, text=True, timeout=args.timeout,
                                                     env={**os.environ, "PYTHONHASHSEED": "0"})
                        value = json.loads(process.stdout) if process.returncode == 0 else {
                            "status": "error", "reason": process.stderr[-4000:], "returncode": process.returncode,
                        }
                    except subprocess.TimeoutExpired:
                        value = {"status": "timeout", "reason": f"worker exceeded {args.timeout}s"}
                    except ValueError as exc:
                        value = {"status": "error", "reason": str(exc)}
                    rows.append({**spec, **value, "temp_root": args.temp_root, "worker_wall_s": time.perf_counter() - start})
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True,
                                           cwd=Path(__file__).resolve().parents[1], stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        revision = None
    report = {"schema_version": 1, "python": sys.version, "platform": platform.platform(), "git_revision": revision,
              "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "machine": platform.machine(), "processor": platform.processor(), "logical_cpus": os.cpu_count(),
              "command": sys.argv, "footprint": footprint(args.wheel), "rows": rows}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"rows": len(rows), "statuses": {status: sum(row["status"] == status for row in rows)
                                                     for status in sorted({row["status"] for row in rows})},
                      "output": None if args.output is None else str(args.output)}, sort_keys=True))
    return report


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.worker:
        print(json.dumps(_run_worker(json.loads(args.worker)), sort_keys=True))
        return
    report = run_benchmark(args)
    if any(row["status"] == "error" for row in report["rows"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
