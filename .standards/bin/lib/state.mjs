// Parsing for `.standards/STATE.md` and `.standards/MODE.md`. The installer
// imports the basic validators from here too, so installs, upgrades, and
// `check` read these files the same way.
import { COMPLETION_POLICIES, CYCLE_MODES, MODES, STATES, VERIFICATION_CADENCES, fieldPairs, withoutBom } from './core.mjs';

// The one non-empty value of a field, or an error naming the field.
export function field(text, name, relative) {
  const values = fieldPairs(text).filter(([key]) => key === name);
  if (values.length !== 1 || !values[0][1]) throw new Error(`Invalid or missing ${name} in ${relative}`);
  return values[0][1];
}

export function stateSection(text, name) {
  const heading = `## ${name}`;
  const start = text.indexOf(heading);
  if (start === -1 || text.indexOf(heading, start + heading.length) !== -1) {
    throw new Error(`Missing or repeated ${name} in .standards/STATE.md`);
  }
  const next = text.indexOf('\n## ', start + heading.length);
  return text.slice(start + heading.length, next === -1 ? undefined : next);
}

// The Active Work section split into its plain fields and the
// `BaselineReconciliation` value, which is `NONE` or a Markdown list whose
// entries repeat field names such as `Request`. Comments are ignored.
function splitActiveWork(section) {
  const text = section.replace(/<!--[\s\S]*?-->/g, '');
  const label = '`BaselineReconciliation`:';
  const start = text.indexOf(label);
  if (start === -1) return { fields: text, baseline: null };
  const lines = text.slice(start + label.length).split('\n');
  let end = 1;
  let inList = false;
  for (; end < lines.length; end += 1) {
    const line = lines[end];
    if (!line.trim()) continue;
    if (/^\s*-\s/.test(line) || (inList && /^\s+\S/.test(line))) {
      inList = true;
      continue;
    }
    break;
  }
  return {
    fields: `${text.slice(0, start)}\n${lines.slice(end).join('\n')}`,
    baseline: lines.slice(0, end).join('\n'),
  };
}

// Structural check used by the installer before upgrades. `check` applies the
// stricter workflow rules on top of this.
export function validateState(input) {
  const text = withoutBom(input);
  const relative = '.standards/STATE.md';
  if (!text.startsWith('# S.T.A.N.D.A.R.D.S. Workflow State')) {
    throw new Error(`Invalid ${relative} header`);
  }
  const head = text.slice(0, text.indexOf('## Active Work'));
  const workflowState = field(head, 'WorkflowState', relative);
  const cycleMode = field(head, 'CycleMode', relative);
  if (!STATES.has(workflowState)) throw new Error(`Invalid WorkflowState in ${relative}`);
  if (!CYCLE_MODES.has(cycleMode)) {
    throw new Error(`Invalid CycleMode in ${relative}`);
  }
  const pendingMode = field(head, 'PendingCycleMode', relative);
  const pendingRequest = field(head, 'PendingCycleRequest', relative);
  const pendingBlockedOn = field(head, 'PendingCycleBlockedOn', relative);
  if (!CYCLE_MODES.has(pendingMode)
      || (pendingRequest === 'UNSET') !== (pendingBlockedOn === 'NONE')
      || (['SIGNED_OFF', 'CANCELLED'].includes(workflowState) && cycleMode !== 'UNSET')) {
    throw new Error(`Inconsistent cycle or pending fields in ${relative}`);
  }
  const { fields: activeFields, baseline } = splitActiveWork(stateSection(text, 'Active Work'));
  const id = field(activeFields, 'Id', relative);
  const completionPolicy = field(activeFields, 'CompletionPolicy', relative);
  if (!COMPLETION_POLICIES.includes(completionPolicy)) {
    throw new Error(`Invalid CompletionPolicy in ${relative}`);
  }
  for (const name of ['Request', 'Scope', 'Architecture', 'Development', 'PromotionReason', 'AuditTarget', 'BlockedOn']) {
    field(activeFields, name, relative);
  }
  const pendingCadence = field(activeFields, 'PendingVerificationCadence', relative);
  if (!['NONE', ...VERIFICATION_CADENCES].includes(pendingCadence)) {
    throw new Error(`Invalid PendingVerificationCadence in ${relative}`);
  }
  if ((id === 'UNSET' || ['SIGNED_OFF', 'CANCELLED'].includes(workflowState)) && pendingCadence !== 'NONE') {
    throw new Error(`Inactive cycles require PendingVerificationCadence NONE in ${relative}`);
  }
  if (baseline === null) throw new Error(`Missing BaselineReconciliation in ${relative}`);
  const handoff = stateSection(text, 'Handoff');
  for (const name of ['Kind', 'From', 'FailureType', 'Reason']) {
    if (!fieldPairs(handoff).some(([key, value]) => key === name && value)) {
      throw new Error(`Invalid or missing Handoff.${name} in ${relative}`);
    }
  }
  for (const name of ['Recovery', 'Outstanding Obligations']) {
    if (!['true', 'false'].includes(field(stateSection(text, name), 'Active', relative))) {
      throw new Error(`Invalid ${name}.Active in ${relative}`);
    }
  }
  return { workflowState, cycleMode, pendingMode, id, completionPolicy };
}

