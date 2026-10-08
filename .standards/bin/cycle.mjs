#!/usr/bin/env node
// Generate a new cycle ID and print it.
//
//   node .standards/bin/cycle.mjs new --request "Add user search by name"
//
// The ID is `<request-slug>-<UTC time>-<random hex>`. The tool checks that no
// existing cycle record uses it. It changes no files; the agent records the
// printed ID in `Active Work.Id`.
import { randomBytes } from 'node:crypto';

import { GENERATED_CYCLE_ID, TERMINAL_STATES, UsageError, exists, isMain, printProblem, projectRootFor, readText } from './lib/core.mjs';
import { RECORDS_ROOT, defaultPath, scanArtifacts } from './lib/records.mjs';
import { validateState } from './lib/state.mjs';

const USAGE = 'Usage: node .standards/bin/cycle.mjs new --request "<request text>"';

function parseArgs(args) {
  if (args[0] !== 'new') throw new UsageError(USAGE);
  let request = null;
  for (let index = 1; index < args.length; index += 1) {
    const [name, inline] = args[index].split(/=(.*)/s, 2);
    if (name !== '--request' || request !== null) throw new UsageError(USAGE);
    request = inline ?? args[(index += 1)];
  }
  if (!request?.trim()) throw new UsageError(`A request is required.\n${USAGE}`);
  return { request };
}

// Lowercase ASCII words from the request, at most 40 characters.
export function slugFor(request) {
  const words = request.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase()
    .split(/[^a-z0-9]+/).filter(Boolean);
  let slug = '';
  for (const word of words) {
    const next = slug ? `${slug}-${word}` : word;
    if (next.length > 40) break;
    slug = next;
  }
  return slug || (words[0]?.slice(0, 40) ?? '') || 'cycle';
}

function timestamp(date) {
  return date.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
}

// A cycle ID may be generated only when no cycle is active: before the first
// cycle (`Active Work.Id` is UNSET) or after sign-off or retained cancellation.
function assertCanAllocate(stateText) {
  const { id, workflowState } = validateState(stateText);
  if (id !== 'UNSET' && !TERMINAL_STATES.has(workflowState)) {
    throw new UsageError(`Cycle ${id} is still active (${workflowState}). A new cycle ID is only generated `
      + 'before the first cycle or after sign-off or cancellation.');
  }
}

async function isUsed(root, id, artifacts) {
  if (artifacts.some((artifact) => artifact.provenance.cycle === id)) return true;
  // Every path `artifact init` could create for this ID, plus the reviews folder.
  const paths = [`${RECORDS_ROOT}/reviews/${id}`,
    ...['SCOPE', 'ARCHITECTURE', 'DEVELOPMENT', 'VERIFICATION', 'DOCUMENTATION', 'SYNCHRONIZATION']
      .map((type) => defaultPath(type, id))];
  for (const relative of paths) if (await exists(root, relative)) return true;
  return false;
}

async function main(args) {
  const { request } = parseArgs(args);
  const root = await projectRootFor(import.meta.url);
  const stateText = await readText(root, '.standards/STATE.md');
  if (stateText === null) throw new UsageError('.standards/STATE.md is missing; the runtime is incomplete.');
  if (/^<<<<<<< /m.test(stateText)) throw new UsageError('.standards/STATE.md has unresolved merge conflicts.');
  assertCanAllocate(stateText);
  const slug = slugFor(request);
  const artifacts = (await scanArtifacts(root)).filter((artifact) => !artifact.provenance.error);
  for (let attempt = 0; attempt < 5; attempt += 1) {
    const id = `${slug}-${timestamp(new Date())}-${randomBytes(4).toString('hex')}`;
    if (!GENERATED_CYCLE_ID.test(id) || await isUsed(root, id, artifacts)) continue;
    process.stdout.write(`${id}\n`);
    return 0;
  }
  throw new UsageError('Could not generate an unused cycle ID; try again.');
}

if (isMain(import.meta.url)) {
  main(process.argv.slice(2)).then((code) => { process.exitCode = code; },
    (error) => { process.exitCode = printProblem(error); });
}
