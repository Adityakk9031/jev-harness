import React from 'react';
import {AbsoluteFill, Easing, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {Sfx} from '../components/Fx';
import {C, INTER, MONO, glow} from '../theme';
import {at} from '../timeline';

const CHIPS: [string, string, string][] = [
  ['Hooks', 'Hooks', C.jev],
  ['Permission rules', 'Permission rules', C.cyan],
  ['Instruction files', 'CLAUDE.md · AGENTS.md', C.violet],
  ['AI code reviewers', 'AI code reviewers', C.amber],
  ['Fast model gates', 'Fast model gates', C.warn],
];

export const Hook: React.FC = () => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const words = 'Your AI coding agent has guardrails.'.split(' ');
  const push = interpolate(f, [0, 280], [1.0, 1.14]);
  return (
    <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center', transform: `scale(${push})`}}>
      <div style={{display: 'flex', gap: 26, flexWrap: 'wrap', justifyContent: 'center', maxWidth: 1600}}>
        {words.map((w, i) => {
          const s = spring({frame: f - 8 - i * 4, fps, config: {damping: 13, stiffness: 160}});
          const isKey = w.startsWith('guardrails');
          return (
            <span
              key={i}
              style={{
                fontFamily: INTER, fontWeight: 900, fontSize: 118, letterSpacing: -4, color: isKey ? C.jev : C.ink,
                transform: `translateY(${(1 - s) * 80}px) scale(${0.7 + 0.3 * s})`, opacity: s, display: 'inline-block',
                textShadow: isKey ? glow(C.jev, 30) : 'none', filter: `blur(${(1 - s) * 10}px)`,
              }}
            >
              {w}
            </span>
          );
        })}
      </div>
      <div style={{display: 'flex', gap: 22, marginTop: 70, flexWrap: 'wrap', justifyContent: 'center', maxWidth: 1700}}>
        {CHIPS.map(([phrase, label, color], i) => {
          const start = at('hook', phrase);
          const s = spring({frame: f - start, fps, config: {damping: 11, stiffness: 170}});
          return (
            <div
              key={label}
              style={{
                fontFamily: i === 2 ? MONO : INTER, fontWeight: 800, fontSize: 40, color: C.ink, padding: '18px 32px',
                borderRadius: 999, background: C.card, border: `2px solid ${color}`, boxShadow: glow(color, 18 * s),
                transform: `scale(${s}) rotate(${(1 - s) * (i % 2 ? 8 : -8)}deg)`, opacity: Math.min(1, s * 2),
              }}
            >
              {label}
              <Sfx at={start} name="tick" volume={0.45} />
            </div>
          );
        })}
      </div>
      <div
        style={{
          position: 'absolute', bottom: 0, left: 0, right: 0, height: 6,
          background: `linear-gradient(90deg, ${C.jev}, ${C.cyan}, ${C.violet})`,
          transform: `scaleX(${interpolate(f, [0, 240], [0, 1], {easing: Easing.out(Easing.quad), extrapolateRight: 'clamp'})})`,
          transformOrigin: 'left',
        }}
      />
    </AbsoluteFill>
  );
};
