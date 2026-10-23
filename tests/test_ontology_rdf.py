import socket
import pytest

from gestaltdb.ontology import Domain, OntologyError, SubClassOf, Symmetric, Transitive
from gestaltdb.ontology.adapters.rdf import RDFDocument, RDFImportLimits, import_rdf

pytest.importorskip("rdflib")
PREFIX = '@prefix ex: <urn:ex:> . @prefix owl: <http://www.w3.org/2002/07/owl#> . @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> . '


def test_named_axioms_annotations_and_stable_iris():
    doc = RDFDocument(PREFIX + 'ex:A a owl:Class; rdfs:subClassOf ex:B; rdfs:label "A" . ex:p a owl:TransitiveProperty, owl:SymmetricProperty; rdfs:domain ex:A .')
    result = import_rdf(doc, ontology_id="urn:test")
    assert result.complete and result.annotation_count == 1
    assert set(result.ontology.axioms) == {SubClassOf("urn:ex:A", "urn:ex:B"), Domain("urn:ex:p", "urn:ex:A"), Transitive("urn:ex:p"), Symmetric("urn:ex:p")}
    assert result.compile().revision_id == result.ontology.revision_id


def test_offline_imports_are_explicit_deduplicated_and_cycle_safe(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("network access"))
    root = RDFDocument(PREFIX + '<urn:root> owl:imports <https://example.invalid/import> .')
    child = RDFDocument(PREFIX + '<urn:child> owl:imports <urn:root> . ex:A rdfs:subClassOf ex:B .')
    with pytest.raises(OntologyError, match="unresolved"):
        import_rdf(root, ontology_id="urn:root")
    result = import_rdf(root, ontology_id="urn:root", imports={"https://example.invalid/import": child})
    assert result.complete and len(result.ontology.axioms) == 1
    assert result.resolved_imports == ("https://example.invalid/import",)


def test_unsupported_logic_fails_or_reports_explicit_incompleteness():
    doc = RDFDocument(PREFIX + 'ex:A rdfs:subClassOf [ a owl:Restriction; owl:onProperty ex:p; owl:someValuesFrom ex:B ] .')
    with pytest.raises(OntologyError):
        import_rdf(doc, ontology_id="urn:test")
    result = import_rdf(doc, ontology_id="urn:test", strict=False, limits=RDFImportLimits(max_diagnostics=1))
    assert not result.complete and result.unsupported_count > len(result.diagnostics) == 1
    with pytest.raises(OntologyError, match="incomplete"):
        result.compile()
    result.compile(allow_incomplete=True)


@pytest.mark.parametrize("statement", ['ex:A rdfs:subClassOf owl:Thing .', 'ex:p a owl:FunctionalProperty .', 'ex:a a ex:A .', 'ex:p rdfs:range <http://www.w3.org/2001/XMLSchema#string> .'])
def test_nonprofile_vocabulary_and_abox_are_rejected(statement):
    with pytest.raises(OntologyError):
        import_rdf(RDFDocument(PREFIX + statement), ontology_id="urn:test")


@pytest.mark.parametrize("limits,match", [(RDFImportLimits(max_bytes=10), "max_bytes"), (RDFImportLimits(max_triples=1), "max_triples")])
def test_parser_limits_fail_before_complete_import(limits, match):
    with pytest.raises(OntologyError, match=match):
        import_rdf(RDFDocument(PREFIX + 'ex:A rdfs:subClassOf ex:B . ex:B rdfs:subClassOf ex:C .'), ontology_id="urn:test", limits=limits)


def test_nt_xml_and_no_unsafe_parser_plugins():
    nt = RDFDocument('<urn:A> <http://www.w3.org/2000/01/rdf-schema#subClassOf> <urn:B> .', "nt")
    assert import_rdf(nt, ontology_id="urn:test").complete
    xml = RDFDocument('<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"><rdf:Description rdf:about="urn:A"><rdfs:subClassOf rdf:resource="urn:B"/></rdf:Description></rdf:RDF>', "xml")
    assert import_rdf(xml, ontology_id="urn:test").complete
    for data in ('<!DOCTYPE x SYSTEM "file:///etc/passwd"><x/>', '<!ENTITY e "x"><x/>'):
        with pytest.raises(OntologyError, match="DTD"):
            import_rdf(RDFDocument(data, "xml"), ontology_id="urn:test")
    with pytest.raises(OntologyError):
        RDFDocument('{}', "json-ld")
