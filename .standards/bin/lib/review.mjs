// Mechanical checks for Reviewer's separate early-closure assessment. Content
// identities and evidence sufficiency are assessed by Reviewer, not inferred
// from a COMPLETE label or from keywords in narrative findings.
import { fieldPairs } from './core.mjs';
import { FULL_DELIVERABLE_PHASES } from './completion.mjs';
import { fenceAfterLine, recordSections } from './records.mjs';

const blankComment = (text) => text.replace(/[^\r\n]/g, ' ');
const blankLine = /^[ \t]*$/;

const openingTag = /<[A-Za-z][A-Za-z0-9-]*(?:[ \t\r\n]+[A-Za-z_:][A-Za-z0-9_.:-]*(?:[ \t\r\n]*=[ \t\r\n]*(?:[^ \t\r\n"'=<>`]+|'[^']*'|"[^"]*"))?)*[ \t\r\n]*\/?>/y;
// Closing tags cannot have attributes or a self-closing slash. Otherwise a
// malformed tag could make a real comment look like a quoted attribute.
const closingTag = /<\/[A-Za-z][A-Za-z0-9-]*[ \t\r\n]*>/y;

// CommonMark's block tag names (https://spec.commonmark.org/0.31.2/#html-blocks).
const htmlBlockNames = new Set(('address article aside base basefont blockquote body caption center col colgroup dd '
  + 'details dialog dir div dl dt fieldset figcaption figure footer form frame frameset h1 h2 h3 h4 h5 h6 head '
  + 'header hr html iframe legend li link main menu menuitem nav noframes ol optgroup option p param search '
  + 'section summary table tbody td tfoot th thead title tr track ul').split(' '));

// Return the line pattern that ends an HTML block, or null for inline HTML.
// General HTML blocks end at a blank line, even inside a quoted attribute;
// comments and raw-text elements instead have an explicit ending delimiter.
function htmlBlockEnd(line, interruptingParagraph) {
  const start = /^ {0,3}(<[^\r\n]*)$/.exec(line)?.[1];
  if (!start) return null;
  if (/^<(?:pre|script|style|textarea)(?=[ \t>]|$)/i.test(start)) return /<\/(?:pre|script|style|textarea)>/i;
  if (start.startsWith('<!--')) return /-->/;
  if (start.startsWith('<?')) return /\?>/;
  if (start.startsWith('<![CDATA[')) return /\]\]>/;
  if (/^<![A-Za-z]/.test(start)) return />/;
  const name = /^<\/?([A-Za-z][A-Za-z0-9-]*)(?=[ \t]|\/?>|$)/.exec(start)?.[1];
  if (htmlBlockNames.has(name?.toLowerCase())) return /^[ \t]*$/;
  if (interruptingParagraph) return null;
  const tag = start[1] === '/' ? closingTag : openingTag;
  tag.lastIndex = 0;
  return tag.test(start) && /^[ \t]*$/.test(start.slice(tag.lastIndex)) ? /^[ \t]*$/ : null;
}

// Filter comments in a record paragraph or heading. Backtick spans and quoted
// HTML attributes are literal: their comment markers must not change evidence.
// This is record parsing, not an HTML sanitizer for rendering untrusted HTML.
function maskInlineComments(text) {
  const runs = new Map();
  for (const match of text.matchAll(/`+/g)) {
    const positions = runs.get(match[0].length) ?? [];
    positions.push(match.index);
    runs.set(match[0].length, positions);
  }
  const closingTick = (length, after) => {
    const positions = runs.get(length) ?? [];
    let low = 0;
    let high = positions.length;
    while (low < high) {
      const middle = (low + high) >>> 1;
      if (positions[middle] <= after) low = middle + 1;
      else high = middle;
    }
    return positions[low];
  };
  const parts = [];
  let kept = 0;
  for (let index = 0; index < text.length;) {
    if (text[index] === '\\') {
      index += 2;
    } else if (text[index] === '`') {
      let end = index + 1;
      while (text[end] === '`') end++;
      const length = end - index;
      const close = closingTick(length, index);
      index = close === undefined ? end : close + length;
    } else if (text.startsWith('<!--', index)) {
      // Also recognize the short HTML comment forms <!--> and <!--->.
      const close = text.indexOf('-->', index + 2);
      // An unclosed inline opener is literal text. Unclosed block comments
      // are handled separately, without consuming unrelated later sections.
      if (close === -1) break;
      const end = close + 3;
      parts.push(text.slice(kept, index), blankComment(text.slice(index, end)));
      kept = index = end;
    } else {
      const tag = text[index + 1] === '/' ? closingTag : openingTag;
      tag.lastIndex = index;
      index = text[index] === '<' && tag.test(text) ? tag.lastIndex : index + 1;
    }
  }
  parts.push(text.slice(kept));
  return parts.join('');
}

