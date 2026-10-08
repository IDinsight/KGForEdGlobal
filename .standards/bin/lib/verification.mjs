// Mechanical assignment checks shared by the checker and its stop-hook call.
// Roles still judge outcomes, execution results, and whether evidence is current.
import { VERIFICATION_CADENCES, fieldPairs } from './core.mjs';
import { developmentSteps, fixedPath, headerFieldPairs, headerFields, recordSection } from './records.mjs';

const NUMBER = /^[1-9]\d*$/;
const incrementTarget = (value) => /^Increment ([1-9]\d*)$/.exec(value ?? '')?.[1] ?? null;
const latestAssignment = (assignments, frame) => assignments.findLast((entry) => entry.frame === frame);

function required(pairs, name, problem, choices) {
  const values = pairs.filter(([key]) => key === name).map(([, value]) => value);
  if (values.length !== 1 || !values[0]?.trim()) {
    problem(`\`${name}\` must occur exactly once with a nonempty value.`);
    return null;
  }
  if (choices && !choices.includes(values[0])) {
    problem(`\`${name}\` must be ${choices.join(' or ')}; found \`${values[0]}\`.`);
    return null;
  }
  return values[0];
}

function entries(text, label) {
  const headings = [...text.matchAll(new RegExp(`^### ${label} (.*?)\\s*$`, 'gm'))];
  return headings.map((match, index) => ({
    number: match[1],
    text: text.slice(match.index + match[0].length, headings[index + 1]?.index),
  }));
}

function references(value, prefix, problem) {
  const ids = value?.split(/\s*,\s*/) ?? [];
  if (!ids.length || ids.some((id) => !new RegExp(`^${prefix}-\\d{3,}$`).test(id))) {
    problem(`must name one or more ${prefix}-NNN identifiers separated by commas.`);
    return [];
  }
  if (new Set(ids).size !== ids.length) problem('repeats an identifier within the increment.');
  return ids;
}

function readPlan(text, problem) {
  const pairs = headerFieldPairs(text);
  const cadence = required(pairs, 'Verification Cadence', problem, VERIFICATION_CADENCES);
  const current = required(pairs, 'Current Increment', problem);
  if (current !== null && current !== 'NONE' && !NUMBER.test(current)) {
    problem('`Current Increment` must be NONE or a positive increment number.');
  }
  if (cadence === 'AFTER_IMPLEMENTATION' && current !== null && current !== 'NONE') {
    problem('AFTER_IMPLEMENTATION requires `Current Increment`: `NONE`.');
  }
  const steps = new Map(developmentSteps(text).map((step) => [step.id, step]));
  const increments = new Map();
  for (const [index, entry] of entries(recordSection(text, 'Verification Increments'), 'Increment').entries()) {
    const own = (message) => problem(`Increment ${entry.number}: ${message}`);
    if (entry.number !== String(index + 1)) own('entries must be numbered 1, 2, 3, ... in order.');
    const metadata = entry.text.split(/^\*\*Ready Outcome\*\*/m)[0];
    const fields = fieldPairs(metadata);
    const dev = references(required(fields, 'Development Steps', own), 'DEV', own);
    const acceptance = references(required(fields, 'Acceptance', own), 'AC', own);
    const outcome = entry.text.split(/^\*\*Ready Outcome\*\*\s*$/m)[1]?.trim();
    if (!outcome) own('needs a nonempty **Ready Outcome**.');
    for (const id of dev) if (!steps.has(id)) own(`${id} is not a step in this plan.`);
    if (increments.has(entry.number)) own('is defined more than once.');
    increments.set(entry.number, { ...entry, dev, acceptance });
  }
  if (current && current !== 'NONE' && !increments.has(current)) {
    problem(`Current Increment ${current} has no definition in Verification Increments.`);
  }
  const header = headerFields(text);
  if (header.Status === 'COMPLETE' && current !== null && current !== 'NONE') {
    problem('A COMPLETE plan requires `Current Increment`: `NONE`.');
  }
  return { ...header, cadence, current, steps, increments };
}

