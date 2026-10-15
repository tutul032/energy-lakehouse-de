# 1. Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-10-16

## Context
This project makes design choices (bundle layout, serverless-only compute, catalog/schema
strategy, ingestion approach) that should be explainable to reviewers and to my future self.

## Decision
We record significant architecture decisions as ADRs in `docs/adr/`, using Michael Nygard's
lightweight format: `NNNN-short-title.md` with **Status, Context, Decision, Consequences**.
ADRs are immutable once accepted; a change of mind is a new ADR that supersedes the old one.

## Consequences
- Decisions and their trade-offs are visible in the repo history next to the code.
- Small overhead per decision; only decisions that are costly to reverse get an ADR.
