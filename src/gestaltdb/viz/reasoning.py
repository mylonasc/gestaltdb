"""Reasoning-trace visualization builders (result-viz).

Maps proof/explanation structures onto the existing deterministic
:class:`gestaltdb.viz.ir.VizGraph` payload so reasoning results reuse the
packaged offline D3/React viewer, Jupyter ``_repr_html_`` iframe, and
dependency-free SVG renderer with no new runtime dependency and no
front-end rebuild.

Covered inputs (all read-only, all duck-typed so callers never need to
import reasoning internals for type checks):

- ``ClaimExplanation`` from ``graph.rules.explain`` — claim/rule/absence
  derivation graphs with ``derived_by`` / ``premise`` / ``absent_premise``
  relationships and the root version highlighted.
- ``InferenceExplanation`` audit documents from
  ``gestaltdb.inference.export_inference`` (object or ``to_dict()`` form) —
  the same derivation graph rebuilt from plain JSON-safe data.
- ``Explanation`` / ``ProofTrace.explain`` witnesses from the standalone
  Datalog layer (``reasoning.proofs`` / ``EvaluationResult.explain``) —
  collapsed fact-to-fact proof trees whose edge types are rule IDs.
- Typed support sets (``reasoning.maintenance.DependencyIndex`` supports
  plus an optional ``RankedWitness``) — all alternative justifications for
  one fact, with the chosen witness highlighted when supplied.
- ``Ontology`` revisions (``ontology.model``) — class/property hierarchy
  graphs where binary axioms become typed edges and n-ary axioms
  (property chains, intersections) become explicit axiom nodes.

Standard library only. Every builder sorts IDs and edges deterministically
and funnels through ``VizGraph.from_nodes_edges`` so caps, absolute
ceilings, truncation accounting, and ``meta["builder"]`` provenance behave
exactly like the graph builders.
"""

from __future__ import annotations

import json
from typing import Any, Mapping


def _short_digest(value: str, length: int = 12) -> str:
    digest = value.split(":", 1)[-1] if value.startswith("sha256:") else value
    return digest[:length] if len(digest) > length else digest


def _fact_label(parts: list[str]) -> str:
    return " ".join(str(part) for part in parts)


def _typed_value_text(value: Any) -> str:
    kind = getattr(value, "kind", None)
    if kind is not None and hasattr(value, "lexical"):
        try:
            lexical = value.lexical
        except Exception:  # pragma: no cover - defensive, lexical is total
            lexical = str(value)
        return f"{kind}({lexical})"
    return str(value)


def _typed_fact_id(predicate: str, values: Any) -> str:
    encoded: list[Any] = []
    for item in values or ():
        kind = getattr(item, "kind", None)
        if kind is not None and hasattr(item, "value"):
            raw = getattr(item, "value")
            if isinstance(raw, bytes):
                encoded.append(["literal", kind, raw.hex()])
            elif raw is None and kind == "null":
                encoded.append(["literal", kind, ""])
            else:
                encoded.append(["literal", kind, str(raw)])
        else:
            encoded.append(["entity", str(item)])
    return json.dumps([str(predicate), encoded], ensure_ascii=True, separators=(",", ":"))


def _typed_fact_label(predicate: str, values: Any) -> str:
    return f"{predicate}({', '.join(_typed_value_text(v) for v in values or ())})"


def _binary_fact_id(subject: Any, predicate: Any, obj: Any) -> str:
    return json.dumps(
        [str(subject), str(predicate), str(obj)],
        ensure_ascii=True,
        separators=(",", ":"),
    )


