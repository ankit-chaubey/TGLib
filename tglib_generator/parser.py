"""
TL (Type Language) Schema Parser for tglib.

Supports two TL file formats:
  Format 1: Plain - types at top, then '---functions---' section
  Format 2: Sectioned - '---types---' section, then '---functions---' section
"""
import re
import struct
import zlib
from dataclasses import dataclass, field
from typing import List, Optional


CORE_TYPE_IDS = {
    0xbc799737,  # boolFalse
    0x997275b5,  # boolTrue
    0x3fedd339,  # true
    0xc4b9f9bb,  # error
    0x56730bcc,  # null
}

# These types use bytes instead of string for auth key process
AUTH_KEY_TYPE_IDS = {
    0x05162463,  # resPQ
    0x83c95aec,  # p_q_inner_data
    0xa9f55f95,  # p_q_inner_data_dc
    0x3c6a84d4,  # p_q_inner_data_temp
    0x56fddf88,  # p_q_inner_data_temp_dc
    0xd0e8075c,  # server_DH_params_ok
    0xb5890dba,  # server_DH_inner_data
    0x6643b654,  # client_DH_inner_data
    0xd712e4be,  # req_DH_params
    0xf5045f1f,  # set_client_DH_params
    0x3072cfa1,  # gzip_packed
}


def _sanitize_identifier(name: str) -> str:
    """
    Turn any string into a valid Python identifier.

    Rules applied in order:
      1. Replace dots, dashes, slashes → underscore  (core fix for Layer.223)
      2. Strip all remaining non-[a-zA-Z0-9_] characters
      3. Collapse multiple underscores
      4. If result starts with a digit, prefix 'Tl'
      5. If result is empty, use '_Unknown'
    """
    # Step 1: treat dots and other separators as word boundaries
    name = re.sub(r'[.\-/\\]', '_', name)
    # Step 2: strip anything that isn't alphanumeric or underscore
    name = re.sub(r'[^\w]', '', name)
    # Step 3: collapse runs of underscores
    name = re.sub(r'_+', '_', name).strip('_')
    # Step 4: can't start with a digit
    if name and name[0].isdigit():
        name = 'Tl' + name
    # Step 5: must not be empty
    return name or '_Unknown'


def snake_to_camel(name: str, suffix: str = '') -> str:
    """
    Convert snake_case or camelCase TL names to PascalCase Python identifier.

    FIX: dots (and other non-identifier chars) are now stripped BEFORE splitting
    so that names like 'Layer.223' → 'Layer223' instead of crashing.
    """
    # Sanitize first — removes dots and other invalid chars
    name = _sanitize_identifier(name)

    # Split on underscores
    parts = name.split('_')

    # Also split on embedded camelCase boundaries
    final_parts = []
    for part in parts:
        sub = re.sub(r'([a-z])([A-Z])', r'\1_\2', part).split('_')
        final_parts.extend(sub)

    result = ''.join(word.capitalize() for word in final_parts if word)

    # Digit-leading guard (second layer, after capitalize may strip prefix)
    if result and result[0].isdigit():
        result = 'Tl' + result

    if not result:
        result = '_Unknown'

    return result + suffix


