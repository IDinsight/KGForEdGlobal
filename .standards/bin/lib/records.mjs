// Cycle-owned workflow artifacts: provenance blocks, fixed paths, header
// fields, and the identifiers (AC, DEV, F, D, DOC) used inside them.
import { readdir, readFile } from 'node:fs/promises';
import path from 'node:path';

import { CYCLE_ID, fieldPairs, git } from './core.mjs';
import { requiredCompletionPhases } from './completion.mjs';

export const ARTIFACT_TYPES = new Set([
  'SCOPE', 'ARCHITECTURE', 'DEVELOPMENT', 'VERIFICATION', 'REVIEW', 'DOCUMENTATION', 'SYNCHRONIZATION',
]);
export const REVIEW_KINDS = new Set(['IMPLEMENTATION', 'FINAL_DELIVERABLE']);
// Role whose installed `template.md` defines each artifact's header.
export const TEMPLATE_ROLE = {
  SCOPE: 'scoper', ARCHITECTURE: 'architect', DEVELOPMENT: 'developer', VERIFICATION: 'tester',
  REVIEW: 'reviewer', DOCUMENTATION: 'documenter', SYNCHRONIZATION: 'synchronizer',
};
// Record-local identifier prefix for each record type.
export const LOCAL_PREFIX = { DEVELOPMENT: 'DEV', REVIEW: 'F', SYNCHRONIZATION: 'D', DOCUMENTATION: 'DOC' };
// Identifiers that exist in more than one record, so a reference from another
// record must name the file: `.standards/docs/reviews/<id>/implementation.md#F-003`.
export const QUALIFIED_PREFIXES = ['F', 'D', 'DOC'];
// Folder that holds every cycle record `artifact init` creates.
export const RECORDS_ROOT = '.standards/docs';

// The protocol-fixed location of a record, or null when the role may instead
// keep it in an existing project document (scope, architecture).
export function fixedPath(artifact, cycle, reviewKind) {
  switch (artifact) {
    case 'DEVELOPMENT': return `${RECORDS_ROOT}/development/${cycle}.md`;
    case 'VERIFICATION': return `${RECORDS_ROOT}/verification/${cycle}.md`;
    case 'DOCUMENTATION': return `${RECORDS_ROOT}/documentation/${cycle}.md`;
    case 'SYNCHRONIZATION': return `${RECORDS_ROOT}/synchronization/${cycle}.md`;
    case 'REVIEW':
      return `${RECORDS_ROOT}/reviews/${cycle}/${reviewKind === 'FINAL_DELIVERABLE' ? 'final-deliverable' : 'implementation'}.md`;
    default: return null;
  }
}

// The records a cycle keeps at fixed paths, with the state that completes each
// (PROTOCOL.md, Workflow Artifact Provenance).
export function fixedRecords(cycle) {
  return [
    { artifact: 'VERIFICATION', reviewKind: null, phase: 'TESTING' },
    { artifact: 'REVIEW', reviewKind: 'IMPLEMENTATION', phase: 'REVIEWING_IMPLEMENTATION' },
    { artifact: 'DOCUMENTATION', reviewKind: null, phase: 'DOCUMENTING' },
    { artifact: 'REVIEW', reviewKind: 'FINAL_DELIVERABLE', phase: 'REVIEWING_FINAL' },
    { artifact: 'SYNCHRONIZATION', reviewKind: null, phase: 'SYNCHRONIZING' },
  ].map((record) => ({ ...record, path: fixedPath(record.artifact, cycle, record.reviewKind) }));
}

// Presence/completion requirements are policy-dependent. Always use
// fixedRecords for discovery so an omitted phase cannot hide an existing file.
export function requiredCompletionRecords(cycle, cycleMode, policy) {
  const phases = requiredCompletionPhases(cycleMode, policy);
  return fixedRecords(cycle).filter((record) => phases?.includes(record.phase));
}

// A record's name in messages, e.g. "REVIEW IMPLEMENTATION".
export const recordName = (artifact, reviewKind) => (reviewKind ? `${artifact} ${reviewKind}` : artifact);

// Default location for a new artifact created by `artifact init`.
export function defaultPath(artifact, cycle, reviewKind) {
  if (artifact === 'SCOPE') return `${RECORDS_ROOT}/scope/${cycle}.md`;
  if (artifact === 'ARCHITECTURE') return `${RECORDS_ROOT}/specs/${cycle}.md`;
  return fixedPath(artifact, cycle, reviewKind);
}

