# Equation conventions

Dollar math (``myst_enable_extensions = ["dollarmath"]``):

Start a positive closure at its asserted input facts, not at the empty set:

$$I_0 = F,\quad I_{n+1} = I_n \cup \{h\sigma \mid (B\Rightarrow h)\in P, B\sigma\subseteq I_n\}.$$

Write membership explicitly rather than overloading a pair as an RDF triple:

$$\frac{(x,\mathrm{rdf:type},C)}{(x,\mathrm{rdf:type},D)}\quad(C\sqsubseteq D).$$

For positive temporal supports, intersect premise validity per derivation,
then union the alternative derivations. Entire-window truth is coverage:

$$\mathrm{holdsThroughout}(s,W,h) \iff W\subseteq V_s(h).$$

Here `V_s(h)` is the selected positive truth-coverage set at system horizon `h`,
not necessarily one assertion version's interval. See chapter 5 for boundaries.
