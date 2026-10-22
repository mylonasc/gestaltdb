:orphan:

Cross-context, temporal-modal and confidence design gates
============================================================

``gestaltdb.reasoning.scopes`` specifies boundaries; it evaluates
nothing. Cross-world and cross-context inheritance is default-deny —
only an explicit ``InheritanceGrant`` authorizes flow. Temporal-modal
evaluation stays inside declared operator/frame/depth bounds without
changing the existing bounded modal API. Confidence, fuzzy and
probabilistic combination are separately selected calculi, never logical
truth and never accidental multiplication.

.. code-block:: python

   from gestaltdb.reasoning import InheritancePolicy, ModalScope

   policy = InheritancePolicy()  # default deny
   policy.check("alice", "base", "derived", "belief")  # raises: no grant

Calculi stay gated
----------------------------------------

``CalculusRegistry.select`` fails closed until a calculus registers
combination, retraction, convergence and provenance policies — each with
independent tests — plus a user-value scenario. No calculus ships
approved; ``approval_gates`` names exactly what an extension must
present. Probabilistic rules (#124) and neuro-symbolic loops (#128)
coordinate through this gate: combination is a separately selected
calculus with its own retraction, convergence and provenance contract,
never a default mode.
