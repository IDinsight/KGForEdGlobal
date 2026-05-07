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
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";

// Package Library
import {
  BrowseSubjectInputSchema,
  BrowseSubjectSchema,
  GetFrameworkInputSchema,
  GetFrameworkSchema,
  GetItemInputSchema,
  GetItemSchema,
  GetLearningComponentsForStandardInputSchema,
  GetLearningComponentsForStandardSchema,
  GetPathInputSchema,
  GetPathSchema,
  GetProgressionInputSchema,
  GetProgressionSchema,
  GetProvenanceInputSchema,
  GetProvenanceSchema,
  GetRelatedItemsInputSchema,
  GetRelatedItemsSchema,
  ListFacetsInputSchema,
  ListFacetsSchema,
  NavigateInputSchema,
  NavigateSchema,
  OverviewInputSchema,
  OverviewSchema,
  SearchItemsInputSchema,
  SearchItemsSchema,
} from "@/lib/schemas.js";
import {
  buildKnowledgeGraphIndexes,
  createKnowledgeGraphUtils,
  loadKnowledgeGraph,
  toolError,
  toolResult,
} from "@/lib/utils.js";

/**
 * Bootstrap the Educational KG MCP Server.
 *
 * 1. Load and index: Read the KG JSON file from disk and build in-memory lookup
 *    maps (nodes by ID, relationships by start/end, SFIs/LCs by identifier).
 * 2. Register tools: Expose read-only KG tools (overview, search, navigate,
 *    progressions, provenance, etc.) via the MCP ListTools handler.
 * 3. Handle calls: Route incoming CallTool requests through schema validations and
 *    resolve them against the in-memory indexes.
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
  const { frameworks, learningComponents, sfis } = indexes;

  // Create utility functions with KG and indexes.
  const kgUtils = createKnowledgeGraphUtils({ kg, ...indexes });
  const {
    buildHierarchyForSubject,
    buildProgressionTraversal,
    compactNode,
    detailedNode,
    findAnyNode,
    findLearningComponent,
    findStandardItem,
    getAncestors,
    getChildrenAny,
    getDescendants,
    getFacetValues,
    getLearningComponentsForStandard,
    getPathForNode,
    getRelatesTo,
    getSiblingItems,
    getStandardsSupportedByLearningComponent,
    getSupportRelationshipsForLearningComponent,
    getUniqueGradeLevels,
    getUniqueSubjects,
    provenanceForNode,
    searchItems,
  } = kgUtils;

  /*
   * Initialize MCP server with basic info and empty capabilities (tools are defined in
   * the request handler).
   */
  const server = new Server(
    {
      name: "edu-kg-mcp",
      version: "1.1.0",
    },
    {
      capabilities: {
        tools: {},
      },
    },
  );

  /*
   * Handle tool listing requests by returning the available tools with their input
   * schemas and descriptions.
   */
  server.setRequestHandler(ListToolsRequestSchema, () => {
    return {
      tools: [
        {
          annotations: {
            idempotentHint: true,
            openWorldHint: false,
            readOnlyHint: true,
          },
          description:
            "Alternative discovery tool. Browse the hierarchical curriculum tree for a subject, optionally filtered by grade. For the bundled Senegal KG, the available subject is 'Langue et Communication'. Returns a nested tree of items with identifiers that can be passed to get_item, navigate, or other tools. Prefer this over search_items when exploring curriculum structure top-down.",
          inputSchema: BrowseSubjectInputSchema,
          name: "browse_subject",
          title: "Browse Subject Hierarchy",
        },
        {
          annotations: {
            idempotentHint: true,
            openWorldHint: false,
            readOnlyHint: true,
          },
          description:
            "Return framework-level metadata: name, jurisdiction, author, provider, license, graph type, included graph types, and source PDF name. Use this to answer questions about the curriculum document itself rather than individual items.",
          inputSchema: GetFrameworkInputSchema,
          name: "get_framework",
          title: "Get Framework Metadata",
        },
        {
          annotations: {
            idempotentHint: true,
            openWorldHint: false,
            readOnlyHint: true,
          },
          description:
            "Get detailed information about a single node by identifier. Use an identifier returned by search_items or browse_subject. Returns full properties plus contextual summaries: hierarchy path, child count, learning components, progression links, and related items when available.",
          inputSchema: GetItemInputSchema,
          name: "get_item",
          title: "Get KG Item",
        },
        {
          annotations: {
            idempotentHint: true,
            openWorldHint: false,
            readOnlyHint: true,
          },
          description:
            "List the atomic LearningComponent skills extracted from a StandardsFrameworkItem. Requires a standard item identifier from search_items or browse_subject (node_type='standard_item'). Use this to break a curriculum standard down into its constituent teachable skills.",
          inputSchema: GetLearningComponentsForStandardInputSchema,
          name: "get_learning_components_for_standard",
          title: "Get Learning Components for Standard",
        },
        {
          annotations: {
            idempotentHint: true,
            openWorldHint: false,
            readOnlyHint: true,
          },
          description:
            "Return the full path from the curriculum root down to a specific item. Requires an identifier from search_items or browse_subject. For LearningComponents, the path goes through the supported StandardsFrameworkItem. Use this to understand where an item sits in the overall curriculum hierarchy.",
          inputSchema: GetPathInputSchema,
          name: "get_path",
          title: "Get Curriculum Path",
        },
        {
          annotations: {
            idempotentHint: true,
            openWorldHint: false,
            readOnlyHint: true,
          },
          description:
            "Trace learning progressions for a StandardsFrameworkItem: what it builds from (prerequisites), what it builds towards (next steps), and cross-curricular related standards. Requires an identifier from search_items or browse_subject. Accepts an optional direction filter ('builds_from', 'builds_towards', 'related', or 'both') and depth (1–3). If given a LearningComponent identifier, it maps to the supported standard first.",
          inputSchema: GetProgressionInputSchema,
          name: "get_progression",
          title: "Get Learning Progression Links",
        },
        {
          annotations: {
            idempotentHint: true,
            openWorldHint: false,
            readOnlyHint: true,
          },
          description:
            "Return source traceability for any item: attribution, author, license, source labels, page indices, bounding boxes, source segment/decision IDs, and LLM rationale when available. Requires an identifier from search_items or browse_subject. Use this to answer questions about where a curriculum item or learning component originally came from in the source PDF.",
          inputSchema: GetProvenanceInputSchema,
          name: "get_provenance",
          title: "Get Source Provenance",
        },
        {
          annotations: {
            idempotentHint: true,
            openWorldHint: false,
            readOnlyHint: true,
          },
          description:
            "Return StandardsFrameworkItem nodes connected to the target via relatesTo relationships. Requires an identifier from search_items or browse_subject. This is a focused subset of get_progression—use it when you only need cross-curricular connections without the full builds-from/builds-towards chains.",
          inputSchema: GetRelatedItemsInputSchema,
          name: "get_related_items",
          title: "Get Related Standards",
        },
        {
          annotations: {
            idempotentHint: true,
            openWorldHint: false,
            readOnlyHint: true,
          },
          description:
            "List available filter values in the KG, including subjects, grades, statement types, source labels, node types, and relationship types with counts. Use this before search_items to discover valid filter values.",
          inputSchema: ListFacetsInputSchema,
          name: "list_facets",
          title: "List KG Facets",
        },
        {
          annotations: {
            idempotentHint: true,
            openWorldHint: false,
            readOnlyHint: true,
          },
          description:
            "Move through the curriculum hierarchy from a known item. Requires an identifier from search_items or browse_subject plus a direction: parent, children, siblings, ancestors, or descendants. For LearningComponents, parent/ancestor navigation follows the supports relationship to the linked StandardsFrameworkItem. Use this to explore neighbors of an item you already have.",
          inputSchema: NavigateInputSchema,
          name: "navigate",
          title: "Navigate Curriculum Hierarchy",
        },
        {
          annotations: {
            idempotentHint: true,
            openWorldHint: false,
            readOnlyHint: true,
          },
          description:
            "Start here. Get summary statistics and sample structure for the Senegal CE1 Langue et Communication knowledge graph. Returns subjects, grade levels, node/relationship counts, and a sample item. Use this to orient before drilling in with search_items or browse_subject.",
          inputSchema: OverviewInputSchema,
          name: "overview",
          title: "Knowledge Graph Overview",
        },
        {
          annotations: {
            idempotentHint: true,
            openWorldHint: false,
            readOnlyHint: true,
          },
          description:
            "Primary discovery tool. Search StandardsFrameworkItem and LearningComponent nodes by text query and/or filters for subject, grade, statement type, source label, and node category. Returns identifiers that can be passed to get_item, navigate, get_path, get_progression, get_learning_components_for_standard, get_related_items, or get_provenance.",
          inputSchema: SearchItemsInputSchema,
          name: "search_items",
          title: "Search Curriculum Items",
        },
      ],
    };
  });
  server.setRequestHandler(CallToolRequestSchema, (request) => {
    const { arguments: args, name } = request.params;

    try {
      switch (name) {
        case "browse_subject": {
          const { grade, subject } = BrowseSubjectSchema.parse(args ?? {});
          const hierarchy = buildHierarchyForSubject(subject, grade);

          if (hierarchy.length === 0) {
            return toolResult({
              availableSubjects: getUniqueSubjects(),
              error: `Subject '${subject}' not found.`,
            });
          }

          return toolResult({
            gradeFilter: grade || null,
            hierarchy,
            subject,
            topLevelCount: hierarchy.length,
          });
        }
        case "get_framework": {
          GetFrameworkSchema.parse(args ?? {});
          return toolResult({
            frameworks: frameworks.map((framework) => ({
              ...compactNode(framework, 1000),
              adoptionStatus: framework.properties.adoption_status,
              attributionStatement: framework.properties.attribution_statement,
              author: framework.properties.author,
              inLanguage: framework.properties.in_language,
              jurisdiction: framework.properties.jurisdiction,
              license: framework.properties.license,
              metadata: framework.properties.metadata,
              provider: framework.properties.provider,
              sourcePdfName: framework.properties.metadata?.pdf_name,
            })),
            graph: {
              docKey: kg.doc_key,
              exportDialect: kg.export_dialect,
              generatedAt: kg.generated_at,
              graphType: kg.graph_type,
              includedGraphTypes: kg.included_graph_types,
            },
          });
        }
        case "get_item": {
          const { identifier } = GetItemSchema.parse(args ?? {});
          const result = findAnyNode(identifier);

          if (!result) {
            return toolError(`Item '${identifier}' not found.`, {
              hint: "Use search_items or list_facets to find valid identifiers.",
            });
          }

          const { item, type: itemType } = result;

          if (itemType === "standard_item") {
            const parent = getAncestors(item.id).at(-1) ?? null;
            const children = getChildrenAny(item.id).filter((node) =>
              node.labels.includes("StandardsFrameworkItem"),
            );
            const learningComponentsForStandard =
              getLearningComponentsForStandard(item.id);
            const progressions = buildProgressionTraversal(item, "both", 1);
            const related = getRelatesTo(item.id);
            return toolResult({
              children: children.map((child) => compactNode(child)),
              item: detailedNode(item),
              learningComponentCount: learningComponentsForStandard.length,
              learningComponents: learningComponentsForStandard.map((lc) =>
                compactNode(lc),
              ),
              learningProgressions: progressions,
              parent: parent ? compactNode(parent) : null,
              path: getPathForNode(item),
              relatedStandards: related.map((node) => compactNode(node)),
              type: "standard_item",
            });
          }

          if (itemType === "learning_component") {
            const supportedStandards = getStandardsSupportedByLearningComponent(
              item.id,
            );
            const supportRelationships =
              getSupportRelationshipsForLearningComponent(item.id);
            return toolResult({
              item: detailedNode(item),
              path: getPathForNode(item),
              supportedStandards: supportedStandards.map((standard) =>
                compactNode(standard),
              ),
              supportRelationships: supportRelationships.map((rel) => ({
                end: rel.end,
                id: rel.id,
                properties: rel.properties,
                start: rel.start,
                type: rel.type,
              })),
              type: "learning_component",
            });
          }

          return toolResult({
            children: getChildrenAny(item.id).map((child) =>
              compactNode(child),
            ),
            item: detailedNode(item),
            type: "framework",
          });
        }
        case "get_learning_components_for_standard": {
          const { standard_id } = GetLearningComponentsForStandardSchema.parse(
            args ?? {},
          );
          const standardNode = findStandardItem(standard_id);

          if (!standardNode) {
            return toolError(`Standard item '${standard_id}' not found.`, {
              hint: "Use search_items with node_type='standard_item' to find a valid identifier.",
            });
          }

          const components = getLearningComponentsForStandard(standardNode.id);
          return toolResult({
            learningComponentCount: components.length,
            learningComponents: components.map((component) =>
              compactNode(component, 500),
            ),
            standard: compactNode(standardNode, 1000),
          });
        }
        case "get_path": {
          const { identifier } = GetPathSchema.parse(args ?? {});
          const result = findAnyNode(identifier);

          if (!result) {
            return toolError(`Item '${identifier}' not found.`, {
              hint: "Use search_items to find a valid identifier.",
            });
          }

          return toolResult(getPathForNode(result.item));
        }
        case "get_progression": {
          const { depth, direction, identifier } = GetProgressionSchema.parse(
            args ?? {},
          );
          let standardNode = findStandardItem(identifier);
          let mappedFromLearningComponent: Record<string, unknown> | null =
            null;

          if (!standardNode) {
            const lc = findLearningComponent(identifier);
            const supportedStandards = lc
              ? getStandardsSupportedByLearningComponent(lc.id)
              : [];
            standardNode = supportedStandards[0];
            mappedFromLearningComponent = lc
              ? {
                  learningComponent: compactNode(lc),
                  supportedStandards: supportedStandards.map((standard) =>
                    compactNode(standard),
                  ),
                }
              : null;
          }

          if (!standardNode) {
            return toolError(`Standard item '${identifier}' not found.`, {
              hint: "Use search_items with node_type='standard_item' to find a valid identifier.",
            });
          }

          return toolResult({
            mappedFromLearningComponent,
            ...buildProgressionTraversal(standardNode, direction, depth),
          });
        }
        case "get_related_items": {
          const { identifier } = GetRelatedItemsSchema.parse(args ?? {});
          const standardNode = findStandardItem(identifier);

          if (!standardNode) {
            return toolError(`Standard item '${identifier}' not found.`, {
              hint: "Use search_items with node_type='standard_item' to find a valid identifier.",
            });
          }

          const related = getRelatesTo(standardNode.id);
          return toolResult({
            relatedCount: related.length,
            relatedItems: related.map((node) => compactNode(node)),
            target: compactNode(standardNode),
          });
        }
        case "get_provenance": {
          const { identifier } = GetProvenanceSchema.parse(args ?? {});
          const result = findAnyNode(identifier);

          if (!result) {
            return toolError(`Item '${identifier}' not found.`, {
              hint: "Use search_items to find a valid identifier.",
            });
          }

          return toolResult(provenanceForNode(result.item));
        }
        case "list_facets": {
          ListFacetsSchema.parse(args ?? {});
          return toolResult(getFacetValues());
        }
        case "navigate": {
          const { depth, direction, identifier } = NavigateSchema.parse(
            args ?? {},
          );
          const result = findAnyNode(identifier);

          if (!result) {
            return toolError(`Item '${identifier}' not found.`, {
              hint: "Use search_items to find a valid identifier.",
            });
          }

          const item = result.item;

          if (item.labels.includes("LearningComponent")) {
            const supportedStandards = getStandardsSupportedByLearningComponent(
              item.id,
            );
            return toolResult({
              direction,
              note:
                direction === "parent" || direction === "ancestors"
                  ? "LearningComponents are attached to curriculum items through supports relationships, not hasChild hierarchy."
                  : "LearningComponents do not have hasChild hierarchy in this KG.",
              results:
                direction === "parent"
                  ? supportedStandards.map((standard) => compactNode(standard))
                  : direction === "ancestors"
                    ? supportedStandards.flatMap((standard) =>
                        [...getAncestors(standard.id), standard].map((node) =>
                          compactNode(node),
                        ),
                      )
                    : [],
              target: compactNode(item),
            });
          }

          let results: GraphNode[] = [];

          switch (direction) {
            case "parent": {
              const ancestors = getAncestors(item.id);
              const parent = ancestors.at(-1);
              results = parent ? [parent] : [];

              break;
            }
            case "children": {
              results = getChildrenAny(item.id);

              break;
            }
            case "siblings": {
              results = getSiblingItems(item.id);

              break;
            }
            case "ancestors": {
              results = getAncestors(item.id);

              break;
            }
            case "descendants": {
              results = getDescendants(item.id, depth);

              break;
            }
            // No default
          }

          return toolResult({
            count: results.length,
            depth,
            direction,
            results: results.map((node) => compactNode(node)),
            target: compactNode(item),
          });
        }
        case "overview": {
          OverviewSchema.parse(args ?? {});
          const relTypeCounts: Record<string, number> = {};

          for (const rel of kg.relationships) {
            relTypeCounts[rel.type] = (relTypeCounts[rel.type] || 0) + 1;
          }

          return toolResult({
            gradeLevels: getUniqueGradeLevels(),
            sampleStructure: {
              learningComponent: learningComponents[0]
                ? compactNode(learningComponents[0])
                : null,
              standardItem: sfis[0] ? compactNode(sfis[0]) : null,
            },
            subjects: getUniqueSubjects(),
            summary: {
              frameworkName: frameworks[0]?.properties.name || "Unknown",
              generatedAt: kg.generated_at,
              graphType: kg.graph_type,
              includedGraphTypes: kg.included_graph_types,
              jurisdiction: frameworks[0]?.properties.jurisdiction || "Unknown",
              relationshipTypes: relTypeCounts,
              totalFrameworks: frameworks.length,
              totalLearningComponents: learningComponents.length,
              totalRelationships: kg.relationships.length,
              totalStandardItems: sfis.length,
            },
          });
        }
        case "search_items": {
          const parsed = SearchItemsSchema.parse(args ?? {});
          const nodeType = parsed.node_type ?? "all";
          const results = searchItems({
            grade: parsed.grade,
            limit: parsed.limit,
            nodeType,
            query: parsed.query,
            sourceLabel: parsed.source_label,
            statementType: parsed.statement_type,
            subject: parsed.subject,
          });
          return toolResult({
            count: results.length,
            filters: {
              grade: parsed.grade,
              limit: parsed.limit,
              nodeType,
              sourceLabel: parsed.source_label,
              statementType: parsed.statement_type,
              subject: parsed.subject,
            },
            query: parsed.query ?? "",
            results: results.map((result) => compactNode(result.item)),
          });
        }
        default: {
          return toolError(`Unknown tool: ${name}`);
        }
      }
    } catch (error) {
      return toolError(
        `Error: ${error instanceof Error ? error.message : String(error)}`,
      );
    }
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
  console.error("Fatal error:", error);

  // eslint-disable-next-line unicorn/no-process-exit -- this file is the MCP server CLI entrypoint
  process.exit(1);
}
