from .gzippacked import GzipPacked
from .messagecontainer import MessageContainer
from .rpcresult import RpcResult
from .tlmessage import TLMessage
from ..mtproto_types import HANDSHAKE_TYPES

core_objects = {
    GzipPacked.CONSTRUCTOR_ID: GzipPacked,
    MessageContainer.CONSTRUCTOR_ID: MessageContainer,
    RpcResult.CONSTRUCTOR_ID: RpcResult,
    **HANDSHAKE_TYPES,
}
