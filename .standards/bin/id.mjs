#!/usr/bin/env node
// Print the next free identifier for a record.
//
//   node .standards/bin/id.mjs next AC .standards/docs/scope/<cycle-id>.md
//   node .standards/bin/id.mjs next DEV .standards/docs/development/<cycle-id>.md
//   node .standards/bin/id.mjs next F .standards/docs/reviews/<cycle-id>/implementation.md
//   node .standards/bin/id.mjs next D .standards/docs/synchronization/<cycle-id>.md
//   node .standards/bin/id.mjs next DOC .standards/docs/documentation/<cycle-id>.md
//
// The next number is one more than the highest number used in the file now or
// in its last committed version, including retired IDs and earlier cycles'
// conditions, so an identifier is never handed out twice. It does not edit the
// file.

import { UsageError, committedText, isMain, printProblem, projectRelative, projectRootFor, readText } from './lib/core.mjs';
import { formatId, identifierNumbers, parseProvenance } from './lib/records.mjs';

const USAGE = 'Usage: node .standards/bin/id.mjs next <AC|DEV|F|D|DOC> <file>';
// Which record type may hold each prefix. AC IDs live in the scope, which may
// be an unmarked project document.
const ARTIFACT_FOR = {
  AC: 'SCOPE', DEV: 'DEVELOPMENT', F: 'REVIEW', D: 'SYNCHRONIZATION', DOC: 'DOCUMENTATION',
};

export function nextId(prefix, texts) {
  const numbers = texts.filter((text) => text !== null).flatMap((text) => identifierNumbers(text, prefix));
  return formatId(prefix, numbers.length ? Math.max(...numbers) + 1 : 1);
}

async function main(args) {
  const [command, prefix, file, ...extra] = args;
  if (command !== 'next' || !ARTIFACT_FOR[prefix] || !file || extra.length) throw new UsageError(USAGE);
  const root = await projectRootFor(import.meta.url);
  const relative = await projectRelative(root, file);
  const text = await readText(root, relative);
  if (text === null) throw new UsageError(`${relative} does not exist. Create it before numbering its entries.`);
  const provenance = parseProvenance(text);
  if (provenance?.error) throw new UsageError(`${relative} has a malformed provenance block: ${provenance.error}.`);
  const expected = ARTIFACT_FOR[prefix];
  if (provenance ? provenance.artifact !== expected : prefix !== 'AC') {
    throw new UsageError(`${prefix} identifiers belong in a ${expected} record, but ${relative} is `
      + `${provenance ? `a ${provenance.artifact} record` : 'not a STANDARDS record'}.`);
  }
  process.stdout.write(`${nextId(prefix, [text, await committedText(root, relative)])}\n`);
  return 0;
}

if (isMain(import.meta.url)) {
  main(process.argv.slice(2)).then((code) => { process.exitCode = code; },
    (error) => { process.exitCode = printProblem(error); });
}
