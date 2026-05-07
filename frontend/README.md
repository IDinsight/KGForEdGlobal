# Educational Knowledge Graph MCP Server

An MCP server for navigating educational curriculum data in Learning Commons format. The bundled dataset covers the Senegal CE1 *Langue et Communication* curriculum, with standards, learning components, hierarchical navigation, learning progressions, and source provenance.

## Available Tools

All tools are read-only.

### Discovery

- **overview**: Summary statistics, subjects, grade levels, node/relationship counts, and a sample compact-node structure. Start here to orient before drilling in.
- **list_facets**: List filter values available in the KG (subjects, grades, statement types, source labels, node types, relationship types) with counts. Use before `search_items` to discover valid filter values.
- **search_items**: Primary discovery tool. Search `StandardsFrameworkItem` and `LearningComponent` nodes by text query and/or filters for subject, grade, statement type, source label, and node type.
- **browse_subject**: Alternative discovery tool. Browse the hierarchical curriculum tree for a subject, optionally filtered by grade. For the bundled KG, the available subject is `Langue et Communication`.

### Item details

- **get_item**: Get detailed information about a single node by identifier, plus contextual summaries (hierarchy path, child count, learning components, progression links, related items).
- **get_framework**: Framework-level metadata: name, jurisdiction, author, provider, license, graph type, included graph types, and source PDF name.
- **get_learning_components_for_standard**: List the atomic `LearningComponent` skills extracted from a standard.

### Navigation

- **navigate**: Move through the curriculum hierarchy from a known item, in one of five directions: `parent`, `children`, `siblings`, `ancestors`, `descendants`.
- **get_path**: Full path from the curriculum root down to a specific item. For learning components, the path goes through the supported standard.

### Progressions and provenance

- **get_progression**: Trace learning progressions for a standard: prerequisites (`builds_from`), next steps (`builds_towards`), and cross-curricular (`relatesTo`) links. Accepts a direction filter and a depth of 1–3.
- **get_related_items**: Standards connected to the target via `relatesTo`. Focused subset of `get_progression` for when you only need cross-curricular connections.
- **get_provenance**: Source traceability for any item: attribution, author, license, source labels, page indices, bounding boxes, source segment/decision IDs, and LLM rationale when available.

## Setup Instructions

### Prerequisites

- Node.js 18+ installed
- npm or yarn package manager

### Installation

1. **Extract the zip file** to a directory of your choice:
   ```bash
   unzip edu-kg-mcp.zip
   cd edu-kg-mcp
   ```

2. **Install dependencies**:
   ```bash
   npm install
   ```

3. **Build the project**:
   ```bash
   npm run build
   ```

### Configure for Claude Code

Claude Code is the terminal-based agent. If you don't already have it installed, the native installer is the recommended path:

```bash
# macOS / Linux
curl -fsSL https://claude.ai/install.sh | bash

# Windows (PowerShell)
irm https://claude.ai/install.ps1 | iex
```

Or via npm (requires Node.js 18+; do **not** use `sudo`):

```bash
npm install -g @anthropic-ai/claude-code
```

Verify with `claude --version` and `claude doctor`. Authenticate by running `claude` once — it opens a browser for OAuth login. Full install docs: https://code.claude.com/docs/en/setup

Once installed, register the MCP server using one of the three options below.

#### Option 1: CLI (recommended)

```bash
claude mcp add edu-kg --scope user -- node /absolute/path/to/edu-kg-mcp/build/index.js
```

All flags must come **before** the server name; the `--` separates the server name from the command and arguments. Pick a scope based on how you want the server loaded:

| Scope | Loads in | Shared with team | Stored in |
|---|---|---|---|
| `local` (default) | Current project only | No | `~/.claude.json` |
| `project` | Current project only | Yes — committed to git | `.mcp.json` at project root |
| `user` | All your projects | No | `~/.claude.json` |

For team workflows, `--scope project` writes a `.mcp.json` at the repo root that you can commit. Anyone who clones the repo and runs Claude Code in it gets the server (after a one-time approval prompt).

#### Option 2: Import from Claude Desktop

If you've already configured the server in Claude Desktop and you're on macOS or WSL:

