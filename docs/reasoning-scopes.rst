Cross-context and confidence extension boundaries
============================================================

``gestaltdb.reasoning.scopes`` specifies boundaries; it evaluates
nothing. Cross-world and cross-context inheritance is default-deny —
only an explicit ``InheritanceGrant`` authorizes flow. Temporal-modal
evaluation stays inside declared operator/frame/depth bounds without
changing the existing bounded modal API. Confidence, fuzzy and
probabilistic combination are separately selected calculi, never logical
truth and never accidental multiplication.

**Standalone example, base install only.** An undeclared cross-world inheritance
request is rejected; constructing a policy does not evaluate claims.

.. code-block:: python

   from gestaltdb.reasoning import InheritancePolicy, ReasoningError

   policy = InheritancePolicy()  # default deny
   try:
       policy.check("alice", "base", "derived", "belief")
   except ReasoningError:
       pass  # no inheritance grant
   else:
       raise AssertionError("undeclared cross-world inheritance was accepted")

Calculi stay gated
----------------------------------------

``CalculusRegistry.select`` fails closed until a calculus registers
combination, retraction, convergence and provenance policies — each with
independent tests — plus a user-value scenario. No calculus ships
approved; ``approval_gates`` names exactly what an extension must
present. Probabilistic rules and automated neuro-symbolic loops are not built-in
capabilities. An extension must explicitly define its calculus, retraction,
convergence and provenance contracts; it is never a default mode.
