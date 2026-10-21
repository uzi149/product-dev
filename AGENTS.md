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
- Remove anything that sends footage off the local network, such as the hosted
  OpenRouter default. A local vLLM server is the default endpoint here.

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

## Git workflow

- `develop` is the base branch. Always start from the latest `develop`
  (`git checkout develop && git pull`).
- Do each piece of work on a feature branch created from `develop`,
  e.g. `feature/<short-name>`.
- Merge feature branches into `develop` only through a pull request.
- `main` is for releases only. When a release is needed, `develop` is merged into
  `main` through a release PR.
- Never commit or push directly to `develop` or `main`.

## Project context

- Requirements: `docs/Local VLM Camera Intelligence Platform — Technical Scope.pdf`
- Open decisions and answers: `docs/MVP-open-questions.md`; settled decisions go in `docs/ADRs/`
- Python 3.12, `venv` + `pip`, dependencies in `pyproject.toml`
  (`pip install -e ".[dev]"`, add `vlm` for local Qwen). After changing dependencies,
  regenerate `requirements.lock` with `pip freeze --exclude-editable > requirements.lock`.
