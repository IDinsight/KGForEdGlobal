/** @file This file contains general utility functions. */

// Standard Library
import {readFileSync} from "fs";
import {join} from "path";

// Third Party Library
import type {CallToolResult} from "@modelcontextprotocol/sdk/types.js";

// Package Library
import {
    KnowledgeGraphSchema,
    type ProgressionDirection,
    type SearchNodeType
} from "./schemas.js";

/**
 * Build fast-lookup indexes over a parsed Knowledge Graph.
 *
 * Partitions nodes by label into frameworks, standard items (SFIs), learning
 * components, and unknown nodes. Constructs the following index maps:
 *
 * - `nodesById` — All nodes keyed by graph node ID.
 * - `sfisByIdentifier`/`lcByIdentifier` — SFIs and LCs keyed by their
 *  `properties.identifier` for direct lookup by external ID.
 * - `relsByStart`/`relsByEnd` — Relationship adjacency lists keyed by source and
 *  target node ID for efficient graph traversal.
 *
 * Nodes with unrecognized labels are collected in `unknownNodes` and a warning is
 * logged to stderr.
 *
 * @param kg - A validated {@link KnowledgeGraph} object (typically from
 *  {@link loadKnowledgeGraph}).
 *
 * @returns A {@link KnowledgeGraphIndexes} containing all partitioned arrays and
 *  lookup maps.
 */
