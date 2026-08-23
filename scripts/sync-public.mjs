import { copyFile, mkdir, readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const checkOnly = process.argv.includes('--check');
const mirroredFiles = [
  'index.html',
  'service-worker.js',
  'manifest.webmanifest',
  'assets/styles/khadamati-v1.css',
  'assets/scripts/khadamati-visuals.js',
  'assets/scripts/khadamati-ui-state.js',
  'app-icon-192.png',
  'app-icon-512.png',
];

const mismatches = [];
for (const relativePath of mirroredFiles) {
  const source = join(root, relativePath);
  const target = join(root, 'public', relativePath);
  if (!checkOnly) {
    await mkdir(dirname(target), { recursive: true });
    await copyFile(source, target);
  }
  const [sourceBytes, targetBytes] = await Promise.all([
    readFile(source),
    readFile(target),
  ]);
  if (!sourceBytes.equals(targetBytes)) mismatches.push(relativePath);
}

if (mismatches.length) {
  console.error(`Public mirror mismatch: ${mismatches.join(', ')}`);
  process.exitCode = 1;
} else {
  console.log(`${checkOnly ? 'Verified' : 'Synchronized'} ${mirroredFiles.length} public mirror files.`);
}
