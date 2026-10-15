import React from 'react';
import {AbsoluteFill, Sequence, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {Callout} from '../components/Callout';
import {Flash, Sfx, Shake} from '../components/Fx';
import {SectionTag} from '../components/SectionTag';
import {Shot} from '../components/Shot';
import {C, INTER, MONO, glow} from '../theme';
import {SceneId, after, at} from '../timeline';

/** "TOOL 01 / GUARD" opener: holds until the name has been said, then shrinks to a corner badge. */
const Opener: React.FC<{id: SceneId; num: string; name: string; question: string; color: string; word: string}> = ({id, num, name, question, color, word}) => {
  const hold = after(id, word) + 8;
  return (
    <>
      <SectionTag num={num} name={name} question={question} color={color} hold={hold} />
      <Flash at={0} color={color} peak={0.45} len={12} />
      <Sfx at={0} name="boom" volume={0.6} />
      <Sfx at={hold - 6} name="whoosh" volume={0.32} />
    </>
  );
};

const Badge: React.FC<{num: string; name: string; color: string}> = ({num, name, color}) => (
  <SectionTag num={num} name={name} question="" color={color} badgeOnly />
);

export const Guard1: React.FC = () => {
  const t = (p: string) => at('guard1', p);
  return (
    <AbsoluteFill>
      <Shot keys={[{at: 0, shot: '02-hook-1.png'}, {at: t('a list of dangerous'), shot: '02-hook-1.png', focus: 'hook_blocked', color: C.warn, zoom: 1.8}]} />
      <Callout from={t('dangerous words') + 6} big="6 strings" label="the whole safety net: a list of dangerous words" color={C.warn} pos="br" size={96} />
      <Sfx at={t('a list of dangerous')} name="whoosh" volume={0.28} />
      <Sfx at={t('dangerous words') + 6} name="pop" volume={0.45} />
      <Opener id="guard1" num="01" name="GUARD" question="Do your hooks block disguised dangerous commands?" color={C.jev} word="Guard." />
    </AbsoluteFill>
  );
};

export const Guard2: React.FC = () => {
  const t = (p: string) => at('guard2', p);
  const hit331 = t('Three hundred') + 4;
  return (
    <AbsoluteFill>
      <Shake hits={[hit331]} strength={18}>
        <Shot
          keys={[
            {at: 0, shot: '03-guard-1.png'},
            {at: t('Three hundred'), shot: '03-guard-1.png', focus: 'guard_331', color: C.warn, zoom: 2.4},
            {at: t('Reading secrets'), shot: '03-guard-1.png', focus: 'guard_head_env', color: C.warn},
            {at: t('Base sixty four'), shot: '03-guard-1.png', focus: 'guard_base64', color: C.warn},
            {at: t('Delete commands hidden'), shot: '03-guard-2.png', focus: 'guard_var_rm', color: C.warn},
            {at: t('And not one normal'), shot: '03-guard-1.png', focus: 'guard_benign', color: C.jev},
          ]}
        />
      </Shake>
      <Callout from={t('fires five hundred')} to={t('Three hundred') - 4} big="571" count={571} label="disguised commands fired · nothing executed" color={C.cyan} pos="center" size={200} />
      <Callout from={hit331} to={t('Reading secrets') + 18} big="331 / 503" count={331} label="dangerous commands slipped straight through" color={C.warn} pos="br" glitch />
      <Callout from={t('And not one normal') + 10} big="0 / 68" label="normal commands blocked" color={C.jev} pos="tr" />
      <Sfx at={t('fires five hundred')} name="swell" volume={0.4} />
      <Sfx at={hit331 - 2} name="impact" volume={0.6} />
      <Sfx at={hit331} name="glitch" volume={0.35} />
      {['Reading secrets', 'Base sixty four', 'Delete commands hidden', 'And not one normal'].map((p) => (
        <Sfx key={p} at={t(p)} name="whoosh" volume={0.22} />
      ))}
      <Sfx at={t('And not one normal') + 10} name="pop" volume={0.45} />
      <Badge num="01" name="GUARD" color={C.jev} />
    </AbsoluteFill>
  );
};

export const Guard3: React.FC = () => {
  const t = (p: string) => at('guard3', p);
  return (
    <AbsoluteFill>
      <Shot
        keys={[
          {at: 0, shot: '04-guard-jev-1.png'},
          {at: t('It called all'), shot: '04-guard-jev-1.png', focus: 'jev_catch', color: C.jev},
          {at: t('and flagged zero'), shot: '04-guard-jev-1.png', focus: 'jev_false', color: C.cyan},
        ]}
      />
      <Callout from={t('It called all') + 10} to={t('and flagged zero') + 2} big="45 / 45" count={45} label="bypasses Jev called dangerous" color={C.jev} pos="br" />
      <Callout from={t('and flagged zero') + 12} big="0 / 20" label="normal commands Jev flagged" color={C.cyan} pos="br" />
      <Sfx at={t('It called all')} name="whoosh" volume={0.25} />
      <Sfx at={t('It called all') + 10} name="pop" volume={0.45} />
      <Sfx at={t('and flagged zero')} name="whoosh" volume={0.22} />
      <Sfx at={t('and flagged zero') + 12} name="pop" volume={0.45} />
      <Badge num="01" name="GUARD" color={C.jev} />
    </AbsoluteFill>
  );
};

export const Cal1: React.FC = () => {
  const t = (p: string) => at('cal1', p);
  return (
    <AbsoluteFill>
      <Shot keys={[{at: 0, shot: '05-gate-1.png'}, {at: t('We made Jev'), shot: '05-gate-1.png', focus: 'gate_wrote', color: C.cyan, zoom: 2.2}]} />
      <Callout from={t('forty sample') - 2} big="40 PRs" count={40} label="sample PRs · Jev as the gate, logging how sure it was" color={C.cyan} pos="br" />
      <Sfx at={t('We made Jev')} name="whoosh" volume={0.28} />
      <Sfx at={t('forty sample') - 2} name="pop" volume={0.45} />
      <Opener id="cal1" num="02" name="CALIBRATE" question="When your gate says 99% sure, is it?" color={C.cyan} word="Calibrate." />
    </AbsoluteFill>
  );
};

export const Cal2: React.FC = () => {
  const t = (p: string) => at('cal2', p);
  return (
    <AbsoluteFill>
      <Shot
        keys={[
          {at: 0, shot: '06-gate-report-1.png'},
          {at: t('When it said'), shot: '06-gate-report-1.png', focus: 'gate_reliability', color: C.jev},
          {at: t('Forty examples'), shot: '06-gate-report-1.png', focus: 'gate_more', color: C.amber},
        ]}
      />
      <Callout from={t('right every time') - 2} to={t('Forty examples') + 2} big="99% → 100%" label="said 99% sure · was right every time" color={C.jev} pos="br" size={104} />
      <Callout from={t('fifty two more') - 2} big="~52 more" label="labels before a threshold can be trusted" color={C.amber} pos="tr" size={104} />
      <Sfx at={t('When it said')} name="whoosh" volume={0.25} />
      <Sfx at={t('right every time') - 2} name="pop" volume={0.45} />
      <Sfx at={t('Forty examples')} name="whoosh" volume={0.22} />
      <Sfx at={t('fifty two more') - 2} name="pop" volume={0.45} />
      <Badge num="02" name="CALIBRATE" color={C.cyan} />
    </AbsoluteFill>
  );
};

const SimBadge: React.FC<{from: number}> = ({from}) => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const s = spring({frame: f - from, fps, config: {damping: 12}});
  if (f < from) return null;
  return (
    <div
      style={{
        position: 'absolute', top: 44, right: 56, zIndex: 25, fontFamily: MONO, fontWeight: 800, fontSize: 28, color: C.amber,
        border: `2px solid ${C.amber}`, borderRadius: 12, padding: '8px 18px', background: 'rgba(10,14,24,0.9)', opacity: s,
        transform: `scale(${0.8 + 0.2 * s})`, boxShadow: glow(C.amber, 14), letterSpacing: 3,
      }}
    >
      SIMULATED RUN
    </div>
  );
};