function readAssessment(text, plan, problem) {
  const pairs = headerFieldPairs(text);
  const purpose = required(pairs, 'Assessment Purpose', problem, ['FULL', 'INCREMENT', 'CORRECTION']);
  const target = required(pairs, 'Assessment Target', problem);
  const increment = incrementTarget(target);
  if (purpose === 'FULL' && target !== null && target !== 'NONE') problem('FULL assessment requires Assessment Target NONE.');
  if (purpose === 'CORRECTION' && target === 'NONE') problem('CORRECTION assessment requires its specific target.');
  if (purpose === 'INCREMENT') {
    if (!increment) problem('INCREMENT assessment requires target `Increment N`.');
    else if (!plan?.increments.has(increment)) problem(`${target} has no definition in the development plan.`);
  }
  const header = headerFields(text);
  if (header.Status === 'COMPLETE' && purpose && purpose !== 'FULL') problem('A COMPLETE report requires Assessment Purpose FULL.');
  const assessments = new Map();
  for (const line of recordSection(text, 'Increment Assessments').split('\n')) {
    const cells = line.split('|').slice(1, -1).map((cell) => cell.trim());
    if (!NUMBER.test(cells[0] ?? '')) continue;
    if (cells.length !== 4 || !['VERIFIED', 'BLOCKED', 'SUPERSEDED'].includes(cells[3])) {
      problem(`Increment ${cells[0]} assessment needs outcome, evidence, and VERIFIED, BLOCKED, or SUPERSEDED disposition.`);
      continue;
    }
    if (!plan?.increments.has(cells[0])) problem(`Increment ${cells[0]} assessment has no development-plan definition.`);
    if (!cells[1] || !cells[2] || cells[2] === 'NONE') problem(`Increment ${cells[0]} assessment needs outcome and evidence.`);
    assessments.set(cells[0], { outcome: cells[1], evidence: cells[2], disposition: cells[3] });
  }
  return { ...header, purpose, target, increment, assessments };
}

function suspendedAssignments(text, section, developer, problem) {
  const seen = new Set();
  return entries(recordSection(text ?? '', section), 'Suspended Assignment').map((entry) => {
    const own = (message) => problem(`Suspended Assignment ${entry.number}: ${message}`);
    if (!NUMBER.test(entry.number)) own('needs a positive assignment number.');
    if (seen.has(entry.number)) own('assignment numbers must be unique within the artifact.');
    seen.add(entry.number);
    const fields = fieldPairs(entry.text);
    const purpose = required(fields, 'Purpose', own, developer
      ? ['DEVELOPMENT', 'INCREMENT', 'CORRECTION'] : ['FULL', 'INCREMENT', 'CORRECTION']);
    const target = required(fields, 'Target', own);
    const frame = required(fields, 'Recovery Frame', own);
    const reason = required(fields, 'Recovery Reason', own);
    required(fields, 'Assessed Inputs', own);
    required(fields, 'Next Action', own);
    if (frame !== null && !NUMBER.test(frame)) own('Recovery Frame must be a positive frame number.');
    if (purpose === 'INCREMENT' && !incrementTarget(target)) own('INCREMENT needs target `Increment N`.');
    if (['FULL', 'DEVELOPMENT'].includes(purpose) && target !== 'NONE') own(`${purpose} needs target NONE.`);
    if (purpose === 'CORRECTION' && target === 'NONE') own('CORRECTION needs its specific target.');
    return { purpose, target, frame, reason };
  });
}

// Select scope from persisted recovery assignments, not cadence or report status.
// An interrupted FULL assessment does not authorize unfinished implementation.
function recoveryScope(state, planText, verificationText, report, planPath, verificationPath) {
  const saved = {
    DEVELOPING: suspendedAssignments(planText, 'Plan Notes', true, (message) => report(planPath, message)),
    TESTING: suspendedAssignments(verificationText, 'Resume or Handoff', false, (message) => report(verificationPath, message)),
  };
  const matches = state.recovery.frames.map((frame) => {
    const assignment = latestAssignment(saved[frame.fields.From] ?? [], frame.label);
    return { frame, assignment: assignment?.reason === frame.fields.Reason ? assignment : null };
  });
  const scoped = state.recovery.active && matches.some(({ frame, assignment }) => assignment
    && (frame.fields.From === 'DEVELOPING' || ['INCREMENT', 'CORRECTION'].includes(assignment.purpose)));
  if (scoped) {
    for (const { frame, assignment } of matches) {
      const relative = { DEVELOPING: planPath, TESTING: verificationPath }[frame.fields.From];
      if (relative && !assignment) {
        report(relative, `Scoped recovery needs a suspended assignment for Frame ${frame.label} and its Recovery Reason.`);
      }
    }
  }
  return { scoped, saved };
}

