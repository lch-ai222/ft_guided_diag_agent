# Fault-Tree Guided Diagnostic Agent

This project implements a fault-tree guided manufacturing quality diagnostic agent with a controlled Tree Evolution pipeline. Production diagnosis uses reviewed and released TTL fault trees, while unsupported development cases can be diagnosed temporarily in a non-PASS exploratory mode and converted into auditable `TreeProposal` candidates for future fault-tree generation, evaluation, review, gray release, and final release.

## Architecture

- Streamlit diagnostic workbench in `app/streamlit_app.py`
- Core diagnostic engine in `src/ft_diag_agent/`
- Fault tree ingestion from TTL via `rdflib`
- Document/case RAG from `data/raw_docs/` into `data/chroma/`
- LangGraph-backed conditional workflow, Planner, Tool Registry, DiagnosticState, Gate, Report, Replay, Eval, and dataset export modules
- Case-only autonomous planner for unsupported development cases
- Rework/misdiagnosis guard for repeat-repair and prior ineffective-action risk
- Confirmation planner actions for publish-before-release evidence checks after a suspected root cause is reached
- Dynamic fault-tree generation request objects for unsupported development cases, aligned with `docs/tree_gen_agent.md`
- Tree Evolution planning: TreeProposal store, lifecycle, evaluation, review, gray release, release and rollback design
- Batch document tree-generation entrypoint for quality reports, 8D reports, SOP/FMEA/manual documents, producing DRAFT_TREE proposals only
- Optional DeepSeek/OpenAI-compatible structured generation through provider wrappers

For a detailed module-by-module developer guide, see `docs/developer_guide.md`. For the dynamic tree evolution plan, see `docs/tree_evolution_plan.md`. Keep both guides updated whenever code changes affect modules, state fields, tools, RAG/LLM behavior, UI flow, data directories, proposal lifecycle, or evaluation outputs.

## Environment

Use a project-local environment. Do not install Python packages globally.

Recommended:

```bash
brew install uv
uv venv --python 3.11
uv sync --extra dev
cp .env.example .env
```

If `uv` must be removed later:

```bash
brew uninstall uv
rm -rf .venv .uv-cache
```

Fallback without `uv`:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"
cp .env.example .env
```

## Configuration

Set values in `.env`:

- `LLM_PROVIDER`: defaults to `deepseek`.
- `LLM_ENABLE`: set to `true` to call the configured LLM provider.
- `DEEPSEEK_API_KEY`: enables DeepSeek-enhanced work-order classification, report polish, and optional rerank. Keep it only in local `.env`.
- `DEEPSEEK_MODEL_FAST`: default `deepseek-v4-flash` for extraction and normal development.
- `DEEPSEEK_MODEL_PRO`: default `deepseek-v4-pro` for low-confidence classification and complex reasoning.
- `DIAGNOSIS_MODE`: `PRODUCTION` rejects unsupported work orders; `DEVELOPMENT` allows case-only exploratory diagnosis without production PASS.
- `FAULT_TREE_TTL_PATH`: TTL file path.
- `RAW_DOCS_DIR`: source documents, project-local by default.
- `CHROMA_DIR`: project-local vector index cache.
- `RUNS_DIR`: replay records.
- `DATASETS_DIR`: exported SFT/preference/eval datasets.
- `TREE_GENERATION_DIR`: local batch tree-generation jobs, uploads, and artifacts.
- `TREE_PROPOSALS_DIR`: local TreeProposal JSONL store.

Rules-only mode works without `DEEPSEEK_API_KEY`.

## Data Layout

```text
data/raw_docs/   # Put real PDF/MD/TXT/CSV documents here.
data/chroma/     # Generated local vector DB cache.
data/tree_generation/ # Generated batch tree-generation jobs/uploads/artifacts.
data/tree_proposals/  # Generated TreeProposal JSONL records.
runs/            # Generated replay JSONL records.
datasets/        # Generated SFT/preference/eval JSONL or CSV files.
```

These generated directories are ignored by git so they can be cleaned safely:

```bash
rm -rf data/chroma/* data/tree_generation/* data/tree_proposals/* runs/* \
  datasets/*.jsonl datasets/*.csv datasets/*.json datasets/eval_results*/*