@dataclass
class TLArg:
    name: str
    arg_type: str
    generic_definition: bool = False

    # Derived
    is_vector: bool = False
    use_vector_id: bool = False
    flag: Optional[str] = None
    flag_index: int = -1
    flag_indicator: bool = False
    is_generic: bool = False
    can_be_inferred: bool = False
    skip_constructor_id: bool = False
    cls: list = field(default_factory=list)

    def __post_init__(self):
        # Fix reserved Python names
        if self.name == 'self':
            self.name = 'is_self'
        elif self.name == 'from':
            self.name = 'from_'

        self.can_be_inferred = (self.name == 'random_id')

        if self.arg_type == '#':
            self.flag_indicator = True
            self.type = None
            self.is_generic = False
            return

        self.flag_indicator = False
        self.is_generic = self.arg_type.startswith('!')
        t = self.arg_type.lstrip('!')

        # Check for flag type: flags.0?RealType
        flag_match = re.match(r'(\w+)\.(\d+)\?([\w<>.]+)', t)
        if flag_match:
            self.flag = flag_match.group(1)
            self.flag_index = int(flag_match.group(2))
            t = flag_match.group(3)

        # Check for Vector<T>
        vector_match = re.match(r'[Vv]ector<([\w\d.]+)>', t)
        if vector_match:
            self.is_vector = True
            self.use_vector_id = t[0] == 'V'
            t = vector_match.group(1)

        # If type starts with lowercase it's a constructor (skip constructor ID)
        if t.split('.')[-1][:1].islower():
            self.skip_constructor_id = True

        # Detect date fields
        if t == 'int' and (
            re.search(r'(\b|_)(date|until|since)(\b|_)', self.name)
            or self.name in ('expires', 'expires_at', 'was_online')
        ):
            t = 'date'

        self.type = t

    def type_hint(self) -> str:
        cls = self.type or ''
        if '.' in cls:
            cls = cls.split('.')[-1]
        mapping = {
            'int': 'int', 'long': 'int', 'int128': 'int', 'int256': 'int',
            'double': 'float', 'string': 'str', 'date': 'Optional[datetime]',
            'bytes': 'bytes', 'Bool': 'bool', 'true': 'bool',
        }
        result = mapping.get(cls, f"'Type{cls}'")
        if self.is_vector:
            result = f'List[{result}]'
        if self.flag and cls != 'date':
            result = f'Optional[{result}]'
        return result

    def real_type(self) -> str:
        t = self.type or ''
        if self.flag_indicator:
            return '#'
        if self.is_vector:
            prefix = 'Vector' if self.use_vector_id else 'vector'
            t = f'{prefix}<{t}>'
        if self.is_generic:
            t = f'!{t}'
        if self.flag:
            t = f'{self.flag}.{self.flag_index}?{t}'
        return t

    def orig_name(self) -> str:
        return self.name.replace('is_self', 'self').rstrip('_')

    def __str__(self):
        n = self.orig_name()
        if self.generic_definition:
            return f'{{{n}:{self.real_type()}}}'
        return f'{n}:{self.real_type()}'

    def __repr__(self):
        return str(self).replace(':date', ':int').replace('?date', '?int')


@dataclass
class TLObject:
    fullname: str
    object_id: Optional[str]
    args: List[TLArg]
    result: str
    is_function: bool
    layer: int

    def __post_init__(self):
        # FIX: use rsplit('.', 1) so that multi-dot fullnames like
        # 'keyboardButton.Layer.223' give namespace='keyboardButton', name='Layer.223'
        # rather than namespace='keyboardButton', name='Layer.223' from split(maxsplit=1).
        # ACTUALLY the real fix: split on the FIRST dot only for namespace, then
        # sanitize the rest of the name — see snake_to_camel() which strips dots.
        if '.' in self.fullname:
            # Take the first segment as namespace, everything else as name.
            # snake_to_camel will safely strip any remaining dots from the name.
            first_dot = self.fullname.index('.')
            self.namespace = self.fullname[:first_dot]
            self.name      = self.fullname[first_dot + 1:]
        else:
            self.namespace = None
            self.name      = self.fullname

        if self.object_id is None:
            self.id = self._infer_id()
        else:
            self.id = int(self.object_id, 16)

        suffix = 'Request' if self.is_function else ''
        self.class_name = snake_to_camel(self.name, suffix=suffix)

        # ── Safety net: guarantee class_name is a valid Python identifier ──
        # snake_to_camel() already handles this, but we double-check here so
        # a future change to that function can never silently ship broken output.
        if not self.class_name.isidentifier():
            safe = re.sub(r'[^\w]', '', self.class_name)
            if safe and safe[0].isdigit():
                safe = 'Tl' + safe
            self.class_name = safe or '_Unknown'

        self.real_args = [
            a for a in self._sorted_args()
            if not (a.flag_indicator or a.generic_definition)
        ]

    def _sorted_args(self):
        return sorted(
            self.args,
            key=lambda x: bool(x.flag) or x.can_be_inferred
        )

    @property
    def innermost_result(self) -> str:
        idx = self.result.find('<')
        return self.result if idx == -1 else self.result[idx + 1:-1]

    def _infer_id(self) -> int:
        rep = repr(self)
        rep = (rep
               .replace(':bytes ', ':string ')
               .replace('?bytes ', '?string ')
               .replace('<', ' ').replace('>', '')
               .replace('{', '').replace('}', ''))
        rep = re.sub(r' \w+:\w+\.\d+\?true', '', rep)
        return zlib.crc32(rep.encode('ascii')) & 0xFFFFFFFF

    def __repr__(self, ignore_id=False):
        if self.id is None or ignore_id:
            hex_id = ''
        else:
            hex_id = f'#{self.id:08x}'
        args_str = (' ' + ' '.join(repr(a) for a in self.args)) if self.args else ''
        return f'{self.fullname}{hex_id}{args_str} = {self.result}'


