import React from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {CameraLayer} from '../components/Camera';
import {Sfx} from '../components/Fx';
import {C, INTER, MONO, glow} from '../theme';
import {at} from '../timeline';

const useIn = (start: number, damping = 14) => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  return spring({frame: f - start, fps, config: {damping, stiffness: 140}});
};

const Box: React.FC<{x: number; y: number; w: number; h: number; s: number; color: string; children: React.ReactNode; glowOn?: number}> = ({
  x, y, w, h, s, color, children, glowOn = 1,
}) => (
  <div
    style={{
      position: 'absolute', left: x, top: y, width: w, height: h, borderRadius: 26, background: C.card,
      border: `2px solid ${color}aa`, boxShadow: `${glow(color, 22 * glowOn)}, 0 30px 70px rgba(0,0,0,0.5)`,
      opacity: Math.min(1, s * 1.6), transform: `scale(${0.8 + 0.2 * s}) translateY(${(1 - s) * 40}px)`, padding: 26,
      boxSizing: 'border-box',
    }}
  >
    {children}
  </div>
);

const Wire: React.FC<{d: string; p: number; color: string; dashed?: boolean}> = ({d, p, color, dashed}) => (
  <path
    d={d} fill="none" stroke={color} strokeWidth={4} strokeLinecap="round" pathLength={1}
    strokeDasharray={dashed ? '0.02 0.02' : '1 1'} strokeDashoffset={dashed ? -p * 0.5 : 1 - p}
    style={{filter: `drop-shadow(0 0 8px ${color})`, opacity: p > 0 ? 1 : 0}}
  />
);

const CHIPS: [string, string, string][] = [
  ['reads agent sessions', 'Session importers', 'Claude Code · Codex'],
  ['runs agents in a throwaway', 'Throwaway workspace', 'your real repo is never touched'],
  ['runs agents in a throwaway', 'Agent runner', 'headless claude / codex'],
  ['computes honest statistics', 'Honest statistics', 'every rate has n + interval'],
  ['writes reports', 'Reports', 'terminal · markdown · CI'],
  ['The rules always', 'Policy engine', 'rules have the final say'],
];
const TOOLS: [string, string, string][] = [
  ['🛡️', 'guard', C.jev],
  ['🎯', 'calibrate', C.cyan],
  ['🧠', 'compaction', C.violet],
  ['🔍', 'reviewers', C.amber],
];