// Reference definitions are separate blocks, even without a following blank
// line. Their labels, destinations, and titles cannot open inline code spans
// in the paragraph that follows. Return a complete definition's source length.
function referenceDefinitionLength(text) {
  const label = /^ {0,3}\[((?:\\[[\]\\]|\\(?![[\]\\])|[^[\]\\])+)\]:[ \t]*(?:\n[ \t]*)?/.exec(text);
  if (!label || label[1].length > 999 || !/[^ \t\n]/.test(label[1])) return 0;
  let end = label[0].length;
  if (text[end] === '<') {
    const destination = /^<(?:\\[<>\\]|\\(?![<>\\])|[^<>\\\n])*>/.exec(text.slice(end));
    if (!destination) return 0;
    end += destination[0].length;
  } else {
    const start = end;
    let balance = 0;
    for (; end < text.length; end++) {
      const char = text[end];
      if (char.charCodeAt(0) <= 32 || char.charCodeAt(0) === 127) break;
      if (char === '\\' && /[()\\]/.test(text[end + 1] ?? '')) end++;
      else if (char === '(') balance++;
      else if (char === ')') {
        if (!balance) break;
        balance--;
      }
    }
    if (end === start || balance) return 0;
  }
  const whitespace = /^[ \t]*(?:\n[ \t]*)?/.exec(text.slice(end))[0];
  const start = end + whitespace.length;
  const opener = text[start];
  const closer = opener === '(' ? ')' : opener;
  if (whitespace && ['"', "'", '('].includes(opener)) {
    for (let index = start + 1; index < text.length; index++) {
      const char = text[index];
      if (char === '\\' && [opener, closer, '\\'].includes(text[index + 1])) index++;
      else if (char === closer) {
        const ending = /^[ \t]*\n/.exec(text.slice(index + 1));
        if (ending) return index + 1 + ending[0].length;
        break;
      } else if (opener === '(' && char === '(') break;
    }
  }
  // A malformed title on a later line does not invalidate the destination-only
  // definition; text on the destination's own line does invalidate it.
  const ending = /^[ \t]*\n/.exec(text.slice(end));
  return ending ? end + ending[0].length : 0;
}

const thematicBreak = /^ {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$/;
const listMarker = /^ {0,3}([-+*]|\d{1,9}[.)])(?: +|$)/;

function listPrefix(line, interruptingParagraph) {
  const match = thematicBreak.test(line) ? null : listMarker.exec(line);
  if (!match) return null;
  // Only a nonempty item can interrupt a paragraph, and an ordered list must
  // start with exactly 1. or 1). Other numbers can start after a block boundary.
  if (interruptingParagraph && (blankLine.test(line.slice(match[0].length))
      || (/^\d/.test(match[1]) && !['1.', '1)'].includes(match[1])))) return null;
  return match[0];
}

