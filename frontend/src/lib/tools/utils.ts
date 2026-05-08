/** @file - This file contains tool utilities. */

// Third Party Library
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";

// Package Library
import {
  compactNode,
  nodeMatchesGrade,
  nodeMatchesPathSegment,
  nodeMatchesSourceLabel,
  nodeMatchesStatementType,
  nodeMatchesSubject,
} from "@/lib/kgs/utils.js";
import {
  BrowseSubjectSchema,
  GetAuxStatementsSchema,
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
  type ProgressionDirection,
  SearchItemsSchema,
  type SearchNodeType,
} from "@/lib/schemas.js";
import { KNOWLEDGE_GRAPH_TOOL_DEFINITIONS } from "@/lib/tools/constants.js";
import { countBy, normalizeOptionalText, uniqueSorted } from "@/lib/utils.js";

/**
 * Build the full set of filterable facet values and aggregate counts for a
 * loaded Knowledge Graph.
 *
 * This function is intentionally module-level so that
 * `createKnowledgeGraphUtils` can compute the facet payload once during factory
 * setup, then return the cached object from `getFacetValues` on every MCP tool
 * call. The graph is read-only after loading, so recomputing these global
 * counts and unique value lists per request is unnecessary work.
 *
 * `gradeLevels` and `subjects` are injected rather than recomputed here because
 * those same precomputed arrays are also exposed through `getUniqueGradeLevels`
 * and `getUniqueSubjects`. Keeping them as named arguments guarantees all three
 * utility methods agree on the exact same cached values.
 *
 * @param root0 - Named arguments used to build the facet payload.
 * @param root0.frameworks - Standards framework nodes from the KG indexes.
 * @param root0.gradeLevels - Precomputed sorted grade-level values.
 * @param root0.kg - Parsed and validated Knowledge Graph object.
 * @param root0.learningComponents - LearningComponent nodes from the KG
 *   indexes.
 * @param root0.sfis - StandardsFrameworkItem nodes from the KG indexes.
 * @param root0.subjects - Precomputed sorted academic-subject values.
 *
 * @returns A JSON-serializable object with facet values and aggregate counts
 *   for the loaded KG.
 */
export function buildFacetValues({
  frameworks,
  gradeLevels,
  kg,
  learningComponents,
  sfis,
  subjects,
}: {
  frameworks: GraphNode[];
  gradeLevels: string[];
  kg: KnowledgeGraph;
  learningComponents: GraphNode[];
  sfis: GraphNode[];
  subjects: string[];
}): Record<string, unknown> {
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
    gradeLevels,
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
    subjects,
  };
}

/**
 * Precompute lowercase search text for a set of graph nodes.
 *
 * `searchItems` uses substring matching against the same allow-listed node
 * fields on every call. Building this map once during
 * `createKnowledgeGraphUtils` setup avoids rebuilding the concatenated search
 * blob for every candidate node on every search request. The returned map is
 * keyed by graph node ID and holds exactly the same strings that
 * `getSearchText` would have generated inline.
 *
 * @param root0 - Named arguments used to build the search-text index.
 * @param root0.nodes - Graph nodes that should participate in text search.
 *
 * @returns A map from graph node ID to precomputed lowercase search text.
 */
export function buildSearchTextByNodeId({
  nodes,
}: {
  nodes: GraphNode[];
}): Map<string, string> {
  const searchTextByNodeId = new Map<string, string>();

  for (const node of nodes) {
    searchTextByNodeId.set(node.id, getSearchText(node));
  }

  return searchTextByNodeId;
}

/**
 * Build the sorted, deduplicated grade-level list for a set of SFIs.
 *
 * This is computed once during `createKnowledgeGraphUtils` setup and reused by
 * both `getUniqueGradeLevels` and the cached facet payload. Only array-valued
 * `properties.grade_level` fields contribute values; missing or malformed grade
 * fields are ignored.
 *
 * @param root0 - Named arguments used to build the grade-level list.
 * @param root0.sfis - StandardsFrameworkItem nodes to scan for `grade_level`
 *   arrays.
 *
 * @returns The unique grade-level strings across all SFIs, lexicographically
 *   sorted. Empty if no SFI has a `grade_level` array.
 */