export const Architecture: React.FC = () => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const setup = useIn(4);
  const core = useIn(at('arch', 'one shared core'));
  const tools = at('arch', 'four tools');
  const jev = useIn(at('arch', 'And Jev'));
  const rules = at('arch', 'The rules always');
  const banner = useIn(rules, 11);
  const wire = (start: number) => interpolate(f, [start, start + 20], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const coreAt = at('arch', 'one shared core');
  const jevAt = at('arch', 'And Jev');
  const stops = [
    {at: 0, cx: 960, cy: 540, s: 1.0},
    {at: coreAt, cx: 975, cy: 660, s: 1.5},
    {at: tools - 4, cx: 975, cy: 480, s: 1.1},
    {at: jevAt - 2, cx: 1250, cy: 560, s: 1.22},
    {at: rules - 4, cx: 960, cy: 540, s: 0.97},
  ];
  const label = interpolate(f, [tools - 10, tools], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <AbsoluteFill>
    <CameraLayer stops={stops}>
      <div style={{position: 'absolute', top: 54, left: 0, right: 0, textAlign: 'center', fontFamily: MONO, fontWeight: 800, fontSize: 34, color: C.jev, letterSpacing: 8, opacity: setup * label}}>
        UNDER THE HOOD
      </div>
      <svg width={1920} height={1080} style={{position: 'absolute', inset: 0}}>
        <Wire d="M 470 610 C 540 610, 560 610, 640 610" p={wire(at('arch', 'one shared core') + 6)} color={C.cyan} />
        {TOOLS.map((_, i) => (
          <Wire key={i} d={`M ${745 + i * 190} 430 L ${745 + i * 190} 330`} p={wire(tools + 4 + i * 4)} color={TOOLS[i][2]} />
        ))}
        <Wire d="M 1470 640 C 1420 640, 1380 790, 1310 790" p={wire(at('arch', 'And Jev') + 8)} color={C.jev} dashed />
      </svg>
      <Box x={90} y={420} w={380} h={380} s={setup} color={C.dim}>
        <div style={{fontFamily: MONO, fontWeight: 800, fontSize: 24, color: C.dim, letterSpacing: 3}}>YOUR AGENT SETUP</div>
        {['Hooks', 'Permission rules', 'CLAUDE.md · AGENTS.md', 'AI reviewers', 'Model gates'].map((t, i) => {
          const s = spring({frame: f - 8 - i * 4, fps, config: {damping: 15}});
          return (
            <div key={t} style={{fontFamily: INTER, fontWeight: 700, fontSize: 32, color: C.ink, marginTop: 18, opacity: s, transform: `translateX(${(1 - s) * -30}px)`}}>
              • {t}
            </div>
          );
        })}
      </Box>
      <Box x={640} y={430} w={670} h={470} s={core} color={C.cyan} glowOn={1.2}>
        <div style={{fontFamily: INTER, fontWeight: 900, fontSize: 40, color: C.ink}}>
          jev-harness <span style={{color: C.cyan}}>core</span>
        </div>
        <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginTop: 20}}>
          {CHIPS.map(([phrase, name, sub], i) => {
            const s = spring({frame: f - at('arch', phrase) - (i === 2 ? 6 : 0), fps, config: {damping: 13, stiffness: 160}});
            const isPolicy = name === 'Policy engine';
            const hot = isPolicy ? interpolate(f, [rules, rules + 10], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}) : 0;
            return (
              <div
                key={name}
                style={{
                  padding: '14px 18px', borderRadius: 16, background: isPolicy ? `rgba(124,255,178,${0.08 + 0.14 * hot})` : 'rgba(255,255,255,0.05)',
                  border: `1px solid ${isPolicy ? C.jev : C.line}`, opacity: s, transform: `scale(${0.8 + 0.2 * s})`,
                  boxShadow: hot ? glow(C.jev, 26 * hot) : 'none',
                }}
              >
                <div style={{fontFamily: INTER, fontWeight: 800, fontSize: 27, color: C.ink}}>{name}</div>
                <div style={{fontFamily: INTER, fontWeight: 600, fontSize: 20, color: C.dim, marginTop: 4}}>{sub}</div>
              </div>
            );
          })}
        </div>
      </Box>
      {TOOLS.map(([icon, name, color], i) => {
        const s = spring({frame: f - tools - i * 5, fps, config: {damping: 11, stiffness: 170}});
        return (
          <div
            key={name}
            style={{
              position: 'absolute', left: 655 + i * 190, top: 160, width: 180, height: 170, borderRadius: 24, background: C.card,
              border: `2px solid ${color}`, boxShadow: glow(color, 24 * s), display: 'flex', flexDirection: 'column', alignItems: 'center',
              justifyContent: 'center', gap: 10, opacity: s, transform: `translateY(${(1 - s) * -60}px) scale(${0.7 + 0.3 * s})`,
            }}
          >
            <span style={{fontSize: 62}}>{icon}</span>
            <span style={{fontFamily: INTER, fontWeight: 800, fontSize: 27, color: C.ink}}>{name}</span>
          </div>
        );
      })}
      <div
        style={{
          position: 'absolute', left: 1470, top: 470, width: 340, height: 340, borderRadius: '50%', display: 'flex', flexDirection: 'column',
          alignItems: 'center', justifyContent: 'center', background: `radial-gradient(circle, ${C.jev}33, ${C.card})`,
          border: `3px dashed ${C.jev}`, boxShadow: glow(C.jev, 40 * jev), opacity: jev, transform: `scale(${0.6 + 0.4 * jev}) rotate(${(1 - jev) * 40}deg)`,
        }}
      >
        <div style={{fontFamily: INTER, fontWeight: 900, fontSize: 72, color: C.jev}}>Jev</div>
        <div style={{fontFamily: INTER, fontWeight: 700, fontSize: 30, color: C.ink}}>the judge</div>
        <div style={{fontFamily: INTER, fontWeight: 600, fontSize: 24, color: C.dim, marginTop: 6}}>second opinion</div>
      </div>
      <div
        style={{
          position: 'absolute', left: 0, right: 0, top: 46, display: 'flex', justifyContent: 'center', opacity: banner,
          transform: `scale(${0.8 + 0.2 * banner})`,
        }}
      >
        <div style={{fontFamily: INTER, fontWeight: 900, fontSize: 62, color: C.ink, letterSpacing: -1}}>
          Rules decide. <span style={{color: C.jev, textShadow: glow(C.jev, 24)}}>Jev advises.</span>
        </div>
      </div>
    </CameraLayer>
      {[4, coreAt, tools, jevAt, rules].map((t, i) => (
        <Sfx key={i} at={t} name="whoosh" volume={0.2} />
      ))}
      {CHIPS.map(([phrase], i) => (
        <Sfx key={`c${i}`} at={at('arch', phrase) + (i === 2 ? 6 : 0)} name="tick" volume={0.35} />
      ))}
      {TOOLS.map((_, i) => (
        <Sfx key={`t${i}`} at={tools + i * 5} name="pop" volume={0.32} />
      ))}
      <Sfx at={rules} name="impact" volume={0.4} />
    </AbsoluteFill>
  );
};
