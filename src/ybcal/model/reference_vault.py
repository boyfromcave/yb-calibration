# ==== ybcal vendored file -- do not edit; re-vendor with `ybcal verify --revendor` ====
# source: boyfromcave/ycash6 qa/rpc-tests/test_framework/vault.py
# commit: 4129f22cd7a9732b5a15dd2b1893dd09b7f4471c
# sha256: 96817339b151fd91927ae4994c59ab7ec9977e16d493463a01924c798b24d039
# body-sha256: acc9c1458ca2b633c8dc1b25a7bfec167018dafa3686b0db7824d473484d7d7b
# mode: verbatim top-level definitions (transitive closure of the roots in vendor.SPECS)
# ruff: noqa
# ==== end of ybcal header; the vendored source follows unchanged ====
"""Verbatim top-level definitions extracted from the ycash6 test framework (see header)."""

import hashlib
import hmac
import struct


# ---- from qa/rpc-tests/test_framework/vault.py

OP_PUSHDATA1 = 0x4c

OP_PUSHDATA2 = 0x4d

OP_PUSHDATA4 = 0x4e

OP_RETURN = 0x6a

ROLE_UNLOCK = 1

ROLE_CANCEL = 2

ACT_MAGIC = b'YV'

ACT_VERSION = 0x01

ACT_SET_CREATE = 0x01

ACT_SET_JOIN = 0x02

ACT_SET_HEARTBEAT = 0x03

ACT_SET_REMOVE = 0x04

ACT_SET_EQUIVOCATION = 0x05

ACT_SET_WINDDOWN = 0x06

ACT_NAMES = {
    ACT_SET_CREATE: 'SET_CREATE', ACT_SET_JOIN: 'SET_JOIN', ACT_SET_HEARTBEAT: 'SET_HEARTBEAT',
    ACT_SET_REMOVE: 'SET_REMOVE', ACT_SET_EQUIVOCATION: 'SET_EQUIVOCATION', ACT_SET_WINDDOWN: 'SET_WINDDOWN',
}

ACT_BODY_SIZE = {
    ACT_SET_CREATE: 64, ACT_SET_JOIN: 70, ACT_SET_HEARTBEAT: 65,
    ACT_SET_REMOVE: 66, ACT_SET_EQUIVOCATION: 264, ACT_SET_WINDDOWN: 32,
}

SET_FLAG_OPEN = 0x01

ACT_DOMAIN = b'YcashSetAct'              # 11 ASCII bytes

SETSIG_DOMAIN = b'YcashSetSig'           # 11 ASCII bytes

SET_SIG_SIZE = 65

MAX_SEATS = 15

MAX_WINDOW = 1048576

LOCKTIME_THRESHOLD = 500000000

COIN = 100000000

MAX_MONEY = 21000000 * COIN

class VaultError(Exception):
    """A rejection.  ``reason`` is a short code; the node's strings may differ in wording
    (``bad-vault-*``), the golden vector only records *that* a case is rejected and at which
    stage."""

    def __init__(self, reason, detail=''):
        Exception.__init__(self, reason if not detail else '%s: %s' % (reason, detail))
        self.reason = reason

def sha256(b):
    return hashlib.sha256(b).digest()

def sha256d(b):
    return sha256(sha256(b))

def txid_internal(txid_hex):
    """The 32 internal bytes of an RPC txid (``uint256::begin()``)."""
    b = bytes.fromhex(txid_hex)
    assert len(b) == 32
    return b[::-1]

def ser_prevout(txid_hex, n):
    """``COutPoint`` serialisation: 32 internal bytes || n u32 LE (36 bytes)."""
    return txid_internal(txid_hex) + struct.pack('<I', n)

_P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F

_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141

_G = (0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
      0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8)

def _add(p, q):
    if p is None:
        return q
    if q is None:
        return p
    if p[0] == q[0]:
        if (p[1] + q[1]) % _P == 0:
            return None
        lam = 3 * p[0] * p[0] * pow(2 * p[1], _P - 2, _P) % _P
    else:
        lam = (q[1] - p[1]) * pow(q[0] - p[0], _P - 2, _P) % _P
    x = (lam * lam - p[0] - q[0]) % _P
    return (x, (lam * (p[0] - x) - p[1]) % _P)