export function provenanceBlock(artifact, cycle, reviewKind) {
  return ['<!-- STANDARDS', `Artifact: ${artifact}`, `Cycle: ${cycle}`,
    ...(artifact === 'REVIEW' ? [`ReviewKind: ${reviewKind}`] : []), '-->'].join('\n');
}

// Read the provenance block at the start of a file. Returns null when the file
// has none, `{ error }` when it starts like one but is malformed, and
// `{ artifact, cycle, reviewKind }` otherwise.
export function parseProvenance(text) {
  const start = text.replace(/^\uFEFF/, '');
  if (!/^<!--\s*STANDARDS(\s|$)/.test(start)) return null;
  const end = start.indexOf('-->');
  if (end === -1) return { error: 'the provenance block is never closed with `-->`' };
  const lines = start.slice(0, end).split(/\r?\n/).slice(1).map((line) => line.trim()).filter(Boolean);
  const values = {};
  for (const line of lines) {
    const match = /^(Artifact|Cycle|ReviewKind):\s*(\S+)$/.exec(line);
    if (!match) return { error: `unexpected provenance line \`${line}\`` };
    if (values[match[1]]) return { error: `\`${match[1]}\` appears twice in the provenance block` };
    values[match[1]] = match[2];
  }
  if (!ARTIFACT_TYPES.has(values.Artifact)) return { error: `unknown Artifact \`${values.Artifact ?? ''}\`` };
  if (!values.Cycle || !CYCLE_ID.test(values.Cycle)) return { error: `invalid Cycle \`${values.Cycle ?? ''}\`` };
  if (values.Artifact === 'REVIEW' ? !REVIEW_KINDS.has(values.ReviewKind) : values.ReviewKind) {
    return { error: values.Artifact === 'REVIEW'
      ? 'a REVIEW block needs `ReviewKind: IMPLEMENTATION` or `ReviewKind: FINAL_DELIVERABLE`'
      : 'only REVIEW blocks may have `ReviewKind`' };
  }
  return { artifact: values.Artifact, cycle: values.Cycle, reviewKind: values.ReviewKind ?? null };
}

// Header values still showing template choices, e.g. `A | B` or `<name>`.
// Shared by the checker and invocation discovery, including style selectors.
export function isUnfilledHeaderValue(value) {
  return value.includes(' | ') || /^<.*>$/.test(value);
}

// The `Name`: `value` fields between a record's `# Title` and its first `##`
// section, e.g. `Cycle`, `Status`, `Mode`.
export function headerFields(text) {
  const fields = {};
  for (const [name, value] of headerFieldPairs(text)) if (!(name in fields)) fields[name] = value;
  return fields;
}

// Keep occurrences when a field's contract requires exactly one value.
export function headerFieldPairs(text) {
  const body = text.replace(/^\uFEFF?<!--[\s\S]*?-->/, '');
  const title = /^# .*$/m.exec(body);
  if (!title) return [];
  const rest = body.slice(title.index + title[0].length);
  const next = /^## /m.exec(rest);
  const header = next ? rest.slice(0, next.index) : rest;
  return fieldPairs(header);
}

const SKIPPED_FOLDERS = new Set(['.git', 'node_modules', '.standards', '.agents', '.claude', '.codex']);

async function walkMarkdown(root, relative = '') {
  const found = [];
  let entries;
  try {
    entries = await readdir(path.join(root, relative), { withFileTypes: true });
  } catch {
    return found; // An unreadable folder cannot hold records we can check.
  }
  for (const entry of entries) {
    const child = relative ? `${relative}/${entry.name}` : entry.name;
    if (entry.isDirectory() && !SKIPPED_FOLDERS.has(entry.name)) found.push(...await walkMarkdown(root, child));
    else if (entry.isFile() && entry.name.endsWith('.md')) found.push(child);
  }
  return found;
}

// Markdown files that may hold workflow artifacts: git-tracked and untracked
// (but not ignored) files when git is available, otherwise a folder walk.
// Framework and client folders are skipped because their templates contain
// example provenance blocks; inside `.standards/`, only the records folder is
// read.
export async function markdownFiles(root) {
  const listed = await git(root, ['ls-files', '-z', '--cached', '--others', '--exclude-standard', '--', '*.md']);
  const files = listed === null
    ? [...await walkMarkdown(root), ...await walkMarkdown(root, RECORDS_ROOT)]
    : listed.split('\0').filter(Boolean);
  const scanned = (file) => file.startsWith(`${RECORDS_ROOT}/`) || !SKIPPED_FOLDERS.has(file.split('/')[0]);
  return [...new Set(files)].filter(scanned).sort();
}

