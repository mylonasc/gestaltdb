"""Claim/rule/modal guides retain source, inference and audit boundaries.

All three complete database workflows execute under each declared backend/codec
combination in test_documentation_variants.py.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_claim_status_precedes_world_models():
    text = (ROOT / "docs/claims-introduction.rst").read_text()
    introduction = text.split(".. code-block:: python", 1)[0]
    for term in ("SUPPORTED", "REFUTED", "BOTH", "UNKNOWN", "Confidence", "absence"):
        assert term in introduction
    for page in ("stored-rules", "modal-epistemic", "inference-audit", "temporal-introduction"):
        assert f":doc:`{page}`" in text


def test_stored_and_standalone_contracts_are_distinct():
    text = (ROOT / "docs/stored-rules.rst").read_text()
    for term in ("Standalone", "final support", "not an explicit denial", "max_justifications",
                 "truncated", "independent proof verification", "conservative"):
        assert term in text


def test_modal_example_and_navigation_are_complete():
    text = (ROOT / "docs/modal-epistemic.rst").read_text()
    for term in ("BELIEVES", "KNOWS", "POSSIBLE", "NECESSARY", "outsider", "UNKNOWN",
                 "max_states=100", "Shape fragments", "not a calibrated modal probability"):
        assert term in text
    navigation = (ROOT / "docs/index.rst").read_text()
    for page in ("claims-introduction", "stored-rules", "modal-epistemic"):
        assert f"   {page}\n" in navigation
