#!/usr/bin/env node
// Discover invocation options without modifying the project or applying choices.
//   node .standards/bin/invocation.mjs developer --client codex --json
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { isMain, printProblem, UsageError } from './lib/core.mjs';
import { discoverInvocation } from './lib/invocation.mjs';

const USAGE = 'Usage: node .standards/bin/invocation.mjs <role> [--client codex|claude] [--json] [--project <path>]';

function parseArgs(args) {
  const options = { json: false };
  for (let index = 0; index < args.length; index++) {
    const [name, inline] = args[index].split(/=(.*)/s, 2);
    if (name === '--json' && inline === undefined && !options.json) {
      options.json = true;
    } else if (['--client', '--project'].includes(name) && !Object.hasOwn(options, name.slice(2))) {
      const value = inline ?? args[++index];
      if (!value || value.startsWith('--')) throw new UsageError(USAGE);
      options[name.slice(2)] = value;
    } else if (!name.startsWith('-') && !options.role && inline === undefined) options.role = name;
    else throw new UsageError(USAGE);
  }
  if (!options.role || (options.client && !['codex', 'claude'].includes(options.client))) throw new UsageError(USAGE);
  return options;
}

function styleText(style) {
  if (style.selector === null) return style.id + ' (' + style.selectorReason + ')';
  return style.id + (style.selector === style.id ? '' : ' (invoke as ' + style.selector + ')');
}

function selectionText(selection) {
  return selection.status + (selection.value === null ? '' : ': ' + selection.value)
    + (selection.reason ? ' (' + selection.reason + ')' : '');
}

export function formatInvocation(result) {
  const lines = [result.role + (result.client ? ' (' + result.client + ')' : '')];
  for (const group of result.groups) {
    lines.push('', group.label + ' — ' + selectionText(group.selected));
    if (group.savedValue.status === 'known') lines.push('Saved: ' + group.savedValue.value);
    if (group.savedArgument) lines.push('Target: ' + selectionText(group.savedArgument));
    if (group.defaultForNew) lines.push('Default for new work only: ' + group.defaultForNew + ' (not applied)');
    if (group.locked !== 'false') lines.push('Selection lock: ' + group.locked);
    for (const option of group.options) {
      lines.push('- ' + option.id + ': ' + option.description + ' [' + option.selectability
        + (option.assessmentRequired ? '; assessment required' : '') + ']');
    }
    lines.push('Selection rules: ' + group.selectionRules);
  }
  if (result.userStyles) {
    const styles = result.userStyles;
    lines.push('', 'User styles (' + styles.status + ' inventory): '
      + (styles.options.map(styleText).join(', ') || 'none discovered'),
    'Selected style: ' + selectionText(styles.selected), 'Style lock: ' + styles.locked);
  }
  if (result.diagnostics.length) {
    lines.push('', 'Unresolved discovery:');
    for (const item of result.diagnostics) lines.push('- ' + item.source + ': ' + item.message);
  }
  lines.push('', 'Discovery does not invoke a role, apply defaults, or waive workflow gates.');
  return lines.join('\n') + '\n';
}

export async function runInvocation(args, { scriptUrl = import.meta.url, stdout = process.stdout } = {}) {
  if (args.length === 1 && args[0] === '--help') { stdout.write(USAGE + '\n'); return 0; }
  const { role, client, project, json } = parseArgs(args);
  // Unlike mutating tools, discovery can work with a standalone skill or an
  // incomplete runtime (notably Navigator); no PROTOCOL.md prerequisite.
  const root = project ? path.resolve(project) : fileURLToPath(new URL('../../', scriptUrl));
  const result = await discoverInvocation(root, role, { client });
  stdout.write(json ? JSON.stringify(result, null, 2) + '\n' : formatInvocation(result));
  return result.catalogStatus === 'complete' ? 0 : 1;
}

if (isMain(import.meta.url)) {
  runInvocation(process.argv.slice(2)).then((code) => { process.exitCode = code; },
    (error) => { process.exitCode = printProblem(error); });
}