def _jdouble(p):
    x, y, z = p
    if y == 0:
        return (0, 1, 0)
    ysq = y * y % _P
    s_ = 4 * x * ysq % _P
    m = 3 * x * x % _P
    nx = (m * m - 2 * s_) % _P
    return (nx, (m * (s_ - nx) - 8 * ysq * ysq) % _P, 2 * y * z % _P)

def _jadd(p, q):
    if p[2] == 0:
        return q
    if q[2] == 0:
        return p
    z1s, z2s = p[2] * p[2] % _P, q[2] * q[2] % _P
    u1, u2 = p[0] * z2s % _P, q[0] * z1s % _P
    s1, s2 = p[1] * z2s * q[2] % _P, q[1] * z1s * p[2] % _P
    if u1 == u2:
        return _jdouble(p) if s1 == s2 else (0, 1, 0)
    h = (u2 - u1) % _P
    r = (s2 - s1) % _P
    h2 = h * h % _P
    h3 = h * h2 % _P
    u1h2 = u1 * h2 % _P
    nx = (r * r - h3 - 2 * u1h2) % _P
    return (nx, (r * (u1h2 - nx) - s1 * h3) % _P, h * p[2] * q[2] % _P)

def _mul(k, point):
    """Scalar multiplication (Jacobian internally; affine in and out, None = infinity)."""
    if point is None or k % _N == 0:
        return None
    r, a = (0, 1, 0), (point[0], point[1], 1)
    for bit in bin(k)[2:]:
        r = _jdouble(r)
        if bit == '1':
            r = _jadd(r, a)
    if r[2] == 0:
        return None
    zi = pow(r[2], _P - 2, _P)
    return (r[0] * zi * zi % _P, r[1] * zi * zi * zi % _P)

