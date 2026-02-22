"""
MTProto protocol-level handshake types.

These types are NOT part of the auto-generated TL schema.
They are hard-coded protocol messages used during auth key generation:
https://core.telegram.org/mtproto/auth_key

All int128 values (nonce, server_nonce) use little-endian signed encoding.
int256 (new_nonce) uses little-endian signed encoding.
"""
import struct

from .tlobject import TLObject, TLRequest


# ── Helper serialization ────────────────────────────────────────────────────

def _write_int128(value: int) -> bytes:
    return value.to_bytes(16, 'little', signed=True)

def _write_int256(value: int) -> bytes:
    return value.to_bytes(32, 'little', signed=True)

def _read_int128(reader) -> int:
    return int.from_bytes(reader.read(16), 'little', signed=True)

def _read_int256(reader) -> int:
    return int.from_bytes(reader.read(32), 'little', signed=True)


# ══════════════════════════════════════════════════════════════════════════════
# REQUESTS (client → server)
# ══════════════════════════════════════════════════════════════════════════════

class ReqPqMultiRequest(TLRequest):
    """
    req_pq_multi#be7e8ef1 nonce:int128 = ResPQ;
    First step: ask the server for PQ.
    """
    CONSTRUCTOR_ID = 0xbe7e8ef1
    SUBCLASS_OF_ID = 0x051625A5  # ResPQ

    def __init__(self, nonce: int):
        self.nonce = nonce

    def _bytes(self) -> bytes:
        return struct.pack('<I', self.CONSTRUCTOR_ID) + _write_int128(self.nonce)

    @classmethod
    def from_reader(cls, reader):
        nonce = _read_int128(reader)
        return cls(nonce=nonce)

    def to_dict(self):
        return {'_': 'ReqPqMultiRequest', 'nonce': self.nonce}


class ReqDHParamsRequest(TLRequest):
    """
    req_DH_params#d712e4be nonce:int128 server_nonce:int128
        p:bytes q:bytes public_key_fingerprint:long encrypted_data:bytes
        = Server_DH_Params;
    Second step: send encrypted PQ inner data.
    """
    CONSTRUCTOR_ID = 0xd712e4be
    SUBCLASS_OF_ID = 0x60b14082  # Server_DH_Params

    def __init__(self, nonce, server_nonce, p, q, public_key_fingerprint, encrypted_data):
        self.nonce = nonce
        self.server_nonce = server_nonce
        self.p = p
        self.q = q
        self.public_key_fingerprint = public_key_fingerprint
        self.encrypted_data = encrypted_data

    def _bytes(self) -> bytes:
        return b''.join([
            struct.pack('<I', self.CONSTRUCTOR_ID),
            _write_int128(self.nonce),
            _write_int128(self.server_nonce),
            TLObject.serialize_bytes(self.p),
            TLObject.serialize_bytes(self.q),
            struct.pack('<q', self.public_key_fingerprint),
            TLObject.serialize_bytes(self.encrypted_data),
        ])

    @classmethod
    def from_reader(cls, reader):
        return cls(
            nonce=_read_int128(reader),
            server_nonce=_read_int128(reader),
            p=reader.tgread_bytes(),
            q=reader.tgread_bytes(),
            public_key_fingerprint=reader.read_long(),
            encrypted_data=reader.tgread_bytes(),
        )

    def to_dict(self):
        return {'_': 'ReqDHParamsRequest', 'nonce': self.nonce}


class SetClientDHParamsRequest(TLRequest):
    """
    set_client_DH_params#f5045f1f nonce:int128 server_nonce:int128
        encrypted_data:bytes = Set_client_DH_params_answer;
    Third step: send encrypted g_b.
    """
    CONSTRUCTOR_ID = 0xf5045f1f
    SUBCLASS_OF_ID = 0x22c0d325  # Set_client_DH_params_answer

    def __init__(self, nonce, server_nonce, encrypted_data):
        self.nonce = nonce
        self.server_nonce = server_nonce
        self.encrypted_data = encrypted_data

    def _bytes(self) -> bytes:
        return b''.join([
            struct.pack('<I', self.CONSTRUCTOR_ID),
            _write_int128(self.nonce),
            _write_int128(self.server_nonce),
            TLObject.serialize_bytes(self.encrypted_data),
        ])

    @classmethod
    def from_reader(cls, reader):
        return cls(
            nonce=_read_int128(reader),
            server_nonce=_read_int128(reader),
            encrypted_data=reader.tgread_bytes(),
        )

    def to_dict(self):
        return {'_': 'SetClientDHParamsRequest', 'nonce': self.nonce}


