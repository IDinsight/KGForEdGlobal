// Read-only assembly and resolution of role invocation metadata. Mode and style
// inventories come from files, never a registry of names.
import { access, lstat, readFile, readdir, realpath, stat } from 'node:fs/promises';
import { constants } from 'node:fs';
import path from 'node:path';

import { GENERATED_CYCLE_ID, HANDOFF_KINDS, TERMINAL_STATES, withoutBom } from './core.mjs';
import { DOCUMENTATION_PHASES, FULL_DELIVERABLE_PHASES, omittedDocumentationRecord, requiredCompletionPhases } from './completion.mjs';
import { fenceAfterLine, fixedPath, headerFieldPairs, isUnfilledHeaderValue, parseProvenance, REVIEW_KINDS, TEMPLATE_ROLE } from './records.mjs';
import { field, modeFromFile, parseState, stateSection, validateState } from './state.mjs';
import { compileInvocationSchema } from './invocation-schema.mjs';

const CLIENTS = { codex: '.agents/skills', claude: '.claude/skills' };
const MARKER = '<!-- standards:invocation -->';
const FENCE = String.fromCharCode(96).repeat(3);
const compare = (a, b) => a < b ? -1 : a > b ? 1 : 0;
const known = (value, source) => ({ status: 'known', value, source });
const unresolved = (status, reason) => ({ status, value: null, reason });
const within = (root, file) => {
  const relative = path.relative(root, file);
  return relative === '' || (!path.isAbsolute(relative) && relative !== '..' && !relative.startsWith('..' + path.sep));
};
const message = (error) => error.code ? error.code + ': ' + error.message : error.message;

// Anchors themselves must be real directories. Individual files may be symlinks
// only when their resolved target stays within the relevant skill/style folder.
async function directory(root, relative) {
  let current = root;
  for (const part of relative.split('/')) {
    current = path.join(current, part);
    const entry = await lstat(current);
    if (!entry.isDirectory() || entry.isSymbolicLink()) {
      throw new Error(relative + ' must use normal directories');
    }
  }
  return current;
}

async function boundedFile(root, relative, boundary = root, read = true) {
  const logical = path.resolve(root, relative);
  if (!within(boundary, logical)) throw new Error(relative + ' escapes its allowed folder');
  const resolved = await realpath(logical);
  if (!within(boundary, resolved)) throw new Error(relative + ' is a symlink escaping its allowed folder');
  const info = await stat(resolved);
  if (!info.isFile()) throw new Error(relative + ' is not a regular file');
  if (!read) {
    await access(resolved, constants.R_OK);
    return null;
  }
  if (info.size > 2 * 1024 * 1024) throw new Error(relative + ' exceeds the 2 MiB discovery limit');
  return withoutBom(await readFile(resolved, 'utf8')).replace(/\r\n/g, '\n');
}

// Return lines outside fences and HTML comments. Keep the exact metadata marker
// visible, but ignore examples of it in a larger fence or HTML comment.
function visibleLines(text) {
  let fence = null;
  let comment = false;
  return text.split('\n').map((line) => {
    if (fence) { fence = fenceAfterLine(line, fence); return null; }
    if (!comment) {
      if (line === MARKER) return line;
      const next = fenceAfterLine(line);
      if (next) { fence = next; return null; }
    } else {
      const end = line.indexOf('-->');
      if (end < 0) return null;
      line = line.slice(end + 3);
      comment = false;
    }
    // Preserve code spans, including comment-looking text in field values.
    // Strip only comments, not the visible fields beside them.
    return line.replace(/(`+)(?!`).*?(?<!`)\1(?!`)|<!--.*?(?:-->|$)/gs, (match, code) => {
      if (code) return match;
      if (!match.endsWith('-->')) comment = true;
      return '';
    });
  });
}

