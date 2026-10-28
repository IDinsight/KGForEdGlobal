// Shared helpers for the tools installed in `.standards/bin/`. These scripts run
// inside the user's project with plain Node.js, so they must not import
// anything outside `.standards/bin/` or any npm package.
import { execFile } from 'node:child_process';
import { realpathSync } from 'node:fs';
import { lstat, mkdir, readFile, realpath, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const MARKER = '<!-- standards:framework-owned -->';
export const MODES = new Set(['GREENFIELD', 'BROWNFIELD']);
export const CYCLE_MODES = new Set(['UNSET', 'STANDARD', 'EXPEDITED', 'DOCUMENTATION']);
export const COMPLETION_POLICIES = ['NONE', 'FULL_DELIVERABLE', 'IMPLEMENTATION_REVIEWED'];
export const VERIFICATION_CADENCES = ['AFTER_IMPLEMENTATION', 'INCREMENTAL'];
export const STATES = new Set([
  'SCOPING', 'ARCHITECTING', 'AUDITING', 'DEVELOPING', 'TESTING',
  'REVIEWING_IMPLEMENTATION', 'DOCUMENTING', 'REVIEWING_FINAL',
  'SYNCHRONIZING', 'AWAITING_USER_SIGNOFF', 'SIGNED_OFF', 'CANCELLED',
]);
export const TERMINAL_STATES = new Set(['SIGNED_OFF', 'CANCELLED']);
export const FAILURE_TYPES = new Set([
  'SCOPING', 'ARCHITECTURE', 'PROJECT_CONTEXT', 'IMPLEMENTATION',
  'VERIFICATION', 'DOCUMENTATION', 'REVIEW', 'SYNCHRONIZATION',
]);
export const HANDOFF_KINDS = new Set([
  'INITIAL', 'FORWARD', 'CHECKPOINT', 'FAILURE', 'RESUME', 'PROMOTE', 'USER_REWORK',
  'NEW_CYCLE', 'SIGNOFF', 'CANCEL', 'COMPLETION_CHANGE',
]);
// Characters a cycle ID may use. Provenance blocks from any cycle are read
// with this rule.
export const CYCLE_ID = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
// The exact shape `cycle.mjs new` generates: a lowercase slug of the request,
// the UTC time, and eight random hex digits, e.g.
// `add-user-search-20260927T190146Z-7bef0f04`.
export const GENERATED_CYCLE_ID = /^[a-z0-9]+(?:-[a-z0-9]+)*-\d{8}T\d{6}Z-[0-9a-f]{8}$/;

// Tools live at `<project>/.standards/bin/<tool>.mjs`, so the project root is
// two directories above the script, whatever the caller's working directory.
export async function projectRootFor(scriptUrl) {
  const root = await resolveReal(fileURLToPath(new URL('../../', scriptUrl)));
  const protocol = await readText(root, '.standards/PROTOCOL.md');
  if (!protocol?.includes(MARKER)) {
    throw new UsageError(`No installed STANDARDS runtime found at ${root}`);
  }
  return root.replace(/[\\/]$/, '');
}

// Whether the script at `url` is the one Node was asked to run. Compares real
// paths: Node resolves symlinks in the running module's URL but not in
// process.argv[1], so a plain comparison fails when the project is reached
// through a symlink and the tool would silently do nothing.
export function isMain(url) {
  if (!process.argv[1]) return false;
  try {
    return realpathSync(process.argv[1]) === realpathSync(fileURLToPath(url));
  } catch {
    return false;
  }
}

// A user mistake (bad arguments, wrong project); printed without a stack trace.
export class UsageError extends Error {}

// The real location of a path, following symlinks in the part that exists, so
// `/tmp/project/x` and `/private/tmp/project/x` compare equal. The path itself
// does not have to exist.
export async function resolveReal(target) {
  try {
    return await realpath(target);
  } catch (error) {
    if (error.code !== 'ENOENT' && error.code !== 'ENOTDIR') throw error;
    const parent = path.dirname(target);
    return parent === target ? target : path.join(await resolveReal(parent), path.basename(target));
  }
}

// Resolve a user-supplied path to a project-relative one. A relative path is
// tried from the current folder first, then from the project root.
export async function projectRelative(root, file) {
  const fromCwd = path.relative(root, await resolveReal(path.resolve(file))).split(path.sep).join('/');
  const inside = (relative) => relative && !relative.startsWith('..') && !path.isAbsolute(relative);
  if (inside(fromCwd) && (path.isAbsolute(file) || await exists(root, fromCwd))) return fromCwd;
  const fromRoot = path.relative(root, path.resolve(root, file)).split(path.sep).join('/');
  if (!path.isAbsolute(file) && inside(fromRoot)) return fromRoot;
  throw new UsageError(`${file} is outside the project.`);
}

export async function readText(root, relative) {
  try {
    return await readFile(path.join(root, relative), 'utf8');
  } catch (error) {
    if (error.code === 'ENOENT' || error.code === 'ENOTDIR') return null;
    throw error;
  }
}

export async function exists(root, relative) {
  try {
    await lstat(path.join(root, relative));
    return true;
  } catch (error) {
    if (error.code === 'ENOENT' || error.code === 'ENOTDIR') return false;
    throw error;
  }
}

// Refuse to write through a symlink or into a path whose parent is a file, so a
// tool can never write outside the project. Creates missing parent folders.
async function prepareParents(root, relative) {
  let current = root;
  for (const part of relative.split('/').slice(0, -1)) {
    current = path.join(current, part);
    let entry;
    try {
      entry = await lstat(current);
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
      await mkdir(current);
      continue;
    }
    if (entry.isSymbolicLink() || !entry.isDirectory()) {
      throw new UsageError(`Cannot write ${relative}: ${path.relative(root, current)} is not a normal folder`);
    }
  }
  const target = path.join(root, relative);
  try {
    if ((await lstat(target)).isSymbolicLink()) throw new UsageError(`Cannot write ${relative}: it is a symbolic link`);
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
}

// Create a new file, failing if anything already exists at that path.
export async function writeNew(root, relative, contents) {
  await prepareParents(root, relative);
  await writeFile(path.join(root, relative), contents, { flag: 'wx' });
}

// Run git in the project. Returns null when git is missing, the project is not
// a repository, or the command fails, so callers can skip git-based checks.
export function git(root, args) {
  return new Promise((resolve) => {
    execFile('git', ['-C', root, ...args], { maxBuffer: 64 * 1024 * 1024, encoding: 'utf8' },
      (error, stdout) => resolve(error ? null : stdout));
  });
}

// The committed version of a project file, or null when there is no commit,
// no repository, or the file is not in the last commit. A file moved since the
// last commit (with `git mv`) is followed back to its committed path.
export async function committedText(root, relative) {
  const text = await git(root, ['show', `HEAD:./${relative}`]);
  if (text !== null) return text;
  const changes = (await git(root, ['diff', '--relative', '-M', '--name-status', '-z', 'HEAD'])) ?? '';
  const parts = changes.split('\0');
  for (let index = 0; index < parts.length - 2; index += 1) {
    if (/^R\d*$/.test(parts[index]) && parts[index + 2] === relative) {
      return git(root, ['show', `HEAD:./${parts[index + 1]}`]);
    }
  }
  return null;
}

// Every `Name`: `value` pair in a piece of Markdown, in order. Each value is a
// Markdown code span, so it may use more backticks to hold a backtick, as in
// `Request`: ``Add a `--json` flag``.
export function fieldPairs(text) {
  return [...text.matchAll(/`([A-Za-z][A-Za-z ]*)`:\s*(`+)(?!`)([\s\S]*?)(?<!`)\2(?!`)/g)]
    .map((match) => [match[1], /^ [\s\S]*\S[\s\S]* $/.test(match[3]) ? match[3].slice(1, -1) : match[3]]);
}

// Remove a UTF-8 byte order mark, which some editors add.
export const withoutBom = (text) => text.replace(/^\uFEFF/, '');

// Report a failure and return the process exit code.
export function printProblem(error) {
  process.stderr.write(`${error instanceof UsageError ? '' : 'Unexpected error: '}${error.message}\n`);
  return 1;
}