# ══════════════════════════════════════════════════════════════════════════════
# RESPONSES (server → client)
# ══════════════════════════════════════════════════════════════════════════════

class ResPQ(TLObject):
    """
    resPQ#05162463 nonce:int128 server_nonce:int128 pq:bytes
        server_public_key_fingerprints:Vector long = ResPQ;
    Server reply to ReqPqMultiRequest.
    """
    CONSTRUCTOR_ID = 0x05162463
    SUBCLASS_OF_ID = 0x051625A5

    def __init__(self, nonce, server_nonce, pq, server_public_key_fingerprints):
        self.nonce = nonce
        self.server_nonce = server_nonce
        self.pq = pq
        self.server_public_key_fingerprints = server_public_key_fingerprints

    @classmethod
    def from_reader(cls, reader):
        nonce = _read_int128(reader)
        server_nonce = _read_int128(reader)
        pq = reader.tgread_bytes()
        # Read vector of long fingerprints
        assert reader.read_int(signed=False) == 0x1cb5c415, 'Expected vector'
        count = reader.read_int()
        fingerprints = [reader.read_long() for _ in range(count)]
        return cls(nonce=nonce, server_nonce=server_nonce, pq=pq,
                   server_public_key_fingerprints=fingerprints)

    def _bytes(self) -> bytes:
        fps = struct.pack('<II', 0x1cb5c415, len(self.server_public_key_fingerprints))
        fps += b''.join(struct.pack('<q', fp) for fp in self.server_public_key_fingerprints)
        return b''.join([
            struct.pack('<I', self.CONSTRUCTOR_ID),
            _write_int128(self.nonce),
            _write_int128(self.server_nonce),
            TLObject.serialize_bytes(self.pq),
            fps,
        ])

    def to_dict(self):
        return {'_': 'ResPQ', 'nonce': self.nonce, 'server_nonce': self.server_nonce}


class PQInnerData(TLObject):
    """
    p_q_inner_data#83c95aec pq:bytes p:bytes q:bytes nonce:int128
        server_nonce:int128 new_nonce:int256 = P_Q_inner_data;
    Serialized and RSA-encrypted, then sent in ReqDHParamsRequest.
    """
    CONSTRUCTOR_ID = 0x83c95aec
    SUBCLASS_OF_ID = 0x5f1c7300

    def __init__(self, pq, p, q, nonce, server_nonce, new_nonce):
        self.pq = pq
        self.p = p
        self.q = q
        self.nonce = nonce
        self.server_nonce = server_nonce
        self.new_nonce = new_nonce

    def _bytes(self) -> bytes:
        return b''.join([
            struct.pack('<I', self.CONSTRUCTOR_ID),
            TLObject.serialize_bytes(self.pq),
            TLObject.serialize_bytes(self.p),
            TLObject.serialize_bytes(self.q),
            _write_int128(self.nonce),
            _write_int128(self.server_nonce),
            _write_int256(self.new_nonce),
        ])

    @classmethod
    def from_reader(cls, reader):
        return cls(
            pq=reader.tgread_bytes(),
            p=reader.tgread_bytes(),
            q=reader.tgread_bytes(),
            nonce=_read_int128(reader),
            server_nonce=_read_int128(reader),
            new_nonce=_read_int256(reader),
        )

    def to_dict(self):
        return {'_': 'PQInnerData'}