export function parseInvocationBlock(text, validate, source = 'metadata') {
  text = withoutBom(text).replace(/\r\n/g, '\n');
  const lines = text.split('\n');
  const markers = visibleLines(text).flatMap((line, index) => line === MARKER ? [index] : []);
  if (markers.length !== 1) throw new Error(source + ': expected exactly one invocation metadata block');
  let start = markers[0] + 1;
  while (start < lines.length && !lines[start].trim()) start++;
  if (lines[start] !== FENCE + 'json') throw new Error(source + ': metadata needs a json fence');
  const end = lines.indexOf(FENCE, start + 1);
  if (end < 0) throw new Error(source + ': unclosed metadata fence');
  let value;
  try { value = JSON.parse(lines.slice(start + 1, end).join('\n')); } catch {
    throw new Error(source + ': malformed metadata JSON');
  }
  if (value?.schemaVersion !== 1) throw new Error(source + ': unsupported schemaVersion');
  const errors = validate(value);
  if (errors.length) throw new Error(source + ': ' + errors.join('; '));
  return value;
}

function anchors(text) {
  const result = new Set();
  for (const line of visibleLines(text)) {
    const match = line && /^ {0,3}#{1,6} (.+?)(?: +#+)?$/.exec(line);
    if (!match) continue;
    const base = match[1].toLowerCase().replace(/[^\p{L}\p{N}_ -]/gu, '').replace(/ /g, '-');
    let anchor = base;
    for (let index = 1; result.has(anchor); index++) anchor = base + '-' + index;
    result.add(anchor);
  }
  return result;
}

function visit(value, fn) {
  if (!value || typeof value !== 'object') return;
  fn(value);
  for (const child of Object.values(value)) visit(child, fn);
}

