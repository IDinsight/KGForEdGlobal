/** @file This file contains general utility functions. */

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
export function countBy(
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
export function normalizeOptionalText(
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
export function normalizeWhitespace(value: string): string {
  return value.replaceAll(/\s+/g, " ").trim();
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
export function uniqueSorted(
  values: Array<string | null | undefined>,
): string[] {
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