class PQInnerDataDc(TLObject):
    """
    p_q_inner_data_dc#a9f55f95 pq:bytes p:bytes q:bytes nonce:int128
        server_nonce:int128 new_nonce:int256 dc:int = P_Q_inner_data;
    DC-aware version of PQInnerData.
    """
    CONSTRUCTOR_ID = 0xa9f55f95
    SUBCLASS_OF_ID = 0x5f1c7300

    def __init__(self, pq, p, q, nonce, server_nonce, new_nonce, dc):
        self.pq = pq
        self.p = p
        self.q = q
        self.nonce = nonce
        self.server_nonce = server_nonce
        self.new_nonce = new_nonce
        self.dc = dc

    def _bytes(self) -> bytes:
        return b''.join([
            struct.pack('<I', self.CONSTRUCTOR_ID),
            TLObject.serialize_bytes(self.pq),
            TLObject.serialize_bytes(self.p),
            TLObject.serialize_bytes(self.q),
            _write_int128(self.nonce),
            _write_int128(self.server_nonce),
            _write_int256(self.new_nonce),
            struct.pack('<i', self.dc),
        ])

    @classmethod
    def from_reader(cls, reader):
        return cls(
            pq=reader.tgread_bytes(),
            p=reader.tgread_bytes(),
            q=reader.tgread_bytes(),
            nonce=_read_int128(reader),
            server_nonce=_read_int128(reader),
            new_nonce=_read_int256(reader),
            dc=reader.read_int(),
        )

    def to_dict(self):
        return {'_': 'PQInnerDataDc'}


class ServerDHParamsOk(TLObject):
    """
    server_DH_params_ok#d0e8075c nonce:int128 server_nonce:int128
        encrypted_answer:bytes = Server_DH_Params;
    Successful reply to ReqDHParamsRequest.
    """
    CONSTRUCTOR_ID = 0xd0e8075c
    SUBCLASS_OF_ID = 0x60b14082

    def __init__(self, nonce, server_nonce, encrypted_answer):
        self.nonce = nonce
        self.server_nonce = server_nonce
        self.encrypted_answer = encrypted_answer

    @classmethod
    def from_reader(cls, reader):
        return cls(
            nonce=_read_int128(reader),
            server_nonce=_read_int128(reader),
            encrypted_answer=reader.tgread_bytes(),
        )

    def _bytes(self) -> bytes:
        return b''.join([
            struct.pack('<I', self.CONSTRUCTOR_ID),
            _write_int128(self.nonce),
            _write_int128(self.server_nonce),
            TLObject.serialize_bytes(self.encrypted_answer),
        ])

    def to_dict(self):
        return {'_': 'ServerDHParamsOk', 'nonce': self.nonce}


class ServerDHParamsFail(TLObject):
    """
    server_DH_params_fail#79cb045d nonce:int128 server_nonce:int128
        new_nonce_hash:int128 = Server_DH_Params;
    Failed reply to ReqDHParamsRequest.
    """
    CONSTRUCTOR_ID = 0x79cb045d
    SUBCLASS_OF_ID = 0x60b14082

    def __init__(self, nonce, server_nonce, new_nonce_hash):
        self.nonce = nonce
        self.server_nonce = server_nonce
        self.new_nonce_hash = new_nonce_hash

    @classmethod
    def from_reader(cls, reader):
        return cls(
            nonce=_read_int128(reader),
            server_nonce=_read_int128(reader),
            new_nonce_hash=_read_int128(reader),
        )

    def _bytes(self) -> bytes:
        return b''.join([
            struct.pack('<I', self.CONSTRUCTOR_ID),
            _write_int128(self.nonce),
            _write_int128(self.server_nonce),
            _write_int128(self.new_nonce_hash),
        ])

    def to_dict(self):
        return {'_': 'ServerDHParamsFail', 'nonce': self.nonce}


class ServerDHInnerData(TLObject):
    """
    server_DH_inner_data#b5890dba nonce:int128 server_nonce:int128
        g:int dh_prime:bytes g_a:bytes server_time:int = Server_DH_inner_data;
    Decrypted from ServerDHParamsOk.encrypted_answer.
    """
    CONSTRUCTOR_ID = 0xb5890dba
    SUBCLASS_OF_ID = 0x45c23abe

    def __init__(self, nonce, server_nonce, g, dh_prime, g_a, server_time):
        self.nonce = nonce
        self.server_nonce = server_nonce
        self.g = g
        self.dh_prime = dh_prime
        self.g_a = g_a
        self.server_time = server_time

    @classmethod
    def from_reader(cls, reader):
        return cls(
            nonce=_read_int128(reader),
            server_nonce=_read_int128(reader),
            g=reader.read_int(),
            dh_prime=reader.tgread_bytes(),
            g_a=reader.tgread_bytes(),
            server_time=reader.read_int(),
        )

    def _bytes(self) -> bytes:
        return b''.join([
            struct.pack('<I', self.CONSTRUCTOR_ID),
            _write_int128(self.nonce),
            _write_int128(self.server_nonce),
            struct.pack('<i', self.g),
            TLObject.serialize_bytes(self.dh_prime),
            TLObject.serialize_bytes(self.g_a),
            struct.pack('<i', self.server_time),
        ])

    def to_dict(self):
        return {'_': 'ServerDHInnerData', 'g': self.g, 'server_time': self.server_time}


