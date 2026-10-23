"""Strict, bounded offline TBox import. No URL/file fetch or implicit imports.

RDFLib is imported only when parsing; install ``gestaltdb[rdf]``. JSON-LD and
arbitrary parser plugins are intentionally not exposed, since they may fetch
remote contexts. RDF/XML DTD/entity declarations are rejected before parsing.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping
from urllib.parse import urlsplit

from ..compiler import compile_ontology
from ..model import (
    Domain, EquivalentClasses, EquivalentProperties, IntersectionSubClassOf, InverseOf, Ontology,
    OntologyError, PropertyChain, Range, Reflexive, SubClassOf, SubPropertyOf, Symmetric, Transitive,
    UnionSubClassOf,
)


_FORMATS = {"turtle", "nt", "xml"}
_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_RDFS = "http://www.w3.org/2000/01/rdf-schema#"
_OWL = "http://www.w3.org/2002/07/owl#"
_XSD = "http://www.w3.org/2001/XMLSchema#"


@dataclass(frozen=True)
class RDFDocument:
    data: str | bytes
    format: str = "turtle"

    def __post_init__(self):
        if not isinstance(self.data, (str, bytes)) or self.format not in _FORMATS:
            raise OntologyError("RDF documents require text/bytes and turtle, nt or xml format")


@dataclass(frozen=True)
class RDFImportLimits:
    max_bytes: int = 1_048_576
    max_triples: int = 10_000
    max_documents: int = 32
    max_diagnostics: int = 100

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise OntologyError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class RDFImportResult:
    ontology: Ontology
    unsupported_count: int
    diagnostics: tuple[str, ...]
    resolved_imports: tuple[str, ...]
    annotation_count: int

    @property
    def complete(self):
        return self.unsupported_count == 0

    def compile(self, *, allow_incomplete: bool = False):
        if not self.complete and not allow_incomplete:
            raise OntologyError("incomplete RDF import requires explicit allow_incomplete=True")
        return compile_ontology(self.ontology)


def _absolute(value):
    return isinstance(value, str) and bool(urlsplit(value).scheme) and not any(c.isspace() or ord(c) < 32 for c in value)


def import_rdf(
    document: RDFDocument, *, ontology_id: str,
    imports: Mapping[str, RDFDocument] | None = None,
    strict: bool = True, limits: RDFImportLimits = RDFImportLimits(),
) -> RDFImportResult:
    """Import the thirteen named axiom forms; unsupported logic fails by default.

    ``ontology_id`` is an absolute base IRI. ``imports`` is a caller-supplied
    in-memory resolver, not a URL loader. Permissive mode reports incompleteness.
    Limits count cumulative document bytes and parser add attempts, even duplicates.
    They are not a process-memory sandbox for arbitrary parser code or inputs.
    """
    if not isinstance(document, RDFDocument) or not isinstance(limits, RDFImportLimits):
        raise OntologyError("explicit RDFDocument and RDFImportLimits required")
    if not isinstance(strict, bool) or not _absolute(ontology_id):
        raise OntologyError("strict must be bool and ontology_id an absolute IRI")
    resolver = dict(imports or {})
    if any(not _absolute(key) or not isinstance(value, RDFDocument) for key, value in resolver.items()):
        raise OntologyError("imports must map absolute IRIs to in-memory RDFDocument values")
    try:
        import rdflib
    except ImportError as exc:
        raise ImportError("RDF import requires gestaltdb[rdf]") from exc

    counters = {"bytes": 0, "triples": 0, "unsupported": 0, "annotations": 0}
    diagnostics, axioms, resolved = [], [], set()

    def unsupported(message):
        counters["unsupported"] += 1
        if strict:
            raise OntologyError(message)
        if len(diagnostics) < limits.max_diagnostics:
            diagnostics.append(message)

    class BoundedGraph(rdflib.Graph):
        def add(self, triple):
            counters["triples"] += 1
            if counters["triples"] > limits.max_triples:
                raise OntologyError("RDF import exceeded max_triples")
            return super().add(triple)

        def addN(self, quads):
            raise OntologyError("RDF quad ingestion is not supported")

    def named(term):
        if not isinstance(term, rdflib.URIRef) or not _absolute(str(term)):
            raise OntologyError("logical axiom terms must be absolute named IRIs")
        value = str(term)
        if value.startswith((_RDF, _RDFS, _OWL, _XSD)):
            raise OntologyError("builtin vocabulary terms have semantics outside gestalt-core-v1")
        return value

    binary = {
        _RDFS + "subClassOf": SubClassOf, _RDFS + "subPropertyOf": SubPropertyOf,
        _OWL + "equivalentClass": EquivalentClasses, _OWL + "equivalentProperty": EquivalentProperties,
        _RDFS + "domain": Domain, _RDFS + "range": Range, _OWL + "inverseOf": InverseOf,
    }
    annotation_predicates = {_RDFS + "label", _RDFS + "comment", _OWL + "versionInfo", _OWL + "versionIRI"}
    declarations = {_OWL + "Class", _RDFS + "Class", _OWL + "ObjectProperty", _RDF + "Property", _OWL + "Ontology"}
    pending = [(ontology_id, document)]
    visited = set()
    while pending:
        iri, source = pending.pop()
        if iri in visited:
            continue
        if len(visited) >= limits.max_documents:
            raise OntologyError("RDF import exceeded max_documents")
        visited.add(iri)
        try:
            data = source.data.encode("utf-8") if isinstance(source.data, str) else source.data
        except UnicodeEncodeError as exc:
            raise OntologyError("invalid RDF Unicode input") from exc
        counters["bytes"] += len(data)
        if counters["bytes"] > limits.max_bytes:
            raise OntologyError("RDF import exceeded max_bytes")
        # XML encodings can otherwise hide declarations from a byte-level check.
        if source.format == "xml":
            try:
                data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise OntologyError("XML input must be UTF-8") from exc
            if b"\x00" in data or re.search(br"<!\s*(DOCTYPE|ENTITY)\b", data, re.IGNORECASE):
                raise OntologyError("XML DTD/entities and non-UTF-8 encodings are not supported")
        graph = BoundedGraph()
        try:
            graph.parse(data=data, format=source.format, publicID=iri)
        except OntologyError:
            raise
        except Exception as exc:
            raise OntologyError(f"invalid RDF document at {iri}") from exc
        def read_list(head, what):
            members, seen, node = [], set(), head
            nil = rdflib.URIRef(_RDF + "nil")
            first, rest = rdflib.URIRef(_RDF + "first"), rdflib.URIRef(_RDF + "rest")
            while node != nil:
                if not isinstance(node, rdflib.BNode) or node in seen or len(members) >= 64:
                    raise OntologyError(f"malformed {what} collection")
                seen.add(node)
                heads = list(graph.objects(node, first))
                tails = list(graph.objects(node, rest))
                if len(heads) != 1 or len(tails) != 1:
                    raise OntologyError(f"malformed {what} collection")
                members.append(heads[0])
                node = tails[0]
            return members

        constructs: dict = {}
        used_constructs: set = set()
        chain_subjects: set = set()
        for s, p, o in sorted(graph, key=lambda triple: tuple(str(value) for value in triple)):
            predicate = str(p)
            if predicate not in (_OWL + "intersectionOf", _OWL + "unionOf", _OWL + "propertyChainAxiom"):
                continue
            try:
                if predicate in (_OWL + "intersectionOf", _OWL + "unionOf"):
                    if not isinstance(s, rdflib.BNode):
                        raise OntologyError("class collection subjects must be blank nodes")
                    if s in constructs:
                        raise OntologyError("duplicate collection definition")
                    members = [named(term) for term in read_list(o, "class collection")]
                    if len(members) < 2:
                        raise OntologyError("class collections require at least two members")
                    constructs[s] = ("intersection" if predicate.endswith("intersectionOf") else "union", members)
                    continue
                if s in constructs:
                    raise OntologyError("duplicate property chain definition")
                props = [named(term) for term in read_list(o, "property chain")]
                if len(props) != 2:
                    raise OntologyError("property chains must combine exactly two properties")
                subject = named(s)
                if subject in chain_subjects:
                    raise OntologyError("duplicate property chain definition")
                chain_subjects.add(subject)
                axioms.append(PropertyChain(props[0], props[1], subject))
            except OntologyError as exc:
                if strict:
                    raise
                unsupported(str(exc))
        for s, p, o in sorted(graph, key=lambda triple: tuple(str(value) for value in triple)):
            predicate, value = str(p), str(o)
            if predicate in (_RDF + "first", _RDF + "rest",
                             _OWL + "intersectionOf", _OWL + "unionOf", _OWL + "propertyChainAxiom"):
                continue
            if predicate in annotation_predicates:
                counters["annotations"] += 1
                continue
            if predicate == _OWL + "imports":
                if not isinstance(o, rdflib.URIRef) or not _absolute(value) or value not in resolver:
                    # A cycle to the root is already resolved without fetching.
                    if value in visited and isinstance(o, rdflib.URIRef):
                        continue
                    unsupported(f"unresolved offline import: {value}")
                else:
                    resolved.add(value)
                    pending.append((value, resolver[value]))
                continue
            try:
                if predicate in binary and not isinstance(s, rdflib.BNode) and not isinstance(o, rdflib.BNode):
                    axioms.append(binary[predicate](named(s), named(o)))
                elif predicate == _RDFS + "subClassOf" and isinstance(s, rdflib.BNode):
                    construct = constructs.get(s)
                    if construct is None:
                        raise OntologyError(f"unsupported blank subject: {s.n3()}")
                    used_constructs.add(s)
                    members = construct[1]
                    if construct[0] == "intersection":
                        axioms.append(IntersectionSubClassOf(tuple(members), named(o)))
                    else:
                        axioms.append(UnionSubClassOf(tuple(members), named(o)))
                elif predicate == _RDFS + "subClassOf" and isinstance(o, rdflib.BNode):
                    construct = constructs.get(o)
                    if construct is None:
                        raise OntologyError(f"unsupported blank superclass: {o.n3()}")
                    used_constructs.add(o)
                    if construct[0] == "intersection":
                        axioms.extend(SubClassOf(named(s), member) for member in construct[1])
                    else:
                        raise OntologyError("disjunctive superclass is not Horn; union belongs on the left")
                elif predicate in binary:
                    axioms.append(binary[predicate](named(s), named(o)))
                elif predicate == _RDF + "type" and value in {_OWL + "TransitiveProperty", _OWL + "SymmetricProperty"}:
                    axioms.append((Transitive if value == _OWL + "TransitiveProperty" else Symmetric)(named(s)))
                elif predicate == _RDF + "type" and value == _OWL + "ReflexiveProperty":
                    raise OntologyError("unscoped reflexivity is not supported; declare an explicit scoped form")
                elif predicate == _RDF + "type" and value in declarations:
                    if isinstance(s, rdflib.BNode) and s in constructs:
                        used_constructs.add(s)
                    else:
                        named(s)
                else:
                    unsupported(f"unsupported RDF statement: {s.n3()} {p.n3()} {o.n3()}")
            except OntologyError as exc:
                if strict:
                    raise
                # Avoid double counting unsupported()'s permissive branch.
                unsupported(str(exc))
        for leftover in sorted(constructs, key=str):
            if leftover not in used_constructs:
                unsupported(f"unused class collection: {leftover.n3()}")
    return RDFImportResult(Ontology(ontology_id, axioms), counters["unsupported"], tuple(diagnostics),
                           tuple(sorted(resolved)), counters["annotations"])
