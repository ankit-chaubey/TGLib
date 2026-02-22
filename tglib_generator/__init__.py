"""tglib TL code generator package."""
from .parser import parse_tl, find_layer, TLObject, TLArg
from .generator import generate_tl_modules

__all__ = ['parse_tl', 'find_layer', 'TLObject', 'TLArg', 'generate_tl_modules']