def _lift_x(x, odd):
    if x >= _P:
        return None
    y2 = (x * x * x + 7) % _P
    y = pow(y2, (_P + 1) // 4, _P)
    if y * y % _P != y2:
        return None
    if (y & 1) != odd:
        y = _P - y
    return (x, y)

def _ser_point(pt):
    return bytes([2 + (pt[1] & 1)]) + pt[0].to_bytes(32, 'big')

def is_compressed_pubkey(b):
    """33 bytes with prefix 02/03 (``CPubKey`` of a compressed size; no curve check).  This is
    15.3's "compressed" for template and act keys (ambiguity A-2 in VAULT_VECTORS.md: an
    off-curve key is accepted by the parsers; it simply never verifies or recovers)."""
    b = bytes(b)
    return len(b) == 33 and b[0] in (2, 3)

def _rfc6979_nonces(secret32, msg32):
    """The candidate stream of libsecp256k1's ``nonce_function_rfc6979`` with no extra data:
    key = secret || (msg mod n), HMAC-SHA256 DRBG; candidate ``counter`` is the counter-th
    output.  libsecp256k1 skips a candidate that overflows or is zero, as the caller does."""
    h1 = (int.from_bytes(msg32, 'big') % _N).to_bytes(32, 'big')
    seed = bytes(secret32) + h1
    v = b'\x01' * 32
    k = b'\x00' * 32
    k = hmac.new(k, v + b'\x00' + seed, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    k = hmac.new(k, v + b'\x01' + seed, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    while True:
        v = hmac.new(k, v, hashlib.sha256).digest()
        yield int.from_bytes(v, 'big')
        k = hmac.new(k, v + b'\x00', hashlib.sha256).digest()
        v = hmac.new(k, v, hashlib.sha256).digest()

def ecdsa_sign_rec(secret32, msg32):
    """``(r, s, recid, flipped)`` with low S, as ``secp256k1_ecdsa_sign_recoverable`` with the
    default nonce function: recid = (R.x >= n) << 1 | R.y odd, flipped (and recid ^= 1) when
    s was normalised.  ``flipped`` is for the vectors (exercises the high-S branch)."""
    d = int.from_bytes(secret32, 'big')
    assert 0 < d < _N
    z = int.from_bytes(msg32, 'big') % _N
    for k in _rfc6979_nonces(secret32, msg32):
        if k == 0 or k >= _N:
            continue
        rx, ry = _mul(k, _G)
        r = rx % _N
        if r == 0:
            continue
        s = pow(k, _N - 2, _N) * (z + r * d) % _N
        if s == 0:
            continue
        recid = (2 if rx >= _N else 0) | (ry & 1)
        flipped = s > _N // 2
        if flipped:
            s = _N - s
            recid ^= 1
        return r, s, recid, flipped

def sign_recoverable(secret32, msg32):
    """65 bytes ``header || r || s``, header = 27 + recid + 4 (compressed key): 31..34.  Equals
    ``CKey::SignCompact(uint256(msg32))`` byte for byte."""
    r, s, recid, _f = ecdsa_sign_rec(secret32, msg32)
    return bytes([31 + recid]) + r.to_bytes(32, 'big') + s.to_bytes(32, 'big')

def recover_compact(sig65, msg32, strict=True):
    """The 33-byte compressed key a 65-byte recoverable signature recovers to, or None.
    ``strict`` (the set-signature rule, 15.2 step 3): header 31..34 and S <= n/2.  Non-strict
    mirrors ``CPubKey::RecoverCompact``: any header, recid = (h - 27) & 3, and the key comes
    back uncompressed (65 bytes) unless (h - 27) & 4."""
    sig65 = bytes(sig65)
    if len(sig65) != SET_SIG_SIZE:
        return None
    header = sig65[0]
    if strict and not 31 <= header <= 34:
        return None
    recid = (header - 27) & 3
    r = int.from_bytes(sig65[1:33], 'big')
    s = int.from_bytes(sig65[33:65], 'big')
    if not (0 < r < _N and 0 < s < _N):
        return None
    if strict and s > _N // 2:
        return None
    x = r + (_N if recid & 2 else 0)
    if x >= _P:
        return None
    R = _lift_x(x, recid & 1)
    if R is None:
        return None
    z = int.from_bytes(msg32, 'big') % _N
    rinv = pow(r, _N - 2, _N)
    Q = _add(_mul(s * rinv % _N, R), _mul((-z * rinv) % _N, _G))
    if Q is None:
        return None
    if not strict and not (header - 27) & 4:
        return b'\x04' + Q[0].to_bytes(32, 'big') + Q[1].to_bytes(32, 'big')
    return _ser_point(Q)

def push(data):
    """``CScript << std::vector<unsigned char>``: the canonical (smallest) push."""
    data = bytes(data)
    n = len(data)
    if n < OP_PUSHDATA1:
        return bytes([n]) + data
    if n <= 0xFF:
        return bytes([OP_PUSHDATA1, n]) + data
    if n <= 0xFFFF:
        return bytes([OP_PUSHDATA2]) + struct.pack('<H', n) + data
    return bytes([OP_PUSHDATA4]) + struct.pack('<I', n) + data

def get_ops(script):
    """``CScript::GetOp`` over the whole script: ``[(opcode, data_or_None)]``, or None if a push
    runs past the end.  A push's opcode is the byte that introduced it (so a non-minimal push is
    visible: rebuilding with ``push`` gives different bytes)."""
    script = bytes(script)
    ops, i, n = [], 0, len(script)
    while i < n:
        op = script[i]
        i += 1
        if op <= OP_PUSHDATA4:
            if op < OP_PUSHDATA1:
                size = op
            elif op == OP_PUSHDATA1:
                if i + 1 > n:
                    return None
                size = script[i]
                i += 1
            elif op == OP_PUSHDATA2:
                if i + 2 > n:
                    return None
                size = struct.unpack('<H', script[i:i + 2])[0]
                i += 2
            else:
                if i + 4 > n:
                    return None
                size = struct.unpack('<I', script[i:i + 4])[0]
                i += 4
            if i + size > n:
                return None
            ops.append((op, script[i:i + size]))
            i += size
        else:
            ops.append((op, None))
    return ops

def set_sig_msg_raw(set_id32, role, prevout36, sighash32):
    return sha256d(SETSIG_DOMAIN + bytes(set_id32) + bytes([role]) + bytes(prevout36) + bytes(sighash32))

def act_msg(payload, prevout_txid_hex, prevout_n):
    """SHA256d("YcashSetAct" || P || vin[0].prevout (36))."""
    return sha256d(ACT_DOMAIN + bytes(payload) + ser_prevout(prevout_txid_hex, prevout_n))

def _u8(v):
    return struct.pack('<B', v)

def _key(k):
    k = bytes.fromhex(k) if isinstance(k, str) else bytes(k)
    assert len(k) == 33
    return k

def _h32(h):
    h = bytes.fromhex(h) if isinstance(h, str) else bytes(h)
    assert len(h) == 32
    return h

def encode_act_body(act):
    """The body bytes of an act dict (no validation: negative vectors encode bad values)."""
    t = act['type']
    if t == ACT_SET_CREATE:
        return (struct.pack('<BBBBB', act['seats'], act['unlockThreshold'], act['cancelThreshold'],
                            act['slashThreshold'], act['flags'])
                + struct.pack('<HIIqII', act['rateLimitBps'], act['rateWindow'], act['livenessWindow'],
                              act['bondMin'], act['bondLockMin'], act['maturity'])
                + _key(act['admitKey']))
    if t == ACT_SET_JOIN:
        return _h32(act['setId']) + _key(act['memberKey']) + struct.pack('<IB', act['bondLocktime'], act['bondVout'])
    if t == ACT_SET_HEARTBEAT:
        return _h32(act['setId']) + _key(act['memberKey'])
    if t == ACT_SET_REMOVE:
        return _h32(act['setId']) + _key(act['memberKey']) + _u8(act['burn'])
    if t == ACT_SET_EQUIVOCATION:
        po = bytes.fromhex(act['prevout']) if isinstance(act['prevout'], str) else bytes(act['prevout'])
        assert len(po) == 36
        sa = bytes.fromhex(act['sigA']) if isinstance(act['sigA'], str) else bytes(act['sigA'])
        sb = bytes.fromhex(act['sigB']) if isinstance(act['sigB'], str) else bytes(act['sigB'])
        assert len(sa) == 65 and len(sb) == 65
        return (_h32(act['setId']) + po + _u8(act['roleA']) + _h32(act['sighashA']) + sa
                + _u8(act['roleB']) + _h32(act['sighashB']) + sb)
    if t == ACT_SET_WINDDOWN:
        return _h32(act['setId'])
    raise VaultError('bad-vault-act-type')

def encode_act(act, check=True):
    """P = "YV" || 0x01 || type || body.  ``check`` runs ``decode_act`` on the result (so an
    encoder can never emit what the decoder rejects)."""
    p = ACT_MAGIC + bytes([act.get('version', ACT_VERSION), act['type']]) + encode_act_body(act)
    if check:
        decode_act(p)
    return p

def decode_act(p):
    """The act dict of payload ``P`` (hex fields), or raises ``VaultError``.  Checks everything
    that needs no chain state: magic, version, type, exact body length, and the field rules of
    15.5 that are context-free (CREATE's ranges and flags; compressed keys; ``bondLocktime <
    500000000``; ``burn`` in {0,1}; roles in {1,2}; signature headers 31..34)."""
    p = bytes(p)
    if len(p) < 4 or p[:2] != ACT_MAGIC:
        raise VaultError('bad-vault-act-magic')
    if p[2] != ACT_VERSION:
        raise VaultError('bad-vault-act-version')
    t = p[3]
    if t not in ACT_BODY_SIZE:
        raise VaultError('bad-vault-act-type')
    body = p[4:]
    if len(body) != ACT_BODY_SIZE[t]:
        raise VaultError('bad-vault-act-size', '%s body %d != %d' % (ACT_NAMES[t], len(body), ACT_BODY_SIZE[t]))
    a = {'type': t, 'name': ACT_NAMES[t]}

    def key_at(off, name):
        k = body[off:off + 33]
        if not is_compressed_pubkey(k):
            raise VaultError('bad-vault-act-key', name)
        return k.hex()

    if t == ACT_SET_CREATE:
        seats, ut, ct, st, flags = struct.unpack('<BBBBB', body[0:5])
        bps, rw, lw, bond_min, blm, mat = struct.unpack('<HIIqII', body[5:31])
        a.update(seats=seats, unlockThreshold=ut, cancelThreshold=ct, slashThreshold=st, flags=flags,
                 rateLimitBps=bps, rateWindow=rw, livenessWindow=lw, bondMin=bond_min, bondLockMin=blm,
                 maturity=mat, admitKey=key_at(31, 'admitKey'))
        if not 1 <= seats <= MAX_SEATS:
            raise VaultError('bad-vault-act-seats')
        for th in (ut, ct, st):
            if not 1 <= th <= seats:
                raise VaultError('bad-vault-act-threshold')
        if flags & ~SET_FLAG_OPEN:
            raise VaultError('bad-vault-act-flags')
        if bps > 10000:
            raise VaultError('bad-vault-act-rate')
        if not 1 <= rw <= MAX_WINDOW or not 1 <= lw <= MAX_WINDOW:
            raise VaultError('bad-vault-act-window')
        if not 1 <= bond_min <= MAX_MONEY:          # D-1 (reconciled 2026-10-05): MoneyRange
            raise VaultError('bad-vault-act-bondmin')
    elif t == ACT_SET_JOIN:
        lt, vout = struct.unpack('<IB', body[65:70])
        a.update(setId=body[0:32].hex(), memberKey=key_at(32, 'memberKey'), bondLocktime=lt, bondVout=vout)
        if lt >= LOCKTIME_THRESHOLD:
            raise VaultError('bad-vault-act-locktime')
    elif t == ACT_SET_HEARTBEAT:
        a.update(setId=body[0:32].hex(), memberKey=key_at(32, 'memberKey'))
    elif t == ACT_SET_REMOVE:
        a.update(setId=body[0:32].hex(), memberKey=key_at(32, 'memberKey'), burn=body[65])
        if body[65] not in (0, 1):
            raise VaultError('bad-vault-act-burn')
    elif t == ACT_SET_EQUIVOCATION:
        a.update(setId=body[0:32].hex(), prevout=body[32:68].hex(),
                 roleA=body[68], sighashA=body[69:101].hex(), sigA=body[101:166].hex(),
                 roleB=body[166], sighashB=body[167:199].hex(), sigB=body[199:264].hex())
        if a['roleA'] not in (ROLE_UNLOCK, ROLE_CANCEL) or a['roleB'] not in (ROLE_UNLOCK, ROLE_CANCEL):
            raise VaultError('bad-vault-act-role')
        if not 31 <= body[101] <= 34 or not 31 <= body[199] <= 34:
            raise VaultError('bad-vault-act-sig')
    elif t == ACT_SET_WINDDOWN:
        a.update(setId=body[0:32].hex())
    return a

def act_script(payload, sigs=()):
    """``OP_RETURN <P> [<S_1> ... <S_n>]``, canonical pushes."""
    s = bytes([OP_RETURN]) + push(payload) + b''.join(push(x) for x in sigs)
    return s

def is_act_script(spk):
    """True if ``spk`` is an OP_RETURN whose first push starts with "YV" (an act output, which
    must then parse or the transaction is invalid)."""
    spk = bytes(spk)
    if not spk or spk[0] != OP_RETURN:
        return False
    ops = get_ops(spk[1:])
    if not ops:
        return False
    d = ops[0][1]
    return d is not None and d[:2] == ACT_MAGIC

def parse_act_script(spk):
    """``(P, act, [sigs])`` for an act output, None if ``spk`` is not one, raises
    ``VaultError`` if it is one and is malformed: every element after OP_RETURN a canonical data
    push, each S_i exactly 65 bytes, P decodes."""
    spk = bytes(spk)
    if not is_act_script(spk):
        return None
    ops = get_ops(spk[1:])
    vals = []
    for op, data in ops:
        if data is None:
            raise VaultError('bad-vault-act-script', 'non-push after OP_RETURN')
        vals.append(data)
    if act_script(vals[0], vals[1:]) != spk:
        raise VaultError('bad-vault-act-script', 'non-canonical push')
    p, sigs = vals[0], vals[1:]
    for s in sigs:
        if len(s) != SET_SIG_SIZE:
            raise VaultError('bad-vault-act-sig', 'signature size')
    return p, decode_act(p), sigs

def act_set_join(set_id32, member_key, bond_locktime, bond_vout=0):
    return {'type': ACT_SET_JOIN, 'setId': bytes(set_id32).hex(), 'memberKey': bytes(member_key).hex(),
            'bondLocktime': bond_locktime, 'bondVout': bond_vout}

def act_set_heartbeat(set_id32, member_key):
    return {'type': ACT_SET_HEARTBEAT, 'setId': bytes(set_id32).hex(), 'memberKey': bytes(member_key).hex()}
