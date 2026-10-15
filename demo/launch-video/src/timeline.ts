import timing from './narration-timing.json';
import narration from './narration.json';
import config from './scene-config.json';
import wordTimes from './words.json';

export const FPS = config.fps;
export const W = 1920;
export const H = 1080;

export type SceneId = keyof typeof timing;
export type Word = {w: string; s: number; e: number}; // s/e: frames from the scene start

export type Scene = {
  id: SceneId;
  start: number; // frame the scene starts
  lead: number; // frames before the voice
  voice: number; // frames of voice
  duration: number; // total frames
  text: string;
  words: Word[];
};

const texts = Object.fromEntries(narration.map((n) => [n.id, n.text])) as Record<SceneId, string>;
const leads = config.lead as Partial<Record<SceneId, number>>;
const tails = config.tail as Partial<Record<SceneId, number>>;

export const SCENES: Scene[] = (() => {
  let start = 0;
  return (config.order as SceneId[]).map((id) => {
    const lead = Math.round((leads[id] ?? config.defaultLead) * FPS);
    const voice = Math.round(timing[id] * FPS);
    const tail = Math.round((tails[id] ?? config.defaultTail) * FPS);
    const words = (wordTimes as Record<string, {w: string; s: number; e: number}[]>)[id].map((x) => ({
      w: x.w,
      s: lead + x.s * FPS,
      e: lead + x.e * FPS,
    }));
    const scene = {id, start, lead, voice, duration: lead + voice + tail, text: texts[id], words};
    start += scene.duration;
    return scene;
  });
})();

export const TOTAL = SCENES.reduce((sum, s) => sum + s.duration, 0);

export const sceneById = (id: SceneId): Scene => SCENES.find((s) => s.id === id)!;

const clean = (w: string) => w.toLowerCase().replace(/[^a-z0-9']/g, '');

/**
 * Frame (relative to the scene start) at which `phrase` starts being spoken, from word-level
 * timings. `early` pulls the cue forward so motion lands on the word rather than after it.
 */
export const at = (id: SceneId, phrase: string, early = 3): number => {
  const {words} = sceneById(id);
  const want = phrase.split(/\s+/).map(clean).filter(Boolean);
  const have = words.map((w) => clean(w.w));
  for (let i = 0; i + want.length <= have.length; i++) {
    if (want.every((w, k) => have[i + k] === w)) return Math.max(0, Math.round(words[i].s) - early);
  }
  throw new Error(`phrase not in ${id}: ${phrase}`);
};

/** Frame (relative to the scene start) at which `phrase` has finished being spoken. */
export const after = (id: SceneId, phrase: string): number => {
  const {words} = sceneById(id);
  const want = phrase.split(/\s+/).map(clean).filter(Boolean);
  const have = words.map((w) => clean(w.w));
  for (let i = 0; i + want.length <= have.length; i++) {
    if (want.every((w, k) => have[i + k] === w)) return Math.round(words[i + want.length - 1].e);
  }
  throw new Error(`phrase not in ${id}: ${phrase}`);
};
