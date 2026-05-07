/** @file This file contains general utility functions. */

// Standard Library
import { readFileSync } from "node:fs";
import path from "node:path";

// Third Party Library
import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";

// Package Library
import {
  KnowledgeGraphSchema,
  type ProgressionDirection,
  type SearchNodeType,
} from "@/lib/schemas.js";

/**
 * Return shape of `createKnowledgeGraphUtils` — the public query API used by
 * the MCP tool handlers in `index.ts`. Declared explicitly so the function's
 * return type is verifiable by
 */
type KnowledgeGraphUtils = {
  buildHierarchyForSubject: (subject: string, gradeFilter?: string) => object[];
  buildProgressionTraversal: (
    standardNode: GraphNode,
    direction: ProgressionDirection,
    depth: number,
  ) => Record<string, unknown>;
  compactNode: (
    node: GraphNode,
    maxDescription?: number,
  ) => Record<string, unknown>;
  detailedNode: (node: GraphNode) => Record<string, unknown>;
  findAnyNode: (identifier: string) => { item: GraphNode; type: string } | null;
  findLearningComponent: (identifier: string) => GraphNode | undefined;
  findStandardItem: (identifier: string) => GraphNode | undefined;
  getAncestors: (nodeId: string) => GraphNode[];
  getChildrenAny: (parentNodeId: string) => GraphNode[];
  getDescendants: (nodeId: string, depth: number) => GraphNode[];
  getFacetValues: () => Record<string, unknown>;
  getLearningComponentsForStandard: (standardNodeId: string) => GraphNode[];
  getPathForNode: (node: GraphNode) => Record<string, unknown>;
  getRelatesTo: (standardNodeId: string) => GraphNode[];
  getSiblingItems: (nodeId: string) => GraphNode[];
  getStandardsSupportedByLearningComponent: (
    learningComponentNodeId: string,
  ) => GraphNode[];
  getSupportRelationshipsForLearningComponent: (
    learningComponentNodeId: string,
  ) => GraphRelationship[];
  getUniqueGradeLevels: () => string[];
  getUniqueSubjects: () => string[];
  provenanceForNode: (node: GraphNode) => Record<string, unknown>;
  searchItems: (options: {
    grade?: string;
    limit?: number;
    nodeType?: SearchNodeType;
    query?: string;
    sourceLabel?: string;
    statementType?: string;
    subject?: string;
  }) => Array<{ item: GraphNode; type: string }>;
};

/**
 * Build fast-lookup indexes over a parsed Knowledge Graph.
 *
 * Partitions nodes by label into frameworks, standard items (SFIs), learning
 * components, and unknown nodes. Constructs the following index maps:
 *
 * - `nodesById` — All nodes keyed by graph node ID.
 * - `sfisByIdentifier`/`lcByIdentifier` — SFIs and LCs keyed by their
 *   `properties.identifier` for direct lookup by external ID.
 * - `relsByStart`/`relsByEnd` — Relationship adjacency lists keyed by source and
 *   target node ID for efficient graph traversal.
 *
 * Nodes with unrecognized labels are collected in `unknownNodes` and a warning
 * is logged to stderr.
 *
 * @param kg - A validated `KnowledgeGraph` object (typically from
 *   `loadKnowledgeGraph`).
 *
 * @returns A `KnowledgeGraphIndexes` containing all partitioned arrays and
 *   lookup maps.
 *
 * @throws {Error} If a node carries more than one of the known KG labels
 *   (`StandardsFramework`, `StandardsFrameworkItem`, `LearningComponent`),
 *   which are expected to be mutually exclusive.
 */