```

## Run

```bash
uv run streamlit run app/streamlit_app.py
```

Fallback:

```bash
.venv/bin/streamlit run app/streamlit_app.py
```

The Streamlit app avoids expensive reruns by caching fault-tree repositories, RAG indexes, and document scans with `st.cache_resource` / `st.cache_data`, while the active diagnostic case stays in `st.session_state`.

## Work-Order Driven Diagnosis

The primary entrypoint is a new work order:

1. Parse, select, paste, or create a simple-input work order.
2. Classify the fault type and decide whether a fault tree covers it.
3. In `PRODUCTION`, unsupported work orders fail fast and cannot receive a production `PASS`.
4. In `DEVELOPMENT`, unsupported work orders can enter case-only exploratory mode, but Gate remains `GRAY`; the same run can create or update a TreeProposal discovery record.
5. The planner works from the active fault-tree node, selects the next transition test, and routes it to HITL or a tool executor. If a suspected root cause has already been reached, it can still propose non-blocking confirmation checks.
6. Unsupported development cases use RAG evidence, optional LLM proposals, and domain rule fallbacks to generate hypotheses, an exploratory plan, and traceable HITL actions while keeping Gate `GRAY`.
7. Unsupported development cases can emit a `FaultTreeGenerationRequest` and later a `TreeProposal`. This is a candidate/evolution artifact only: it does not create production TTL, does not modify released `FaultTree`, and does not allow Gate `PASS`.
8. Dynamic requests are grouped into an in-state `FaultTreeRequestCluster` seed with review-status recommendations. Replay export and the Streamlit Replay tab can merge similar requests across `runs/*.jsonl` into `dynamic_tree_clusters.jsonl`.
9. A TreeProposal must move through the controlled lifecycle before production use: `DRAFT_TREE -> CANDIDATE_TREE -> GRAY_TREE -> RELEASED_TREE`, or `REJECTED`. Only `RELEASED_TREE` can become a production diagnosis tree.

The LangGraph workflow now branches by coverage state: unsupported production cases go directly to Gate/Report, while
unsupported development cases skip fault-tree retrieval and move into case-only evidence retrieval and planning.

Dynamic tree generation follows `docs/tree_gen_agent.md`: downstream generation should model ontology entities and
`SymptomTransition` first, then rebuild final `FaultTree` deterministically from start nodes after validation, offline replay, gray validation, human review, and rollback preparation. LLMs must not directly write final `FaultTree.symptom_ids`.

Mock work orders live under `data/raw_docs/mock_work_orders_*.md` and are used by tests and offline eval.

## Batch Tree Generation

The first Tree Evolution entrypoint is available in Streamlit under **树生成：批量文档预生成候选树**.

Inputs:

- Existing `PDF/MD/TXT/CSV` files from `data/raw_docs/`
- Uploaded quality reports, 8D reports, SOP, FMEA, or repair/manual documents

Outputs:

- `TreeGenerationJob`
- draft ontology entities: `FailureSymptom`, `OntologyTest`, `OntologyMeasure`
- draft `SymptomTransition`
- deterministic BFS `FaultTree` preview
- `TreeProposal(status=DRAFT_TREE)`
- stage timing records for each generation phase
- Mermaid tree visualization with tests rendered on transition edges

This entrypoint follows `docs/tree_ontology_schema.md` and `docs/tree_gen_agent.md`: LLM-first extraction creates
ontology drafts and transitions across multiple passes:

1. `PASS_1`: candidate entity extraction.
2. `PASS_2`: entity classification, start merge, and `start/inner/root` leveling.
3. `PASS_3`: `SymptomTransition` binding with tests on edges.
4. `VALIDATE`: deterministic structure validation.
5. `PASS_4`: repair only when validation reports issues.
6. deterministic BFS rebuild preview.

Rules are only a low-confidence debug fallback when LLM is unavailable;
those outputs are marked `LOW_CONF_DEBUG_DRAFT` and should not be treated as usable trees. Generated proposals are not
production trees and cannot make Gate `PASS`.

Task title and supplemental instructions are stored as job metadata only. They are not evidence and must not become
candidate symptoms, tests, measures, or transitions. Rule fallback only reads input document chunks and uses `MISSING`
placeholders when source evidence is absent.

LLM extraction passes persist both parsed JSON and the raw response text for debugging. If PASS_1 returns `{}` or all
candidate arrays are empty, the service performs one stricter retry before falling back to `LOW_CONF_DEBUG_DRAFT`.
`EXTRACTED_INFERRED` and `MISSING` fields are not deleted by the repair pass merely because they are low-confidence;
they are preserved as draft knowledge and listed in the Tree Generation HITL completion queue. The UI can ask the LLM
to act as a domain/process/repair expert and propose confirmation options for those fields, but each suggestion must be
grounded in source chunks first, RAG evidence second, and expert knowledge only as contextual support. Confirmed user
decisions are written back to the draft artifact as `CONFIRMED`, then validation and the deterministic preview are
rebuilt.

The Streamlit page shows the live generation stage and elapsed time while a job is running, then persists the completed
stage timings in the artifact. It also shows the effective LLM settings for the run, selects the newly generated job
after completion, and orders historical jobs by update time. The tree visualization tab renders the same Mermaid graph
that can be rendered from the command line without extra dependencies:

```bash
.venv/bin/python scripts/render_tree_generation_tree.py data/tree_generation/artifacts/<JOB_ID>/artifact.json
.venv/bin/python scripts/render_tree_generation_tree.py data/tree_generation/artifacts/<JOB_ID>/artifact.json --output /tmp/tree.md
```

Tree Generation is still not a release pipeline: proposal review, gray release, release manifests, rollback metadata,
and writing a reviewed `RELEASED_TREE` TTL remain future work.

## TreeProposal Review

Generated proposals are stored in a local file-backed `TreeProposalStore` under `data/tree_proposals/`.
The store tracks proposals, case links, eval results, review logs, and artifact snapshots. Streamlit includes a
TreeProposal review panel that can approve a `DRAFT_TREE` into `CANDIDATE_TREE`, request changes while keeping the
current status, or reject the proposal. This review step is still pre-release: it does not write production TTL, does
not create release manifests, and does not allow Gate `PASS`.

The first Tree Proposal Eval pass is deterministic. It checks schema validity, validation errors, start/root/test/
transition counts, root-to-test coverage, missing test bindings, evidence binding rate, HITL confirmation rate, pending
HITL count, and unsafe blockers. Eval results are written to `data/tree_proposals/eval_results.jsonl` and surfaced in
the review panel, but they do not automatically promote a proposal.

## Test

```bash
uv run pytest
```

Fallback:

```bash
.venv/bin/python -m pytest
```

## Replay and Preference Data

Each diagnostic run can append records under `runs/*.jsonl`. Export derived datasets:

```bash
uv run ft-diag-export-datasets --runs-dir runs --datasets-dir datasets
```

Generated files:

- `planner_sft.jsonl`
- `report_sft.jsonl`
- `preference_pairs.jsonl`
- `dynamic_tree_clusters.jsonl`
- `offline_eval_summary.json`

Run the diagnostic evaluation suite:

```bash
uv run ft-diag-eval --diagnostic-eval --eval-output-dir datasets/eval_results
uv run ft-diag-eval --diagnostic-eval --eval-suite labeled_v1 --eval-output-dir datasets/eval_results_labeled_v1
```

The default suite executes mock work orders plus a non-tree development exploratory case. `labeled_v1` reads the
38-case labeled suite under `data/raw_docs/diagnostic_eval_labeled_cases_v1/` without injecting expected tree/leaf
labels into the diagnostic input. RAG also strips labeled-eval truth fields such as `expected_*`, repair closure, and
human review text before using those files as historical case evidence. Both suites use the same `DiagnosticEngine` as
Streamlit. They write:

- `diagnostic_eval_summary.json`
- `diagnostic_eval_results.jsonl`
- `diagnostic_eval_details.jsonl`

The Streamlit Eval tab can drill into failed cases by group and failure tag, showing expected vs predicted outputs,
planner actions, executed tests, and evidence summaries for each case.

LoRA/QLoRA/DPO training should only be enabled after replay/preference samples are large enough and manually quality-checked.