export function buildKnowledgeGraphIndexes(kg: KnowledgeGraph): KnowledgeGraphIndexes {
    const frameworks: GraphNode[] = [];
    const learningComponents: GraphNode[] = [];
    const nodesById = new Map<string, GraphNode>();
    const sfis: GraphNode[] = [];
    const unknownNodes: GraphNode[] = [];

    for (const node of kg.nodes) {
        const isFramework = node.labels.includes("StandardsFramework");
        const isSfi = node.labels.includes("StandardsFrameworkItem");
        const isLc = node.labels.includes("LearningComponent");

        // The three known KG label types are expected to be mutually exclusive. If a
        // node ever carries more than one, the partitioning below would silently drop
        // it from one of the indexes; surface the malformed node loudly at load so it
        // can be fixed at the source rather than chased through downstream queries.
        const matchedKnownLabels = Number(isFramework) + Number(isSfi) + Number(isLc);

        if (matchedKnownLabels > 1) {
            throw new Error(
                `Node ${node.id} has multiple known KG labels (${node.labels.join(", ")}); ` +
                `StandardsFramework, StandardsFrameworkItem, and LearningComponent are expected to be mutually exclusive.`
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

        // Index all nodes by their graph node ID. For StandardsFramework/SFI nodes
        // this is usually `case_identifier_uuid`; for LearningComponent nodes this is
        // usually `identifier`.
        nodesById.set(node.id, node);
    }

    if (unknownNodes.length > 0) {
        // Surface the distinct label combinations and a handful of node IDs so the KG
        // author can search the source JSON and decide whether to extend the
        // partitioner or correct a typo.
        const uniqueLabelSets = Array.from(
            new Set(unknownNodes.map((n) => JSON.stringify(n.labels)))
        );
        const sampleIds = unknownNodes.slice(0, 3).map((n) => n.id);
        console.error(
            `Ignored ${unknownNodes.length} node(s) with unrecognized labels. ` +
            `Unique label sets: ${uniqueLabelSets.join(", ")}. ` +
            `Sample node IDs: ${sampleIds.join(", ")}.`
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
 * Tally string occurrences in an array, returning a frequency map sorted
 * alphabetically by key.
 *
 * Each value is normalized before counting:
 * - `null`, `undefined`, empty strings, and whitespace-only strings are bucketed
 *  together under the key `"Unspecified"`.
 * - All other strings are passed through {@link normalizeWhitespace} (internal runs of
 *   whitespace collapsed to a single space, ends trimmed) before being used as the
 *   bucket key. So "  Objectif  spécifique " and "Objectif spécifique" count to the
 *   same bucket.
 *
 * Counting is case-sensitive: "Foo" and "foo" are distinct buckets. The returned
 * object's keys are ordered alphabetically via `localeCompare`.
 *
 * @param values - Array of strings to tally. `null` and `undefined` entries are
 * allowed and are routed to the `"Unspecified"` bucket alongside empty/whitespace-only
 * strings.
 *
 * @returns A plain object mapping each normalized key to its count, with entries
 * ordered alphabetically by key.
 *
 * @example
 * // Basic tally over a homogeneous string array.
 * countBy(["hasChild", "supports", "hasChild", "buildsTowards"]);
 * // -> { buildsTowards: 1, hasChild: 2, supports: 1 }
 *
 * @example
 * // null, undefined, "", and whitespace-only strings all collapse into "Unspecified".
 * countBy(["Conjugaison", null, "Conjugaison", undefined, "", "   ", "Orthographe"]);
 * // -> { Conjugaison: 2, Orthographe: 1, Unspecified: 4 }
 *
 * @example
 * // Internal whitespace is collapsed and ends are trimmed before bucketing.
 * countBy(["  Objectif  spécifique ", "Objectif spécifique"]);
 * // -> { "Objectif spécifique": 2 }
 *
 * @example
 * // Counting is case-sensitive.
 * countBy(["Foo", "foo", "Foo"]);
 * // -> { foo: 1, Foo: 2 }
 */
function countBy(values: Array<string | null | undefined>): Record<string, number> {
    const counts: Record<string, number> = {};

    for (const value of values) {
        const key = value && value.trim().length > 0 ? normalizeWhitespace(value) : "Unspecified";
        counts[key] = (counts[key] ?? 0) + 1;
    }

    return Object.fromEntries(
        Object.entries(counts).sort(([a], [b]) => a.localeCompare(b))
    );
}

/**
 * Create a set of query helpers that operate over a loaded Knowledge Graph and its
 * pre-built indexes.
 *
 * This is the main "query layer" for the MCP server. It accepts a
 * {@link KnowledgeGraphContext} (the raw graph plus the index maps produced by
 * {@link buildKnowledgeGraphIndexes}) and returns an object of pure functions that the
 * MCP tool handlers call to answer requests.
 *
 * The returned helpers fall into several categories:
 *
 * - **Lookup** (`findStandardItem`, `findLearningComponent`, `findAnyNode`) — resolve
 *  a user-supplied identifier (graph node ID, CASE UUID, or `properties.identifier`)
 *  to a concrete {@link GraphNode}.
 *
 * - **Hierarchy traversal** (`getAncestors`, `getDescendants`, `getChildrenAny`,
 *  `getSiblingItems`, `getPathForNode`) — walk the `hasChild` tree in either direction.
 *
 * - **Cross-reference** (`getLearningComponentsForStandard`,
 *  `getStandardsSupportedByLearningComponent`,
 *  `getSupportRelationshipsForLearningComponent`, `getRelatesTo`) — follow `supports`
 *  and `relatesTo` edges between SFIs and LearningComponents.
 *
 * - **Progression** (`buildProgressionTraversal`) — traverse
 *  `buildsTowards`/`relatesTo` edges to map learning progressions.
 *
 * - **Search and browse** (`searchItems`, `buildHierarchyForSubject`, `getFacetValues`) —
 *  full-text-ish search with facet filters and hierarchical subject browsing.
 *
 * - **Serialization** (`compactNode`, `detailedNode`, `provenanceForNode`) — shape a
 *  {@link GraphNode} into the JSON payload returned by MCP tools.
 *
 * All functions are read-only; nothing mutates the underlying graph or indexes.
 *
 * @param context - A {@link KnowledgeGraphContext} containing the parsed KG and its
 *  index maps.
 *
 * @returns An object of query functions consumed by the MCP tool handlers in
 *  `index.ts`.
 */
export function createKnowledgeGraphUtils(context: KnowledgeGraphContext) {
    const {
        frameworks,
        lcByIdentifier,
        learningComponents,
        kg,
        nodesById,
        relsByEnd,
        relsByStart,
        sfis,
        sfisByIdentifier,
    } = context;

    function buildHierarchyForSubject(
        subject: string,
        gradeFilter?: string
    ): object[] {
        const normalizedSubject = normalizeOptionalText(subject);
        const topLevelItems: GraphNode[] = [];

        for (const node of sfis) {
            const itemSubject = normalizeOptionalText(
                node.properties.academic_subject?.replace(/\n/g, " ")
            );

            if (itemSubject === normalizedSubject) {
                const parent = getParent(node.id);
                const parentSubject = normalizeOptionalText(
                    parent?.properties.academic_subject?.replace(/\n/g, " ")
                );

                if (!parent || parentSubject !== normalizedSubject) {
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
                            c.properties.statement_code?.includes(gradeFilter)
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

    function buildProgressionTraversal(
        standardNode: GraphNode,
        direction: ProgressionDirection,
        depth: number
    ): Record<string, unknown> {
        const seenFrom = new Set<string>();
        const seenTo = new Set<string>();
        const seenRelated = new Set<string>();

        function collectBuildsFrom(nodeId: string, remainingDepth: number): GraphNode[] {
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
                ...direct.flatMap((node) => collectBuildsFrom(node.id, remainingDepth - 1)),
            ];
        }

        function collectBuildsTowards(nodeId: string, remainingDepth: number): GraphNode[] {
            if (remainingDepth <= 0) return [];

            const direct = (relsByStart.get(nodeId) || [])
                .filter((rel) => rel.type === "buildsTowards")
                .map((rel) => nodesById.get(rel.end))
                .filter(
                    (node): node is GraphNode =>
                        node != null && node.labels.includes("StandardsFrameworkItem")
                )
                .filter((node) => {
                    if (seenTo.has(node.id)) return false;
                    seenTo.add(node.id);
                    return true;
                });
            return [
                ...direct,
                ...direct.flatMap((node) => collectBuildsTowards(node.id, remainingDepth - 1)),
            ];
        }

        const related = getRelatesTo(standardNode.id).filter((node) => {
            if (seenRelated.has(node.id)) return false;

            seenRelated.add(node.id);
            return true;
        });
        return {
            target: compactNode(standardNode),
            depth,
            direction,
            buildsFrom:
                direction === "both" || direction === "builds_from"
                    ? collectBuildsFrom(standardNode.id, depth).map((node) => compactNode(node))
                    : [],
            buildsTowards:
                direction === "both" || direction === "builds_towards"
                    ? collectBuildsTowards(standardNode.id, depth).map((node) => compactNode(node))
                    : [],
            related:
                direction === "both" || direction === "related"
                    ? related.map((node) => compactNode(node))
                    : [],
        };
    }

    function compactNode(node: GraphNode, maxDescription = 220): Record<string, unknown> {
        const metadata = node.properties.metadata ?? {};
        const desc = node.properties.description;
        return {
            nodeType: node.labels.includes("StandardsFramework") ? "framework"
                : node.labels.includes("StandardsFrameworkItem") ? "standard_item"
                : node.labels.includes("LearningComponent") ? "learning_component"
                : "unknown",
            labels: node.labels,
            identifier: node.properties.identifier,
            uuid: node.id,
            name: node.properties.name,
            description: !desc ? desc : (desc.length > maxDescription ? `${desc.substring(0, maxDescription)}...` : desc),
            subject: node.properties.academic_subject?.replace(/\n/g, " "),
            gradeLevel: node.properties.grade_level,
            statementCode: node.properties.statement_code,
            statementType: node.properties.statement_type,
            normalizedStatementType: node.properties.normalized_statement_type,
            sourceLabel: metadata.source_label ?? metadata.supporting_sfi_source_label,
            canonicalPathKey: metadata.canonical_path_key ?? metadata.supporting_sfi_canonical_path_key,
        };
    }

    function detailedNode(node: GraphNode): Record<string, unknown> {
        return {
            ...compactNode(node, 1000),
            properties: {
                ...node.properties,
                academic_subject: node.properties.academic_subject?.replace(/\n/g, " "),
            },
        };
    }

    function findAnyNode(identifier: string): { type: string; item: GraphNode } | null {
        const standard = findStandardItem(identifier);

        if (standard) return {type: "standard_item", item: standard};

        const learningComponent = findLearningComponent(identifier);

        if (learningComponent) return {
            type: "learning_component",
            item: learningComponent
        };

        const framework = nodesById.get(identifier);

        if (framework && framework.labels.includes("StandardsFramework")) {
            return {type: "framework", item: framework};
        }

        return null;
    }

    function findLearningComponent(identifier: string): GraphNode | undefined {
        const byIdentifier = lcByIdentifier.get(identifier);

        if (byIdentifier) return byIdentifier;

        const byId = nodesById.get(identifier);

        if (byId && byId.labels.includes("LearningComponent")) return byId;

        return learningComponents.find(
            (node) => node.properties.metadata?.canonical_node_id === identifier
        );
    }

    function findStandardItem(identifier: string): GraphNode | undefined {
        const byIdentifier = sfisByIdentifier.get(identifier);

        if (byIdentifier) return byIdentifier;

        const byId = nodesById.get(identifier);

        if (byId && byId.labels.includes("StandardsFrameworkItem")) return byId;

        return sfis.find(
            (node) =>
                node.properties.case_identifier_uuid === identifier ||
                node.properties.case_identifier_uri === identifier ||
                node.properties.metadata?.canonical_node_id === identifier
        );
    }

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

    function getChildrenAny(parentNodeId: string): GraphNode[] {
        const rels = relsByStart.get(parentNodeId) || [];
        return rels
            .filter((rel) => rel.type === "hasChild")
            .sort(
                (a, b) =>
                    (a.properties.order_index ?? a.properties.metadata?.canonical_order_index ?? 0) -
                    (b.properties.order_index ?? b.properties.metadata?.canonical_order_index ?? 0)
            )
            .map((rel) => nodesById.get(rel.end))
            .filter((node): node is GraphNode => Boolean(node));
    }

    function getDescendants(nodeId: string, depth: number): GraphNode[] {
        const descendants: GraphNode[] = [];
        const seen = new Set<string>();

        function visit(currentNodeId: string, remainingDepth: number) {
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

    function getFacetValues(): Record<string, unknown> {
        return {
            subjects: getUniqueSubjects(),
            gradeLevels: getUniqueGradeLevels(),
            nodeTypes: ["framework", "standard_item", "learning_component"],
            relationshipTypes: uniqueSorted(kg.relationships.map((rel) => rel.type)),
            statementTypes: uniqueSorted(sfis.map((node) => node.properties.statement_type)),
            normalizedStatementTypes: uniqueSorted(
                sfis.map((node) => node.properties.normalized_statement_type)
            ),
            sourceLabels: uniqueSorted(
                sfis.map((node) => node.properties.metadata?.source_label as string | undefined)
            ),
            learningComponentSourceLabels: uniqueSorted(
                learningComponents.map(
                    (node) => node.properties.metadata?.supporting_sfi_source_label as string | undefined
                )
            ),
            counts: {
                frameworks: frameworks.length,
                standardItems: sfis.length,
                learningComponents: learningComponents.length,
                relationships: kg.relationships.length,
                byRelationshipType: countBy(kg.relationships.map((rel) => rel.type)),
                byStatementType: countBy(sfis.map((node) => node.properties.statement_type)),
                bySourceLabel: countBy(
                    sfis.map((node) => node.properties.metadata?.source_label as string | undefined)
                ),
            },
        };
    }

    function getLearningComponentsForStandard(
        standardNodeId: string
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

    function getParentAny(childNodeId: string): GraphNode | undefined {
        const rels = relsByEnd.get(childNodeId) || [];
        const parentRel = rels
            .filter((rel) => rel.type === "hasChild")
            .sort(
                (a, b) =>
                    (a.properties.order_index ?? a.properties.metadata?.canonical_order_index ?? 0) -
                    (b.properties.order_index ?? b.properties.metadata?.canonical_order_index ?? 0)
            )[0];

        return parentRel ? nodesById.get(parentRel.start) : undefined;
    }

    function getPathForNode(node: GraphNode): Record<string, unknown> {
        if (node.labels.includes("LearningComponent")) {
            const supportedStandards = getStandardsSupportedByLearningComponent(node.id);
            const primaryStandard = supportedStandards[0];
            return {
                target: compactNode(node),
                supportedStandardCount: supportedStandards.length,
                supportedStandards: supportedStandards.map((standard) => compactNode(standard)),
                path: primaryStandard
                    ? [...getAncestors(primaryStandard.id), primaryStandard, node].map((pathNode) =>
                        compactNode(pathNode)
                    )
                    : [compactNode(node)],
            };
        }

        return {
            target: compactNode(node),
            path: [...getAncestors(node.id), node].map((pathNode) => compactNode(pathNode)),
        };
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
            .replace(/\n/g, " ")
            .toLowerCase();
    }

    function getSiblingItems(nodeId: string): GraphNode[] {
        const parent = getParentAny(nodeId);

        if (!parent) return [];

        return getChildrenAny(parent.id).filter((node) => node.id !== nodeId);
    }

    function getStandardsSupportedByLearningComponent(
        learningComponentNodeId: string
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

    function getSupportRelationshipsForLearningComponent(learningComponentNodeId: string): GraphRelationship[] {
        return (relsByStart.get(learningComponentNodeId) || []).filter(
            (rel) => rel.type === "supports"
        );
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

    function nodeMatchesGrade(node: GraphNode, grade?: string): boolean {
        if (!grade) return true;

        const expected = normalizeOptionalText(grade);
        const gradeLevels = node.properties.grade_level ?? node.properties.metadata?.supporting_sfi_grade_level;
        return Array.isArray(gradeLevels)
            ? gradeLevels.some((g) => normalizeOptionalText(String(g)) === expected)
            : false;
    }

    function nodeMatchesSourceLabel(node: GraphNode, sourceLabel?: string): boolean {
        if (!sourceLabel) return true;

        const expected = normalizeOptionalText(sourceLabel);
        const candidates = [
            node.properties.metadata?.source_label as string | undefined,
            node.properties.metadata?.supporting_sfi_source_label as string | undefined,
        ];
        return candidates.some((candidate) => normalizeOptionalText(candidate) === expected);
    }

    function nodeMatchesStatementType(node: GraphNode, statementType?: string): boolean {
        if (!statementType) return true;

        const expected = normalizeOptionalText(statementType);
        const candidates = [
            node.properties.statement_type,
            node.properties.normalized_statement_type,
            node.properties.metadata?.supporting_sfi_statement_type as string | undefined,
            node.properties.metadata?.supporting_sfi_normalized_statement_type as string | undefined,
        ];
        return candidates.some((candidate) => normalizeOptionalText(candidate) === expected);
    }

    function nodeMatchesSubject(node: GraphNode, subject?: string): boolean {
        if (!subject) return true;

        const expected = normalizeOptionalText(subject);
        const actual = normalizeOptionalText(node.properties.academic_subject?.replace(/\n/g, " "));
        return actual === expected;
    }

    function provenanceForNode(node: GraphNode): Record<string, unknown> {
        const metadata = node.properties.metadata ?? {};
        const supportedStandards = node.labels.includes("LearningComponent")
            ? getStandardsSupportedByLearningComponent(node.id)
            : [];
        return {
            target: compactNode(node),
            attributionStatement: node.properties.attribution_statement,
            author: node.properties.author,
            provider: node.properties.provider,
            license: node.properties.license,
            dateCreated: node.properties.date_created,
            dateModified: node.properties.date_modified,
            sourceLabel: metadata.source_label ?? metadata.supporting_sfi_source_label,
            sourceSegmentIds: metadata.source_segment_ids,
            sourceDecisionIds: metadata.source_decision_ids,
            pageIndices: metadata.page_indices ?? (metadata.provenance as Record<string, unknown> | undefined)?.page_indices,
            bbox: metadata.bbox ?? (metadata.provenance as Record<string, unknown> | undefined)?.bbox,
            bboxRef: metadata.bbox_ref ?? (metadata.provenance as Record<string, unknown> | undefined)?.bbox_ref,
            canonicalPathKey: metadata.canonical_path_key ?? metadata.supporting_sfi_canonical_path_key,
            progressionContext:
                metadata.progression_context ?? metadata.supporting_sfi_progression_context,
            supportingSfi: supportedStandards.map((standard) => compactNode(standard)),
            supportingSfiAuxStatements: metadata.supporting_sfi_aux_statements,
            llmRationale: metadata.llm_rationale,
        };
    }

    function searchItems(options: {
        grade?: string;
        limit?: number;
        nodeType?: SearchNodeType;
        query?: string;
        sourceLabel?: string;
        statementType?: string;
        subject?: string;
    }): Array<{ type: string; item: GraphNode }> {
        const q = normalizeOptionalText(options.query) ?? "";
        const nodeType = options.nodeType ?? "all";
        const limit = options.limit ?? 20;
        const results: Array<{ type: string; item: GraphNode }> = [];
        const candidateGroups: Array<{ type: string; nodes: GraphNode[] }> = [];

        if (nodeType === "all" || nodeType === "standard_item") {
            candidateGroups.push({type: "standard_item", nodes: sfis});
        }

        if (nodeType === "all" || nodeType === "learning_component") {
            candidateGroups.push({
                type: "learning_component",
                nodes: learningComponents
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

                results.push({type: group.type, item: node});
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
 * Load and validate a Knowledge Graph JSON file from the bundled examples directory.
 *
 * Resolves the file path relative to `runtimeDir` (expected to be the compiled source
 * directory) via `../../examples/kgs/<kgFn>`. Loads in three stages (file read, JSON
 * parse, schema validation) each of which surfaces a distinct, kgFp-tagged error
 * message so connector setup failures are easy to diagnose from Claude Desktop's log
 * output. Schema validation is enforced via {@link KnowledgeGraphSchema} (Zod) and
 * checks that every node has `id`, `labels`, and `properties.identifier`, and every
 * relationship has `id`, `start`, `end`, and `type`. Extra fields are passed through.
 * Logs a summary of loaded node/relationship counts to stderr.
 *
 * @param kgFn - Filename of the KG JSON file (e.g. "senegal_reading.json").
 * @param runtimeDir - Directory of the calling module, typically
 *  `dirname(fileURLToPath(import.meta.url))`.
 *
 * @returns The parsed and validated {@link KnowledgeGraph} object.
 *
 * @throws If the file is not found, unreadable, malformed JSON, or fails schema
 *  validation. All thrown errors include the resolved filepath in the message.
 */
export function loadKnowledgeGraph(kgFn: string, runtimeDir: string): KnowledgeGraph {
    const kgFp = join(runtimeDir, "..", "..", "examples", "kgs", kgFn);

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
                `Failed to load knowledge graph: file not found at ${kgFp}`
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
        throw new Error(`Failed to parse knowledge graph JSON at ${kgFp}: ${message}`);
    }

    const validation = KnowledgeGraphSchema.safeParse(parsed);

    if (!validation.success) {
        const issues = validation.error.issues
            .map((issue) => `  - ${issue.path.join(".") || "<root>"}: ${issue.message}`)
            .join("\n");
        throw new Error(
            `Failed to validate knowledge graph at ${kgFp}:\n${issues}`
        );
    }

    const kg = validation.data as unknown as KnowledgeGraph;
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
}

/**
 * Normalize a string for case-insensitive comparison and bucketing by trimming,
 * collapsing internal whitespace, and lowercasing. Returns `undefined` for nullish or
 * whitespace-only inputs to simplify optional field handling in filters.
 *
 * @param value - The string to normalize, or null/undefined.
 *
 * @return The normalized string, or `undefined` if the input was nullish or
 *  whitespace-only.
 */
function normalizeOptionalText(value: string | null | undefined): string | undefined {
    return value ? normalizeWhitespace(value).toLowerCase() : undefined;
}

/**
 * Normalize a string by trimming and collapsing internal whitespace to a single space.
 *
 * @param value - The string to normalize.
 *
 * @returns The normalized string with trimmed and collapsed whitespace.
 */
function normalizeWhitespace(value: string): string {
    return value.replace(/\s+/g, " ").trim();
}

/**
 * Wrap a tool failure into the MCP `CallToolResult` envelope expected by Claude
 * Desktop.
 *
 * Builds the dual representation the MCP protocol requires: the JSON-pretty-printed
 * text placed in `content[0].text` is what the LLM literally reads as the tool's
 * reply, while the same payload as a structured object is placed in
 * `structuredContent` for SDK clients that consume tool output programmatically. Both
 * views are kept in sync because they are built from the same
 * `{error: message, ...details}` object. The `isError: true` flag is the protocol's
 * signal that this call failed, distinct from a successful call whose payload happens
 * to describe a problem.
 *
 * Used by every tool handler in `index.ts` for explicit failure paths (item not found,
 * unknown tool name, invalid arguments) and by the catch-all `try/catch` wrapping the
 * `CallTool` switch, which funnels any thrown exception into a properly-shaped error
 * response so the JSON-RPC response cycle is never broken.
 *
 * NB: Because the error payload is built as `{error: message, ...details}`, a
 * `details` object that itself contains an `error` key would shadow the message. None
 * of the current call sites do this; just be aware if you start passing richer error
 * context.
 *
 * @param message - Human-readable error text. Becomes the `error` field of the payload
 *  and is what the model sees first when narrating the failure.
 * @param details - Optional supplementary fields merged into the payload alongside
 *  `error`. Use for hints, valid alternatives, attempted identifiers, etc.
 *
 * @returns A `CallToolResult` with `isError: true`, the error payload pretty-printed
 *  into `content[0].text`, and the same payload as `structuredContent`.
 *
 * @example
 * // Minimal error — just a message.
 * toolError("Item 'xyz' not found.");
 * // -> {
 * //     content: [{type: "text", text: '{\n  "error": "Item \'xyz\' not found."\n}'}],
 * //     structuredContent: {error: "Item 'xyz' not found."},
 * //     isError: true,
 * //   }
 *
 * @example
 * // Error with diagnostic details for the model to act on.
 * toolError("Item 'xyz' not found.", {hint: "Use search_items or list_facets."});
 * // -> {
 * //     content: [{type: "text", text: '{\n  "error": "Item \'xyz\' not found.",\n  "hint": "Use search_items or list_facets."\n}'}],
 * //     structuredContent: {error: "Item 'xyz' not found.", hint: "Use search_items or list_facets."},
 * //     isError: true,
 * //   }
 */
export function toolError(message: string, details?: Record<string, unknown>) {
    return {
        content: [
            {
                type: "text",
                text: JSON.stringify({error: message, ...(details ?? {})}, null, 2),
            },
        ],
        structuredContent: {error: message, ...(details ?? {})},
        isError: true,
    };
}

/**
 * Wrap a successful tool payload into the MCP `CallToolResult` envelope expected by
 * Claude Desktop.
 *
 * Builds the dual representation the MCP protocol requires: the JSON-pretty-printed
 * text placed in `content[0].text` is what the LLM literally reads as the tool's
 * reply, while the same `data` object is placed in `structuredContent` for SDK clients
 * that consume tool output programmatically. Both views are guaranteed in sync because
 * they derive from the same input. No `isError` flag is set, signalling a successful
 * call.
 *
 * Used by every tool handler in `index.ts` to return its payload: node lookups,
 * navigation results, search hits, facet values, progressions, etc.
 *
 * @param data - The tool's payload. Whatever shape the tool wants to surface to the
 *  caller; serialized with `JSON.stringify(_, null, 2)` for the text view and passed
 *  through unchanged for the structured view. Must be JSON-serializable.
 *
 * @returns A `CallToolResult` with the data pretty-printed into `content[0].text` and
 *  the same data as `structuredContent`. `isError` is omitted.
 *
 * @example
 * // Single-item lookup result.
 * toolResult({type: "standard_item", id: "abc-123"});
 * // -> {
 * //     content: [{type: "text", text: '{\n  "type": "standard_item",\n  "id": "abc-123"\n}'}],
 * //     structuredContent: {type: "standard_item", id: "abc-123"},
 * //   }
 *
 * @example
 * // List-of-items result; the same array is stringified into content and exposed as
 * // structuredContent.
 * toolResult({results: [{id: "a"}, {id: "b"}], total: 2});
 * // -> {
 * //     content: [{type: "text", text: '{\n  "results": [\n    {\n      "id": "a"\n    },\n    {\n      "id": "b"\n    }\n  ],\n  "total": 2\n}'}],
 * //     structuredContent: {results: [{id: "a"}, {id: "b"}], total: 2},
 * //   }
 */
export function toolResult(data: Record<string, unknown>) {
    return {
        content: [
            {
                type: "text",
                text: JSON.stringify(data, null, 2),
            },
        ],
        structuredContent: data,
    };
}


/**
 * Return the unique, alphabetically sorted set of meaningful string values from an
 * array.
 *
 * Each value is processed before deduplication:
 * - `null`, `undefined`, empty strings, and whitespace-only strings are **dropped**.
 * - All other strings are passed through {@link normalizeWhitespace} (internal runs of
 *   whitespace collapsed to a single space, ends trimmed) before deduplication. So
 *   "  Objectif  spécifique " and "Objectif spécifique" collapse to one entry.
 *
 * Deduplication is case-sensitive: "Foo" and "foo" remain distinct entries. The
 * returned array is sorted alphabetically via `localeCompare`.
 *
 * Used by {@link createKnowledgeGraphUtils}'s `getFacetValues` helper to build the
 * lists of valid filter values (`relationshipTypes`, `statementTypes`, `sourceLabels`,
 * etc.) surfaced through the MCP `list_facets`/`overview` tool responses.
 *
 * @param values - Array of strings to dedupe and sort. `null` and `undefined` entries
 *  are allowed and silently dropped, alongside empty/whitespace-only strings.
 *
 * @returns A new array containing each meaningful, whitespace-normalized value exactly
 *  once, sorted alphabetically.
 *
 * @example
 * // Basic dedupe and sort.
 * uniqueSorted(["hasChild", "supports", "hasChild", "buildsTowards"]);
 * // -> ["buildsTowards", "hasChild", "supports"]
 *
 * @example
 * // null, undefined, "", and whitespace-only strings are dropped (NOT bucketed).
 * uniqueSorted(["Conjugaison", null, "Conjugaison", undefined, "", "   ", "Orthographe"]);
 * // -> ["Conjugaison", "Orthographe"]
 *
 * @example
 * // Internal whitespace is collapsed and ends are trimmed before deduplication.
 * uniqueSorted(["  Objectif  spécifique ", "Objectif spécifique", "Vocabulaire"]);
 * // -> ["Objectif spécifique", "Vocabulaire"]
 *
 * @example
 * // Deduplication is case-sensitive.
 * uniqueSorted(["Foo", "foo", "Foo"]);
 * // -> ["foo", "Foo"]
 *
 * @example
 * // Returns an empty array if no meaningful values are present.
 * uniqueSorted([null, undefined, "", "   "]);
 * // -> []
 */
function uniqueSorted(values: Array<string | null | undefined>): string[] {
    return Array.from(
        new Set(
            values
                .filter((v): v is string => typeof v === "string" && v.trim().length > 0)
                .map((v) => normalizeWhitespace(v))
        )
    ).sort((a, b) => a.localeCompare(b));
}
