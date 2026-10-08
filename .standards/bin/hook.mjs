#!/usr/bin/env node
// Stop hook for Claude Code and Codex. The client sends the event as JSON on
// stdin; exit code 2 with a message on stderr sends the agent back (both
// clients support this), and exit code 0 lets it stop.
//
//   node .standards/bin/hook.mjs stop
//
// When workflow files changed, run the STANDARDS check and, if it finds
// problems, send the agent back once per turn with them. It does not hold the
// current state's own COMPLETE records to full acceptance coverage, since the
// turn may have ended with a handoff to that state's owner, who reconciles
// them. During recovery it holds the record of the state whose role made the
// last handoff instead, except while in SCOPING.
import { createHash } from 'node:crypto';
import { mkdir, readdir, rm, stat, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

import { runCheck } from './check.mjs';
import { git, isMain, projectRootFor } from './lib/core.mjs';

const BLOCK = 2;
const MARKER_DIR = path.join(os.tmpdir(), 'standards-hooks');
const SHOWN_PROBLEMS = 15;

async function readInput() {
  let text = '';
  for await (const chunk of process.stdin) text += chunk;
  if (!text.trim()) return {};
  try {
    const value = JSON.parse(text);
    return value && typeof value === 'object' ? value : {};
  } catch {
    return {};
  }
}

// Only check when the agent may have changed workflow files: uncommitted
// changes under `.standards/` or in any Markdown file. Outside git there is no
// way to tell, so always check.
async function workflowFilesChanged(root) {
  const status = await git(root, ['status', '--porcelain', '-z', '--untracked-files=all', '--', '.standards', '*.md']);
  return status === null || status.length > 0;
}

// Fallback for clients without `stop_hook_active`: record that this turn was
// already sent back, so the next stop goes through. Returns 'new' the first
// time, 'seen' after that, and 'unavailable' when the marker cannot be written
// (the caller then does not block, to avoid a loop).
async function markTurn(root, input) {
  const turn = input.turn_id ?? input.prompt_id ?? 'session';
  const key = createHash('sha256').update(`${root}\0${input.session_id ?? ''}\0${turn}`).digest('hex').slice(0, 32);
  try {
    await mkdir(MARKER_DIR, { recursive: true });
    await removeOldMarkers();
    await writeFile(path.join(MARKER_DIR, key), '', { flag: 'wx' });
    return 'new';
  } catch (error) {
    return error.code === 'EEXIST' ? 'seen' : 'unavailable';
  }
}

async function removeOldMarkers() {
  const cutoff = Date.now() - 24 * 60 * 60 * 1000;
  for (const name of await readdir(MARKER_DIR)) {
    const file = path.join(MARKER_DIR, name);
    try {
      if ((await stat(file)).mtimeMs < cutoff) await rm(file, { force: true });
    } catch {
      // Another hook may have removed it already.
    }
  }
}

export function stopMessage(problems) {
  const shown = problems.slice(0, SHOWN_PROBLEMS).map(({ file, message }) => `- ${file}: ${message}`);
  const more = problems.length - shown.length;
  return [
    'STANDARDS check found problems in the workflow files:',
    ...shown,
    ...(more > 0 ? [`- ...and ${more} more. Run \`node .standards/bin/check.mjs\` to see them all.`] : []),
    '',
    'Do not guess a repair. If a problem is in work your current role owns, fix it now. If another role '
      + 'owns it, route it as .standards/PROTOCOL.md describes. If you are not sure, or you are Navigator, '
      + 'report the problems to the user and stop.',
  ].join('\n');
}

async function onStop(root, input) {
  // Claude Code and Codex both send `stop_hook_active`, which is true when the
  // agent is already continuing because a Stop hook sent it back. That flag is
  // the once-per-turn guard; the marker file is only a fallback for clients
  // that do not send it.
  if (input.stop_hook_active === true) return 0;
  if (!(await workflowFilesChanged(root))) return 0;
  const { problems } = await runCheck(root, { atTurnEnd: true });
  if (problems.length === 0) return 0;
  if (typeof input.stop_hook_active !== 'boolean' && await markTurn(root, input) !== 'new') return 0;
  process.stderr.write(`${stopMessage(problems)}\n`);
  return BLOCK;
}

async function main(args) {
  if (args.length !== 1 || args[0] !== 'stop') {
    process.stderr.write('Usage: node .standards/bin/hook.mjs stop\n');
    return 1;
  }
  const input = await readInput();
  const root = await projectRootFor(import.meta.url);
  return onStop(root, input);
}

if (isMain(import.meta.url)) {
  // A failing hook must never trap the agent: report the error without blocking.
  main(process.argv.slice(2)).then((code) => { process.exitCode = code; }, (error) => {
    process.stderr.write(`STANDARDS hook error: ${error.message}\n`);
    process.exitCode = 1;
  });
}
