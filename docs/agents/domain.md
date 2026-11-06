# Domain Docs

This repo uses one domain context: `GLOSSARY.md` at the root and ADRs under `.agents/adr/`.

## Before exploring

Read `GLOSSARY.md` and the ADRs in `.agents/adr/` that touch the area being explored. If a document is absent, proceed silently; the domain-modeling skill creates documentation when terms or decisions are resolved.

## Use the glossary's vocabulary

Use the terms defined in `GLOSSARY.md` when naming domain concepts in issues, proposals, code, and tests. When a needed concept is missing, check whether an existing term fits before noting a gap for domain-modeling.

## Flag ADR conflicts

If a proposal contradicts an existing ADR, identify the decision and explain why it should be revisited.