async function clientFor(root, role, requested) {
  if (requested !== undefined) {
    if (!Object.hasOwn(CLIENTS, requested)) throw new Error('Unsupported client: ' + requested);
    return requested;
  }
  const found = [];
  for (const [client, base] of Object.entries(CLIENTS)) {
    try {
      await lstat(path.join(root, base, role));
      found.push(client);
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
  }
  if (found.length !== 1) {
    throw new Error(found.length ? 'Both client copies exist; specify --client codex or --client claude'
      : 'No installed skill found for ' + role);
  }
  return found[0];
}

// Exposed separately for source-contract checks and consumers that need only
// the catalog. This function never resolves workflow state or reads styles.
export async function readInvocationCatalog(root, role, { client } = {}) {
  if (typeof role !== 'string' || !/^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/.test(role) || role.includes('\n')) throw new Error('Invalid role identifier');
  root = await realpath(root);
  client = await clientFor(root, role, client);
  const base = CLIENTS[client] + '/' + role;
  const skill = await directory(root, base);
  const schema = JSON.parse(await readFile(new URL('../schemas/invocation-metadata.schema.json', import.meta.url), 'utf8'));
  const validate = compileInvocationSchema(schema);
  const read = (relative) => boundedFile(root, base + '/' + relative, skill);
  const metadata = parseInvocationBlock(await read('SKILL.md'), validate, base + '/SKILL.md');
  if (metadata.kind !== 'role' || metadata.role !== role) throw new Error(base + ': role metadata must match its folder');
  const groups = new Map();
  for (const group of metadata.groups) {
    if (groups.has(group.id)) throw new Error(base + ': duplicate group ' + group.id);
    groups.set(group.id, { ...group, options: group.source === 'inline' ? group.options.map((option) => ({ ...option })) : [] });
  }
  let files = [];
  try {
    const modes = await directory(root, base + '/modes');
    files = (await readdir(modes)).filter((name) => name.endsWith('.md')).sort(compare);
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  for (const file of files) {
    const mode = parseInvocationBlock(await read('modes/' + file), validate, base + '/modes/' + file);
    const group = groups.get(mode.group);
    if (mode.kind !== 'mode' || group?.source !== 'modes') throw new Error(file + ': mode must belong to a declared modes group');
    const option = Object.fromEntries(Object.entries(mode).filter(([key]) => !['schemaVersion', 'kind', 'group'].includes(key)));
    group.options.push({ ...option, instructionFile: base + '/modes/' + file });
  }
  for (const group of groups.values()) {
    if (!group.options.length) throw new Error(group.id + ': no options found');
    if (new Set(group.options.map((option) => option.id)).size !== group.options.length) {
      throw new Error(group.id + ': duplicate option IDs');
    }
    if (group.source === 'modes') group.options.sort((a, b) => compare(a.id, b.id));
    const [file, anchor] = group.selectionRules.split('#');
    const rules = await read(file);
    if (anchor && !anchors(rules).has(anchor)) throw new Error(group.selectionRules + ': missing heading');
    group.selectionRules = base + '/' + group.selectionRules;
    if (group.defaultForNew && group.options.find((option) => option.id === group.defaultForNew)?.selection !== 'user') {
      throw new Error(group.id + ': default must identify a user option');
    }
    if (group.inferFromRequest && group.options.some((option) => option.selection !== 'user')) {
      throw new Error(group.id + ': request inference requires only user options');
    }
    if (group.lockedWhen && !group.options.some((option) => option.selection === 'user')) {
      throw new Error(group.id + ': locks require user options');
    }
    const argumentsCount = group.options.filter((option) => option.argument).length;
    if (group.savedArgument && (!argumentsCount || (!group.savedValue && argumentsCount !== 1))) {
      throw new Error(group.id + ': saved argument has no unambiguous option');
    }
  }
  // All review-kind references must resolve from state, never a saved report.
  const dependencies = new Map([...groups.keys()].map((id) => [id, new Set()]));
  visit({ metadata, groups: [...groups.values()] }, (value) => {
    if (!value.reviewKindFromGroup) return;
    const target = groups.get(value.reviewKindFromGroup);
    if (!target || target.options.some((option) => option.selection !== 'state' || !REVIEW_KINDS.has(option.id))) {
      throw new Error('Invalid state-derived review-kind group: ' + value.reviewKindFromGroup);
    }
  });
  for (const group of groups.values()) {
    for (const option of group.options.filter((item) => item.selection === 'state')) {
      visit(option.when, (value) => {
        if (value.reviewKindFromGroup) dependencies.get(group.id).add(value.reviewKindFromGroup);
      });
    }
  }
  const done = new Set();
  function checkCycle(id, active = new Set()) {
    if (active.has(id)) throw new Error('Cyclic state-selection dependency: ' + id);
    if (done.has(id)) return;
    for (const dependency of dependencies.get(id)) checkCycle(dependency, new Set([...active, id]));
    done.add(id);
  }
  for (const id of groups.keys()) checkCycle(id);
  return { role, client, skillFile: base + '/SKILL.md', groups: [...groups.values()], userStyles: metadata.userStyles };
}

export async function evaluateInvocationCondition(condition, fact) {
  if (!condition) return 'true';
  if (condition.fact) {
    const result = await fact(condition.fact);
    return result.status === 'known' ? (result.value === condition.equals ? 'true' : 'false') : 'unknown';
  }
  const values = await Promise.all((condition.all ?? condition.any).map((child) => evaluateInvocationCondition(child, fact)));
  if (condition.all) return values.includes('false') ? 'false' : values.every((value) => value === 'true') ? 'true' : 'unknown';
  return values.includes('true') ? 'true' : values.every((value) => value === 'false') ? 'false' : 'unknown';
}

function oneHeader(pairs, name) {
  const values = pairs.filter(([key]) => key === name).map(([, value]) => value);
  if (values.length !== 1 || !values[0].trim() || /[\r\n]/.test(values[0])
      || (name === 'Target Detail' ? /^<.*>$/.test(values[0]) : isUnfilledHeaderValue(values[0]))) {
    throw new Error('Missing, repeated, or unfilled record field: ' + name);
  }
  return values[0];
}

function contextReader(root, report, resolveGroup, catalog) {
  const catalogs = new Map([[catalog.role, Promise.resolve(catalog)]]);
  let statePromise;
  let modePromise;
  const records = new Map();
  async function state() {
    statePromise ??= (async () => {
      const text = await boundedFile(root, '.standards/STATE.md');
      if (/^(<<<<<<< |>>>>>>> )/m.test(text)) throw new Error('STATE.md has merge conflicts');
      const result = validateState(text);
      const parsed = parseState(text);
      const kind = field(stateSection(text, 'Handoff'), 'Kind', '.standards/STATE.md');
      if (!HANDOFF_KINDS.has(kind)) throw new Error('Invalid Handoff.Kind');
      const terminal = TERMINAL_STATES.has(result.workflowState);
      const projectMode = await mode();
      if ((projectMode === 'GREENFIELD' && [result.cycleMode, result.pendingMode].some((value) => ['EXPEDITED', 'DOCUMENTATION'].includes(value)))
          || (result.id === 'UNSET' && result.workflowState !== (projectMode === 'GREENFIELD' ? 'SCOPING' : 'AUDITING'))) {
        throw new Error('Project mode conflicts with the cycle state');
      }
      if ((result.id !== 'UNSET' && !GENERATED_CYCLE_ID.test(result.id))
          || (result.id === 'UNSET' && (result.cycleMode !== 'UNSET' || terminal || result.completionPolicy !== 'NONE'
            || !['SCOPING', 'AUDITING'].includes(result.workflowState)))
          || (result.id !== 'UNSET' && !terminal && result.cycleMode === 'UNSET')
          || requiredCompletionPhases(result.cycleMode, result.completionPolicy) === null) {
        throw new Error('Inconsistent cycle identity, state, or completion policy');
      }
      if ((result.cycleMode === 'DOCUMENTATION' && ![...DOCUMENTATION_PHASES, 'AWAITING_USER_SIGNOFF'].includes(result.workflowState))
          || (result.cycleMode === 'EXPEDITED' && !['DEVELOPING', 'REVIEWING_IMPLEMENTATION', 'AWAITING_USER_SIGNOFF'].includes(result.workflowState))
          || (result.cycleMode === 'STANDARD' && result.completionPolicy === 'IMPLEMENTATION_REVIEWED'
            && FULL_DELIVERABLE_PHASES.includes(result.workflowState) && !parsed.recovery.active)) {
        throw new Error('WorkflowState is outside the cycle route');
      }
      return { ...result, parsed, text, kind, terminal };
    })();
    return statePromise;
  }
  async function mode() {
    modePromise ??= boundedFile(root, '.standards/MODE.md').then((text) => {
      if (/^(<<<<<<< |>>>>>>> )/m.test(text)) throw new Error('MODE.md has merge conflicts');
      return modeFromFile(text);
    });
    return modePromise;
  }
  async function record(binding) {
    const current = await state();
    if (current.id === 'UNSET' || current.terminal) throw new Error('No active cycle record');
    let kind = binding.reviewKind ?? null;
    if (binding.reviewKindFromGroup) {
      const selection = await resolveGroup(binding.reviewKindFromGroup);
      if (selection.status !== 'known' || selection.source !== 'state') throw new Error('Review kind is unresolved');
      kind = selection.value;
    }
    if (current.cycleMode === 'DOCUMENTATION' && omittedDocumentationRecord(binding.artifact, kind)) {
      throw new Error('DOCUMENTATION omits this record type');
    }
    const file = fixedPath(binding.artifact, current.id, kind);
    if (binding.artifact === 'DEVELOPMENT') {
      const reference = current.parsed.active.development;
      // Match the checker's POSIX path semantics; still read only the fixed record.
      if (reference !== 'NONE' && path.posix.normalize(reference) !== file) throw new Error('Active Work.Development does not match the current-cycle path');
    }
    if (!records.has(file)) records.set(file, (async () => {
      const text = await boundedFile(root, file);
      if (/^(<<<<<<< |>>>>>>> )/m.test(text)) throw new Error(file + ': unresolved merge conflicts');
      const provenance = parseProvenance(text);
      if (!provenance || provenance.error || provenance.artifact !== binding.artifact
          || provenance.cycle !== current.id || provenance.reviewKind !== kind) {
        throw new Error(file + ': wrong or malformed current-cycle provenance');
      }
      const pairs = headerFieldPairs(visibleLines(text).filter((line) => line !== null).join('\n'));
      if (oneHeader(pairs, 'Cycle') !== current.id
          || (binding.artifact === 'REVIEW' && oneHeader(pairs, 'ReviewKind') !== kind)) {
        throw new Error(file + ': header conflicts with provenance');
      }
      return { pairs, file };
    })());
    return records.get(file);
  }
  const cache = new Map();
  async function read(binding) {
    if (binding.kind === 'conversation') return unresolved('conversation', 'Use the current conversation; no disk value exists');
    try {
      if (binding.kind === 'workflow') {
        if (binding.field === 'ProjectMode') return known(await mode(), '.standards/MODE.md');
        const current = await state();
        const values = {
          CycleMode: current.cycleMode, WorkflowState: current.workflowState,
          'Active Work.CompletionPolicy': current.completionPolicy,
          'Active Work.PendingVerificationCadence': current.parsed.active.pendingVerificationCadence,
          'Active Work.AuditTarget': field(stateSection(current.text, 'Active Work'), 'AuditTarget', '.standards/STATE.md'),
          'Handoff.Kind': current.kind,
        };
        return known(values[binding.field], '.standards/STATE.md');
      }
      const { pairs, file } = await record(binding);
      const value = oneHeader(pairs, binding.field);
      if (['Mode', 'Collaboration', 'Target', 'ReviewKind'].includes(binding.field)) {
        let allowed;
        if (binding.field === 'ReviewKind') allowed = [...REVIEW_KINDS];
        else {
          const owner = TEMPLATE_ROLE[binding.artifact];
          if (!catalogs.has(owner)) catalogs.set(owner, readInvocationCatalog(root, owner, { client: catalog.client }));
          const source = await catalogs.get(owner);
          const owners = source.groups.filter((group) => group.savedValue?.kind === 'record'
            && group.savedValue.artifact === binding.artifact && group.savedValue.field === binding.field);
          if (owners.length !== 1) throw new Error(file + ': choice field has no unambiguous metadata group');
          allowed = owners[0].options.map((option) => option.id);
        }
        if (!allowed.includes(value)) {
          const reason = 'Saved option is absent from the current inventory: ' + value;
          report('invalid-record-choice', file, reason);
          return { status: 'invalid', value, source: file, reason };
        }
      }
      if (binding.field === 'User Style Locked') {
        if (!['true', 'false'].includes(value)) throw new Error(file + ': User Style Locked must be true or false');
        const status = oneHeader(pairs, 'Status');
        if (!['PROPOSED', 'APPROVED', 'IN_PROGRESS', 'COMPLETE'].includes(status)
            || (value === 'false' && status !== 'PROPOSED')) {
          throw new Error(file + ': plan status contradicts its style lock');
        }
      }
      return known(value, file);
    } catch (error) {
      const reason = message(error);
      report('unknown-fact', binding.kind === 'record' ? binding.artifact + '.' + binding.field : binding.field, reason);
      return unresolved('unknown', reason);
    }
  }
  return (binding) => {
    const key = JSON.stringify(binding);
    if (!cache.has(key)) cache.set(key, read(binding));
    return cache.get(key);
  };
}

function validStyleId(id) {
  return Boolean(id) && !['.', '..', 'NONE'].includes(id) && !/[/\\\r\n\0]/.test(id);
}

function matchingStyles(options, value) {
  return options.filter((item) => item.id === value || (value.endsWith('.md') && item.id === value.slice(0, -3)));
}

async function styleInventory(root, role, report, persisted) {
  const relative = '.standards/user-styles/' + role;
  const result = { directory: relative, status: 'complete', options: [] };
  let entries, boundary;
  try {
    boundary = await directory(root, relative);
    entries = await readdir(boundary);
  } catch (error) {
    if (error.code === 'ENOENT') return result;
    report('style-inventory', relative, message(error));
    return { ...result, status: 'unknown' };
  }
  for (const name of entries.sort(compare)) {
    if (!name.endsWith('.md')) continue;
    const id = name.slice(0, -3);
    try {
      if (!validStyleId(id)) throw new Error('Invalid or reserved user-style identifier');
      const file = relative + '/' + name;
      await boundedFile(root, file, boundary, false);
      result.options.push({ id, file, selector: null, selectorReason: 'selection unverified' });
    } catch (error) {
      result.status = 'unknown';
      report('style-inventory', relative + '/' + name, message(error));
    }
  }
  if (result.status === 'complete') {
    for (const option of result.options) {
      // A selectable name must resolve uniquely and, for record bindings,
      // survive both header parsing and the checker's placeholder rules.
      const unique = [option.id, option.id + '.md']
        .filter((value) => matchingStyles(result.options, value).length === 1);
      option.selector = unique.find((value) => !persisted || (value.trim() && !isUnfilledHeaderValue(value))) ?? null;
      option.selectorReason = option.selector !== null ? null
        : unique.length ? 'no record-compatible name' : 'ambiguous name';
    }
  }
  return result;
}

function selectability(option, condition, lock, stateSelection) {
  if (condition === 'false') return 'unavailable';
  if (condition === 'unknown') return 'unknown';
  if (option.selection !== 'user') return 'informational';
  if (stateSelection.status === 'known') return 'state-controlled';
  if (['unknown', 'conflict'].includes(stateSelection.status) || lock === 'unknown') return 'unknown';
  if (lock === 'true') return 'locked';
  return option.requiresAssessment ? 'assessment-required' : 'user';
}

export async function discoverInvocation(root, role, options = {}) {
  const diagnostics = [];
  const report = (code, source, text) => {
    if (!diagnostics.some((item) => item.code === code && item.source === source && item.message === text)) {
      diagnostics.push({ code, source, message: text });
    }
  };
  const result = { schemaVersion: 1, role, client: options.client ?? null, catalogStatus: 'unavailable',
    complete: false, groups: [], userStyles: null, diagnostics };
  try {
    root = await realpath(root);
    const catalog = await readInvocationCatalog(root, role, options);
    Object.assign(result, { client: catalog.client, skillFile: catalog.skillFile, catalogStatus: 'complete' });
    const groups = new Map(catalog.groups.map((group) => [group.id, group]));
    const stateCache = new Map();
    async function resolveState(id) {
      if (!stateCache.has(id)) stateCache.set(id, (async () => {
        const group = groups.get(id);
        const values = await Promise.all(group.options.filter((option) => option.selection === 'state')
          .map(async (option) => ({ id: option.id, when: await evaluateInvocationCondition(option.when, fact) })));
        const matches = values.filter((item) => item.when === 'true');
        if (matches.length > 1) {
          report('conflicting-state-selection', id, 'Multiple state conditions match');
          return unresolved('conflict', 'Multiple state conditions match');
        }
        if (values.some((item) => item.when === 'unknown')) return unresolved('unknown', 'A state-selection condition is unknown');
        return matches.length ? known(matches[0].id, 'state') : unresolved('none', 'No state-selected option');
      })());
      return stateCache.get(id);
    }
    const fact = contextReader(root, report, resolveState, catalog);
    for (const group of catalog.groups) {
      const { savedValue: binding, savedArgument: argumentBinding, lockedWhen, options: choices, ...definition } = group;
      const stateSelection = await resolveState(group.id);
      const lock = lockedWhen ? await evaluateInvocationCondition(lockedWhen, fact) : 'false';
      const savedValue = binding ? await fact(binding) : unresolved('none', 'No saved-value binding');
      let savedArgument = argumentBinding ? await fact(argumentBinding) : null;
      if (argumentBinding?.kind === 'workflow' && argumentBinding.field === 'Active Work.AuditTarget'
          && savedArgument.status === 'known' && savedArgument.value === 'NONE') {
        savedArgument = unresolved('none', 'No saved audit target');
      }
      const resolved = await Promise.all(choices.map(async (option) => {
        const condition = await evaluateInvocationCondition(option.when, fact);
        return { ...option, condition, assessmentRequired: option.selection === 'assessment' || option.requiresAssessment === true,
          selectability: selectability(option, condition, lock, stateSelection) };
      }));
      let selected = stateSelection;
      if (savedValue.status === 'invalid' && selected.status === 'none') selected = savedValue;
      const savedOption = resolved.find((option) => option.id === savedValue.value);
      if (savedValue.status === 'known' && !savedOption) {
        report('invalid-saved-selection', group.id, 'Saved option is absent from the current inventory: ' + savedValue.value);
        if (selected.status === 'none') selected = { ...savedValue, status: 'invalid' };
      }
      if (selected.status === 'none') {
        if (resolved.every((option) => option.condition === 'false')) {
          selected = unresolved('none', 'No options satisfy the current conditions');
        } else if (savedOption && savedOption.selection === 'user' && savedOption.condition === 'true' && !savedOption.assessmentRequired) {
          selected = { ...savedValue, source: binding.kind };
        } else if (resolved.some((option) => option.condition === 'true' && option.assessmentRequired)) {
          selected = unresolved('assessment', 'The role must assess current evidence; a saved mode is resume context only');
        } else if (savedValue.status === 'conversation') selected = savedValue;
        else selected = unresolved('unknown', 'Resolve the current request and saved intent; defaults are not automatically applied');
      }
      if (savedOption && (savedOption.condition === 'false'
          || (stateSelection.status === 'known' && savedOption.id !== stateSelection.value))) {
        report('inconsistent-saved-selection', group.id, 'Saved choice conflicts with current conditions or state');
      }
      result.groups.push({ ...definition, options: resolved, selected, savedValue,
        ...(savedArgument ? { savedArgument } : {}), locked: lock });
    }
    const styles = await styleInventory(root, role, report, catalog.userStyles.savedValue.kind === 'record');
    const savedValue = await fact(catalog.userStyles.savedValue);
    const locked = catalog.userStyles.lockedWhen
      ? await evaluateInvocationCondition(catalog.userStyles.lockedWhen, fact) : 'false';
    let selected = savedValue;
    if (savedValue.status === 'known' && savedValue.value !== 'NONE') {
      const raw = savedValue.value;
      const candidates = matchingStyles(styles.options, raw);
      if (styles.status === 'unknown') selected = { ...savedValue, status: 'unknown', reason: 'Style inventory is incomplete' };
      else if (!validStyleId(raw) || candidates.length !== 1) {
        selected = { ...savedValue, status: 'invalid', reason: 'Saved style does not resolve to exactly one available file' };
        report('invalid-saved-style', styles.directory, selected.reason);
      } else selected = { ...savedValue, value: candidates[0].id };
    }
    result.userStyles = { ...styles, savedValue, selected, locked };
    result.complete = diagnostics.length === 0;
  } catch (error) {
    report('catalog-error', role, message(error));
  }
  diagnostics.sort((a, b) => compare(a.source, b.source) || compare(a.code, b.code) || compare(a.message, b.message));
  return result;
}