/** Explainer: the context window fills, compacts, and a path-scoped rule disappears. */
const ContextWindow: React.FC<{fill: number; squeeze: number; vanish: number; out: number}> = ({fill, squeeze, vanish, out}) => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const enter = spring({frame: f - (fill - 14), fps, config: {damping: 14}});
  const level = interpolate(f, [fill, squeeze], [0.08, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const sq = spring({frame: f - squeeze, fps, config: {damping: 11, stiffness: 160}});
  const shown = level * (1 - 0.68 * sq);
  const gone = interpolate(f, [vanish, vanish + 26], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const leave = interpolate(f, [out, out + 12], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  if (f < fill - 14 || leave >= 1) return null;
  const blocks = 24;
  const rules: [string, string, boolean][] = [
    ['CLAUDE.md', 'root rules', false],
    ['.claude/rules/style.md', 'always loaded', false],
    ['.claude/rules/api.md', 'path-scoped: src/api/**', true],
  ];
  return (
    <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center', opacity: enter * (1 - leave), transform: `scale(${1.2 * (0.9 + 0.1 * enter) * (1 + 0.4 * leave)})`, filter: leave ? `blur(${leave * 14}px)` : undefined}}>
      <div style={{fontFamily: MONO, fontWeight: 800, fontSize: 30, color: C.violet, letterSpacing: 6, marginBottom: 22}}>CONTEXT WINDOW</div>
      <div style={{position: 'relative', width: 1300, height: 96, borderRadius: 20, border: `2px solid ${C.violet}88`, background: 'rgba(10,14,24,0.8)', overflow: 'hidden', boxShadow: glow(C.violet, 20)}}>
        <div style={{position: 'absolute', left: 8, top: 8, bottom: 8, width: `calc(${shown * 100}% - 16px)`, display: 'flex', gap: 6}}>
          {Array.from({length: blocks}).map((_, k) => (
            <div key={k} style={{flex: 1, borderRadius: 8, background: [C.violet, C.cyan, C.jev, C.amber][k % 4], opacity: 0.75}} />
          ))}
        </div>
        {sq > 0.05 && (
          <div style={{position: 'absolute', right: 30, top: 0, bottom: 0, display: 'flex', alignItems: 'center', fontFamily: MONO, fontWeight: 800, fontSize: 34, color: C.amber, opacity: sq}}>
            ⇢ compacted
          </div>
        )}
      </div>
      <div style={{display: 'flex', gap: 30, marginTop: 70}}>
        {rules.map(([file, note, scoped], k) => {
          const s = spring({frame: f - (fill + 8 + k * 6), fps, config: {damping: 13}});
          const g = scoped ? gone : 0;
          return (
            <div
              key={file}
              style={{
                width: 400, padding: '26px 30px', borderRadius: 24, background: C.card, border: `2px solid ${scoped ? C.warn : C.jev}${scoped && g > 0 ? 'cc' : '77'}`,
                opacity: s * (1 - 0.85 * g), transform: `translateY(${(1 - s) * 40 + g * 40}px) scale(${1 - 0.1 * g})`, filter: g ? `blur(${g * 8}px)` : undefined,
                boxShadow: glow(scoped && g > 0 ? C.warn : C.jev, 14),
              }}
            >
              <div style={{fontFamily: MONO, fontWeight: 800, fontSize: 28, color: C.ink}}>{file}</div>
              <div style={{fontFamily: INTER, fontWeight: 600, fontSize: 26, color: C.dim, marginTop: 8}}>{note}</div>
              <div style={{fontFamily: INTER, fontWeight: 900, fontSize: 34, marginTop: 12, color: scoped && g > 0.2 ? C.warn : C.jev}}>
                {scoped && g > 0.2 ? '✗ forgotten' : '✓ in context'}
              </div>
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

export const Comp: React.FC = () => {
  const t = (p: string) => at('comp', p);
  const shotFrom = t('In this simulated');
  const rel = (p: string) => t(p) - (shotFrom - 6);
  return (
    <AbsoluteFill>
      <ContextWindow fill={t("When an agent's")} squeeze={t('it compacts')} vanish={t('quietly vanish')} out={shotFrom - 6} />
      <Sequence from={shotFrom - 6}>
        <Shot
          keys={[
            {at: 0, shot: '07-compaction-1.png'},
            {at: rel('In this simulated') + 18, shot: '07-compaction-1.png', focus: 'comp_sim', color: C.amber, zoom: 1.7},
            {at: rel('the path scoped'), shot: '07-compaction-1.png', focus: 'comp_survival', color: C.violet},
          ]}
        />
      </Sequence>
      <SimBadge from={shotFrom} />
      <Callout from={t('is gone after') - 2} to={t('while the root') + 2} big="forgotten" label="path-scoped API rule, after 1 compaction" color={C.warn} pos="br" size={96} glitch />
      <Callout from={t('while the root') + 8} big="100%" label="root rules survived every round" color={C.jev} pos="br" />
      <Sfx at={t('it compacts')} name="impact" volume={0.35} />
      <Sfx at={t('quietly vanish')} name="glitch" volume={0.3} />
      <Sfx at={shotFrom - 6} name="whoosh" volume={0.3} />
      <Sfx at={t('is gone after') - 2} name="impact" volume={0.4} />
      <Sfx at={t('while the root') + 8} name="pop" volume={0.45} />
      <Opener id="comp" num="03" name="COMPACTION" question="What does your agent forget when its memory fills up?" color={C.violet} word="Compaction." />
    </AbsoluteFill>
  );
};

export const Rev1: React.FC = () => {
  const t = (p: string) => at('rev1', p);
  return (
    <AbsoluteFill>
      <Shot
        keys={[
          {at: 0, shot: '09-mine-1.png'},
          {at: t('finds real bug fixes'), shot: '09-mine-1.png', focus: 'mine_bugfix', color: C.jev},
          {at: t('Then it puts one bug back'), shot: '10-build-plan-1.png', focus: 'build_plan', color: C.amber},
        ]}
      />
      <Callout from={t('and Jev confirms') + 6} to={t('Then it puts') - 2} big="3 / 3" count={3} label="real bug fixes, confirmed by Jev" color={C.jev} pos="br" />
      <Callout from={t('in a fresh pull request')} big="1 bug → 1 PR" label="put back into a fresh PR on a sandbox repo" color={C.amber} pos="br" size={96} />
      <Sfx at={t('finds real bug fixes')} name="whoosh" volume={0.28} />
      <Sfx at={t('and Jev confirms') + 6} name="pop" volume={0.45} />
      <Sfx at={t('Then it puts one bug back')} name="whoosh" volume={0.28} />
      <Sfx at={t('in a fresh pull request')} name="pop" volume={0.45} />
      <Opener id="rev1" num="04" name="REVIEWERS" question="Does your AI code reviewer catch real bugs?" color={C.amber} word="Reviewers." />
    </AbsoluteFill>
  );
};

const AndOr: React.FC<{from: number; to: number; line: number}> = ({from, to, line}) => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  if (f < from || f > to + 10) return null;
  const s = spring({frame: f - from, fps, config: {damping: 10, stiffness: 180}});
  const flip = spring({frame: f - from - 16, fps, config: {damping: 12}});
  const out = interpolate(f, [to, to + 10], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const g = Math.max(0, 1 - (f - from - 16) / 10) * (f >= from + 16 ? 1 : 0);
  const sub = spring({frame: f - line, fps, config: {damping: 13, stiffness: 160}});
  return (
    <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center', zIndex: 22, opacity: 1 - out, background: `rgba(3,5,10,${0.55 * s * (1 - out)})`}}>
      <div
        style={{
          display: 'flex', alignItems: 'center', gap: 50, padding: '50px 90px', borderRadius: 40, background: 'rgba(8,10,18,0.92)',
          border: `2px solid ${C.warn}`, boxShadow: glow(C.warn, 44), transform: `scale(${(0.6 + 0.4 * s) * (1 + 0.3 * out)}) translateX(${g * 14}px)`,
        }}
      >
        <span style={{fontFamily: MONO, fontWeight: 800, fontSize: 160, color: C.jev, textDecoration: flip > 0.5 ? 'line-through' : 'none', opacity: 1 - 0.55 * flip}}>and</span>
        <span style={{fontFamily: INTER, fontWeight: 900, fontSize: 120, color: C.ink, opacity: flip}}>→</span>
        <span style={{fontFamily: MONO, fontWeight: 800, fontSize: 160, color: C.warn, transform: `scale(${flip})`, display: 'inline-block', textShadow: glow(C.warn, 30)}}>or</span>
      </div>
      <div style={{fontFamily: INTER, fontWeight: 900, fontSize: 56, color: C.ink, marginTop: 40, opacity: sub, transform: `translateY(${(1 - sub) * 24}px)`, letterSpacing: -1}}>
        now <span style={{color: C.warn}}>any admin</span> can delete <span style={{color: C.warn}}>anything</span>
      </div>
    </AbsoluteFill>
  );
};

export const Rev2: React.FC = () => {
  const t = (p: string) => at('rev2', p);
  const flipAt = t('One word flipped');
  const rabbit = t('update the test') + 20;
  return (
    <AbsoluteFill>
      <Shake hits={[flipAt + 16]} strength={16}>
        <Shot
          keys={[
            {at: 0, shot: '12-pr-diff.png'},
            {at: t('CodeRabbit'), shot: '12-pr-diff.png', focus: 'pr_heads', color: C.amber},
            {at: t('both commented'), shot: '12-pr-diff.png', focus: 'pr_diff_line', color: C.warn},
            {at: t('And both of them said'), shot: '11b-pr-cubic-review.png', focus: 'cubic_comment', color: C.amber},
            {at: rabbit, shot: '11c-pr-coderabbit-review.png', focus: 'rabbit_comment', color: C.amber},
          ]}
        />
      </Shake>
      <AndOr from={flipAt} to={t('And both of them said') - 6} line={t('Now any admin')} />
      <Callout from={t('update the test') + 34} big="“update the test”" label="both reviewers: make the test match the bug" color={C.amber} pos="tr" size={76} />
      <Sfx at={t('CodeRabbit')} name="whoosh" volume={0.25} />
      <Sfx at={t('both commented')} name="whoosh" volume={0.28} />
      <Sfx at={flipAt} name="swell" volume={0.35} />
      <Sfx at={flipAt + 14} name="impact" volume={0.55} />
      <Sfx at={flipAt + 16} name="glitch" volume={0.35} />
      <Sfx at={t('And both of them said')} name="whoosh" volume={0.28} />
      <Sfx at={rabbit} name="whoosh" volume={0.22} />
      <Sfx at={t('update the test') + 34} name="pop" volume={0.45} />
      <Badge num="04" name="REVIEWERS" color={C.amber} />
    </AbsoluteFill>
  );
};

export const Rev3: React.FC = () => {
  const t = (p: string) => at('rev3', p);
  const zero = t('Confirmed recall') + 6;
  return (
    <AbsoluteFill>
      <Shake hits={[zero]} strength={14}>
        <Shot
          keys={[
            {at: 0, shot: '14-report-1.png'},
            {at: t('By location'), shot: '14-report-1.png', focus: 'report_recall', color: C.jev},
            {at: t("But Jev wasn't"), shot: '14-report-1.png', focus: 'report_confirmed', color: C.warn},
          ]}
        />
      </Shake>
      <Callout from={t('Recall: one') - 2} big="1 / 1" label="recall: commented on the bug line" color={C.jev} pos="bl" />
      <Callout from={zero} big="0 / 1" label="confirmed: explained the actual bug" color={C.warn} pos="br" glitch />
      <Sfx at={t('By location')} name="whoosh" volume={0.25} />
      <Sfx at={t("But Jev wasn't")} name="whoosh" volume={0.25} />
      <Sfx at={t('Recall: one') - 2} name="pop" volume={0.45} />
      <Sfx at={zero - 2} name="impact" volume={0.6} />
      <Sfx at={zero} name="glitch" volume={0.3} />
      <Badge num="04" name="REVIEWERS" color={C.amber} />
    </AbsoluteFill>
  );
};
