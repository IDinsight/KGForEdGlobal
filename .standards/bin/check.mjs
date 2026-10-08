#!/usr/bin/env node
// Check the installed runtime and the active cycle's records against the rules
// the protocol defines. It only reads files; it never repairs anything.
//
//   node .standards/bin/check.mjs [--json]
//
// Exit code 0 means no problems were found; 1 means at least one problem (or
// the check itself could not run).
import { readdir } from 'node:fs/promises';
import path from 'node:path';

import { FAILURE_TYPES, GENERATED_CYCLE_ID, HANDOFF_KINDS, STATES, TERMINAL_STATES, UsageError, committedText, exists, git, isMain, printProblem, projectRootFor, readText } from './lib/core.mjs';
import { LOCAL_PREFIX, QUALIFIED_PREFIXES, acceptanceInventory, acceptanceMentions, bareIds, developmentSteps, fixedPath, fixedRecords, headerFields, headingIds, isUnfilledHeaderValue, parseProvenance, qualifiedReferences, recordName, recordSection, requiredCompletionRecords, scanArtifacts, withoutLinkLabels, withoutPreviousCycles } from './lib/records.mjs';
import { modeFromFile, parseState, validateState } from './lib/state.mjs';
import { checkVerificationWorkflow } from './lib/verification.mjs';
import { DOCUMENTATION_PHASES, FULL_DELIVERABLE_PHASES, completionChangeRoute, omittedDocumentationRecord, requiredCompletionPhases } from './lib/completion.mjs';
import { checkClosureAssessment } from './lib/review.mjs';

const RUNTIME_FILES = ['PROTOCOL.md', 'VERSION.json', 'INSTALLATION.json', 'MODE.md', 'STATE.md'];
const STATE_FILE = '.standards/STATE.md';
// Shared ordering from implementation onward; documentation uses only the
// final four states. Earlier states (scoping, architecture, audit) rank 0.
const TAIL = ['DEVELOPING', 'TESTING', 'REVIEWING_IMPLEMENTATION', 'DOCUMENTING', 'REVIEWING_FINAL',
  'SYNCHRONIZING', 'AWAITING_USER_SIGNOFF'];
const rank = (state) => TAIL.indexOf(state) + 1;
// Whether a scoped cycle has moved past ARCHITECTING. Greenfield audits after
// the design; brownfield audits before scoping, when there is no design yet.
const pastArchitecture = (state, mode) => rank(state) > 0 || (state === 'AUDITING' && mode === 'GREENFIELD');
// The state that completes each record. From that state on, a COMPLETE record
// must account for every current acceptance condition, so its owner's own
// check before handoff catches a gap. During recovery only the current state's
// own record is held to this, and at the end of a turn the stop hook holds a
// different record set; see `held` in checkAcceptance.
const COMPLETED_IN = {
  VERIFICATION: 'TESTING', DOCUMENTATION: 'DOCUMENTING', SYNCHRONIZATION: 'SYNCHRONIZING',
  'REVIEW:IMPLEMENTATION': 'REVIEWING_IMPLEMENTATION', 'REVIEW:FINAL_DELIVERABLE': 'REVIEWING_FINAL',
};
const HEADER_RECORDS = new Set(['DEVELOPMENT', 'VERIFICATION', 'REVIEW', 'DOCUMENTATION', 'SYNCHRONIZATION']);
const RECORD_STATUSES = ['IN_PROGRESS', 'BLOCKED', 'COMPLETE'];
// The state or states that own each failure type (PROTOCOL.md, Failure Handoffs).
const FAILURE_OWNER = {
  SCOPING: ['SCOPING'], ARCHITECTURE: ['ARCHITECTING'], PROJECT_CONTEXT: ['AUDITING'],
  IMPLEMENTATION: ['DEVELOPING'], VERIFICATION: ['TESTING'], DOCUMENTATION: ['DOCUMENTING'],
  REVIEW: ['REVIEWING_IMPLEMENTATION', 'REVIEWING_FINAL'], SYNCHRONIZATION: ['SYNCHRONIZING'],
};
// Where each qualified identifier lives, for the example in a message.
const EXAMPLE_RECORD = {
  F: fixedPath('REVIEW', '<cycle>', 'IMPLEMENTATION'), D: fixedPath('SYNCHRONIZATION', '<cycle>'),
  DOC: fixedPath('DOCUMENTATION', '<cycle>'),
};
const COLLISION = 'Treat it as a collision: leave it unchanged and ask the user how to resolve it.';
const NEW_ID = 'Get cycle IDs only from `node .standards/bin/cycle.mjs new`.';
// How a scope defines a condition (PROTOCOL.md, Acceptance Traceability).
const DEFINE_CONDITION = 'Each condition must be a list item that starts with its ID, for example '
  + '"- `AC-001`: <condition>"; a table row, heading, or prose mention does not define it.';

const within = (file) => (message) => message.replace(` in ${file}`, '');
const ownersOf = (failureType) => FAILURE_OWNER[failureType] ?? [];