export function modeFromFile(text) {
  const mode = field(withoutBom(text), 'ProjectMode', '.standards/MODE.md');
  if (!MODES.has(mode)) throw new Error('Invalid ProjectMode in .standards/MODE.md');
  return mode;
}

// The fields of a piece of Markdown by name; the first occurrence wins.
function fieldsOf(text) {
  const fields = {};
  for (const [name, value] of fieldPairs(text)) if (!(name in fields)) fields[name] = value;
  return fields;
}

// Split a section into its `### <label> N` entries. Anything after the label
// is kept as the entry's number, so a heading such as `### Frame 1 (scope)` is
// still found, and then reported for not being numbered `1`, `2`, ...
function numberedEntries(section, label) {
  const headings = [...section.matchAll(new RegExp(`^###\\s+${label}\\b[ \\t]*(.*?)[ \\t]*$`, 'gm'))];
  return headings.map((match, index) => ({
    label: match[1],
    fields: fieldsOf(section.slice(match.index + match[0].length, headings[index + 1]?.index)),
  }));
}

// `BaselineReconciliation` is either `NONE` or a Markdown list of
// `SourceCycle`/`Request` pairs (PROTOCOL.md, Baseline Reconciliation Format).
function parseBaseline(value) {
  const body = value.trim();
  if (/^`NONE`$/.test(body)) return { entries: [] };
  if (!body.startsWith('- ')) return { error: 'must be `NONE` or a list of `SourceCycle`/`Request` entries' };
  const entries = [];
  for (const chunk of body.split(/^\s*- /m).slice(1)) {
    const fields = fieldsOf(chunk);
    if (!fields.SourceCycle || !fields.Request) {
      return { error: 'every entry needs both `SourceCycle` and `Request`' };
    }
    entries.push({ sourceCycle: fields.SourceCycle, request: fields.Request });
  }
  return { entries };
}

// Full read of STATE.md for `check`. Call validateState first; this assumes the
// basic structure is valid.
export function parseState(input) {
  const text = withoutBom(input);
  const head = text.slice(0, text.indexOf('## Active Work'));
  const { fields, baseline } = splitActiveWork(stateSection(text, 'Active Work'));
  const activeFields = fieldsOf(fields);
  const recovery = stateSection(text, 'Recovery');
  const obligations = stateSection(text, 'Outstanding Obligations');
  return {
    ...fieldsOf(head),
    active: {
      id: activeFields.Id,
      request: activeFields.Request,
      completionPolicy: activeFields.CompletionPolicy,
      blockedOn: activeFields.BlockedOn,
      scope: activeFields.Scope,
      architecture: activeFields.Architecture,
      development: activeFields.Development,
      promotionReason: activeFields.PromotionReason,
      pendingVerificationCadence: activeFields.PendingVerificationCadence,
      baseline: parseBaseline(baseline),
    },
    handoff: fieldsOf(stateSection(text, 'Handoff')),
    recovery: { active: fieldsOf(recovery).Active === 'true', frames: numberedEntries(recovery, 'Frame') },
    obligations: {
      active: fieldsOf(obligations).Active === 'true',
      items: numberedEntries(obligations, 'Obligation'),
    },
  };
}
