"""
MTProto Auth Key Generation (Diffie-Hellman key exchange).
Implements the full handshake described at:
https://core.telegram.org/mtproto/auth_key
"""
import os
import time
from hashlib import sha1

from ..crypto import AES, AuthKey, Factorization, rsa
from ..errors import SecurityError
from ..extensions import BinaryReader
from ..helpers import generate_key_data_from_nonce
from ..tl.mtproto_types import (
    ReqPqMultiRequest, ReqDHParamsRequest, SetClientDHParamsRequest,
    PQInnerData, ServerDHParamsOk, ServerDHParamsFail,
    ServerDHInnerData, ClientDHInnerData, DhGenOk, DhGenRetry, DhGenFail,
)


def _get_int(b: bytes, signed: bool = True) -> int:
    return int.from_bytes(b, 'big', signed=signed)


async def do_authentication(sender):
    """
    Execute the 3-step MTProto DH auth key exchange.
    Returns (AuthKey, time_offset_seconds).
    """
    # ── Step 1: ReqPqMulti ──────────────────────────────────────────────
    nonce = int.from_bytes(os.urandom(16), 'big', signed=True)
    res_pq = await sender.send(ReqPqMultiRequest(nonce=nonce))

    if res_pq.nonce != nonce:
        raise SecurityError('Step 1: Invalid nonce from server')

    pq = _get_int(res_pq.pq)
    p, q = Factorization.factorize(pq)
    p_bytes = rsa.get_byte_array(p)
    q_bytes = rsa.get_byte_array(q)
    new_nonce = int.from_bytes(os.urandom(32), 'little', signed=True)

    pq_inner = bytes(PQInnerData(
        pq=rsa.get_byte_array(pq),
        p=p_bytes, q=q_bytes,
        nonce=res_pq.nonce,
        server_nonce=res_pq.server_nonce,
        new_nonce=new_nonce,
    ))

    # ── Step 2: ReqDHParams ─────────────────────────────────────────────
    cipher_text = None
    target_fp = None

    for fp in res_pq.server_public_key_fingerprints:
        ct = rsa.encrypt(fp, pq_inner)
        if ct is not None:
            cipher_text, target_fp = ct, fp
            break

    if cipher_text is None:
        for fp in res_pq.server_public_key_fingerprints:
            ct = rsa.encrypt(fp, pq_inner, use_old=True)
            if ct is not None:
                cipher_text, target_fp = ct, fp
                break

    if cipher_text is None:
        raise SecurityError(
            'Step 2: No matching RSA key for fingerprints: '
            + ', '.join(str(f) for f in res_pq.server_public_key_fingerprints)
        )

    server_dh_params = await sender.send(ReqDHParamsRequest(
        nonce=res_pq.nonce,
        server_nonce=res_pq.server_nonce,
        p=p_bytes, q=q_bytes,
        public_key_fingerprint=target_fp,
        encrypted_data=cipher_text,
    ))

    if server_dh_params.nonce != res_pq.nonce:
        raise SecurityError('Step 2: Invalid nonce from server')
    if server_dh_params.server_nonce != res_pq.server_nonce:
        raise SecurityError('Step 2: Invalid server nonce')

    if isinstance(server_dh_params, ServerDHParamsFail):
        raise SecurityError('Step 2: Server returned DH params fail')

    # ── Step 3: SetClientDHParams ────────────────────────────────────────
    key, iv = generate_key_data_from_nonce(res_pq.server_nonce, new_nonce)

    if len(server_dh_params.encrypted_answer) % 16 != 0:
        raise SecurityError('Step 3: AES block size mismatch')

    plain_answer = AES.decrypt_ige(server_dh_params.encrypted_answer, key, iv)

    with BinaryReader(plain_answer) as reader:
        reader.read(20)  # skip SHA1 hash
        server_dh_inner = reader.tgread_object()

    if not isinstance(server_dh_inner, ServerDHInnerData):
        raise SecurityError(f'Step 3: Expected ServerDHInnerData, got {type(server_dh_inner)}')
    if server_dh_inner.nonce != res_pq.nonce:
        raise SecurityError('Step 3: Invalid nonce in encrypted answer')
    if server_dh_inner.server_nonce != res_pq.server_nonce:
        raise SecurityError('Step 3: Invalid server nonce in encrypted answer')

    dh_prime = _get_int(server_dh_inner.dh_prime, signed=False)
    g = server_dh_inner.g
    g_a = _get_int(server_dh_inner.g_a, signed=False)
    time_offset = server_dh_inner.server_time - int(time.time())

    # Security checks (https://core.telegram.org/mtproto/auth_key)
    safety = 2 ** (2048 - 64)
    if not (1 < g < dh_prime - 1):
        raise SecurityError('g out of range')
    if not (1 < g_a < dh_prime - 1):
        raise SecurityError('g_a out of range')
    if not (safety <= g_a <= dh_prime - safety):
        raise SecurityError('g_a out of safe range')

    b = _get_int(os.urandom(256), signed=False)
    g_b = pow(g, b, dh_prime)
    gab = pow(g_a, b, dh_prime)

    if not (safety <= g_b <= dh_prime - safety):
        raise SecurityError('g_b out of safe range')

    client_dh_inner = bytes(ClientDHInnerData(
        nonce=res_pq.nonce,
        server_nonce=res_pq.server_nonce,
        retry_id=0,
        g_b=rsa.get_byte_array(g_b),
    ))
    client_dh_hashed = sha1(client_dh_inner).digest() + client_dh_inner
    client_dh_encrypted = AES.encrypt_ige(client_dh_hashed, key, iv)

    dh_gen = await sender.send(SetClientDHParamsRequest(
        nonce=res_pq.nonce,
        server_nonce=res_pq.server_nonce,
        encrypted_data=client_dh_encrypted,
    ))

    nonce_types = (DhGenOk, DhGenRetry, DhGenFail)

    if dh_gen.nonce != res_pq.nonce:
        raise SecurityError(f'Step 3: Invalid {type(dh_gen).__name__} nonce')
    if dh_gen.server_nonce != res_pq.server_nonce:
        raise SecurityError(f'Step 3: Invalid {type(dh_gen).__name__} server nonce')

    auth_key = AuthKey(rsa.get_byte_array(gab))
    nonce_number = 1 + nonce_types.index(type(dh_gen))
    expected_hash = auth_key.calc_new_nonce_hash(new_nonce, nonce_number)
    actual_hash = getattr(dh_gen, f'new_nonce_hash{nonce_number}')

    if actual_hash != expected_hash:
        raise SecurityError('Step 3: Invalid new nonce hash')

    if not isinstance(dh_gen, DhGenOk):
        raise AssertionError(f'Step 3 answer was {dh_gen}')

    return auth_key, time_offset
