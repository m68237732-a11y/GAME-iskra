// Renders songs from the game's own synth in headless Chromium and saves raw float32 stereo files.
// A song is built block by block (8 bars each, with a tail that rings out), overlap-added, and only then sent
// through the music compressor + make-up gain — the same result as one long render, many times faster.
const { chromium } = require('playwright');
const fs = require('fs');
async function newPage(b, file) {
  const ctx = await b.newContext({ acceptDownloads: true });
  const p = await ctx.newPage();
  p.on('pageerror', (e) => console.log('PAGEERROR', e.message));
  await p.addInitScript(() => { window.__noIntro = true; try { localStorage.setItem('iskra.save.v1', JSON.stringify({ v: 2, nick: 'R', seen2: 1, seen21: 1, seen24: 1, seen25: 1, seen26: 1, patchSeen: '9', tut: 1, vol24: 1 })); } catch (e) {} });
  await p.goto('file://' + file);
  await p.waitForTimeout(500);
  await p.evaluate(() => { window.__simLock = true; SFX.init(); });
  return { p, ctx };
}
async function render(p, id, plan, out, o = {}) {
  const t0 = Date.now();
  const dl = p.waitForEvent('download', { timeout: 0 });
  const meta = await p.evaluate(async ({ id, plan, sr, tail, raw, bossBpm, bpm }) => {
    const TAIL = 6;
    let total = 0.05; const starts = [];
    for (const [bars, I, B] of plan) { starts.push(total); total += bars * 240 / (B ? bossBpm : bpm); }
    const N = Math.ceil((total + tail) * sr), L = new Float32Array(N), R = new Float32Array(N);
    for (let k = 0; k < plan.length; k++) {
      const r = await SFX._song(id, [plan[k]], sr, TAIL, true);
      const o0 = Math.round((starts[k] - 0.05) * sr), l = r.buf.getChannelData(0), rr = r.buf.getChannelData(1), n = Math.min(l.length, N - o0);
      for (let i = 0; i < n; i++) { L[o0 + i] += l[i]; R[o0 + i] += rr[i]; }
    }
    let outL = L, outR = R;
    if (!raw) {
      const off = new OfflineAudioContext(2, N, sr), buf = off.createBuffer(2, N, sr);
      buf.getChannelData(0).set(L); buf.getChannelData(1).set(R);
      const src = off.createBufferSource(); src.buffer = buf;
      const trim = off.createGain(); trim.gain.value = SFX._tr(id).vol || 1;
      const mc = off.createDynamicsCompressor(); mc.threshold.value = -24; mc.knee.value = 14; mc.ratio.value = 3.2; mc.attack.value = 0.006; mc.release.value = 0.25;
      const mk = off.createGain(); mk.gain.value = 2.0;
      src.connect(trim); trim.connect(mc); mc.connect(mk); mk.connect(off.destination); src.start(0);
      const res = await off.startRendering();
      outL = res.getChannelData(0); outR = res.getChannelData(1);
    }
    const f = new Float32Array(N * 2); let pk = 0;
    for (let i = 0; i < N; i++) { const a = outL[i], c = outR[i]; f[i * 2] = a; f[i * 2 + 1] = c; const x = Math.abs(a), y = Math.abs(c); if (x > pk) pk = x; if (y > pk) pk = y; }
    const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([f.buffer])); a.download = 'x.f32'; document.body.appendChild(a); a.click(); a.remove();
    return { n: N, sr, len: total, peak: pk, starts };
  }, { id, plan, sr: o.sr || 48000, tail: o.tail || 4, raw: !!o.raw, bpm: o.bpm, bossBpm: o.bossBpm || o.bpm });
  const d = await dl; await d.saveAs(out);
  meta.ms = Date.now() - t0;
  return meta;
}
module.exports = { chromium, newPage, render, fs };
