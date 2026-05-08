/** @file This file contains knowledge graph utilities. */

// Standard Library
import { readFileSync } from "node:fs";
import path from "node:path";

// Package Library
import { KnowledgeGraphSchema } from "@/lib/schemas.js";
import { normalizeOptionalText } from "@/lib/utils.js";

/**
 * Build fast-lookup indexes over a parsed Knowledge Graph.
 *
 * Partitions nodes by label into frameworks, standard framework items (SFIs),
 * learning components, and unknown nodes. Constructs the following index maps:
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

  /*
   * Report progression edge coverage so missing `buildsTowards` edges are visible at
   * boot time rather than discovered as silent empty traversals during query handling.
   * An SFI is "covered" if it participates in at least one `buildsTowards` edge in
   * either direction. The output is informational only: frameworks with intentionally
   * sparse progressions will simply log a high uncovered count, which is correct for
   * them. Down-stream tools additionally surface the same signal per-call via
   * `progressionAvailability` on `get_progression` responses.
   */
  let sfisWithProgressionEdges = 0;

  for (const sfi of sfis) {
    const incoming = relsByEnd.get(sfi.id) ?? [];
    const outgoing = relsByStart.get(sfi.id) ?? [];
    const hasProgressionEdge =
      incoming.some((rel) => rel.type === "buildsTowards") ||
      outgoing.some((rel) => rel.type === "buildsTowards");

    if (hasProgressionEdge) sfisWithProgressionEdges++;
  }

  const sfisWithoutProgressionEdges = sfis.length - sfisWithProgressionEdges;

  if (sfis.length > 0) {
    const coveragePct = (
      (sfisWithProgressionEdges / sfis.length) *
      100
    ).toFixed(1);
    console.error(
      `Progression-edge coverage: ${sfisWithProgressionEdges}/${sfis.length} ` +
        `StandardsFrameworkItems (${coveragePct}%) participate in at least one ` +
        `buildsTowards edge. ${sfisWithoutProgressionEdges} SFI(s) have no ` +
        `progression edges and will return empty get_progression traversals.`,
    );
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
 * Project an `AuxStatement` to the minimal camelCase shape carried inside
 * `compactNode().auxStatements`.
 *
 * The compact projection drops provenance fields (`bbox`, `bbox_ref`,
 * `canonical_node_id`, `page_indices`, `source_decision_ids`,
 * `source_segment_ids`) and keeps only the user-facing trio of `role`,
 * `sourceLabel`, and `text`. The full original aux statement remains accessible
 * verbatim via `detailedNode().properties.metadata.aux_statements` (used by
 * `get_item`) and via the `get_aux_statements` tool, which return the
 * unprojected entries with provenance intact.
 *
 * The two-tier exposure pattern keeps compact responses (search results,
 * progression traversals, navigation neighbors) scannable without truncating
 * teachable-content text, while still letting callers retrieve full provenance
 * when they ask for it explicitly.
 *
 * @param aux - A single aux statement entry, typically from
 *   `node.properties.metadata.aux_statements`.
 *
 * @returns A flat object with `role`, `sourceLabel`, and `text` fields, each of
 *   which is `undefined` when absent on the source.
 */
function compactAuxStatement(aux: AuxStatement): Record<string, unknown> {
  return {
    role: aux.role,
    sourceLabel: aux.source_label,
    text: aux.text,
  };
}

/**
 * Reduce a `GraphNode` to a flat, camelCase summary suitable for inclusion in
 * MCP tool responses.
 *
 * This is the canonical "node summary" shape used across the progression,
 * search, and lookup tool outputs: anywhere a node needs to be returned to the
 * model with enough context to identify and describe it, but without the full
 * nested `properties` object the underlying graph carries. Doubles as the
 * snake_case -> camelCase boundary for the MCP API surface, so source
 * properties like `statement_code` are renamed to `statementCode` here.
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
 * `auxStatements` carries a minimal projection (`role`, `sourceLabel`, `text`)
 * of the SFI's `metadata.aux_statements` array: the full entries with
 * provenance fields are accessible through `detailedNode` or
 * `get_aux_statements`. The field is omitted (`undefined`) when no aux
 * statements are attached, so consumers can use simple truthiness checks.
 *
 * `nodeType` is derived from the node's labels via `nodeTypeFromLabels`.
 *
 * Description truncation takes `[0, maxDescription)` of the original string
 * with "..." appended. So the output is at most `maxDescription + 3`
 * characters, not `maxDescription`. Falsy descriptions (`undefined`, `null`,
 * "") pass through unchanged since there's nothing to truncate, and preserving
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
 * @param maxDescription - Maximum description length before the "..." suffix is
 *   appended, in characters. Defaults to 220. Negative values are clamped to
 *   0.
 *
 * @returns A flat, camelCase, JSON-serializable summary of the node. Fields
 *   that don't apply to the node's kind (e.g. `statementCode` on a framework)
 *   come through as `undefined` rather than being omitted, so consumers can
 *   index into them unconditionally.
 */
export function compactNode(
  node: GraphNode,
  maxDescription = 220,
): Record<string, unknown> {
  const metadata = node.properties.metadata ?? {};
  const desc = node.properties.description;
  const auxStatements = Array.isArray(metadata.aux_statements)
    ? metadata.aux_statements.map((aux) => compactAuxStatement(aux))
    : undefined;
  return {
    auxStatements,
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
 * The filter is true when `grade` is omitted or empty, so callsites can pass
 * the user-supplied filter through unconditionally. A provided filter is
 * compared via `normalizeOptionalText` (lowercase, trim, collapse whitespace)
 * against each entry of the node's grade-level array.
 *
 * For SFIs the grade array is read from `properties.grade_level`. For LCs the
 * `grade_level` field is absent, so the function falls back to
 * `metadata.supporting_sfi_grade_level`: the grade levels of the SFI the LC
 * supports. This lets a single grade filter scope both SFIs and LCs without the
 * caller branching on node kind.
 *
 * Equality is whole-string after normalization, not substring so that a filter
 * of "CE1" matches the entry "CE1" but not "CE1-CE2". If the grade-level field
 * is not an array (missing, malformed, or a scalar), the predicate returns
 * `false` rather than coercing.
 *
 * @param node - The graph node to test.
 * @param grade - Optional grade-level filter (e.g. "CE1"). When omitted or
 *   empty, the predicate is true.
 *
 * @returns `true` if `grade` is omitted/empty, or if any entry in the node's
 *   resolved grade-level array equals `grade` after normalization. `false`
 *   otherwise.
 */
export function nodeMatchesGrade(node: GraphNode, grade?: string): boolean {
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
 * `pathSegment` filter against its `canonical_path_key`.
 *
 * The filter is true when `pathSegment` is omitted or empty. A provided filter
 * is matched as a complete `/`-delimited segment of the node's
 * `canonical_path_key`: the path is split on `/` and compared
 * segment-by-segment after `normalizeOptionalText` (lowercase, trim, collapse
 * whitespace) on both sides. Substring matching is intentionally avoided so
 * that a filter of `"week:10"` does not accidentally match `"week:100"`.
 *
 * For SFIs the path key is read from `metadata.canonical_path_key`. For LCs
 * that field is absent, so the predicate falls back to
 * `metadata.supporting_sfi_canonical_path_key`: the path of the SFI the LC
 * supports. This lets a single filter scope both SFIs and LCs without the
 * caller branching on node kind, mirroring the fallback pattern in
 * `compactNode` and `nodeMatchesSourceLabel`. If neither field is present, or
 * either is non-string, the predicate returns `false`.
 *
 * The path-segment vocabulary is framework-specific. A canonical path key like
 * `section:.../substage:palier-2-lecture/week:10/expectation::HASH` exposes
 * segments such as `substage:palier-2-lecture` and `week:10`, but other
 * frameworks may use `unit:3`, `quarter:Q1`, `lesson:5`, or any `key:value`
 * convention they emit at build time. Pass the exact segment including its
 * `key:` prefix.
 *
 * @param node - The graph node to test.
 * @param pathSegment - Optional canonical-path segment filter (e.g.
 *   `"week:10"`, `"unit:3"`). When omitted or empty, the predicate is true.
 *
 * @returns `true` if `pathSegment` is omitted/empty, or if any `/`-delimited
 *   segment of the node's resolved canonical path key equals it after
 *   normalization. `false` otherwise.
 */
export function nodeMatchesPathSegment(
  node: GraphNode,
  pathSegment?: string,
): boolean {
  if (!pathSegment) return true;

  const expected = normalizeOptionalText(pathSegment);

  if (!expected) return true;

  const pathKey =
    node.properties.metadata?.canonical_path_key ??
    node.properties.metadata?.supporting_sfi_canonical_path_key;

  if (typeof pathKey !== "string") return false;

  return pathKey
    .split("/")
    .some((segment) => normalizeOptionalText(segment) === expected);
}

/**
 * Predicate used by `searchItems` to test whether a node matches an optional
 * `sourceLabel` filter.
 *
 * The filter is true when `sourceLabel` is omitted or empty. A provided filter
 * is compared via `normalizeOptionalText` (lowercase, trim, collapse
 * whitespace) against two candidate fields on the node's metadata:
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
 *   `"Orthographe"`). When omitted or empty, the predicate is true.
 *
 * @returns `true` if `sourceLabel` is omitted/empty, or if either of the
 *   candidate fields equals it after normalization. `false` otherwise.
 */
export function nodeMatchesSourceLabel(
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
 * The filter is true when `statementType` is omitted or empty. A provided
 * filter is compared via `normalizeOptionalText` (lowercase, trim, collapse
 * whitespace) against four candidate fields:
 *
 * - `properties.statement_type` — the raw statement type on SFIs (e.g. "Objectif
 *   spécifique").
 * - `properties.normalized_statement_type` — the canonicalized form on SFIs (one
 *   of "Standard", "Standard Grouping", "Other", or an open string).
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
 * @param statementType - Optional statement-type filter (e.g. "Standard",
 *   "Objectif spécifique"). When omitted or empty, the predicate is true.
 *
 * @returns `true` if `statementType` is omitted/empty, or if any of the four
 *   candidate fields equals it after normalization. `false` otherwise.
 */
export function nodeMatchesStatementType(
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
 * The filter is true when `subject` is omitted or empty. A provided filter is
 * compared via `normalizeOptionalText` (lowercase, trim, collapse whitespace)
 * against `properties.academic_subject`, which is first newline-flattened (the
 * source data occasionally embeds newlines in subject strings.
 *
 * Equality is whole-string after normalization, not substring so that a filter
 * of "Mathematics" matches the value "Mathematics" but not "Mathematics and
 * Statistics".
 *
 * Unlike `nodeMatchesStatementType` and `nodeMatchesSourceLabel`, this
 * predicate consults a single field. There is no LC-side fallback: LCs carry
 * their subject implicitly through their supported SFI, so a subject filter on
 * a result set that includes both SFIs and LCs will effectively only match
 * SFIs. If the LC subject filter ever becomes required, mirror the
 * `metadata.supporting_sfi_*` fallback pattern.
 *
 * @param node - The graph node to test.
 * @param subject - Optional academic-subject filter (e.g. "Mathematics",
 *   "Langue et Communication"). When omitted or empty, the predicate is true.
 *
 * @returns `true` if `subject` is omitted/empty, or if the node's
 *   newline-flattened `academic_subject` equals it after normalization. `false`
 *   otherwise.
 */
export function nodeMatchesSubject(node: GraphNode, subject?: string): boolean {
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
 * `LearningComponent` > "unknown". In practice the priority is not observable,
 * because `buildKnowledgeGraphIndexes` throws on any node carrying more than
 * one of those labels but if that invariant is ever relaxed, this function will
 * silently pick the first-listed kind rather than reporting the conflict.
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
