#!/usr/bin/env node
// Create the active cycle's record with its provenance block and header, then
// print its path.
//
//   node .standards/bin/artifact.mjs init VERIFICATION
//   node .standards/bin/artifact.mjs init REVIEW --kind IMPLEMENTATION
//
// Types: SCOPE, ARCHITECTURE, DEVELOPMENT, VERIFICATION, REVIEW,
// DOCUMENTATION, SYNCHRONIZATION. Each record is created under
// `.standards/docs/`. The header comes from the owning role's installed
// template, with the cycle ID filled in; the role writes the rest. Running it
// again for a record that already exists prints the path and changes nothing.
// It never overwrites a different file.

import { TERMINAL_STATES, UsageError, isMain, printProblem, projectRootFor, readText, writeNew } from './lib/core.mjs';
import { ARTIFACT_TYPES, REVIEW_KINDS, TEMPLATE_ROLE, defaultPath, parseProvenance, provenanceBlock } from './lib/records.mjs';
import { validateState } from './lib/state.mjs';
import { omittedDocumentationRecord } from './lib/completion.mjs';

const USAGE = 'Usage: node .standards/bin/artifact.mjs init <TYPE> [--kind IMPLEMENTATION|FINAL_DELIVERABLE]';

function parseArgs(args) {
  const [command, type, ...rest] = args;
  if (command !== 'init' || !ARTIFACT_TYPES.has(type)) throw new UsageError(USAGE);
  const options = { type, kind: null };
  for (let index = 0; index < rest.length; index += 1) {
    const [name, inline] = rest[index].split(/=(.*)/s, 2);
    const key = { '--kind': 'kind' }[name];
    if (!key || options[key] !== null) throw new UsageError(USAGE);
    options[key] = inline ?? rest[(index += 1)] ?? null;
    if (options[key] === null) throw new UsageError(USAGE);
  }
  if ((type === 'REVIEW') !== (options.kind !== null)) {
    throw new UsageError(type === 'REVIEW' ? 'REVIEW needs --kind IMPLEMENTATION or --kind FINAL_DELIVERABLE.'
      : '--kind is only used with REVIEW.');
  }
  if (options.kind !== null && !REVIEW_KINDS.has(options.kind)) throw new UsageError(USAGE);
  return options;
}

// The record header from a role template: the text from the first `# ` title
// after the template's `---` separator up to its first `## ` section. Only
// records whose header carries a visible `Cycle` field get one; scope and
// architecture documents start with just the provenance block.
export function templateHeader(template, cycle, reviewKind) {
  const shape = template.split(/^---$/m)[1] ?? '';
  const title = /^# .*$/m.exec(shape);
  if (!title) return null;
  const rest = shape.slice(title.index);
  const end = /^## /m.exec(rest);
  const header = (end ? rest.slice(0, end.index) : rest).trim();
  if (!header.includes('`Cycle`:')) return null;
  return header.replace(/`<Active Work\.Id>`/g, `\`${cycle}\``)
    .replace(/`ReviewKind`:\s*`[^`]*`/, `\`ReviewKind\`: \`${reviewKind}\``);
}

async function readTemplate(root, type) {
  for (const base of ['.claude/skills', '.agents/skills']) {
    const template = await readText(root, `${base}/${TEMPLATE_ROLE[type]}/template.md`);
    if (template !== null) return template;
  }
  throw new UsageError(`The ${TEMPLATE_ROLE[type]} template is not installed; reinstall STANDARDS.`);
}

async function main(args) {
  const options = parseArgs(args);
  const root = await projectRootFor(import.meta.url);
  const stateText = await readText(root, '.standards/STATE.md');
  if (stateText === null) throw new UsageError('.standards/STATE.md is missing; the runtime is incomplete.');
  const { id, workflowState, cycleMode } = validateState(stateText);
  if (id === 'UNSET' || TERMINAL_STATES.has(workflowState)) {
    throw new UsageError('There is no active cycle. Start one before creating its records.');
  }
  if (cycleMode === 'DOCUMENTATION' && omittedDocumentationRecord(options.type, options.kind)) {
    throw new UsageError(`DOCUMENTATION omits ${options.type}${options.kind ? `/${options.kind}` : ''}; do not create a current-cycle record for an omitted owner.`);
  }
  const relative = defaultPath(options.type, id, options.kind);
  const existing = await readText(root, relative);
  if (existing !== null) {
    const provenance = parseProvenance(existing);
    if (provenance && !provenance.error && provenance.artifact === options.type && provenance.cycle === id
        && provenance.reviewKind === options.kind) {
      process.stdout.write(`${relative}\n`);
      return 0;
    }
    throw new UsageError(`${relative} already exists and is not this cycle's ${options.type} record. `
      + 'Leave it unchanged and ask the user how to resolve the collision.');
  }
  const header = templateHeader(await readTemplate(root, options.type), id, options.kind);
  const block = provenanceBlock(options.type, id, options.kind);
  await writeNew(root, relative, `${block}\n${header ? `\n${header}\n` : ''}`);
  process.stdout.write(`${relative}\n`);
  return 0;
}

if (isMain(import.meta.url)) {
  main(process.argv.slice(2)).then((code) => { process.exitCode = code; },
    (error) => { process.exitCode = printProblem(error); });
}
