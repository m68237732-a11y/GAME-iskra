// Full-length arrangements of every track → audio/raw/<id>_<kind>.f32 (+ .json)
const { chromium, newPage, render, fs } = require('./render_lib.js');
const FILE = require('path').resolve(process.argv[2]);
const ONLY = process.argv[3] ? process.argv[3].split(',') : null;
const RUN_T = 165, BOSS_T = 72;
function runPlan(bpm, id) {
  const bar = 240 / bpm, N = Math.max(5, Math.round(RUN_T / (8 * bar))), plan = [];
  if (id === 'ambient') { const pat = [0, 0, 2, 2, 3, 2, 0, 0]; for (let k = 0; k < N; k++) plan.push([8, pat[Math.min(pat.length - 1, Math.floor((k / N) * pat.length))], false]); return plan; }
  for (let k = 0; k < N; k++) {
    const x = k / N; let I;
    if (k === 0) I = 0; else if (k === N - 1) I = 0;
    else if (x < 0.2) I = 1; else if (x < 0.42) I = 2; else if (x < 0.55) I = 3; else if (x < 0.64) I = 1; else if (x < 0.78) I = 2; else I = 3;
    plan.push([8, I, false]);
  }
  return plan;
}
function bossPlan(bpm) {
  const bar = 240 / bpm, N = Math.max(3, Math.round(BOSS_T / (8 * bar))), plan = [];
  for (let k = 0; k < N; k++) plan.push([8, k === 0 ? 2 : 3, true]);
  return plan;
}
(async () => {
  const b = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
  let { p, ctx } = await newPage(b, FILE);
  const ids = await p.evaluate(() => SFX._tracks());
  const info = {};
  for (const id of ids) info[id] = await p.evaluate((id) => { const t = SFX._tr(id); return { bpm: t.bpm, bossBpm: t.bossBpm || t.bpm }; }, id);
  await ctx.close();
  for (const id of ids) {
    if (ONLY && !ONLY.includes(id)) continue;
    for (const kind of ['run', 'boss']) {
      if (kind === 'boss' && id === 'ambient') continue;
      const out = `audio/raw/${id}_${kind}.f32`;
      if (fs.existsSync(out + '.json')) { console.log('skip', id, kind); continue; }
      const plan = kind === 'run' ? runPlan(info[id].bpm, id) : bossPlan(info[id].bossBpm);
      const pg = await newPage(b, FILE);
      try {
        const m = await render(pg.p, id, plan, out, { tail: 4, bpm: info[id].bpm, bossBpm: info[id].bossBpm });
        const lastStart = m.starts[m.starts.length - 1];
        fs.writeFileSync(out + '.json', JSON.stringify({ id, kind, plan, len: m.len, n: m.n, sr: m.sr, peak: m.peak, lastStart, ms: m.ms }));
        console.log(new Date().toISOString(), id, kind, 'blocks', plan.length, 'len', m.len.toFixed(1), 'peak', m.peak.toFixed(2), 'ms', m.ms);
      } catch (e) { console.log('FAIL', id, kind, e.message); }
      await pg.ctx.close();
    }
  }
  await b.close();
  console.log('ALL DONE');
})();
