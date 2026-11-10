#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Builds and signs ISKRA.apk without the Android SDK.

The app is a tiny WebView wrapper whose compiled parts never change (tools/wrapper/: manifest, resources, classes.dex,
taken from the 2.9 app). This script
  1. patches the version into the binary manifest and into classes.dex (version strings + the bundled build number),
  2. packs them with the game (index.html) and the HD pack (hd/: soundtrack, backdrops) into a zip-aligned archive,
  3. signs it: JAR signature (v1) and APK Signature Scheme v2, both with the given PKCS#12 key.

usage: build_apk.py --wrapper tools/wrapper --index index.html --hd <dir> --version 4.0 --build 400 --code 40 \
                    --key iskra-release.p12 --key-pass <password> --out ISKRA.apk
       build_apk.py --verify ISKRA.apk
"""
import argparse, base64, hashlib, io, os, struct, sys, zlib
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import pkcs12, pkcs7

OLD_VERSION, OLD_BUILD = '2.9', 290          # what the pristine wrapper carries
STORED_EXT = ('.png', '.jpg', '.jpeg', '.webp', '.ogg', '.mp3', '.m4a', '.arsc', '.flac', '.wav')
u16 = lambda d, o: struct.unpack_from('<H', d, o)[0]
u32 = lambda d, o: struct.unpack_from('<I', d, o)[0]


# ---------------------------------------------------------------- binary manifest
def patch_manifest(d, version, code):
    d = bytearray(d)
    assert u16(d, 0) == 3, 'not a binary XML'
    p = 8
    assert u16(d, p) == 1, 'string pool expected'
    hs, sz, n, flags, so = u16(d, p + 2), u32(d, p + 4), u32(d, p + 8), u32(d, p + 16), u32(d, p + 20)
    assert not flags & 0x100, 'UTF-16 pool expected'
    strs, offs = [], []
    for i in range(n):
        o = p + so + u32(d, p + hs + 4 * i); l = u16(d, o)
        strs.append(bytes(d[o + 2:o + 2 + l * 2]).decode('utf-16le')); offs.append(o + 2)
    assert len(version) == len(OLD_VERSION), 'the version must be %d characters long (in-place patch)' % len(OLD_VERSION)
    q = p + sz; done = 0
    while q < len(d):
        t, csz = u16(d, q), u32(d, q + 4)
        if t == 0x0102 and strs[u32(d, q + 20)] == 'manifest':
            ac, ao = u16(d, q + 28), q + 16 + u16(d, q + 24)
            for i in range(ac):
                a = ao + 20 * i; name = strs[u32(d, a + 4)]
                if name == 'versionCode': struct.pack_into('<I', d, a + 16, code); done += 1
                elif name == 'versionName':
                    si = u32(d, a + 16); assert strs[si] == OLD_VERSION, strs[si]
                    d[offs[si]:offs[si] + len(version) * 2] = version.encode('utf-16le'); done += 1
            break
        q += csz
    assert done == 2, 'versionCode/versionName not found'
    return bytes(d)


# ---------------------------------------------------------------- classes.dex
def _uleb(d, o):
    r = s = 0
    while True:
        b = d[o]; o += 1; r |= (b & 0x7f) << s; s += 7
        if not b & 0x80: return r, o

_LEN = [1] * 256
for _op in (0x02, 0x05, 0x08, 0x13, 0x15, 0x16, 0x19, 0x1a, 0x1c, 0x1f, 0x20, 0x22, 0x23, 0x29): _LEN[_op] = 2
for _op in (0x03, 0x06, 0x09, 0x14, 0x17, 0x1b, 0x24, 0x25, 0x26, 0x2a, 0x2b, 0x2c, 0xfc, 0xfd): _LEN[_op] = 3
_LEN[0x18] = 5
for _a, _b, _n in ((0x2d, 0x3d, 2), (0x44, 0x6d, 2), (0x6e, 0x72, 3), (0x74, 0x78, 3), (0x90, 0xaf, 2), (0xd0, 0xe2, 2), (0xfa, 0xfb, 4), (0xfe, 0xff, 2)):
    for _i in range(_a, _b + 1): _LEN[_i] = _n

def patch_dex(d, version, build):
    d = bytearray(d)
    assert d[:4] == b'dex\n'
    assert len(version) == len(OLD_VERSION)
    # 1. strings (same length, and the sort order of the string table is kept: checked below)
    n, off = u32(d, 0x38), u32(d, 0x3c)
    def sdata(i):
        so = u32(d, off + 4 * i); _, p = _uleb(d, so); return p, d.index(b'\0', p)
    hits = 0
    for i in range(n):
        p, e = sdata(i); s = bytes(d[p:e])
        if s in (OLD_VERSION.encode(), (' IskraApp/' + OLD_VERSION).encode()):
            d[p:e] = s.replace(OLD_VERSION.encode(), version.encode()); hits += 1
    assert hits == 2, 'version strings: %d found' % hits
    prev = None
    for i in range(n):
        p, e = sdata(i); s = bytes(d[p:e]).decode('utf-8', 'replace')
        assert prev is None or prev < s, 'string order broken by the new version: %r >= %r' % (prev, s)
        prev = s
    # 2. the bundled build number: every `const/16 vX, OLD_BUILD` in the code + the static field's initial value
    cls_n, cls_o = u32(d, 0x60), u32(d, 0x64)
    consts = statics = 0
    for c in range(cls_n):
        o = cls_o + 32 * c; cdo, svo = u32(d, o + 24), u32(d, o + 28)
        if svo:
            cnt, p = _uleb(d, svo)
            if cnt and d[p] == 0x24 and struct.unpack_from('<h', d, p + 1)[0] == OLD_BUILD:
                struct.pack_into('<h', d, p + 1, build); statics += 1
        if not cdo: continue
        p = cdo
        sf, p = _uleb(d, p); inf, p = _uleb(d, p); dm, p = _uleb(d, p); vm, p = _uleb(d, p)
        for _ in range(sf + inf): _, p = _uleb(d, p); _, p = _uleb(d, p)
        for _ in range(dm + vm):
            _, p = _uleb(d, p); _, p = _uleb(d, p); co, p = _uleb(d, p)
            if not co: continue
            q = co + 16; end = q + u32(d, co + 12) * 2
            while q < end:
                w = u16(d, q); op, hi = w & 0xff, w >> 8
                if op == 0 and hi in (1, 2, 3):   # switch / array payloads
                    if hi == 1: ln = 4 + u16(d, q + 2) * 2
                    elif hi == 2: ln = 2 + u16(d, q + 2) * 4
                    else: ln = 4 + (u32(d, q + 4) * u16(d, q + 2) + 1) // 2
                    q += ln * 2; continue
                if op == 0x13 and struct.unpack_from('<h', d, q + 2)[0] == OLD_BUILD:
                    struct.pack_into('<h', d, q + 2, build); consts += 1
                q += _LEN[op] * 2
    assert consts >= 3 and statics == 1, 'build constants: %d in code, %d static' % (consts, statics)
    # 3. header: SHA-1 signature, then the Adler-32 checksum
    d[12:32] = hashlib.sha1(bytes(d[32:])).digest()
    struct.pack_into('<I', d, 8, zlib.adler32(bytes(d[12:])) & 0xffffffff)
    return bytes(d)


# ---------------------------------------------------------------- zip (aligned)
def zip_build(entries):
    """entries: [(name, data)] -> (bytes of the local part, central directory records, eocd builder)"""
    out = io.BytesIO(); cd = io.BytesIO(); n = 0
    for name, data in entries:
        nb = name.encode('utf-8')
        stored = name.lower().endswith(STORED_EXT)
        if stored: comp = data; method = 0
        else:
            co = zlib.compressobj(9, zlib.DEFLATED, -15); comp = co.compress(data) + co.flush(); method = 8
        crc = zlib.crc32(data) & 0xffffffff
        off = out.tell()
        extra = b''
        if stored:                                    # stored entries start on a 4-byte boundary (resources.arsc must)
            pad = (4 - (off + 30 + len(nb)) % 4) % 4
            extra = b'\0' * pad
        hdr = struct.pack('<IHHHHHIIIHH', 0x04034b50, 20, 0x0800, method, 0, 0x0021, crc, len(comp), len(data), len(nb), len(extra))
        out.write(hdr + nb + extra + comp)
        cd.write(struct.pack('<IHHHHHHIIIHHHHHII', 0x02014b50, 20, 20, 0x0800, method, 0, 0x0021, crc, len(comp), len(data), len(nb), 0, 0, 0, 0, 0, off) + nb)
        n += 1
    return out.getvalue(), cd.getvalue(), n

def eocd(n, cd_size, cd_off):
    return struct.pack('<IHHHHIIH', 0x06054b50, 0, 0, n, n, cd_size, cd_off, 0)


# ---------------------------------------------------------------- v1 (JAR) signature
def _wrap72(line):
    b = line.encode('utf-8'); out = [b[:70]]; b = b[70:]
    while b: out.append(b' ' + b[:69]); b = b[69:]
    return b'\r\n'.join(out) + b'\r\n'

def v1_files(entries, key, cert, alias='ISKRA'):
    sha = lambda b: base64.b64encode(hashlib.sha256(b).digest()).decode()
    main = b'Manifest-Version: 1.0\r\nCreated-By: ISKRA build_apk.py\r\n\r\n'
    secs = []
    for name, data in entries:
        secs.append(_wrap72('Name: ' + name) + ('SHA-256-Digest: %s\r\n\r\n' % sha(data)).encode())
    mf = main + b''.join(secs)
    sf = ('Signature-Version: 1.0\r\nCreated-By: ISKRA build_apk.py\r\nSHA-256-Digest-Manifest: %s\r\nX-Android-APK-Signed: 2\r\n\r\n' % sha(mf)).encode()
    for (name, _), sec in zip(entries, secs):
        sf += _wrap72('Name: ' + name) + ('SHA-256-Digest: %s\r\n\r\n' % sha(sec)).encode()
    p7 = pkcs7.PKCS7SignatureBuilder().set_data(sf).add_signer(cert, key, hashes.SHA256()).sign(
        serialization.Encoding.DER, [pkcs7.PKCS7Options.DetachedSignature, pkcs7.PKCS7Options.Binary, pkcs7.PKCS7Options.NoAttributes])
    return [('META-INF/MANIFEST.MF', mf), ('META-INF/%s.SF' % alias, sf), ('META-INF/%s.RSA' % alias, p7)]


# ---------------------------------------------------------------- v2 signature
CHUNK = 1 << 20
def _chunks(b):
    return [b[i:i + CHUNK] for i in range(0, len(b), CHUNK)]

def content_digest(parts):
    ds = []
    for part in parts:
        for c in _chunks(part): ds.append(hashlib.sha256(b'\xa5' + struct.pack('<I', len(c)) + c).digest())
    return hashlib.sha256(b'\x5a' + struct.pack('<I', len(ds)) + b''.join(ds)).digest()

lp = lambda b: struct.pack('<I', len(b)) + b   # length-prefixed

def v2_block(local, cd, n, key, cert_der):
    digest = content_digest([local, cd, eocd(n, len(cd), len(local))])   # the EOCD is digested as if the CD came right after the entries
    ALG = 0x0103                                                            # RSASSA-PKCS1-v1_5 with SHA2-256
    signed = lp(lp(struct.pack('<I', ALG) + lp(digest))) + lp(lp(cert_der)) + lp(b'')
    sig = key.sign(signed, padding.PKCS1v15(), hashes.SHA256())
    pub = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    signer = lp(signed) + lp(lp(struct.pack('<I', ALG) + lp(sig))) + lp(pub)
    value = lp(lp(signer))
    pair = struct.pack('<QI', 4 + len(value), 0x7109871a) + value
    size = len(pair) + 8 + 16
    return struct.pack('<Q', size) + pair + struct.pack('<Q', size) + b'APK Sig Block 42'


# ---------------------------------------------------------------- verification (an independent re-read of a finished APK)
def verify(path):
    d = open(path, 'rb').read()
    e = d.rfind(b'PK\x05\x06'); assert e >= 0, 'no EOCD'
    n, cd_size, cd_off = u16(d, e + 10), u32(d, e + 12), u32(d, e + 16)
    assert d[cd_off - 16:cd_off] == b'APK Sig Block 42', 'no APK Signing Block'
    size = struct.unpack_from('<Q', d, cd_off - 24)[0]
    b0 = cd_off - size - 8
    assert struct.unpack_from('<Q', d, b0)[0] == size, 'block sizes differ'
    p = b0 + 8; v2 = None
    while p < cd_off - 24:
        ln = struct.unpack_from('<Q', d, p)[0]; pid = u32(d, p + 8)
        if pid == 0x7109871a: v2 = d[p + 12:p + 8 + ln]
        p += 8 + ln
    assert v2 is not None, 'no v2 signature'
    rd = lambda b, o: (b[o + 4:o + 4 + u32(b, o)], o + 4 + u32(b, o))
    signers, _ = rd(v2, 0); signer, _ = rd(signers, 0)
    signed, o = rd(signer, 0); sigs, o = rd(signer, o); pub, o = rd(signer, o)
    digests, so = rd(signed, 0); certs, so = rd(signed, so)
    dg, _ = rd(digests, 0); alg = u32(dg, 0); want, _ = rd(dg, 4)
    cert_der, _ = rd(certs, 0); cert = x509.load_der_x509_certificate(cert_der)
    sg, _ = rd(sigs, 0); sig, _ = rd(sg, 4)
    assert alg == u32(sg, 0), 'algorithm mismatch'
    assert alg in (0x0103, 0x0104), 'unsupported signature algorithm %#x' % alg
    hsh = hashes.SHA256() if alg == 0x0103 else hashes.SHA512()
    assert cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo) == pub, 'public key != certificate'
    cert.public_key().verify(sig, signed, padding.PKCS1v15(), hsh)          # raises if the signature is wrong
    ee = bytearray(d[e:]); struct.pack_into('<I', ee, 16, b0)
    if alg == 0x0103:
        got = content_digest([d[:b0], d[cd_off:cd_off + cd_size], bytes(ee)])
        assert got == want, 'content digest mismatch'
    # zip sanity: every stored entry is 4-aligned, resources.arsc is stored
    q = cd_off; bad = []
    for _ in range(n):
        assert u32(d, q) == 0x02014b50
        method, nl, xl, cl, lo = u16(d, q + 10), u16(d, q + 28), u16(d, q + 30), u16(d, q + 32), u32(d, q + 42)
        name = d[q + 46:q + 46 + nl].decode()
        data_off = lo + 30 + u16(d, lo + 26) + u16(d, lo + 28)
        if method == 0 and data_off % 4: bad.append(name)
        if name == 'resources.arsc': assert method == 0, 'resources.arsc is compressed'
        q += 46 + nl + xl + cl
    fp = hashlib.sha256(cert_der).hexdigest()
    return {'entries': n, 'cert': cert.subject.rfc4514_string(), 'sha256': fp, 'alg': hex(alg), 'unaligned': bad, 'size': len(d)}


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--verify'); ap.add_argument('--wrapper'); ap.add_argument('--index'); ap.add_argument('--hd')
    ap.add_argument('--version'); ap.add_argument('--build', type=int); ap.add_argument('--code', type=int)
    ap.add_argument('--key'); ap.add_argument('--key-pass', default=''); ap.add_argument('--out')
    a = ap.parse_args()
    if a.verify:
        print(verify(a.verify)); return
    W = a.wrapper
    rd = lambda p: open(os.path.join(W, p), 'rb').read()
    entries = [('AndroidManifest.xml', patch_manifest(rd('AndroidManifest.xml'), a.version, a.code))]
    for root, _, files in sorted(os.walk(os.path.join(W, 'res'))):
        for f in sorted(files):
            full = os.path.join(root, f); entries.append((os.path.relpath(full, W).replace(os.sep, '/'), open(full, 'rb').read()))
    entries.append(('resources.arsc', rd('resources.arsc')))
    entries.append(('classes.dex', patch_dex(rd('classes.dex'), a.version, a.build)))
    entries.append(('assets/index.html', open(a.index, 'rb').read()))
    if a.hd:
        for root, _, files in sorted(os.walk(a.hd)):
            for f in sorted(files):
                if f.endswith(('.json', '.log')): continue          # build notes, not part of the pack
                full = os.path.join(root, f)
                entries.append(('assets/hd/' + os.path.relpath(full, a.hd).replace(os.sep, '/'), open(full, 'rb').read()))
    key, cert, _ = pkcs12.load_key_and_certificates(open(a.key, 'rb').read(), a.key_pass.encode() or None)
    entries += v1_files(entries, key, cert)
    local, cd, n = zip_build(entries)
    block = v2_block(local, cd, n, key, cert.public_bytes(serialization.Encoding.DER))
    with open(a.out, 'wb') as f:
        f.write(local); f.write(block); f.write(cd); f.write(eocd(n, len(cd), len(local) + len(block)))
    print('built', a.out, round(os.path.getsize(a.out) / 1048576, 2), 'MB,', n, 'entries')
    print(verify(a.out))

if __name__ == '__main__':
    main()