function checkState(state, { committed, mode, report }) {
  const problem = (message) => report(STATE_FILE, message);
  const workflowState = state.WorkflowState;
  const cycleMode = state.CycleMode;
  const { id } = state.active;
  const terminal = TERMINAL_STATES.has(workflowState);
  const policy = state.active.completionPolicy;
  const requiredPhases = requiredCompletionPhases(cycleMode, policy);

  if (requiredPhases === null) {
    problem(`CompletionPolicy ${policy} is not valid with CycleMode ${cycleMode}. STANDARD requires FULL_DELIVERABLE or IMPLEMENTATION_REVIEWED; EXPEDITED and DOCUMENTATION require NONE.`);
  }
  if (id === 'UNSET' && policy !== 'NONE') {
    problem('No cycle has started, so Active Work.CompletionPolicy must be NONE.');
  }
  if (cycleMode === 'STANDARD' && policy === 'IMPLEMENTATION_REVIEWED'
      && FULL_DELIVERABLE_PHASES.includes(workflowState) && !state.recovery.active) {
    problem(`${workflowState} is outside the normal IMPLEMENTATION_REVIEWED route. Required corrective work uses recovery; normal downstream work requires FULL_DELIVERABLE.`);
  }

  if (id !== 'UNSET' && !GENERATED_CYCLE_ID.test(id)) {
    problem(`Active Work.Id \`${id}\` is not in the form cycle.mjs generates. ${NEW_ID}`);
  }
  // A finished cycle is never reopened: when the last commit ended a cycle and
  // a cycle is active now, it needs a new ID.
  if (committed && TERMINAL_STATES.has(committed.workflowState) && !terminal && committed.id === id) {
    problem(`Active Work.Id \`${id}\` belongs to the cycle the last commit ended in ${committed.workflowState}; a new cycle needs a new ID. ${NEW_ID}`);
  }
  if (committed && !terminal && committed.id === id && committed.cycleMode !== cycleMode
      && [committed.cycleMode, cycleMode].includes('DOCUMENTATION')) {
    problem('A DOCUMENTATION cycle cannot be converted in place. Explicitly cancel it and start a separate cycle with a new ID.');
  }
  if (id === 'UNSET' && cycleMode !== 'UNSET') problem('CycleMode is set, but no cycle has started (Active Work.Id is UNSET).');
  if (id !== 'UNSET' && !terminal && cycleMode === 'UNSET') {
    problem(`Cycle \`${id}\` is active, but CycleMode is UNSET. Record STANDARD, EXPEDITED, or DOCUMENTATION when the cycle starts.`);
  }
  const { Kind: kind, From: from, FailureType: failureType } = state.handoff;
  if (kind === 'COMPLETION_CHANGE') {
    if (id === 'UNSET' || terminal || cycleMode !== 'STANDARD'
        || !completionChangeRoute(from, workflowState)) {
      problem('COMPLETION_CHANGE requires an active STANDARD cycle and either DOCUMENTING -> REVIEWING_IMPLEMENTATION (selection), or AWAITING_USER_SIGNOFF -> DOCUMENTING (withdrawal).');
    }
    if (failureType !== 'NONE') problem('A COMPLETION_CHANGE handoff requires FailureType NONE.');
    if (state.recovery.active || state.recovery.frames.length
        || state.obligations.active || state.obligations.items.length) {
      problem('COMPLETION_CHANGE requires inactive recovery with an empty stack and no outstanding obligations.');
    }
    // This is the latest saved handoff, not an event log. In Reviewer, a later
    // same-state choice can restore FULL_DELIVERABLE while retaining this
    // handoff. The receiving role can also record a blocker or reopen its
    // report. Entry-time authorization and freshness remain role assessments.
    if (!state.handoff.Reason?.trim() || state.handoff.Reason === 'NONE') {
      problem('A COMPLETION_CHANGE handoff needs a reason identifying the policy change.');
    }
  }
  if (kind === 'FORWARD' && !state.recovery.active && !state.recovery.frames.length && requiredPhases?.length) {
    if (workflowState === 'AWAITING_USER_SIGNOFF' && from !== requiredPhases.at(-1)) {
      problem(`Forward sign-off readiness for ${cycleMode}/${policy} must come from ${requiredPhases.at(-1)}, not ${from}.`);
    }
    if (cycleMode === 'STANDARD' && from === 'REVIEWING_IMPLEMENTATION') {
      const next = policy === 'IMPLEMENTATION_REVIEWED' ? 'AWAITING_USER_SIGNOFF' : 'DOCUMENTING';
      if (workflowState !== next) problem(`Normal implementation-review success with ${policy} hands off to ${next}, not ${workflowState}.`);
    }
  }
  // Before the first cycle, nothing may have moved away from the installed state.
  if (id === 'UNSET') {
    const start = { GREENFIELD: 'SCOPING', BROWNFIELD: 'AUDITING' }[mode];
    if (start && workflowState !== start) {
      problem(`No cycle has started (Active Work.Id is UNSET), so WorkflowState must still be ${start}.`);
    }
    if (kind !== 'INITIAL') problem(`No cycle has started (Active Work.Id is UNSET), so Handoff.Kind must still be INITIAL, not ${kind}.`);
    for (const name of ['scope', 'architecture', 'development']) {
      if (state.active[name] !== 'NONE') {
        problem(`No cycle has started (Active Work.Id is UNSET), so Active Work.${name[0].toUpperCase()}${name.slice(1)} must be NONE.`);
      }
    }
  }
  for (const restricted of ['EXPEDITED', 'DOCUMENTATION']) {
    if (mode === 'GREENFIELD' && (cycleMode === restricted || state.PendingCycleMode === restricted)) {
      problem(`A GREENFIELD project cannot use ${restricted} cycles.`);
    }
  }
  if (cycleMode === 'DOCUMENTATION') {
    const route = [...DOCUMENTATION_PHASES, 'AWAITING_USER_SIGNOFF'];
    if (!route.includes(workflowState) && !terminal) {
      problem(`${workflowState} is not part of a DOCUMENTATION cycle.`);
    }
    if (['INITIAL', 'NEW_CYCLE'].includes(kind)) {
      if (workflowState !== 'AUDITING') {
        problem(`A DOCUMENTATION cycle starts in AUDITING for ${kind} entry.`);
      }
      if (kind === 'INITIAL' && from !== 'NONE') {
        problem('A DOCUMENTATION INITIAL handoff requires From NONE.');
      }
      if (kind === 'NEW_CYCLE' && !TERMINAL_STATES.has(from)) {
        problem('A DOCUMENTATION NEW_CYCLE handoff requires From SIGNED_OFF or CANCELLED.');
      }
    }
    if (['INITIAL', 'NEW_CYCLE', 'FORWARD'].includes(kind) && failureType !== 'NONE') {
      problem(`A DOCUMENTATION ${kind} handoff requires FailureType NONE.`);
    }
    if (kind === 'FORWARD') {
      const index = route.indexOf(from);
      if (index < 0 || route[index + 1] !== workflowState) {
        problem(`Normal DOCUMENTATION forward handoffs follow ${route.join(' -> ')}; ${from} -> ${workflowState} skips or leaves that route.`);
      }
    }
    if (['FAILURE', 'USER_REWORK', 'RESUME'].includes(kind)
        && ![...DOCUMENTATION_PHASES, 'AWAITING_USER_SIGNOFF'].includes(from)) {
      problem(`A DOCUMENTATION ${kind} handoff cannot originate in omitted state ${from}.`);
    }
    if (kind === 'RESUME' && failureType !== 'NONE') problem('A DOCUMENTATION RESUME handoff requires FailureType NONE.');
    if (kind === 'RESUME' && !DOCUMENTATION_PHASES.includes(from)) {
      problem('A DOCUMENTATION RESUME handoff must come from an included role that completed its recovery assignment.');
    }
    if (['FAILURE', 'USER_REWORK'].includes(kind)
        && ['IMPLEMENTATION', 'VERIFICATION'].includes(failureType)) {
      problem(`DOCUMENTATION cannot route ${failureType} work to an omitted owner; preserve the cycle and record the required user decision in Active Work.BlockedOn.`);
    }
    if (['FAILURE', 'USER_REWORK'].includes(kind) && failureType === 'REVIEW'
        && workflowState !== 'REVIEWING_FINAL') problem('A DOCUMENTATION REVIEW failure or rework targets REVIEWING_FINAL.');
    if (state.active.development !== 'NONE') problem('A DOCUMENTATION cycle keeps Active Work.Development NONE.');
    if (state.active.pendingVerificationCadence !== 'NONE') problem('A DOCUMENTATION cycle keeps Active Work.PendingVerificationCadence NONE.');
    if (state.active.promotionReason !== 'NONE') problem('A DOCUMENTATION cycle keeps Active Work.PromotionReason NONE; it cannot be promoted in place.');
  }
  if (cycleMode === 'EXPEDITED'
      && !['DEVELOPING', 'REVIEWING_IMPLEMENTATION', 'AWAITING_USER_SIGNOFF'].includes(workflowState)) {
    problem(`${workflowState} is not part of an EXPEDITED cycle. Promote the cycle to STANDARD first.`);
  }
  if (cycleMode === 'EXPEDITED' && (state.active.scope !== 'NONE' || state.active.architecture !== 'NONE')) {
    problem('An EXPEDITED cycle keeps Active Work.Scope and Active Work.Architecture as NONE.');
  }
  if (cycleMode === 'EXPEDITED' && state.active.promotionReason !== 'NONE') {
    problem('Active Work.PromotionReason is set, but the cycle is still EXPEDITED. Promotion sets CycleMode to STANDARD.');
  }

  if (!HANDOFF_KINDS.has(kind)) problem(`Handoff.Kind \`${kind}\` is not a handoff kind.`);
  if (from !== 'NONE' && !STATES.has(from)) problem(`Handoff.From \`${from}\` is not a workflow state.`);
  if (failureType !== 'NONE' && !FAILURE_TYPES.has(failureType)) problem(`Handoff.FailureType \`${failureType}\` is not a failure type.`);
  if (kind === 'FAILURE' && failureType === 'NONE') problem('A FAILURE handoff needs a FailureType.');
  if (kind === 'USER_REWORK' && failureType === 'NONE') {
    problem('A USER_REWORK handoff needs the FailureType of the earliest work the change invalidates.');
  }
  if (kind === 'PROMOTE') {
    if (state.active.promotionReason === 'NONE') problem('A PROMOTE handoff needs Active Work.PromotionReason naming the omitted guarantee.');
    if (cycleMode !== 'STANDARD' || workflowState !== 'AUDITING') {
      problem(`A PROMOTE handoff sets CycleMode STANDARD and WorkflowState AUDITING; found ${cycleMode} and ${workflowState}.`);
    }
  }

  const numbered = (entries, label, section) => {
    entries.forEach((entry, index) => {
      if (entry.label !== String(index + 1)) {
        problem(`${section} entries must be numbered 1, 2, 3, ... in order, each headed exactly \`### ${label} N\`; found \`${label} ${entry.label}\` in position ${index + 1}.`);
      }
    });
  };
  const { frames } = state.recovery;
  if (state.recovery.active !== (frames.length > 0)) {
    problem(`Recovery.Active is \`${state.recovery.active}\` but there ${frames.length === 1 ? 'is 1 frame' : `are ${frames.length} frames`}.`);
  }
  numbered(frames, 'Frame', 'Recovery');
  for (const frame of frames) {
    const where = `Recovery frame ${frame.label}`;
    const { From: frameFrom, Owner: owner, FailureType: frameType, ResumeAt: resumeAt } = frame.fields;
    for (const name of ['From', 'Owner', 'ResumeAt']) {
      if (!STATES.has(frame.fields[name])) problem(`${where}: \`${name}\` must be a workflow state.`);
    }
    if (!FAILURE_TYPES.has(frameType)) problem(`${where}: \`FailureType\` must be a failure type.`);
    else if (STATES.has(owner) && !ownersOf(frameType).includes(owner)) {
      problem(`${where}: a ${frameType} frame belongs to ${ownersOf(frameType).join(' or ')}, not ${owner}.`);
    }
    if (STATES.has(frameFrom) && STATES.has(resumeAt) && resumeAt !== frameFrom) {
      problem(`${where}: \`ResumeAt\` must be the interrupted state, ${frameFrom}.`);
    }
    if (!frame.fields.Reason?.trim()) problem(`${where}: \`Reason\` is missing.`);
    const rerun = frame.fields.RerunThrough;
    if (rerun !== 'NONE' && !STATES.has(rerun)) problem(`${where}: \`RerunThrough\` must be NONE or a workflow state.`);
    if (cycleMode === 'DOCUMENTATION') {
      if (frameFrom === owner) problem(`${where}: a same-state DOCUMENTATION correction does not push a recovery frame.`);
      for (const name of ['From', 'ResumeAt']) {
        if (![...DOCUMENTATION_PHASES, 'AWAITING_USER_SIGNOFF'].includes(frame.fields[name])) {
          problem(`${where}: ${name} must be an included DOCUMENTATION state.`);
        }
      }
      if (!DOCUMENTATION_PHASES.includes(owner)) problem(`${where}: Owner must be an included DOCUMENTATION role.`);
      if (['IMPLEMENTATION', 'VERIFICATION'].includes(frameType)
          || (frameType === 'REVIEW' && owner !== 'REVIEWING_FINAL')) {
        problem(`${where}: ${frameType} cannot target this owner in DOCUMENTATION; REVIEW belongs to REVIEWING_FINAL and omitted owners require a blocking user decision.`);
      }
      if (rerun !== 'NONE' && (!DOCUMENTATION_PHASES.includes(rerun)
          || DOCUMENTATION_PHASES.indexOf(rerun) <= DOCUMENTATION_PHASES.indexOf(owner))) {
        problem(`${where}: RerunThrough must be a downstream included DOCUMENTATION role, or NONE.`);
      }
    }
  }
  // Until its owner finishes the correction (RerunThrough still NONE), the
  // active frame's owner holds the workflow state.
  const activeFrame = frames.at(-1);
  if (cycleMode === 'DOCUMENTATION' && activeFrame && activeFrame.fields.RerunThrough !== 'NONE') {
    const ownerRank = DOCUMENTATION_PHASES.indexOf(activeFrame.fields.Owner);
    const boundaryRank = DOCUMENTATION_PHASES.indexOf(activeFrame.fields.RerunThrough);
    const currentRank = DOCUMENTATION_PHASES.indexOf(workflowState);
    if (ownerRank >= 0 && boundaryRank > ownerRank
        && (currentRank < ownerRank || currentRank > boundaryRank)) {
      problem(`Recovery frame ${activeFrame.label}: the active DOCUMENTATION rerun must stay between ${activeFrame.fields.Owner} and ${activeFrame.fields.RerunThrough} until its boundary returns to ResumeAt.`);
    }
  }
  if (activeFrame && activeFrame.fields.RerunThrough === 'NONE' && STATES.has(activeFrame.fields.Owner)
      && workflowState !== activeFrame.fields.Owner) {
    problem(`Recovery frame ${activeFrame.label} is still being corrected (RerunThrough is NONE), so WorkflowState must be its Owner, ${activeFrame.fields.Owner}.`);
  }
  // All corrections belong to their failure owner. Only a state-changing
  // correction pushes a frame; staying put does not waive ownership.
  if (['FAILURE', 'USER_REWORK'].includes(kind) && STATES.has(from)) {
    if (from !== workflowState
        && (!activeFrame || activeFrame.fields.From !== from || activeFrame.fields.Owner !== workflowState)) {
      problem(`Handoff.Kind is ${kind} from ${from} to ${workflowState}, so the last recovery frame must record \`From\`: \`${from}\` and \`Owner\`: \`${workflowState}\`.`);
    }
    if (FAILURE_TYPES.has(failureType) && !ownersOf(failureType).includes(workflowState)) {
      problem(`A ${failureType} ${kind === 'FAILURE' ? 'failure' : 'rework'} routes to ${ownersOf(failureType).join(' or ')}, not ${workflowState}.`);
    }
  }
  const { items } = state.obligations;
  if (state.obligations.active !== (items.length > 0)) {
    problem(`Outstanding Obligations.Active is \`${state.obligations.active}\` but there ${items.length === 1 ? 'is 1 obligation' : `are ${items.length} obligations`}.`);
  }
  numbered(items, 'Obligation', 'Outstanding Obligations');
  if (cycleMode === 'DOCUMENTATION' && (state.obligations.active || items.length)) {
    problem('DOCUMENTATION has no expedited promotion obligations; preserve recovery and use Active Work.BlockedOn for an omitted-owner decision.');
  }
  for (const item of items) {
    const where = `Obligation ${item.label}`;
    if (!STATES.has(item.fields.Owner)) problem(`${where}: \`Owner\` must be a workflow state.`);
    if (!FAILURE_TYPES.has(item.fields.FailureType)) problem(`${where}: \`FailureType\` must be a failure type.`);
    else if (STATES.has(item.fields.Owner) && !ownersOf(item.fields.FailureType).includes(item.fields.Owner)) {
      problem(`${where}: a ${item.fields.FailureType} obligation belongs to ${ownersOf(item.fields.FailureType).join(' or ')}, not ${item.fields.Owner}.`);
    }
    if (!item.fields.Reason?.trim()) problem(`${where}: \`Reason\` is missing.`);
  }
  if ((terminal || workflowState === 'AWAITING_USER_SIGNOFF') && (frames.length || items.length)) {
    problem(`${workflowState} requires an empty recovery stack and no outstanding obligations.`);
  }
  if (workflowState === 'AWAITING_USER_SIGNOFF' && state.active.blockedOn !== 'NONE') {
    problem('AWAITING_USER_SIGNOFF requires Active Work.BlockedOn NONE.');
  }

  const { baseline } = state.active;
  if (baseline.error) problem(`BaselineReconciliation ${baseline.error}.`);
  const sources = new Set();
  for (const entry of baseline.entries ?? []) {
    if (sources.has(entry.sourceCycle)) {
      problem(`BaselineReconciliation lists \`${entry.sourceCycle}\` more than once.`);
      continue;
    }
    sources.add(entry.sourceCycle);
    if (entry.sourceCycle === id) problem(`BaselineReconciliation lists the active cycle \`${id}\` itself; it lists only cancelled source cycles.`);
    if (!GENERATED_CYCLE_ID.test(entry.sourceCycle)) {
      problem(`BaselineReconciliation source \`${entry.sourceCycle}\` is not a cycle ID; copy the cancelled cycle's Active Work.Id exactly.`);
    }
  }
  if (cycleMode === 'EXPEDITED' && baseline.entries?.length) {
    problem('An EXPEDITED cycle cannot carry BaselineReconciliation; unresolved reconciliation requires STANDARD or DOCUMENTATION.');
  }
  if (workflowState === 'AWAITING_USER_SIGNOFF' && ['STANDARD', 'DOCUMENTATION'].includes(cycleMode) && baseline.entries?.length) {
    problem(`A ${cycleMode} cycle cannot await sign-off while BaselineReconciliation is unresolved.`);
  }
}

