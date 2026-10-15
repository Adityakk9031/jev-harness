import React from 'react';
import {AbsoluteFill, Easing, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {C, INTER, MONO, glow} from '../theme';

/**
 * Big section opener ("01 GUARD") that holds for `hold` frames, then shrinks into a corner badge.
 * With `badgeOnly`, only the corner badge is drawn (for follow-on scenes of the same tool).
 */
export const SectionTag: React.FC<{num: string; name: string; question: string; color: string; hold?: number; badgeOnly?: boolean}> = ({
  num, name, question, color, hold = 40, badgeOnly = false,
}) => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const enter = spring({frame: f, fps, config: {damping: 16, stiffness: 120}});
  const shrink = badgeOnly ? 1 : interpolate(f, [hold, hold + 16], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.bezier(0.83, 0, 0.17, 1)});
  const big = 1 - shrink;
  return (
    <>
      {big > 0.01 && (
        <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center', background: `rgba(5,7,13,${0.85 * big})`, zIndex: 30}}>
          <div style={{transform: `scale(${(0.7 + 0.3 * enter) * (0.4 + 0.6 * big)})`, opacity: big, textAlign: 'center'}}>
            <div style={{fontFamily: MONO, fontWeight: 800, fontSize: 64, color, letterSpacing: 6, textShadow: glow(color, 30)}}>
              TOOL {num}
            </div>
            <div style={{fontFamily: INTER, fontWeight: 900, fontSize: 260, color: C.ink, letterSpacing: -10, lineHeight: 1}}>
              {name}
            </div>
            <div
              style={{
                height: 8, margin: '26px auto 30px', width: `${interpolate(f, [6, 26], [0, 100], {extrapolateRight: 'clamp'})}%`,
                background: `linear-gradient(90deg, ${color}, ${C.cyan})`, borderRadius: 4, boxShadow: glow(color, 20),
              }}
            />
            <div style={{fontFamily: INTER, fontWeight: 600, fontSize: 48, color: C.dim}}>{question}</div>
          </div>
        </AbsoluteFill>
      )}
      <div
        style={{
          position: 'absolute', top: 40, left: 48, zIndex: 25, opacity: shrink, display: 'flex', alignItems: 'center', gap: 16,
          background: C.card, border: `1px solid ${color}66`, borderRadius: 999, padding: '12px 26px 12px 14px',
          boxShadow: glow(color, 16), transform: `translateX(${(1 - shrink) * -40}px)`,
        }}
      >
        <span style={{fontFamily: MONO, fontWeight: 800, fontSize: 26, color: '#05070d', background: color, borderRadius: 999, padding: '4px 14px'}}>
          {num}
        </span>
        <span style={{fontFamily: INTER, fontWeight: 800, fontSize: 32, color: C.ink}}>{name.toLowerCase()}</span>
      </div>
    </>
  );
};
