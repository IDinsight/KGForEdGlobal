// Mechanical completion requirements. STANDARD's gate inventory is not its
// execution order: project mode determines its entry route. DOCUMENTATION has
// one fixed Brownfield route. Recovery may interrupt normal execution order.
import { COMPLETION_POLICIES } from './core.mjs';

const STANDARD_UPSTREAM = ['SCOPING', 'ARCHITECTING', 'AUDITING', 'DEVELOPING',
  'TESTING', 'REVIEWING_IMPLEMENTATION'];
export const FULL_DELIVERABLE_PHASES = ['DOCUMENTING', 'REVIEWING_FINAL', 'SYNCHRONIZING'];
export const DOCUMENTATION_PHASES = Object.freeze(['AUDITING', 'SCOPING', 'ARCHITECTING',
  ...FULL_DELIVERABLE_PHASES]);

// Omitted owners cannot manufacture current-cycle evidence in this topology.
export function omittedDocumentationRecord(type, reviewKind) {
  return ['DEVELOPMENT', 'VERIFICATION'].includes(type)
    || (type === 'REVIEW' && reviewKind === 'IMPLEMENTATION');
}

// null means an invalid mode/policy combination; there is no inferred default.
// UNSET has no active gates and may retain a finished cycle's policy.
export function requiredCompletionPhases(cycleMode, policy) {
  if (!COMPLETION_POLICIES.includes(policy)) return null;
  if (cycleMode === 'UNSET') return [];
  if (cycleMode === 'EXPEDITED') return policy === 'NONE'
    ? ['DEVELOPING', 'REVIEWING_IMPLEMENTATION'] : null;
  if (cycleMode === 'DOCUMENTATION') return policy === 'NONE' ? [...DOCUMENTATION_PHASES] : null;
  if (cycleMode !== 'STANDARD' || policy === 'NONE') return null;
  return policy === 'FULL_DELIVERABLE'
    ? [...STANDARD_UPSTREAM, ...FULL_DELIVERABLE_PHASES] : [...STANDARD_UPSTREAM];
}

// Only these state-changing policy choices use COMPLETION_CHANGE. A same-state
// choice preserves its previous handoff, including an incremental checkpoint.
export function completionChangeRoute(from, to) {
  if (from === 'DOCUMENTING' && to === 'REVIEWING_IMPLEMENTATION') {
    return { fromPolicy: 'FULL_DELIVERABLE', toPolicy: 'IMPLEMENTATION_REVIEWED' };
  }
  if (from === 'AWAITING_USER_SIGNOFF' && to === 'DOCUMENTING') {
    return { fromPolicy: 'IMPLEMENTATION_REVIEWED', toPolicy: 'FULL_DELIVERABLE' };
  }
  return null;
}