class ClientDHInnerData(TLObject):
    """
    client_DH_inner_data#6643b654 nonce:int128 server_nonce:int128
        retry_id:long g_b:bytes = Client_DH_Inner_Data;
    Serialized, hashed and AES-encrypted, then sent in SetClientDHParamsRequest.
    """
    CONSTRUCTOR_ID = 0x6643b654
    SUBCLASS_OF_ID = 0xa2b46df8

    def __init__(self, nonce, server_nonce, retry_id, g_b):
        self.nonce = nonce
        self.server_nonce = server_nonce
        self.retry_id = retry_id
        self.g_b = g_b

    def _bytes(self) -> bytes:
        return b''.join([
            struct.pack('<I', self.CONSTRUCTOR_ID),
            _write_int128(self.nonce),
            _write_int128(self.server_nonce),
            struct.pack('<q', self.retry_id),
            TLObject.serialize_bytes(self.g_b),
        ])

    @classmethod
    def from_reader(cls, reader):
        return cls(
            nonce=_read_int128(reader),
            server_nonce=_read_int128(reader),
            retry_id=reader.read_long(),
            g_b=reader.tgread_bytes(),
        )

    def to_dict(self):
        return {'_': 'ClientDHInnerData'}


# ── DhGen responses ─────────────────────────────────────────────────────────

class DhGenOk(TLObject):
    """
    dh_gen_ok#3bcbf734 nonce:int128 server_nonce:int128
        new_nonce_hash1:int128 = Set_client_DH_params_answer;
    Auth key generation succeeded.
    """
    CONSTRUCTOR_ID = 0x3bcbf734
    SUBCLASS_OF_ID = 0x22c0d325

    def __init__(self, nonce, server_nonce, new_nonce_hash1):
        self.nonce = nonce
        self.server_nonce = server_nonce
        self.new_nonce_hash1 = new_nonce_hash1

    @classmethod
    def from_reader(cls, reader):
        return cls(
            nonce=_read_int128(reader),
            server_nonce=_read_int128(reader),
            new_nonce_hash1=_read_int128(reader),
        )

    def _bytes(self) -> bytes:
        return b''.join([
            struct.pack('<I', self.CONSTRUCTOR_ID),
            _write_int128(self.nonce),
            _write_int128(self.server_nonce),
            _write_int128(self.new_nonce_hash1),
        ])

    def to_dict(self):
        return {'_': 'DhGenOk', 'nonce': self.nonce}


class DhGenRetry(TLObject):
    """
    dh_gen_retry#46dc1fb9 nonce:int128 server_nonce:int128
        new_nonce_hash2:int128 = Set_client_DH_params_answer;
    Auth key generation must be retried.
    """
    CONSTRUCTOR_ID = 0x46dc1fb9
    SUBCLASS_OF_ID = 0x22c0d325

    def __init__(self, nonce, server_nonce, new_nonce_hash2):
        self.nonce = nonce
        self.server_nonce = server_nonce
        self.new_nonce_hash2 = new_nonce_hash2

    @classmethod
    def from_reader(cls, reader):
        return cls(
            nonce=_read_int128(reader),
            server_nonce=_read_int128(reader),
            new_nonce_hash2=_read_int128(reader),
        )

    def _bytes(self) -> bytes:
        return b''.join([
            struct.pack('<I', self.CONSTRUCTOR_ID),
            _write_int128(self.nonce),
            _write_int128(self.server_nonce),
            _write_int128(self.new_nonce_hash2),
        ])

    def to_dict(self):
        return {'_': 'DhGenRetry', 'nonce': self.nonce}


