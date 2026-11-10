#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Writes hd/pack.js — the list of what the HD pack holds (the game loads it with a <script> tag).
usage: make_pack.py <hd dir>   (expects <hd>/music/<track>_run.ogg, <track>_boss.ogg, encode.json and <hd>/bg/<theme>.jpg)"""
import sys, os, json, glob
hd = sys.argv[1]
music = {}
for f in sorted(glob.glob(hd + '/music/*.ogg')):
    tid, kind = os.path.basename(f)[:-4].rsplit('_', 1)
    music.setdefault(tid, {})[kind] = 'music/' + os.path.basename(f)
bg = {os.path.basename(f)[:-4]: 'bg/' + os.path.basename(f) for f in sorted(glob.glob(hd + '/bg/*.jpg'))}
enc = json.load(open(hd + '/music/encode.json'))
# the pack is levelled to one loudness; `gain` lines it up with the synthesizer at the same slider position
avg_in = sum(v['in'] for v in enc.values()) / len(enc); avg_out = sum(v['out'] for v in enc.values()) / len(enc)
gain = round(10 ** ((avg_in - avg_out) / 20), 3)
total = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(hd) for f in fs if not f.endswith('.json') and f != 'pack.js')
minutes = round(sum(v['dur'] for v in enc.values()) / 60)
man = {'v': 1, 'mb': round(total / 1048576), 'min': minutes, 'gain': gain, 'music': music, 'bg': bg}
open(hd + '/pack.js', 'w', encoding='utf-8').write('window.ISKRA_HD = ' + json.dumps(man, ensure_ascii=False, separators=(',', ':')) + ';\n')
print('pack.js:', len(music), 'tracks,', len(bg), 'backdrops,', man['mb'], 'MB,', minutes, 'min, gain', gain)
