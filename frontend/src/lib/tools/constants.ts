/** @file This file contains global constants. */

// Package Library
import {
  BrowseSubjectSchema,
  GetFrameworkSchema,
  GetItemSchema,
  GetLearningComponentsForStandardSchema,
  GetPathSchema,
  GetProgressionSchema,
  GetProvenanceSchema,
  GetRelatedItemsSchema,
  ListFacetsSchema,
  NavigateSchema,
  OverviewSchema,
  SearchItemsSchema,
} from "@/lib/schemas.js";

const READ_ONLY_TOOL_ANNOTATIONS = {
  idempotentHint: true,
  openWorldHint: false,
  readOnlyHint: true,
};

/**
 * Precomputed tool metadata used by the high-level McpServer registration path.
 *
 * McpServer owns the tools/list handler internally after tools are registered,
 * so this catalogue is not returned directly. It still avoids scattering static
 * names, titles, descriptions, annotations, and Zod input shapes through the
 * registration code.
 */
export const KNOWLEDGE_GRAPH_TOOL_DEFINITIONS = [
  {
    annotations: READ_ONLY_TOOL_ANNOTATIONS,
    description:
      "Alternative discovery tool. Browse the hierarchical curriculum tree for a subject, optionally filtered by grade. For the bundled Senegal KG, the available subject is 'Langue et Communication'. Returns a nested tree of items with identifiers that can be passed to get_item, navigate, or other tools. Prefer this over search_items when exploring curriculum structure top-down.",
    inputSchema: BrowseSubjectSchema.shape,
    name: "browse_subject",
    title: "Browse Subject Hierarchy",
  },
  {
    annotations: READ_ONLY_TOOL_ANNOTATIONS,
    description:
      "Return framework-level metadata: name, jurisdiction, author, provider, license, graph type, included graph types, and source PDF name. Use this to answer questions about the curriculum document itself rather than individual items.",
    inputSchema: GetFrameworkSchema.shape,
    name: "get_framework",
    title: "Get Framework Metadata",
  },
  {
    annotations: READ_ONLY_TOOL_ANNOTATIONS,
    description:
      "Get detailed information about a single node by identifier. Use an identifier returned by search_items or browse_subject. Returns full properties plus contextual summaries: hierarchy path, child count, learning components, progression links, and related items when available.",
    inputSchema: GetItemSchema.shape,
    name: "get_item",
    title: "Get KG Item",
  },
  {
    annotations: READ_ONLY_TOOL_ANNOTATIONS,
    description:
      "List the atomic LearningComponent skills extracted from a StandardsFrameworkItem. Requires a standard item identifier from search_items or browse_subject (node_type='standard_item'). Use this to break a curriculum standard down into its constituent teachable skills.",
    inputSchema: GetLearningComponentsForStandardSchema.shape,
    name: "get_learning_components_for_standard",
    title: "Get Learning Components for Standard",
  },
  {
    annotations: READ_ONLY_TOOL_ANNOTATIONS,
    description:
      "Return the full path from the curriculum root down to a specific item. Requires an identifier from search_items or browse_subject. For LearningComponents, the path goes through the supported StandardsFrameworkItem. Use this to understand where an item sits in the overall curriculum hierarchy.",
    inputSchema: GetPathSchema.shape,
    name: "get_path",
    title: "Get Curriculum Path",
  },
  {
    annotations: READ_ONLY_TOOL_ANNOTATIONS,
    description:
      "Trace learning progressions for a StandardsFrameworkItem: what it builds from (prerequisites), what it builds towards (next steps), and cross-curricular related standards. Requires an identifier from search_items or browse_subject. Accepts an optional direction filter ('builds_from', 'builds_towards', 'related', or 'both') and depth (1–3). If given a LearningComponent identifier, it maps to the supported standard first.",
    inputSchema: GetProgressionSchema.shape,
    name: "get_progression",
    title: "Get Learning Progression Links",
  },
  {
    annotations: READ_ONLY_TOOL_ANNOTATIONS,
    description:
      "Return source traceability for any item: attribution, author, license, source labels, page indices, bounding boxes, source segment/decision IDs, and LLM rationale when available. Requires an identifier from search_items or browse_subject. Use this to answer questions about where a curriculum item or learning component originally came from in the source PDF.",
    inputSchema: GetProvenanceSchema.shape,
    name: "get_provenance",
    title: "Get Source Provenance",
  },
  {
    annotations: READ_ONLY_TOOL_ANNOTATIONS,
    description:
      "Return StandardsFrameworkItem nodes connected to the target via relatesTo relationships. Requires an identifier from search_items or browse_subject. This is a focused subset of get_progression—use it when you only need cross-curricular connections without the full builds-from/builds-towards chains.",
    inputSchema: GetRelatedItemsSchema.shape,
    name: "get_related_items",
    title: "Get Related Standards",
  },
  {
    annotations: READ_ONLY_TOOL_ANNOTATIONS,
    description:
      "List available filter values in the KG, including subjects, grades, statement types, source labels, node types, and relationship types with counts. Use this before search_items to discover valid filter values.",
    inputSchema: ListFacetsSchema.shape,
    name: "list_facets",
    title: "List KG Facets",
  },
  {
    annotations: READ_ONLY_TOOL_ANNOTATIONS,
    description:
      "Move through the curriculum hierarchy from a known item. Requires an identifier from search_items or browse_subject plus a direction: parent, children, siblings, ancestors, or descendants. For LearningComponents, parent/ancestor navigation follows the supports relationship to the linked StandardsFrameworkItem. Use this to explore neighbors of an item you already have.",
    inputSchema: NavigateSchema.shape,
    name: "navigate",
    title: "Navigate Curriculum Hierarchy",
  },
  {
    annotations: READ_ONLY_TOOL_ANNOTATIONS,
    description:
      "Start here. Get summary statistics and sample compact-node structure for the Senegal CE1 Langue et Communication knowledge graph. Returns subjects, grade levels, node/relationship counts, graph metadata, and sample standard/LearningComponent items. Use this to orient before drilling in with search_items or browse_subject.",
    inputSchema: OverviewSchema.shape,
    name: "overview",
    title: "Knowledge Graph Overview",
  },
  {
    annotations: READ_ONLY_TOOL_ANNOTATIONS,
    description:
      "Primary discovery tool. Search StandardsFrameworkItem and LearningComponent nodes by text query and/or filters for subject, grade, statement type, source label, and node category. Use node_type='standard_item' when looking for standards, node_type='learning_component' when looking for teachable skills, and node_type='all' for broad discovery. Results are compact nodes with identifiers that can be passed to get_item, navigate, get_path, get_progression, get_learning_components_for_standard, get_related_items, or get_provenance as appropriate for the returned node type.",
    inputSchema: SearchItemsSchema.shape,
    name: "search_items",
    title: "Search Curriculum Items",
  },
] satisfies KnowledgeGraphToolDefinition[];
