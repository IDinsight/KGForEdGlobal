/**
 * Educational Knowledge Graph MCP Server
 *
 * Provides tools for navigating educational curriculum data in Learning Commons
 * format. Supports curriculum standards with learning components, hierarchical
 * navigation, and learning progression relationships.
 *
 * Architecture:
 *  1. Claude Desktop = MCP Host
 *  2. Claude Desktop creates one MCP client connection via stdio/JSON-RPC
 *  3. The Node MCP server is this file
 *  4. The KG JSON file is parsed into arrays/maps
 *  5. Available MCP tools:
 *      5a. overview
 *      5b. list_facets
 *      5c. search_items
 *      5d. get_item
 *      5e. browse_subject
 *      5f. get_framework
 *      5g. get_path
 *      5h. navigate
 *      5i. get_learning_components_for_standard
 *      5j. get_progression
 *      5k. get_related_items
 *      5l. get_provenance
 */

// Standard Library
import {fileURLToPath} from "url";
import {dirname} from "path";

// Third Party Library
import {Server} from "@modelcontextprotocol/sdk/server/index.js";
import {StdioServerTransport} from "@modelcontextprotocol/sdk/server/stdio.js";
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
} from "./lib/schemas.js";
import {
    buildKnowledgeGraphIndexes,
    createKnowledgeGraphUtils,
    loadKnowledgeGraph,
    toolError,
    toolResult,
} from "./lib/utils.js";

/**
 * Bootstrap the Educational KG MCP Server.
 *
 * 1. Load and index: Read the KG JSON file from disk and build in-memory lookup maps
 *  (nodes by ID, relationships by start/end, SFIs/LCs by identifier).
 * 2. Register tools: Expose read-only KG tools (overview, search, navigate,
 *  progressions, provenance, etc.) via the MCP ListTools handler.
 * 3. Handle calls: Route incoming CallTool requests through schema validations and
 *  resolve them against the in-memory indexes.
 * 4. Connect transport: Bind to stdio (JSON-RPC) so Claude Desktop can spawn this
 *  process as a child and communicate over stdin/stdout.
 */