// Header values still showing the template's choices, e.g. `A | B` or `<name>`.
function unfilledFields(fields) {
  return Object.entries(fields).filter(([, value]) => isUnfilledHeaderValue(value));
}

async function checkDevelopmentPlan(root, relative, text, { mustBeComplete, report }) {
  const problem = (message) => report(relative, message);
  const status = headerFields(text).Status;
  if (status && !status.includes(' | ') && !['PROPOSED', 'APPROVED', 'IN_PROGRESS', 'COMPLETE'].includes(status)) {
    problem(`Plan Status \`${status}\` must be PROPOSED, APPROVED, IN_PROGRESS, or COMPLETE.`);
  } else if (mustBeComplete && status !== 'COMPLETE' && !status?.includes(' | ')) {
    problem(`The plan must be COMPLETE once the cycle has passed DEVELOPING; its Status is \`${status ?? 'missing'}\`.`);
  }
  const steps = developmentSteps(text);
  const ids = new Set(steps.map((step) => step.id));
  const graph = new Map();
  for (const step of steps) {
    const stepStatus = step.fields.Status;
    if (!['PENDING', 'IN_PROGRESS', 'DONE'].includes(stepStatus)) {
      problem(`${step.id}: Status must be PENDING, IN_PROGRESS, or DONE.`);
    }
    const depends = (step.fields['Depends On'] ?? 'NONE').trim();
    const targets = depends === 'NONE' ? [] : depends.split(/\s*,\s*/);
    for (const target of targets) {
      if (target === step.id) problem(`${step.id} depends on itself.`);
      else if (!ids.has(target)) problem(`${step.id} depends on ${target}, which is not a step in this plan.`);
    }
    graph.set(step.id, targets.filter((target) => ids.has(target) && target !== step.id));
    if (status === 'COMPLETE' && stepStatus !== 'DONE') problem(`The plan is COMPLETE but ${step.id} is not DONE.`);
  }
  // Dependencies must not loop.
  const visiting = new Set();
  const done = new Set();
  const visit = (node, trail) => {
    if (done.has(node)) return false;
    if (visiting.has(node)) {
      problem(`Steps depend on each other in a loop: ${[...trail, node].join(' -> ')}.`);
      return true;
    }
    visiting.add(node);
    for (const next of graph.get(node) ?? []) if (visit(next, [...trail, node])) return true;
    visiting.delete(node);
    done.add(node);
    return false;
  };
  for (const node of graph.keys()) if (visit(node, [])) break;
  const committed = await committedText(root, relative);
  if (committed !== null) {
    for (const step of developmentSteps(committed)) {
      if (step.fields.Status === 'DONE' && !ids.has(step.id)) {
        problem(`${step.id} was DONE in the last commit but is missing now. Completed steps are never removed or renumbered.`);
      }
    }
  }
}

