/** @file This file contains global schemas. */

// Third Party Library
import {z} from "zod";

// Schemas.
export const BrowseSubjectInputSchema = {
    type: "object",
    properties: {
        subject: {
            type: "string",
            description:
                "Academic subject name. For this KG, use 'Langue et Communication'.",
        },
        grade: {
            type: "string",
            description: "Optional grade level filter, e.g. 'CE1'.",
        },
    },
    required: ["subject"],
    additionalProperties: false,
} as const;

export const BrowseSubjectSchema = z.object({
    grade: z.string().optional().describe("Optional grade level filter such as 'CE1'."),
    subject: z
        .string()
        .describe("Academic subject name. For the bundled Senegal KG, use 'Langue et Communication'."),
}).strict();

export const GetItemSchema = z.object({
    identifier: z.string().describe("Item identifier, graph node UUID, or CASE UUID."),
}).strict();

export const GetLearningComponentsForStandardInputSchema = {
    type: "object",
    properties: {
        standard_id: {
            type: "string",
            description: "StandardsFrameworkItem identifier, graph node UUID, or CASE UUID.",
        },
    },
    required: ["standard_id"],
    additionalProperties: false,
} as const;

export const GetLearningComponentsForStandardSchema = z.object({
    standard_id: z.string().describe("StandardsFrameworkItem identifier, graph node UUID, or CASE UUID."),
}).strict();

export const GetPathSchema = z.object({
    identifier: z.string().describe("StandardsFrameworkItem or LearningComponent identifier."),
}).strict();

export const ProgressionDirectionSchema = z.enum([
    "both",
    "builds_from",
    "builds_towards",
    "related",
]);

export const GetItemInputSchema = {
    type: "object",
    properties: {
        identifier: {
            type: "string",
            description: "Item identifier, graph node UUID, or CASE UUID.",
        },
    },
    required: ["identifier"],
    additionalProperties: false,
} as const;

export const GetPathInputSchema = {
    type: "object",
    properties: {
        identifier: {
            type: "string",
            description: "StandardsFrameworkItem or LearningComponent identifier.",
        },
    },
    required: ["identifier"],
    additionalProperties: false,
} as const;

export const GetProgressionInputSchema = {
    type: "object",
    properties: {
        identifier: {
            type: "string",
            description: "StandardsFrameworkItem identifier, graph node UUID, or CASE UUID.",
        },
        direction: {
            type: "string",
            enum: ["both", "builds_from", "builds_towards", "related"],
            description: "Progression direction to return. Defaults to 'both'.",
        },
        depth: {
            type: "integer",
            minimum: 1,
            maximum: 3,
            description: "Traversal depth. Defaults to 1; maximum is 3.",
        },
    },
    required: ["identifier"],
    additionalProperties: false,
} as const;

export const GetProgressionSchema = z.object({
    depth: z
        .number()
        .int()
        .min(1)
        .max(3)
        .optional()
        .default(1)
        .describe("Traversal depth for progression links. Defaults to 1; maximum is 3."),
    direction: ProgressionDirectionSchema
        .optional()
        .default("both")
        .describe("Which progression direction to return."),
    identifier: z.string().describe("StandardsFrameworkItem identifier, graph node UUID, or CASE UUID."),
}).strict();

export const GetProvenanceInputSchema = {
    type: "object",
    properties: {
        identifier: {
            type: "string",
            description: "StandardsFrameworkItem or LearningComponent identifier.",
        },
    },
    required: ["identifier"],
    additionalProperties: false,
} as const;

export const GetProvenanceSchema = z.object({
    identifier: z.string().describe("StandardsFrameworkItem or LearningComponent identifier."),
}).strict();

export const GetRelatedItemsInputSchema = {
    type: "object",
    properties: {
        identifier: {
            type: "string",
            description: "StandardsFrameworkItem identifier, graph node UUID, or CASE UUID.",
        },
    },
    required: ["identifier"],
    additionalProperties: false,
} as const;

export const GetRelatedItemsSchema = z.object({
    identifier: z.string().describe("StandardsFrameworkItem identifier, graph node UUID, or CASE UUID."),
}).strict();

/**
 * Minimal runtime schema for a Knowledge Graph file.
 *
 * Validates only the structural skeleton that downstream index construction relies on:
 * every node must have an `id`, a `labels` array, and `properties.identifier`; every
 * relationship must have `id`, `start`, `end`, and `type`. Extra fields on nodes,
 * relationships, and the top-level KG (e.g. `doc_key`, metadata blobs, provenance
 * context) are passed through unchanged so the loader stays forward-compatible with
 * new schema additions. `relationship.type` is left as an open string rather than
 * constrained to the current `RelationshipType` union so that loading does not fail
 * when new relationship kinds are introduced (unrecognized types are simply ignored by
 * traversal helpers).
 */
