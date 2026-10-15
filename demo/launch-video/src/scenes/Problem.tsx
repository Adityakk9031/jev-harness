import React from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {Flash, Sfx} from '../components/Fx';
import {C, INTER, glow} from '../theme';
import {at} from '../timeline';

const QUESTIONS: [string, string, string, string][] = [
  ['Does your hook', '🛡️', 'Does your hook stop a disguised delete?', C.jev],
  ['Does your agent remember', '🧠', 'Does your agent remember rules after compaction?', C.violet],
  ['Does your AI reviewer', '🔍', 'Does your AI reviewer catch real bugs?', C.amber],
  ['And when a fast model', '🎯', '"99% sure"… is it?', C.cyan],
];

export const Problem: React.FC = () => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const slam = spring({frame: f - 4, fps, config: {damping: 10, stiffness: 200, mass: 0.8}});
  const shake = f < 18 ? Math.sin(f * 2.2) * (18 - f) * 0.6 : 0;
  const lift = interpolate(f, [at('problem', 'Does your hook') - 12, at('problem', 'Does your hook') + 6], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <AbsoluteFill style={{alignItems: 'center'}}>
      <div
        style={{
          marginTop: interpolate(lift, [0, 1], [420, 160]), transform: `translateX(${shake}px) scale(${(2.2 - 1.2 * slam) * (1 - 0.25 * lift)})`,
          fontFamily: INTER, fontWeight: 900, fontSize: 120, color: C.ink, letterSpacing: -4, opacity: Math.min(1, slam * 1.5), textAlign: 'center',
        }}
      >
        But almost <span style={{color: C.warn, textShadow: glow(C.warn, 30)}}>nobody tests them.</span>
      </div>
      <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 36, marginTop: 84, width: 1680}}>
        {QUESTIONS.map(([phrase, icon, text, color], i) => {
          const s = spring({frame: f - at('problem', phrase), fps, config: {damping: 13, stiffness: 150}});
          return (
            <div
              key={text}
              style={{
                display: 'flex', alignItems: 'center', gap: 30, padding: '42px 46px', borderRadius: 30, background: C.card,
                border: `2px solid ${color}88`, boxShadow: glow(color, 22 * s), opacity: s,
                transform: `translateY(${(1 - s) * 60}px) scale(${0.85 + 0.15 * s})`,
              }}
            >
              <span style={{fontSize: 88}}>{icon}</span>
              <span style={{fontFamily: INTER, fontWeight: 800, fontSize: 50, color: C.ink, lineHeight: 1.15}}>{text}</span>
              <Sfx at={at('problem', phrase)} name="pop" volume={0.4} />
            </div>
          );
        })}
      </div>
      <Flash at={4} color={C.warn} peak={0.35} len={10} />
      <Sfx at={2} name="impact" volume={0.6} />
    </AbsoluteFill>
  );
};
