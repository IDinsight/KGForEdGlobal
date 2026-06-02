# Local Setup

!!! warning "Fast-moving development ahead!"
    We strive to keep our documentation accurate and up to date. However, our development cycles move quickly, and occasionally the docs may fall slightly behind. If you run into any issues or something doesn’t work as expected, please don’t hesitate to [reach out](../contact_us.md) — we’re here to help!

## Table of Contents

- [Setup Instructions](#setup-instructions)
- [Local Startup Instructions](#local-startup-instructions)
- [Local Clean up Instructions](#local-clean-up-instructions)
- [The Pipeline](#the-pipeline)
- [Interacting with the Knowledge Graph](#interacting-with-the-knowledge-graph)

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
2. To interact with the knowledge graph MCP server via either Claude Desktop or Claude Code see [Interacting with the Knowledge Graph](#interacting-with-the-knowledge-graph) section below.
3. See [The Pipeline](#the-pipeline) section for instructions on how to run each step of the pipeline.

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

## Interacting with the Knowledge Graph
To interact with the knowledge graph, you have to first install the run time
dependencies as follows:

1. Install [pnpm](https://pnpm.io/installation) globally if you don't have it already.
2. cd into the `frontend` directory of the repo and run `pnpm install` followed by `pnpm run build` to install and build all dependencies for the MCP server.

### Configure for Claude Code

Claude Code is the terminal-based agent. If you don't already have it installed, the
native installer is the recommended path:

```bash
# macOS/Linux
curl -fsSL https://claude.ai/install.sh | bash

# Windows (PowerShell)
irm https://claude.ai/install.ps1 | iex
```

Or via pnpm (requires Node.js 18+; do **not** use `sudo`):

```bash
pnpm install -g @anthropic-ai/claude-code
```

Verify with `claude --version` and `claude doctor`. Authenticate by running `claude`
once--it opens a browser for OAuth login. Full install docs: https://code.claude.com/docs/en/setup

Once installed, register the MCP server using one of the three options below.

#### Option 1: CLI (recommended)

```bash
claude mcp add edu-kg --scope user -- node /absolute/path/to/project/frontend/build/index.js
```

All flags must come **before** the server name; the `--` separates the server name from
the command and arguments. Pick a scope based on how you want the server loaded:

| Scope | Loads in | Shared with team | Stored in |
|---|---|---|---|
| `local` (default) | Current project only | No | `~/.claude.json` |
| `project` | Current project only | Yes — committed to git | `.mcp.json` at project root |
| `user` | All your projects | No | `~/.claude.json` |

For team workflows, `--scope project` writes a `.mcp.json` at the repo root that you
can commit. Anyone who clones the repo and runs Claude Code in it gets the server
(after a one-time approval prompt).

#### Option 2: Import from Claude Desktop

If you've already configured the server in Claude Desktop and you're on macOS or WSL:

```bash
claude mcp add-from-claude-desktop
```

You'll get an interactive picker. Select `edu-kg` and confirm. Use `--scope user` to
make it available across all projects.

#### Option 3: Edit the config file directly

Add to your `~/.claude.json` file under the project's `mcpServers` section (equivalent
to `--scope local`):

```json
{
  "projects": {
    "/path/to/your/project": {
      "mcpServers": {
        "edu-kg": {
          "type": "stdio",
          "command": "node",
          "args": [
            "/absolute/path/to/project/frontend/build/index.js"
          ]
        }
      }
    }
  }
}
```

Or add it globally under the top-level `mcpServers` (equivalent to `--scope user`):

```json
{
  "mcpServers": {
    "edu-kg": {
      "type": "stdio",
      "command": "node",
      "args": [
        "/absolute/path/to/project/frontend/build/index.js"
      ]
    }
  }
}
```

**Important**: If `node` isn't on the PATH that Claude Code's subprocess sees (common
with nvm), replace `"node"` with the absolute path from `which node`.

#### Verify

```bash
claude mcp list           # Confirm edu-kg shows up
claude mcp get edu-kg     # Show the resolved config
```

Then start a session with `claude` and run `/mcp` inside it to see server status. You
should see `edu-kg` connected with ~12 tools.

### Configure for Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or the
equivalent config file on your OS:

```json
{
  "mcpServers": {
    "edu-kg": {
      "command": "/absolute/path/to/node",
      "args": [
        "/absolute/path/to/project/frontend/build/index.js"
      ]
    }
  }
}
```

**Important**:
- Replace `/absolute/path/to/node` with the full path to your Node binary (run `which node` to find it).
- If using nvm, the path is typically `~/.nvm/versions/node/vX.X.X/bin/node`.
- Using just `"node"` may not work if Claude Desktop can't find it on PATH.

### Restart Claude

After updating configuration files:
- **Claude Code**: Start a new session, or run `/mcp` to reload servers.
- **Claude Desktop**: Quit and reopen the application.

### Usage Examples

Once configured, you can invoke the tools in conversation:

1. **Orient yourself first**:
   > "Use the edu-kg `overview` tool to summarize the bundled curriculum."

2. **Discover available filter values**:
   > "Run `list_facets` on the edu-kg so I know what subjects, grades, and statement types I can filter on."

3. **Search for a topic**:
   > "Search the edu-kg for items about reading comprehension at the CE1 level."

4. **Browse the hierarchy top-down**:
   > "Browse the `Langue et Communication` subject in the edu-kg, filtered to CE1."

5. **Drill into a standard**:
   > "Get the item details for [identifier], then list its learning components and trace its `builds_towards` progression."

6. **Trace a result back to its source PDF**:
   > "Show me the provenance for [identifier] — page indices and any LLM rationale."

## Knowledge Graph Artifacts

The bundled knowledge graph artifact lives at `examples/kgs/senegal_reading.json` and
is loaded relative to the compiled `index.js` at startup. The file is validated against
`KnowledgeGraphSchema` (defined in `src/lib/schemas.ts`), which checks that every node
has `id`, `labels`, and `properties.identifier`, and every relationship has `id`,
`start`, `end`, and `type`. Extra fields are passed through unchanged.

To use a different Learning Commons-format dataset:
1. Drop your JSON file into `examples/kgs/`.
2. Update the filename argument to `loadKnowledgeGraph(...)` in `src/index.ts`.
3. Rebuild with `pnpm run build`.

## Troubleshooting

- **Server not starting**: Check that Node.js 18+ is installed and that the path in your config points at the compiled `build/index.js`.
- **Tools not appearing**: Ensure you've restarted Claude after config changes. In Claude Code, run `/mcp` to inspect server status and reconnect.
- **Claude Code: server shows up in `claude mcp list` but fails to connect**: Usually a `PATH` issue — Claude Code's subprocess has a different shell environment than your interactive terminal. Either edit the config to use the absolute path to `node` (run `which node` to find it), or ensure nvm initializes in non-interactive shells.
- **Claude Code: "MCP tool output exceeds 10,000 tokens" warning**: The default cap is 25,000 tokens. For broad `browse_subject` calls on large hierarchies, raise it via `MAX_MCP_OUTPUT_TOKENS=50000 claude` when starting your session.
- **`Failed to load knowledge graph: file not found`**: The KG file is resolved relative to the compiled entrypoint as `../../examples/kgs/<filename>`. Verify the file exists at that location and that you've run `pnpm run build`.
- **`Failed to parse knowledge graph JSON`**: The KG file is not valid JSON. The error message includes the resolved filepath.
- **`Failed to validate knowledge graph`**: The dataset is missing required fields. The error lists the offending paths (e.g. `nodes.3.properties.identifier`). Fix the source data and reload.
- **`Node <id> has multiple known KG labels`**: A node carries more than one of `StandardsFramework`, `StandardsFrameworkItem`, or `LearningComponent`. The partitioner expects these to be mutually exclusive — fix the source data.
