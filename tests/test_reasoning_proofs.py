"""RE-27 bounded proofs, export interchange and generation checkpoints."""

import pytest

from gestaltdb.reasoning.explanations import (
    CHECKPOINT_FORMAT_VERSION,
    RankedWitness,
    checkpoint_closure,
    discard_checkpoint,
    export_proof,
    is_complete_checkpoint,
    minimal_witness,
    resume_checkpoint,
    top_k,
)
from gestaltdb.reasoning.maintenance import rematerialize
from gestaltdb.reasoning.model import ReasoningError, ReasoningLimitError, ReasoningLimits, Variable
from gestaltdb.reasoning.typed import GenericProgram, GenericRule, TypedAtom, TypedFact


X, Y, Z = Variable("x"), Variable("y"), Variable("z")


def program():
    return GenericProgram([
        GenericRule("base", [TypedAtom("edge", [X, Y])], [], TypedAtom("reach", [X, Y])),
        GenericRule("step", [TypedAtom("reach", [X, Y]), TypedAtom("edge", [Y, Z])], [],
                    TypedAtom("reach", [X, Z])),
    ])


def material():
    prog = program()
    base = [TypedFact("edge", ["a", "b"]), TypedFact("edge", ["b", "c"]), TypedFact("edge", ["a", "c"])]
    return prog, rematerialize(prog, base, limits=ReasoningLimits())


def test_minimal_and_top_k_are_deterministic_and_bounded():
    prog, out = material()
    supports = [item for item in out.supports if item.fact == TypedFact("reach", ["a", "c"])]
    assert len(supports) == 2
    first = minimal_witness(TypedFact("reach", ["a", "c"]), out.supports)
    assert isinstance(first, RankedWitness)
    assert (first.rank, first.total, first.kind) == (1, 2, "chosen-minimal")
    assert len(first.premises) == 1  # direct edge beats the two-hop path
    assert first == minimal_witness(TypedFact("reach", ["a", "c"]), list(reversed(out.supports)))
    ranked = top_k(TypedFact("reach", ["a", "c"]), out.supports, 2)
    assert [item.rank for item in ranked] == [1, 2]
    assert all(item.kind == "chosen-top-k" for item in ranked)
    assert top_k(TypedFact("reach", ["a", "c"]), out.supports, 9) == ranked  # k clamps, never pads
    assert first.digest == minimal_witness(TypedFact("reach", ["a", "c"]), out.supports).digest
    with pytest.raises(ReasoningError, match="mind the gap"):
        minimal_witness(TypedFact("reach", ["zzz"]), out.supports)
    with pytest.raises(ReasoningError, match="mind the gap"):
        top_k(TypedFact("reach", ["zzz"]), out.supports, 1)
    with pytest.raises(ReasoningError):
        top_k(TypedFact("reach", ["a", "c"]), out.supports, 0)
    with pytest.raises(ReasoningError):
        minimal_witness("nope", out.supports)
    with pytest.raises(ReasoningError):
        RankedWitness(TypedFact("reach", ["a", "c"]), "base", [TypedFact("edge", ["a", "b"])], 2, 1, "chosen-minimal")


def test_export_distinguishes_witness_from_all_supports():
    _, out = material()
    fact = TypedFact("reach", ["a", "c"])
    witness = minimal_witness(fact, out.supports)
    document = export_proof(fact, out.supports, witness)
    assert document["format"] == "gestaltdb-proof-v1"
    assert document["witness_kind"] == "chosen-minimal"
    assert len(document["all_supports"]) == 2
    assert document["witness"]["digest"] == witness.digest
    assert "confidence" not in str(document) and "score" not in str(document)
    foreign = minimal_witness(TypedFact("reach", ["a", "b"]), out.supports)
    with pytest.raises(ReasoningError, match="must justify the exported fact"):
        export_proof(fact, out.supports, foreign)
    with pytest.raises(ReasoningError, match="exported support set"):
        export_proof(fact, [], witness)
    with pytest.raises(ReasoningLimitError, match="max_proof_bytes"):
        export_proof(fact, out.supports, witness, max_proof_bytes=10)
    with pytest.raises(ReasoningError):
        export_proof("nope", out.supports, witness)


def test_checkpoint_resume_rejects_drift_and_repairs_nothing():
    prog, out = material()
    assert CHECKPOINT_FORMAT_VERSION == 2
    data = checkpoint_closure(prog, out.facts, out.supports)
    assert is_complete_checkpoint(data)
    assert data["program_digest"] == prog.digest
    facts, index = resume_checkpoint(data, program=prog)
    assert set(facts) == set(out.facts)
    assert index.digest == data["index_digest"]
    assert resume_checkpoint(data, program=prog)[1] == index
    changed = GenericProgram(list(prog.rules) + [
        GenericRule("extra", [TypedAtom("edge", [X, Y])], [], TypedAtom("loop", [X, Y]))])
    with pytest.raises(ReasoningError, match="drifted"):
        resume_checkpoint(data, program=changed)
    assert is_complete_checkpoint(data)  # failed resume repaired nothing
    tampered = dict(data, horizon="sha256:" + "00" * 32)
    assert not is_complete_checkpoint(tampered)
    with pytest.raises(ReasoningError, match="incomplete checkpoint"):
        resume_checkpoint(tampered, program=prog)
    assert not is_complete_checkpoint({"format_version": 1})
    assert not is_complete_checkpoint("nope")
    assert not is_complete_checkpoint(None)
    with pytest.raises(ReasoningError):
        checkpoint_closure("nope", out.facts, out.supports)
    with pytest.raises(ReasoningError):
        checkpoint_closure(prog, ["nope"], out.supports)
    assert discard_checkpoint(tampered, consent=True) is True
    with pytest.raises(ReasoningError, match="consent"):
        discard_checkpoint(tampered, consent=False)
    with pytest.raises(ReasoningError, match="refusing to discard"):
        discard_checkpoint(data, consent=True)


def test_core_runs_without_site_packages():
    from pathlib import Path
    import subprocess
    import sys

    path = Path(__file__).parents[1] / "src/gestaltdb/reasoning/__init__.py"
    script = f"""\
import importlib.util, sys
spec = importlib.util.spec_from_file_location('standalone', {str(path)!r})
module = importlib.util.module_from_spec(spec)
sys.modules['standalone'] = module
spec.loader.exec_module(module)
from standalone.explanations import checkpoint_closure, export_proof, minimal_witness, resume_checkpoint
from standalone.maintenance import rematerialize
from standalone.model import ReasoningLimits, Variable
from standalone.typed import GenericProgram, GenericRule, TypedAtom, TypedFact
x, y = Variable('x'), Variable('y')
prog = GenericProgram([GenericRule('b', [TypedAtom('p', [x, y])], [], TypedAtom('q', [x, y]))])
out = rematerialize(prog, [TypedFact('p', ['a', 'b'])], limits=ReasoningLimits())
witness = minimal_witness(TypedFact('q', ['a', 'b']), out.supports)
assert export_proof(TypedFact('q', ['a', 'b']), out.supports, witness)['witness_kind'] == 'chosen-minimal'
data = checkpoint_closure(prog, out.facts, out.supports)
assert resume_checkpoint(data, program=prog)[1].digest == data['index_digest']
assert not any(name.startswith(('gestaltdb', 'numpy')) for name in sys.modules)
"""
    subprocess.run([sys.executable, "-I", "-S", "-c", script], check=True, capture_output=True, text=True)
