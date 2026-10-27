# CDM schema-graph example

Sidecar example (not core GestaltDB code) backing
`notebooks/11_cdm_schema_graph.ipynb`.

## Sources

Primary source — `pydantic_source.py`:

* `load_cdm_result()` / `load_models()` import every type from the
  `finos-cdm` PyPI distribution (`pip install finos-cdm`, generated from the
  authoritative Rune model, needs no network) and convert the classes to
  `CdmTypeRecord`s with `source="pydantic"`.
* Retains class descriptions (AST-extracted bare-string docs), field
  descriptions and cardinality (`Field(description=...)`, required,
  `list[...]` arrays, min/max length), enum values plus per-member
  descriptions, namespace hierarchy from module paths, and real Python
  inheritance (`Trade` extends `TradableProduct`) as `base_types`.
* Detects Rune choice types structurally (`condition_N_Choice` validators)
  plus the "choice data type" description hint.

Fallback source — `parser.py`:

* `CdmSchemaParser` (`PARSER_VERSION`) lists, downloads (cached), and parses
  the CDM `*.schema.json` files from `https://cdm.finos.org/schemas/7.0/`,
  keeping titles, descriptions, `$ref` targets, cardinality, and enum values.
* JSON is parsed leniently (`strict=False`); files that still fail name the
  culprit file.

## Graph mapping — `graph_builder.py`

* one `CdmType` node per schema (labels: `CdmObject` / `CdmChoice` / `CdmEnum`),
* `HAS_PROPERTY` edge per object property pointing at another type node,
* `EXTENDS` edge per inheritance link (pydantic source),
* `IN_NAMESPACE` edges to fine-grained namespace nodes and `CHILD_NAMESPACE`
  edges forming fine -> coarse -> root metadata hierarchy, so `ns:cdm-event`
  and `ns:cdm-legaldocumentation` always exist,
* `parser_version` stamped on every node/edge plus a `cdm:dataset:*`
  provenance node recording parser version, CDM version, and source.

`queries.py` holds canned Cypher for Trade / Asset / LegalAgreement /
inheritance / metadata exploration.

The notebook puts `notebooks/` on `sys.path` and imports `cdm_schema_graph`.