def claim_explanation_graph(
    explanation: Any,
    *,
    max_nodes: int = 2000,
    max_edges: int = 5000,
    absolute_max_nodes: int | None = None,
    absolute_max_edges: int | None = None,
    meta: Mapping[str, Any] | None = None,
) -> Any:
    """Build a ``VizGraph`` from a ``ClaimExplanation`` derivation graph.

    Node labels carry the ``kind`` (``claim``/``rule``/``absence``); claim
    nodes expose the ``subject predicate object`` triple plus agent,
    polarity, logical and version IDs; rule nodes expose the rule ID and
    head/body shapes; absence nodes expose the closed-world check. Edges
    keep the stored ``derived_by`` (conclusion → rule), ``premise``
    (rule → premise), and ``absent_premise`` (rule → absence) direction so
    arrows read as "depends on" from the highlighted root outward.
    """
    from .ir import VizGraph

    nodes = getattr(explanation, "nodes", None)
    edges = getattr(explanation, "edges", None)
    if nodes is None or edges is None:
        raise TypeError("explanation must expose .nodes and .edges (ClaimExplanation)")
    root_version_id = str(getattr(explanation, "root_version_id", ""))
    claim_id = str(getattr(explanation, "claim_id", ""))
    source_truncated = bool(getattr(explanation, "truncated", False))

    node_values: list[dict[str, Any]] = []
    for node in nodes:
        node_id = str(getattr(node, "node_id", ""))
        kind = str(getattr(node, "kind", ""))
        value = getattr(node, "value", None)
        if kind == "claim":
            claim = getattr(value, "claim", None)
            subject = getattr(claim, "subject", "?") if claim is not None else "?"
            predicate = getattr(claim, "predicate", "?") if claim is not None else "?"
            obj = getattr(claim, "object", "?") if claim is not None else "?"
            polarity = getattr(getattr(claim, "polarity", ""), "value", getattr(claim, "polarity", "")) if claim is not None else ""
            properties: dict[str, Any] = {
                "kind": "claim",
                "statement": _fact_label([subject, predicate, obj]),
                "subject": str(subject),
                "predicate": str(predicate),
                "object": str(obj),
                "polarity": str(polarity),
            }
            if claim is not None:
                for key in ("claim_id", "statement_id", "agent", "source", "world"):
                    item = getattr(claim, key, None)
                    if item is not None:
                        properties[key] = str(item)
                confidence = getattr(claim, "confidence", None)
                if confidence is not None:
                    properties["confidence"] = confidence
            version = value
            for key in ("version_id", "logical_id", "commit_id", "commit_ordinal", "operation"):
                item = getattr(version, key, None)
                if item is not None:
                    enum_value = getattr(item, "value", item)
                    properties[key] = enum_value if isinstance(enum_value, (str, int)) else str(enum_value)
            properties["label"] = properties["statement"]
            labels = ("claim",)
        elif kind == "rule":
            rule_id = str(getattr(value, "rule_id", node_id))
            then = getattr(value, "then", None)
            head = f"{getattr(then, 'subject', '?')} {getattr(then, 'predicate', '?')} {getattr(then, 'object', '?')}" if then is not None else ""
            properties = {"kind": "rule", "rule_id": rule_id, "label": rule_id}
            if head.strip():
                properties["head"] = head
            when = getattr(value, "when", None)
            if when is not None:
                try:
                    properties["premise_count"] = len(list(when))
                except TypeError:
                    pass
            not_when = getattr(value, "not_when", None)
            if not_when:
                try:
                    properties["absence_count"] = len(list(not_when))
                except TypeError:
                    pass
            labels = ("rule",)
        elif kind == "absence":
            properties = {
                "kind": "absence",
                "predicate": str(getattr(value, "predicate", "?")),
                "subject": str(getattr(value, "subject", "?")),
                "object": str(getattr(value, "object", "?")),
                "position": getattr(value, "position", 0),
            }
            scope = getattr(value, "scope", None)
            if scope is not None:
                properties["scope"] = [str(item) for item in scope]
            properties["label"] = (
                f"¬{properties['predicate']}({properties['subject']},{properties['object']})"
            )
            labels = ("absence",)
        else:  # pragma: no cover - fail closed on unknown future kinds
            raise TypeError(f"unknown explanation node kind: {kind!r}")
        node_values.append({"id": node_id, "labels": list(labels), "properties": properties})

    edge_values: list[dict[str, Any]] = []
    for edge in edges:
        source = str(getattr(edge, "source_id", ""))
        target = str(getattr(edge, "target_id", ""))
        relationship = str(getattr(edge, "relationship", ""))
        ordinal = getattr(edge, "premise_ordinal", None)
        properties: dict[str, Any] = {"relationship": relationship}
        if ordinal is not None:
            properties["premise_ordinal"] = ordinal
        edge_values.append({
            "id": f"{source} | {relationship} | {target} | {ordinal}",
            "source": source,
            "target": target,
            "properties": {"type": relationship, **properties},
        })

    payload_meta = dict(meta or {})
    payload_meta["builder"] = "from_claim_explanation"
    payload_meta["suggested_layout"] = "tree"
    payload_meta["claim_id"] = claim_id
    payload_meta["root_version_id"] = root_version_id
    if source_truncated:
        payload_meta["source_truncated"] = True
        payload_meta["note"] = "explanation was truncated by max_nodes/max_depth before visualization"
    return VizGraph.from_nodes_edges(
        node_values,
        edge_values,
        max_nodes=max_nodes,
        max_edges=max_edges,
        absolute_max_nodes=absolute_max_nodes,
        absolute_max_edges=absolute_max_edges,
        highlight_nodes=[root_version_id] if root_version_id else (),
        meta=payload_meta,
    )