class DhGenFail(TLObject):
    """
    dh_gen_fail#a69dae02 nonce:int128 server_nonce:int128
        new_nonce_hash3:int128 = Set_client_DH_params_answer;
    Auth key generation failed permanently.
    """
    CONSTRUCTOR_ID = 0xa69dae02
    SUBCLASS_OF_ID = 0x22c0d325

    def __init__(self, nonce, server_nonce, new_nonce_hash3):
        self.nonce = nonce
        self.server_nonce = server_nonce
        self.new_nonce_hash3 = new_nonce_hash3

    @classmethod
    def from_reader(cls, reader):
        return cls(
            nonce=_read_int128(reader),
            server_nonce=_read_int128(reader),
            new_nonce_hash3=_read_int128(reader),
        )

    def _bytes(self) -> bytes:
        return b''.join([
            struct.pack('<I', self.CONSTRUCTOR_ID),
            _write_int128(self.nonce),
            _write_int128(self.server_nonce),
            _write_int128(self.new_nonce_hash3),
        ])

    def to_dict(self):
        return {'_': 'DhGenFail', 'nonce': self.nonce}


# ══════════════════════════════════════════════════════════════════════════════
# RUNTIME PROTOCOL TYPES  (server → client, after auth key is established)
# These are MTProto service messages, not in the Telegram API TL schema.
# ══════════════════════════════════════════════════════════════════════════════

class Pong(TLObject):
    """pong#347773c5 msg_id:long ping_id:long = Pong;"""
    CONSTRUCTOR_ID = 0x347773c5

    def __init__(self, msg_id, ping_id):
        self.msg_id  = msg_id
        self.ping_id = ping_id

    @classmethod
    def from_reader(cls, reader):
        return cls(msg_id=reader.read_long(), ping_id=reader.read_long())

    def _bytes(self):
        return struct.pack('<Iqq', self.CONSTRUCTOR_ID, self.msg_id, self.ping_id)

    def to_dict(self):
        return {'_': 'Pong', 'msg_id': self.msg_id, 'ping_id': self.ping_id}


class MsgsAck(TLObject):
    """msgs_ack#62d6b459 msg_ids:Vector<long> = MsgsAck;"""
    CONSTRUCTOR_ID = 0x62d6b459

    def __init__(self, msg_ids):
        self.msg_ids = msg_ids

    @classmethod
    def from_reader(cls, reader):
        assert reader.read_int(signed=False) == 0x1cb5c415  # Vector
        count = reader.read_int()
        return cls(msg_ids=[reader.read_long() for _ in range(count)])

    def _bytes(self):
        return (struct.pack('<III', self.CONSTRUCTOR_ID, 0x1cb5c415, len(self.msg_ids))
                + b''.join(struct.pack('<q', m) for m in self.msg_ids))

    def to_dict(self):
        return {'_': 'MsgsAck', 'msg_ids': self.msg_ids}


class MsgResendReq(TLObject):
    """msg_resend_req#7d861a08 msg_ids:Vector<long> = MsgResendReq;
    Sent by client to request server to re-send missed messages (BUG 7 fix)."""
    CONSTRUCTOR_ID = 0x7d861a08

    def __init__(self, msg_ids):
        self.msg_ids = msg_ids

    @classmethod
    def from_reader(cls, reader):
        assert reader.read_int(signed=False) == 0x1cb5c415  # Vector
        count = reader.read_int()
        return cls(msg_ids=[reader.read_long() for _ in range(count)])

    def _bytes(self):
        return (struct.pack('<III', self.CONSTRUCTOR_ID, 0x1cb5c415, len(self.msg_ids))
                + b''.join(struct.pack('<q', m) for m in self.msg_ids))

    def to_dict(self):
        return {'_': 'MsgResendReq', 'msg_ids': self.msg_ids}


class BadMsgNotification(TLObject):
    """bad_msg_notification#a7eff811 bad_msg_id:long bad_msg_seqno:int error_code:int = BadMsgNotification;"""
    CONSTRUCTOR_ID = 0xa7eff811

    def __init__(self, bad_msg_id, bad_msg_seqno, error_code):
        self.bad_msg_id   = bad_msg_id
        self.bad_msg_seqno = bad_msg_seqno
        self.error_code   = error_code

    @classmethod
    def from_reader(cls, reader):
        return cls(
            bad_msg_id=reader.read_long(),
            bad_msg_seqno=reader.read_int(),
            error_code=reader.read_int(),
        )

    def _bytes(self):
        return struct.pack('<Iqii', self.CONSTRUCTOR_ID,
                           self.bad_msg_id, self.bad_msg_seqno, self.error_code)

    def to_dict(self):
        return {'_': 'BadMsgNotification', 'error_code': self.error_code}


