/** @file This file contains global schemas. */

// Third Party Library
import { z } from "zod";

// Schemas.
export const BrowseSubjectInputSchema = {
  additionalProperties: false,
  properties: {
    grade: {
      description: "Optional grade level filter, e.g. 'CE1'.",
      type: "string",
    },
    subject: {
      description:
        "Academic subject name. For this KG, use 'Langue et Communication'.",
      type: "string",
    },
  },
  required: ["subject"],
  type: "object",
} as const;

export const BrowseSubjectSchema = z
  .object({
    grade: z
      .string()
      .optional()
      .describe("Optional grade level filter such as 'CE1'."),
    subject: z
      .string()
      .describe(
        "Academic subject name. For the bundled Senegal KG, use 'Langue et Communication'.",
      ),
  })
  .strict();

export const GetItemSchema = z
  .object({
    identifier: z
      .string()
      .describe("Item identifier, graph node UUID, or CASE UUID."),
  })
  .strict();

export const GetLearningComponentsForStandardInputSchema = {
  additionalProperties: false,
  properties: {
    standard_id: {
      description:
        "StandardsFrameworkItem identifier, graph node UUID, or CASE UUID.",
      type: "string",
    },
  },
  required: ["standard_id"],
  type: "object",
} as const;

export const GetLearningComponentsForStandardSchema = z
  .object({
    standard_id: z
      .string()
      .describe(
        "StandardsFrameworkItem identifier, graph node UUID, or CASE UUID.",
      ),
  })
  .strict();

export const GetPathSchema = z
  .object({
    identifier: z
      .string()
      .describe("StandardsFrameworkItem or LearningComponent identifier."),
  })
  .strict();

export const ProgressionDirectionSchema = z.enum([
  "both",
  "builds_from",
  "builds_towards",
  "related",
]);

export const GetItemInputSchema = {
  additionalProperties: false,
  properties: {
    identifier: {
      description: "Item identifier, graph node UUID, or CASE UUID.",
      type: "string",
    },
  },
  required: ["identifier"],
  type: "object",
} as const;

export const GetPathInputSchema = {
  additionalProperties: false,
  properties: {
    identifier: {
      description: "StandardsFrameworkItem or LearningComponent identifier.",
      type: "string",
    },
  },
  required: ["identifier"],
  type: "object",
} as const;

export const GetProgressionInputSchema = {
  additionalProperties: false,
  properties: {
    depth: {
      description: "Traversal depth. Defaults to 1; maximum is 3.",
      maximum: 3,
      minimum: 1,
      type: "integer",
    },
    direction: {
      description: "Progression direction to return. Defaults to 'both'.",
      enum: ["both", "builds_from", "builds_towards", "related"],
      type: "string",
    },
    identifier: {
      description:
        "StandardsFrameworkItem identifier, graph node UUID, or CASE UUID.",
      type: "string",
    },
  },
  required: ["identifier"],
  type: "object",
} as const;

export const GetProgressionSchema = z
  .object({
    depth: z
      .number()
      .int()
      .min(1)
      .max(3)
      .optional()
      .default(1)
      .describe(
        "Traversal depth for progression links. Defaults to 1; maximum is 3.",
      ),
    direction: ProgressionDirectionSchema.optional()
      .default("both")
      .describe("Which progression direction to return."),
    identifier: z
      .string()
      .describe(
        "StandardsFrameworkItem identifier, graph node UUID, or CASE UUID.",
      ),
  })
  .strict();

export const GetProvenanceInputSchema = {
  additionalProperties: false,
  properties: {
    identifier: {
      description: "StandardsFrameworkItem or LearningComponent identifier.",
      type: "string",
    },
  },
  required: ["identifier"],
  type: "object",
} as const;

export const GetProvenanceSchema = z
  .object({
    identifier: z
      .string()
      .describe("StandardsFrameworkItem or LearningComponent identifier."),
  })
  .strict();

export const GetRelatedItemsInputSchema = {
  additionalProperties: false,
  properties: {
    identifier: {
      description:
        "StandardsFrameworkItem identifier, graph node UUID, or CASE UUID.",
      type: "string",
    },
  },
  required: ["identifier"],
  type: "object",
} as const;

export const GetRelatedItemsSchema = z
  .object({
    identifier: z
      .string()
      .describe(
        "StandardsFrameworkItem identifier, graph node UUID, or CASE UUID.",
      ),
  })
  .strict();