def inference_audit_graph(
    report: Any,
    *,
    max_nodes: int = 2000,
    max_edges: int = 5000,
    absolute_max_nodes: int | None = None,
    absolute_max_edges: int | None = None,
    meta: Mapping[str, Any] | None = None,
) -> Any:
    """Build a ``VizGraph`` from an inference-audit document.

    Accepts an ``InferenceExplanation`` (anything exposing ``to_dict()``)
    or an already-serialized audit mapping. Node ``kind`` values
    (``claim``/``model_output``/``rule``/``absence``) become labels;
    justification entries become ``derived_by``/``premise``/``absent``
    edges mirroring :func:`claim_explanation_graph`.
    """
    from .ir import VizGraph

    if hasattr(report, "to_dict") and not isinstance(report, Mapping):
        try:
            document = report.to_dict()
        except TypeError as exc:  # pragma: no cover - defensive
            raise TypeError("audit report to_dict() failed") from exc
    elif isinstance(report, Mapping):
        document = dict(report)
    else:
        raise TypeError("report must be an InferenceExplanation or an audit mapping")
    if document.get("kind") != "gestaltdb.inference-audit" and "nodes" not in document:
        raise TypeError("report is not a gestaltdb inference-audit document")

    root_version_id = str(document.get("root_version_id", ""))
    source_truncated = bool(document.get("truncated", False))
    node_values: list[dict[str, Any]] = []
    for entry in document.get("nodes", ()):
        if not isinstance(entry, Mapping):
            continue
        node_id = str(entry.get("id", ""))
        kind = str(entry.get("kind", ""))
        properties: dict[str, Any] = {"kind": kind or "claim"}
        if kind in ("claim", "model_output"):
            claim = entry.get("claim", {})
            if isinstance(claim, Mapping):
                for key in ("subject", "predicate", "agent", "polarity", "world", "source", "claim_id", "statement_id"):
                    if claim.get(key) is not None:
                        properties[key] = claim[key]
                if claim.get("subject") is not None and claim.get("predicate") is not None:
                    properties["statement"] = _fact_label([
                        claim.get("subject"), claim.get("predicate"), claim.get("object")])
                    properties["label"] = properties["statement"]
                if isinstance(claim.get("provenance"), Mapping) and claim["provenance"].get("model_output") is not None:
                    properties["model_output"] = claim["provenance"]["model_output"]
            for key in ("logical_id", "commit_id", "commit_ordinal", "operation"):
                if entry.get(key) is not None:
                    properties[key] = entry[key]
            properties.setdefault("label", properties.get("statement", node_id))
        elif kind == "rule":
            value = entry.get("value", {})
            if isinstance(value, Mapping):
                properties["rule_id"] = value.get("rule_id", node_id)
                if value.get("then") is not None:
                    properties["head"] = str(value["then"])
            else:
                properties["rule_id"] = node_id
            properties["label"] = str(properties["rule_id"])
        elif kind == "absence":
            value = entry.get("value", {})
            if isinstance(value, Mapping):
                for key in ("predicate", "subject", "object", "position", "scope"):
                    if value.get(key) is not None:
                        properties[key] = value[key]
            properties["label"] = (
                f"¬{properties.get('predicate', '?')}({properties.get('subject', '?')},"
                f"{properties.get('object', '?')})"
            )
        else:
            properties["kind"] = kind or "claim"
        labels = (kind or "claim",)
        node_values.append({"id": node_id, "labels": list(labels), "properties": properties})

    edge_values: list[dict[str, Any]] = []
    for support in document.get("justifications", ()):
        if not isinstance(support, Mapping):
            continue
        conclusion = str(support.get("conclusion", ""))
        rule = str(support.get("rule", ""))
        premises = support.get("premises", ())
        absences = support.get("absences", ())
        if conclusion and rule:
            edge_values.append({
                "id": f"{conclusion} | derived_by | {rule}",
                "source": conclusion,
                "target": rule,
                "properties": {"type": "derived_by", "relationship": "derived_by"},
            })
        for ordinal, premise in enumerate(premises or ()):
            premise_id = str(premise)
            edge_values.append({
                "id": f"{rule} | premise | {premise_id} | {ordinal}",
                "source": rule,
                "target": premise_id,
                "properties": {"type": "premise", "relationship": "premise", "premise_ordinal": ordinal},
            })
        for absence in absences or ():
            if isinstance(absence, Mapping):
                absence_id = str(absence.get("node_id", absence.get("id", "")))
                if not absence_id:
                    continue
                edge_values.append({
                    "id": f"{rule} | absent | {absence_id}",
                    "source": rule,
                    "target": absence_id,
                    "properties": {"type": "absent_premise", "relationship": "absent_premise"},
                })

    payload_meta = dict(meta or {})
    payload_meta["builder"] = "from_inference_audit"
    payload_meta["suggested_layout"] = "tree"
    payload_meta["claim_id"] = str(document.get("claim_id", ""))
    payload_meta["root_version_id"] = root_version_id
    if source_truncated:
        payload_meta["source_truncated"] = True
        payload_meta["note"] = "audit export was truncated before visualization"
    return VizGraph.from_nodes_edges(
        node_values,
        edge_values,
        max_nodes=max_nodes,
        max_edges=max_edges,
        absolute_max_nodes=absolute_max_nodes,
        absolute_max_edges=absolute_max_edges,
        highlight_nodes=[root_version_id] if root_version_id else (),
        meta=payload_meta,
    )


