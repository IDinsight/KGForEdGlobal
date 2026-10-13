// The installed runtime has no npm dependencies. Interpret only the Draft-07
// keywords used by the adjacent invocation schema, rejecting unsupported schema
// vocabulary instead of silently weakening validation. This is not a general
// JSON Schema implementation.
import { isDeepStrictEqual } from 'node:util';

const ANNOTATIONS = ['$schema', '$id', 'title', 'description'];
const KEYWORDS = new Set([...ANNOTATIONS, '$ref', 'definitions', 'type', 'const',
  'enum', 'pattern', 'minLength', 'minItems', 'required', 'additionalProperties',
  'properties', 'items', 'oneOf', 'anyOf', 'allOf', 'not', 'if', 'then', 'else']);
const object = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);

export function compileInvocationSchema(schema) {
  function reference(ref) {
    if (!/^#\/definitions\/[A-Za-z][A-Za-z0-9]*$/.test(ref)
        || !Object.hasOwn(schema.definitions ?? {}, ref.slice(14))) {
      throw new Error('Unsupported invocation schema reference: ' + ref);
    }
    return schema.definitions[ref.slice(14)];
  }
  function inspect(node) {
    if (!object(node)) throw new Error('Invocation schema nodes must be objects');
    for (const key of Object.keys(node)) {
      if (!KEYWORDS.has(key)) throw new Error('Unsupported invocation schema keyword: ' + key);
    }
    if (node.$ref) {
      reference(node.$ref);
      if (Object.keys(node).some((key) => key !== '$ref' && !ANNOTATIONS.includes(key))) {
        throw new Error('Invocation schema references cannot have validation siblings');
      }
    }
    if (node.type && !['object', 'array', 'string'].includes(node.type)) {
      throw new Error('Unsupported invocation schema type: ' + node.type);
    }
    if ('additionalProperties' in node && node.additionalProperties !== false) {
      throw new Error('Unsupported invocation schema additionalProperties');
    }
    if (node.pattern) new RegExp(node.pattern);
    for (const key of ['definitions', 'properties']) {
      for (const child of Object.values(node[key] ?? {})) inspect(child);
    }
    for (const key of ['items', 'not', 'if', 'then', 'else']) {
      if (node[key]) inspect(node[key]);
    }
    for (const key of ['oneOf', 'anyOf', 'allOf']) {
      for (const child of node[key] ?? []) inspect(child);
    }
  }
  inspect(schema);

  function errors(value, node, at, depth = 0) {
    if (depth > 128) return [at + ': metadata nesting is too deep'];
    const check = (sub, item = value, location = at) => errors(item, sub, location, depth + 1);
    if (node.$ref) return check(reference(node.$ref));
    const result = [];
    const bad = (message) => result.push(at + ': ' + message);
    if (node.type && !(node.type === 'object' ? object(value)
      : node.type === 'array' ? Array.isArray(value) : typeof value === 'string')) {
      return [at + ': expected ' + node.type];
    }
    if ('const' in node && !isDeepStrictEqual(value, node.const)) bad('expected ' + JSON.stringify(node.const));
    if (node.enum && !node.enum.some((item) => isDeepStrictEqual(item, value))) bad('unsupported value ' + JSON.stringify(value));
    if (typeof value === 'string') {
      if (node.minLength && [...value].length < node.minLength) bad('string is too short');
      if (node.pattern && !new RegExp(node.pattern).test(value)) bad('invalid string ' + JSON.stringify(value));
    }
    if (Array.isArray(value)) {
      if (node.minItems && value.length < node.minItems) bad('array is empty');
      if (node.items) value.forEach((item, index) => result.push(...check(node.items, item, at + '[' + index + ']')));
    }
    if (object(value)) {
      for (const key of node.required ?? []) if (!Object.hasOwn(value, key)) bad('missing ' + key);
      for (const [key, item] of Object.entries(value)) {
        if (Object.hasOwn(node.properties ?? {}, key)) result.push(...check(node.properties[key], item, at + '.' + key));
        else if (node.additionalProperties === false) bad('unknown field ' + key);
      }
    }
    for (const sub of node.allOf ?? []) result.push(...check(sub));
    for (const key of ['oneOf', 'anyOf']) {
      if (!node[key]) continue;
      const alternatives = node[key].map((sub) => check(sub));
      const matches = alternatives.filter((issues) => !issues.length).length;
      if (key === 'oneOf' ? matches !== 1 : matches === 0) {
        bad('must match ' + (key === 'oneOf' ? 'exactly one' : 'at least one') + ' allowed shape');
        if (matches === 0) result.push(...alternatives.sort((a, b) => a.length - b.length)[0]);
      }
    }
    if (node.not && !check(node.not).length) bad('forbidden field combination');
    if (node.if) {
      const branch = check(node.if).length ? node.else : node.then;
      if (branch) result.push(...check(branch));
    }
    return result;
  }
  return (value) => errors(value, schema, '$');
}