async function checkAcceptance(root, context) {
  const { state, scope, mine, report } = context;
  const scopePath = path.posix.normalize(state.active.scope);
  const problem = (message) => report(scopePath, message);
  const inventory = acceptanceInventory(scope);
  for (const id of new Set(inventory.duplicates)) problem(`${id} is defined more than once.`);
  const current = new Set(inventory.current);
  const retired = new Set(inventory.retired);
  const previous = new Set(inventory.previous);
  for (const id of current) {
    if (retired.has(id)) problem(`${id} is both current and retired.`);
    if (previous.has(id)) problem(`${id} is both current and listed under Previous Cycles.`);
  }
  const workflowState = state.WorkflowState;
  if (state.CycleMode === 'DOCUMENTATION' && !current.size
      && (context.resumeSource === 'SCOPING' || (workflowState !== 'AUDITING'
        && workflowState !== 'SCOPING' && (!state.recovery.active || state.handoff.Kind === 'FORWARD')))) {
    problem('A DOCUMENTATION cycle needs current acceptance conditions after SCOPING completes.');
  }
  // Numbering continues across cycles, so every earlier cycle's ID is lower
  // than every ID this cycle defined. A higher ID under Previous Cycles, or
  // earlier cycles with no current conditions once scoping is done, means this
  // cycle's conditions sit below that heading, where they would be read as history.
  const number = (id) => Number(id.slice('AC-'.length));
  const own = [...current, ...retired].map(number);
  const keepLast = 'Keep "## Previous Cycles" as the document\'s last section, with this cycle\'s conditions above it.';
  const misplaced = own.length ? [...previous].filter((id) => number(id) > Math.min(...own)) : [];
  if (misplaced.length) {
    problem(`${misplaced.join(', ')} ${misplaced.length === 1 ? 'is' : 'are'} under Previous Cycles but numbered after this cycle's conditions. ${keepLast}`);
  } else if (!own.length && previous.size && workflowState !== 'SCOPING') {
    problem(`has conditions only under Previous Cycles. ${keepLast}`);
  }
  const committed = await committedText(root, scopePath);
  if (committed !== null) {
    const before = acceptanceInventory(committed);
    const now = new Set([...current, ...retired, ...previous]);
    for (const id of [...before.current, ...before.retired, ...before.previous]) {
      if (!now.has(id)) {
        problem(`${id} was in the last commit but is gone now. Move it under "Retired Acceptance Identifiers" instead of deleting it.`);
      }
    }
    for (const id of [...before.retired, ...before.previous]) {
      if (current.has(id)) problem(`${id} was retired in the last commit and cannot be used again; give the condition a new ID.`);
    }
  }

  // An ID the scope mentions without defining is written in a form that does
  // not count, such as a table row or a heading. Reporting it on the scope lets
  // Scoper's own check catch it, including in an unmarked project document.
  const known = new Set([...current, ...retired, ...previous]);
  for (const id of acceptanceMentions(scope)) {
    if (!known.has(id)) problem(`${id} appears in the scope but is not defined as a condition. ${DEFINE_CONDITION}`);
  }
  const inScope = new Set(acceptanceMentions(scope));
  // Canonical project designs may be unmarked. Their acceptance references
  // obey the same scope identity rules as cycle-owned design records.
  const referencing = [...mine];
  const architecturePath = path.posix.normalize(state.active.architecture);
  if (context.architecture !== null && !referencing.some((artifact) => artifact.path === architecturePath)) {
    referencing.push({ path: architecturePath, text: context.architecture });
  }
  for (const artifact of referencing) {
    if (artifact.path === scopePath) continue;
    const references = artifact.path === architecturePath ? withoutPreviousCycles(artifact.text) : artifact.text;
    for (const id of bareIds(references, ['AC'])) {
      if (known.has(id)) continue;
      report(artifact.path, inScope.has(id)
        ? `mentions ${id}, which appears in the scope ${scopePath} but is not defined there as a condition. ${DEFINE_CONDITION}`
        : `mentions ${id}, which is not defined in the scope ${scopePath}.`);
    }
  }

  // During recovery, states run out of order: the design and other roles'
  // records may be stale until the rerun reaches them. Only the record the
  // current state owns is checked then, since its owner is about to hand off,
  // and the design once Architect has handed it off (below). At the end of a
  // turn, the hook holds the record of the role that made the last handoff
  // instead of the current state's (see `held`).
  const recovering = state.recovery.active;
  const missing = (text) => [...current].filter((id) => !bareIds(text, ['AC']).includes(id));
  // The state whose role made the last handoff, for the given kinds. Only a
  // FORWARD, CHECKPOINT, RESUME, or FAILURE is made by its From state's own role;
  // any agent may record a USER_REWORK, whose From state's owner may not have
  // reconciled its work yet. Handoff.From stays set until the next handoff,
  // and only Scoper changes the acceptance conditions, so nothing is taken
  // from it while in SCOPING: Scoper's changes may have made that work stale.
  const handedOffBy = (kinds) => (workflowState !== 'SCOPING' && kinds.includes(state.handoff.Kind)
    ? state.handoff.From : null);
  // The design has no Status, so it is held once Architect has handed off:
  // outside recovery, in any state after ARCHITECTING; during recovery, while
  // the last handoff is a FORWARD or RESUME from ARCHITECTING, which Architect
  // makes only after passing its gate. A documentation RESUME still holds that
  // design after the last frame is popped. A FAILURE from ARCHITECTING
  // interrupts the design, so it is not held then.
  const designDone = context.fullBoundary
    || (context.resumeSource === 'ARCHITECTING' && workflowState !== 'SCOPING')
    || (recovering
      ? handedOffBy(['FORWARD', 'RESUME']) === 'ARCHITECTING'
      : pastArchitecture(workflowState, context.mode));
  if (designDone && context.architecture !== null) {
    for (const id of missing(withoutPreviousCycles(context.architecture))) {
      report(path.posix.normalize(state.active.architecture), `does not account for ${id}. Every current acceptance condition needs design coverage or "No architectural impact".`);
    }
  }
  // At the end of a turn, the current state's own record is left to its owner.
  // Right after a handoff it still carries its last COMPLETE status, and the
  // role that just took over reconciles it when it starts; the owner's own
  // check before handing off still holds it to full coverage. During recovery
  // the record of the state whose role made the last handoff is held instead:
  // that role made the handoff, so its record had to be current then.
  const justLeft = handedOffBy(['FORWARD', 'CHECKPOINT', 'RESUME', 'FAILURE']);
  const held = (phase) => {
    if (context.fullBoundary && phase === 'TESTING') return true;
    if (context.atTurnEnd && phase === workflowState) return false;
    // A completed documentation-cycle assessment still owes current coverage
    // when RESUME returns to an earlier role, even after the last frame is gone.
    if (state.CycleMode === 'DOCUMENTATION' && state.handoff.Kind === 'RESUME'
        && phase === justLeft && phase !== workflowState) return true;
    if (recovering) return phase === (context.atTurnEnd ? justLeft : workflowState);
    return rank(workflowState) >= rank(phase);
  };
  for (const artifact of mine) {
    const key = artifact.provenance.artifact === 'REVIEW'
      ? `REVIEW:${artifact.provenance.reviewKind}` : artifact.provenance.artifact;
    const phase = COMPLETED_IN[key];
    if (state.CycleMode === 'DOCUMENTATION' && !DOCUMENTATION_PHASES.includes(phase)) continue;
    const fields = headerFields(artifact.text);
    const partialAssessment = phase === 'TESTING' && ['INCREMENT', 'CORRECTION'].includes(fields['Assessment Purpose'])
      && (context.atTurnEnd ? justLeft === phase : workflowState === phase);
    if (!phase || !((fields.Status === 'COMPLETE' && held(phase)) || partialAssessment)) continue;
    // Increment history and suspended assignments do not establish current
    // acceptance coverage, whether the assessment is partial or full.
    const coverage = phase === 'TESTING' ? recordSection(artifact.text, 'Acceptance Evidence')
      : state.CycleMode === 'DOCUMENTATION' ? withoutPreviousCycles(artifact.text) : artifact.text;
    for (const id of missing(coverage)) {
      report(artifact.path, `${partialAssessment ? 'the partial assessment' : 'is COMPLETE but'} does not account for ${id}.`);
    }
  }
}

