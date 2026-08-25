import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const files = [
  join(root, 'index.html'),
  join(root, 'assets', 'scripts', 'khadamati-ui-state.js'),
];

function decodeQuoted(raw) {
  try {
    return Function(`"use strict";return (${raw});`)();
  } catch (_) {
    return '';
  }
}

function templateText(raw) {
  if (!raw) return '';
  if (!raw.startsWith('`') || !raw.endsWith('`')) {
    if (!((raw.startsWith("'") && raw.endsWith("'")) || (raw.startsWith('"') && raw.endsWith('"')))) return '';
    const value = decodeQuoted(raw);
    return typeof value === 'string' ? value : '';
  }
  let result = '';
  let placeholder = 0;
  for (let index = 1; index < raw.length - 1; index += 1) {
    const char = raw[index];
    if (char === '\\') {
      const next = raw[index + 1];
      if (next === 'n') result += '\n';
      else if (next === 'r') result += '\r';
      else if (next === 't') result += '\t';
      else result += next || '';
      index += 1;
      continue;
    }
    if (char !== '$' || raw[index + 1] !== '{') {
      result += char;
      continue;
    }
    index += 2;
    let depth = 1;
    let quote = '';
    for (; index < raw.length - 1 && depth; index += 1) {
      const token = raw[index];
      if (quote) {
        if (token === '\\') index += 1;
        else if (token === quote) quote = '';
        continue;
      }
      if (token === "'" || token === '"' || token === '`') quote = token;
      else if (token === '{') depth += 1;
      else if (token === '}') depth -= 1;
    }
    index -= 1;
    result += `{{${placeholder}}}`;
    placeholder += 1;
  }
  return result;
}

function splitCallArguments(source, openParen) {
  const args = [];
  let start = openParen + 1;
  let depth = 0;
  let quote = '';
  let templateExpressionDepth = 0;
  for (let index = start; index < source.length; index += 1) {
    const char = source[index];
    if (quote) {
      if (char === '\\') {
        index += 1;
        continue;
      }
      if (quote === '`' && char === '$' && source[index + 1] === '{') {
        templateExpressionDepth += 1;
        depth += 1;
        index += 1;
        continue;
      }
      if (char === quote && templateExpressionDepth === 0) quote = '';
      else if (quote === '`' && char === '}' && templateExpressionDepth > 0) {
        templateExpressionDepth -= 1;
        depth -= 1;
      }
      continue;
    }
    if (char === "'" || char === '"' || char === '`') {
      quote = char;
      continue;
    }
    if (char === '(' || char === '[' || char === '{') depth += 1;
    else if (char === ')' || char === ']' || char === '}') {
      if (char === ')' && depth === 0) {
        args.push(source.slice(start, index).trim());
        return args;
      }
      depth -= 1;
    } else if (char === ',' && depth === 0) {
      args.push(source.slice(start, index).trim());
      start = index + 1;
    }
  }
  return [];
}

function extractLocalizedEnglish(source) {
  const phrases = new Set();
  const matcher = /(?:^|[^A-Za-z0-9_$])L\s*\(/g;
  let match;
  while ((match = matcher.exec(source))) {
    const openParen = source.indexOf('(', match.index);
    const args = splitCallArguments(source, openParen);
    if (args.length < 2) continue;
    const english = templateText(args[1]);
    if (english) phrases.add(english.trim());
  }
  return phrases;
}

function extractQuotedEnglish(source) {
  const phrases = new Set();
  const matcher = /(['"])((?:\\.|(?!\1)[^\\\r\n])*)\1/g;
  let match;
  while ((match = matcher.exec(source))) {
    const value = decodeQuoted(match[0]).trim();
    if (!/[A-Za-z]/.test(value) || value.length < 2 || value.length > 700) continue;
    if (/[\u0600-\u06ff\u0750-\u077f]/.test(value) || value.startsWith('--') || value.startsWith('_') || value.includes('${')) continue;
    if (/^(?:https?:|\/api\/|\.\/|\.\.|[.#\[\]{}():=+*?^$\\/]|data:|image\/|audio\/)/i.test(value)) continue;
    if (/^[a-z][a-z0-9_.:/-]*$/i.test(value) && !/^[A-Z][a-z]+$/.test(value)) continue;
    if (/^[A-Z0-9_ -]+$/.test(value) && value.includes('_')) continue;
    if (!/[\s.,!?;:'’()-]/.test(value) && value.length > 24) continue;
    phrases.add(value);
  }
  return phrases;
}

const sourceParts = await Promise.all(files.map(file => readFile(file, 'utf8')));
const phrases = new Set();
for (const source of sourceParts) {
  extractLocalizedEnglish(source).forEach(value => phrases.add(value));
  extractQuotedEnglish(source).forEach(value => phrases.add(value));
}

[
  'Language', 'Choose language', 'Arabic', 'English', 'Hindi', 'Bengali', 'Urdu',
  'Show the app in Arabic', 'Show the app in English', 'Show the app in Hindi',
  'Show the app in Bengali', 'Show the app in Urdu', 'Awaiting update',
].forEach(value => phrases.add(value));

const ordered = [...phrases]
  .map(value => value.replace(/\s+/g, ' ').trim())
  .filter(Boolean)
  .sort((a, b) => a.localeCompare(b, 'en'));

const outputPath = join(root, 'tmp', 'i18n-source.json');
await mkdir(dirname(outputPath), { recursive: true });
await writeFile(outputPath, `${JSON.stringify(ordered, null, 2)}\n`, 'utf8');
console.log(`Extracted ${ordered.length} English interface phrases to ${outputPath}`);
