"""Fresh-worker pinned frontier/executor/native crossover, not auto-selection.

Native comparisons are benchmark-only, quiescent assertion-only GraphDB fixtures.
They deliberately retain canonical commit authentication and caller-owned decode;
they are NOT a general history winner-selection or production claim adapter.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import resource
import statistics
import subprocess
import sys
import tempfile
import time


METHODS = ("frontier", "indexed-python", "indexed-scc", "polars-prototype", "native-decoded", "native-locator",
           "native-authenticated")
BENCHMARK_NATIVE_METHODS = ("native-decoded", "native-locator")


def checkpoint(stage):
    """Small flushed worker diagnostics, never substitute partial timings."""
    print("PIPELINE_PROGRESS " + json.dumps({"stage": stage}), file=sys.stderr, flush=True)


def last_checkpoint(stderr):
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    found = None
    for line in (stderr or "").splitlines():
        if line.startswith("PIPELINE_PROGRESS "):
            try:
                value = json.loads(line.removeprefix("PIPELINE_PROGRESS "))
            except ValueError:
                continue
            if isinstance(value, dict) and isinstance(value.get("stage"), str):
                found = value["stage"]
    return found


def entity(index):
    # Variable but bounded IDs respect the backend-independent 511-byte key cap.
    return f"n{index:07d}-" + "v" * (index % 23)


def triples(rows, shape):
    chain = min(rows, 128 if shape == "dense" else 8)
    for i in range(rows):
        if i < chain:
            yield entity(i), "edge", entity(i + 1)
        elif shape == "sparse":
            group, offset = divmod(i - chain, 3)
            yield f"group{group}-{offset}", "edge", f"group{group}-{offset+1}"
        else:
            yield entity(2 * i + 1000), "edge", entity(2 * i + 1001)


def digest(items):
    return hashlib.sha256(json.dumps(sorted(items), separators=(",", ":")).encode()).hexdigest()


def analytical_closure(rows, shape):
    """Fixture-specific independent set oracle, never timed as execution."""
    facts = set(triples(rows, shape))
    chain = min(rows, 128 if shape == "dense" else 8)
    facts.update((entity(i), "reach", entity(j)) for i in range(chain) for j in range(i + 1, chain + 1))
    if shape == "sparse":
        for start in range(chain, rows, 3):
            group = (start - chain) // 3
            size = min(3, rows - start)
            facts.update((f"group{group}-{i}", "reach", f"group{group}-{j}")
                         for i in range(size) for j in range(i + 1, size + 1))
    else:
        chain_heads = {entity(i) for i in range(chain)}
        facts.update((s, "reach", o) for s, _, o in triples(rows, shape) if s not in chain_heads)
    return facts


def fixture(path, rows, shape, backend):
    from gestaltdb.epistemic import Claim
    from gestaltdb.graphdb import GraphDB
    from gestaltdb.versioning import ClaimVersionWrite

    options = {"map_size": max(256 * 1024**2, rows * 32768)} if backend == "lmdb" else {}
    graph = GraphDB.create(path, backend=backend, serializer="json", backend_options=options)
    records, pending = [], []
    start = time.perf_counter()
    try:
        for i, (s, p, o) in enumerate(triples(rows, shape)):
            claim = Claim(s, p, o, "positive", "benchmark", provenance={"payload": "p" * (32 + i % 257)})
            pending.append(ClaimVersionWrite.assertion(claim, (0, 100)))
            if len(pending) == 1000 or i == rows - 1:
                commit = graph.temporal.commit_versions(pending)
                records.extend((v.claim.subject, v.claim.predicate, v.claim.object, v.version_id) for v in commit.versions)
                pending.clear()
        return {"status": "ok", "evidence_digest": digest(records), "load_s": time.perf_counter() - start,
                "rows": rows, "shape": shape, "backend": backend, "backend_options": options}
    finally:
        graph.close()


def native_evidence(graph, view, method, rows):
    """Restricted benchmark decoder with canonical source/commit checks.

    Keep every admission in the measured extraction stage. The fixture has one
    assertion per claim, no corrections/retractions/gaps/other entity kinds.
    Writer guard + before/after horizon checks enforce quiescence; do not reuse
    this shortcut as a general historical admission contract.
    """
    from gestaltdb.graphdb import _TEMPORAL_CLAIM_DIMENSION_INDEX, _TEMPORAL_RECORD_PREFIX
    from gestaltdb.kvstores import _range_index_prefix
    from gestaltdb.reasoning.adapters.native import DecodedRow, NativeKeyScan, iter_decoded_evidence, iter_locator_keys
    from gestaltdb.reasoning.model import Fact
    from gestaltdb.reasoning.protocols import Evidence
    from gestaltdb.versioning import ClaimVersion, VersionOperation

    provenance = view.provenance
    provenance.verify_source(graph)
    admitted = {}
    scanned = 0

    def admit(version):
        if (not isinstance(version, ClaimVersion) or version.operation is not VersionOperation.ASSERT
                or not version.valid.contains(view.valid_time)
                or version.claim.world != "default" or version.claim.agent != "benchmark"
                or not 0 < version.commit_id <= provenance.commit_horizon):
            raise ValueError("native benchmark fixture is not an active assertion-only prefix")
        if version.commit_id not in admitted:
            commit = graph.temporal.get_commit_cached(version.commit_id)
            if commit is None:
                raise ValueError("native fixture commit is not visible")
            admitted[version.commit_id] = commit.versions
        if admitted[version.commit_id][version.commit_ordinal] != version:
            raise ValueError("native version differs from authenticated commit")
        return version

    evidence = []
    if method == "native-decoded":
        prefix = graph.store._key(b"M", _TEMPORAL_RECORD_PREFIX)
        def decode(key, value):
            nonlocal scanned
            scanned += 1
            version = admit(graph.temporal.decode_version(value))
            claim = version.claim
            return DecodedRow(claim.subject, claim.predicate, claim.object, version.version_id, version.version_id)
        with NativeKeyScan(graph.store, prefix=prefix, batch_rows=1024, max_rows=rows + 1) as scan:
            for batch in iter_decoded_evidence(scan, decode, visibility=provenance.token, max_rows=rows):
                evidence.extend(Evidence(Fact(row.subject, row.predicate, row.object), row.version_id) for row in batch.rows)
    else:
        # Actual temporal predicate-index keys, not synthetic locator payloads.
        graph.temporal.ensure_indexes(provenance.commit_horizon)
        name = graph.temporal.index_name(_TEMPORAL_CLAIM_DIMENSION_INDEX, graph.temporal.index_generation())
        prefix = _range_index_prefix(name, [b"predicate", b'"edge"'])
        with NativeKeyScan(graph.store, prefix=prefix, batch_rows=1024, max_rows=rows + 1) as scan:
            for key in iter_locator_keys(scan, max_rows=rows):
                scanned += 1
                encoded = key.rsplit(b"\x1f", 1)[1]
                locator = base64.urlsafe_b64decode(encoded + b"=" * (-len(encoded) % 4))
                commit_id, ordinal = graph.temporal.decode_locator(locator)
                # Point-materialize the record, then authenticate its exact commit.
                value = graph.store.get_metadata(graph.temporal.record_key(commit_id, ordinal))
                version = admit(graph.temporal.decode_version(value))
                claim = version.claim
                evidence.append(Evidence(Fact(claim.subject, claim.predicate, claim.object), version.version_id))
    provenance.verify_source(graph)
    if graph.temporal.system_horizon() != provenance.commit_horizon:
        raise ValueError("native benchmark fixture moved")
    return evidence, scanned


def worker(path, rows, shape, method, cache, proofs=False, polars_max_intermediate_rows=None):
    if polars_max_intermediate_rows is not None:
        if (isinstance(polars_max_intermediate_rows, bool) or not isinstance(polars_max_intermediate_rows, int)
                or polars_max_intermediate_rows < 1 or method != "polars-prototype" or proofs):
            raise ValueError("positive polars_max_intermediate_rows requires a Polars closure worker")
    effective_cap = (100_000 if polars_max_intermediate_rows is None else polars_max_intermediate_rows) if method == "polars-prototype" else None
    checkpoint("imports")
    started = time.perf_counter()
    from gestaltdb.graphdb import GraphDB
    from gestaltdb.reasoning import Atom, Fact, Program, Rule, Variable, ReasoningLimits
    from gestaltdb.reasoning.model import ReasoningLimitError
    from gestaltdb.reasoning.adapters.batched import PinnedBatchedClaimSource
    from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection, ClaimSourceLimits
    from gestaltdb.reasoning.adapters.execution import execute_closure
    from gestaltdb.reasoning.adapters.frontier import answer_pinned_claims
    from gestaltdb.reasoning.executors import ExecutorRequest, INDEXED_SCC
    from gestaltdb.reasoning.extraction import ExtractionRequest
    from gestaltdb.reasoning.plans import PreparedProgram
    from gestaltdb.reasoning.relational import CompilationIdentity

    import_s = time.perf_counter() - started
    if proofs and method not in ("indexed-python", "indexed-scc"):
        raise ValueError("proof measurements require indexed engines, separate from closure")
    begin = time.perf_counter()
    x, y, z = Variable("x"), Variable("y"), Variable("z")
    program = Program([Rule("base", [Atom(x, "edge", y)], Atom(x, "reach", y)),
                       Rule("step", [Atom(x, "reach", y), Atom(y, "edge", z)], Atom(x, "reach", z))])
    limits = ReasoningLimits(max_input_facts=rows + 1, max_facts=max(20000, rows * 5),
                             max_candidate_rows=max(10000000, rows * 50), max_iterations=1000)
    compile_s = time.perf_counter() - begin
    begin = time.perf_counter()
    checkpoint("reopen")
    graph = GraphDB.open(path)
    reopen_s = time.perf_counter() - begin

    def measure():
        metrics = {}
        begin = time.perf_counter()
        checkpoint("read-view")
        with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=50) as view:
            metrics["view_open_s"] = time.perf_counter() - begin
            if method == "frontier":
                begin = time.perf_counter()
                result = answer_pinned_claims(
                    view, program, Atom(entity(0), "reach", y), world="default", ontology_revision="benchmark-v1",
                    selection=ClaimSelection(), mapping=ClaimMapping(), limits=limits,
                    source_limits=ClaimSourceLimits(rows + 1))
                metrics["interleaved_read_evaluate_verify_s"] = time.perf_counter() - begin
                evidence, answers = result.answer.evidence, result.answer.facts
                scanned, known, work = result.scanned_claims, result.answer.known_facts, result.answer.candidate_rows
                requests = result.answer.requests
            else:
                begin = time.perf_counter()
                checkpoint("extraction")
                if method in BENCHMARK_NATIVE_METHODS:
                    evidence, scanned = native_evidence(graph, view, method, rows)
                    context = None
                else:
                    source_type, source_options = PinnedBatchedClaimSource, {}
                    if method == "native-authenticated":
                        from gestaltdb.reasoning.adapters.native_claims import PinnedNativeClaimSource, NativeClaimLimits
                        source_type = PinnedNativeClaimSource
                        source_options["native_limits"] = NativeClaimLimits(
                            max_raw_rows=rows + 1, max_logical_claims=rows + 1)
                    with source_type(
                        view, request=ExtractionRequest("default", predicates=("edge", "reach"), max_rows=rows + 1),
                        ontology_revision="benchmark-v1", selection=ClaimSelection(), mapping=ClaimMapping(),
                        limits=ClaimSourceLimits(rows + 1), **source_options) as source:
                        evidence = [e for batch in source.iter_batches() for e in batch.evidence]
                        scanned, context = source.profile.scanned, source.context
                        if method == "native-authenticated":
                            profile = source.native_profile
                            metrics.update(native_raw_rows=profile.raw_rows, native_visible_records=profile.visible_records,
                                native_logical_claims=profile.logical_claims, native_resolved_claims=profile.resolved_claims)
                metrics["extract_decode_verify_s"] = time.perf_counter() - begin
                begin = time.perf_counter()
                plan = None
                if method == "indexed-scc":
                    plan = PreparedProgram(program, CompilationIdentity(
                        program.digest, context, "external-strings-v1", "positive-binary-entity-v1", INDEXED_SCC))
                metrics["prepare_s"] = time.perf_counter() - begin
                begin = time.perf_counter()
                checkpoint("evaluation")
                preferred = method if not method.startswith("native-") else "indexed-python"
                result = execute_closure(program, evidence, plan=plan, context=context, limits=limits,
                                         polars_max_intermediate_rows=polars_max_intermediate_rows,
                                         collect_proofs=proofs, executor_request=ExecutorRequest(
                                             preferred=preferred, mode="proofs" if proofs else "closure",
                                             polars_available=method == "polars-prototype"))
                metrics["evaluate_convert_s"] = time.perf_counter() - begin
                if result.polars_run is not None:
                    metrics.update({"polars_" + key: value for key, value in result.polars_run.metrics.items()})
                answers = result.result.match(subject=entity(0), predicate="reach")
                known, work, requests = len(result.result.facts), None, None
                if proofs and result.result.proofs is None:
                    raise ValueError("missing requested proof trace")
            begin = time.perf_counter()
            checkpoint("post-verification")
            view.provenance.verify_source(graph)
            metrics["post_verify_s"] = time.perf_counter() - begin
        closure = None if method == "frontier" else result.result.facts
        proof_trace = None if method == "frontier" else result.result.proofs
        return metrics, tuple(answers), tuple(evidence), scanned, known, work, requests, closure, proof_trace

    try:
        # The fixture is read-only for all workers; keep the writer guard for the
        # benchmark-only native decoder's explicitly quiescent contract.
        with graph.temporal.write_guard():
            if cache == "warm":
                measure()
            begin = time.perf_counter()
            metrics, answers, evidence, scanned, known, work, requests, closure, proof_trace = measure()
            metrics["query_pipeline_s"] = time.perf_counter() - begin
        metrics.update(import_s=import_s, compile_s=compile_s, reopen_s=reopen_s)
        metrics["total_pipeline_s"] = import_s + compile_s + reopen_s + metrics["query_pipeline_s"]
        checkpoint("parity-validation")
        chain = min(rows, 128 if shape == "dense" else 8)
        expected = tuple(sorted(Fact(entity(0), "reach", entity(i)) for i in range(1, chain + 1)))
        if answers != expected:
            raise ValueError("analytical goal parity failed")
        manifest = json.loads((Path(path).parent / "fixture.json").read_text())
        evidence_hash = digest((e.fact.subject, e.fact.predicate, e.fact.object, e.reference_id) for e in evidence)
        if method != "frontier" and evidence_hash != manifest["evidence_digest"]:
            raise ValueError("exact evidence parity failed")
        if method == "frontier" and len(evidence) != chain:
            raise ValueError("frontier evidence cardinality failed")
        closure_hash = None if closure is None else digest((f.subject, f.predicate, f.object) for f in closure)
        if closure is not None and closure_hash != digest(analytical_closure(rows, shape)):
            raise ValueError("analytical full-closure parity failed")
        # Conversion is included in evaluate_convert_s, never presented as free.
        return {"status": "ok", "method": method, "mode": "proofs" if proofs else "closure",
                "source_contract": ("authenticated-native-full-history" if method == "native-authenticated" else
                                    "benchmark-only-quiescent-assertions" if method in BENCHMARK_NATIVE_METHODS else
                                    "authenticated-pinned-claims"),
                "rows": rows, "shape": shape, "cache": cache,
                "polars_max_intermediate_rows": effective_cap,
                "cache_boundary": "first extraction on reopened handle" if cache == "first" else "same-method primed extraction; OS cache uncontrolled",
                "metrics": metrics, "answers": len(answers), "answer_digest": digest((f.subject, f.predicate, f.object) for f in answers),
                "evidence_rows": len(evidence), "evidence_digest": evidence_hash, "scanned_claims_or_raw_rows": scanned,
                "known_facts": known, "scheduler_work": work, "requests": requests,
                "closure_digest": closure_hash,
                "proof_nodes": None if proof_trace is None else len(proof_trace.nodes),
                "proof_truncated": None if proof_trace is None else proof_trace.truncated,
                "polars_threads": sys.modules["polars"].thread_pool_size() if "polars" in sys.modules else None,
                "rss_peak_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024),
                "rss_boundary": "worker high-water, includes warmup and parity validation; not query-only RSS",
                "conversion_boundary": "included in evaluate_convert_s; no zero-copy claim",
                "bytes_copied": None, "bytes_copied_boundary": "not instrumented, not assumed zero"}
    except ReasoningLimitError as exc:
        return {"status": "bounded", "method": method, "rows": rows, "shape": shape,
                "cache": cache, "mode": "proofs" if proofs else "closure", "reason": str(exc),
                "polars_max_intermediate_rows": effective_cap,
                "no_result": True, "metrics": {"elapsed_to_limit_s": time.perf_counter() - started}}
    finally:
        graph.close()


def call_worker(command, timeout):
    start = time.perf_counter()
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return {"status": "timeout", "timeout_s": timeout, "last_stage": last_checkpoint(exc.stderr)}
    if result.returncode:
        return {"status": "error", "stderr": result.stderr[-4000:], "stdout": result.stdout[-1000:],
                "last_stage": last_checkpoint(result.stderr)}
    try:
        row = json.loads(result.stdout)
    except ValueError:
        return {"status": "error", "stdout": result.stdout[-1000:]}
    if not isinstance(row, dict) or row.get("status") not in ("ok", "bounded"):
        return {"status": "error", "reason": "worker returned incomplete result", "stdout": result.stdout[-1000:]}
    row["worker_wall_s"] = time.perf_counter() - start
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=1000)
    parser.add_argument("--shape", choices=("selective", "sparse", "dense"), default="selective")
    parser.add_argument("--backend", choices=("lmdb", "leveldb", "pyrex"), default="lmdb")
    parser.add_argument("--methods", nargs="+", choices=METHODS, default=list(METHODS[:4]))
    parser.add_argument("--caches", nargs="+", choices=("first", "warm"), default=["first", "warm"])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--proofs", action="store_true")
    parser.add_argument("--polars-max-intermediate-rows", type=int,
                        help="explicit physical row budget for Polars closure workers only; default unchanged")
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--worker", choices=("load", *METHODS))
    args = parser.parse_args()
    if not 1 <= args.rows <= 1000000 or args.repeats < 1 or args.timeout <= 0:
        parser.error("positive rows <=1000000, repeats and timeout required")
    if args.proofs and any(m not in ("indexed-python", "indexed-scc") for m in args.methods):
        parser.error("proofs require only indexed methods")
    if args.polars_max_intermediate_rows is not None:
        if args.polars_max_intermediate_rows < 1 or args.proofs:
            parser.error("positive Polars closure intermediate budget required")
        if args.worker not in (None, "polars-prototype") or (args.worker is None and "polars-prototype" not in args.methods):
            parser.error("intermediate budget requires a Polars method/worker")
    active_methods = (args.worker,) if args.worker is not None else args.methods
    if any(m.startswith("native-") for m in active_methods) and args.backend != "pyrex":
        parser.error("native methods require plain PyRex")
    if args.worker:
        result = fixture(args.fixture, args.rows, args.shape, args.backend) if args.worker == "load" else worker(
            args.fixture, args.rows, args.shape, args.worker, args.caches[0], args.proofs,
            args.polars_max_intermediate_rows)
        print(json.dumps(result))
        return
    os.environ.update(POLARS_MAX_THREADS="1", OMP_NUM_THREADS="1")
    root = Path(__file__).resolve().parents[1]
    report = {"schema_version": 2, "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
              "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip()),
              "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "python": sys.version, "platform": platform.platform(), "dependencies": {},
              "cpu_count": os.cpu_count(),
              "configuration": {"rows": args.rows, "shape": args.shape, "backend": args.backend,
                                "methods": args.methods, "caches": args.caches, "repeats": args.repeats,
                                 "timeout_s": args.timeout, "proofs": args.proofs,
                                 "polars_max_intermediate_rows": args.polars_max_intermediate_rows},
              "threads": {k: os.environ[k] for k in ("POLARS_MAX_THREADS", "OMP_NUM_THREADS")},
              "os_cache": "uncontrolled; first is not OS-cold", "samples": [], "complete": False,
              "decision": "no automatic executor/native adoption; workload-specific evidence only"}
    for package in ("lmdb", "plyvel", "pyrex-rocksdb", "pyarrow", "polars"):
        try:
            report["dependencies"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            report["dependencies"][package] = None
    def save():
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2) + "\n")
    # Prefer the harness-approved root locally; ordinary CI/users need not have
    # OpenCode's pre-created directory. Let tempfile honor their normal root.
    temporary_root = "/tmp/opencode" if Path("/tmp/opencode").is_dir() else None
    with tempfile.TemporaryDirectory(prefix="re342-", dir=temporary_root) as directory:
        path = Path(directory) / "db"
        common = [sys.executable, "-m", "benchmarks.reasoning_frontier_pipeline", "--fixture", str(path),
                  "--rows", str(args.rows), "--shape", args.shape, "--backend", args.backend]
        loaded = call_worker([*common, "--worker", "load"], args.timeout)
        report["fixture"] = loaded
        save()
        if loaded.get("status") != "ok":
            raise SystemExit("fixture worker failed; diagnostics saved")
        (Path(directory) / "fixture.json").write_text(json.dumps(loaded))
        for repeat in range(args.repeats):
            for cache in args.caches:
                for method in args.methods:
                    command = [*common, "--worker", method, "--caches", cache]
                    if method == "polars-prototype" and args.polars_max_intermediate_rows is not None:
                        command += ["--polars-max-intermediate-rows", str(args.polars_max_intermediate_rows)]
                    if args.proofs:
                        command += ["--proofs", "--methods", *args.methods]
                    row = call_worker(command, args.timeout)
                    row.update(repeat=repeat, method=method, cache=cache)
                    report["samples"].append(row)
                    save()
                    if row.get("status") not in ("ok", "bounded"):
                        raise SystemExit(f"worker failed: {method}; diagnostics saved")
        report["complete"] = True
        report["all_parity_verified"] = all(s["status"] == "ok" for s in report["samples"])
        report["summary"] = {}
        for method in args.methods:
            for cache in args.caches:
                group = [s for s in report["samples"] if s["method"] == method and s["cache"] == cache and s["status"] == "ok"]
                if not group:
                    report["summary"][method + "/" + cache] = {"status": "bounded; no validated result"}
                    continue
                report["summary"][method + "/" + cache] = {
                    key: {"p50": statistics.median(values), "p95": values[math.ceil(.95 * len(values)) - 1]}
                    for key in group[0]["metrics"]
                    for values in [sorted(s["metrics"][key] for s in group)]}
        save()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