def proof_explanation_graph(
    explanation: Any,
    *,
    max_nodes: int = 2000,
    max_edges: int = 5000,
    absolute_max_nodes: int | None = None,
    absolute_max_edges: int | None = None,
    meta: Mapping[str, Any] | None = None,
) -> Any:
    """Build a collapsed proof tree from a standalone ``Explanation``.

    Each ground fact becomes one node (``input`` vs ``derived`` groups);
    each witness premise becomes one directed ``premise → conclusion``
    edge typed by the deriving rule ID, so arrows follow logical support
    from inputs up to the highlighted root. Rule identity, evidence
    references, and truncation travel as properties/meta.
    """
    from .ir import VizGraph

    explanation_nodes = getattr(explanation, "nodes", None)
    if explanation_nodes is None:
        raise TypeError("explanation must expose .nodes (reasoning Explanation)")
    root = getattr(explanation, "root", None)
    source_truncated = bool(getattr(explanation, "truncated", False))

    def fact_parts(fact: Any) -> tuple[str, str, str]:
        return (str(getattr(fact, "subject", "?")), str(getattr(fact, "predicate", "?")), str(getattr(fact, "object", "?")))

    root_id = _binary_fact_id(*fact_parts(root)) if root is not None else ""
    node_values: list[dict[str, Any]] = []
    edge_values: list[dict[str, Any]] = []
    seen: set[str] = set()
    for witness in explanation_nodes:
        fact = getattr(witness, "fact", None)
        if fact is None:
            continue
        subject, predicate, obj = fact_parts(fact)
        fact_id = _binary_fact_id(subject, predicate, obj)
        rule = getattr(witness, "rule", None)
        rule_id = str(getattr(rule, "rule_id", "")) if rule is not None else ""
        if fact_id not in seen:
            seen.add(fact_id)
            evidence = getattr(witness, "evidence", ())
            properties: dict[str, Any] = {
                "kind": "derived" if rule is not None else "input",
                "statement": _fact_label([subject, predicate, obj]),
                "label": _fact_label([subject, predicate, obj]),
                "subject": subject,
                "predicate": predicate,
                "object": obj,
            }
            if rule_id:
                properties["rule_id"] = rule_id
            try:
                evidence_items = [str(item) for item in evidence or ()]
            except TypeError:
                evidence_items = []
            if evidence_items:
                properties["evidence"] = evidence_items
            node_values.append({"id": fact_id, "labels": [properties["kind"]], "properties": properties})
        for premise in getattr(witness, "premises", ()) or ():
            p_subject, p_predicate, p_obj = fact_parts(premise)
            premise_id = _binary_fact_id(p_subject, p_predicate, p_obj)
            if premise_id not in seen:
                seen.add(premise_id)
                node_values.append({
                    "id": premise_id,
                    "labels": ["input"],
                    "properties": {
                        "kind": "input",
                        "statement": _fact_label([p_subject, p_predicate, p_obj]),
                        "label": _fact_label([p_subject, p_predicate, p_obj]),
                        "subject": p_subject,
                        "predicate": p_predicate,
                        "object": p_obj,
                    },
                })
            edge_values.append({
                "id": f"{premise_id} | {rule_id or 'supports'} | {fact_id}",
                "source": premise_id,
                "target": fact_id,
                "properties": {"type": rule_id or "supports", "rule_id": rule_id or "supports"},
            })

    payload_meta = dict(meta or {})
    payload_meta["builder"] = "from_proof_explanation"
    payload_meta["suggested_layout"] = "tree"
    payload_meta["root"] = _fact_label(list(fact_parts(root))) if root is not None else ""
    if source_truncated:
        payload_meta["source_truncated"] = True
        payload_meta["note"] = "proof explanation was truncated by max_nodes before visualization"
    highlights = [root_id] if root_id and any(item["id"] == root_id for item in node_values) else []
    # Deterministic order: inputs and derived facts sort by stable JSON ID.
    node_values.sort(key=lambda item: item["id"])
    edge_values.sort(key=lambda item: (item["source"], item["target"], item["id"]))
    return VizGraph.from_nodes_edges(
        node_values,
        edge_values,
        max_nodes=max_nodes,
        max_edges=max_edges,
        absolute_max_nodes=absolute_max_nodes,
        absolute_max_edges=absolute_max_edges,
        highlight_nodes=highlights,
        meta=payload_meta,
    )


