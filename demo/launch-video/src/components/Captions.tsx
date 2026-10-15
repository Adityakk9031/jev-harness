import React from 'react';
import {interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {C, INTER} from '../theme';
import {Word} from '../timeline';

// Spoken words that read better as figures or names on screen.
const MERGE: [string, string][] = [
  ['five hundred and seventy one', '571'],
  ['three hundred and thirty one', '331'],
  ['forty five', '45'],
  ['ninety nine percent', '99%'],
  ['fifty two', '52'],
  ['base sixty four', 'Base64'],
  ['jev harness', 'jev-harness'],
  ['path scoped', 'path-scoped'],
  ['forty', '40'],
];

type Token = {text: string; s: number; e: number; end: boolean};

const tokens = (words: Word[]): Token[] => {
  const out: Token[] = [];
  const bare = words.map((w) => w.w.toLowerCase().replace(/[^a-z0-9']/g, ''));
  for (let i = 0; i < words.length; ) {
    let merged = false;
    for (const [phrase, shown] of MERGE) {
      const parts = phrase.split(' ');
      if (parts.every((p, k) => bare[i + k] === p)) {
        const last = words[i + parts.length - 1];
        const trail = last.w.match(/[^A-Za-z0-9']+$/)?.[0] ?? '';
        const lead = /^[A-Z]/.test(words[i].w) && /^[a-z]/.test(shown) && shown !== 'jev-harness' ? shown[0].toUpperCase() + shown.slice(1) : shown;
        out.push({text: lead + trail, s: words[i].s, e: last.e, end: /[.?!:]$/.test(last.w)});
        i += parts.length;
        merged = true;
        break;
      }
    }
    if (!merged) {
      out.push({text: words[i].w, s: words[i].s, e: words[i].e, end: /[.?!:]$/.test(words[i].w)});
      i++;
    }
  }
  return out;
};

const chunk = (toks: Token[]): Token[][] => {
  const chunks: Token[][] = [];
  let cur: Token[] = [];
  for (const t of toks) {
    cur.push(t);
    const soft = /,$/.test(t.text) && cur.length >= 3;
    if (cur.length >= 6 || t.end || soft) {
      chunks.push(cur);
      cur = [];
    }
  }
  if (cur.length) chunks.push(cur);
  return chunks;
};

/** Word-timed subtitles: a short phrase at a time, the word being spoken lit up. */
export const Captions: React.FC<{words: Word[]; bottom?: number}> = ({words, bottom = 46}) => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const chunks = chunk(tokens(words));
  let idx = -1;
  for (let k = 0; k < chunks.length; k++) {
    const start = chunks[k][0].s - 3;
    const next = k + 1 < chunks.length ? chunks[k + 1][0].s - 3 : chunks[k][chunks[k].length - 1].e + 14;
    if (f >= start && f < next) idx = k;
  }
  if (idx < 0) return null;
  const c = chunks[idx];
  const pop = spring({frame: f - (c[0].s - 3), fps, config: {damping: 15, stiffness: 220}});
  return (
    <div style={{position: 'absolute', left: 0, right: 0, bottom, display: 'flex', justifyContent: 'center', pointerEvents: 'none', zIndex: 40}}>
      <div
        style={{
          fontFamily: INTER, fontWeight: 800, fontSize: 42, padding: '14px 30px', borderRadius: 18,
          background: 'rgba(4,6,12,0.82)', border: `1px solid ${C.line}`, boxShadow: '0 20px 60px rgba(0,0,0,0.55)',
          transform: `translateY(${interpolate(pop, [0, 1], [16, 0])}px) scale(${0.96 + 0.04 * pop})`, opacity: pop,
          maxWidth: 1560, textAlign: 'center', letterSpacing: -0.3, display: 'flex', gap: 12, flexWrap: 'wrap', justifyContent: 'center',
        }}
      >
        {c.map((t, k) => {
          const on = f >= t.s - 2;
          const now = f >= t.s - 2 && f < t.e + 2;
          return (
            <span key={k} style={{color: now ? C.jev : on ? C.ink : 'rgba(244,247,251,0.45)', textShadow: now ? `0 0 18px ${C.jev}88` : 'none'}}>
              {t.text}
            </span>
          );
        })}
      </div>
    </div>
  );
};