export function buildKnowledgeGraphIndexes(
  kg: KnowledgeGraph,
): KnowledgeGraphIndexes {
  const frameworks: GraphNode[] = [];
  const learningComponents: GraphNode[] = [];
  const nodesById = new Map<string, GraphNode>();
  const sfis: GraphNode[] = [];
  const unknownNodes: GraphNode[] = [];

  for (const node of kg.nodes) {
    const isFramework = node.labels.includes("StandardsFramework");
    const isSfi = node.labels.includes("StandardsFrameworkItem");
    const isLc = node.labels.includes("LearningComponent");

    /*
     * The three known KG label types are expected to be mutually exclusive. If a node
     * ever carries more than one, the partitioning below would silently drop it from
     * one of the indexes; throw the malformed node at load time so it can be fixed at
     * the source rather than chased through downstream queries.
     */
    const matchedKnownLabels =
      Number(isFramework) + Number(isSfi) + Number(isLc);

    if (matchedKnownLabels > 1) {
      throw new Error(
        `Node ${node.id} has multiple known KG labels (${node.labels.join(", ")}); ` +
          `StandardsFramework, StandardsFrameworkItem, and LearningComponent are expected to be mutually exclusive.`,
      );
    }

    if (isFramework) {
      frameworks.push(node);
    } else if (isSfi) {
      sfis.push(node);
    } else if (isLc) {
      learningComponents.push(node);
    } else {
      unknownNodes.push(node);
    }

    /*
     * Index all nodes by their graph node ID. For StandardsFramework/SFI nodes this is
     * usually `case_identifier_uuid`; for LearningComponent nodes this is usually
     * `identifier`.
     */
    nodesById.set(node.id, node);
  }

  if (unknownNodes.length > 0) {
    /*
     * Surface the distinct label combinations and a handful of node IDs so the KG
     * author can search the source JSON and decide whether to extend the partitioner
     * or correct a typo.
     */
    const uniqueLabelSets = [
      ...new Set(unknownNodes.map((n) => JSON.stringify(n.labels))),
    ];
    const sampleIds = unknownNodes.slice(0, 3).map((n) => n.id);
    console.error(
      `Ignored ${unknownNodes.length} node(s) with unrecognized labels. ` +
        `Unique label sets: ${uniqueLabelSets.join(", ")}. ` +
        `Sample node IDs: ${sampleIds.join(", ")}.`,
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
    let existingStartRels = relsByStart.get(rel.start);

    if (!existingStartRels) {
      existingStartRels = [];
      relsByStart.set(rel.start, existingStartRels);
    }

    existingStartRels.push(rel);

    let existingEndRels = relsByEnd.get(rel.end);

    if (!existingEndRels) {
      existingEndRels = [];
      relsByEnd.set(rel.end, existingEndRels);
    }

    existingEndRels.push(rel);
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

/**
 * Reduce a `GraphNode` to a flat, camelCase summary suitable for inclusion in
 * MCP tool responses.
 *
 * This is the canonical "node summary" shape used across the progression,
 * search, and lookup tool outputs — anywhere a node needs to be returned to the
 * model with enough context to identify and describe it, but without the full
 * nested `properties` object the underlying graph carries. Doubles as the
 * snake_case → camelCase boundary for the MCP API surface, so source properties
 * like `statement_code` are renamed to `statementCode` here and never leak
 * through downstream.
 *
 * Two field families fall back to "supporting SFI" variants in `metadata`:
 *
 * - `canonicalPathKey` — `metadata.canonical_path_key`, falling back to
 *   `metadata.supporting_sfi_canonical_path_key`.
 * - `sourceLabel` — `metadata.source_label`, falling back to
 *   `metadata.supporting_sfi_source_label`.
 *
 * Both fallbacks exist because Learning Components don't carry these fields
 * directly; instead they reference the SFI they support. The fallback lets the
 * same compact shape describe both kinds of node without the caller having to
 * branch on `nodeType`.
 *
 * `nodeType` is derived from the node's labels via `nodeTypeFromLabels`.
 *
 * Description truncation takes `[0, maxDescription)` of the original string
 * with `"..."` appended. So the output is at most `maxDescription + 3`
 * characters, not `maxDescription`. Falsy descriptions (`undefined`, `null`,
 * `""`) pass through unchanged — there's nothing to truncate, and preserving
 * the original falsy value lets callers distinguish "missing" from "empty after
 * truncation". The `Math.max(0, maxDescription)` guard clamps negative inputs
 * so that `.slice(0, -n)` doesn't accidentally chop from the end.
 *
 * `subject` carries the same newline-to-space normalization as the rest of the
 * codebase, since `academic_subject` occasionally contains embedded newlines in
 * the source data.
 *
 * @param node - The graph node to compact. May be any kind: framework, SFI, LC,
 *   or unlabeled.
 * @param maxDescription - Maximum description length before the `"..."` suffix
 *   is appended, in characters. Defaults to `220`. Negative values are clamped
 *   to `0`.
 *
 * @returns A flat, camelCase, JSON-serializable summary of the node. Fields
 *   that don't apply to the node's kind (e.g. `statementCode` on a framework)
 *   come through as `undefined` rather than being omitted, so consumers can
 *   index into them unconditionally.
 */
function compactNode(
  node: GraphNode,
  maxDescription = 220,
): Record<string, unknown> {
  const metadata = node.properties.metadata ?? {};
  const desc = node.properties.description;
  return {
    canonicalPathKey:
      metadata.canonical_path_key ?? metadata.supporting_sfi_canonical_path_key,
    description: !desc
      ? desc
      : desc.length > maxDescription
        ? `${desc.slice(0, Math.max(0, maxDescription))}...`
        : desc,
    gradeLevel: node.properties.grade_level,
    identifier: node.properties.identifier,
    labels: node.labels,
    name: node.properties.name,
    nodeType: nodeTypeFromLabels(node.labels),
    normalizedStatementType: node.properties.normalized_statement_type,
    sourceLabel: metadata.source_label ?? metadata.supporting_sfi_source_label,
    statementCode: node.properties.statement_code,
    statementType: node.properties.statement_type,
    subject: node.properties.academic_subject?.replaceAll("\n", " "),
    uuid: node.id,
  };
}

/**
 * Tally string occurrences in an array, returning a frequency map sorted
 * alphabetically by key.
 *
 * Each value is normalized before counting:
 *
 * - `null`, `undefined`, empty strings, and whitespace-only strings are bucketed
 *   together under the key `"Unspecified"`.
 * - All other strings are passed through `normalizeWhitespace` (internal runs of
 *   whitespace collapsed to a single space, ends trimmed) before being used as
 *   the bucket key. So " Objectif spécifique " and "Objectif spécifique" count
 *   to the same bucket.
 *
 * Counting is case-sensitive: "Foo" and "foo" are distinct buckets. The
 * returned object's keys are ordered alphabetically via `localeCompare`.
 *
 * @example
 *   // Basic tally over a homogeneous string array.
 *   countBy(["hasChild", "supports", "hasChild", "buildsTowards"]);
 *   // -> { buildsTowards: 1, hasChild: 2, supports: 1 }
 *
 * @example
 *   // null, undefined, "", and whitespace-only strings all collapse into "Unspecified".
 *   countBy([
 *     "Conjugaison",
 *     null,
 *     "Conjugaison",
 *     undefined,
 *     "",
 *     "   ",
 *     "Orthographe",
 *   ]);
 *   // -> { Conjugaison: 2, Orthographe: 1, Unspecified: 4 }
 *
 * @example
 *   // Internal whitespace is collapsed and ends are trimmed before bucketing.
 *   countBy(["  Objectif  spécifique ", "Objectif spécifique"]);
 *   // -> { "Objectif spécifique": 2 }
 *
 * @example
 *   // Counting is case-sensitive.
 *   countBy(["Foo", "foo", "Foo"]);
 *   // -> { foo: 1, Foo: 2 }
 *
 * @param values - Array of strings to tally. `null` and `undefined` entries are
 *   allowed and are routed to the `"Unspecified"` bucket alongside
 *   empty/whitespace-only strings.
 *
 * @returns A plain object mapping each normalized key to its count, with
 *   entries ordered alphabetically by key.
 */
function countBy(
  values: Array<string | null | undefined>,
): Record<string, number> {
  const counts: Record<string, number> = {};

  for (const value of values) {
    const key =
      value && value.trim().length > 0
        ? normalizeWhitespace(value)
        : "Unspecified";
    counts[key] = (counts[key] ?? 0) + 1;
  }

  return Object.fromEntries(
    Object.entries(counts).sort(([a], [b]) => a.localeCompare(b)),
  );
}

/**
 * Create a set of query helpers that operate over a loaded Knowledge Graph and
 * its pre-built indexes.
 *
 * This is the main "query layer" for the MCP server. It accepts a
 * `KnowledgeGraphContext` (the raw graph plus the index maps produced by
 * `buildKnowledgeGraphIndexes`) and returns an object of pure functions that
 * the MCP tool handlers call to answer requests.
 *
 * The returned helpers fall into several categories:
 *
 * - **Lookup** (`findStandardItem`, `findLearningComponent`, `findAnyNode`) —
 *   resolve a user-supplied identifier (graph node ID, CASE UUID, or
 *   `properties.identifier`) to a concrete `GraphNode`.
 * - **Hierarchy traversal** (`getAncestors`, `getDescendants`, `getChildrenAny`,
 *   `getSiblingItems`, `getPathForNode`) — walk the `hasChild` tree in either
 *   direction.
 * - **Cross-reference** (`getLearningComponentsForStandard`,
 *   `getStandardsSupportedByLearningComponent`,
 *   `getSupportRelationshipsForLearningComponent`, `getRelatesTo`) — follow
 *   `supports` and `relatesTo` edges between SFIs and LearningComponents.
 * - **Progression** (`buildProgressionTraversal`) — traverse
 *   `buildsTowards`/`relatesTo` edges to map learning progressions.
 * - **Search and browse** (`searchItems`, `buildHierarchyForSubject`,
 *   `getFacetValues`) — full-text-ish search with facet filters and
 *   hierarchical subject browsing.
 * - **Serialization** (`compactNode`, `detailedNode`, `provenanceForNode`) —
 *   shape a `GraphNode` into the JSON payload returned by MCP tools.
 *
 * All functions are read-only; nothing mutates the underlying graph or indexes.
 *
 * @param context - A `KnowledgeGraphContext` containing the parsed KG and its
 *   index maps.
 *
 * @returns An object of query functions consumed by the MCP tool handlers in
 *   `index.ts`.
 */
export function createKnowledgeGraphUtils(
  context: KnowledgeGraphContext,
): KnowledgeGraphUtils {
  const {
    frameworks,
    kg,
    lcByIdentifier,
    learningComponents,
    nodesById,
    relsByEnd,
    relsByStart,
    sfis,
    sfisByIdentifier,
  } = context;

  /**
   * Build a tree-shaped view of all standards within a given academic subject
   * by walking `hasChild` relationships among the closure-captured SFIs.
   *
   * Roots are discovered by scanning every SFI for a matching
   * `academic_subject`, then keeping those whose `hasChild` parent is either
   * absent or sits under a different subject. This makes the function robust to
   * frameworks that mix multiple subjects under a shared ancestor: the returned
   * roots are always the shallowest in-subject nodes, not necessarily the
   * framework's outermost items.
   *
   * Subject comparison is whitespace-normalized, lowercased, and tolerant of
   * embedded newlines in `academic_subject` (which the source data occasionally
   * carries), so "Mathematics", " mathematics ", and "Mathe-\nmatics" all match
   * the same bucket.
   *
   * Each node in the returned tree is a plain object with `childCount`,
   * `children`, `code`, `description`, `identifier`, `type`, and `uuid`.
   * Descriptions are truncated at 150 characters at recursed levels and at 100
   * characters in the flat leaf representation. Recursion is capped at four
   * levels (`depth` 0–3); nodes below that are emitted as a flat summary
   * (`childCount`, `code`, `description`, `identifier`) rather than recursed
   * into, which keeps the response size bounded for deep frameworks.
   *
   * `gradeFilter`, when provided, is applied only below the roots — top-level
   * items are never pruned by it. Descendants are kept if their `grade_level`
   * or `statement_code` contains the filter substring. This lets a caller scope
   * the tree to a single grade band ("Grade 4", "G4") while still surfacing the
   * full set of subject roots for orientation.
   *
   * @param subject - Academic subject name to filter by (e.g. "Mathematics",
   *   "Science"). Compared case-insensitively after whitespace and newline
   *   normalization.
   * @param gradeFilter - Optional grade-band substring (e.g. "Grade 4", "G4").
   *   Applied only to descendants of the roots, not to the roots themselves.
   *
   * @returns An array of plain hierarchy objects, one per top-level SFI in the
   *   subject. Empty if no SFI matches.
   */
  function buildHierarchyForSubject(
    subject: string,
    gradeFilter?: string,
  ): object[] {
    const normalizedSubject = normalizeOptionalText(subject);
    const topLevelItems: GraphNode[] = [];

    for (const node of sfis) {
      const itemSubject = normalizeOptionalText(
        node.properties.academic_subject?.replaceAll("\n", " "),
      );

      if (itemSubject === normalizedSubject) {
        const parent = getParent(node.id);
        const parentSubject = normalizeOptionalText(
          parent?.properties.academic_subject?.replaceAll("\n", " "),
        );

        if (!parent || parentSubject !== normalizedSubject) {
          topLevelItems.push(node);
        }
      }
    }

    /**
     * Recursive helper that materializes one node of the subject hierarchy into
     * the plain-object shape consumed by callers of
     * `buildHierarchyForSubject`.
     *
     * Children are presented one of two ways depending on `depth`:
     *
     * - At `depth < 3`, children are recursed via `buildNode(child, depth + 1)`,
     *   yielding the same full shape (`childCount`, `children`, `code`,
     *   `description`, `identifier`, `type`, `uuid`).
     * - At `depth >= 3`, children are emitted as a flat summary (`childCount`,
     *   `code`, `description`, `identifier`) with no further `children` field.
     *   This caps the tree at four full levels (0–3) regardless of the
     *   underlying graph depth and keeps response size bounded.
     *
     * Descriptions are truncated with an ellipsis at 150 characters on the
     * recursed node and at 100 characters on the flat leaf summary — the
     * shorter leaf limit is intentional, since leaves are emitted in bulk and
     * would otherwise dominate the response.
     *
     * `childCount` always reflects the unfiltered count returned by
     * `getChildren`, even when `gradeFilter` removes some children from the
     * `children` array. So `node.childCount !== node.children.length` is a
     * normal, meaningful condition once a filter is in play — it tells the
     * caller "there are 12 standards under this node, 3 of which match the
     * filter" without a separate query.
     *
     * `gradeFilter` is captured from the enclosing `buildHierarchyForSubject`
     * closure (not a parameter) and is applied only when `depth > 0`.
     * Concretely, this means the root call (`buildNode(node)` with `depth`
     * defaulting to 0) does not filter its own children — only deeper recursive
     * calls do. As a consequence, the immediate children of a returned root
     * always appear in full, while everything below them is grade-filtered. See
     * the parent function's docstring for the rationale.
     *
     * @param node - The SFI node to render at this position in the tree.
     * @param depth - Zero-indexed distance from the hierarchy root. Drives both
     *   the four-level recursion cap and whether `gradeFilter` applies.
     *   Defaults to `0`, which is the value the top-level `.map(buildNode)`
     *   call relies on.
     *
     * @returns A plain JSON-serializable hierarchy-node object, suitable for
     *   inclusion in the array returned by `buildHierarchyForSubject`.
     */
    function buildNode(node: GraphNode, depth: number = 0): object {
      const children = getChildren(node.id);
      const filteredChildren =
        gradeFilter && depth > 0
          ? children.filter(
              (c) =>
                c.properties.grade_level?.includes(gradeFilter) ||
                c.properties.statement_code?.includes(gradeFilter),
            )
          : children;
      return {
        childCount: children.length,
        children:
          depth < 3
            ? filteredChildren.map((c) => buildNode(c, depth + 1))
            : filteredChildren.map((c) => ({
                childCount: getChildren(c.id).length,
                code: c.properties.statement_code,
                description:
                  (c.properties.description?.length ?? 0) > 100
                    ? c.properties.description!.slice(0, 100) + "..."
                    : c.properties.description,
                identifier: c.properties.identifier,
              })),
        code: node.properties.statement_code,
        description:
          (node.properties.description?.length ?? 0) > 150
            ? node.properties.description!.slice(0, 150) + "..."
            : node.properties.description,
        identifier: node.properties.identifier,
        type: node.properties.normalized_statement_type,
        uuid: node.id,
      };
    }

    return topLevelItems.map((node) => buildNode(node));
  }

  /**
   * Walk the prerequisite/successor/related graph around an SFI to a bounded
   * depth, returning a single-call snapshot of its position in the
   * progression.
   *
   * Three independent traversals are run, each gated by `direction`:
   *
   * - `buildsFrom` — predecessors. Walks `buildsTowards` relationships in reverse
   *   (via `relsByEnd`), since a relationship `X --buildsTowards--> Y` means X
   *   is a prerequisite of Y. Recursive up to `depth` hops.
   * - `buildsTowards` — successors. Walks `buildsTowards` relationships forward
   *   (via `relsByStart`). Recursive up to `depth` hops.
   * - `related` — sibling/peer connections via `getRelatesTo`. **One hop only**,
   *   regardless of `depth`. This asymmetry is intentional: relatedness carries
   *   no transitive meaning the way prerequisites do.
   *
   * Each axis carries its own dedup set (`seenFrom`/`seenTo`/`seenRelated`).
   * That means a node reachable along multiple axes — e.g. both a prerequisite
   * and a peer — will appear in both arrays. Within a single axis, every node
   * appears at most once, so cycles in the `buildsTowards` graph are safe.
   *
   * Both recursive arms filter to `StandardsFrameworkItem` nodes only;
   * `LearningComponent` and unknown-label nodes are excluded even if they
   * participate in the relationships.
   *
   * The returned `depth` and `direction` fields echo the input parameters as
   * given — they describe the traversal that was _requested_, not the depth
   * actually reached. A traversal that bottoms out after one hop still reports
   * the original `depth`. Result nodes are passed through `compactNode` for a
   * trimmed shape suitable for inclusion in MCP tool responses.
   *
   * Used by the MCP `get_progression` tool to answer "what comes before/after
   * this standard, and what's adjacent to it" in a single call.
   *
   * @param standardNode - The SFI to anchor the traversal on. Returned under
   *   the `target` field, compacted.
   * @param direction - Which axes to populate. `"both"` returns all three;
   *   `"builds_from"`/`"builds_towards"`/`"related"` populate only the matching
   *   axis and leave the others as empty arrays.
   * @param depth - Maximum hop count for the `buildsFrom` and `buildsTowards`
   *   traversals. Has no effect on `related`, which is always one hop.
   *
   * @returns An object `{ target, buildsFrom, buildsTowards, related, depth,
   *   direction }`. Axes not selected by `direction` are present as empty
   *   arrays rather than omitted, so consumers can index into them
   *   unconditionally.
   */
  function buildProgressionTraversal(
    standardNode: GraphNode,
    direction: ProgressionDirection,
    depth: number,
  ): Record<string, unknown> {
    const seenFrom = new Set<string>();
    const seenTo = new Set<string>();
    const seenRelated = new Set<string>();

    /**
     * Recursive helper that collects the prerequisite chain (the "builds from"
     * direction) for a node, up to a bounded number of hops.
     *
     * One hop is taken by reading `relsByEnd.get(nodeId)` and keeping the
     * `buildsTowards` edges that _end_ at this node — the `start` of each such
     * edge is a node that builds towards us, i.e. a prerequisite. The
     * relationship type literal (`"buildsTowards"`) reads inverted relative to
     * the function name; the inversion is correct because we're indexing on the
     * edge's destination rather than its source.
     *
     * Discovered nodes are filtered to `StandardsFrameworkItem` only — orphaned
     * edges (where `nodesById` has no entry for `rel.start`) and edges into
     * non-SFI nodes (e.g. `LearningComponent`) are silently dropped.
     *
     * Dedup is performed against the closure-captured `seenFrom` set shared
     * with the rest of the traversal. A node is added to `seenFrom` the first
     * time it is encountered and skipped on subsequent visits, so each
     * prerequisite appears in the result at most once and cycles in the
     * `buildsTowards` graph terminate safely. Note that this dedup is _eager_:
     * a node first reached on a long path will block the same node from being
     * re-emitted on a shorter path discovered later, so the returned set is
     * "all reachable prerequisites within `remainingDepth`" rather than a
     * particular layer-by-layer ordering.
     *
     * The result interleaves direct predecessors with their own predecessors
     * via a depth-first flat-map, so callers receive a flattened list of all
     * prerequisites within range, not a tree. If layered structure is needed,
     * the caller must reconstruct it from the relationships themselves.
     *
     * @param nodeId - Graph node ID to collect predecessors for. The node
     *   itself is never included in the output.
     * @param remainingDepth - Maximum hop count remaining for this branch of
     *   the recursion. Decremented on each recursive call; values `<= 0` return
     *   an empty array, which is the recursion's base case.
     *
     * @returns A flat, deduplicated list of `StandardsFrameworkItem` nodes that
     *   transitively build towards `nodeId` within `remainingDepth` hops. Empty
     *   if `remainingDepth <= 0`, if no `buildsTowards` edges land on the node,
     *   or if every reachable predecessor has already been visited via the
     *   shared `seenFrom` set.
     */
    function collectBuildsFrom(
      nodeId: string,
      remainingDepth: number,
    ): GraphNode[] {
      if (remainingDepth <= 0) return [];

      const direct = (relsByEnd.get(nodeId) || [])
        .filter((rel) => rel.type === "buildsTowards")
        .map((rel) => nodesById.get(rel.start))
        .filter((node): node is GraphNode => {
          if (!node) return false;
          return node.labels.includes("StandardsFrameworkItem");
        })
        .filter((node) => {
          if (seenFrom.has(node.id)) return false;
          seenFrom.add(node.id);
          return true;
        });
      return [
        ...direct,
        ...direct.flatMap((node) =>
          collectBuildsFrom(node.id, remainingDepth - 1),
        ),
      ];
    }

    /**
     * Recursive helper that collects the successor chain (the "builds towards"
     * direction) for a node, up to a bounded number of hops.
     *
     * One hop is taken by reading `relsByStart.get(nodeId)` and keeping the
     * `buildsTowards` edges that _start_ at this node — the `end` of each such
     * edge is a node we build towards, i.e. a successor. Unlike the
     * `collectBuildsFrom` mirror, the function name and edge direction line up
     * here: we're walking `buildsTowards` forward.
     *
     * Discovered nodes are filtered to `StandardsFrameworkItem` only — orphaned
     * edges (where `nodesById` has no entry for `rel.end`) and edges into
     * non-SFI nodes (e.g. `LearningComponent`) are silently dropped.
     *
     * Dedup is performed against the closure-captured `seenTo` set shared with
     * the rest of the traversal. A node is added to `seenTo` the first time it
     * is encountered and skipped on subsequent visits, so each successor
     * appears in the result at most once and cycles in the `buildsTowards`
     * graph terminate safely. Note that this dedup is _eager_: a node first
     * reached on a long path will block the same node from being re-emitted on
     * a shorter path discovered later, so the returned set is "all reachable
     * successors within `remainingDepth`" rather than a particular
     * layer-by-layer ordering.
     *
     * The result interleaves direct successors with their own successors via a
     * depth-first flat-map, so callers receive a flattened list of all
     * successors within range, not a tree. If layered structure is needed, the
     * caller must reconstruct it from the relationships themselves.
     *
     * @param nodeId - Graph node ID to collect successors for. The node itself
     *   is never included in the output.
     * @param remainingDepth - Maximum hop count remaining for this branch of
     *   the recursion. Decremented on each recursive call; values `<= 0` return
     *   an empty array, which is the recursion's base case.
     *
     * @returns A flat, deduplicated list of `StandardsFrameworkItem` nodes that
     *   `nodeId` transitively builds towards within `remainingDepth` hops.
     *   Empty if `remainingDepth <= 0`, if no `buildsTowards` edges originate
     *   at the node, or if every reachable successor has already been visited
     *   via the shared `seenTo` set.
     */
    function collectBuildsTowards(
      nodeId: string,
      remainingDepth: number,
    ): GraphNode[] {
      if (remainingDepth <= 0) return [];

      const direct = (relsByStart.get(nodeId) || [])
        .filter((rel) => rel.type === "buildsTowards")
        .map((rel) => nodesById.get(rel.end))
        .filter(
          (node): node is GraphNode =>
            node != null && node.labels.includes("StandardsFrameworkItem"),
        )
        .filter((node) => {
          if (seenTo.has(node.id)) return false;
          seenTo.add(node.id);
          return true;
        });
      return [
        ...direct,
        ...direct.flatMap((node) =>
          collectBuildsTowards(node.id, remainingDepth - 1),
        ),
      ];
    }

    const related = getRelatesTo(standardNode.id).filter((node) => {
      if (seenRelated.has(node.id)) return false;

      seenRelated.add(node.id);
      return true;
    });
    return {
      buildsFrom:
        direction === "both" || direction === "builds_from"
          ? collectBuildsFrom(standardNode.id, depth).map((node) =>
              compactNode(node),
            )
          : [],
      buildsTowards:
        direction === "both" || direction === "builds_towards"
          ? collectBuildsTowards(standardNode.id, depth).map((node) =>
              compactNode(node),
            )
          : [],
      depth,
      direction,
      related:
        direction === "both" || direction === "related"
          ? related.map((node) => compactNode(node))
          : [],
      target: compactNode(standardNode),
    };
  }

  /**
   * Render a `GraphNode` as a full-detail JSON payload for MCP tool responses
   * that surface a single item.
   *
   * Extends the canonical `compactNode` shape (so all of its camelCase summary
   * fields are present) with a `properties` block carrying the underlying
   * `node.properties` object verbatim — minus the same newline normalization
   * applied to `academic_subject` elsewhere, since that field occasionally
   * carries embedded newlines in the source data.
   *
   * The compact summary is emitted with a description ceiling of 1000
   * characters, an order of magnitude higher than `compactNode`'s 220 default.
   * This mirrors the fact that detailed views are returned one item at a time
   * (e.g. `get_item`), so the per-response payload budget can absorb a longer
   * description without dominating the response.
   *
   * Used by every `index.ts` tool handler that returns a single resolved node:
   * `get_item`, `get_path`, and `navigate` (for the focal item alongside its
   * neighbors). The compact view should be preferred for list-shaped
   * responses.
   *
   * @param node - The graph node to render in detail. May be any kind:
   *   framework, SFI, LC, or unlabeled.
   *
   * @returns A flat, JSON-serializable object combining the camelCase summary
   *   from `compactNode` with a nested snake_case `properties` block.
   */
  function detailedNode(node: GraphNode): Record<string, unknown> {
    return {
      ...compactNode(node, 1000),
      properties: {
        ...node.properties,
        academic_subject: node.properties.academic_subject?.replaceAll(
          "\n",
          " ",
        ),
      },
    };
  }

  /**
   * Resolve an opaque user-supplied identifier to whichever kind of KG node it
   * refers to: standard item, learning component, or framework.
   *
   * Tries each kind in order — SFI first via `findStandardItem`, then LC via
   * `findLearningComponent`, then framework via `nodesById` — and returns the
   * first match wrapped with a `type` discriminator. The order matters because
   * SFIs and LCs both accept multiple identifier shapes (graph node ID, CASE
   * UUID, `properties.identifier`, `metadata.canonical_node_id`), and a single
   * input string could legitimately match more than one kind in pathological
   * graphs; the SFI-first ordering reflects which kind is more likely to be the
   * user's intent for ambiguous inputs in this dataset.
   *
   * Frameworks are intentionally checked last and only via `nodesById` — there
   * is no per-kind index for them — so framework lookups must use the graph
   * node ID exactly.
   *
   * Used by every MCP tool handler that takes a free-form `identifier`
   * argument: `get_item`, `get_path`, `get_provenance`, `get_related_items`,
   * `navigate`. The `type` discriminator lets handlers branch on node kind
   * without re-inspecting `labels`.
   *
   * @param identifier - StandardsFrameworkItem identifier, LearningComponent
   *   identifier, graph node UUID, or CASE UUID. Any of the shapes accepted by
   *   `findStandardItem`/`findLearningComponent` works.
   *
   * @returns An object `{ item, type }` where `type` is `"standard_item"`,
   *   `"learning_component"`, or `"framework"`, or `null` if the identifier
   *   matches none of the three.
   */
  function findAnyNode(
    identifier: string,
  ): { item: GraphNode; type: string } | null {
    const standard = findStandardItem(identifier);

    if (standard) return { item: standard, type: "standard_item" };

    const learningComponent = findLearningComponent(identifier);

    if (learningComponent)
      return {
        item: learningComponent,
        type: "learning_component",
      };

    const framework = nodesById.get(identifier);

    if (framework && framework.labels.includes("StandardsFramework")) {
      return { item: framework, type: "framework" };
    }

    return null;
  }

  /**
   * Resolve an identifier to a `LearningComponent` graph node, trying the cheap
   * indexed lookups first and falling back to a linear scan as a last resort.
   *
   * Three lookup paths are attempted in order:
   *
   * 1. `lcByIdentifier` — keyed on `properties.identifier`, the most common case
   *    for caller-facing IDs.
   * 2. `nodesById` — keyed on the graph node ID; the result is then label- checked
   *    to ensure we don't accidentally return a non-LC node that shares the ID
   *    space.
   * 3. Linear scan over `learningComponents` looking for a match on
   *    `metadata.canonical_node_id`. This is O(n) but only reached when both
   *    indexes miss, so it stays cheap in practice.
   *
   * Used by `findAnyNode` as its second resolution attempt and directly by the
   * `get_progression` tool handler when the input identifier turns out to refer
   * to an LC rather than an SFI.
   *
   * @param identifier - LearningComponent `properties.identifier`, graph node
   *   UUID, or `metadata.canonical_node_id`. The function does not validate the
   *   shape of the input string; whichever index matches first wins.
   *
   * @returns The matching `LearningComponent` `GraphNode`, or `undefined` if no
   *   LC matches the identifier through any of the three lookup paths.
   */
  function findLearningComponent(identifier: string): GraphNode | undefined {
    const byIdentifier = lcByIdentifier.get(identifier);

    if (byIdentifier) return byIdentifier;

    const byId = nodesById.get(identifier);

    if (byId && byId.labels.includes("LearningComponent")) return byId;

    return learningComponents.find(
      (node) => node.properties.metadata?.canonical_node_id === identifier,
    );
  }

  /**
   * Resolve an identifier to a `StandardsFrameworkItem` (SFI) graph node,
   * trying the cheap indexed lookups first and falling back to a linear scan
   * over multiple candidate fields as a last resort.
   *
   * Three lookup paths are attempted in order:
   *
   * 1. `sfisByIdentifier` — keyed on `properties.identifier`, the most common case
   *    for caller-facing IDs.
   * 2. `nodesById` — keyed on the graph node ID; the result is then label- checked
   *    to ensure we don't accidentally return a non-SFI node that shares the ID
   *    space.
   * 3. Linear scan over `sfis` looking for a match on any of
   *    `case_identifier_uuid`, `case_identifier_uri`, or
   *    `metadata.canonical_node_id`. This is O(n) but only reached when both
   *    indexes miss, so it stays cheap in practice. The CASE UUID/URI fallback
   *    in particular makes it possible to look up SFIs by the identifiers used
   *    in the upstream CASE framework, even when the graph's own
   *    `properties.identifier` differs.
   *
   * Used by `findAnyNode` as its first resolution attempt and directly by the
   * `get_learning_components_for_standard`, `get_progression`, and
   * `get_related_items` tool handlers, which all expect an SFI specifically.
   *
   * @param identifier - SFI `properties.identifier`, graph node UUID, CASE
   *   UUID, CASE URI, or `metadata.canonical_node_id`. The function does not
   *   validate the shape of the input string; whichever index or field matches
   *   first wins.
   *
   * @returns The matching SFI `GraphNode`, or `undefined` if no SFI matches the
   *   identifier through any of the three lookup paths.
   */
  function findStandardItem(identifier: string): GraphNode | undefined {
    const byIdentifier = sfisByIdentifier.get(identifier);

    if (byIdentifier) return byIdentifier;

    const byId = nodesById.get(identifier);

    if (byId && byId.labels.includes("StandardsFrameworkItem")) return byId;

    return sfis.find(
      (node) =>
        node.properties.case_identifier_uuid === identifier ||
        node.properties.case_identifier_uri === identifier ||
        node.properties.metadata?.canonical_node_id === identifier,
    );
  }

  /**
   * Walk the `hasChild` hierarchy upwards from a node and return the chain of
   * ancestors in root-to-parent order.
   *
   * Each step reads the immediate parent via `getParentAny`, which honors
   * `order_index` ordering when a node has multiple incoming `hasChild` edges
   * and accepts both SFIs and other node kinds as parents (in contrast to
   * `getParent`, which is SFI-only). The walk uses `unshift` to prepend each
   * discovered ancestor, so the returned array reads root-first: index 0 is the
   * outermost ancestor reachable from the input, and the last entry is the
   * input's immediate parent.
   *
   * A `seen` set guards against cycles in the `hasChild` graph: if the parent
   * walk re-encounters a previously-seen node, traversal halts. This makes the
   * function safe on malformed graphs without silently looping.
   *
   * The input node itself is never included in the result. A node with no
   * parent returns `[]`.
   *
   * Used by `getPathForNode` to materialize the breadcrumb path for both SFIs
   * and LCs (LCs walk up from the SFI they support), and by the `navigate` tool
   * handler for `direction: "ancestors"`.
   *
   * @param nodeId - Graph node ID to start the walk from. The node itself is
   *   never included in the output.
   *
   * @returns The chain of ancestors in root-to-parent order. Empty if the node
   *   has no parent or if the immediate parent is unresolvable.
   */
  function getAncestors(nodeId: string): GraphNode[] {
    const ancestors: GraphNode[] = [];
    const seen = new Set<string>();
    let currentParent = getParentAny(nodeId);

    while (currentParent && !seen.has(currentParent.id)) {
      seen.add(currentParent.id);
      ancestors.unshift(currentParent);
      currentParent = getParentAny(currentParent.id);
    }

    return ancestors;
  }

  /**
   * Return the immediate `hasChild` children of a node, restricted to
   * `StandardsFrameworkItem` nodes only.
   *
   * Walks the outgoing relationship adjacency list (`relsByStart`) for one hop,
   * keeps the `hasChild` edges, resolves each edge's `end` to a node via
   * `nodesById`, and filters to SFIs. Edges into `LearningComponent` or
   * unknown-label nodes are silently dropped, as are orphaned edges where
   * `nodesById` has no entry.
   *
   * **Ordering is not normalized** — children are returned in the order their
   * relationships appear in `relsByStart`. If a callsite needs the canonical
   * curriculum order (`order_index` / `metadata.canonical_order_index`), use
   * `getChildrenAny` instead, which handles the sort.
   *
   * Used by `buildHierarchyForSubject`'s recursive `buildNode` helper to
   * populate the curriculum tree returned by the `browse_subject` tool. The
   * SFI-only filter is intentional there: that view is a curriculum-standards
   * tree and shouldn't include atomic learning components.
   *
   * @param parentNodeId - Graph node ID of the candidate parent. Need not
   *   itself be an SFI; the filter is on the children, not the parent.
   *
   * @returns The immediate SFI children, in `relsByStart` insertion order.
   *   Empty if the node has no `hasChild` edges, if every child is non-SFI, or
   *   if the node is unknown to the indexes.
   */
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

  /**
   * Return the immediate `hasChild` children of a node in canonical curriculum
   * order, accepting children of any KG label kind.
   *
   * Walks the outgoing relationship adjacency list (`relsByStart`) for one hop,
   * keeps the `hasChild` edges, sorts them by curriculum order, and resolves
   * each to a node. The sort key is the relationship's `order_index`, falling
   * back to `metadata.canonical_order_index`, falling back to `0`. This mirrors
   * how the upstream KG export records ordering and keeps deterministic output
   * across runs.
   *
   * Unlike `getChildren`, this function does **not** filter by node kind:
   * `LearningComponent` and unknown-label children are returned alongside SFIs.
   * Use this when callsites need the full set of structural children (the
   * `navigate` tool's `children`/`descendants`/`siblings` directions all rely
   * on this), and prefer `getChildren` when only SFIs are wanted (e.g.
   * curriculum-tree rendering in `browse_subject`).
   *
   * Orphaned edges (where `nodesById` has no entry for `rel.end`) are dropped
   * via `.filter(Boolean)`. The declared return type promises `GraphNode[]`
   * accordingly, even though the intermediate map produces `(GraphNode |
   * undefined)[]`.
   *
   * Used by `getDescendants`, `getParentAny` (indirectly via the same sort
   * pattern), `getSiblingItems`, and the `navigate` tool handler.
   *
   * @param parentNodeId - Graph node ID of the candidate parent. Need not
   *   itself be an SFI; children of any kind are returned.
   *
   * @returns The immediate children of the node, sorted by curriculum order
   *   index (ascending). Empty if the node has no `hasChild` edges or if every
   *   edge target is unresolvable.
   */
  function getChildrenAny(parentNodeId: string): GraphNode[] {
    const rels = relsByStart.get(parentNodeId) || [];
    return rels
      .filter((rel) => rel.type === "hasChild")
      .sort(
        (a, b) =>
          (a.properties.order_index ??
            a.properties.metadata?.canonical_order_index ??
            0) -
          (b.properties.order_index ??
            b.properties.metadata?.canonical_order_index ??
            0),
      )
      .map((rel) => nodesById.get(rel.end))
      .filter((node): node is GraphNode => node !== undefined);
  }

  /**
   * Walk the `hasChild` hierarchy downwards from a node and return all
   * descendants reachable within a bounded number of hops.
   *
   * Performs a depth-first traversal using `getChildrenAny` at each step, so
   * children are walked in canonical curriculum order
   * (`order_index`/`metadata.canonical_order_index`) and may be of any KG kind
   * — SFIs, LCs, or unknown — not just SFIs. A `seen` set guards against cycles
   * in the `hasChild` graph: a node is added to `seen` the first time it's
   * visited and skipped on subsequent encounters, so each descendant appears in
   * the result at most once and malformed cyclic graphs terminate safely.
   *
   * The output is a flat list of all reachable descendants, not a tree. The
   * order is the depth-first visitation order: a parent precedes its
   * descendants, and earlier siblings precede later ones. Callers needing tree
   * shape should reconstruct it from the relationships.
   *
   * The input node itself is never included. A `depth` of `0` returns `[]`.
   *
   * Used by the `navigate` tool handler for `direction: "descendants"`, which
   * exposes `depth` to the caller (capped at 5 by the schema).
   *
   * @param nodeId - Graph node ID to start the walk from. The node itself is
   *   never included in the output.
   * @param depth - Maximum hop count from the start node. `1` returns only
   *   immediate children; `2` includes grandchildren; etc. Values `<= 0`
   *   short-circuit to an empty array.
   *
   * @returns A flat, deduplicated, depth-first-ordered list of all descendants
   *   reachable within `depth` hops. Empty if the node has no children or if
   *   `depth <= 0`.
   */
  function getDescendants(nodeId: string, depth: number): GraphNode[] {
    const descendants: GraphNode[] = [];
    const seen = new Set<string>();

    /**
     * Recursive helper that performs the DFS walk for `getDescendants`.
     *
     * Reads the immediate children of `currentNodeId` via `getChildrenAny` (so
     * canonical-order sorting and any-kind acceptance both apply), skips any
     * child already present in the closure-captured `seen` set, and pushes new
     * ones onto the closure-captured `descendants` array before recursing. The
     * recursion terminates either when `remainingDepth` hits `0` or when every
     * remaining child has already been visited.
     *
     * @param currentNodeId - Graph node ID whose children are walked at this
     *   recursive step.
     * @param remainingDepth - Hop count remaining for this branch of the
     *   recursion. Decremented on each recursive call; values `<= 0` return
     *   immediately, which is the recursion's base case.
     */
    function visit(currentNodeId: string, remainingDepth: number): void {
      if (remainingDepth <= 0) return;
      for (const child of getChildrenAny(currentNodeId)) {
        if (seen.has(child.id)) continue;
        seen.add(child.id);
        descendants.push(child);
        visit(child.id, remainingDepth - 1);
      }
    }

    visit(nodeId, depth);
    return descendants;
  }

  /**
   * Build the full set of filterable facet values and aggregate counts for the
   * loaded Knowledge Graph, in a single pass-style call.
   *
   * The returned object has two halves:
   *
   * - **Facet values** (`gradeLevels`, `learningComponentSourceLabels`,
   *   `nodeTypes`, `normalizedStatementTypes`, `relationshipTypes`,
   *   `sourceLabels`, `statementTypes`, `subjects`) — the unique, sorted lists
   *   of values that callers can pass back as filters to `searchItems` and
   *   related tools. All string lists go through `uniqueSorted`, so empty,
   *   whitespace-only, and nullish entries are dropped (not bucketed). The
   *   exception is `nodeTypes`, which is a fixed enumeration.
   * - **Aggregate counts** (`counts`) — frequency tallies for relationship types,
   *   source labels, and statement types via `countBy` (which buckets
   *   nullish/empty under `"Unspecified"`), plus simple cardinalities for
   *   frameworks, SFIs, LCs, and total relationships.
   *
   * Note the asymmetry: the same field (e.g. `source_label`) is summarized as a
   * value list (drops blanks) in `sourceLabels` and as a count map (buckets
   * blanks under `"Unspecified"`) in `counts.bySourceLabel`. The two views
   * answer different questions — "what can I filter on" vs. "how is the data
   * distributed" — so the divergent blank-handling is intentional.
   *
   * Used by the MCP `list_facets` and `overview` tool handlers to give the
   * model an upfront picture of the KG's shape before it starts issuing
   * targeted queries.
   *
   * @returns A JSON-serializable object with the fixed shape `{ counts,
   *   gradeLevels, learningComponentSourceLabels, nodeTypes,
   *   normalizedStatementTypes, relationshipTypes, sourceLabels,
   *   statementTypes, subjects }`.
   */
  function getFacetValues(): Record<string, unknown> {
    return {
      counts: {
        byRelationshipType: countBy(kg.relationships.map((rel) => rel.type)),
        bySourceLabel: countBy(
          sfis.map((node) => node.properties.metadata?.source_label),
        ),
        byStatementType: countBy(
          sfis.map((node) => node.properties.statement_type),
        ),
        frameworks: frameworks.length,
        learningComponents: learningComponents.length,
        relationships: kg.relationships.length,
        standardItems: sfis.length,
      },
      gradeLevels: getUniqueGradeLevels(),
      learningComponentSourceLabels: uniqueSorted(
        learningComponents.map(
          (node) => node.properties.metadata?.supporting_sfi_source_label,
        ),
      ),
      nodeTypes: ["framework", "standard_item", "learning_component"],
      normalizedStatementTypes: uniqueSorted(
        sfis.map((node) => node.properties.normalized_statement_type),
      ),
      relationshipTypes: uniqueSorted(kg.relationships.map((rel) => rel.type)),
      sourceLabels: uniqueSorted(
        sfis.map((node) => node.properties.metadata?.source_label),
      ),
      statementTypes: uniqueSorted(
        sfis.map((node) => node.properties.statement_type),
      ),
      subjects: getUniqueSubjects(),
    };
  }

  /**
   * Return the `LearningComponent` nodes that `supports` a given standard.
   *
   * `supports` edges are directed LC → SFI in the KG schema (the LC is the
   * `start`, the SFI is the `end`), so this lookup walks `relsByEnd` for the
   * standard's incoming edges and follows the `start` of each `supports` edge
   * back to the LC. Resolved nodes are then label-checked to drop any edge that
   * lands on a non-LC start (which would be malformed).
   *
   * Edges of any other type (`hasChild`, `buildsTowards`, `relatesTo`) are
   * ignored, as are orphaned edges where `nodesById` has no entry for
   * `rel.start`.
   *
   * Used by the `get_learning_components_for_standard` tool handler directly,
   * by `get_item` and `navigate` to enrich SFI responses with their supporting
   * LCs, and indirectly by `getPathForNode` when building the breadcrumb path
   * for an LC.
   *
   * @param standardNodeId - Graph node ID of the SFI to look up. Should be the
   *   SFI's graph node ID (i.e. what `findStandardItem` returns as `node.id`),
   *   not its `properties.identifier` or CASE UUID.
   *
   * @returns The `LearningComponent` nodes that support this standard. Empty if
   *   the standard has no incoming `supports` edges, if every incoming
   *   `supports` edge originates at a non-LC node, or if the identifier is
   *   unknown to the indexes.
   */
  function getLearningComponentsForStandard(
    standardNodeId: string,
  ): GraphNode[] {
    // supports relationships: LC (start) -> Standard (end).
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

  /**
   * Return the immediate `hasChild` parent of a node, restricted to
   * `StandardsFrameworkItem` parents only.
   *
   * Walks the incoming relationship adjacency list (`relsByEnd`) for one hop
   * and returns the `start` of the first `hasChild` edge whose start node is an
   * SFI. Edges of any other type, edges originating at LC or unknown-label
   * nodes, and orphaned edges (where `nodesById` has no entry for the start)
   * are all skipped.
   *
   * **Returns the first SFI parent encountered**, with no ordering guarantee if
   * a node has multiple incoming `hasChild` edges from SFIs. In practice the KG
   * schema is a tree at the standards level, so the multi-parent case does not
   * arise; if you need ordering-stable parent lookup across mixed node kinds,
   * use `getParentAny` instead, which sorts by `order_index`.
   *
   * Used by `buildHierarchyForSubject` to decide whether an SFI is a top-level
   * item within its subject (i.e. its parent is either absent or sits under a
   * different subject).
   *
   * @param childNodeId - Graph node ID of the candidate child. Need not itself
   *   be an SFI; the filter is on the parent, not the child.
   *
   * @returns The first SFI parent found, or `undefined` if the node has no
   *   incoming `hasChild` edges, if every incoming `hasChild` edge originates
   *   at a non-SFI, or if the identifier is unknown to the indexes.
   */
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

  /**
   * Return the immediate `hasChild` parent of a node in canonical curriculum
   * order, accepting parents of any KG label kind.
   *
   * Walks the incoming relationship adjacency list (`relsByEnd`) for one hop,
   * keeps the `hasChild` edges, sorts them by curriculum order, and resolves
   * the first one to a node. The sort key is the relationship's `order_index`,
   * falling back to `metadata.canonical_order_index`, falling back to `0` — the
   * same key used by `getChildrenAny`, so the parent/child walks line up. The
   * sort matters here because in malformed graphs a node can have more than one
   * incoming `hasChild` edge; choosing the lowest `order_index` yields a
   * deterministic answer rather than relying on insertion order.
   *
   * Unlike `getParent`, this function does **not** filter by node kind:
   * `LearningComponent` and unknown-label parents are returned alongside SFIs.
   * Use this for general-purpose hierarchy walks (the `getAncestors` chain, the
   * `navigate` tool's `direction: "parent"`); prefer `getParent` when the
   * consumer wants SFIs only.
   *
   * Used by `getAncestors` and `getSiblingItems` (which needs to find the
   * shared parent), as well as by the `navigate` tool handler.
   *
   * @param childNodeId - Graph node ID of the candidate child. Need not itself
   *   be an SFI; parents of any kind are returned.
   *
   * @returns The lowest-`order_index` parent reached via a `hasChild` edge, or
   *   `undefined` if no incoming `hasChild` edges exist or if the resolved
   *   parent is unknown to `nodesById`.
   */
  function getParentAny(childNodeId: string): GraphNode | undefined {
    const rels = relsByEnd.get(childNodeId) || [];
    const parentRel = rels
      .filter((rel) => rel.type === "hasChild")
      .sort(
        (a, b) =>
          (a.properties.order_index ??
            a.properties.metadata?.canonical_order_index ??
            0) -
          (b.properties.order_index ??
            b.properties.metadata?.canonical_order_index ??
            0),
      )[0];

    return parentRel ? nodesById.get(parentRel.start) : undefined;
  }

  /**
   * Build the breadcrumb path that locates a node within the curriculum
   * hierarchy, with shape that varies by node kind.
   *
   * Two cases are handled:
   *
   * - **LearningComponent** — Resolves the LC's supported standards via
   *   `getStandardsSupportedByLearningComponent`, picks the first one as the
   *   "primary", and emits a path of the form `[...primary's ancestors,
   *   primary, this LC]`. If the LC supports no standards, the path degenerates
   *   to a single-element array containing only the LC itself. The full list of
   *   supported standards is also returned alongside the path under
   *   `supportedStandards`/`supportedStandardCount` so consumers can see
   *   secondary anchors. **Picking the first supported standard as primary** is
   *   an arbitrary but deterministic choice — there's no primary-flag in the
   *   graph, and LCs that support multiple standards will have a stable but
   *   possibly surprising path root.
   * - **Anything else (SFI, framework, unknown)** — Emits a path of the form
   *   `[...ancestors, this node]`, no `supportedStandards` block.
   *
   * Path nodes are passed through `compactNode` so the response stays trimmed;
   * the focal node is also returned uncompacted-from-its-position but
   * separately as `target` for callers that need to refer to it explicitly
   * (e.g. to disambiguate "which node is this path about" without indexing into
   * the array).
   *
   * Used by the `get_path` tool handler directly and by the `get_item` tool
   * handler to enrich item responses with their hierarchy context.
   *
   * @param node - The graph node to build a path for. May be an SFI, LC,
   *   framework, or unlabeled — the LC branch is selected when the node carries
   *   the `LearningComponent` label.
   *
   * @returns A JSON-serializable object whose shape depends on the node kind.
   *   Always includes `path` (compact-node array) and `target` (compact node).
   *   For LCs, also includes `supportedStandards` (array of compact nodes) and
   *   `supportedStandardCount` (number).
   */
  function getPathForNode(node: GraphNode): Record<string, unknown> {
    if (node.labels.includes("LearningComponent")) {
      const supportedStandards = getStandardsSupportedByLearningComponent(
        node.id,
      );
      const primaryStandard = supportedStandards[0];
      return {
        path: primaryStandard
          ? [...getAncestors(primaryStandard.id), primaryStandard, node].map(
              (pathNode) => compactNode(pathNode),
            )
          : [compactNode(node)],
        supportedStandardCount: supportedStandards.length,
        supportedStandards: supportedStandards.map((standard) =>
          compactNode(standard),
        ),
        target: compactNode(node),
      };
    }

    return {
      path: [...getAncestors(node.id), node].map((pathNode) =>
        compactNode(pathNode),
      ),
      target: compactNode(node),
    };
  }

  /**
   * Return the SFIs that are connected to a given standard via a `relatesTo`
   * edge in either direction.
   *
   * `relatesTo` is treated as an undirected peer link in this codebase: an SFI
   * `X` related to `Y` is the same fact whether the edge is recorded as `X
   * --relatesTo--> Y` or `Y --relatesTo--> X`. This function therefore walks
   * both `relsByStart` (outgoing edges from the standard) and `relsByEnd`
   * (incoming edges to the standard), filters each to `relatesTo`, resolves the
   * other endpoint, and keeps only nodes labeled `StandardsFrameworkItem`.
   *
   * **Deduplication is the caller's responsibility.** If a single peer is
   * connected by both an outgoing and an incoming `relatesTo` edge (which the
   * schema doesn't forbid), it will appear twice in the returned array.
   * `buildProgressionTraversal` is the main caller and dedups against its own
   * `seenRelated` set; ad-hoc callers should do the same if exact-once
   * semantics matter.
   *
   * Edges of any other type are ignored, as are orphaned edges where
   * `nodesById` has no entry, and edges whose other endpoint is an LC or
   * unknown-label node.
   *
   * Used by `buildProgressionTraversal`, the `get_related_items` tool handler,
   * and the `get_item` tool handler when enriching SFI responses with their
   * related peers.
   *
   * @param standardNodeId - Graph node ID of the SFI to look up. Should be the
   *   SFI's graph node ID (i.e. what `findStandardItem` returns as `node.id`).
   *
   * @returns The SFI peers connected by `relatesTo` edges in either direction,
   *   possibly with duplicates if a peer is reciprocally connected. Empty if no
   *   `relatesTo` edges touch the node.
   */
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

  /**
   * Return the siblings of a node — the other children of its immediate parent
   * — in canonical curriculum order.
   *
   * Resolves the parent via `getParentAny` (which accepts parents of any kind
   * and sorts on `order_index`), then reads `getChildrenAny` for that parent
   * and removes the input node itself from the result. Both calls inherit the
   * canonical-order sort, so the returned list reflects the curriculum's
   * intended sibling ordering.
   *
   * **Returns siblings of any kind**, mirroring `getChildrenAny`: an SFI with
   * both SFI and LC children would surface its LC peers alongside its SFI
   * peers. If the input node has no parent (it's a framework root, or its
   * parent is unresolvable), an empty array is returned — there are no siblings
   * to speak of.
   *
   * Used by the `navigate` tool handler for `direction: "siblings"`.
   *
   * @param nodeId - Graph node ID whose siblings should be returned. The input
   *   node is excluded from the result.
   *
   * @returns The other children of the input node's immediate parent, in
   *   canonical curriculum order. Empty if the node has no parent or is the
   *   parent's only child.
   */
  function getSiblingItems(nodeId: string): GraphNode[] {
    const parent = getParentAny(nodeId);

    if (!parent) return [];

    return getChildrenAny(parent.id).filter((node) => node.id !== nodeId);
  }

  /**
   * Return the SFIs that a given LearningComponent `supports`.
   *
   * `supports` edges are directed LC → SFI in the KG schema (the LC is the
   * `start`, the SFI is the `end`), so this lookup walks `relsByStart` for the
   * LC's outgoing edges, keeps `supports` edges, resolves each edge's `end` to
   * a node, and label-checks for `StandardsFrameworkItem`. Edges of any other
   * type are ignored, as are orphaned edges where `nodesById` has no entry and
   * edges that land on a non-SFI end (which would be malformed).
   *
   * **Order of results is the order of edges in `relsByStart`** — there is no
   * curriculum-order sort. Callers that treat the first result as "primary"
   * (notably `getPathForNode`) inherit this insertion-order stability rather
   * than a semantic notion of primacy.
   *
   * Used by `getPathForNode` to anchor an LC's breadcrumb path on its primary
   * supported standard, by `provenanceForNode` to surface supporting SFIs in
   * provenance responses, and by the `get_item`, `get_progression`, and
   * `get_provenance` tool handlers.
   *
   * @param learningComponentNodeId - Graph node ID of the LC to look up. Should
   *   be the LC's graph node ID (i.e. what `findLearningComponent` returns as
   *   `node.id`), not its `properties.identifier`.
   *
   * @returns The SFIs supported by this LC, in `relsByStart` insertion order.
   *   Empty if the LC has no outgoing `supports` edges, if every `supports`
   *   edge lands on a non-SFI, or if the identifier is unknown to the indexes.
   */
  function getStandardsSupportedByLearningComponent(
    learningComponentNodeId: string,
  ): GraphNode[] {
    const rels = relsByStart.get(learningComponentNodeId) || [];
    return rels
      .filter((rel) => rel.type === "supports")
      .map((rel) => nodesById.get(rel.end))
      .filter((node): node is GraphNode => {
        if (!node) return false;
        return node.labels.includes("StandardsFrameworkItem");
      });
  }

  /**
   * Return the raw `supports` relationship objects originating at a given
   * LearningComponent.
   *
   * Reads the LC's outgoing relationship adjacency list (`relsByStart`) and
   * filters to `supports` edges. Unlike
   * `getStandardsSupportedByLearningComponent`, this returns the full
   * `GraphRelationship` objects — including their `properties.metadata` payload
   * (confidence, evidence, inference context, supporting SFI provenance, etc.)
   * — rather than the resolved target SFIs. Callers that need only the
   * supported standards should use `getStandardsSupportedByLearningComponent`
   * instead and avoid the relationship overhead.
   *
   * No node-kind filtering is applied: if a `supports` edge points at something
   * other than an SFI, the relationship is still returned. That differs from
   * `getStandardsSupportedByLearningComponent`, which drops non-SFI targets.
   *
   * Used by the `get_item` tool handler when surfacing per-edge support
   * metadata alongside a Learning Component, e.g. confidence scores or LLM
   * rationale recorded on the relationship rather than the node.
   *
   * @param learningComponentNodeId - Graph node ID of the LC to look up. Should
   *   be the LC's graph node ID (i.e. what `findLearningComponent` returns as
   *   `node.id`).
   *
   * @returns The `supports` relationships originating at this LC, in
   *   `relsByStart` insertion order. Empty if the LC has no outgoing `supports`
   *   edges.
   */
  function getSupportRelationshipsForLearningComponent(
    learningComponentNodeId: string,
  ): GraphRelationship[] {
    return (relsByStart.get(learningComponentNodeId) || []).filter(
      (rel) => rel.type === "supports",
    );
  }

  /**
   * Return the sorted, deduplicated set of grade-level strings present across
   * all SFIs in the loaded KG.
   *
   * Iterates every SFI, reads `properties.grade_level` (which the schema
   * declares as `string[]`), and accumulates each entry into a set. Nodes with
   * no `grade_level` field, or whose `grade_level` is not an array, contribute
   * nothing — they're silently skipped via the `Array.isArray` guard. Strings
   * are added verbatim with no whitespace/case normalization, so `"CE1"` and `"
   * CE1 "` would be distinct entries (in practice the upstream data is
   * consistent).
   *
   * The result is sorted with the default `Array.sort` lexicographic comparator
   * — _not_ `localeCompare` — which keeps ASCII grade codes like `"CE1"`,
   * `"CE2"`, `"CM1"` ordered correctly but may produce surprising orderings if
   * the KG ever introduces non-ASCII grade-level strings.
   *
   * Used by `getFacetValues` to populate the `gradeLevels` filter list and by
   * the `overview` tool handler directly.
   *
   * @returns The unique grade-level strings across all SFIs, lexicographically
   *   sorted. Empty if no SFI has a `grade_level` array.
   */
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
    return [...grades].sort();
  }

  /**
   * Return the sorted, deduplicated set of academic subject names present
   * across all SFIs in the loaded KG.
   *
   * Iterates every SFI, reads `properties.academic_subject`, normalizes
   * embedded newlines to spaces (which the source data occasionally carries —
   * see `compactNode` and `buildHierarchyForSubject` for the same pattern),
   * trims, and accumulates into a set. Falsy values (missing, empty string) are
   * skipped via the `if (subj)` guard.
   *
   * **Normalization is intentionally lighter than `uniqueSorted`'s** — internal
   * whitespace runs are not collapsed and casing is preserved. So `"Mathematics
   * "` and `"Mathematics"` collapse to one entry (post-trim), but
   * `"mathematics"` and `"Mathematics"` would remain distinct. In the current
   * dataset this distinction does not arise; if subject naming ever becomes
   * inconsistent, prefer running the result through `uniqueSorted` downstream
   * rather than changing this normalization in place (callers may rely on the
   * current case-preserving behavior).
   *
   * The result is sorted with the default `Array.sort` lexicographic comparator
   * (not `localeCompare`).
   *
   * Used by `getFacetValues` to populate the `subjects` filter list and by the
   * `overview` tool handler directly.
   *
   * @returns The unique academic-subject strings across all SFIs,
   *   newline-flattened and lexicographically sorted. Empty if no SFI has a
   *   non-empty `academic_subject` field.
   */
  function getUniqueSubjects(): string[] {
    const subjects = new Set<string>();

    for (const node of sfis) {
      const subj = node.properties.academic_subject;

      if (subj) {
        subjects.add(subj.replaceAll("\n", " ").trim());
      }
    }
    return [...subjects].sort();
  }

  /**
   * Build the provenance payload for a node — the bibliographic, source-
   * tracking, and inference-context fields that document _where this node came
   * from_ — in a single JSON-serializable object.
   *
   * Fields are drawn from three places and merged into a flat camelCase shape:
   *
   * - `node.properties` for the standard CASE-style attribution metadata
   *   (`attribution_statement`, `author`, `date_created`, `date_modified`,
   *   `license`, `provider`).
   * - `node.properties.metadata` for KG-specific provenance (`bbox`, `bbox_ref`,
   *   `canonical_path_key`, `llm_rationale`, `page_indices`,
   *   `progression_context`, `source_decision_ids`, `source_label`,
   *   `source_segment_ids`, `supporting_sfi_aux_statements`).
   * - The `metadata.provenance` sub-object as a fallback for `bbox`, `bbox_ref`,
   *   and `page_indices` — these fields appear under `metadata` directly on
   *   some nodes and nested under `metadata.provenance` on others, depending on
   *   the upstream extraction pipeline.
   *
   * Several fields fall back to `supporting_sfi_*` variants in metadata
   * (`canonicalPathKey`, `progressionContext`, `sourceLabel`) so the same shape
   * works for LCs (which inherit those fields from the SFI they support) and
   * SFIs (which carry them directly). The fallback ladder mirrors
   * `compactNode`'s.
   *
   * For `LearningComponent` nodes only, `supportingSfi` is populated via
   * `getStandardsSupportedByLearningComponent` and rendered through
   * `compactNode`, so the provenance response includes the standards the LC
   * traces back to. For SFIs and other kinds, `supportingSfi` is the empty
   * array.
   *
   * Used by the `get_provenance` tool handler.
   *
   * @param node - The graph node whose provenance to render. Any kind (SFI, LC,
   *   framework, unlabeled) — fields that don't apply come through as
   *   `undefined`.
   *
   * @returns A flat, camelCase, JSON-serializable provenance object. Always
   *   includes `target` (compact node) and `supportingSfi` (possibly empty);
   *   all other fields are `undefined` when absent on the source.
   */
  function provenanceForNode(node: GraphNode): Record<string, unknown> {
    const metadata = node.properties.metadata ?? {};
    const supportedStandards = node.labels.includes("LearningComponent")
      ? getStandardsSupportedByLearningComponent(node.id)
      : [];
    return {
      attributionStatement: node.properties.attribution_statement,
      author: node.properties.author,
      bbox: metadata.bbox ?? metadata.provenance?.bbox,
      bboxRef: metadata.bbox_ref ?? metadata.provenance?.bbox_ref,
      canonicalPathKey:
        metadata.canonical_path_key ??
        metadata.supporting_sfi_canonical_path_key,
      dateCreated: node.properties.date_created,
      dateModified: node.properties.date_modified,
      license: node.properties.license,
      llmRationale: metadata.llm_rationale,
      pageIndices: metadata.page_indices ?? metadata.provenance?.page_indices,
      progressionContext:
        metadata.progression_context ??
        metadata.supporting_sfi_progression_context,
      provider: node.properties.provider,
      sourceDecisionIds: metadata.source_decision_ids,
      sourceLabel:
        metadata.source_label ?? metadata.supporting_sfi_source_label,
      sourceSegmentIds: metadata.source_segment_ids,
      supportingSfi: supportedStandards.map((standard) =>
        compactNode(standard),
      ),
      supportingSfiAuxStatements: metadata.supporting_sfi_aux_statements,
      target: compactNode(node),
    };
  }

  /**
   * Substring-search across SFIs and Learning Components with optional facet
   * filters and a result cap.
   *
   * Builds the search candidate set from `nodeType`:
   *
   * - `"all"` (default) — both SFIs and LCs, in that group order.
   * - `"standard_item"` — SFIs only.
   * - `"learning_component"` — LCs only.
   *
   * Within the candidate set, each node is tested against the optional facet
   * filters (`subject`, `grade`, `statementType`, `sourceLabel`) via the
   * `nodeMatches*` predicates, then against the free-text `query` via
   * `getSearchText` substring matching. Filters are AND-ed: all provided facets
   * must match _and_ the query must be a substring of the node's search blob.
   * An omitted filter is vacuously satisfied. The query is normalized
   * (lowercase + trim + collapse whitespace) via `normalizeOptionalText` before
   * matching, so casual queries match the lowercased blob produced by
   * `getSearchText`.
   *
   * **Iteration order matters.** SFIs are scanned before LCs (when both are
   * candidates), and within each group nodes are scanned in their underlying
   * array order. This means results are biased toward SFIs and are not ranked —
   * first-match-wins until `limit` is reached. There is no relevance scoring;
   * if ranking ever becomes important, do it after collecting candidates rather
   * than altering this loop.
   *
   * The function early-returns as soon as `results.length >= limit`, so `limit`
   * is a hard cap (not "approximately"). The trailing `slice(0, limit)` is
   * defensive but the loop already enforces the same bound.
   *
   * Used by the `search_items` tool handler. Callers paginate by tightening
   * filters; there is no offset parameter.
   *
   * @param options - Search parameters.
   * @param options.grade - Optional grade-level filter forwarded to
   *   `nodeMatchesGrade`.
   * @param options.limit - Maximum number of results to return. Defaults to
   *   `20`. The schema caps this at `100` upstream.
   * @param options.nodeType - Which kinds of node to consider. Defaults to
   *   `"all"`.
   * @param options.query - Optional free-text query, substring-matched against
   *   the lowercased search blob. Defaults to `""` (no text filter, facet-only
   *   browse).
   * @param options.sourceLabel - Optional source-label filter forwarded to
   *   `nodeMatchesSourceLabel`.
   * @param options.statementType - Optional statement-type filter forwarded to
   *   `nodeMatchesStatementType`.
   * @param options.subject - Optional academic-subject filter forwarded to
   *   `nodeMatchesSubject`.
   *
   * @returns Up to `limit` matching nodes wrapped as `{ item, type }` pairs,
   *   where `type` is `"standard_item"` or `"learning_component"`. Empty if no
   *   node matches.
   */
  function searchItems(options: {
    grade?: string;
    limit?: number;
    nodeType?: SearchNodeType;
    query?: string;
    sourceLabel?: string;
    statementType?: string;
    subject?: string;
  }): Array<{ item: GraphNode; type: string }> {
    const q = normalizeOptionalText(options.query) ?? "";
    const nodeType = options.nodeType ?? "all";
    const limit = options.limit ?? 20;
    const results: Array<{ item: GraphNode; type: string }> = [];
    const candidateGroups: Array<{ nodes: GraphNode[]; type: string }> = [];

    if (nodeType === "all" || nodeType === "standard_item") {
      candidateGroups.push({ nodes: sfis, type: "standard_item" });
    }

    if (nodeType === "all" || nodeType === "learning_component") {
      candidateGroups.push({
        nodes: learningComponents,
        type: "learning_component",
      });
    }

    for (const group of candidateGroups) {
      for (const node of group.nodes) {
        if (results.length >= limit) return results;

        if (!nodeMatchesSubject(node, options.subject)) continue;

        if (!nodeMatchesGrade(node, options.grade)) continue;

        if (!nodeMatchesStatementType(node, options.statementType)) continue;

        if (!nodeMatchesSourceLabel(node, options.sourceLabel)) continue;

        if (q && !getSearchText(node).includes(q)) continue;

        results.push({ item: node, type: group.type });
      }
    }

    return results.slice(0, limit);
  }

  return {
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
  };
}

/**
 * Build the lowercase, whitespace-flattened search blob used by `searchItems`
 * for substring matching against a node.
 *
 * Concatenates a fixed set of searchable fields drawn from `node.properties`
 * and `node.properties.metadata` — IDs, names, descriptions, statement
 * codes/types, subject, source labels, normalized and split text, and the LLM
 * rationale that produced the node — joins them with spaces, replaces newlines
 * with spaces (which the source data occasionally embeds in `description` and
 * similar long fields), and lowercases the result. Non-string values are
 * filtered out before the join, so a missing or non-string field contributes
 * nothing rather than the literal string `"undefined"`.
 *
 * The function returns a single flat string rather than a structured
 * tokenization. Callers (currently only `searchItems`) treat it as
 * substring-matchable: a query like `"pluriel"` will match any field containing
 * that substring. This is good enough for fuzzy curriculum search but does no
 * stemming, transliteration, or accent folding — so `"specifique"` will not
 * match `"spécifique"`. The query side is normalized through the same
 * `normalizeOptionalText` (lowercase + trim + collapse whitespace) so
 * accidental spacing/casing mismatches don't matter.
 *
 * **Field selection is an allow-list, not a reflection of the whole node.** New
 * properties added to nodes are not searchable until added here. This is
 * intentional: it keeps the search blob bounded and predictable.
 *
 * @param node - The graph node to render. Any kind (SFI, LC, framework,
 *   unlabeled) — fields that don't apply simply contribute nothing.
 *
 * @returns A single lowercase, whitespace-flattened string suitable for
 *   `.includes()` matching against a normalized query. Empty string if none of
 *   the allow-listed fields are present as strings on the node.
 */
function getSearchText(node: GraphNode): string {
  const metadata = node.properties.metadata ?? {};
  const fields = [
    node.id,
    node.properties.identifier,
    node.properties.description,
    node.properties.name,
    node.properties.statement_code,
    node.properties.statement_type,
    node.properties.normalized_statement_type,
    node.properties.academic_subject,
    metadata.source_label,
    metadata.normalized_text,
    metadata.canonical_path_key,
    metadata.supporting_sfi_source_label,
    metadata.supporting_sfi_statement_type,
    metadata.supporting_sfi_canonical_path_key,
    metadata.split_display_text,
    metadata.split_id_text,
    metadata.llm_rationale,
  ];
  return fields
    .filter((value): value is string => typeof value === "string")
    .join(" ")
    .replaceAll("\n", " ")
    .toLowerCase();
}

/**
 * Load and validate a Knowledge Graph JSON file from the bundled examples
 * directory.
 *
 * Resolves the file path relative to `runtimeDir` (expected to be the compiled
 * source directory) via `../../examples/kgs/<kgFn>`. Loads in three stages
 * (file read, JSON parse, schema validation) each of which surfaces a distinct,
 * kgFp-tagged error message so connector setup failures are easy to diagnose
 * from Claude Desktop's log output. Schema validation is enforced via
 * `KnowledgeGraphSchema` and checks that every node has `id`, `labels`, and
 * `properties.identifier`, and every relationship has `id`, `start`, `end`, and
 * `type`. Extra fields are passed through. Logs a summary of loaded
 * node/relationship counts to stderr.
 *
 * @param kgFn - Filename of the KG JSON file (e.g. "senegal_reading.json").
 * @param runtimeDir - Directory of the calling module, typically
 *   `dirname(fileURLToPath(import.meta.url))`.
 *
 * @returns The parsed and validated `KnowledgeGraph` object.
 *
 * @throws {Error} If the file is not found, unreadable, malformed JSON, or
 *   fails schema validation. All thrown errors include the resolved filepath in
 *   the message.
 */
export function loadKnowledgeGraph(
  kgFn: string,
  runtimeDir: string,
): KnowledgeGraph {
  const kgFp = path.join(runtimeDir, "..", "..", "examples", "kgs", kgFn);

  console.error("Resolved KG filepath:", kgFp);

  let rawData: string;

  try {
    rawData = readFileSync(kgFp, "utf8");
  } catch (error: unknown) {
    if (
      typeof error === "object" &&
      error !== null &&
      "code" in error &&
      error.code === "ENOENT"
    ) {
      throw new Error(
        `Failed to load knowledge graph: file not found at ${kgFp}`,
      );
    }

    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`Failed to read knowledge graph at ${kgFp}: ${message}`);
  }

  let parsed: unknown;

  try {
    parsed = JSON.parse(rawData);
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(
      `Failed to parse knowledge graph JSON at ${kgFp}: ${message}`,
    );
  }

  const validation = KnowledgeGraphSchema.safeParse(parsed);

  if (!validation.success) {
    const issues = validation.error.issues
      .map(
        (issue) => `  - ${issue.path.join(".") || "<root>"}: ${issue.message}`,
      )
      .join("\n");
    throw new Error(
      `Failed to validate knowledge graph at ${kgFp}:\n${issues}`,
    );
  }

  const kg = validation.data as unknown as KnowledgeGraph;
  const sfCount = kg.nodes.filter((n) =>
    n.labels.includes("StandardsFramework"),
  ).length;
  const sfiCount = kg.nodes.filter((n) =>
    n.labels.includes("StandardsFrameworkItem"),
  ).length;
  const lcCount = kg.nodes.filter((n) =>
    n.labels.includes("LearningComponent"),
  ).length;
  const relTypeCounts: Record<string, number> = {};

  for (const rel of kg.relationships) {
    relTypeCounts[rel.type] = (relTypeCounts[rel.type] ?? 0) + 1;
  }

  console.error(`Loaded KG from ${kgFp}:
  - ${sfCount} Standards Framework(s)
  - ${sfiCount} Standards Framework Items
  - ${lcCount} Learning Components
  - ${kg.relationships.length} Total Relationships
  - Relationship types: ${JSON.stringify(relTypeCounts)}`);

  return kg;
}

/**
 * Predicate used by `searchItems` to test whether a node matches an optional
 * `grade` filter.
 *
 * The filter is **vacuously true** when `grade` is omitted or empty, so
 * callsites can pass the user-supplied filter through unconditionally. A
 * provided filter is compared via `normalizeOptionalText` (lowercase, trim,
 * collapse whitespace) against each entry of the node's grade-level array.
 *
 * For SFIs the grade array is read from `properties.grade_level`. For LCs the
 * `grade_level` field is absent, so the function falls back to
 * `metadata.supporting_sfi_grade_level` — the grade levels of the SFI the LC
 * supports. This lets a single grade filter scope both SFIs and LCs without the
 * caller branching on node kind.
 *
 * Equality is whole-string after normalization, not substring — so a filter of
 * `"CE1"` matches the entry `"CE1"` but not `"CE1-CE2"`. If the grade-level
 * field is not an array (missing, malformed, or a scalar), the predicate
 * returns `false` rather than coercing.
 *
 * @param node - The graph node to test.
 * @param grade - Optional grade-level filter (e.g. `"CE1"`). When omitted or
 *   empty, the predicate is vacuously true.
 *
 * @returns `true` if `grade` is omitted/empty, or if any entry in the node's
 *   resolved grade-level array equals `grade` after normalization. `false`
 *   otherwise.
 */
function nodeMatchesGrade(node: GraphNode, grade?: string): boolean {
  if (!grade) return true;

  const expected = normalizeOptionalText(grade);
  const gradeLevels =
    node.properties.grade_level ??
    node.properties.metadata?.supporting_sfi_grade_level;
  return Array.isArray(gradeLevels)
    ? gradeLevels.some((g) => normalizeOptionalText(String(g)) === expected)
    : false;
}

/**
 * Predicate used by `searchItems` to test whether a node matches an optional
 * `sourceLabel` filter.
 *
 * The filter is **vacuously true** when `sourceLabel` is omitted or empty. A
 * provided filter is compared via `normalizeOptionalText` (lowercase, trim,
 * collapse whitespace) against two candidate fields on the node's metadata:
 *
 * - `metadata.source_label` — populated on SFIs.
 * - `metadata.supporting_sfi_source_label` — populated on LCs and carries the
 *   source label of the SFI the LC supports.
 *
 * The OR over both candidates lets a single filter value scope both SFIs and
 * LCs without the caller branching on node kind, mirroring the fallback pattern
 * used in `compactNode` and `nodeMatchesStatementType`.
 *
 * @param node - The graph node to test.
 * @param sourceLabel - Optional source-label filter (e.g. `"Conjugaison"`,
 *   `"Orthographe"`). When omitted or empty, the predicate is vacuously true.
 *
 * @returns `true` if `sourceLabel` is omitted/empty, or if either of the
 *   candidate fields equals it after normalization. `false` otherwise.
 */
function nodeMatchesSourceLabel(
  node: GraphNode,
  sourceLabel?: string,
): boolean {
  if (!sourceLabel) return true;

  const expected = normalizeOptionalText(sourceLabel);
  const candidates = [
    node.properties.metadata?.source_label,
    node.properties.metadata?.supporting_sfi_source_label,
  ];
  return candidates.some(
    (candidate) => normalizeOptionalText(candidate) === expected,
  );
}

/**
 * Predicate used by `searchItems` to test whether a node matches an optional
 * `statementType` filter.
 *
 * The filter is **vacuously true** when `statementType` is omitted or empty. A
 * provided filter is compared via `normalizeOptionalText` (lowercase, trim,
 * collapse whitespace) against four candidate fields:
 *
 * - `properties.statement_type` — the raw statement type on SFIs (e.g. `"Objectif
 *   spécifique"`).
 * - `properties.normalized_statement_type` — the canonicalized form on SFIs (one
 *   of `"Standard"`, `"Standard Grouping"`, `"Other"`, or an open string).
 * - `metadata.supporting_sfi_statement_type` — for LCs, the raw statement type of
 *   the SFI the LC supports.
 * - `metadata.supporting_sfi_normalized_statement_type` — for LCs, the normalized
 *   form of the same.
 *
 * The OR over all four lets a single filter value scope SFIs and LCs and accept
 * either the raw or normalized form, so callers can pass through whatever the
 * user typed without rewriting it. This is the broadest fallback ladder of the
 * `nodeMatches*` predicates; treat its accept-rate as correspondingly
 * forgiving.
 *
 * @param node - The graph node to test.
 * @param statementType - Optional statement-type filter (e.g. `"Standard"`,
 *   `"Objectif spécifique"`). When omitted or empty, the predicate is vacuously
 *   true.
 *
 * @returns `true` if `statementType` is omitted/empty, or if any of the four
 *   candidate fields equals it after normalization. `false` otherwise.
 */
function nodeMatchesStatementType(
  node: GraphNode,
  statementType?: string,
): boolean {
  if (!statementType) return true;

  const expected = normalizeOptionalText(statementType);
  const candidates = [
    node.properties.statement_type,
    node.properties.normalized_statement_type,
    node.properties.metadata?.supporting_sfi_statement_type,
    node.properties.metadata?.supporting_sfi_normalized_statement_type,
  ];
  return candidates.some(
    (candidate) => normalizeOptionalText(candidate) === expected,
  );
}

/**
 * Predicate used by `searchItems` to test whether a node matches an optional
 * `subject` filter.
 *
 * The filter is **vacuously true** when `subject` is omitted or empty. A
 * provided filter is compared via `normalizeOptionalText` (lowercase, trim,
 * collapse whitespace) against `properties.academic_subject`, which is first
 * newline-flattened (the source data occasionally embeds newlines in subject
 * strings — see `compactNode` and `buildHierarchyForSubject` for the same
 * pattern).
 *
 * Equality is whole-string after normalization, not substring — so a filter of
 * `"Mathematics"` matches the value `"Mathematics"` but not `"Mathematics and
 * Statistics"`.
 *
 * Unlike `nodeMatchesStatementType` and `nodeMatchesSourceLabel`, this
 * predicate consults a single field. There is no LC-side fallback: LCs carry
 * their subject implicitly through their supported SFI, so a subject filter on
 * a result set that includes both SFIs and LCs will effectively only match
 * SFIs. If the LC subject filter ever becomes required, mirror the
 * `metadata.supporting_sfi_*` fallback pattern.
 *
 * @param node - The graph node to test.
 * @param subject - Optional academic-subject filter (e.g. `"Mathematics"`,
 *   `"Langue et Communication"`). When omitted or empty, the predicate is
 *   vacuously true.
 *
 * @returns `true` if `subject` is omitted/empty, or if the node's
 *   newline-flattened `academic_subject` equals it after normalization. `false`
 *   otherwise.
 */
function nodeMatchesSubject(node: GraphNode, subject?: string): boolean {
  if (!subject) return true;

  const expected = normalizeOptionalText(subject);
  const actual = normalizeOptionalText(
    node.properties.academic_subject?.replaceAll("\n", " "),
  );
  return actual === expected;
}

/**
 * Map a node's `labels` array to the canonical `nodeType` discriminant used by
 * `compactNode` and other compact-shape consumers.
 *
 * Priority is `StandardsFramework` > `StandardsFrameworkItem` >
 * `LearningComponent` > `"unknown"`. In practice the priority is not
 * observable, because `buildKnowledgeGraphIndexes` throws on any node carrying
 * more than one of those labels — but if that invariant is ever relaxed, this
 * function will silently pick the first-listed kind rather than reporting the
 * conflict.
 *
 * Extracted out of `compactNode` to avoid a deeply nested ternary expression.
 *
 * @param labels - The `labels` array from a `GraphNode`.
 *
 * @returns One of `"framework"`, `"standard_item"`, `"learning_component"`, or
 *   `"unknown"`.
 */
function nodeTypeFromLabels(labels: string[]): string {
  if (labels.includes("StandardsFramework")) return "framework";

  if (labels.includes("StandardsFrameworkItem")) return "standard_item";

  if (labels.includes("LearningComponent")) return "learning_component";

  return "unknown";
}

/**
 * Normalize a string for case-insensitive comparison and bucketing by trimming,
 * collapsing internal whitespace, and lowercasing. Returns `undefined` for
 * nullish or whitespace-only inputs to simplify optional field handling in
 * filters.
 *
 * @param value - The string to normalize, or null/undefined.
 *
 * @returns The normalized string, or `undefined` if the input was nullish or
 *   whitespace-only.
 */
function normalizeOptionalText(
  value: string | null | undefined,
): string | undefined {
  return value ? normalizeWhitespace(value).toLowerCase() : undefined;
}

/**
 * Normalize a string by trimming and collapsing internal whitespace to a single
 * space.
 *
 * @param value - The string to normalize.
 *
 * @returns The normalized string with trimmed and collapsed whitespace.
 */
function normalizeWhitespace(value: string): string {
  return value.replaceAll(/\s+/g, " ").trim();
}

/**
 * Wrap a tool failure into the MCP `CallToolResult` envelope expected by Claude
 * Desktop.
 *
 * Builds the dual representation the MCP protocol requires: the
 * JSON-pretty-printed text placed in `content[0].text` is what the LLM
 * literally reads as the tool's reply, while the same payload as a structured
 * object is placed in `structuredContent` for SDK clients that consume tool
 * output programmatically. Both views are kept in sync because they are built
 * from the same `{error: message, ...details}` object. The `isError: true` flag
 * is the protocol's signal that this call failed, distinct from a successful
 * call whose payload happens to describe a problem.
 *
 * Used by every tool handler in `index.ts` for explicit failure paths (item not
 * found, unknown tool name, invalid arguments) and by the catch-all `try/catch`
 * wrapping the `CallTool` switch, which funnels any thrown exception into a
 * properly-shaped error response so the JSON-RPC response cycle is never
 * broken.
 *
 * NB: Because the error payload is built as `{error: message, ...details}`, a
 * `details` object that itself contains an `error` key would shadow the
 * message. None of the current call sites do this; just be aware if you start
 * passing richer error context.
 *
 * @example
 *   // Minimal error — just a message.
 *   toolError("Item 'xyz' not found.");
 *   // -> {
 *   //     content: [{type: "text", text: '{\n  "error": "Item \'xyz\' not found."\n}'}],
 *   //     structuredContent: {error: "Item 'xyz' not found."},
 *   //     isError: true,
 *   //   }
 *
 * @example
 *   // Error with diagnostic details for the model to act on.
 *   toolError("Item 'xyz' not found.", {
 *     hint: "Use search_items or list_facets.",
 *   });
 *   // -> {
 *   //     content: [{type: "text", text: '{\n  "error": "Item \'xyz\' not found.",\n  "hint": "Use search_items or list_facets."\n}'}],
 *   //     structuredContent: {error: "Item 'xyz' not found.", hint: "Use search_items or list_facets."},
 *   //     isError: true,
 *   //   }
 *
 * @param message - Human-readable error text. Becomes the `error` field of the
 *   payload and is what the model sees first when narrating the failure.
 * @param details - Optional supplementary fields merged into the payload
 *   alongside `error`. Use for hints, valid alternatives, attempted
 *   identifiers, etc.
 *
 * @returns A `CallToolResult` with `isError: true`, the error payload
 *   pretty-printed into `content[0].text`, and the same payload as
 *   `structuredContent`.
 */
export function toolError(
  message: string,
  details?: Record<string, unknown>,
): CallToolResult {
  return {
    content: [
      {
        text: JSON.stringify({ error: message, ...details }, null, 2),
        type: "text",
      },
    ],
    isError: true,
    structuredContent: { error: message, ...details },
  };
}

/**
 * Wrap a successful tool payload into the MCP `CallToolResult` envelope
 * expected by Claude Desktop.
 *
 * Builds the dual representation the MCP protocol requires: the
 * JSON-pretty-printed text placed in `content[0].text` is what the LLM
 * literally reads as the tool's reply, while the same `data` object is placed
 * in `structuredContent` for SDK clients that consume tool output
 * programmatically. Both views are guaranteed in sync because they derive from
 * the same input. No `isError` flag is set, signalling a successful call.
 *
 * Used by every tool handler in `index.ts` to return its payload: node lookups,
 * navigation results, search hits, facet values, progressions, etc.
 *
 * @example
 *   // Single-item lookup result.
 *   toolResult({ type: "standard_item", id: "abc-123" });
 *   // -> {
 *   //     content: [{type: "text", text: '{\n  "type": "standard_item",\n  "id": "abc-123"\n}'}],
 *   //     structuredContent: {type: "standard_item", id: "abc-123"},
 *   //   }
 *
 * @example
 *   // List-of-items result; the same array is stringified into content and exposed as
 *   // structuredContent.
 *   toolResult({ results: [{ id: "a" }, { id: "b" }], total: 2 });
 *   // -> {
 *   //     content: [{type: "text", text: '{\n  "results": [\n    {\n      "id": "a"\n    },\n    {\n      "id": "b"\n    }\n  ],\n  "total": 2\n}'}],
 *   //     structuredContent: {results: [{id: "a"}, {id: "b"}], total: 2},
 *   //   }
 *
 * @param data - The tool's payload. Whatever shape the tool wants to surface to
 *   the caller; serialized with `JSON.stringify(_, null, 2)` for the text view
 *   and passed through unchanged for the structured view. Must be
 *   JSON-serializable.
 *
 * @returns A `CallToolResult` with the data pretty-printed into
 *   `content[0].text` and the same data as `structuredContent`. `isError` is
 *   omitted.
 */
export function toolResult(data: Record<string, unknown>): CallToolResult {
  return {
    content: [
      {
        text: JSON.stringify(data, null, 2),
        type: "text",
      },
    ],
    structuredContent: data,
  };
}

/**
 * Return the unique, alphabetically sorted set of meaningful string values from
 * an array.
 *
 * Each value is processed before deduplication:
 *
 * - `null`, `undefined`, empty strings, and whitespace-only strings are
 *   **dropped**.
 * - All other strings are passed through `normalizeWhitespace` (internal runs of
 *   whitespace collapsed to a single space, ends trimmed) before deduplication.
 *   So " Objectif spécifique " and "Objectif spécifique" collapse to one
 *   entry.
 *
 * Deduplication is case-sensitive: "Foo" and "foo" remain distinct entries. The
 * returned array is sorted alphabetically via `localeCompare`.
 *
 * Used by `createKnowledgeGraphUtils`'s `getFacetValues` helper to build the
 * lists of valid filter values (`relationshipTypes`, `statementTypes`,
 * `sourceLabels`, etc.) surfaced through the MCP `list_facets`/`overview` tool
 * responses.
 *
 * @example
 *   // Basic dedupe and sort.
 *   uniqueSorted(["hasChild", "supports", "hasChild", "buildsTowards"]);
 *   // -> ["buildsTowards", "hasChild", "supports"]
 *
 * @example
 *   // null, undefined, "", and whitespace-only strings are dropped (NOT bucketed).
 *   uniqueSorted([
 *     "Conjugaison",
 *     null,
 *     "Conjugaison",
 *     undefined,
 *     "",
 *     "   ",
 *     "Orthographe",
 *   ]);
 *   // -> ["Conjugaison", "Orthographe"]
 *
 * @example
 *   // Internal whitespace is collapsed and ends are trimmed before deduplication.
 *   uniqueSorted([
 *     "  Objectif  spécifique ",
 *     "Objectif spécifique",
 *     "Vocabulaire",
 *   ]);
 *   // -> ["Objectif spécifique", "Vocabulaire"]
 *
 * @example
 *   // Deduplication is case-sensitive.
 *   uniqueSorted(["Foo", "foo", "Foo"]);
 *   // -> ["foo", "Foo"]
 *
 * @example
 *   // Returns an empty array if no meaningful values are present.
 *   uniqueSorted([null, undefined, "", "   "]);
 *   // -> []
 *
 * @param values - Array of strings to dedupe and sort. `null` and `undefined`
 *   entries are allowed and silently dropped, alongside empty/whitespace-only
 *   strings.
 *
 * @returns A new array containing each meaningful, whitespace-normalized value
 *   exactly once, sorted alphabetically.
 */
function uniqueSorted(values: Array<string | null | undefined>): string[] {
  return [
    ...new Set(
      values
        .filter(
          (v): v is string => typeof v === "string" && v.trim().length > 0,
        )
        .map((v) => normalizeWhitespace(v)),
    ),
  ].sort((a, b) => a.localeCompare(b));
}
