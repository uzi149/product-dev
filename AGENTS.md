# Agent Instructions

## Reference code for VLM work

Before starting any VLM task, check the reference repository at `D:\Nodlays\langvio`
first. VLM tasks include model loading, prompting, sending frames or images to a model,
parsing model JSON output, the OpenAI-compatible (vLLM) client, keyframe sampling, and
device selection. Reuse what it already solves instead of writing it from scratch.

The most relevant files there:

| File | What it has |
|---|---|
| `langvio/llm/qwen_api.py` | OpenAI-compatible chat client: retries, rate-limit cooldown, base64 images |
| `langvio/llm/qwen_loader.py` | Shared in-process Qwen-VL loader, cuda/mps/cpu selection, generation lock |
| `langvio/vision/qwen_vlm/detector.py` | Multi-image prompting (`_generate`), `_resize`, `_try_parse_json`, keyframe sampling in `process_video`, scene-description prompt |
| `langvio/core/registry.py` | Registry pattern for swappable model processors |
| `langvio/prompts/templates.py` | Natural-language → strict JSON prompt style |

How to use it:

- **Copy and adapt the code into this repo.** Do not install it as a package, import it,
  or add it to `pyproject.toml`.
- Put adapted code where it belongs in this repo's structure, usually
  `shared/model_adapters/` or `shared/video/`.
- Take only what the task needs. Leave out its YOLO-World, ByteTracker, visualizers,
  Flask webapp, CLI and LangChain wrappers.
- Keep the reference code's VLM backend choice: the OpenRouter API is the default when its
  key is set, and the local in-process model is the fallback when it isn't (ADR 0019). Keep
  the endpoint configurable (`VLM_BASE_URL`) so the pilot can point it at a local vLLM server.
  Only test videos may be analysed through OpenRouter; real camera footage stays on the local
  network.
- Embeddings always run locally (ADR 0020); don't add a hosted embedding service or a
  run-time fallback between embedding models.

## Naming rule: no reference-repo name in this codebase

The word `langvio` (in any casing) must never appear anywhere in this repository's code.
That covers:

- module, package, file, class, function and variable names
- comments, docstrings and log messages
- environment variable names (e.g. `LANGVIO_BACKEND` becomes a project name such as
  `VLM_BACKEND`)
- strings, HTTP headers (e.g. `X-Title`), error messages and install hints
- config keys and default paths (e.g. `langvio.log`)

When copying code, rename all of these to this project's own naming.

## Decisions while coding

When implementing, if a feature needs a decision that is critical for the product, do not
decide it yourself. Ask the user first:

- Explain the decision briefly and why it matters.
- Give the options, each with its trade-off.
- Mark one option as **recommended** and say why.

Small implementation details that don't change the product's behaviour can be decided
without asking, following the plan and the existing code.

## MVP workflow: plan each step before coding

The MVP is built from `docs/MVP-implementation-plan.md`, one step (feature branch) at a
time. For every step, follow this order:

1. **Write the step plan first.** Before any code, create
   `docs/plans/<step>-<branch-name>.md` (e.g. `docs/plans/1.1-infra-compose.md`). It
   covers what will be built, the files involved, the tests, the acceptance check and the
   open questions for that step.
2. **Settle the questions.** Ask the user every product-critical question, following
   "Decisions while coding" above (options + a recommended one).
3. **Record decisions as ADRs.** Write each settled decision in `docs/ADRs/` as
   `NNNN-short-title.md` (e.g. `0001-docker-for-infrastructure.md`) with: context,
   options considered, decision, consequences. Link every ADR from the step plan.
4. **Update the MVP plan.** Change the matching step in
   `docs/MVP-implementation-plan.md` to reflect the decisions, and link the step plan
   and its ADRs from it.
5. **Get approval, then code.** Start coding only after the user approves the step plan.

`docs/` is git-ignored and stays on the local machine. Write and update the step plans, ADRs,
the MVP plan and the testing guide there, but never commit or push anything under `docs/`, and
don't change `.gitignore` to track it. A PR can say that its plan and ADRs are local.

If a new decision comes up while coding, stop, ask, then add the ADR and update both plans
before continuing.

## Git workflow

- `develop` is the base branch. Always start from the latest `develop`
  (`git checkout develop && git pull`).
- Do each piece of work on a feature branch created from `develop`,
  e.g. `feature/<short-name>`.
- Merge feature branches into `develop` only through a pull request.
- `main` is for releases only. When a release is needed, `develop` is merged into
  `main` through a release PR.
- Never commit or push directly to `develop` or `main`.
- Never commit or push `docs/` (see "MVP workflow" above).

## Project context

- Requirements: `docs/Local VLM Camera Intelligence Platform — Technical Scope.pdf`
- Open decisions and answers: `docs/MVP-open-questions.md`; settled decisions go in `docs/ADRs/`
- Python 3.12, `venv` + `pip`, dependencies in `pyproject.toml`
  (`pip install -e ".[dev]"`, add `vlm` for local Qwen). After changing dependencies,
  regenerate `requirements.lock` with `pip freeze --exclude-editable > requirements.lock`.
