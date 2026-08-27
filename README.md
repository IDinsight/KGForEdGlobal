# Knowledge Graph for Education Global

A configurable, provenance-preserving pipeline for transforming curriculum PDFs into
validated knowledge graphs aligned to the
[Learning Commons ontology](https://docs.learningcommons.org/knowledge-graph/understanding-knowledge-graph/introduction).

The pipeline separates document reconstruction from curriculum-semantic interpretation
so that intermediate decisions can be inspected, validated, and audited.

## Pipeline

```text
Curriculum PDF
    |
    v
Page IR Extraction
    |
    v
Page IR Verification
    |
    v
Document IR
    |
    v
Academic Standards KG
    |
    v
Academic Standards + Learning Components KG
```

The five conceptual stages are implemented through four CLI entry points. The final
entry point builds the validated Academic Standards layer first and then derives
Learning Components from it.

## Quick start

Follow the [local setup guide](docs/development/local-setup.md), create or adapt a
runtime config, and run the pipeline from `backend/`:

```bash
python src/skg/entries/extract_page_ir.py <config.json>
python src/skg/entries/verify_page_ir_continuity.py <config.json>
python src/skg/entries/stitch_document_ir.py <config.json>
python src/skg/entries/create_kgs.py <config.json>
```

Example curriculum profiles are available under [`examples/`](examples/). If you are
adapting the system to a new source, start with the
[Add a New Curriculum](docs/guides/adding-a-curriculum.md) guide rather than editing the
backend for source-specific conventions.

## What the pipeline produces

The run preserves intermediate evidence for debugging and auditability while producing
two main validated KG handoffs:

- `kgs/as_kg_bundle.json` — Academic Standards framework, items, hierarchy, and
  provenance; and
- `kgs/as_lc_kg_bundle.json` — the combined Academic Standards + Learning Components
  graph with `hasChild` and `supports` relationships.

See the [pipeline overview](docs/pipeline/index.md) for the full artifact map and stage
contracts.

## Documentation

- [Official documentation](https://idinsight.github.io/SenegalKG/)
- [Architecture](docs/architecture.md)
- [Pipeline overview](docs/pipeline/index.md)
- [Add a new curriculum](docs/guides/adding-a-curriculum.md)
- [Run, resume, and debug](docs/guides/running-and-debugging.md)
- [Local development setup](docs/development/local-setup.md)
- [Contributing](docs/contributing.md)

## Design principles

- **Source grounded:** final standards and skills remain traceable to source evidence.
- **Stage separated:** PageIR and DocumentIR reconstruct the document before curriculum
  semantics are inferred.
- **Validated:** higher-risk LLM assertions use explicit deterministic checks and, where
  appropriate, independent producer/checker flows.
- **Deterministic where possible:** identity construction, graph constraints,
  reconciliation checks, and final compilation are enforced in Python.
- **Configuration driven:** curriculum-specific taxonomy, hierarchy, codes, extraction
  policy, and LC policy belong in document profiles rather than source-specific backend
  branches.

## Contact

See [Contact us](docs/contact_us.md) for project and team contact information.
