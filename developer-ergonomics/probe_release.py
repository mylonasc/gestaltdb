"""Replay a library-user exploration against an installed PyPI release.

Run with an isolated Python, from outside the checkout. No implementation-source
inspection, private attributes, repository fixtures, or internal storage access.
Output is JSON; exceptions are observations, not assertions of desired behavior.
"""

import contextlib
import importlib.metadata
import inspect
import io
import json
from pathlib import Path
import platform
import tempfile

from gestaltdb import agent_docs
from gestaltdb.graphdb import Edge, GraphDB, Node


def main():
    observations = []

    def observe(name, action):
        try:
            value = action()
            observations.append({"name": name, "value": value})
        except Exception as error:
            observations.append({
                "name": name,
                "exception": type(error).__name__,
                "message": str(error),
            })

    topics = ["quickstart", "backends", "cypher", "indexing", "sampling",
              "ingestion", "visualization", "epistemic", "temporal", "ontology"]
    examples = []
    for topic in topics:
        for number, code in enumerate(
            agent_docs.extract_python_examples(agent_docs.read_topic(topic)), 1
        ):
            result = {"topic": topic, "example": number}
            output = io.StringIO()
            try:
                with contextlib.redirect_stdout(output):
                    exec(compile(code, f"packaged-{topic}-{number}", "exec"),
                         {"__name__": "__main__"})
                result["status"] = "passed"
            except Exception as error:
                result.update(status="failed", exception=type(error).__name__,
                              message=str(error))
            result["stdout"] = output.getvalue()
            examples.append(result)

    with tempfile.TemporaryDirectory() as root:
        path = Path(root) / "graph"
        graph = GraphDB.create(path, backend="lmdb", serializer="json")
        try:
            graph.put_nodes([
                Node("alice", labels=["Person"], properties={"name": "Alice", "age": 34}),
                Node("bob", labels=["Person"], properties={"name": "Bob"}),
                Node("unlabeled"),
            ])
            graph.put_edge(Edge("e1", "alice", "bob", properties={"type": "KNOWS", "score": 0.9}))
            observe("get_node_string", lambda: graph.get_node("alice"))
            observe("get_node_bytes", lambda: graph.get_node(b"alice").to_dict())
            observe("node_repr", lambda: repr(graph.get_node(b"alice")))
            observe("node_dot_id", lambda: graph.get_node(b"alice").id)
            observe("node_call_get_id", lambda: graph.get_node(b"alice").get_id())
            observe("node_property_get_id", lambda: graph.get_node(b"alice").get_id)
            observe("all_node_keys", lambda: list(graph.nodes.keys()))
            observe("all_edge_keys", lambda: list(graph.edges.keys()))
            observe("iterate_node_repository", lambda: list(graph.nodes))
            observe("unconfigured_node_exact", lambda: graph.nodes_by_property("name", "Alice"))
            observe("unconfigured_node_count", lambda: graph.count_nodes_by_property("name", "Alice"))
            observe("unconfigured_edge_exact", lambda: graph.edges_by_property("score", 0.9))
            observe("unconfigured_node_range", lambda: graph.nodes_by_property_range("age", 30, 40))
            observe("index_statistics", graph.index_statistics)
            observe("manifest_counts", lambda: graph.manifest["graph"])
            observe("context_manager_support", lambda: hasattr(graph, "__enter__"))
            observe("old_read_view_exists", lambda: hasattr(graph, "read_view"))
            for name in ["run_rules", "maintain_truth", "explain_claim"]:
                observe(f"old_{name}_exists", lambda name=name: hasattr(graph, name))

            queries = [
                "MATCH (n) RETURN n LIMIT 2",
                "MATCH (n) RETURN count(n) AS nodes",
                "MATCH ()-[r]->() RETURN count(r) AS edges",
                "MATCH (n) UNWIND labels(n) AS label RETURN DISTINCT label",
                "MATCH ()-[r]->() RETURN DISTINCT type(r) AS type",
                "MATCH (n) UNWIND keys(n) AS key RETURN DISTINCT key",
                "MATCH (n) RETURN id(n) AS id, labels(n) AS labels, properties(n) AS properties LIMIT 2",
                "SHOW LABELS", "CALL db.labels()", "SHOW INDEXES", "RETURN 1 AS n",
                'MATCH (n:Person {name: "Alice"}) RETURN n.name AS name',
            ]
            for query in queries:
                observe(query, lambda query=query: graph.query(query).records)

            graph.create_node_property_index("name")
            graph.create_node_property_index("age")
            graph.create_edge_property_index("score")
            observe("configured_node_exact", lambda: [n.to_dict() for n in graph.nodes_by_property("name", "Alice")])
            observe("configured_node_range", lambda: [n.to_dict() for n in graph.nodes_by_property_range("age", 30, 40)])
            observe("legacy_range_query_nodes", lambda: [n.to_dict() for n in graph.range_query_nodes("age", 30, 40)])
            observe("configured_edge_exact", lambda: [e.to_dict() for e in graph.edges_by_property("score", 0.9)])
            observe("index_statistics_after", graph.index_statistics)
            observe("index_definitions_query", lambda: graph.query("SHOW INDEXES").records)
            observe("neighbors", lambda: graph.neighbors_by_edge_type("alice", "KNOWS"))
            figure = graph.visualize("MATCH (a)-[r]->(b) RETURN a,r,b")
            figure.save(Path(root) / "graph.html")
            observe("offline_html_written", lambda: (Path(root) / "graph.html").stat().st_size > 0)
            observe("static_svg", lambda: figure.as_svg().startswith("<svg"))

            graph.temporal.put_node_version(Node("historical"), valid=(0, 100))
            observe("history_separate_from_current", lambda: graph.get_node(b"historical") is None)
            with graph.temporal.open_read_view(
                graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=50
            ) as view:
                observe("replacement_temporal_view", lambda: view.get_node_as_of("historical").logical_id)

            docstrings = {
                name: inspect.getdoc(getattr(graph, name))
                for name in ["create", "open", "get_node", "get_nodes", "index_statistics",
                             "get_node_keys_generator", "iter_edge_ids", "range_query_nodes"]
            }
        finally:
            graph.close()

        reopened = GraphDB.open(path)
        try:
            observe("reopened_node", lambda: reopened.get_node(b"alice").to_dict())
            observe("reopened_index_statistics", reopened.index_statistics)
        finally:
            reopened.close()

    report = {
        "python": platform.python_version(),
        "package_file": str(Path(inspect.getfile(GraphDB))),
        "distributions": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()},
        "packaged_examples": examples,
        "observations": observations,
        "docstrings": docstrings,
        "limits": "No source reads, raw store access, concurrency, scale or browser interaction tests.",
    }
    print(json.dumps(report, indent=2, default=lambda value: (
        {"bytes_utf8": value.decode("utf-8")} if isinstance(value, bytes) else repr(value)
    )))


if __name__ == "__main__":
    main()
