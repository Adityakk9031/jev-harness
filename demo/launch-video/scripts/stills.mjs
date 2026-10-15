// Render selected frames as JPEG stills for review:  node scripts/stills.mjs <outDir> <frame>...
import {bundle} from '@remotion/bundler';
import {openBrowser, renderStill, selectComposition} from '@remotion/renderer';
import fs from 'node:fs';
import path from 'node:path';

const [outDir, ...rest] = process.argv.slice(2);
const frames = rest.join(" ").split(/\s+/).filter(Boolean).map(Number);
fs.mkdirSync(outDir, {recursive: true});
const serveUrl = await bundle({entryPoint: path.resolve('src/index.ts')});
const browser = await openBrowser('chrome');
const composition = await selectComposition({serveUrl, id: 'Launch', puppeteerInstance: browser});
console.log(`duration ${composition.durationInFrames} frames`);
for (const frame of frames) {
  const output = path.join(outDir, `${String(frame).padStart(5, '0')}.jpg`);
  await renderStill({serveUrl, composition, frame, output, imageFormat: 'jpeg', jpegQuality: 82, scale: 0.5, puppeteerInstance: browser});
}
await browser.close({silent: true});
console.log(`rendered ${frames.length} stills to ${outDir}`);