```bash
claude mcp add-from-claude-desktop
```

You'll get an interactive picker. Select `edu-kg` and confirm. Use `--scope user` to make it available across all projects.

#### Option 3: Edit the config file directly

Add to your `~/.claude.json` file under the project's `mcpServers` section (equivalent to `--scope local`):

```json
{
  "projects": {
    "/path/to/your/project": {
      "mcpServers": {
        "edu-kg": {
          "type": "stdio",
          "command": "node",
          "args": [
            "/absolute/path/to/edu-kg-mcp/build/index.js"
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
        "/absolute/path/to/edu-kg-mcp/build/index.js"
      ]
    }
  }
}
```

**Important**: Replace `/absolute/path/to/edu-kg-mcp` with the actual path where you extracted the zip. If `node` isn't on the PATH that Claude Code's subprocess sees (common with nvm), replace `"node"` with the absolute path from `which node`.

#### Verify

```bash
claude mcp list           # confirm edu-kg shows up
claude mcp get edu-kg     # show the resolved config
```

Then start a session with `claude` and run `/mcp` inside it to see server status. You should see `edu-kg` connected with 12 tools.

### Configure for Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or the equivalent config file on your OS:

```json
{
  "mcpServers": {
    "edu-kg": {
      "command": "/absolute/path/to/node",
      "args": [
        "/absolute/path/to/edu-kg-mcp/build/index.js"
      ]
    }
  }
}
```

**Important**:
- Replace `/absolute/path/to/edu-kg-mcp` with the actual path where you extracted the zip.
- Replace `/absolute/path/to/node` with the full path to your Node binary (run `which node` to find it).
- If using nvm, the path is typically `~/.nvm/versions/node/vX.X.X/bin/node`.
- Using just `"node"` may not work if Claude Desktop can't find it on PATH.

### Restart Claude

After updating the configuration:
- **Claude Code**: Start a new session, or run `/mcp` to reload servers.
- **Claude Desktop**: Quit and reopen the application.

## Usage Examples

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

## Data

The bundled dataset lives at `examples/kgs/senegal_reading.json` and is loaded relative to the compiled `index.js` at startup. The file is validated against `KnowledgeGraphSchema` (defined in `src/lib/schemas.ts`), which checks that every node has `id`, `labels`, and `properties.identifier`, and every relationship has `id`, `start`, `end`, and `type`. Extra fields are passed through unchanged.

To use a different Learning Commons-format dataset:
1. Drop your JSON file into `examples/kgs/`.
2. Update the filename argument to `loadKnowledgeGraph(...)` in `src/index.ts`.
3. Rebuild with `npm run build`.

## Troubleshooting

- **Server not starting**: Check that Node.js 18+ is installed and that the path in your config points at the compiled `build/index.js`.
- **Tools not appearing**: Ensure you've restarted Claude after config changes. In Claude Code, run `/mcp` to inspect server status and reconnect.
- **Claude Code: server shows up in `claude mcp list` but fails to connect**: Usually a `PATH` issue — Claude Code's subprocess has a different shell environment than your interactive terminal. Either edit the config to use the absolute path to `node` (run `which node` to find it), or ensure nvm initializes in non-interactive shells.
- **Claude Code: "MCP tool output exceeds 10,000 tokens" warning**: The default cap is 25,000 tokens. For broad `browse_subject` calls on large hierarchies, raise it via `MAX_MCP_OUTPUT_TOKENS=50000 claude` when starting your session.
- **`Failed to load knowledge graph: file not found`**: The KG file is resolved relative to the compiled entrypoint as `../../examples/kgs/<filename>`. Verify the file exists at that location and that you've run `npm run build`.
- **`Failed to parse knowledge graph JSON`**: The KG file is not valid JSON. The error message includes the resolved filepath.
- **`Failed to validate knowledge graph`**: The dataset is missing required fields. The error lists the offending paths (e.g. `nodes.3.properties.identifier`). Fix the source data and reload.
- **`Node <id> has multiple known KG labels`**: A node carries more than one of `StandardsFramework`, `StandardsFrameworkItem`, or `LearningComponent`. The partitioner expects these to be mutually exclusive — fix the source data.