class BadServerSalt(TLObject):
    """bad_server_salt#edab447b bad_msg_id:long bad_msg_seqno:int error_code:int new_server_salt:long = BadMsgNotification;"""
    CONSTRUCTOR_ID = 0xedab447b

    def __init__(self, bad_msg_id, bad_msg_seqno, error_code, new_server_salt):
        self.bad_msg_id      = bad_msg_id
        self.bad_msg_seqno   = bad_msg_seqno
        self.error_code      = error_code
        self.new_server_salt = new_server_salt

    @classmethod
    def from_reader(cls, reader):
        return cls(
            bad_msg_id=reader.read_long(),
            bad_msg_seqno=reader.read_int(),
            error_code=reader.read_int(),
            new_server_salt=reader.read_long(),
        )

    def _bytes(self):
        return struct.pack('<Iqiiq', self.CONSTRUCTOR_ID,
                           self.bad_msg_id, self.bad_msg_seqno,
                           self.error_code, self.new_server_salt)

    def to_dict(self):
        return {'_': 'BadServerSalt', 'error_code': self.error_code,
                'new_server_salt': self.new_server_salt}


class NewSessionCreated(TLObject):
    """new_session_created#9ec20908 first_msg_id:long unique_id:long server_salt:long = NewSession;"""
    CONSTRUCTOR_ID = 0x9ec20908

    def __init__(self, first_msg_id, unique_id, server_salt):
        self.first_msg_id = first_msg_id
        self.unique_id    = unique_id
        self.server_salt  = server_salt

    @classmethod
    def from_reader(cls, reader):
        return cls(
            first_msg_id=reader.read_long(),
            unique_id=reader.read_long(),
            server_salt=reader.read_long(),
        )

    def _bytes(self):
        return struct.pack('<Iqqq', self.CONSTRUCTOR_ID,
                           self.first_msg_id, self.unique_id, self.server_salt)

    def to_dict(self):
        return {'_': 'NewSessionCreated', 'server_salt': self.server_salt}


class MsgDetailedInfo(TLObject):
    """msg_detailed_info#276d3ec6 msg_id:long answer_msg_id:long bytes:int status:int = MsgDetailedInfo;"""
    CONSTRUCTOR_ID = 0x276d3ec6

    def __init__(self, msg_id, answer_msg_id, bytes_, status):
        self.msg_id        = msg_id
        self.answer_msg_id = answer_msg_id
        self.bytes         = bytes_
        self.status        = status

    @classmethod
    def from_reader(cls, reader):
        return cls(
            msg_id=reader.read_long(),
            answer_msg_id=reader.read_long(),
            bytes_=reader.read_int(),
            status=reader.read_int(),
        )

    def _bytes(self):
        return struct.pack('<Iqqii', self.CONSTRUCTOR_ID,
                           self.msg_id, self.answer_msg_id, self.bytes, self.status)

    def to_dict(self):
        return {'_': 'MsgDetailedInfo'}


class MsgNewDetailedInfo(TLObject):
    """msg_new_detailed_info#809db6df answer_msg_id:long bytes:int status:int = MsgDetailedInfo;"""
    CONSTRUCTOR_ID = 0x809db6df

    def __init__(self, answer_msg_id, bytes_, status):
        self.answer_msg_id = answer_msg_id
        self.bytes         = bytes_
        self.status        = status

    @classmethod
    def from_reader(cls, reader):
        return cls(
            answer_msg_id=reader.read_long(),
            bytes_=reader.read_int(),
            status=reader.read_int(),
        )

    def _bytes(self):
        return struct.pack('<Iqii', self.CONSTRUCTOR_ID,
                           self.answer_msg_id, self.bytes, self.status)

    def to_dict(self):
        return {'_': 'MsgNewDetailedInfo'}


class MsgsStateReq(TLObject):
    """msgs_state_req#da69fb52 msg_ids:Vector<long> = MsgsStateReq;"""
    CONSTRUCTOR_ID = 0xda69fb52

    def __init__(self, msg_ids):
        self.msg_ids = msg_ids

    @classmethod
    def from_reader(cls, reader):
        assert reader.read_int(signed=False) == 0x1cb5c415
        count = reader.read_int()
        return cls(msg_ids=[reader.read_long() for _ in range(count)])

    def _bytes(self):
        return (struct.pack('<III', self.CONSTRUCTOR_ID, 0x1cb5c415, len(self.msg_ids))
                + b''.join(struct.pack('<q', m) for m in self.msg_ids))

    def to_dict(self):
        return {'_': 'MsgsStateReq'}