export const KnowledgeGraphSchema = z.object({
    nodes: z.array(
        z.object({
            id: z.string().min(1),
            labels: z.array(z.string()),
            properties: z.object({
                identifier: z.string().min(1),
            }).passthrough(),
        }).passthrough()
    ),
    relationships: z.array(
        z.object({
            id: z.string().min(1),
            start: z.string().min(1),
            end: z.string().min(1),
            type: z.string().min(1),
            properties: z.object({}).passthrough().optional(),
        }).passthrough()
    ),
}).passthrough();

export const NavigateInputSchema = {
    type: "object",
    properties: {
        identifier: {
            type: "string",
            description: "StandardsFrameworkItem or LearningComponent identifier.",
        },
        direction: {
            type: "string",
            enum: ["parent", "children", "siblings", "ancestors", "descendants"],
            description: "Hierarchy direction to traverse.",
        },
        depth: {
            type: "integer",
            minimum: 1,
            maximum: 5,
            description: "Traversal depth for descendants. Defaults to 1.",
        },
    },
    required: ["identifier", "direction"],
    additionalProperties: false,
} as const;

export const NavigateSchema = z.object({
    depth: z
        .number()
        .int()
        .min(1)
        .max(5)
        .optional()
        .default(1)
        .describe("Traversal depth for descendants. Defaults to 1; maximum is 5."),
    direction: z
        .enum(["parent", "children", "siblings", "ancestors", "descendants"])
        .describe("Navigation direction in the curriculum hierarchy."),
    identifier: z.string().describe("StandardsFrameworkItem or LearningComponent identifier."),
}).strict();

export const NoArgsInputSchema = {
    additionalProperties: false,
    properties: {},
    type: "object",
} as const;

export const NoArgsSchema = z.object({}).strict();

export const GetFrameworkInputSchema = NoArgsInputSchema;

export const GetFrameworkSchema = NoArgsSchema;

export const ListFacetsInputSchema = NoArgsInputSchema;

export const ListFacetsSchema = NoArgsSchema;

export const OverviewInputSchema = NoArgsInputSchema;

export const OverviewSchema = NoArgsSchema;

export const SearchNodeTypeSchema = z.enum([
    "all",
    "standard_item",
    "learning_component",
]);

export const SearchItemsSchema = z.object({
    grade: z.string().optional().describe("Optional grade level filter, e.g. 'CE1'."),
    limit: z
        .number()
        .int()
        .min(1)
        .max(100)
        .optional()
        .default(20)
        .describe("Maximum results to return. Defaults to 20; maximum is 100."),
    node_type: SearchNodeTypeSchema
        .optional()
        .describe("Preferred filter by KG node category."),
    query: z
        .string()
        .optional()
        .default("")
        .describe("Search query text. Leave empty when filtering by facets only."),
    source_label: z.string().optional().describe("Optional source label filter, e.g. 'Conjugaison' or 'Orthographe'."),
    statement_type: z.string().optional().describe("Optional statement type filter."),
    subject: z.string().optional().describe("Optional subject filter, e.g. 'Langue et Communication'."),
}).strict();

export const SearchItemsInputSchema = {
    type: "object",
    properties: {
        query: {
            type: "string",
            description: "Search text. Leave empty when using only filters.",
        },
        node_type: {
            type: "string",
            enum: ["all", "standard_item", "learning_component"],
            description:
                "Filter by KG node category. Use 'standard_item' for curriculum nodes and 'learning_component' for atomic extracted skills.",
        },
        subject: {
            type: "string",
            description:
                "Optional academic subject filter. For this KG, use 'Langue et Communication'.",
        },
        grade: {
            type: "string",
            description: "Optional grade level filter. For this KG, use 'CE1'.",
        },
        statement_type: {
            type: "string",
            description:
                "Optional statement type filter, e.g. 'Conjugaison', 'Orthographe', or 'Objectif spécifique'.",
        },
        source_label: {
            type: "string",
            description:
                "Optional source label filter, e.g. 'Conjugaison', 'Grammaire', or 'Vocabulaire'.",
        },
        limit: {
            type: "integer",
            minimum: 1,
            maximum: 100,
            description: "Maximum number of results. Defaults to 20.",
        },
    },
    additionalProperties: false,
} as const;

// Types.
export type ProgressionDirection = "both" | "builds_from" | "builds_towards" | "related";
export type SearchNodeType = "all" | "standard_item" | "learning_component";