def support_set_graph(
    fact: Any,
    supports: Any,
    witness: Any | None = None,
    *,
    max_nodes: int = 2000,
    max_edges: int = 5000,
    absolute_max_nodes: int | None = None,
    absolute_max_edges: int | None = None,
    meta: Mapping[str, Any] | None = None,
) -> Any:
    """Build an all-supports view for one typed fact.

    The conclusion fans out to one support node per alternative
    derivation (``support`` group, ``rule_id`` property); each support
    node fans out to its premise facts. When a ``RankedWitness`` is
    supplied, the support node matching its rule/premises is highlighted
    together with the conclusion, so the chosen derivation stands out
    from the full alternative set.
    """
    from .ir import VizGraph

    try:
        items = list(supports or ())
    except TypeError as exc:
        raise TypeError("supports must be iterable") from exc
    if not items:
        raise TypeError("supports must contain at least one Support")

    predicate = getattr(fact, "predicate", None)
    values = getattr(fact, "values", None)
    if predicate is None or values is None:
        raise TypeError("fact must be a TypedFact with .predicate and .values")
    conclusion_id = _typed_fact_id(str(predicate), list(values))
    conclusion_label = _typed_fact_label(str(predicate), list(values))

    def premises_of(support: Any) -> list[Any]:
        return list(getattr(support, "premises", ()) or ())

    node_values = [{
        "id": conclusion_id,
        "labels": ["conclusion"],
        "properties": {"kind": "conclusion", "statement": conclusion_label,
                       "label": conclusion_label, "predicate": str(predicate)},
    }]
    edge_values: list[dict[str, Any]] = []
    chosen_support_id = ""
    if witness is not None:
        witness_rule = str(getattr(witness, "rule_id", ""))
        witness_premises = {
            _typed_fact_id(str(getattr(item, "predicate", "")),
                           list(getattr(item, "values", ())))
            for item in (getattr(witness, "premises", ()) or ())
        }
    else:
        witness_rule, witness_premises = "", set()

    support_ids: list[str] = []
    for support in items:
        rule_id = str(getattr(support, "rule_id", ""))
        premise_ids = [
            _typed_fact_id(str(getattr(item, "predicate", "")),
                           list(getattr(item, "values", ())))
            for item in premises_of(support)
        ]
        support_id = json.dumps(
            ["support", rule_id, sorted(premise_ids)],
            ensure_ascii=True, separators=(",", ":"))
        support_ids.append(support_id)
        node_values.append({
            "id": support_id,
            "labels": ["support"],
            "properties": {"kind": "support", "rule_id": rule_id, "label": rule_id,
                           "digest": _short_digest(getattr(support, "digest", support_id))},
        })
        edge_values.append({
            "id": f"{conclusion_id} | derived_by | {support_id}",
            "source": conclusion_id,
            "target": support_id,
            "properties": {"type": "derived_by", "rule_id": rule_id},
        })
        for ordinal, premise in enumerate(premises_of(support)):
            premise_id = premise_ids[ordinal]
            node_values.append({
                "id": premise_id,
                "labels": ["premise"],
                "properties": {
                    "kind": "premise",
                    "statement": _typed_fact_label(
                        str(getattr(premise, "predicate", "")),
                        list(getattr(premise, "values", ()))),
                    "predicate": str(getattr(premise, "predicate", "")),
                },
            })
            node_values[-1]["properties"]["label"] = node_values[-1]["properties"]["statement"]
            edge_values.append({
                "id": f"{support_id} | premise | {premise_id} | {ordinal}",
                "source": support_id,
                "target": premise_id,
                "properties": {"type": "premise", "premise_ordinal": ordinal, "rule_id": rule_id},
            })
        if witness is not None and rule_id == witness_rule and set(premise_ids) == witness_premises:
            chosen_support_id = support_id

    payload_meta = dict(meta or {})
    payload_meta["builder"] = "from_support_set"
    payload_meta["suggested_layout"] = "tree"
    payload_meta["fact"] = conclusion_label
    payload_meta["support_count"] = len(items)
    if witness is not None:
        payload_meta["witness_kind"] = str(getattr(witness, "kind", ""))
        payload_meta["witness_rank"] = getattr(witness, "rank", None)
        payload_meta["witness_total"] = getattr(witness, "total", None)
    highlights = [conclusion_id] + ([chosen_support_id] if chosen_support_id else [])
    return VizGraph.from_nodes_edges(
        node_values,
        edge_values,
        max_nodes=max_nodes,
        max_edges=max_edges,
        absolute_max_nodes=absolute_max_nodes,
        absolute_max_edges=absolute_max_edges,
        highlight_nodes=highlights,
        meta=payload_meta,
    )


