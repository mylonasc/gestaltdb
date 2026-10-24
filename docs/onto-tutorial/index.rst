From Graph Databases to Knowledge Graphs
========================================

Looking for installation instructions or the API reference? Visit the
`GestaltDB library documentation <https://mylonasc.github.io/gestaltdb/>`_.
This tutorial is a separate, self-contained learning guide.

Imagine building a research directory from three sources. A personnel system
lists appointments, a grant system identifies investigators, and a public
catalog records which laboratories belong to which institutions. Each source
has useful information, but none answers the product question by itself:
"Which people are connected to this institution, and why do we believe that?"

At first, it is tempting to join a few tables and put the result behind an API.
That may be exactly the right solution. The difficulty grows when the sources
use different vocabularies, relationships span several hops, records arrive
at different times, or an answer must include its evidence. One source calls
Alice a researcher, another asks for people. Acme Lab belongs to a group that
belongs to a university. A correction to one appointment should change the
answers that depended on it, without erasing answers supported elsewhere.

These are several different problems, not one problem solved by choosing a
database brand. A **graph model** makes identities and relationships explicit.
A **graph database** persists that model and retrieves connected records.
A **query language** states which records or paths an application wants. None
of those choices alone tells the system that every researcher is a person.
For that, we need explicit meaning: a vocabulary and semantic assumptions,
often expressed by an **ontology**. A **reasoning engine** applies those
assumptions systematically, so consequences need not be reimplemented in
every report or endpoint. Together, such choices can support a **knowledge
graph**—a representation of domain knowledge, not merely a particular storage
format.

The distinction becomes practical immediately. Storing one more label on
Alice makes one query work, but now the application must keep that label in
sync when the schema or supporting records change. Repeating the same logic
inside every query avoids that copy but spreads the policy across the system.
Computing a reusable logical closure offers another trade-off: share the
rules and answers, while paying for inference, context binding, and maintenance.
This tutorial develops those choices instead of treating inference as a
feature that should always be switched on.

GestaltDB provides a concrete setting in which to learn the distinctions. It
has an embedded property graph, Cypher queries, an explicit ontology-to-rule
compiler, standalone reasoning APIs, and separate adapters for stored data
and result consumption. We will start with one fact about Alice, follow it
through a complete inference, then ask what changes when data is large,
historical, contradictory, or used for sampling. The goal is both to use
GestaltDB and to recognize the same underlying design problems in other
databases and reasoning engines.

The intended reader is comfortable with Python, dictionaries, SQL-style joins,
and basic data structures. No previous RDF, OWL, or logic-programming experience
is assumed. Equations and execution details are included for readers who want
to understand the implementation costs, but the first pass can focus on the
worked examples and their explanations.

.. rubric:: What you will learn

By the end, you should be able to:

* distinguish a graph data model, a graph database, and a knowledge graph;
* choose what to represent as entities, relationships, labels, or properties;
* explain where RDF, OWL, Cypher, GraphQL, SQL, Datalog, and Prolog fit;
* read basic rule notation and trace joins and recursion to a fixed point;
* separate a stored fact, an inferred fact, a proof witness, and a source claim;
* decide when to compute a closure, query a subset, or build a sampling snapshot;
* recognize unsupported combinations and the costs that optimizations cannot remove.

.. rubric:: The running example

Alice works for ACME. We record a ``worksFor`` relationship that explicitly
means employment—not merely being present at ACME's premises. Its domain is
``Employee``, and every ``Employee`` is a ``Person``. Can the application infer
Alice's membership without storing either label? What more would it need to
classify her as a ``Researcher``?

The second question is just as important as the first. Employment alone does
not establish a research role. We will add a separate ``hasJobDescription``
fact, with the simplified categories ``researcher`` and ``not_researcher``,
and a rule that refines an employee into a researcher only with positive
research-job evidence. Missing job information remains missing information,
not a negative classification. These distinctions let us see both what an
ontology implies and what it must **not** imply.

.. rubric:: How to read this document

**First pass: build the mental model.** Read chapters 1–4 in order: graph and
knowledge-graph concepts, ontology implications, the reasoning engine, then
the complete database example. Run notebook 1 after chapter 2 if desired,
and continue to chapter 6 to understand how applications consume the answer.
You should be able to draw the example and explain each inferred membership
without reading the library source.

**Second pass: study the trade-offs.** Revisit chapter 3's execution strategies
and engine comparisons. Chapter 5 adds sources, updates, and time; chapter 7
explains performance. Chapter 8 helps choose a supported workflow rather than
promising a universal reasoner. The glossary provides a quick reference, and
each chapter ends with a self-check and an answer.

.. rubric:: Running the examples

This is a standalone tutorial: its chapters introduce the concepts and supply
the worked examples without requiring you to read the library manual first.
For API details and installation alternatives, consult the
`GestaltDB library documentation <https://mylonasc.github.io/gestaltdb/>`_
and `library repository <https://github.com/mylonasc/gestaltdb>`_. External
library references follow the current library and may describe APIs newer than
the checkout tested here; they are optional further reading, not prerequisites.

The examples target the source checkout accompanying this tutorial, including
its ``gestalt-core-v1`` ontology fragment. Do not assume that an older PyPI
release has the same APIs. Use Python 3.10–3.13. From the repository root::

   uv sync --extra lmdb
   uv run --with jupyterlab --with ipykernel jupyter lab docs/onto-tutorial/notebooks

The standalone reasoning examples need no embedded backend. Basic database
examples support LMDB, LevelDB, and RocksDB/PyRex; see :doc:`backends` for
install commands and backend choices. Pinned current-graph examples explicitly
use LMDB because not every backend offers the required snapshot guarantee.
Database examples use disposable temporary directories.
Select a Jupyter kernel from this environment. You can also just read the code:
assertions in the examples state the expected results.

.. toctree::
   :maxdepth: 2
   :caption: Reading guide

   ch01-property-graph-vs-semantic
   ch02-ontology-fragment
   ch03-datalog-irs
   ch04-storage-grounding
   ch05-claims-temporal
   ch06-query-sampling
   ch07-performance
   ch08-gaps
   glossary
   backends
   notebooks/index

.. only:: html

   .. toctree::
      :maxdepth: 1
      :caption: For documentation maintainers (not part of the reader PDF)

      maintainer-notes
      toolkit/index
      OUTLINE_COVERAGE
