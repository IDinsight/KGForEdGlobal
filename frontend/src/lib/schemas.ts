/** @file This file contains global schemas. */

// Third Party Library
import { z } from "zod";

// Schemas.
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

export const GetAuxStatementsSchema = z
  .object({
    identifier: z
      .string()
      .describe(
        "StandardsFrameworkItem identifier, graph node UUID, or CASE UUID.",
      ),
    path_segment: z
      .string()
      .optional()
      .describe(
        "Optional filter: only return aux statements whose own canonical path key " +
          "(if present) contains the given `/`-delimited segment. Useful when an " +
          "aux statement carries its own canonical_node_id with a distinct path. " +
          "Most frameworks attach aux statements without their own path, in which " +
          "case this filter has no effect — to scope by week/unit/lesson, prefer " +
          "the path_segment filter on search_items at the standard level.",
      ),
    source_labels: z
      .array(z.string().min(1))
      .optional()
      .describe(
        "Optional filter: only return aux statements whose `source_label` matches " +
          "one of the provided values (case-insensitive, whitespace-normalized). " +
          "Source label vocabularies are framework-specific — call list_facets or " +
          "inspect a sample item to discover available values. When omitted, all " +
          "aux statements on the item are returned.",
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

export const GetProvenanceSchema = z
  .object({
    identifier: z
      .string()
      .describe("StandardsFrameworkItem or LearningComponent identifier."),
  })
  .strict();

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

export const NoArgsSchema = z.object({}).strict();

export const GetFrameworkSchema = NoArgsSchema;

export const ListFacetsSchema = NoArgsSchema;

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
    path_segment: z
      .string()
      .optional()
      .describe(
        "Optional filter: only return nodes whose `canonical_path_key` contains " +
          "the given segment as a complete `/`-delimited path component. The path " +
          "key encodes curriculum scoping using framework-specific `key:value` " +
          "segments — e.g. 'week:10', 'unit:3', 'quarter:Q1', 'lesson:5', or " +
          "'substage:palier-2-lecture'. Pass the exact segment text including its " +
          "key prefix. For LearningComponents, the supporting SFI's path key is " +
          "consulted as a fallback.",
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

// Types.
export type ProgressionDirection =
  | "both"
  | "builds_from"
  | "builds_towards"
  | "related";
export type SearchNodeType = "all" | "standard_item" | "learning_component";
