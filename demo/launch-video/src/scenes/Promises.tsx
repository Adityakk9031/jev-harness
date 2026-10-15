import React from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {Sfx} from '../components/Fx';
import {C, INTER, MONO, glow} from '../theme';
import {at} from '../timeline';

const ITEMS: [string, string, string, string][] = [
  ['Payloads are never', '01', 'Payloads are never executed', C.warn],
  ['Your real setup', '02', 'Your real setup is never touched', C.cyan],
  ['Every number', '03', 'Every number shows its sample size', C.jev],
  ['every model call', '04', 'Every model call is capped', C.amber],
];

export const Promises: React.FC = () => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const push = interpolate(f, [0, 260], [1.04, 1.0]);
  return (
    <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center', transform: `scale(${push})`}}>
      <div style={{fontFamily: MONO, fontWeight: 800, fontSize: 32, color: C.jev, letterSpacing: 8, marginBottom: 40, opacity: spring({frame: f, fps})}}>
        THE PROMISES
      </div>
      <div style={{display: 'flex', flexDirection: 'column', gap: 26}}>
        {ITEMS.map(([phrase, num, text, color], i) => {
          const start = at('promises', phrase);
          const s = spring({frame: f - start, fps, config: {damping: 12, stiffness: 170}});
          return (
            <div
              key={text}
              style={{
                display: 'flex', alignItems: 'center', gap: 34, width: 1240, padding: '26px 44px', borderRadius: 26, background: C.card,
                border: `2px solid ${color}99`, boxShadow: glow(color, 20 * s), opacity: s,
                transform: `translateX(${(1 - s) * (i % 2 ? 260 : -260)}px) scale(${0.9 + 0.1 * s})`,
              }}
            >
              <span style={{fontFamily: MONO, fontWeight: 800, fontSize: 40, color: '#05070d', background: color, borderRadius: 14, padding: '6px 16px'}}>{num}</span>
              <span style={{fontFamily: INTER, fontWeight: 900, fontSize: 58, color: C.ink, letterSpacing: -1}}>{text}</span>
              <Sfx at={start} name="tick" volume={0.5} />
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
