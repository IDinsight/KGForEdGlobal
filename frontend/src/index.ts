/**
 * @file Educational Knowledge Graph MCP Server.
 *
 *   Provides tools for navigating educational curriculum data in Learning Commons
 *   format. Supports curriculum standards with learning components,
 *   hierarchical navigation, and learning progression relationships.
 *
 *   Architecture:
 *
 *   1. Claude Desktop = MCP Host
 *   2. Claude Desktop creates one MCP client connection via stdio/JSON-RPC
 *   3. The Node MCP server is this file
 *   4. The KG JSON file is parsed into arrays/maps
 *   5. Available MCP tools: 5a. overview 5b. list_facets 5c. search_items 5d.
 *        get_item 5e. browse_subject 5f. get_framework 5g. get_path 5h.
 *        navigate 5i. get_learning_components_for_standard 5j. get_progression
 *        5k. get_related_items 5l. get_provenance
 */

// Standard Library
import path from "node:path";
import { fileURLToPath } from "node:url";

// Third Party Library
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";

// Package Library
import {
  buildKnowledgeGraphIndexes,
  loadKnowledgeGraph,
} from "@/lib/kgs/utils.js";
import {
  createKnowledgeGraphUtils,
  registerKnowledgeGraphTools,
} from "@/lib/tools/utils.js";

/**
 * Bootstrap the Educational KG MCP Server.
 *
 * 1. Load and index: Read the KG JSON file from disk and build in-memory lookup
 *    maps (nodes by ID, relationships by start/end, SFIs/LCs by identifier).
 * 2. Register tools: Expose read-only KG tools (overview, search, navigate,
 *    progressions, provenance, etc.) via McpServer.registerTool.
 * 3. Handle calls: Let McpServer route incoming tool calls to each registered
 *    callback, which validates inputs and resolves them against the KG
 *    indexes.
 * 4. Connect transport: Bind to stdio (JSON-RPC) so Claude Desktop can spawn this
 *    process as a child and communicate over stdin/stdout.
 */
async function main(): Promise<void> {
  // Load KG.
  const kg = loadKnowledgeGraph(
    "senegal_reading.json",
    path.dirname(fileURLToPath(import.meta.url)),
  );

  // Build indexes for efficient lookup.
  const indexes = buildKnowledgeGraphIndexes(kg);

  // Create utility functions with KG and indexes.
  const kgUtils = createKnowledgeGraphUtils({ kg, ...indexes });

  // Initialize high-level MCP server; registered tools define capabilities.
  const server = new McpServer({
    name: "edu-kg-mcp",
    version: "0.1.0",
  });

  registerKnowledgeGraphTools({
    indexes,
    kg,
    kgUtils,
    server,
  });

  /*
   * Connect the server to the transport (stdio in this case) to start listening for
   * requests.
   */
  const transport = new StdioServerTransport();
  await server.connect(transport);

  // NB: Use console.error() to avoid corrupting JSON-RPC channel.
  console.error("Educational KG MCP Server running on stdio");
}

// Start the server and catch any unhandled errors to prevent silent failures.
try {
  await main();
} catch (error) {
  // NB: Use console.error() to avoid corrupting JSON-RPC channel.
  console.error("Fatal error:", error);

  // eslint-disable-next-line unicorn/no-process-exit -- this file is the MCP server CLI entrypoint
  process.exit(1);
}
