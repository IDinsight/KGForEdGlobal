# Local Setup

!!! warning "Fast-moving development ahead!"
    We strive to keep our documentation accurate and up to date. However, our development cycles move quickly, and occasionally the docs may fall slightly behind. If you run into any issues or something doesn’t work as expected, please don’t hesitate to [reach out](../contact_us.md) — we’re here to help!

## Table of Contents

- [Setup Instructions](#setup-instructions)
- [Local Startup Instructions](#local-startup-instructions)
- [Local Clean up Instructions](#local-clean-up-instructions)
- [The Pipeline](#the-pipeline)

## Setup Instructions

1. Install [direnv](https://direnv.net/docs/installation.html).
2. If you are using `zsh`, then add `eval "$(direnv hook zsh)"` to the end of your `~/.zshrc` file. If you are using `bash`, then add `eval "$(direnv hook bash)"` to the end of your `~/.bashrc` (or `~/.bash_profile`) file. Ensure you reload the file by running `source ~/.zshrc` or `source ~/.bashrc` (or `source ~/.bash_profile`).
3. Install the latest version of [uv](https://docs.astral.sh/uv/) using: `curl -LsSf https://astral.sh/uv/install.sh | sh`
4. Install [pre-commit](https://pre-commit.com/) globally using: `uv tool install pre-commit`
5. Run `git clone git@github.com:IDinsight/KGForEdGlobal.git` and cd into the root directory of the repo.
6. Run `pre-commit install` to set up the git hooks.
7. In the root `.envrc` file, ensure `PROJECT_ENV` is set to `local`.
8. Copy the **root** `.template.env` to `.env` and update the following environment variables in `.env`:
    1. `ANTHROPIC_API_KEY`: Your Anthropic API key if you plan on using Anthropic models in the pipeline.
    2. `OPENAI_API_KEY`: Your OpenAI API key if you plan on using OpenAI models in the pipeline.
    3. `PATHS_PROJECT_DIR`: The absolute path to the root directory of the project.
9. Copy the **root** `.template.env.local` to `.env.local`.
10. Allow `direnv` to load the root environment variables by running `direnv allow`.
11. Create a `data` folder in the root directory. This is where you should place the curriculum PDF files you want to process.
12. Create a `results` folder in the root directory. This is where the output files for each step in the pipeline will be saved.
13. cd into the backend directory of the repo and:
    1. Copy `.template.env.local` to `.env.local`.
    2. Allow `direnv` to load the backend environment variables by running `direnv allow`.

## Local Startup Instructions

1. cd into the `backend` directory of the repo and:
    1. Run `make fresh-env`. This will create a new virtual environment for the backend and install all dependencies.
    2. Run `source .venv/bin/activate`: This will activate the virtual environment created by `make fresh-env`.
2. See [The Pipeline](#the-pipeline) section for instructions on how to run each step of the pipeline.

## Local Clean up Instructions

1. In the backend directory, run `deactivate`. This will exit out of the virtual environment created by `uv`.

## The Pipeline

The curriculum-document processing pipeline converts a source curriculum PDF into a
validated knowledge graph. It has **five conceptual stages implemented through four
main CLI entry points**. The first three stages reconstruct the source document; the
final two stages construct the Academic Standards and Learning Components layers.

All commands below are run from the `backend` directory and consume the same runtime
configuration JSON file. For a detailed description of each stage and its artifacts,
see the [Pipeline Overview](../pipeline/index.md).

### Step 1: Extract Page IR

Render the configured PDF pages and extract one structured `PageIR` per page:

```bash
python src/kgfeg/entries/extract_page_ir.py <config.json>
```

### Step 2: Verify Page IR continuity

Evaluate plausible continuations across adjacent page boundaries and produce the
verified PageIR set:

```bash
python src/kgfeg/entries/verify_page_ir_continuity.py <config.json>
```

### Step 3: Construct Document IR

Deterministically stitch the verified PageIRs into one provenance-preserving
`DocumentIR`:

```bash
python src/kgfeg/entries/stitch_document_ir.py <config.json>
```

### Step 4: Construct the knowledge graph

Build and validate the Academic Standards KG first, then construct Learning Components
from the validated Academic Standards layer:

```bash
python src/kgfeg/entries/create_kgs.py <config.json>
```

`create_kgs.py` therefore implements the final **two conceptual stages** of the
pipeline: Academic Standards KG construction followed by Learning Components
construction.