export function buildUniqueGradeLevels({
  sfis,
}: {
  sfis: GraphNode[];
}): string[] {
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
 * Build the sorted, deduplicated academic-subject list for a set of SFIs.
 *
 * This is computed once during `createKnowledgeGraphUtils` setup and reused by
 * both `getUniqueSubjects` and the cached facet payload. Embedded newlines are
 * flattened to spaces and leading/trailing whitespace is trimmed before values
 * are inserted into the set.
 *
 * @param root0 - Named arguments used to build the academic-subject list.
 * @param root0.sfis - StandardsFrameworkItem nodes to scan for
 *   `academic_subject` strings.
 *
 * @returns The unique academic-subject strings across all SFIs,
 *   newline-flattened and lexicographically sorted. Empty if no SFI has a
 *   non-empty `academic_subject` field.
 */
export function buildUniqueSubjects({ sfis }: { sfis: GraphNode[] }): string[] {
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

  const uniqueGradeLevels = buildUniqueGradeLevels({ sfis });
  const uniqueSubjects = buildUniqueSubjects({ sfis });
  const facetValues = buildFacetValues({
    frameworks,
    gradeLevels: uniqueGradeLevels,
    kg,
    learningComponents,
    sfis,
    subjects: uniqueSubjects,
  });
  const searchTextByNodeId = buildSearchTextByNodeId({
    nodes: [...sfis, ...learningComponents],
  });

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
   * loaded Knowledge Graph from the factory-level cache.
   *
   * The returned object is precomputed when `createKnowledgeGraphUtils` is
   * called and has two halves:
   *
   * - **Facet values** (`gradeLevels`, `learningComponentSourceLabels`,
   *   `nodeTypes`, `normalizedStatementTypes`, `relationshipTypes`,
   *   `sourceLabels`, `statementTypes`, `subjects`) — the unique, sorted lists
   *   of values that callers can pass back as filters to `searchItems` and
   *   related tools. Relationship, statement, source-label,
   *   normalized-statement, and LearningComponent source-label lists go through
   *   `uniqueSorted`, so empty, whitespace-only, and nullish entries are
   *   dropped (not bucketed). Grade levels and subjects come from their own
   *   cached builders, and `nodeTypes` is a fixed enumeration.
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
    return facetValues;
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
   * Returns the factory-level cached result built by `buildUniqueGradeLevels`,
   * which reads `properties.grade_level` (declared as `string[]`) and
   * accumulates each entry into a set. Nodes with no `grade_level` field, or
   * whose `grade_level` is not an array, contribute nothing — they're silently
   * skipped via the `Array.isArray` guard. Strings are added verbatim with no
   * whitespace/case normalization, so `"CE1"` and `" CE1 "` would be distinct
   * entries (in practice the upstream data is consistent).
   *
   * The result is sorted with the default `Array.sort` lexicographic comparator
   * — _not_ `localeCompare` — which keeps ASCII grade codes like `"CE1"`,
   * `"CE2"`, `"CM1"` ordered correctly but may produce surprising orderings if
   * the KG ever introduces non-ASCII grade-level strings.
   *
   * Used by `getFacetValues` to populate the `gradeLevels` filter list and by
   * the `overview` tool handler directly.
   *
   * @returns The cached unique grade-level strings across all SFIs,
   *   lexicographically sorted. Empty if no SFI has a `grade_level` array.
   */
  function getUniqueGradeLevels(): string[] {
    return uniqueGradeLevels;
  }

  /**
   * Return the sorted, deduplicated set of academic subject names present
   * across all SFIs in the loaded KG.
   *
   * Returns the factory-level cached result built by `buildUniqueSubjects`,
   * which reads `properties.academic_subject`, normalizes embedded newlines to
   * spaces (which the source data occasionally carries — see `compactNode` and
   * `buildHierarchyForSubject` for the same pattern), trims, and accumulates
   * into a set. Falsy values (missing, empty string) are skipped via the `if
   * (subj)` guard.
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
   * @returns The cached unique academic-subject strings across all SFIs,
   *   newline-flattened and lexicographically sorted. Empty if no SFI has a
   *   non-empty `academic_subject` field.
   */
  function getUniqueSubjects(): string[] {
    return uniqueSubjects;
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
   *   `page_indices`, `source_decision_ids`, and `source_segment_ids` — these
   *   fields appear under `metadata` directly on some nodes and nested under
   *   `metadata.provenance` on others, depending on the upstream extraction
   *   pipeline.
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
      sourceDecisionIds:
        metadata.source_decision_ids ??
        metadata.provenance?.source_decision_ids,
      sourceLabel:
        metadata.source_label ?? metadata.supporting_sfi_source_label,
      sourceSegmentIds:
        metadata.source_segment_ids ?? metadata.provenance?.source_segment_ids,
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
   * filters (`subject`, `grade`, `statementType`, `sourceLabel`, `pathSegment`)
   * via the `nodeMatches*` predicates, then against the free-text `query` via
   * the precomputed `searchTextByNodeId` substring index. Filters are AND-ed:
   * all provided facets must match **and** the query must be a substring of the
   * node's cached search blob. An omitted filter is satisfied. The query is
   * normalized (lowercase + trim + collapse whitespace) via
   * `normalizeOptionalText` before matching, so casual queries match the
   * lowercased blob produced by `getSearchText` during factory setup.
   *
   * `pathSegment` is matched as a complete `/`-delimited segment of the node's
   * `canonical_path_key`, not as a substring; see `nodeMatchesPathSegment` for
   * the framework-agnostic semantics. Useful for scoping to a specific week,
   * unit, lesson, palier, etc., without committing to any one curriculum's
   * vocabulary.
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
   * @param options.pathSegment - Optional canonical-path segment filter
   *   forwarded to `nodeMatchesPathSegment`.
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
    pathSegment?: string;
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

        if (!nodeMatchesPathSegment(node, options.pathSegment)) continue;

        if (q && !(searchTextByNodeId.get(node.id) ?? "").includes(q)) continue;

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
 * and `node.properties.metadata` (IDs, names, descriptions, statement
 * codes/types, subject, source labels, normalized and split text, and the LLM
 * rationale that produced the node) and joins them with spaces, replaces
 * newlines with spaces (which the source data occasionally embeds in
 * `description` and similar long fields), and lowercases the result. Non-string
 * values are filtered out before the join, so a missing or non-string field
 * contributes nothing rather than the literal string `"undefined"`.
 *
 * The function returns a single flat string rather than a structured
 * tokenization. Callers (currently only `searchItems`) treat it as
 * substring-matchable: a query like "pluriel" will match any field containing
 * that substring. This is good enough for fuzzy curriculum search but does no
 * stemming, transliteration, or accent folding so "specifique" will not match
 * "spécifique". The query side is normalized through the same
 * `normalizeOptionalText` (lowercase + trim + collapse whitespace) so
 * accidental spacing/casing mismatches don't matter.
 *
 * **Field selection is an allow-list, not a reflection of the whole node.** New
 * properties added to nodes are not searchable until added here. This is
 * intentional: it keeps the search blob bounded and predictable.
 *
 * @param node - The graph node to render. Any kind (SFI, LC, framework,
 *   unlabeled). Fields that don't apply simply contribute nothing.
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
 * Return static registration metadata for one named MCP tool.
 *
 * @param root0 - Named arguments used to look up the tool definition.
 * @param root0.name - Programmatic name of the tool, which must match the
 *   `name` field of one of the `KnowledgeGraphToolDefinition` entries in the
 *   static catalogue.
 *
 * @returns The precomputed definition for the requested tool.
 *
 * @throws {Error} If the tool name is not in the static catalogue.
 */
function getToolDefinition({
  name,
}: {
  name: string;
}): KnowledgeGraphToolDefinition {
  const definition = KNOWLEDGE_GRAPH_TOOL_DEFINITIONS.find(
    (toolDefinition) => toolDefinition.name === name,
  );

  if (!definition) {
    throw new Error(`Missing static MCP tool definition: ${name}`);
  }

  return definition;
}

/**
 * Register every read-only Knowledge Graph MCP tool.
 *
 * `McpServer` owns tools/list and tools/call after each tool is registered with
 * `registerTool`.
 *
 * @param root0 - Named arguments for registering the tools.
 * @param root0.indexes - Precomputed KG indexes for efficient lookup (nodes by
 *   ID, relationships by start/end, SFIs/LCs by identifier).
 * @param root0.kg - Parsed and validated Knowledge Graph object, which is
 *   read-only after loading.
 * @param root0.kgUtils - Utility functions that wrap common KG operations like
 *   traversals and lookups. These are built with closures that capture the KG
 *   and indexes, so the MCP tool handlers can call them without needing to pass
 *   the KG or indexes directly.
 * @param root0.server - The McpServer instance against which the tools should
 *   be registered.
 */
export function registerKnowledgeGraphTools({
  indexes,
  kg,
  kgUtils,
  server,
}: {
  indexes: KnowledgeGraphIndexes;
  kg: KnowledgeGraph;
  kgUtils: ReturnType<typeof createKnowledgeGraphUtils>;
  server: McpServer;
}): void {
  const { frameworks, learningComponents, sfis } = indexes;
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

  registerReadOnlyTool({
    /**
     * MCP entry point for the `browse_subject` tool: build a tree-shaped view
     * of standards filed under a single academic subject, optionally scoped to
     * a grade band.
     *
     * Delegates to `buildHierarchyForSubject` after schema validation. If the
     * subject does not match any SFI in the loaded KG, the response is still a
     * normal (non-error) result that surfaces the list of `availableSubjects`
     * alongside an `error` field, so the caller has an immediate recovery hint
     * without a separate facet round-trip.
     *
     * @param args - Raw tool arguments forwarded by `McpServer`. Validated
     *   against `BrowseSubjectSchema`: a `subject` name (case-insensitive,
     *   newline-tolerant) and an optional `grade` substring applied to
     *   descendants only.
     *
     * @returns A `CallToolResult` with the subject hierarchy, the applied grade
     *   filter, the subject name, and the count of top-level items; or a result
     *   describing the missing subject and listing available alternatives.
     */
    handler: (args) =>
      runToolHandler({
        /**
         * Parse `args` with `BrowseSubjectSchema` and resolve them through
         * `buildHierarchyForSubject`. The "subject not found" branch returns
         * `toolResult` with an `error` key (not `toolError`) so the response
         * carries `availableSubjects` for the caller to retry against.
         *
         * @returns A `CallToolResult` carrying the hierarchy or the not-found
         *   recovery payload.
         */
        handler: () => {
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
        },
      }),
    name: "browse_subject",
    server,
  });

  registerReadOnlyTool({
    /**
     * MCP entry point for the `get_aux_statements` tool: return the auxiliary
     * statements attached to a single StandardsFrameworkItem, optionally
     * filtered by `source_label` and/or `path_segment`.
     *
     * Aux statements live on `properties.metadata.aux_statements` and are
     * framework-specific secondary annotations (teachable contents, durations,
     * examples, descriptors, etc.) that supplement the primary standard
     * description without being separate `StandardsFrameworkItem` nodes. See
     * the `AuxStatement` type for the full per-entry shape.
     *
     * Resolution falls back through `findStandardItem` only. LearningComponent
     * identifiers are rejected with a not-found error rather than silently
     * walking through `supports` edges, since aux statements live exclusively
     * on the SFI side and the LC's view is already covered by the
     * `supportingSfiAuxStatements` field of `get_provenance`.
     *
     * Filters are case-insensitive and whitespace-normalized via
     * `normalizeOptionalText` before comparison. They are AND-ed: an entry
     * matches when its `source_label` is in the requested set **and** its own
     * `canonical_path_key` (when present) contains the requested path segment.
     * When `source_labels` is omitted, all labels match. When `path_segment` is
     * omitted, the path filter is a no-op.
     *
     * @param args - Raw tool arguments forwarded by `McpServer`. Validated
     *   against `GetAuxStatementsSchema`: an `identifier`, an optional
     *   `source_labels` array, and an optional `path_segment` string.
     *
     * @returns A `CallToolResult` carrying the resolved target as a compact
     *   node, the matching aux statements verbatim, the total available count
     *   on the target, and the applied filters echoed for visibility. Returns
     *   an empty `auxStatements` array (not an error) when the target carries
     *   no aux statements at all or when the filters exclude every entry.
     */
    handler: (args) =>
      runToolHandler({
        /**
         * Parse `args`, resolve the target SFI, then project
         * `metadata.aux_statements` through the optional filters.
         *
         * The applied filter values are echoed back as `appliedFilters` so the
         * caller can confirm which constraints were actually used (e.g. when
         * the model misnames a label or passes an empty string that the
         * normalizer drops).
         *
         * @returns A `CallToolResult` with the aux statements and provenance,
         *   or a `toolError` if the identifier does not resolve to an SFI.
         */
        handler: () => {
          const { identifier, path_segment, source_labels } =
            GetAuxStatementsSchema.parse(args ?? {});
          const standardNode = findStandardItem(identifier);

          if (!standardNode) {
            return toolError(`Standard item '${identifier}' not found.`, {
              hint: "Use search_items with node_type='standard_item' to find a valid identifier. Aux statements live only on StandardsFrameworkItems; for the LC view, use get_provenance.",
            });
          }

          const allAux = standardNode.properties.metadata?.aux_statements ?? [];
          const expectedLabels = source_labels?.length
            ? new Set(
                source_labels
                  .map((label) => normalizeOptionalText(label))
                  .filter((label): label is string => label != null),
              )
            : null;
          const expectedSegment = normalizeOptionalText(path_segment);
          const matched = allAux.filter((aux) => {
            if (expectedLabels) {
              const label = normalizeOptionalText(aux.source_label);

              if (!label || !expectedLabels.has(label)) return false;
            }

            if (expectedSegment) {
              /*
               * Aux statements may carry their own canonical_path_key under a sibling
               * field used by some pipelines; fall back through common locations. When
               * no path key is present at all, the filter excludes the entry rather
               * than matching by accident.
               */
              const auxRecord = aux as Record<string, unknown>;
              const auxPathKey =
                typeof auxRecord.canonical_path_key === "string"
                  ? auxRecord.canonical_path_key
                  : undefined;

              if (typeof auxPathKey !== "string") return false;

              const matchesSegment = auxPathKey
                .split("/")
                .some(
                  (segment) =>
                    normalizeOptionalText(segment) === expectedSegment,
                );

              if (!matchesSegment) return false;
            }

            return true;
          });
          return toolResult({
            appliedFilters: {
              pathSegment: path_segment,
              sourceLabels: source_labels,
            },
            auxStatements: matched,
            count: matched.length,
            target: compactNode(standardNode),
            totalAuxStatements: allAux.length,
          });
        },
      }),
    name: "get_aux_statements",
    server,
  });

  registerReadOnlyTool({
    /**
     * MCP entry point for the `get_framework` tool: return metadata for every
     * `StandardsFramework` registered in the loaded KG, alongside graph-level
     * provenance.
     *
     * `GetFrameworkSchema` carries no fields today; the parse call is kept for
     * forward compatibility and to surface a structured Zod error if a future
     * argument is added but malformed.
     *
     * @param args - Raw tool arguments forwarded by `McpServer`. Validated
     *   against `GetFrameworkSchema`.
     *
     * @returns A `CallToolResult` whose payload lists each framework with its
     *   adoption status, attribution, jurisdiction, license, language, and
     *   source-PDF metadata, plus a `graph` block carrying the parent KG's doc
     *   key, export dialect, generation timestamp, and graph-type info.
     */
    handler: (args) =>
      runToolHandler({
        /**
         * Parse `args` (effectively a no-op today), then project each framework
         * node into a flat shape that hoists common metadata fields out of
         * `properties` and appends the surrounding KG-level provenance from
         * `kg`.
         *
         * @returns A `CallToolResult` carrying the framework list and graph
         *   provenance.
         */
        handler: () => {
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
        },
      }),
    name: "get_framework",
    server,
  });

  registerReadOnlyTool({
    /**
     * MCP entry point for the `get_item` tool: resolve a free-form identifier
     * to whichever node kind it refers to and return a detailed view together
     * with the node's immediate neighbors.
     *
     * The response shape varies by node kind. Standard items include parent,
     * ordered children, related standards, supporting learning components, and
     * one-step learning progression links. Learning components include the
     * standards they support plus the underlying support-relationship records.
     * Frameworks include only their immediate children.
     *
     * @param args - Raw tool arguments forwarded by `McpServer`. Validated
     *   against `GetItemSchema`: an `identifier` accepted in any of the shapes
     *   `findAnyNode` understands (graph node UUID, CASE UUID/URI, or
     *   `properties.identifier`).
     *
     * @returns A `CallToolResult` carrying the resolved node in its
     *   kind-appropriate shape, or an error result with a hint pointing to
     *   `search_items` and `list_facets` when the identifier matches nothing.
     */
    handler: (args) =>
      runToolHandler({
        /**
         * Parse `args` with `GetItemSchema`, resolve via `findAnyNode`, then
         * branch on the discriminator to assemble the kind-specific neighbor
         * payload. The three branches differ only in which traversal helpers
         * are called and which fields are surfaced; all return through
         * `toolResult` so the envelope is uniform.
         *
         * @returns A `CallToolResult` carrying the detailed node and its
         *   neighbors, or a `toolError` for unresolved identifiers.
         */
        handler: () => {
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
        },
      }),
    name: "get_item",
    server,
  });

  registerReadOnlyTool({
    /**
     * MCP entry point for the `get_learning_components_for_standard` tool:
     * return every `LearningComponent` that supports the requested SFI.
     *
     * Resolves `standard_id` against SFI-only indexes via `findStandardItem`,
     * so passing an LC identifier or framework ID yields a not-found error
     * rather than a coerced result.
     *
     * @param args - Raw tool arguments forwarded by `McpServer`. Validated
     *   against `GetLearningComponentsForStandardSchema`: a `standard_id` in
     *   any shape accepted by `findStandardItem`.
     *
     * @returns A `CallToolResult` with the supporting LCs as compact nodes, the
     *   matched standard as a longer-description compact node, and the total
     *   count; or an error result with a hint to use `search_items` if the
     *   standard isn't found.
     */
    handler: (args) =>
      runToolHandler({
        /**
         * Parse `args` with `GetLearningComponentsForStandardSchema`, resolve
         * the standard via `findStandardItem`, then call
         * `getLearningComponentsForStandard` for the supporting LCs. LCs are
         * compacted at a 500-char description ceiling because they are
         * list-shaped; the standard itself is compacted at a 1000-char ceiling
         * because it is the focal item.
         *
         * @returns A `CallToolResult` with the LC list and standard, or a
         *   `toolError` when the SFI lookup misses.
         */
        handler: () => {
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
        },
      }),
    name: "get_learning_components_for_standard",
    server,
  });

  registerReadOnlyTool({
    /**
     * MCP entry point for the `get_path` tool: return the root-to-node
     * hierarchical path for any KG node.
     *
     * Accepts SFIs, LCs, and frameworks via `findAnyNode`, deferring path
     * construction to `getPathForNode`, which knows the right edge type to walk
     * for each node kind.
     *
     * @param args - Raw tool arguments forwarded by `McpServer`. Validated
     *   against `GetPathSchema`: a single `identifier` string in any shape
     *   accepted by `findAnyNode`.
     *
     * @returns A `CallToolResult` carrying the path payload produced by
     *   `getPathForNode`, or an error result with a hint to use `search_items`
     *   when the identifier resolves to no node.
     */
    handler: (args) =>
      runToolHandler({
        /**
         * Parse `args`, resolve the identifier through `findAnyNode`, and pass
         * the resolved node into `getPathForNode`. Node-kind dispatch lives
         * inside `getPathForNode`; this handler is just an
         * identifier-to-payload pipe.
         *
         * @returns A `CallToolResult` with the path, or a `toolError` for
         *   unresolved identifiers.
         */
        handler: () => {
          const { identifier } = GetPathSchema.parse(args ?? {});
          const result = findAnyNode(identifier);

          if (!result) {
            return toolError(`Item '${identifier}' not found.`, {
              hint: "Use search_items to find a valid identifier.",
            });
          }

          return toolResult(getPathForNode(result.item));
        },
      }),
    name: "get_path",
    server,
  });

  registerReadOnlyTool({
    /**
     * MCP entry point for the `get_progression` tool: traverse
     * `buildsTowards`/`buildsFrom`/`relatesTo` edges from a focal SFI to map a
     * learning progression.
     *
     * Accepts an LC identifier as a fallback: when the identifier resolves to
     * an LC rather than an SFI, the handler picks the first SFI the LC supports
     * as the focal node and reports the redirection via
     * `mappedFromLearningComponent` so the caller can see how the indirection
     * happened.
     *
     * @param args - Raw tool arguments forwarded by `McpServer`. Validated
     *   against `GetProgressionSchema`: an `identifier`, a `direction`
     *   (`builds_towards` | `builds_from` | `related` | `both`), and a `depth`
     *   cap.
     *
     * @returns A `CallToolResult` carrying the progression traversal payload
     *   plus the optional `mappedFromLearningComponent` block, or an error
     *   result if neither the identifier nor any LC fallback yields an SFI.
     */
    handler: (args) =>
      runToolHandler({
        /**
         * Parse `args`, then resolve the focal SFI in two passes:
         *
         * 1. Try `findStandardItem` directly.
         * 2. If that misses, try `findLearningComponent`. If an LC is found,
         *    follow its `supports` edges and pick the first supported standard
         *    as the focal node, recording the mapping for the caller via
         *    `mappedFromLearningComponent`.
         *
         * The traversal itself is delegated to `buildProgressionTraversal`. The
         * returned payload spreads the traversal fields alongside
         * `mappedFromLearningComponent`, which is `null` when the focal node
         * was resolved directly.
         *
         * @returns A `CallToolResult` with the progression and mapping, or a
         *   `toolError` if both resolution passes miss.
         */
        handler: () => {
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

          const traversal = buildProgressionTraversal(
            standardNode,
            direction,
            depth,
          );
          const totalEdges =
            (traversal.buildsFrom as unknown[]).length +
            (traversal.buildsTowards as unknown[]).length +
            (traversal.related as unknown[]).length;
          return toolResult({
            mappedFromLearningComponent,
            ...traversal,
            progressionAvailability:
              totalEdges > 0 ? "edges_present" : "no_edges_found",
          });
        },
      }),
    name: "get_progression",
    server,
  });

  registerReadOnlyTool({
    /**
     * MCP entry point for the `get_related_items` tool: return every node
     * connected to the focal SFI via a `relatesTo` edge.
     *
     * SFI-only: accepts just `findStandardItem`-resolvable identifiers. Unlike
     * `get_progression`, this tool does not fall back to LCs; the `relatesTo`
     * edge is meaningful only between curriculum items, so an LC identifier
     * yields a not-found error.
     *
     * @param args - Raw tool arguments forwarded by `McpServer`. Validated
     *   against `GetRelatedItemsSchema`: a single `identifier` accepted by
     *   `findStandardItem`.
     *
     * @returns A `CallToolResult` with the focal item, the related-items array
     *   as compact nodes, and a count; or an error result with a hint to use
     *   `search_items` when the SFI isn't found.
     */
    handler: (args) =>
      runToolHandler({
        /**
         * Parse `args`, resolve the SFI via `findStandardItem`, and pass its ID
         * to `getRelatesTo`. Related nodes are deduped by graph node ID before
         * being compacted at the default description budget because they are
         * list-shaped.
         *
         * @returns A `CallToolResult` with the related items, or a `toolError`
         *   for unresolved SFIs.
         */
        handler: () => {
          const { identifier } = GetRelatedItemsSchema.parse(args ?? {});
          const standardNode = findStandardItem(identifier);

          if (!standardNode) {
            return toolError(`Standard item '${identifier}' not found.`, {
              hint: "Use search_items with node_type='standard_item' to find a valid identifier.",
            });
          }

          const relatedById = new Map<string, GraphNode>();

          for (const node of getRelatesTo(standardNode.id)) {
            if (!relatedById.has(node.id)) {
              relatedById.set(node.id, node);
            }
          }

          const related = [...relatedById.values()];
          return toolResult({
            relatedCount: related.length,
            relatedItems: related.map((node) => compactNode(node)),
            target: compactNode(standardNode),
          });
        },
      }),
    name: "get_related_items",
    server,
  });

  registerReadOnlyTool({
    /**
     * MCP entry point for the `get_provenance` tool: return the provenance
     * metadata for any KG node.
     *
     * Accepts SFIs, LCs, and frameworks via `findAnyNode`. The actual
     * provenance shape is decided by `provenanceForNode`, which surfaces
     * source-document, framework, and ingestion-pipeline fields appropriate to
     * the node kind.
     *
     * @param args - Raw tool arguments forwarded by `McpServer`. Validated
     *   against `GetProvenanceSchema`: a single `identifier` accepted by
     *   `findAnyNode`.
     *
     * @returns A `CallToolResult` carrying the provenance payload, or an error
     *   result with a hint to use `search_items` when the identifier resolves
     *   to no node.
     */
    handler: (args) =>
      runToolHandler({
        /**
         * Parse `args`, resolve the identifier through `findAnyNode`, and feed
         * the resolved node into `provenanceForNode`. Node-kind dispatch lives
         * inside `provenanceForNode`; this handler is a thin
         * identifier-to-provenance pipe.
         *
         * @returns A `CallToolResult` with the provenance, or a `toolError` for
         *   unresolved identifiers.
         */
        handler: () => {
          const { identifier } = GetProvenanceSchema.parse(args ?? {});
          const result = findAnyNode(identifier);

          if (!result) {
            return toolError(`Item '${identifier}' not found.`, {
              hint: "Use search_items to find a valid identifier.",
            });
          }

          return toolResult(provenanceForNode(result.item));
        },
      }),
    name: "get_provenance",
    server,
  });

  registerReadOnlyTool({
    /**
     * MCP entry point for the `list_facets` tool: return the cached facet
     * payload built once during `createKnowledgeGraphUtils` setup.
     *
     * The payload includes the full set of filterable facet values (subjects,
     * grade levels, source labels, statement types, normalized statement types,
     * relationship types, node types) and aggregate counts across the loaded
     * KG. Because the graph is read-only after loading, the entire response is
     * precomputed and returned by reference; no scanning happens at request
     * time.
     *
     * @param args - Raw tool arguments forwarded by `McpServer`. Validated
     *   against `ListFacetsSchema`, which carries no fields today.
     *
     * @returns A `CallToolResult` with the precomputed facet values and
     *   aggregate counts.
     */
    handler: (args) =>
      runToolHandler({
        /**
         * Parse `args` (effectively a no-op today), then return the cached
         * facet payload via `getFacetValues`. No graph scanning happens at
         * request time.
         *
         * @returns A `CallToolResult` carrying the cached facet values.
         */
        handler: () => {
          ListFacetsSchema.parse(args ?? {});
          return toolResult(getFacetValues());
        },
      }),
    name: "list_facets",
    server,
  });

  registerReadOnlyTool({
    /**
     * MCP entry point for the `navigate` tool: walk the `hasChild` hierarchy
     * outward from a focal node in a chosen direction.
     *
     * `direction` selects one of `parent`, `children`, `siblings`, `ancestors`,
     * or `descendants` (the only direction that respects `depth`). LCs are
     * special-cased because they don't participate in `hasChild`:
     * `parent`/`ancestors` instead surface the standards they support (and, for
     * `ancestors`, those standards' SFI ancestors). Other directions on an LC
     * return an empty result with a `note` explaining the absence of LC
     * hierarchy.
     *
     * @param args - Raw tool arguments forwarded by `McpServer`. Validated
     *   against `NavigateSchema`: an `identifier`, a `direction`, and a `depth`
     *   cap (only consulted for `descendants`).
     *
     * @returns A `CallToolResult` with the focal item, the traversal results as
     *   compact nodes, the direction, depth, and result count; or an error
     *   result with a hint when the identifier resolves to no node.
     */
    handler: (args) =>
      runToolHandler({
        /**
         * Parse `args`, resolve the focal node via `findAnyNode`, then branch
         * on whether the node is an LC. LCs route to
         * `getStandardsSupportedByLearningComponent` (with an `ancestors` pass
         * through `getAncestors` per supported standard); SFIs and frameworks
         * go through a switch on `direction` calling the matching
         * `getAncestors`/`getChildrenAny`/`getSiblingItems`/ `getDescendants`
         * helper.
         *
         * @returns A `CallToolResult` with the traversal results, or a
         *   `toolError` for unresolved identifiers.
         */
        handler: () => {
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
            const results =
              direction === "parent"
                ? supportedStandards
                : direction === "ancestors"
                  ? supportedStandards.flatMap((standard) => [
                      ...getAncestors(standard.id),
                      standard,
                    ])
                  : [];
            return toolResult({
              direction,
              note:
                direction === "parent" || direction === "ancestors"
                  ? "LearningComponents are attached to curriculum items through supports relationships, not hasChild hierarchy."
                  : "LearningComponents do not have hasChild hierarchy in this KG.",
              results: results.map((node) => compactNode(node)),
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
        },
      }),
    name: "navigate",
    server,
  });

  registerReadOnlyTool({
    /**
     * MCP entry point for the `overview` tool: return a high-level summary of
     * the loaded KG suitable for orienting a fresh client.
     *
     * The payload includes per-relationship-type counts, total node and
     * relationship counts by kind, the framework name and jurisdiction,
     * generation metadata, and a single-sample SFI/LC pair so the caller sees
     * the canonical compact-node shape without a separate query.
     *
     * @param args - Raw tool arguments forwarded by `McpServer`. Validated
     *   against `OverviewSchema`, which carries no fields today.
     *
     * @returns A `CallToolResult` carrying the summary block, the unique
     *   subject and grade-level lists, and one sample of each searchable node
     *   kind.
     */
    handler: (args) =>
      runToolHandler({
        /**
         * Parse `args` (effectively a no-op today), build a fresh
         * per-relationship-type count by scanning `kg.relationships` (cheap
         * because the array is already in memory), then assemble the overview
         * by combining cached facet helpers (`getUniqueGradeLevels`,
         * `getUniqueSubjects`) with single-element sample lookups against the
         * SFI/LC index slices.
         *
         * @returns A `CallToolResult` with the assembled overview payload.
         */
        handler: () => {
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
        },
      }),
    name: "overview",
    server,
  });

  registerReadOnlyTool({
    /**
     * MCP entry point for the `search_items` tool: substring text search across
     * SFIs and LCs with optional facet filters.
     *
     * Searches the precomputed lowercase blob built by
     * `buildSearchTextByNodeId` for the requested `query`, narrows by any
     * combination of `node_type`, `grade`, `subject`, `source_label`, and
     * `statement_type`, and caps the result at `limit`. `node_type` defaults to
     * `"all"` when not provided so callers can search across both kinds without
     * specifying it.
     *
     * @param args - Raw tool arguments forwarded by `McpServer`. Validated
     *   against `SearchItemsSchema`: optional `query`, optional facet filters,
     *   optional `node_type`, and optional `limit`.
     *
     * @returns A `CallToolResult` with the matching nodes as compact nodes, the
     *   applied filters echoed for visibility, the count, and the original
     *   query string (or empty string when omitted).
     */
    handler: (args) =>
      runToolHandler({
        /**
         * Parse `args`, default `node_type` to `"all"`, and dispatch to
         * `searchItems`. Filters are passed through verbatim — the handler does
         * no normalization of its own; case folding and whitespace collapse
         * happen inside `searchItems` and the underlying `getSearchText`
         * function. The applied filters are echoed back in the payload so the
         * caller can verify which constraints were actually used.
         *
         * @returns A `CallToolResult` with the search results, filters, and
         *   query echo.
         */
        handler: () => {
          const parsed = SearchItemsSchema.parse(args ?? {});
          const nodeType = parsed.node_type ?? "all";
          const results = searchItems({
            grade: parsed.grade,
            limit: parsed.limit,
            nodeType,
            pathSegment: parsed.path_segment,
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
              pathSegment: parsed.path_segment,
              sourceLabel: parsed.source_label,
              statementType: parsed.statement_type,
              subject: parsed.subject,
            },
            query: parsed.query ?? "",
            results: results.map((result) => compactNode(result.item)),
          });
        },
      }),
    name: "search_items",
    server,
  });
}

/**
 * Register one read-only KG tool against a high-level `McpServer` instance.
 *
 * This function centralizes the lookup from static tool metadata to McpServer's
 * `registerTool` call.
 *
 * @param root0 - Named arguments for registering the tool.
 * @param root0.handler - The tool handler function that implements the actual
 *   behavior of the tool.
 * @param root0.name - The programmatic name of the tool, which must match the
 *   `name` field of one of the `KnowledgeGraphToolDefinition` entries in the
 *   static catalogue.
 * @param root0.server - The McpServer instance against which the tool should be
 *   registered.
 */
function registerReadOnlyTool({
  handler,
  name,
  server,
}: {
  handler: (args: Record<string, unknown>) => CallToolResult;
  name: string;
  server: McpServer;
}): void {
  const { annotations, description, inputSchema, title } = getToolDefinition({
    name,
  });

  server.registerTool(
    name,
    {
      annotations,
      description,
      inputSchema,
      title,
    },
    handler,
  );
}

/**
 * Run a tool implementation and convert thrown errors into MCP tool errors.
 *
 * NB: With `McpServer` each tool is registered independently.
 *
 * @param root0 - Named arguments for running the tool handler.
 * @param root0.handler - The tool handler function that implements the actual
 *   behavior of the tool.
 *
 * @returns A normal or error MCP tool result.
 */
function runToolHandler({
  handler,
}: {
  handler: () => CallToolResult;
}): CallToolResult {
  try {
    return handler();
  } catch (error) {
    return toolError(
      `Error: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
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