async function main() {
    // Load KG.
    const kg = loadKnowledgeGraph("senegal_reading.json", dirname(fileURLToPath(import.meta.url)));

    // Build indexes for efficient lookup.
    const indexes = buildKnowledgeGraphIndexes(kg);
    const {frameworks, learningComponents, sfis} = indexes;

    // Create utility functions with KG and indexes.
    const kgUtils = createKnowledgeGraphUtils({kg, ...indexes});
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

    // Initialize MCP server with basic info and empty capabilities (tools are defined
    // in the request handler).
    const server = new Server(
        {
            name: "edu-kg-mcp",
            version: "1.1.0",
        },
        {
            capabilities: {
                tools: {},
            },
        }
    );

    // Handle tool listing requests by returning the available tools with their input
    // schemas and descriptions.
    server.setRequestHandler(ListToolsRequestSchema, async () => {
    return {
        tools: [
            {
                name: "overview",
                title: "Knowledge Graph Overview",
                description:
                    "Get summary statistics and sample structure for the Senegal CE1 Langue et Communication knowledge graph.",
                inputSchema: OverviewInputSchema,
                annotations: {readOnlyHint: true, idempotentHint: true},
            },
            {
                name: "list_facets",
                title: "List KG Facets",
                description:
                    "List available filter values in the KG, including subjects, grades, statement types, source labels, node types, and relationship types.",
                inputSchema: ListFacetsInputSchema,
                annotations: {readOnlyHint: true, idempotentHint: true},
            },
            {
                name: "search_items",
                title: "Search Curriculum Items",
                description:
                    "Search StandardsFrameworkItem and LearningComponent nodes. Supports filters for subject, grade, statement type, source label, and node category.",
                inputSchema: SearchItemsInputSchema,
                annotations: {readOnlyHint: true, idempotentHint: true},
            },
            {
                name: "get_item",
                title: "Get KG Item",
                description:
                    "Get detailed information about a StandardsFrameworkItem, LearningComponent, or framework node by identifier. Includes hierarchy, learning components, progressions, related items, and path summary when available.",
                inputSchema: GetItemInputSchema,
                annotations: {readOnlyHint: true, idempotentHint: true},
            },
            {
                name: "browse_subject",
                title: "Browse Subject Hierarchy",
                description:
                    "Browse the hierarchical curriculum structure for a subject. For the bundled Senegal KG, the available subject is 'Langue et Communication'.",
                inputSchema: BrowseSubjectInputSchema,
                annotations: {readOnlyHint: true, idempotentHint: true},
            },
            {
                name: "get_framework",
                title: "Get Framework Metadata",
                description:
                    "Return framework-level metadata such as name, jurisdiction, author, provider, license, graph type, included graph types, and source PDF name.",
                inputSchema: GetFrameworkInputSchema,
                annotations: {readOnlyHint: true, idempotentHint: true},
            },
            {
                name: "get_path",
                title: "Get Curriculum Path",
                description:
                    "Return the curriculum path for a StandardsFrameworkItem or LearningComponent. LearningComponents are first mapped to their supported standard item.",
                inputSchema: GetPathInputSchema,
                annotations: {readOnlyHint: true, idempotentHint: true},
            },
            {
                name: "navigate",
                title: "Navigate Curriculum Hierarchy",
                description:
                    "Navigate parent, children, siblings, ancestors, or descendants for a curriculum item. For LearningComponents, parent/ancestor navigation returns the supported StandardsFrameworkItem path.",
                inputSchema: NavigateInputSchema,
                annotations: {readOnlyHint: true, idempotentHint: true},
            },
            {
                name: "get_learning_components_for_standard",
                title: "Get Learning Components for Standard",
                description:
                    "Get LearningComponent nodes that support a StandardsFrameworkItem via supports relationships.",
                inputSchema: GetLearningComponentsForStandardInputSchema,
                annotations: {readOnlyHint: true, idempotentHint: true},
            },
            {
                name: "get_progression",
                title: "Get Learning Progression Links",
                description:
                    "Return buildsFrom, buildsTowards, and related standards for a StandardsFrameworkItem using buildsTowards and relatesTo relationships.",
                inputSchema: GetProgressionInputSchema,
                annotations: {readOnlyHint: true, idempotentHint: true},
            },
            {
                name: "get_related_items",
                title: "Get Related Standards",
                description:
                    "Return StandardsFrameworkItem nodes connected to the target item via relatesTo relationships.",
                inputSchema: GetRelatedItemsInputSchema,
                annotations: {readOnlyHint: true, idempotentHint: true},
            },
            {
                name: "get_provenance",
                title: "Get Source Provenance",
                description:
                    "Return source traceability for a StandardsFrameworkItem or LearningComponent, including source labels, page indices, bounding boxes, source segment IDs, attribution, license, and supporting statements when available.",
                inputSchema: GetProvenanceInputSchema,
                annotations: {readOnlyHint: true, idempotentHint: true},
            },
        ],
    };
});
    server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const {name, arguments: args} = request.params;

    try {
        switch (name) {
            case "overview": {
                OverviewSchema.parse(args ?? {});

                const relTypeCounts: Record<string, number> = {};
                for (const rel of kg.relationships) {
                    relTypeCounts[rel.type] = (relTypeCounts[rel.type] || 0) + 1;
                }

                return toolResult({
                    summary: {
                        jurisdiction: frameworks[0]?.properties.jurisdiction || "Unknown",
                        frameworkName: frameworks[0]?.properties.name || "Unknown",
                        graphType: kg.graph_type,
                        includedGraphTypes: kg.included_graph_types,
                        generatedAt: kg.generated_at,
                        totalFrameworks: frameworks.length,
                        totalStandardItems: sfis.length,
                        totalLearningComponents: learningComponents.length,
                        totalRelationships: kg.relationships.length,
                        relationshipTypes: relTypeCounts,
                    },
                    subjects: getUniqueSubjects(),
                    gradeLevels: getUniqueGradeLevels(),
                    sampleStructure: {
                        standardItem: sfis[0] ? compactNode(sfis[0]) : null,
                        learningComponent: learningComponents[0]
                            ? compactNode(learningComponents[0])
                            : null,
                    },
                });
            }

            case "list_facets": {
                ListFacetsSchema.parse(args ?? {});
                return toolResult(getFacetValues());
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
                    query: parsed.query ?? "",
                    filters: {
                        nodeType,
                        subject: parsed.subject,
                        grade: parsed.grade,
                        statementType: parsed.statement_type,
                        sourceLabel: parsed.source_label,
                        limit: parsed.limit,
                    },
                    count: results.length,
                    results: results.map((result) => compactNode(result.item)),
                });
            }

            case "get_item": {
                const {identifier} = GetItemSchema.parse(args ?? {});
                const result = findAnyNode(identifier);

                if (!result) {
                    return toolError(`Item '${identifier}' not found.`, {
                        hint: "Use search_items or list_facets to find valid identifiers.",
                    });
                }

                const {type: itemType, item} = result;

                if (itemType === "standard_item") {
                    const parent = getAncestors(item.id).at(-1) ?? null;
                    const children = getChildrenAny(item.id).filter((node) =>
                        node.labels.includes("StandardsFrameworkItem")
                    );
                    const learningComponentsForStandard = getLearningComponentsForStandard(item.id);
                    const progressions = buildProgressionTraversal(item, "both", 1);
                    const related = getRelatesTo(item.id);

                    return toolResult({
                        type: "standard_item",
                        item: detailedNode(item),
                        path: getPathForNode(item),
                        parent: parent ? compactNode(parent) : null,
                        children: children.map((child) => compactNode(child)),
                        learningComponents: learningComponentsForStandard.map((lc) => compactNode(lc)),
                        learningComponentCount: learningComponentsForStandard.length,
                        learningProgressions: progressions,
                        relatedStandards: related.map((node) => compactNode(node)),
                    });
                }

                if (itemType === "learning_component") {
                    const supportedStandards = getStandardsSupportedByLearningComponent(item.id);
                    const supportRelationships = getSupportRelationshipsForLearningComponent(item.id);

                    return toolResult({
                        type: "learning_component",
                        item: detailedNode(item),
                        path: getPathForNode(item),
                        supportedStandards: supportedStandards.map((standard) => compactNode(standard)),
                        supportRelationships: supportRelationships.map((rel) => ({
                            id: rel.id,
                            type: rel.type,
                            start: rel.start,
                            end: rel.end,
                            properties: rel.properties,
                        })),
                    });
                }

                return toolResult({
                    type: "framework",
                    item: detailedNode(item),
                    children: getChildrenAny(item.id).map((child) => compactNode(child)),
                });
            }

            case "browse_subject": {
                const {subject, grade} = BrowseSubjectSchema.parse(args ?? {});
                const hierarchy = buildHierarchyForSubject(subject, grade);

                if (hierarchy.length === 0) {
                    return toolResult({
                        error: `Subject '${subject}' not found.`,
                        availableSubjects: getUniqueSubjects(),
                    });
                }

                return toolResult({
                    subject,
                    gradeFilter: grade || null,
                    topLevelCount: hierarchy.length,
                    hierarchy,
                });
            }

            case "get_framework": {
                GetFrameworkSchema.parse(args ?? {});
                return toolResult({
                    graph: {
                        docKey: kg.doc_key,
                        exportDialect: kg.export_dialect,
                        generatedAt: kg.generated_at,
                        graphType: kg.graph_type,
                        includedGraphTypes: kg.included_graph_types,
                    },
                    frameworks: frameworks.map((framework) => ({
                        ...compactNode(framework, 1000),
                        jurisdiction: framework.properties.jurisdiction,
                        adoptionStatus: framework.properties.adoption_status,
                        author: framework.properties.author,
                        provider: framework.properties.provider,
                        license: framework.properties.license,
                        inLanguage: framework.properties.in_language,
                        attributionStatement: framework.properties.attribution_statement,
                        sourcePdfName: framework.properties.metadata?.pdf_name,
                        metadata: framework.properties.metadata,
                    })),
                });
            }

            case "get_path": {
                const {identifier} = GetPathSchema.parse(args ?? {});
                const result = findAnyNode(identifier);
                if (!result) {
                    return toolError(`Item '${identifier}' not found.`, {
                        hint: "Use search_items to find a valid identifier.",
                    });
                }
                return toolResult(getPathForNode(result.item));
            }

            case "navigate": {
                const {identifier, direction, depth} = NavigateSchema.parse(args ?? {});
                const result = findAnyNode(identifier);
                if (!result) {
                    return toolError(`Item '${identifier}' not found.`, {
                        hint: "Use search_items to find a valid identifier.",
                    });
                }

                const item = result.item;

                if (item.labels.includes("LearningComponent")) {
                    const supportedStandards = getStandardsSupportedByLearningComponent(item.id);
                    return toolResult({
                        target: compactNode(item),
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
                                            compactNode(node)
                                        )
                                    )
                                    : [],
                    });
                }

                let results: GraphNode[] = [];
                if (direction === "parent") {
                    results = getAncestors(item.id).at(-1)
                        ? [getAncestors(item.id).at(-1)!]
                        : [];
                } else if (direction === "children") {
                    results = getChildrenAny(item.id);
                } else if (direction === "siblings") {
                    results = getSiblingItems(item.id);
                } else if (direction === "ancestors") {
                    results = getAncestors(item.id);
                } else if (direction === "descendants") {
                    results = getDescendants(item.id, depth);
                }

                return toolResult({
                    target: compactNode(item),
                    direction,
                    depth,
                    count: results.length,
                    results: results.map((node) => compactNode(node)),
                });
            }

            case "get_learning_components_for_standard": {
                const {standard_id} = GetLearningComponentsForStandardSchema.parse(args ?? {});
                const standardNode = findStandardItem(standard_id);

                if (!standardNode) {
                    return toolError(`Standard item '${standard_id}' not found.`, {
                        hint: "Use search_items with node_type='standard_item' to find a valid identifier.",
                    });
                }

                const components = getLearningComponentsForStandard(standardNode.id);
                return toolResult({
                    standard: compactNode(standardNode, 1000),
                    learningComponentCount: components.length,
                    learningComponents: components.map((component) => compactNode(component, 500)),
                });
            }

            case "get_progression": {
                const {identifier, direction, depth} = GetProgressionSchema.parse(args ?? {});
                let standardNode = findStandardItem(identifier);
                let mappedFromLearningComponent: Record<string, unknown> | null = null;

                if (!standardNode) {
                    const lc = findLearningComponent(identifier);
                    const supportedStandards = lc
                        ? getStandardsSupportedByLearningComponent(lc.id)
                        : [];
                    standardNode = supportedStandards[0];
                    mappedFromLearningComponent = lc
                        ? {
                            learningComponent: compactNode(lc),
                            supportedStandards: supportedStandards.map((standard) => compactNode(standard)),
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
                const {identifier} = GetRelatedItemsSchema.parse(args ?? {});
                const standardNode = findStandardItem(identifier);
                if (!standardNode) {
                    return toolError(`Standard item '${identifier}' not found.`, {
                        hint: "Use search_items with node_type='standard_item' to find a valid identifier.",
                    });
                }

                const related = getRelatesTo(standardNode.id);
                return toolResult({
                    target: compactNode(standardNode),
                    relatedCount: related.length,
                    relatedItems: related.map((node) => compactNode(node)),
                });
            }

            case "get_provenance": {
                const {identifier} = GetProvenanceSchema.parse(args ?? {});
                const result = findAnyNode(identifier);
                if (!result) {
                    return toolError(`Item '${identifier}' not found.`, {
                        hint: "Use search_items to find a valid identifier.",
                    });
                }
                return toolResult(provenanceForNode(result.item));
            }

            default:
                return toolError(`Unknown tool: ${name}`);
        }
    } catch (error) {
        return toolError(
            `Error: ${error instanceof Error ? error.message : String(error)}`
        );
    }
});

    // Connect the server to the transport (stdio in this case) to start listening for
    // requests.
    const transport = new StdioServerTransport();
    await server.connect(transport);

    // NB: Use console.error() to avoid corrupting JSON-RPC channel.
    console.error("Educational KG MCP Server running on stdio");
}

// Start the server and catch any unhandled errors to prevent silent failures.
main().catch((error) => {
    console.error("Fatal error:", error);
    process.exit(1);
});