class FutureSalt(TLObject):
    """future_salt#0949d9dc valid_since:int valid_until:int salt:long = FutureSalt;"""
    CONSTRUCTOR_ID = 0x0949d9dc

    def __init__(self, valid_since, valid_until, salt):
        self.valid_since = valid_since
        self.valid_until = valid_until
        self.salt        = salt

    @classmethod
    def from_reader(cls, reader):
        return cls(
            valid_since=reader.read_int(),
            valid_until=reader.read_int(),
            salt=reader.read_long(),
        )

    def _bytes(self):
        return struct.pack('<Iiiq', self.CONSTRUCTOR_ID,
                           self.valid_since, self.valid_until, self.salt)

    def to_dict(self):
        return {'_': 'FutureSalt'}


class FutureSalts(TLObject):
    """future_salts#ae500895 req_msg_id:long now:int salts:vector<future_salt> = FutureSalts;"""
    CONSTRUCTOR_ID = 0xae500895

    def __init__(self, req_msg_id, now, salts):
        self.req_msg_id = req_msg_id
        self.now        = now
        self.salts      = salts

    @classmethod
    def from_reader(cls, reader):
        req_msg_id = reader.read_long()
        now = reader.read_int()
        count = reader.read_int()  # bare vector (no 0x1cb5c415 prefix)
        salts = []
        for _ in range(count):
            reader.read_int(signed=False)  # skip FutureSalt constructor
            salts.append(FutureSalt.from_reader(reader))
        return cls(req_msg_id=req_msg_id, now=now, salts=salts)

    def _bytes(self):
        return b''  # not sent by client

    def to_dict(self):
        return {'_': 'FutureSalts'}


# ── Registry for BinaryReader.tgread_object() ──────────────────────────────

HANDSHAKE_TYPES = {
    # Auth key handshake types
    ReqPqMultiRequest.CONSTRUCTOR_ID:        ReqPqMultiRequest,
    ReqDHParamsRequest.CONSTRUCTOR_ID:       ReqDHParamsRequest,
    SetClientDHParamsRequest.CONSTRUCTOR_ID: SetClientDHParamsRequest,
    ResPQ.CONSTRUCTOR_ID:                    ResPQ,
    PQInnerData.CONSTRUCTOR_ID:              PQInnerData,
    PQInnerDataDc.CONSTRUCTOR_ID:            PQInnerDataDc,
    ServerDHParamsOk.CONSTRUCTOR_ID:         ServerDHParamsOk,
    ServerDHParamsFail.CONSTRUCTOR_ID:       ServerDHParamsFail,
    ServerDHInnerData.CONSTRUCTOR_ID:        ServerDHInnerData,
    ClientDHInnerData.CONSTRUCTOR_ID:        ClientDHInnerData,
    DhGenOk.CONSTRUCTOR_ID:                  DhGenOk,
    DhGenRetry.CONSTRUCTOR_ID:              DhGenRetry,
    DhGenFail.CONSTRUCTOR_ID:               DhGenFail,
    # Runtime protocol service types
    Pong.CONSTRUCTOR_ID:                     Pong,
    MsgsAck.CONSTRUCTOR_ID:                 MsgsAck,
    MsgResendReq.CONSTRUCTOR_ID:            MsgResendReq,
    BadMsgNotification.CONSTRUCTOR_ID:       BadMsgNotification,
    BadServerSalt.CONSTRUCTOR_ID:            BadServerSalt,
    NewSessionCreated.CONSTRUCTOR_ID:        NewSessionCreated,
    MsgDetailedInfo.CONSTRUCTOR_ID:          MsgDetailedInfo,
    MsgNewDetailedInfo.CONSTRUCTOR_ID:       MsgNewDetailedInfo,
    MsgsStateReq.CONSTRUCTOR_ID:             MsgsStateReq,
    FutureSalt.CONSTRUCTOR_ID:              FutureSalt,
    FutureSalts.CONSTRUCTOR_ID:             FutureSalts,
}
