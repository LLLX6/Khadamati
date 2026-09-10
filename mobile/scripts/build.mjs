import { cp, mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { approvedApiBase } from '../src/native-helpers.mjs';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const source = join(root, '..', 'public');
const output = join(root, 'www');
const apiBase = approvedApiBase(process.env.KHADAMATI_MOBILE_API_BASE || 'https://khadamati-app-api.onrender.com');
// An allowlist prevents databases, private uploads, policies still in draft,
// repository metadata, or server secrets from being packaged into the app.
const roots = ['index.html', 'assets', 'vendor', 'app-icon-192.png', 'app-icon-512.png', 'apple-touch-icon.png', 'logo.svg'];
const extensions = /\.(?:html|css|js|mjs|json|png|webp|jpe?g|svg|gif|ico|woff2?|ttf|mp3|mp4|ogg)$/i;
await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
for (const path of roots) {
  await cp(join(source, path), join(output, path), {
    recursive: true,
    filter: async path => {
      const name = path.slice(path.lastIndexOf('/') + 1);
      if (name.startsWith('.')) return false;
      const { lstat } = await import('node:fs/promises');
      const stat = await lstat(path);
      if (stat.isSymbolicLink()) throw new Error(`Symlinks cannot be packaged: ${name}`);
      return stat.isDirectory() || extensions.test(name);
    },
  });
}
let html = await readFile(join(output, 'index.html'), 'utf8');
html = html.replace(/<link rel="manifest"[^>]*>\r?\n/, '');
const marker = '<script src="assets/scripts/khadamati-i18n-data.js';
if (!html.includes(marker)) throw new Error('Native bridge insertion point changed.');
html = html.replace(marker, `<script src="native-bridge.js"></script>\n${marker}`);
await writeFile(join(output, 'index.html'), html);
await build({
  entryPoints: [join(root, 'src/native-bridge.js')],
  outfile: join(output, 'native-bridge.js'), bundle: true, format: 'iife', target: 'es2020',
  minify: true, legalComments: 'eof', define: { __KHADAMATI_MOBILE_API__: JSON.stringify(apiBase) },
});
async function fileCount(path) {
  let count = 0;
  for (const entry of await readdir(path, { withFileTypes: true })) count += entry.isDirectory() ? await fileCount(join(path, entry.name)) : 1;
  return count;
}
console.log(`Bundled ${await fileCount(output)} public files. API: ${apiBase}. No service worker or private uploads included.`);