/**
 * Minimal runtime schema for a Knowledge Graph file.
 *
 * Validates only the structural skeleton that downstream index construction
 * relies on: every node must have an `id`, a `labels` array, and
 * `properties.identifier`; every relationship must have `id`, `start`, `end`,
 * and `type`. Extra fields on nodes, relationships, and the top-level KG (e.g.
 * `doc_key`, metadata blobs, provenance context) are passed through unchanged
 * so the loader stays forward-compatible with new schema additions.
 * `relationship.type` is left as an open string rather than constrained to the
 * current `RelationshipType` union so that loading does not fail when new
 * relationship kinds are introduced (unrecognized types are simply ignored by
 * traversal helpers). Relationship `properties` defaults to `{}` when omitted
 * so downstream traversal helpers can safely rely on the field being present.
 */
export const KnowledgeGraphSchema = z
  .object({
    nodes: z.array(
      z
        .object({
          id: z.string().min(1),
          labels: z.array(z.string()),
          properties: z
            .object({
              identifier: z.string().min(1),
            })
            .passthrough(),
        })
        .passthrough(),
    ),
    relationships: z.array(
      z
        .object({
          end: z.string().min(1),
          id: z.string().min(1),
          properties: z.object({}).passthrough().default({}),
          start: z.string().min(1),
          type: z.string().min(1),
        })
        .passthrough(),
    ),
  })
  .passthrough();

export const NavigateInputSchema = {
  additionalProperties: false,
  properties: {
    depth: {
      description: "Traversal depth for descendants. Defaults to 1.",
      maximum: 5,
      minimum: 1,
      type: "integer",
    },
    direction: {
      description: "Hierarchy direction to traverse.",
      enum: ["parent", "children", "siblings", "ancestors", "descendants"],
      type: "string",
    },
    identifier: {
      description: "StandardsFrameworkItem or LearningComponent identifier.",
      type: "string",
    },
  },
  required: ["identifier", "direction"],
  type: "object",
} as const;

export const NavigateSchema = z
  .object({
    depth: z
      .number()
      .int()
      .min(1)
      .max(5)
      .optional()
      .default(1)
      .describe(
        "Traversal depth for descendants. Defaults to 1; maximum is 5.",
      ),
    direction: z
      .enum(["parent", "children", "siblings", "ancestors", "descendants"])
      .describe("Navigation direction in the curriculum hierarchy."),
    identifier: z
      .string()
      .describe("StandardsFrameworkItem or LearningComponent identifier."),
  })
  .strict();

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

export const SearchItemsSchema = z
  .object({
    grade: z
      .string()
      .optional()
      .describe("Optional grade level filter, e.g. 'CE1'."),
    limit: z
      .number()
      .int()
      .min(1)
      .max(100)
      .optional()
      .default(20)
      .describe("Maximum results to return. Defaults to 20; maximum is 100."),
    node_type: SearchNodeTypeSchema.optional().describe(
      "Preferred filter by KG node category.",
    ),
    query: z
      .string()
      .optional()
      .default("")
      .describe(
        "Search query text. Leave empty when filtering by facets only.",
      ),
    source_label: z
      .string()
      .optional()
      .describe(
        "Optional source label filter, e.g. 'Conjugaison' or 'Orthographe'.",
      ),
    statement_type: z
      .string()
      .optional()
      .describe("Optional statement type filter."),
    subject: z
      .string()
      .optional()
      .describe("Optional subject filter, e.g. 'Langue et Communication'."),
  })
  .strict();

export const SearchItemsInputSchema = {
  additionalProperties: false,
  properties: {
    grade: {
      description: "Optional grade level filter. For this KG, use 'CE1'.",
      type: "string",
    },
    limit: {
      description: "Maximum number of results. Defaults to 20.",
      maximum: 100,
      minimum: 1,
      type: "integer",
    },
    node_type: {
      description:
        "Filter by KG node category. Use 'standard_item' for curriculum nodes and 'learning_component' for atomic extracted skills.",
      enum: ["all", "standard_item", "learning_component"],
      type: "string",
    },
    query: {
      description: "Search text. Leave empty when using only filters.",
      type: "string",
    },
    source_label: {
      description:
        "Optional source label filter, e.g. 'Conjugaison', 'Grammaire', or 'Vocabulaire'.",
      type: "string",
    },
    statement_type: {
      description:
        "Optional statement type filter, e.g. 'Conjugaison', 'Orthographe', or 'Objectif spécifique'.",
      type: "string",
    },
    subject: {
      description:
        "Optional academic subject filter. For this KG, use 'Langue et Communication'.",
      type: "string",
    },
  },
  type: "object",
} as const;

// Types.
export type ProgressionDirection =
  | "both"
  | "builds_from"
  | "builds_towards"
  | "related";
export type SearchNodeType = "all" | "standard_item" | "learning_component";
