# -*- coding: utf-8 -*-
"""raw float32 renders -> levelled, limited, faded OGG Vorbis files of the HD soundtrack"""
import sys, os, json, re, subprocess, glob
RAW, OUT, Q = sys.argv[1], sys.argv[2], sys.argv[3]
ONLY = sys.argv[4].split(',') if len(sys.argv) > 4 else None
TARGET = -16.0  # LUFS, every composition
os.makedirs(OUT, exist_ok=True)
res = {}
for jf in sorted(glob.glob(RAW + '/*_*.f32.json')):
    m = json.load(open(jf)); name = m['id'] + '_' + m['kind']
    if ONLY and name not in ONLY and m['id'] not in ONLY: continue
    raw = jf[:-5]
    inp = ['-f', 'f32le', '-ar', '48000', '-ac', '2', '-i', raw]
    o = subprocess.run(['ffmpeg', '-hide_banner', '-nostats'] + inp + ['-af', 'ebur128', '-f', 'null', '-'], capture_output=True, text=True).stderr
    I = float(re.findall(r'I:\s+(-?[\d.]+) LUFS', o)[-1])
    gain = TARGET - I
    total = m['n'] / m['sr']
    if m['kind'] == 'run':   # the last block is the calm outro: it fades away
        st = m['lastStart'] + (m['len'] - m['lastStart']) * 0.3
        fade = f"afade=t=out:st={st:.2f}:d={max(1.0, total - st - 0.4):.2f}:curve=qsin"
    else:                    # a boss piece ends on its last hit and rings out
        fade = f"afade=t=out:st={total - 2.6:.2f}:d=2.5"
    af = f"volume={gain:.2f}dB,alimiter=limit=0.85:attack=4:release=60:level=disabled,{fade},afade=t=in:st=0:d=0.03"
    out = f"{OUT}/{name}.ogg"
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y'] + inp + ['-af', af, '-c:a', 'libvorbis', '-q:a', Q, '-metadata', 'title=' + name, '-metadata', 'artist=ISKRA', out], check=True)
    o2 = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-i', out, '-af', 'ebur128=peak=true', '-f', 'null', '-'], capture_output=True, text=True).stderr
    I2 = float(re.findall(r'I:\s+(-?[\d.]+) LUFS', o2)[-1]); pk = float(re.findall(r'Peak:\s+(-?[\d.]+) dBFS', o2)[-1])
    size = os.path.getsize(out)
    res[name] = {'in': I, 'gain': round(gain, 2), 'out': I2, 'peak': pk, 'dur': round(total, 1), 'kb': size // 1024, 'kbps': round(size * 8 / total / 1000)}
    print(name, res[name], flush=True)
json.dump(res, open(OUT + '/encode.json', 'w'), indent=1)
tot = sum(v['kb'] for v in res.values()); dur = sum(v['dur'] for v in res.values())
print('TOTAL', round(tot / 1024, 1), 'MB', round(dur / 60, 1), 'min', 'avg in LUFS', round(sum(v['in'] for v in res.values()) / len(res), 2))