def _parse_args(line: str) -> List[TLArg]:
    args_match = re.findall(
        r'({)?'
        r'(\w+)'
        r':'
        r'([\w\d<>#.?!]+)'
        r'}?',
        line
    )
    return [
        TLArg(name=name, arg_type=arg_type, generic_definition=(brace != ''))
        for brace, name, arg_type in args_match
    ]


def _from_line(line: str, is_function: bool, layer: int) -> Optional[TLObject]:
    match = re.match(
        r'^([\w.]+)'            # name (may contain dots for namespacing)
        r'(?:#([0-9a-fA-F]+))?' # optional #id
        r'(?:\s{?\w+:[\w\d<>#.?!]+}?)*'  # args
        r'\s=\s'                # ' = '
        r'([\w\d<>#.?]+);$',    # result type
        line
    )
    if match is None:
        return None

    return TLObject(
        fullname=match.group(1),
        object_id=match.group(2),
        result=match.group(3),
        is_function=is_function,
        layer=layer,
        args=_parse_args(line),
    )


def find_layer(file_path: str) -> int:
    """Find the LAYER number in a TL file (// LAYER N comment)."""
    layer_re = re.compile(r'^//\s*LAYER\s*(\d+)$')
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                m = layer_re.match(line.strip())
                if m:
                    return int(m.group(1))
    except Exception:
        pass
    return 0


def parse_tl(file_path: str, layer: int = 0) -> List[TLObject]:
    """
    Parse a TL schema file and return all TLObjects.

    Supports:
      - Format 1: Types at top, then '---functions---' marks function section
      - Format 2: '---types---' section, then '---functions---' section
    """
    if layer == 0:
        layer = find_layer(file_path)

    objects = []
    by_name = {}
    by_type = {}

    with open(file_path, 'r', encoding='utf-8') as f:
        is_function = False
        for raw_line in f:
            # Strip comments
            comment_idx = raw_line.find('//')
            if comment_idx != -1:
                raw_line = raw_line[:comment_idx]
            line = raw_line.strip()
            if not line:
                continue

            # Section markers
            section_match = re.match(r'^---(\w+)---$', line)
            if section_match:
                section = section_match.group(1)
                is_function = (section == 'functions')
                continue

            # Skip vector definition
            if line.startswith('vector#1cb5c415'):
                continue

            try:
                obj = _from_line(line, is_function, layer)
                if obj is None:
                    continue
                if obj.id in CORE_TYPE_IDS:
                    continue

                # Fix auth key types: string -> bytes
                if obj.id in AUTH_KEY_TYPE_IDS:
                    for arg in obj.args:
                        if hasattr(arg, 'type') and arg.type == 'string':
                            arg.type = 'bytes'

                objects.append(obj)
                if not obj.is_function:
                    by_name[obj.fullname] = obj
                    by_type.setdefault(obj.result, []).append(obj)

            except Exception:
                continue

    # Resolve cls references
    for obj in objects:
        for arg in obj.args:
            if not hasattr(arg, 'type') or not arg.type:
                continue
            arg.cls = (
                by_type.get(arg.type)
                or ([by_name[arg.type]] if arg.type in by_name else [])
            )

    return objects