// Track container membership separately from leaf blocks: a list comment can
// contain blank lines, but cannot consume a later sibling item or a top-level
// section. Each entry is a quote marker or a list's continuation indentation.
function containerContent(raw, containers, { lazy, leaf }) {
  let extra = 0;
  let line = raw.replace(/\t/g, (_, offset) => {
    const width = 4 - (offset + extra) % 4;
    extra += width - 1;
    return ' '.repeat(width);
  });
  let matched = 0;
  for (const container of containers) {
    const prefix = container === '>' ? /^ {0,3}> ?/.exec(line)?.[0]
      : (line.startsWith(' '.repeat(container)) ? line.slice(0, container) : null);
    if (prefix !== null && prefix !== undefined) line = line.slice(prefix.length);
    else if (container !== '>' && blankLine.test(line)) { /* Blank lines can stay inside lists. */ }
    else break;
    matched++;
  }
  const interrupts = htmlBlockEnd(line, true) !== null || /^ {0,3}(?:#{1,6}(?: |$)|>|`{3,}|~{3,})/.test(line)
    || listPrefix(line, lazy && matched === containers.length) !== null || thematicBreak.test(line) || blankLine.test(line);
  // Paragraph continuation may omit container prefixes; comments and fences
  // cannot. Do not allow an inline backtick span to cross a new block boundary.
  if (matched < containers.length && lazy && !interrupts) return { line, boundary: false };
  let boundary = matched < containers.length;
  containers.length = matched;
  while (!leaf || boundary) {
    const quote = /^ {0,3}> ?/.exec(line)?.[0];
    const list = listPrefix(line, lazy && !boundary);
    // Five or more spaces after a list marker use one space for the marker;
    // the remaining indentation can start an indented code block.
    const prefix = quote ?? list?.replace(/ {5,}$/, ' ');
    if (!prefix) break;
    containers.push(quote ? '>' : prefix.length);
    line = line.slice(prefix.length);
    boundary = true;
  }
  return { line, boundary };
}

// Mask comments, code, and raw HTML before reading Markdown assessment fields.
// Masking keeps line endings and never joins field values or creates new
// delimiters. No npm dependencies can be used by installed tools.
function maskReviewComments(text) {
  const parts = [];
  let paragraph = [];
  let fence = null;
  let htmlEnd = null;
  const containers = [];
  const flush = () => {
    const content = paragraph.map(({ line }) => `${line}\n`).join('');
    let end = 0;
    let length;
    while ((length = referenceDefinitionLength(content.slice(end))) > 0) end += length;
    const definitions = content.slice(0, end).split('\n').length - 1;
    parts.push(blankComment(paragraph.slice(0, definitions).map(({ raw }) => raw).join('')),
      maskInlineComments(paragraph.slice(definitions).map(({ raw }) => raw).join('')));
    paragraph = [];
  };
  for (const raw of text.replace(/^\uFEFF/, '').split(/(?<=\n)/)) {
    const { line, boundary } = containerContent(raw.replace(/\r?\n$/, ''), containers,
      { lazy: paragraph.length > 0, leaf: htmlEnd !== null || fence !== null });
    if (boundary) {
      flush();
      htmlEnd = null;
      fence = null;
    }
    const htmlStart = htmlEnd === null && fence === null ? htmlBlockEnd(line, paragraph.length > 0) : null;
    if (htmlEnd !== null) {
      parts.push(blankComment(raw));
      if (htmlEnd.test(line)) htmlEnd = null;
    } else if (fence !== null) {
      parts.push(blankComment(raw));
      fence = fenceAfterLine(line, fence);
    } else if (htmlStart !== null) {
      flush();
      parts.push(blankComment(raw));
      htmlEnd = htmlStart.test(line) ? null : htmlStart;
    } else if (fenceAfterLine(line) !== null) {
      flush();
      parts.push(blankComment(raw));
      fence = fenceAfterLine(line);
    } else if (!paragraph.length && /^ {4}/.test(line)) {
      parts.push(blankComment(raw));
    } else if (blankLine.test(line)) {
      flush();
      parts.push(raw);
    } else if (thematicBreak.test(line) || /^ {0,3}(?:=+|-+)[ \t]*$/.test(line)) {
      flush();
      parts.push(raw);
    } else if (/^ {0,3}#{1,6}(?:[ \t]|$)/.test(line)) {
      flush();
      parts.push(maskInlineComments(raw));
    } else {
      paragraph.push({ raw, line });
    }
  }
  flush();
  return parts.join('');
}

export function checkClosureAssessment(text, { reviewKind, required, problem }) {
  const sections = recordSections(maskReviewComments(text), 'Implementation-Reviewed Closure');
  if (!sections.length) {
    if (required) problem('IMPLEMENTATION_REVIEWED sign-off readiness requires an Implementation-Reviewed Closure assessment.');
    return;
  }
  if (sections.length !== 1) {
    problem('Keep exactly one current Implementation-Reviewed Closure section; retain superseded conclusions under a different heading.');
    return;
  }
  if (reviewKind !== 'IMPLEMENTATION') {
    problem('Implementation-Reviewed Closure belongs only in the implementation review report.');
    return;
  }
  const pairs = fieldPairs(sections[0]);
  const fields = {};
  for (const name of ['Policy', 'Eligibility', 'User Choice', 'User Reason', 'Assessed Inputs',
    'Evidence', 'Unmet Requirements', 'Omitted Phases', 'Omitted Guarantees']) {
    const values = pairs.filter(([key]) => key === name).map(([, value]) => value.trim());
    if (values.length !== 1 || !values[0] || values[0].includes(' | ') || /^<.*>$/.test(values[0])) {
      problem(`Implementation-Reviewed Closure requires one concrete ${name} field.`);
    } else fields[name] = values[0];
  }
  if (fields.Policy && fields.Policy !== 'IMPLEMENTATION_REVIEWED') {
    problem('The closure assessment Policy must be IMPLEMENTATION_REVIEWED; it records the assessed policy, not a later coordination choice.');
  }
  if (fields.Eligibility && !['NOT_ASSESSED', 'INELIGIBLE', 'ELIGIBLE'].includes(fields.Eligibility)) {
    problem('Closure Eligibility must be NOT_ASSESSED, INELIGIBLE, or ELIGIBLE.');
  }
  if (required && fields.Eligibility !== 'ELIGIBLE') {
    problem('IMPLEMENTATION_REVIEWED sign-off readiness requires Closure Eligibility ELIGIBLE; ordinary review completion is insufficient.');
  }
  if (fields['Omitted Phases'] !== undefined
      && fields['Omitted Phases'].split(/\s*,\s*/).sort().join(',') !== [...FULL_DELIVERABLE_PHASES].sort().join(',')) {
    problem('Closure Omitted Phases must list DOCUMENTING, REVIEWING_FINAL, and SYNCHRONIZING exactly once.');
  }
  if (fields.Eligibility === 'ELIGIBLE') {
    for (const name of ['User Choice', 'Assessed Inputs', 'Evidence', 'Omitted Guarantees']) {
      if (fields[name] === 'NONE') problem(`ELIGIBLE closure requires ${name}, not NONE.`);
    }
    if (fields['Unmet Requirements'] !== undefined && fields['Unmet Requirements'] !== 'NONE') {
      problem('ELIGIBLE closure requires Unmet Requirements NONE; record remaining work as INELIGIBLE and route it to its owner.');
    }
  }
  if (fields.Eligibility === 'INELIGIBLE' && fields['Unmet Requirements'] === 'NONE') {
    problem('INELIGIBLE closure must identify the unmet requirements and their owners.');
  }
}