// Every file whose first line opens a STANDARDS provenance block. A file that
// cannot be read (no permission, a symlink loop, too large) is skipped and
// passed to `onUnreadable`, so one bad file never stops the scan.
export async function scanArtifacts(root, { onUnreadable = () => {} } = {}) {
  const artifacts = [];
  for (const file of await markdownFiles(root)) {
    let text;
    try {
      text = await readFile(path.join(root, file), 'utf8');
    } catch (error) {
      if (error.code !== 'ENOENT' && error.code !== 'EISDIR') onUnreadable(file, error);
      continue;
    }
    const provenance = parseProvenance(text);
    if (provenance) artifacts.push({ path: file, text, provenance });
  }
  return artifacts;
}

// Identifier numbers written with a prefix, e.g. `AC-007`. A `#` before the
// identifier marks a reference into another file, which is not counted as a
// local identifier.
export function identifierNumbers(text, prefix) {
  const escapedPrefix = prefix.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const pattern = new RegExp(`(?<![A-Za-z0-9_#-])${escapedPrefix}-(\\d{3,})(?![0-9])`, 'g');
  return [...text.matchAll(pattern)].map((match) => Number(match[1]));
}

export function formatId(prefix, number) {
  return `${prefix}-${String(number).padStart(3, '0')}`;
}

// Record-local identifiers defined as `### PREFIX-NNN` headings.
export function headingIds(text, prefix) {
  return [...text.matchAll(new RegExp(`^###\\s+(${prefix}-\\d{3,})\\b`, 'gm'))].map((match) => match[1]);
}