def ontology_graph(
    ontology: Any,
    *,
    max_nodes: int = 2000,
    max_edges: int = 5000,
    absolute_max_nodes: int | None = None,
    absolute_max_edges: int | None = None,
    meta: Mapping[str, Any] | None = None,
) -> Any:
    """Build a hierarchy graph from an ``Ontology`` revision.

    Classes and properties become ``class``/``property``/``term`` nodes;
    binary axioms (subclass, subproperty, equivalence, domain, range,
    inverses, characteristics) become single typed edges; n-ary axioms
    (property chains, intersections) become explicit ``axiom`` nodes so no
    premise is silently dropped. Union members each imply the superclass,
    so they stay direct edges.
    """
    from .ir import VizGraph

    axioms = getattr(ontology, "axioms", None)
    if axioms is None:
        raise TypeError("ontology must expose .axioms (Ontology)")
    try:
        axiom_list = list(axioms)
    except TypeError as exc:
        raise TypeError("ontology axioms must be iterable") from exc

    roles: dict[str, set[str]] = {}
    node_values: dict[str, dict[str, Any]] = {}
    edge_values: list[dict[str, Any]] = []

    def tag(term: str, role: str) -> None:
        roles.setdefault(str(term), set()).add(role)

    def add_edge(source: str, target: str, edge_type: str, properties: Mapping[str, Any] | None = None) -> None:
        payload = {"type": edge_type}
        if properties:
            payload.update(dict(properties))
        edge_values.append({
            "id": f"{source} | {edge_type} | {target} | {len(edge_values)}",
            "source": str(source),
            "target": str(target),
            "properties": payload,
        })

    for axiom in axiom_list:
        name = type(axiom).__name__
        get = lambda field: getattr(axiom, field, None)  # noqa: E731
        if name == "SubClassOf":
            sub, sup = str(get("subclass")), str(get("superclass"))
            tag(sub, "class")
            tag(sup, "class")
            add_edge(sub, sup, "rdfs:subClassOf", {"axiom": name})
        elif name == "SubPropertyOf":
            sub, sup = str(get("subproperty")), str(get("superproperty"))
            tag(sub, "property")
            tag(sup, "property")
            add_edge(sub, sup, "rdfs:subPropertyOf", {"axiom": name})
        elif name == "EquivalentClasses":
            left, right = str(get("left")), str(get("right"))
            tag(left, "class")
            tag(right, "class")
            add_edge(left, right, "owl:equivalentClass", {"axiom": name, "symmetric": True})
        elif name == "EquivalentProperties":
            left, right = str(get("left")), str(get("right"))
            tag(left, "property")
            tag(right, "property")
            add_edge(left, right, "owl:equivalentProperty", {"axiom": name, "symmetric": True})
        elif name == "Domain":
            prop, cls = str(get("property")), str(get("class_id"))
            tag(prop, "property")
            tag(cls, "class")
            add_edge(prop, cls, "rdfs:domain", {"axiom": name})
        elif name == "Range":
            prop, cls = str(get("property")), str(get("class_id"))
            tag(prop, "property")
            tag(cls, "class")
            add_edge(prop, cls, "rdfs:range", {"axiom": name})
        elif name == "InverseOf":
            left, right = str(get("left")), str(get("right"))
            tag(left, "property")
            tag(right, "property")
            add_edge(left, right, "owl:inverseOf", {"axiom": name, "symmetric": True})
        elif name in ("Symmetric", "Transitive"):
            prop = str(get("property"))
            tag(prop, "property")
            add_edge(prop, prop, f"owl:{name.lower()}Property", {"axiom": name})
        elif name == "Reflexive":
            prop, scope = str(get("property")), str(get("scope"))
            tag(prop, "property")
            tag(scope, "class")
            add_edge(prop, scope, "reflexiveIn", {"axiom": name})
        elif name == "PropertyChain":
            first, second, result = str(get("first")), str(get("second")), str(get("result"))
            tag(first, "property")
            tag(second, "property")
            tag(result, "property")
            axiom_id = str(getattr(axiom, "axiom_id", f"chain:{first}+{second}=>{result}"))
            node_values[axiom_id] = {
                "id": axiom_id, "labels": ["axiom"],
                "properties": {"kind": "axiom", "axiom": name,
                               "statement": f"{first} ∘ {second} ⇒ {result}",
                               "label": f"{first} ∘ {second} ⇒ {result}"},
            }
            add_edge(first, axiom_id, "chainFirst", {"axiom": name})
            add_edge(second, axiom_id, "chainSecond", {"axiom": name})
            add_edge(axiom_id, result, "chainResult", {"axiom": name})
        elif name == "IntersectionSubClassOf":
            members = [str(item) for item in (get("members") or ())]
            sup = str(get("superclass"))
            for member in members:
                tag(member, "class")
            tag(sup, "class")
            axiom_id = str(getattr(axiom, "axiom_id", f"intersection:{'+'.join(sorted(members))}=>{sup}"))
            node_values[axiom_id] = {
                "id": axiom_id, "labels": ["axiom"],
                "properties": {"kind": "axiom", "axiom": name,
                               "statement": f"{' ⊓ '.join(sorted(members))} ⇒ {sup}",
                               "label": f"{' ⊓ '.join(sorted(members))} ⇒ {sup}"},
            }
            for member in members:
                add_edge(member, axiom_id, "intersectionMember", {"axiom": name})
            add_edge(axiom_id, sup, "intersectionImplies", {"axiom": name})
        elif name == "UnionSubClassOf":
            members = [str(item) for item in (get("members") or ())]
            sup = str(get("superclass"))
            for member in members:
                tag(member, "class")
            tag(sup, "class")
            for member in members:
                add_edge(member, sup, "unionSubClassOf", {"axiom": name})
        else:  # pragma: no cover - fail closed on unknown future axioms
            raise TypeError(f"unsupported ontology axiom: {name!r}")

    for term, kinds in roles.items():
        label = next(iter(kinds)) if len(kinds) == 1 else "term"
        node_values.setdefault(term, {
            "id": term, "labels": [label],
            "properties": {"kind": label, "term": term, "label": term},
        })
        if len(kinds) > 1:
            node_values[term]["properties"]["roles"] = sorted(kinds)

    payload_meta = dict(meta or {})
    payload_meta["builder"] = "from_ontology"
    payload_meta["suggested_layout"] = "tree"
    payload_meta["ontology_id"] = str(getattr(ontology, "ontology_id", ""))
    revision = getattr(ontology, "revision_id", None)
    if revision is not None:
        payload_meta["revision_id"] = str(revision)
    payload_meta["axiom_count"] = len(axiom_list)
    return VizGraph.from_nodes_edges(
        list(node_values.values()),
        edge_values,
        max_nodes=max_nodes,
        max_edges=max_edges,
        absolute_max_nodes=absolute_max_nodes,
        absolute_max_edges=absolute_max_edges,
        meta=payload_meta,
    )
