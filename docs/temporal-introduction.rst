Temporal and bitemporal data
============================

Use temporal versions when you need to retain earlier accounts of a node or edge,
not just its current value. GestaltDB stores immutable version records alongside
the mutable property graph. Historical reads select versions; they do not run
rules, infer conclusions or update current records.

Two independent clocks
------------------------

:term:`Valid time <valid time>` says when an assertion applies in your domain.
:term:`System time <system time>` says when the database recorded it. Together
they are :term:`bitemporal`: “what applied in April?” differs from “what had we
recorded about April by this earlier commit?”

Valid intervals are **half-open**: ``[start, end)`` includes the start, excludes
the end, and uses ``None`` for an open end. System horizons include the selected
visible commit. System timestamps are allocated by the database, not supplied
as backdated valid times.

.. list-table:: Alice's department: two recorded accounts
   :header-rows: 1

   * - Recorded horizon
     - January 15
     - April 15
     - June 1
   * - S1: original assertion, Research on [January 1, December 31)
     - Research
     - Research
     - Research
   * - S2: correction, Safety on [March 1, June 1)
     - Research
     - Safety
     - Research

S1 and S2 are the actual system timestamps returned by consecutive writes. The
March-to-June correction changes only that valid interval, not the entire year;
it does not erase the original account or change answers requested at S1.

Run the two-horizon example
----------------------------

This standalone example creates a disposable managed database. It deliberately
stores a different mutable current record to make the storage boundary visible.
Dates include UTC offsets; captured timestamps avoid sleeps or assumed wall-clock
values. Default temporal writes maintain their derived indexes.

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import GraphDB, Node
   from gestaltdb.temporal import TemporalInterval

   with TemporaryDirectory() as root:
       graph = GraphDB.create(f"{root}/graph", backend="lmdb", serializer="json")
       try:
           graph.put_node(Node("alice", properties={"department": "current-only"}))
           first = graph.temporal.put_node_version(
               Node("alice", properties={"department": "Research"}),
               valid=TemporalInterval.parse("2024-01-01T00:00:00Z", "2024-12-31T00:00:00Z"),
           )
           corrected = graph.temporal.correct_node_version(
               Node("alice", properties={"department": "Safety"}),
               supersedes_version_id=first.version_id,
               valid=TemporalInterval.parse("2024-03-01T00:00:00Z", "2024-06-01T00:00:00Z"),
           )
           instants = ["2024-01-15T00:00:00Z", "2024-04-15T00:00:00Z", "2024-06-01T00:00:00Z"]
           answers = [
               [graph.temporal.get_node_as_of("alice", valid_time=at, system_time=known)
                .node.properties["department"] for at in instants]
               for known in (first.system_time, corrected.system_time)
           ]
           assert answers == [["Research", "Research", "Research"],
                              ["Research", "Safety", "Research"]]
           assert graph.get_node("alice").properties["department"] == "current-only"
           assert graph.temporal.get_node_version(first.version_id).node.properties == {
               "department": "Research",
           }
           print(answers)
       finally:
           graph.close()

``get_node_as_of`` returns a version record with its exact ``version_id``,
``commit_id``, ``system_time`` and decoded ``node``. It returns ``None`` when no
assertion applies or a retraction wins. Edge versions follow the same rule and
expose ``edge``. Mutating a returned payload does not persist a change.

Choose the right read/write path
---------------------------------

.. list-table:: Keep current state, history and inference distinct
   :header-rows: 1

   * - Task
     - API / next guide
   * - Replace a current node or edge
     - Ordinary graph CRUD and unqualified Cypher (:doc:`property-graph`)
   * - Append assertions, corrections or retractions
     - ``graph.temporal`` (:doc:`temporal-updates`)
   * - Compare known histories or share a fixed horizon
     - As-of APIs and authenticated views (:doc:`temporal-reads`)
   * - Query historical graph patterns
     - Qualified read-only Cypher (:doc:`temporal-reads`, :doc:`cypher-tutorial`)
   * - Sample historical structure
     - :doc:`sampling-temporal`
   * - Retain sourced positive/negative propositions or run stored rules
     - Separate services (:doc:`claims-introduction`, :doc:`stored-rules`, :doc:`modal-epistemic`)
   * - Compute validity intervals of inferred conclusions
     - :doc:`reasoning-temporal`; temporal storage alone does not evaluate Datalog

Property values created by Cypher ``date()`` or ``datetime()`` are not themselves
version history. Likewise, an ordinary ``put_node`` does not automatically append
a temporal assertion, and a temporal write does not refresh the mutable graph.

For exact winner ordering, publication and integrity contracts, use
:doc:`temporal-versioning`. For deferred indexes, see :doc:`temporal-updates`;
for backups, orphan inspection, retention and legacy migration, use
:doc:`operations`. Authentication verifies selected source identity and stored
history, not the real-world truth of an assertion.
