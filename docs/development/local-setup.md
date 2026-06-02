# Local Setup

!!! warning "Fast-moving development ahead!"
    We strive to keep our documentation accurate and up to date. However, our development cycles move quickly, and occasionally the docs may fall slightly behind. If you run into any issues or something doesn’t work as expected, please don’t hesitate to [reach out](../contact_us.md) — we’re here to help!

## Table of Contents

- [Setup Instructions](#setup-instructions)
- [Local Startup Instructions](#local-startup-instructions)
- [Local Clean up Instructions](#local-clean-up-instructions)

## Setup Instructions

1. Install [direnv](https://direnv.net/docs/installation.html).
2. If you are using `zsh`, then add `eval "$(direnv hook zsh)"` to the end of your `~/.zshrc` file. If you are using `bash`, then add `eval "$(direnv hook bash)"` to the end of your `~/.bashrc` (or `~/.bash_profile`) file. Ensure you reload the file by running `source ~/.zshrc` or `source ~/.bashrc` (or `source ~/.bash_profile`).
3. Install the latest version of [uv](https://docs.astral.sh/uv/) using: `curl -LsSf https://astral.sh/uv/install.sh | sh`
4. Install [pre-commit](https://pre-commit.com/) globally using: `uv tool install pre-commit`
5. Run `git clone git@github.com:IDinsight/SenegalKG.git` and cd into the root directory of the repo.
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

The SenegalKG pipeline currently converts a raw curriculum PDF document from non-U.S.
countries into a knowledge graph that follows the [Learning Commons ontology](https://docs.learningcommons.org/knowledge-graph/v1-2-0/understanding-knowledge-graph/about-knowledge-graph).

At the moment, we only create the following knowledge graphs from the curriculum PDF:

- Academic Standards
- Learning Components
- Learning Progressions

Each step can be executed from the `backend` directory using their specified commands.

### Step 1: Structural per-page intermediate representation (IR) extraction from PDF

```bash
python src/skg/entries/extract_page_ir.py ../examples/senegal/config_reading_curriculum.json
```

### Step 2: Verifying continuity of extracted page IRs

```bash
python src/skg/entries/verify_page_ir_continuity.py ../examples/senegal/config_reading_curriculum.json
```

### Step 3: Stitching single document IR JSON from (Verified) per-page IR JSONs

```bash
python src/skg/entries/stitch_document_ir.py ../examples/senegal/config_reading_curriculum.json
```

### Step 4: Creating canonical IR from document IR

```bash
python src/skg/entries/create_canonical_ir.py ../examples/senegal/config_reading_curriculum.json
```

### Step 5: Creating knowledge graphs from canonical IR

```bash
python src/skg/entries/create_kgs.py ../examples/senegal/config_reading_curriculum.json
```