function readyIncrement(plan, number, problem) {
  const increment = plan?.increments.get(number);
  if (!increment) { problem(`Checkpoint Increment ${number} has no development-plan definition.`); return; }
  if (!['APPROVED', 'IN_PROGRESS', 'COMPLETE'].includes(plan.Status) || plan['User Style Locked'] !== 'true') {
    problem('A checkpoint requires an approved plan with User Style Locked true.');
  }
  const checked = new Set();
  const visit = (id) => {
    if (checked.has(id)) return;
    checked.add(id);
    const step = plan.steps.get(id);
    if (!step) return; // The plan's reference checker reports the missing step.
    if (step.fields.Status !== 'DONE') problem(`Checkpoint Increment ${number} requires ${id} and its dependencies to be DONE.`);
    if (!step.text.split(/^\*\*Self-Check\*\*\s*$/m)[1]?.split(/^\*\*/m)[0].trim()) {
      problem(`${id} needs persisted **Self-Check** evidence before a checkpoint.`);
    }
    const depends = step.fields['Depends On'];
    if (depends && depends !== 'NONE') for (const target of depends.split(/\s*,\s*/)) visit(target);
  };
  for (const id of increment.dev) visit(id);
}

export function checkVerificationWorkflow({ state, planText, verificationText, report, atTurnEnd, fullBoundary }) {
  const planPath = state.active.development === 'NONE' ? fixedPath('DEVELOPMENT', state.active.id) : state.active.development;
  const verificationPath = fixedPath('VERIFICATION', state.active.id);
  const planProblem = (message) => report(planPath, message);
  const assessmentProblem = (message) => report(verificationPath, message);
  const plan = planText === null ? null : readPlan(planText, planProblem);
  const assessment = verificationText === null ? null : readAssessment(verificationText, plan, assessmentProblem);
  const { scoped, saved } = recoveryScope(state, planText, verificationText, report, planPath, verificationPath);
  // Active recovery uses its saved route. Only after the last frame is popped
  // can its interrupted normal increment replace that scoped assignment.
  const resumed = latestAssignment(saved.TESTING, '1');
  const resuming = !state.recovery.active && state.WorkflowState === 'TESTING' && state.handoff.Kind === 'RESUME'
    && plan?.cadence === 'INCREMENTAL' && resumed?.purpose === 'INCREMENT'
    && incrementTarget(resumed.target) === plan.current;
  const sameStateCorrection = state.WorkflowState === 'TESTING' && state.handoff.From === 'TESTING'
    && ['FAILURE', 'USER_REWORK'].includes(state.handoff.Kind) && state.handoff.FailureType === 'VERIFICATION'
    && plan?.cadence === 'INCREMENTAL' && assessment?.purpose === 'INCREMENT' && assessment.increment === plan.current;
  const incrementalAssignment = resuming || sameStateCorrection;
  if (incrementalAssignment) {
    readyIncrement(plan, plan.current, planProblem);
    if (!atTurnEnd && (assessment?.purpose !== 'INCREMENT' || assessment.increment !== plan.current)) {
      assessmentProblem(`Restore the suspended INCREMENT assessment for Increment ${plan.current} before continuing.`);
    }
  }
  if (state.CycleMode === 'EXPEDITED' && plan?.cadence !== undefined && plan.cadence !== 'AFTER_IMPLEMENTATION') {
    planProblem('An EXPEDITED cycle requires AFTER_IMPLEMENTATION cadence.');
  }
  if (state.CycleMode === 'EXPEDITED' && state.active.pendingVerificationCadence === 'INCREMENTAL') {
    report('.standards/STATE.md', 'An INCREMENTAL request requires expedited promotion before recording standard-cycle scheduling.');
  }

  let checkpoint = null;
  if (state.handoff.Kind === 'CHECKPOINT') {
    const route = `${state.handoff.From} -> ${state.WorkflowState}`;
    const targets = [...(state.handoff.Reason ?? '').matchAll(/\bIncrement ([1-9]\d*)\b/gi)];
    if (state.CycleMode !== 'STANDARD' || state.recovery.active
        || !['DEVELOPING -> TESTING', 'TESTING -> DEVELOPING'].includes(route)
        || state.handoff.FailureType !== 'NONE') {
      report('.standards/STATE.md', 'CHECKPOINT requires a STANDARD Developer/Tester exchange, FailureType NONE, and no active recovery.');
    } else if (targets.length !== 1) {
      report('.standards/STATE.md', 'CHECKPOINT Reason must identify exactly one `Increment N`.');
    } else {
      checkpoint = targets[0][1];
      if (!plan?.increments.has(checkpoint)) planProblem(`Checkpoint Increment ${checkpoint} has no definition.`);
      if (state.WorkflowState === 'TESTING') {
        if (plan?.cadence !== 'INCREMENTAL' || plan?.current !== checkpoint) {
          planProblem(`A Developer checkpoint requires INCREMENTAL cadence and Current Increment ${checkpoint}.`);
        }
        readyIncrement(plan, checkpoint, planProblem);
        if (!atTurnEnd && (assessment?.purpose !== 'INCREMENT' || assessment.increment !== checkpoint)) {
          assessmentProblem(`The assigned checkpoint requires Assessment Purpose INCREMENT and target Increment ${checkpoint}.`);
        }
      } else {
        if (assessment?.purpose !== 'INCREMENT' || assessment.increment !== checkpoint
            || assessment.Status !== 'IN_PROGRESS' || assessment.assessments.get(checkpoint)?.disposition !== 'VERIFIED') {
          assessmentProblem(`A Tester checkpoint requires an IN_PROGRESS Increment ${checkpoint} assessment recorded as VERIFIED.`);
        }
        if (!recordSection(verificationText ?? '', 'Execution Evidence', { includeFenced: true }).trim()) {
          assessmentProblem('A Tester checkpoint needs persisted Execution Evidence.');
        }
      }
    }
  }
  if (fullBoundary) {
    if (state.active.pendingVerificationCadence !== 'NONE') {
      report('.standards/STATE.md', 'Clear pending cadence intent when no implementation or increment scheduling remains before Reviewer.');
    }
    if (state.CycleMode === 'STANDARD' && assessment?.purpose && assessment.purpose !== 'FULL'
        && assessment.Status !== 'COMPLETE') {
      assessmentProblem('Reviewer entry requires Assessment Purpose FULL, including during recovery.');
    }
  }
  if (assessment?.Status === 'COMPLETE' && /\bAWAITING_IMPLEMENTATION\b/.test(recordSection(verificationText, 'Acceptance Evidence'))) {
    assessmentProblem('A COMPLETE report cannot retain AWAITING_IMPLEMENTATION acceptance work.');
  }
  // A receiving role may reopen implementation after a failure. Do not judge
  // Tester's historical COMPLETE claim against those later plan changes.
  if (!atTurnEnd && state.WorkflowState === 'TESTING'
      && assessment?.Status === 'COMPLETE' && plan && plan.Status !== 'COMPLETE') {
    assessmentProblem('A COMPLETE report requires full Developer completion; scoped recovery cannot certify unfinished implementation.');
  }
  const partialDevelopment = !fullBoundary && (scoped || incrementalAssignment
    || (checkpoint !== null && state.WorkflowState === 'TESTING'));
  if (!atTurnEnd && state.WorkflowState === 'TESTING' && assessment?.purpose) {
    if (!partialDevelopment && assessment.purpose !== 'FULL') {
      assessmentProblem('Full verification requires Assessment Purpose FULL and target NONE.');
    }
    const frame = state.recovery.frames.at(-1);
    if (scoped && frame?.fields.Owner === 'TESTING' && frame.fields.RerunThrough === 'NONE'
        && assessment.purpose !== 'CORRECTION') {
      assessmentProblem('The scoped Tester correction requires Assessment Purpose CORRECTION and its specific target.');
    }
  }
  return { partialDevelopment };
}