// References that name another record: `path/to/file.md#F-003`.
export function qualifiedReferences(text) {
  return [...text.matchAll(/([A-Za-z0-9_./-]+\.md)#((?:F|D|DOC)-\d{3,})(?![0-9])/g)]
    .map((match) => ({ file: match[1], id: match[2] }));
}

// A Markdown link whose text is the identifier its target points at, e.g.
// `[F-001](../reviews/<id>/implementation.md#F-001)`, already names its record,
// so its text is not a bare mention. Blank that text; a link whose text and
// target disagree is left alone and still reported.
export function withoutLinkLabels(text) {
  return text.replace(/\[((?:F|D|DOC)-\d{3,})\]\(([^)\s]*\.md#((?:F|D|DOC)-\d{3,}))\)/g,
    (link, label, target, id) => (label === id ? `[](${target})` : link));
}

// Bare identifiers with one of the given prefixes (no `#` or path before them).
export function bareIds(text, prefixes) {
  const pattern = new RegExp(`(?<![A-Za-z0-9_#./-])((?:${prefixes.join('|')})-\\d{3,})(?![0-9])`, 'g');
  return [...new Set([...text.matchAll(pattern)].map((match) => match[1]))];
}

// Shared by record section parsing and review comment masking. Closing fences
// use only the opening character and may have trailing whitespace, not an info
// string. Backtick info strings cannot themselves contain backticks.
export function fenceAfterLine(line, fence = null) {
  const marker = /^ {0,3}(`{3,}|~{3,})([^\r\n]*)$/.exec(line);
  if (fence === null) {
    return marker && (marker[1][0] === '~' || !marker[2].includes('`')) ? marker[1] : null;
  }
  return marker && marker[1][0] === fence[0] && marker[1].length >= fence.length
    && /^[ \t]*$/.test(marker[2]) ? null : fence;
}

// The lines of a Markdown file, each marked when it sits inside a fenced code
// block, so a "## " line in an example is not read as a section heading.
function markdownLines(text) {
  let fence = null;
  return text.split(/\r?\n/).map((line) => {
    const previous = fence;
    fence = fenceAfterLine(line, fence);
    return { line, fenced: previous !== null || fence !== null };
  });
}

// The lowercased name of a "## " heading outside code blocks, or null. A
// closing "#" sequence and a trailing colon are not part of the name.
function sectionName({ line, fenced }) {
  const name = fenced ? null : /^##\s+(.+?)\s*$/.exec(line)?.[1];
  return name === undefined || name === null ? null
    : name.replace(/\s+#+$/, '').replace(/:$/, '').trim().toLowerCase();
}

// A record section, excluding fenced examples and stopping at the next H2.
export function recordSection(text, name, { includeFenced = false } = {}) {
  return recordSections(text, name, { includeFenced })[0] ?? '';
}

// Retain repeated sections so contracts requiring one assessment cannot silently
// accept the first of two contradictory conclusions.
export function recordSections(text, name, { includeFenced = false } = {}) {
  const lines = markdownLines(text);
  return lines.flatMap((entry, start) => {
    if (sectionName(entry) !== name.toLowerCase()) return [];
    const next = lines.findIndex((candidate, index) => index > start && sectionName(candidate) !== null);
    return [lines.slice(start + 1, next === -1 ? undefined : next)
      .map(({ line, fenced }) => (fenced && !includeFenced ? '' : line)).join('\n')];
  });
}

// The first fields in each step are authoritative; later self-check prose may
// mention fields such as Status with a different meaning.
export function developmentSteps(text) {
  const lines = markdownLines(text);
  const raw = lines.map(({ line }) => line).join('\n');
  const body = lines.map(({ line, fenced }) => (fenced ? ' '.repeat(line.length) : line)).join('\n');
  const headings = [...body.matchAll(/^###\s+(DEV-\d{3,})\b.*$/gm)];
  return headings.map((match, index) => {
    const chunk = body.slice(match.index + match[0].length, headings[index + 1]?.index).split(/^## /m)[0];
    const fields = {};
    for (const [name, value] of fieldPairs(chunk)) if (!(name in fields)) fields[name] = value;
    return { id: match[1], fields, text: raw.slice(match.index + match[0].length, match.index + match[0].length + chunk.length) };
  });
}

// Everything before the "## Previous Cycles" heading. That section is the
// document's last one: everything under it, including headings the moved
// content kept, is earlier cycles' history, not current coverage.
export function withoutPreviousCycles(text) {
  const lines = markdownLines(text);
  const start = lines.findIndex((entry) => sectionName(entry) === 'previous cycles');
  return (start === -1 ? lines : lines.slice(0, start)).map(({ line }) => line).join('\n');
}

// The IDs a list item defines: one or more `AC-NNN` right after the list
// marker, before ":" or a dash. Covers "- `AC-001`: ...", "1. AC-001 — ...",
// "- [ ] **AC-001**: ...", and grouped "- `AC-003`, `AC-004`: Replaced ...".
function definedIds(line) {
  const id = '[*_`]*AC-\\d{3,}[*_`]*';
  const match = new RegExp(`^\\s*(?:[-*+]|\\d+[.)])\\s+(?:\\[[ xX]\\]\\s+)?(${id}(?:\\s*(?:,|and|&)\\s*${id})*)\\s*(?::|—|–|-\\s)`)
    .exec(line);
  return match ? match[1].match(/AC-\d{3,}/g) : [];
}

// Acceptance IDs a scope mentions anywhere before "## Previous Cycles" and
// outside code blocks, whether or not the mention is a definition. An ID found
// here but missing from the inventory below is written in a form that does not
// define it, such as a table row or a heading.
export function acceptanceMentions(text) {
  const lines = markdownLines(withoutPreviousCycles(text)).filter((entry) => !entry.fenced);
  return bareIds(lines.map(({ line }) => line).join('\n'), ['AC']);
}

// Acceptance conditions in a scope document. Definitions are list items that
// start with the identifier, e.g. "- `AC-001`: ...", outside code blocks
// (PROTOCOL.md, Acceptance Traceability).
// Items under "## Retired Acceptance Identifiers" are retired; everything from
// "## Previous Cycles" to the end belongs to earlier cycles that reused this
// document.
export function acceptanceInventory(text) {
  const inventory = { current: [], retired: [], previous: [], duplicates: [] };
  let section = 'current';
  for (const entry of markdownLines(text)) {
    const name = sectionName(entry);
    if (name !== null && section !== 'previous') {
      section = name === 'retired acceptance identifiers' ? 'retired'
        : name === 'previous cycles' ? 'previous' : 'current';
      continue;
    }
    if (entry.fenced) continue;
    for (const id of definedIds(entry.line)) {
      if (inventory[section].includes(id)) inventory.duplicates.push(id);
      else inventory[section].push(id);
    }
  }
  return inventory;
}