// A reference may name its file from the project root
// (`.standards/docs/reviews/<id>/implementation.md#F-003`, optionally with a
// leading `/`) or relative to the referring file, as a Markdown link would
// (`../reviews/<id>/implementation.md#F-003`). Only `./` and `../` mark a
// relative path; `.standards/...` is from the root.
function referencedRecord(byPath, from, file) {
  const fromRoot = file.replace(/^\/+/, '');
  const relative = path.posix.normalize(path.posix.join(path.posix.dirname(from), file));
  return byPath.get(/^\.\.?\//.test(file) ? relative : fromRoot) ?? byPath.get(relative) ?? byPath.get(fromRoot);
}

function checkReferences(artifact, { byPath, planIds, report }) {
  const own = LOCAL_PREFIX[artifact.provenance.artifact];
  // Entry headings define identifiers; they are not mentions of them.
  const body = withoutLinkLabels(artifact.text).replace(/^###\s+(?:F|D|DOC|DEV)-\d{3,}\b.*$/gm, '');
  for (const id of bareIds(body, QUALIFIED_PREFIXES.filter((prefix) => prefix !== own))) {
    const example = EXAMPLE_RECORD[id.replace(/-\d+$/, '')];
    report(artifact.path, `mentions ${id} without naming its record. Write it as <path>#${id}, e.g. ${example}#${id}.`);
  }
  // A bare identifier with the record's own prefix names one of its entries.
  if (own && own !== 'DEV') {
    const defined = new Set(headingIds(artifact.text, own));
    for (const id of bareIds(body, [own])) {
      if (!defined.has(id)) {
        report(artifact.path, `mentions ${id}, but it has no \`### ${id}\` entry. If it is another record's entry, write it as <path>#${id}.`);
      }
    }
  }
  // Development steps are referred to without a path and must exist in the plan.
  if (planIds) {
    for (const id of bareIds(body, ['DEV'])) {
      if (!planIds.has(id)) report(artifact.path, `mentions ${id}, which is not a step in the development plan.`);
    }
  }
  for (const reference of qualifiedReferences(artifact.text)) {
    const target = referencedRecord(byPath, artifact.path, reference.file);
    const prefix = reference.id.replace(/-\d+$/, '');
    if (!target) report(artifact.path, `refers to ${reference.file}#${reference.id}, but ${reference.file} is not a STANDARDS record.`);
    else if (!headingIds(target.text, prefix).includes(reference.id)) {
      report(artifact.path, `refers to ${reference.file}#${reference.id}, but that file has no ${reference.id}.`);
    }
  }
}

// Read a file named by the workflow, turning read failures into a message
// instead of an exception: null when it does not exist.
async function readRecord(root, relative) {
  try {
    return { text: await readText(root, relative) };
  } catch (error) {
    return { error: error.code === 'EISDIR' ? 'is a folder, not a file' : `could not be read (${error.code ?? error.message})` };
  }
}

async function checkActiveCycle(root, state, mode, artifacts, report, { atTurnEnd }) {
  const { id } = state.active;
  if (id === 'UNSET' || TERMINAL_STATES.has(state.WorkflowState)) return;
  const recovering = state.recovery.active;
  const workflowState = state.WorkflowState;
  const documentation = state.CycleMode === 'DOCUMENTATION';
  // A successful recovery handoff still owes the source role's owned output,
  // including after its last frame is popped. The receiving role may reopen
  // its own work, so a same-state return does not assert its current completion.
  const resumeSource = documentation && state.handoff.Kind === 'RESUME'
    && workflowState !== state.handoff.From ? state.handoff.From : null;
  // This recognizes the saved return shape, not the sufficiency of correction
  // evidence. The returning owner must establish the protocol's qualified gate.
  const correctiveReturn = documentation && state.handoff.Kind === 'RESUME'
    && ['DOCUMENTING', 'SYNCHRONIZING'].includes(state.handoff.From)
    && DOCUMENTATION_PHASES.includes(workflowState) && workflowState !== state.handoff.From;
  // Active Work paths, normalized so `./docs/x.md` and `docs/x.md` match.
  const active = Object.fromEntries(['scope', 'architecture', 'development'].map((name) => [name,
    state.active[name] === 'NONE' ? 'NONE' : path.posix.normalize(state.active[name])]));

  // The cycle's fixed record paths are read directly, so a record that is
  // gitignored, or whose provenance block is missing or belongs to another
  // cycle, is still examined.
  const byPathAll = new Map(artifacts.map((artifact) => [artifact.path, artifact]));
  const discovery = fixedRecords(id);
  // Documentation keeps Development NONE, so its forbidden plan cannot rely
  // on an Active Work pointer or Git's non-ignored file inventory for discovery.
  if (documentation) discovery.push({ artifact: 'DEVELOPMENT', reviewKind: null, path: fixedPath('DEVELOPMENT', id) });
  for (const record of discovery) {
    let artifact = byPathAll.get(record.path);
    if (!artifact) {
      const { text, error } = await readRecord(root, record.path);
      if (error) {
        report(record.path, `${error}.`);
        continue;
      }
      if (text === null) continue;
      const provenance = parseProvenance(text);
      if (!provenance) {
        report(record.path, `does not start with a STANDARDS provenance block, so it cannot be this cycle's ${recordName(record.artifact, record.reviewKind)} record. ${COLLISION}`);
        continue;
      }
      artifact = { path: record.path, text, provenance };
      artifacts.push(artifact);
      byPathAll.set(record.path, artifact);
      if (provenance.error) {
        report(record.path, `has a malformed provenance block (${provenance.error}). ${COLLISION}`);
      }
    }
    if (!artifact.provenance.error && artifact.provenance.cycle !== id) {
      report(record.path, `holds a ${recordName(artifact.provenance.artifact, artifact.provenance.reviewKind)} record for cycle \`${artifact.provenance.cycle}\`, not this cycle's ${recordName(record.artifact, record.reviewKind)} record. ${COLLISION}`);
    } else if (!artifact.provenance.error && (artifact.provenance.artifact !== record.artifact
        || artifact.provenance.reviewKind !== record.reviewKind)) {
      report(record.path, `must hold this cycle's ${recordName(record.artifact, record.reviewKind)} record, but holds ${recordName(artifact.provenance.artifact, artifact.provenance.reviewKind)}. ${COLLISION}`);
    }
  }

  // The scope, design, and plan named in Active Work are read directly too, so
  // a gitignored one still gets its record checks. Read errors and missing
  // provenance are reported with the Active Work paths below.
  for (const relative of [active.scope, active.architecture, active.development]) {
    if (relative === 'NONE' || byPathAll.has(relative)) continue;
    const { text } = await readRecord(root, relative);
    const provenance = text ? parseProvenance(text) : null;
    if (!provenance) continue;
    const artifact = { path: relative, text, provenance };
    artifacts.push(artifact);
    byPathAll.set(relative, artifact);
    if (provenance.error) report(relative, `has a malformed provenance block (${provenance.error}). ${COLLISION}`);
  }

  const valid = artifacts.filter((artifact) => !artifact.provenance.error);
  // Records at their required location come first, so a stray copy is the one
  // reported as the duplicate.
  const inPlace = ({ path: file, provenance: { artifact, reviewKind } }) => file === fixedPath(artifact, id, reviewKind);
  const mine = valid.filter((artifact) => artifact.provenance.cycle === id)
    .sort((left, right) => Number(inPlace(right)) - Number(inPlace(left)));
  const byPath = new Map(valid.map((artifact) => [artifact.path, artifact]));

  // The plan: the one Active Work names, or this cycle's plan record if the
  // path was never recorded, so its checks still run.
  const planPath = active.development !== 'NONE' ? active.development
    : mine.find((artifact) => artifact.provenance.artifact === 'DEVELOPMENT')?.path ?? null;
  const planText = planPath ? (byPath.get(planPath)?.text ?? (await readRecord(root, planPath)).text ?? null) : null;
  const planProvenance = planText === null ? null : parseProvenance(planText);
  const ownPlan = planProvenance?.artifact === 'DEVELOPMENT' && planProvenance.cycle === id;
  // Step references are checked only against this cycle's own plan.
  const planIds = ownPlan ? new Set(headingIds(planText, 'DEV')) : null;

  const seen = new Map();
  for (const artifact of mine) {
    const { artifact: type, reviewKind } = artifact.provenance;
    if (documentation && omittedDocumentationRecord(type, reviewKind)) {
      report(artifact.path, 'DOCUMENTATION omits this owner; a current-cycle implementation record cannot be fabricated or adopted as documentation evidence.');
    }
    const key = `${type}:${reviewKind ?? ''}`;
    if (seen.has(key)) {
      report(artifact.path, `is a second ${recordName(type, reviewKind)} record for this cycle; ${seen.get(key)} is the first.`);
      continue;
    }
    seen.set(key, artifact.path);
    const expected = fixedPath(type, id, reviewKind);
    if (expected && artifact.path !== expected) report(artifact.path, `is this cycle's ${type} record, but it must be at ${expected}.`);
    if (!HEADER_RECORDS.has(type)) continue;
    const fields = headerFields(artifact.text);
    if (fields.Cycle !== id) report(artifact.path, `shows Cycle \`${fields.Cycle ?? ''}\` but its provenance block says \`${id}\`.`);
    if (type === 'REVIEW' && fields.ReviewKind !== reviewKind) {
      report(artifact.path, `shows ReviewKind \`${fields.ReviewKind ?? ''}\` but its provenance block says \`${reviewKind}\`.`);
    }
    if (type === 'REVIEW') {
      checkClosureAssessment(artifact.text, {
        reviewKind,
        required: reviewKind === 'IMPLEMENTATION' && state.CycleMode === 'STANDARD'
          && state.active.completionPolicy === 'IMPLEMENTATION_REVIEWED'
          && workflowState === 'AWAITING_USER_SIGNOFF',
        problem: (message) => report(artifact.path, message),
      });
    }
    const unfilled = unfilledFields(fields);
    for (const [name, value] of unfilled) {
      report(artifact.path, `field \`${name}\` still shows the template's choices (\`${value}\`); set one value.`);
    }
    if (type !== 'DEVELOPMENT' && !unfilled.some(([name]) => name === 'Status')) {
      if (fields.Status === undefined) report(artifact.path, 'has no `Status` field in its header.');
      else if (!RECORD_STATUSES.includes(fields.Status)) {
        report(artifact.path, `Status \`${fields.Status}\` must be ${RECORD_STATUSES.join(', ').replace(/, (?=[^,]*$)/, ', or ')}.`);
      }
    }
    for (const local of new Set(headingIds(artifact.text, LOCAL_PREFIX[type]).filter((value, index, all) => all.indexOf(value) !== index))) {
      report(artifact.path, `defines ${local} more than once.`);
    }
    if (type === 'DOCUMENTATION' && headingIds(artifact.text, 'D').length) {
      report(artifact.path, 'numbers its discrepancies D-NNN; documentation records use DOC-NNN.');
    }
    checkReferences(artifact, { byPath, planIds: type === 'DEVELOPMENT' ? null : planIds, report });
  }

  // Paths recorded in Active Work must point at this cycle's files.
  const references = { Scope: 'SCOPE', Architecture: 'ARCHITECTURE', Development: 'DEVELOPMENT' };
  const texts = {};
  for (const [name, type] of Object.entries(references)) {
    const relative = active[name.toLowerCase()];
    if (relative === 'NONE') continue;
    // The plan has a fixed location; scope and design may be project documents.
    const required = fixedPath(type, id);
    if (required && relative !== required) {
      report(STATE_FILE, `Active Work.${name} points to ${relative}, but this cycle's ${type} record must be at ${required}.`);
    }
    const { text, error } = await readRecord(root, relative);
    if (error) {
      report(STATE_FILE, `Active Work.${name} points to ${relative}, which ${error}.`);
      continue;
    }
    if (text === null) {
      report(STATE_FILE, `Active Work.${name} points to ${relative}, which does not exist.`);
      continue;
    }
    const provenance = parseProvenance(text);
    if (provenance?.error) continue;
    if (type === 'DEVELOPMENT' && !provenance) {
      report(relative, 'is the development plan but has no provenance block.');
      continue;
    }
    if (provenance && (provenance.artifact !== type || provenance.cycle !== id)) {
      report(STATE_FILE, `Active Work.${name} points to ${relative}, which is a ${provenance.artifact} record for cycle \`${provenance.cycle}\`, not this cycle's ${type}.`);
      continue;
    }
    texts[name] = text;
  }
  // The full implementation boundary never applies to a documentation cycle.
  const fullBoundary = !documentation && rank(workflowState) >= rank('REVIEWING_IMPLEMENTATION')
    && (!recovering || ['FORWARD', 'RESUME'].includes(state.handoff.Kind));
  const verification = mine.find((artifact) => artifact.path === fixedPath('VERIFICATION', id)
    && artifact.provenance.artifact === 'VERIFICATION');
  const workflow = checkVerificationWorkflow({
    state, planText: ownPlan ? planText : null, verificationText: verification?.text ?? null,
    report, atTurnEnd, fullBoundary,
  });
  const pastDevelopment = !documentation && rank(workflowState) > rank('DEVELOPING')
    && (!recovering || fullBoundary || workflowState === 'TESTING');
  if (planPath && planText !== null && (state.active.development === 'NONE' || texts.Development !== undefined)) {
    await checkDevelopmentPlan(root, planPath, planText, { mustBeComplete: pastDevelopment && !workflow.partialDevelopment, report });
  }
  if (pastDevelopment && state.active.development === 'NONE') {
    report(STATE_FILE, `Active Work.Development is NONE, but this cycle is already in ${workflowState}.`);
  }

  const standard = state.CycleMode === 'STANDARD';
  const requirePrior = (standard || documentation)
    && (!recovering || fullBoundary || (documentation && state.handoff.Kind === 'FORWARD'));
  if (state.active.scope === 'NONE' && (resumeSource === 'SCOPING'
      || (requirePrior && (workflowState === 'ARCHITECTING' || pastArchitecture(workflowState, mode))))) {
    report(STATE_FILE, `Active Work.Scope is NONE, but ${resumeSource === 'SCOPING'
      ? 'a successful RESUME from SCOPING requires its completed scope'
      : `this ${state.CycleMode} cycle is already in ${workflowState}`}.`);
  }
  if (state.active.architecture === 'NONE' && (resumeSource === 'ARCHITECTING'
      || (requirePrior && pastArchitecture(workflowState, mode)))) {
    report(STATE_FILE, `Active Work.Architecture is NONE, but ${resumeSource === 'ARCHITECTING'
      ? 'a successful RESUME from ARCHITECTING requires its completed design'
      : `this ${state.CycleMode} cycle is already in ${workflowState}`}.`);
  }
  if (documentation && ((!recovering && !correctiveReturn && workflowState !== 'AUDITING')
      || resumeSource === 'AUDITING' || (state.handoff.Kind === 'FORWARD' && workflowState !== 'AUDITING'))) {
    const { text, error } = await readRecord(root, '.standards/CONTEXT.md');
    if (error || !text?.trim()) {
      report('.standards/CONTEXT.md', `${error ?? 'is missing or empty'}; a DOCUMENTATION cycle requires Auditor-owned context after AUDITING.`);
    }
  }
  // Require only this policy's records. Discovery and validation above still
  // examine all existing records, including those owned by omitted phases.
  if ((!recovering || fullBoundary || documentation) && ['STANDARD', 'EXPEDITED', 'DOCUMENTATION'].includes(state.CycleMode)) {
    const required = requiredCompletionRecords(id, state.CycleMode, state.active.completionPolicy);
    // An incomplete downstream corrective gate may return through Reviewer,
    // but cannot pop directly to sign-off, even when its normal phase is omitted.
    const directReturn = standard && state.active.completionPolicy === 'IMPLEMENTATION_REVIEWED'
      && workflowState === 'AWAITING_USER_SIGNOFF' && state.handoff.Kind === 'RESUME'
      && FULL_DELIVERABLE_PHASES.includes(state.handoff.From)
      ? fixedRecords(id).find((record) => record.phase === state.handoff.From) : null;
    if (directReturn) required.push(directReturn);
    for (const record of required) {
      // Recovery's later-role exceptions remain intact; Reviewer entry still
      // holds the verification record to full completion.
      const correctionSource = correctiveReturn && record.phase === state.handoff.From;
      const recoveryForward = documentation && state.handoff.Kind === 'FORWARD' && record.phase === state.handoff.From;
      const recoveryResume = documentation && state.handoff.Kind === 'RESUME'
        && record.phase === state.handoff.From && workflowState !== record.phase
        && workflowState !== 'AWAITING_USER_SIGNOFF';
      if (recovering && (documentation
        ? !correctionSource && !recoveryForward && !recoveryResume && workflowState !== 'AWAITING_USER_SIGNOFF'
        : record.phase !== 'TESTING')) continue;
      const policyReturn = state.handoff.Kind === 'COMPLETION_CHANGE' && record.reviewKind === 'IMPLEMENTATION';
      if (!policyReturn && !correctionSource && !recoveryForward && !recoveryResume && rank(workflowState) <= rank(record.phase)) continue;
      const artifact = mine.find((candidate) => candidate.path === record.path
        && candidate.provenance.artifact === record.artifact && candidate.provenance.reviewKind === record.reviewKind);
      if (!artifact) {
        if (!(await exists(root, record.path))) {
          report(record.path, record === directReturn ? 'must exist and be COMPLETE for a direct recovery return to sign-off.'
            : correctionSource ? 'must exist with current-cycle provenance for a corrective RESUME handoff.'
            : recoveryResume ? `must exist for a successful RESUME handoff from ${record.phase}.`
            : policyReturn ? 'must exist for a COMPLETION_CHANGE handoff, but it does not.'
            : `must exist once the cycle has passed ${record.phase}, but it does not.`);
        }
        continue;
      }
      // A returned Reviewer can reopen its own report while this saved handoff
      // remains. Its prior report must exist, but current work need not already
      // pass the gate. Other roles still require that review to be COMPLETE.
      if (policyReturn && workflowState === record.phase) continue;
      if (correctionSource) continue;
      // A qualified incomplete Documenter return can survive further recovery
      // before the interrupted role finishes. The latest handoff may be a
      // local correction or a RESUME from another owner. Its record still has
      // to exist; normal forward gates and readiness require full completion.
      // STANDARD retains its existing incomplete-return allowance.
      if (record.artifact === 'DOCUMENTATION' && workflowState !== 'AWAITING_USER_SIGNOFF'
          && (!documentation || (['RESUME', 'FAILURE', 'USER_REWORK'].includes(state.handoff.Kind)
            && DOCUMENTATION_PHASES.includes(workflowState)))) continue;
      const status = headerFields(artifact.text).Status;
      // A missing, unfilled, or invalid Status is already reported with the
      // record's header, so only a valid but unfinished one is reported here.
      if (['IN_PROGRESS', 'BLOCKED'].includes(status)) {
        report(record.path, record === directReturn ? `must be COMPLETE for a direct recovery return to sign-off; its Status is \`${status}\`. An incomplete corrective return must rerun implementation Reviewer.`
          : recoveryResume ? `must be COMPLETE for a successful RESUME handoff from ${record.phase}; its Status is \`${status}\`.`
          : policyReturn ? `must be COMPLETE for a COMPLETION_CHANGE handoff; its Status is \`${status}\`.`
          : `must be COMPLETE once the cycle has passed ${record.phase}; its Status is \`${status}\`.`);
      }
    }
  }
  if ((standard || documentation) && texts.Scope !== undefined) {
    await checkAcceptance(root, {
      state, mode, scope: texts.Scope, architecture: texts.Architecture ?? null, mine, report, atTurnEnd,
      fullBoundary, resumeSource,
    });
  }
  if (state.CycleMode === 'EXPEDITED') {
    for (const artifact of mine) {
      const used = bareIds(artifact.text, ['AC']);
      if (used.length) report(artifact.path, `uses acceptance IDs (${used.join(', ')}), but EXPEDITED cycles have none.`);
    }
  }
}

// `atTurnEnd` is set by the stop hook, which runs after the agent's last
// action, possibly a handoff (see checkAcceptance).
export async function runCheck(root, { atTurnEnd = false } = {}) {
  const problems = [];
  const notes = [];
  const reported = new Set();
  const report = (file, message) => {
    const key = `${file}\0${message}`;
    if (reported.has(key)) return;
    reported.add(key);
    problems.push({ file, message });
  };

  const files = {};
  for (const name of RUNTIME_FILES) {
    const { text, error } = await readRecord(root, `.standards/${name}`);
    files[name] = text ?? null;
    if (error) report(`.standards/${name}`, `${error}, so the runtime cannot be checked.`);
    else if (text === null) report(`.standards/${name}`, 'is missing, so the runtime is incomplete.');
  }
  // Merge conflicts anywhere in .standards/ stop everything else.
  const unmerged = await git(root, ['ls-files', '-u', '--', '.standards']);
  const conflicted = new Set((unmerged ?? '').split('\n').filter(Boolean).map((line) => line.split('\t')[1]));
  let standardsFiles = [];
  try {
    standardsFiles = (await readdir(path.join(root, '.standards'))).filter((name) => name.endsWith('.md'));
  } catch {
    // Reported above as missing runtime files.
  }
  for (const name of new Set([...RUNTIME_FILES, ...standardsFiles])) {
    const text = name in files ? files[name] : (await readRecord(root, `.standards/${name}`)).text;
    if (text && /^<<<<<<< /m.test(text)) conflicted.add(`.standards/${name}`);
  }
  if (conflicted.size) {
    for (const file of [...conflicted].sort()) {
      report(file, file === STATE_FILE
        ? 'has an unresolved merge conflict. Stop and ask the user to resolve it by keeping exactly one cycle.'
        : 'has an unresolved merge conflict. Stop and ask the user to resolve it.');
    }
    return { problems, notes };
  }
  if (await git(root, ['rev-parse', '--is-inside-work-tree']) === null) {
    notes.push('This project is not a git repository (or git is unavailable), so comparisons with the last commit were skipped.');
  }

  let mode = null;
  if (files['MODE.md'] !== null) {
    try { mode = modeFromFile(files['MODE.md']); } catch (error) {
      report('.standards/MODE.md', within('.standards/MODE.md')(error.message));
    }
  }
  let state = null;
  if (files['STATE.md'] !== null) {
    try {
      validateState(files['STATE.md']);
      state = parseState(files['STATE.md']);
    } catch (error) {
      report(STATE_FILE, within(STATE_FILE)(error.message));
    }
  }
  if (state) {
    // The last committed STATE.md, to catch a finished cycle's ID being reused.
    let committed = null;
    try {
      const text = await committedText(root, STATE_FILE);
      if (text !== null) committed = validateState(text);
    } catch {
      // An unreadable or invalid committed state has nothing to compare.
    }
    checkState(state, { committed, mode, report });
  }

  const artifacts = await scanArtifacts(root, {
    onUnreadable: (file, error) => notes.push(`Could not read ${file} (${error.code ?? error.message}), so it was not checked.`),
  });
  for (const artifact of artifacts) {
    if (artifact.provenance.error) {
      report(artifact.path, `has a malformed provenance block (${artifact.provenance.error}). ${COLLISION}`);
    }
  }
  if (state) await checkActiveCycle(root, state, mode, artifacts, report, { atTurnEnd });
  return { problems, notes };
}

async function main(args) {
  const json = args.length === 1 && args[0] === '--json';
  if (args.length && !json) throw new UsageError('Usage: node .standards/bin/check.mjs [--json]');
  const root = await projectRootFor(import.meta.url);
  let result;
  try {
    result = await runCheck(root);
  } catch (error) {
    // Report an unexpected failure as a problem, so --json output stays JSON.
    result = { problems: [{ file: '.standards/bin/check.mjs', message: `could not finish: ${error.message}` }], notes: [] };
  }
  const { problems, notes } = result;
  if (json) {
    process.stdout.write(`${JSON.stringify({ ok: problems.length === 0, problems, notes }, null, 2)}\n`);
  } else {
    const lines = problems.length === 0 ? ['STANDARDS check passed.']
      : [`STANDARDS check found ${problems.length} problem${problems.length === 1 ? '' : 's'}:`,
        ...problems.map(({ file, message }) => `- ${file}: ${message}`)];
    process.stdout.write(`${[...lines, ...notes.map((note) => `Note: ${note}`)].join('\n')}\n`);
  }
  return problems.length === 0 ? 0 : 1;
}

if (isMain(import.meta.url)) {
  main(process.argv.slice(2)).then((code) => { process.exitCode = code; },
    (error) => { process.exitCode = printProblem(error); });
}
