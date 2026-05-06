# Educational Knowledge Graph MCP Server

An MCP server for navigating educational curriculum data in Learning Commons format. Supports Tanzania curriculum with standards, learning components, and hierarchical navigation.

## Available Tools

- **overview**: Get summary statistics, available subjects, grade levels, and data structure
- **search**: Search for standards and learning objectives by text query
- **get_item**: Get detailed information about a specific item by identifier
- **browse_subject**: Browse hierarchical structure of standards for a subject
- **get_objectives**: Get learning objectives that support a specific standard

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

3. **Build the project** (if needed):
   ```bash
   npm run build
   ```

### Configure for Claude Code

Add to your `~/.claude.json` file under the project's `mcpServers` section:

```json
{
  "projects": {
    "/path/to/your/project": {
      "mcpServers": {
        "edu-kg": {
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

Or add it globally under the top-level `mcpServers`:

```json
{
  "mcpServers": {
    "edu-kg": {
      "command": "node",
      "args": [
        "/absolute/path/to/edu-kg-mcp/build/index.js"
      ]
    }
  }
}
```

**Important**: Replace `/absolute/path/to/edu-kg-mcp` with the actual path where you extracted the zip.

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
- Replace `/absolute/path/to/edu-kg-mcp` with the actual path where you extracted the zip
- Replace `/absolute/path/to/node` with the full path to your node binary (run `which node` to find it)
- If using nvm, the path is typically `~/.nvm/versions/node/vX.X.X/bin/node`
- Using just `"node"` may not work if Claude Desktop can't find it in PATH

### Restart Claude

After updating the configuration:
- **Claude Code**: Start a new session or run `/mcp` to reload servers
- **Claude Desktop**: Quit and reopen the application

## Usage Examples

Once configured, you can use the MCP tools in your Claude conversations:

1. **Get an overview of the curriculum**:
   > "Use the edu-kg overview tool to see what subjects and grade levels are available"

2. **Search for specific topics**:
   > "Search the education knowledge graph for 'fractions'"

3. **Browse a subject hierarchy**:
   > "Browse the Mathematics subject in the edu-kg"

4. **Get details on a specific standard**:
   > "Get item details for [identifier]"

## Data

The `knowledge_graph.json` file contains the curriculum data. You can replace it with your own Learning Commons format data.

## Troubleshooting

- **Server not starting**: Check that Node.js is installed and the path in your config is correct
- **Tools not appearing**: Ensure you've restarted Claude after config changes
- **Errors loading data**: Verify `knowledge_graph.json` exists in the project directory
