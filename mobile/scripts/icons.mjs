import sharp from 'sharp';
import { mkdir } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const source = join(root, '..', 'app-icon-512.png');
const background = '#071a2b';
// Produce platform sizes from the existing approved brand asset.
// No new logo is introduced. A 1024px original can replace the 512px source later.
const ios = join(root, 'ios/App/App/Assets.xcassets');
await sharp(source).resize(1024, 1024).flatten({ background }).removeAlpha().png().toFile(join(ios, 'AppIcon.appiconset/AppIcon-512@2x.png'));
const splashLogo = await sharp(source).resize(512, 512).png().toBuffer();
const splash = await sharp({ create: { width: 2732, height: 2732, channels: 3, background } }).composite([{ input: splashLogo, gravity: 'centre' }]).png().toBuffer();
for (const name of ['splash-2732x2732.png', 'splash-2732x2732-1.png', 'splash-2732x2732-2.png']) {
  await sharp(splash).toFile(join(ios, 'Splash.imageset', name));
}
const res = join(root, 'android/app/src/main/res');
for (const [density, scale] of Object.entries({ mdpi: 1, hdpi: 1.5, xhdpi: 2, xxhdpi: 3, xxxhdpi: 4 })) {
  const folder = join(res, `mipmap-${density}`);
  await mkdir(folder, { recursive: true });
  for (const name of ['ic_launcher.png', 'ic_launcher_round.png']) {
    await sharp(source).resize(48 * scale, 48 * scale).png().toFile(join(folder, name));
  }
  const foreground = await sharp(source).resize(64 * scale, 64 * scale).png().toBuffer();
  await sharp({ create: { width: 108 * scale, height: 108 * scale, channels: 4, background: '#00000000' } })
    .composite([{ input: foreground, gravity: 'centre' }]).png().toFile(join(folder, 'ic_launcher_foreground.png'));
}
await mkdir(join(res, 'drawable'), { recursive: true });
await sharp({ create: { width: 512, height: 512, channels: 3, background } })
  .composite([{ input: await sharp(source).resize(256, 256).png().toBuffer(), gravity: 'centre' }])
  .png().toFile(join(res, 'drawable', 'splash.png'));
console.log('Generated iOS and Android icons from the existing Khadamati artwork.');
