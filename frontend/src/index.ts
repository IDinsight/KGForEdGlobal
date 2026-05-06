#!/usr/bin/env node
/**
 * Educational Knowledge Graph MCP Server
 *
 * Provides tools for navigating educational curriculum data in Learning Commons
 * format. Supports curriculum standards with learning components, hierarchical
 * navigation, and learning progression relationships.
 *
 * The architecture is:
 *  1. Claude Desktop = MCP Host
 *  2. Claude Desktop creates one MCP client connection via stdio/JSON-RPC
 *  3. The Node MCP server is this file (index.ts)
 *  4. The KG JSON file is parsed into arrays/maps
 *  5. Available MCP tools:
 *      5a. overview
 *      5b. search
 *      5c. get_item
 *      5d. browse_subject
 *      5e. get_objectives
 */

import {Server} from "@modelcontextprotocol/sdk/server/index.js";
import {StdioServerTransport} from "@modelcontextprotocol/sdk/server/stdio.js";
import {
    CallToolRequestSchema,
    ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import {z} from "zod";
import {readFileSync} from "fs";
import {fileURLToPath} from "url";
import {dirname, join} from "path";

// Define root paths.
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Global type aliases.
type ISODateString = string;
type RelationshipEntity = string;
type RelationshipEntityKey = string;
type RelationshipType = "hasChild" | "supports" | "buildsTowards" | "relatesTo";
type UUIDString = string;

// Global interfaces.
interface GraphNode {
    id: string;
    labels: string[];
    properties: NodeProperties;
}

interface GraphRelationship {
    end: UUIDString; // Target node id
    id: UUIDString;
    properties: RelationshipProperties;
    start: UUIDString; // Source node id
    type: RelationshipType; // hasChild, supports, buildsTowards, relatesTo
}

interface KnowledgeGraph {
    doc_key: string;
    export_dialect: string;
    generated_at: string;
    graph_type: string;
    included_graph_types: string[];
    nodes: GraphNode[];
    relationships: GraphRelationship[];
}

interface KnowledgeGraphIndexes {
    frameworks: GraphNode[];
    lcByIdentifier: Map<string, GraphNode>;
    learningComponents: GraphNode[];
    nodesById: Map<string, GraphNode>;
    relsByEnd: Map<string, GraphRelationship[]>;
    relsByStart: Map<string, GraphRelationship[]>;
    sfis: GraphNode[];
    sfisByIdentifier: Map<string, GraphNode>;
    unknownNodes: GraphNode[];
}

interface NodeMetadata {
    canonical_node_id?: UUIDString;

    // Common SFI metadata.
    bbox?: number[];
    bbox_ref?: string;
    canonical_path_key?: string;
    identity_basis?: string;
    local_code?: string | null;
    normalized_text?: string;
    page_indices?: number[];
    progression_context?: Record<string, unknown>;
    role?: string;
    source_decision_ids?: string[];
    source_label?: string;
    source_segment_ids?: string[];

    // Common LearningComponent metadata.
    id_source_kind?: string;
    llm_rationale?: string;
    provenance?: Record<string, unknown>;
    split_display_text?: string;
    split_hash?: string;
    split_id_text?: string;
    split_index?: number;
    split_policy?: string;
    split_truncated?: boolean;
    supporting_sfi_aux_statements?: unknown[];
    supporting_sfi_canonical_path_key?: string;
    supporting_sfi_case_uuid?: UUIDString;
    supporting_sfi_grade_level?: string[];
    supporting_sfi_in_language?: string;
    supporting_sfi_normalized_statement_type?: string;
    supporting_sfi_progression_context?: Record<string, unknown>;
    supporting_sfi_role?: string;
    supporting_sfi_source_label?: string;
    supporting_sfi_statement_type?: string;

    // Framework metadata.
    decision_set_id?: string;
    doc_key?: string;
    export_dialect?: string;
    pdf_name?: string;
    provenance_context?: Record<string, unknown>;

    // Keep this because metadata is intentionally extensible.
    [key: string]: unknown;
}

interface NodeProperties {
    academic_subject?: string;
    adoption_status?: string;
    attribution_statement?: string;
    author?: string;
    case_identifier_uri?: string;
    case_identifier_uuid?: UUIDString;
    date_created?: ISODateString | null;
    date_modified?: ISODateString | null;
    description?: string | null;
    grade_level?: string[];
    identifier: UUIDString;
    in_language?: string;
    jurisdiction?: string;
    license?: string;
    metadata?: NodeMetadata;
    name?: string;
    normalized_statement_type?: "Standard" | "Standard Grouping" | "Other" | string;
    notes?: string | null;
    provider?: string;
    statement_code?: string | null;
    statement_type?: string | null;

    [key: string]: unknown;
}

interface RelationshipMetadata {
    // Common metadata.
    source_kg?: string;

    // `hasChild` metadata.
    canonical_order_index?: number;
    canonical_parent_id?: UUIDString;
    canonical_child_id?: UUIDString;
    canonical_edge_source_decision_ids?: string[];
    canonical_edge_source_segment_ids?: string[];
    export_order_index?: number;
    export_parent_id?: UUIDString;

    // `supports` metadata.
    supporting_sfi_case_uuid?: UUIDString;

    // Learning progressions metadata: buildsTowards/relatesTo.
    phase?: string;
    level_label?: string;
    candidate_order_index?: number;
    candidate_uid?: string;
    dedupe?: Record<string, unknown>;
    confidence?: number;
    evidence?: string | Record<string, unknown>;
    inference_context_scope?: string;
    inference_source?: string;
    inference_type?: string;
    source_sfi_context?: Record<string, unknown>;
    target_sfi_context?: Record<string, unknown>;
    source_sfi_inference_context?: Record<string, unknown>;
    target_sfi_inference_context?: Record<string, unknown>;

    // buildsTowards specific metadata.
    lp_bucket_key?: string;
    lp_thread_key?: string;
    progression_subtype?: string;
    progression_subtype_source?: string;
    subject_label?: string;
    topic_path_examples?: unknown[];
    topic_path_keys?: string[];

    // relatesTo specific metadata.
    bidirectional_confirmed?: boolean;
    sampled_a_count?: number;
    sampled_a_sfi_uuids?: UUIDString[];
    sampled_b_count?: number;
    sampled_b_sfi_uuids?: UUIDString[];
    subject_a?: string;
    subject_b?: string;
    thread_a_path?: string;
    thread_b_path?: string;

    // Keep this because metadata is intentionally extensible.
    [key: string]: unknown;
}


interface RelationshipProperties {
    attribution_statement?: string;
    author?: string;
    date_created?: ISODateString | null;
    date_modified?: ISODateString | null;
    description?: string;
    identifier?: UUIDString;
    license?: string;
    metadata?: RelationshipMetadata;
    order_index?: number;
    provider?: string;
    relationship_type?: RelationshipType;
    source_entity?: RelationshipEntity;
    source_entity_key?: RelationshipEntityKey;
    source_entity_value?: UUIDString;
    target_entity?: RelationshipEntity;
    target_entity_key?: RelationshipEntityKey;
    target_entity_value?: UUIDString;

    [key: string]: unknown;
}

// Tool schemas.
const OverviewSchema = z.object({});
const BrowseSubjectSchema = z.object({
    grade: z.string().optional().describe("Optional grade level filter"),
    subject: z
        .string()
        .describe("Academic subject name (e.g., 'Mathematics', 'Science')"),
});
const GetItemSchema = z.object({
    identifier: z.string().describe("Item identifier (UUID or ID)"),
});
const GetObjectivesSchema = z.object({
    standard_id: z.string().describe("Standard identifier (UUID or ID)"),
});
const SearchSchema = z.object({
    limit: z
        .number()
        .optional()
        .default(20)
        .describe("Maximum results to return"),
    query: z.string().describe("Search query text"),
    type: z
        .enum(["all", "standard", "objective"])
        .optional()
        .default("all")
        .describe("Filter by item type: all, standard, or objective"),
});

// Helper functions.
function buildHierarchyForSubject(
    subject: string,
    gradeFilter?: string
): object[] {
    const normalizedSubject = subject.toLowerCase().replace(/\s+/g, " ");

    const topLevelItems: GraphNode[] = [];

    for (const node of sfis) {
        const itemSubject = node.properties.academic_subject
            ?.replace(/\n/g, " ")
            .toLowerCase()
            .trim();
        if (itemSubject === normalizedSubject) {
            const parent = getParent(node.id);
            if (
                !parent ||
                parent.properties.academic_subject?.toLowerCase() !== normalizedSubject
            ) {
                topLevelItems.push(node);
            }
        }
    }

    function buildNode(node: GraphNode, depth: number = 0): object {
        const children = getChildren(node.id);
        const filteredChildren =
            gradeFilter && depth > 0
                ? children.filter(
                    (c) =>
                        c.properties.grade_level?.includes(gradeFilter) ||
                        c.properties.statement_code?.includes(gradeFilter) ||
                        children.length === 0
                )
                : children;

        return {
            identifier: node.properties.identifier,
            uuid: node.id,
            code: node.properties.statement_code,
            description:
                (node.properties.description?.length ?? 0) > 150
                    ? node.properties.description!.substring(0, 150) + "..."
                    : node.properties.description,
            type: node.properties.normalized_statement_type,
            childCount: children.length,
            children:
                depth < 3
                    ? filteredChildren.map((c) => buildNode(c, depth + 1))
                    : filteredChildren.map((c) => ({
                        identifier: c.properties.identifier,
                        code: c.properties.statement_code,
                        description:
                            (c.properties.description?.length ?? 0) > 100
                                ? c.properties.description!.substring(0, 100) + "..."
                                : c.properties.description,
                        childCount: getChildren(c.id).length,
                    })),
        };
    }

    return topLevelItems.map((node) => buildNode(node));
}

/**
 * Build derived node and relationship indexes for a loaded knowledge graph.
 *
 * This function performs two jobs:
 *
 * 1. Partitions graph nodes into the major supported node categories:
 *    StandardsFramework, StandardsFrameworkItem, and LearningComponent.
 * 2. Builds lookup maps used by MCP tools for fast traversal and retrieval.
 *
 * The returned indexes are read-only by convention: callers should treat them as
 * startup-time data structures and should not mutate them while the MCP server is
 * handling requests.
 *
 * @param kg - Parsed and minimally validated knowledge graph.
 *
 * @returns An object containing node partitions and lookup maps:
 *  - `frameworks`: nodes labeled StandardsFramework.
 *  - `lcByIdentifier`: LearningComponent nodes indexed by `properties.identifier`
 *  - `learningComponents`: nodes labeled LearningComponent.
 *  - `nodesById`: all graph nodes indexed by graph node ID.
 *  - `relsByEnd`: relationships indexed by target node ID.
 *  - `relsByStart`: relationships indexed by source node ID.
 *  - `sfis`: nodes labeled StandardsFrameworkItem.
 *  - `sfisByIdentifier`: SFI nodes indexed by `properties.identifier`.
 *  - `unknownNodes`: nodes with labels not recognized by this server.
 */
function buildKnowledgeGraphIndexes(kg: KnowledgeGraph): KnowledgeGraphIndexes {
    const frameworks: GraphNode[] = [];
    const learningComponents: GraphNode[] = [];
    const nodesById = new Map<string, GraphNode>();
    const sfis: GraphNode[] = [];
    const unknownNodes: GraphNode[] = [];

    for (const node of kg.nodes) {
        if (node.labels.includes("StandardsFramework")) {
            frameworks.push(node);
        } else if (node.labels.includes("StandardsFrameworkItem")) {
            sfis.push(node);
        } else if (node.labels.includes("LearningComponent")) {
            learningComponents.push(node);
        } else {
            unknownNodes.push(node);
        }

        // Index all nodes by their graph node ID. For StandardsFramework/SFI nodes
        // this is usually `case_identifier_uuid`; for LearningComponent nodes this is
        // usually `identifier`.
        nodesById.set(node.id, node);
    }

    if (unknownNodes.length > 0) {
        console.error(
            `Ignored ${unknownNodes.length} node(s) with unrecognized labels.`
        );
    }

    const sfisByIdentifier = new Map<string, GraphNode>();

    for (const node of sfis) {
        sfisByIdentifier.set(node.properties.identifier, node);
    }

    const lcByIdentifier = new Map<string, GraphNode>();

    for (const node of learningComponents) {
        lcByIdentifier.set(node.properties.identifier, node);
    }

    const relsByStart = new Map<string, GraphRelationship[]>();
    const relsByEnd = new Map<string, GraphRelationship[]>();

    for (const rel of kg.relationships) {
        const existingStartRels = relsByStart.get(rel.start) || [];
        existingStartRels.push(rel);
        relsByStart.set(rel.start, existingStartRels);

        const existingEndRels = relsByEnd.get(rel.end) || [];
        existingEndRels.push(rel);
        relsByEnd.set(rel.end, existingEndRels);
    }

    return {
        frameworks,
        lcByIdentifier,
        learningComponents,
        nodesById,
        relsByEnd,
        relsByStart,
        sfis,
        sfisByIdentifier,
        unknownNodes,
    };
}


function getBuildsTowards(
    standardNodeId: string
): { from: GraphNode[]; to: GraphNode[] } {
    const from: GraphNode[] = [];
    const to: GraphNode[] = [];

    // Outgoing buildsTowards: this standard builds towards others
    const outRels = relsByStart.get(standardNodeId) || [];
    for (const rel of outRels) {
        if (rel.type === "buildsTowards") {
            const target = nodesById.get(rel.end);
            if (target && target.labels.includes("StandardsFrameworkItem")) {
                to.push(target);
            }
        }
    }

    // Incoming buildsTowards: other standards build towards this one
    const inRels = relsByEnd.get(standardNodeId) || [];
    for (const rel of inRels) {
        if (rel.type === "buildsTowards") {
            const source = nodesById.get(rel.start);
            if (source && source.labels.includes("StandardsFrameworkItem")) {
                from.push(source);
            }
        }
    }

    return {from, to};
}

function getChildren(parentNodeId: string): GraphNode[] {
    const rels = relsByStart.get(parentNodeId) || [];
    const children: GraphNode[] = [];

    for (const rel of rels) {
        if (rel.type === "hasChild") {
            const child = nodesById.get(rel.end);
            if (child && child.labels.includes("StandardsFrameworkItem")) {
                children.push(child);
            }
        }
    }

    return children;
}

function getItemByIdentifier(
    identifier: string
): { type: string; item: GraphNode } | null {
    // Check standards by properties.identifier
    const standard = sfisByIdentifier.get(identifier);
    if (standard) {
        return {type: "standard", item: standard};
    }

    // Check by node id (== case_identifier_uuid)
    const nodeById = nodesById.get(identifier);
    if (nodeById && nodeById.labels.includes("StandardsFrameworkItem")) {
        return {type: "standard", item: nodeById};
    }

    // Check learning components by identifier
    const lc = lcByIdentifier.get(identifier);
    if (lc) {
        return {type: "objective", item: lc};
    }

    // Check learning components by node id
    if (nodeById && nodeById.labels.includes("LearningComponent")) {
        return {type: "objective", item: nodeById};
    }

    return null;
}

function getLearningComponentsForStandard(
    standardNodeId: string
): GraphNode[] {
    // supports relationships: LC (start) -> Standard (end)
    const rels = relsByEnd.get(standardNodeId) || [];
    const components: GraphNode[] = [];

    for (const rel of rels) {
        if (rel.type === "supports") {
            const lc = nodesById.get(rel.start);
            if (lc && lc.labels.includes("LearningComponent")) {
                components.push(lc);
            }
        }
    }

    return components;
}

function getParent(childNodeId: string): GraphNode | undefined {
    const rels = relsByEnd.get(childNodeId) || [];

    for (const rel of rels) {
        if (rel.type === "hasChild") {
            const parent = nodesById.get(rel.start);
            if (parent && parent.labels.includes("StandardsFrameworkItem")) {
                return parent;
            }
        }
    }

    return undefined;
}

function getRelatesTo(standardNodeId: string): GraphNode[] {
    const related: GraphNode[] = [];

    const outRels = relsByStart.get(standardNodeId) || [];
    for (const rel of outRels) {
        if (rel.type === "relatesTo") {
            const target = nodesById.get(rel.end);
            if (target && target.labels.includes("StandardsFrameworkItem")) {
                related.push(target);
            }
        }
    }

    const inRels = relsByEnd.get(standardNodeId) || [];
    for (const rel of inRels) {
        if (rel.type === "relatesTo") {
            const source = nodesById.get(rel.start);
            if (source && source.labels.includes("StandardsFrameworkItem")) {
                related.push(source);
            }
        }
    }

    return related;
}

function getUniqueGradeLevels(): string[] {
    const grades = new Set<string>();
    for (const node of sfis) {
        const gl = node.properties.grade_level;
        if (gl && Array.isArray(gl)) {
            for (const g of gl) {
                grades.add(g);
            }
        }
    }
    return Array.from(grades).sort();
}

function getUniqueSubjects(): string[] {
    const subjects = new Set<string>();
    for (const node of sfis) {
        const subj = node.properties.academic_subject;
        if (subj) {
            subjects.add(subj.replace(/\n/g, " ").trim());
        }
    }
    return Array.from(subjects).sort();
}

/**
 * Load and validate a Learning Commons knowledge graph JSON export from disk.
 *
 * The KG file is expected to live under:
 *
 *   ../../examples/kgs/<kgFn>
 *
 * relative to this server file's runtime directory. The function reads the JSON
 * synchronously because the MCP server should either start with a valid KG or fail
 * fast before accepting tool calls.
 *
 * @param kgFn - Filename of the KG JSON export to load (e.g., "senegal_reading.json").
 *
 * @returns The parsed and minimally validated knowledge graph.
 *
 * @throws If the file cannot be read, the JSON cannot be parsed, or the parsed object
 *  does not contain `nodes` and `relationships` arrays.
 */
function loadKnowledgeGraph(kgFn: string): KnowledgeGraph {
    const kgFp = join(__dirname, "..", "..", "examples", "kgs", kgFn);

    console.error("Resolved KG filepath:", kgFp);

    try {
        const rawData = readFileSync(kgFp, "utf8");
        const kg = JSON.parse(rawData) as KnowledgeGraph;

        if (!Array.isArray(kg.nodes)) {
            throw new Error("Invalid KG file: expected `nodes` to be an array");
        }

        if (!Array.isArray(kg.relationships)) {
            throw new Error(
                "Invalid KG file: expected `relationships` to be an array"
            );
        }

        const sfCount = kg.nodes.filter((n) =>
            n.labels.includes("StandardsFramework")
        ).length;

        const sfiCount = kg.nodes.filter((n) =>
            n.labels.includes("StandardsFrameworkItem")
        ).length;

        const lcCount = kg.nodes.filter((n) =>
            n.labels.includes("LearningComponent")
        ).length;

        const relTypeCounts = kg.relationships.reduce<Record<string, number>>(
            (acc, rel) => {
                acc[rel.type] = (acc[rel.type] ?? 0) + 1;
                return acc;
            },
            {}
        );

        console.error(`Loaded KG from ${kgFp}:
  - ${sfCount} Standards Framework(s)
  - ${sfiCount} Standards Framework Items
  - ${lcCount} Learning Components
  - ${kg.relationships.length} Total Relationships
  - Relationship types: ${JSON.stringify(relTypeCounts)}`);

        return kg;
    } catch (error: unknown) {
        if (
            typeof error === "object" &&
            error !== null &&
            "code" in error &&
            error.code === "ENOENT"
        ) {
            throw new Error(`Failed to load knowledge graph: File not found: ${kgFp}`);
        }

        throw error;
    }
}

function searchItems(
    query: string,
    typeFilter: "all" | "standard" | "objective" = "all",
    limit: number = 20
): Array<{ type: string; item: GraphNode }> {
    const q = query.toLowerCase();
    const results: Array<{ type: string; item: GraphNode }> = [];

    if (typeFilter === "all" || typeFilter === "standard") {
        for (const node of sfis) {
            if (results.length >= limit) break;

            const searchText = [
                node.properties.description,
                node.properties.statement_code,
                node.properties.academic_subject,
            ]
                .filter(Boolean)
                .join(" ")
                .toLowerCase();

            if (searchText.includes(q)) {
                results.push({type: "standard", item: node});
            }
        }
    }

    if (
        (typeFilter === "all" || typeFilter === "objective") &&
        results.length < limit
    ) {
        for (const node of learningComponents) {
            if (results.length >= limit) break;

            const searchText = [
                node.properties.description,
                node.properties.academic_subject,
            ]
                .filter(Boolean)
                .join(" ")
                .toLowerCase();

            if (searchText.includes(q)) {
                results.push({type: "objective", item: node});
            }
        }
    }

    return results.slice(0, limit);
}

// Server setup.
const server = new Server(
    {
        name: "edu-kg-mcp",
        version: "1.0.0",
    },
    {
        capabilities: {
            tools: {},
        },
    }
);

// Load KG.
const kg = loadKnowledgeGraph("senegal_reading.json");

// Building KG indexes and partitions.
const {
    frameworks,
    lcByIdentifier,
    learningComponents,
    nodesById,
    relsByEnd,
    relsByStart,
    sfis,
    sfisByIdentifier,
} = buildKnowledgeGraphIndexes(kg);


// Register tools.
server.setRequestHandler(ListToolsRequestSchema, async () => {
    return {
        tools: [
            {
                name: "overview",
                description:
                    "Get an overview of the knowledge graph including summary statistics, available subjects, grade levels, and a sample of the data structure.",
                inputSchema: {
                    type: "object",
                    properties: {},
                },
            },
            {
                name: "search",
                description:
                    "Search the knowledge graph for standards and learning objectives matching a text query. Returns matching items with their descriptions and identifiers.",
                inputSchema: {
                    type: "object",
                    properties: {
                        query: {type: "string", description: "Search query text"},
                        type: {
                            type: "string",
                            enum: ["all", "standard", "objective"],
                            description:
                                "Filter results by type: 'all' (default), 'standard', or 'objective'",
                        },
                        limit: {
                            type: "number",
                            description: "Maximum number of results (default 20)",
                        },
                    },
                    required: ["query"],
                },
            },
            {
                name: "get_item",
                description:
                    "Get detailed information about a specific item by its identifier. Returns the full item with parent, children, related learning objectives, learning progressions (builds towards / builds from), and cross-subject links.",
                inputSchema: {
                    type: "object",
                    properties: {
                        identifier: {
                            type: "string",
                            description: "Item identifier (UUID or ID)",
                        },
                    },
                    required: ["identifier"],
                },
            },
            {
                name: "browse_subject",
                description:
                    "Browse the hierarchical structure of standards for a specific academic subject. Returns a tree view of the curriculum hierarchy.",
                inputSchema: {
                    type: "object",
                    properties: {
                        subject: {
                            type: "string",
                            description:
                                "Academic subject name (e.g., 'Mathematics', 'Science', 'English')",
                        },
                        grade: {
                            type: "string",
                            description: "Optional grade level filter",
                        },
                    },
                    required: ["subject"],
                },
            },
            {
                name: "get_objectives",
                description:
                    "Get the learning objectives (learning components) that support a specific standard.",
                inputSchema: {
                    type: "object",
                    properties: {
                        standard_id: {
                            type: "string",
                            description: "Standard identifier (UUID or ID)",
                        },
                    },
                    required: ["standard_id"],
                },
            },
        ],
    };
});

// Handle tool calls.
server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const {name, arguments: args} = request.params;

    try {
        switch (name) {
            case "overview": {
                OverviewSchema.parse(args);

                const subjects = getUniqueSubjects();
                const grades = getUniqueGradeLevels();

                const sampleStandard = sfis[0];
                const sampleComponent = learningComponents[0];

                // Count relationship types
                const relTypeCounts: Record<string, number> = {};
                for (const rel of kg.relationships) {
                    relTypeCounts[rel.type] = (relTypeCounts[rel.type] || 0) + 1;
                }

                return {
                    content: [
                        {
                            type: "text",
                            text: JSON.stringify(
                                {
                                    summary: {
                                        jurisdiction:
                                            frameworks[0]?.properties.jurisdiction || "Unknown",
                                        frameworkName:
                                            frameworks[0]?.properties.name || "Unknown",
                                        totalStandards: sfis.length,
                                        totalLearningComponents: learningComponents.length,
                                        totalRelationships: kg.relationships.length,
                                        relationshipTypes: relTypeCounts,
                                    },
                                    subjects,
                                    gradeLevels:
                                        grades.length > 0
                                            ? grades
                                            : ["Not explicitly defined in data"],
                                    sampleStructure: {
                                        standard: {
                                            identifier: sampleStandard?.properties.identifier,
                                            description:
                                                sampleStandard?.properties.description?.substring(
                                                    0,
                                                    200
                                                ),
                                            subject:
                                                sampleStandard?.properties.academic_subject?.replace(
                                                    /\n/g,
                                                    " "
                                                ),
                                            statementCode:
                                            sampleStandard?.properties.statement_code,
                                            type: sampleStandard?.properties
                                                .normalized_statement_type,
                                        },
                                        learningComponent: {
                                            identifier: sampleComponent?.properties.identifier,
                                            description:
                                                sampleComponent?.properties.description?.substring(
                                                    0,
                                                    200
                                                ),
                                            subject:
                                                sampleComponent?.properties.academic_subject?.replace(
                                                    /\n/g,
                                                    " "
                                                ),
                                        },
                                    },
                                },
                                null,
                                2
                            ),
                        },
                    ],
                };
            }

            case "search": {
                const {query, type, limit} = SearchSchema.parse(args);
                const results = searchItems(query, type, limit);

                return {
                    content: [
                        {
                            type: "text",
                            text: JSON.stringify(
                                {
                                    query,
                                    typeFilter: type,
                                    count: results.length,
                                    results: results.map((r) => ({
                                        type: r.type,
                                        identifier: r.item.properties.identifier,
                                        description:
                                            (r.item.properties.description?.length ?? 0) > 200
                                                ? r.item.properties.description!.substring(0, 200) +
                                                "..."
                                                : r.item.properties.description,
                                        subject:
                                            r.item.properties.academic_subject?.replace(/\n/g, " "),
                                        code: r.item.properties.statement_code,
                                    })),
                                },
                                null,
                                2
                            ),
                        },
                    ],
                };
            }

            case "get_item": {
                const {identifier} = GetItemSchema.parse(args);
                const result = getItemByIdentifier(identifier);

                if (!result) {
                    return {
                        content: [
                            {
                                type: "text",
                                text: `Item '${identifier}' not found. Try using the search tool to find valid identifiers.`,
                            },
                        ],
                    };
                }

                const {type: itemType, item} = result;

                if (itemType === "standard") {
                    const parent = getParent(item.id);
                    const children = getChildren(item.id);
                    const objectives = getLearningComponentsForStandard(item.id);
                    const progressions = getBuildsTowards(item.id);
                    const related = getRelatesTo(item.id);

                    return {
                        content: [
                            {
                                type: "text",
                                text: JSON.stringify(
                                    {
                                        type: "standard",
                                        item: {
                                            identifier: item.properties.identifier,
                                            uuid: item.id,
                                            description: item.properties.description,
                                            subject: item.properties.academic_subject?.replace(
                                                /\n/g,
                                                " "
                                            ),
                                            statementCode: item.properties.statement_code,
                                            statementType:
                                            item.properties.normalized_statement_type,
                                            gradeLevel: item.properties.grade_level,
                                        },
                                        parent: parent
                                            ? {
                                                identifier: parent.properties.identifier,
                                                uuid: parent.id,
                                                description:
                                                    (parent.properties.description?.length ?? 0) > 150
                                                        ? parent.properties.description!.substring(
                                                        0,
                                                        150
                                                    ) + "..."
                                                        : parent.properties.description,
                                            }
                                            : null,
                                        children: children.map((c) => ({
                                            identifier: c.properties.identifier,
                                            uuid: c.id,
                                            code: c.properties.statement_code,
                                            description:
                                                (c.properties.description?.length ?? 0) > 100
                                                    ? c.properties.description!.substring(0, 100) +
                                                    "..."
                                                    : c.properties.description,
                                        })),
                                        relatedObjectives: objectives.map((o) => ({
                                            identifier: o.properties.identifier,
                                            description:
                                                (o.properties.description?.length ?? 0) > 150
                                                    ? o.properties.description!.substring(0, 150) +
                                                    "..."
                                                    : o.properties.description,
                                        })),
                                        learningProgressions: {
                                            buildsFrom: progressions.from.map((n) => ({
                                                identifier: n.properties.identifier,
                                                uuid: n.id,
                                                description:
                                                    (n.properties.description?.length ?? 0) > 100
                                                        ? n.properties.description!.substring(0, 100) +
                                                        "..."
                                                        : n.properties.description,
                                            })),
                                            buildsTowards: progressions.to.map((n) => ({
                                                identifier: n.properties.identifier,
                                                uuid: n.id,
                                                description:
                                                    (n.properties.description?.length ?? 0) > 100
                                                        ? n.properties.description!.substring(0, 100) +
                                                        "..."
                                                        : n.properties.description,
                                            })),
                                        },
                                        relatedStandards: related.map((n) => ({
                                            identifier: n.properties.identifier,
                                            uuid: n.id,
                                            description:
                                                (n.properties.description?.length ?? 0) > 100
                                                    ? n.properties.description!.substring(0, 100) +
                                                    "..."
                                                    : n.properties.description,
                                        })),
                                    },
                                    null,
                                    2
                                ),
                            },
                        ],
                    };
                } else {
                    // Learning component
                    let parentStandard: GraphNode | undefined;
                    const outRels = relsByStart.get(item.id) || [];
                    for (const rel of outRels) {
                        if (rel.type === "supports") {
                            const target = nodesById.get(rel.end);
                            if (
                                target &&
                                target.labels.includes("StandardsFrameworkItem")
                            ) {
                                parentStandard = target;
                                break;
                            }
                        }
                    }

                    return {
                        content: [
                            {
                                type: "text",
                                text: JSON.stringify(
                                    {
                                        type: "objective",
                                        item: {
                                            identifier: item.properties.identifier,
                                            description: item.properties.description,
                                            subject: item.properties.academic_subject?.replace(
                                                /\n/g,
                                                " "
                                            ),
                                        },
                                        parentStandard: parentStandard
                                            ? {
                                                identifier: parentStandard.properties.identifier,
                                                uuid: parentStandard.id,
                                                code: parentStandard.properties.statement_code,
                                                description:
                                                    (parentStandard.properties.description?.length ??
                                                        0) > 150
                                                        ? parentStandard.properties.description!.substring(
                                                        0,
                                                        150
                                                    ) + "..."
                                                        : parentStandard.properties.description,
                                            }
                                            : null,
                                    },
                                    null,
                                    2
                                ),
                            },
                        ],
                    };
                }
            }

            case "browse_subject": {
                const {subject, grade} = BrowseSubjectSchema.parse(args);
                const hierarchy = buildHierarchyForSubject(subject, grade);

                if (hierarchy.length === 0) {
                    const subjects = getUniqueSubjects();
                    return {
                        content: [
                            {
                                type: "text",
                                text: JSON.stringify(
                                    {
                                        error: `Subject '${subject}' not found.`,
                                        availableSubjects: subjects,
                                    },
                                    null,
                                    2
                                ),
                            },
                        ],
                    };
                }

                return {
                    content: [
                        {
                            type: "text",
                            text: JSON.stringify(
                                {
                                    subject,
                                    gradeFilter: grade || "none",
                                    topLevelCount: hierarchy.length,
                                    hierarchy,
                                },
                                null,
                                2
                            ),
                        },
                    ],
                };
            }

            case "get_objectives": {
                const {standard_id} = GetObjectivesSchema.parse(args);

                let standardNode = sfisByIdentifier.get(standard_id);
                if (!standardNode) {
                    const nodeById = nodesById.get(standard_id);
                    if (
                        nodeById &&
                        nodeById.labels.includes("StandardsFrameworkItem")
                    ) {
                        standardNode = nodeById;
                    }
                }

                if (!standardNode) {
                    return {
                        content: [
                            {
                                type: "text",
                                text: `Standard '${standard_id}' not found. Try using the search tool to find valid standard identifiers.`,
                            },
                        ],
                    };
                }

                const objectives = getLearningComponentsForStandard(standardNode.id);

                return {
                    content: [
                        {
                            type: "text",
                            text: JSON.stringify(
                                {
                                    standard: {
                                        identifier: standardNode.properties.identifier,
                                        uuid: standardNode.id,
                                        code: standardNode.properties.statement_code,
                                        description: standardNode.properties.description,
                                        subject:
                                            standardNode.properties.academic_subject?.replace(
                                                /\n/g,
                                                " "
                                            ),
                                    },
                                    objectiveCount: objectives.length,
                                    objectives: objectives.map((o) => ({
                                        identifier: o.properties.identifier,
                                        description: o.properties.description,
                                        subject: o.properties.academic_subject?.replace(
                                            /\n/g,
                                            " "
                                        ),
                                    })),
                                },
                                null,
                                2
                            ),
                        },
                    ],
                };
            }

            default:
                return {
                    content: [{type: "text", text: `Unknown tool: ${name}`}],
                    isError: true,
                };
        }
    } catch (error) {
        return {
            content: [
                {
                    type: "text",
                    text: `Error: ${error instanceof Error ? error.message : String(error)}`,
                },
            ],
            isError: true,
        };
    }
});

// Start server.
async function main() {
    const transport = new StdioServerTransport();
    await server.connect(transport);
    console.error("Educational KG MCP Server running on stdio");
}

main().catch((error) => {
    console.error("Fatal error:", error);
    process.exit(1);
});
