import React from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {Flash, Sfx} from '../components/Fx';
import {C, INTER, MONO, glow} from '../theme';
import {at} from '../timeline';
import {Logo} from './Intro';

export const Cta: React.FC = () => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const logo = spring({frame: f, fps, config: {damping: 14, stiffness: 100}});
  const line = spring({frame: f - at('cta', 'Test your'), fps, config: {damping: 14}});
  const rulesAt = at('cta', 'Rules decide');
  const rules = spring({frame: f - rulesAt, fps, config: {damping: 11, stiffness: 160}});
  const jevAt = at('cta', 'Jev advises');
  const jev = spring({frame: f - jevAt, fps, config: {damping: 10, stiffness: 180}});
  const ring = interpolate(f, [0, 45], [0, 1], {extrapolateRight: 'clamp'});
  const end = interpolate(f, [jevAt + 60, jevAt + 95], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center', opacity: end}}>
      <div
        style={{
          position: 'absolute', width: 1600 * ring, height: 1600 * ring, borderRadius: '50%', border: `3px solid ${C.jev}`,
          opacity: (1 - ring) * 0.8, boxShadow: glow(C.jev, 60),
        }}
      />
      <div style={{transform: `scale(${2.6 - 1.6 * logo})`, opacity: logo, filter: `blur(${(1 - logo) * 16}px)`}}>
        <Logo size={170} />
      </div>
      <div style={{fontFamily: INTER, fontWeight: 700, fontSize: 52, color: C.ink, marginTop: 52, opacity: line, transform: `translateY(${(1 - line) * 30}px)`}}>
        Test your agent's guardrails before they fail in production.
      </div>
      <div style={{fontFamily: INTER, fontWeight: 900, fontSize: 84, marginTop: 46, letterSpacing: -2, display: 'flex', gap: 24}}>
        <span style={{color: C.ink, opacity: rules, transform: `scale(${0.7 + 0.3 * rules})`, display: 'inline-block'}}>Rules decide.</span>
        <span style={{color: C.jev, textShadow: glow(C.jev, 30), opacity: jev, transform: `scale(${0.7 + 0.3 * jev})`, display: 'inline-block'}}>Jev advises.</span>
      </div>
      <div style={{fontFamily: MONO, fontWeight: 500, fontSize: 30, color: C.dim, marginTop: 54, opacity: jev}}>
        guard · calibrate · compaction · reviewers — Python 3.11+ · MIT
      </div>
      <Flash at={0} peak={0.7} len={16} />
      <Sfx at={0} name="boom" volume={0.7} />
      <Sfx at={rulesAt} name="pop" volume={0.45} />
      <Sfx at={jevAt} name="impact" volume={0.55} />
    </AbsoluteFill>
  );
};
