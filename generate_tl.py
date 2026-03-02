#!/usr/bin/env python3
"""
tglib TL Code Generator
=======================
Generates Python source files from Telegram TL schema files.

MODES
─────────────────────────────────────────────────────────────────────────────
  stable        Use tl_files/api.tl  (Telegram Desktop production schema)
                Authoritative constructor IDs, Payments/Stars APIs, always
                in sync with the live Telegram client.

  beta          Use tl_files/main_api.tl  (community-maintained schema)
                Has ~25 extra legacy types.  Bleeding-edge additions appear
                here first.  Great for testing upcoming API changes.

  experimental  Smart union of BOTH files  ← the fun one 😁
                • Same constructor ID  → keep first seen (no duplicate)
                • Same name, different ID  → richer version wins as base,
                  PLUS extra fields from the other are merged in
                • Completely new name/ID  → always added
                • Output is wiped clean before writing — ZERO trace of any
                  previous generation pass

─────────────────────────────────────────────────────────────────────────────

Usage:
    python generate_tl.py                          # stable (default)
    python generate_tl.py --mode stable
    python generate_tl.py --mode beta
    python generate_tl.py --mode experimental

    python generate_tl.py --tl path/to/my.tl       # custom file override
    python generate_tl.py --out /path/to/tglib      # custom output dir
    python generate_tl.py --layer 224               # force layer number

Legacy --source flag still works (upgrade_layer.py uses it):
    --source main   → same as --mode beta
    --source api    → same as --mode stable
    --source both   → same as --mode experimental
"""
import argparse
import ast
import glob
import os
import sys
from typing import List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tglib_generator.parser import parse_tl, find_layer, TLObject

# ── TL file locations ──────────────────────────────────────────────────────
TL_STABLE   = 'tl_files/api.tl'        # Telegram Desktop production
TL_BETA     = 'tl_files/main_api.tl'   # Community / bleeding-edge
DEFAULT_OUT  = 'tglib'
DEFAULT_MODE = 'stable'

MODES = ('stable', 'beta', 'experimental')

# backwards-compat map for --source flag
LEGACY_SOURCE_MAP = {'main': 'beta', 'api': 'stable', 'both': 'experimental'}

MODE_LABELS = {
    'stable':       '🟢  Stable       (api.tl — Telegram Desktop production)',
    'beta':         '🔵  Beta         (main_api.tl — bleeding-edge community)',
    'experimental': '🟣  Experimental (smart union: api.tl ⊕ main_api.tl)',
    'custom':       '⚙️   Custom       (user-supplied TL file(s))',
}


# ══════════════════════════════════════════════════════════════════════════════
# Loading / merging
# ══════════════════════════════════════════════════════════════════════════════

def _load_file(path: str, layer_override: int = None) -> List[TLObject]:
    if not os.path.exists(path):
        print(f'❌  TL file not found: {path}', file=sys.stderr)
        sys.exit(1)
    layer = layer_override or find_layer(path)
    print(f'   {path}  (layer {layer or "unknown"})')
    return parse_tl(path, layer=layer or 0)


def _dedup(objects_list: List[List[TLObject]]) -> List[TLObject]:
    """Simple first-wins dedup on both id and fullname."""
    seen_ids, seen_names = set(), set()
    result = []
    for objects in objects_list:
        added = skipped = 0
        for obj in objects:
            if obj.id not in seen_ids and obj.fullname not in seen_names:
                result.append(obj)
                seen_ids.add(obj.id)
                seen_names.add(obj.fullname)
                added += 1
            else:
                skipped += 1
        print(f'   → {added} objects added, {skipped} duplicates skipped')
    return result


def _merge_args(base: TLObject, extra: TLObject) -> TLObject:
    """
    Merge fields from two versions of the same constructor name.

    - base supplies the authoritative constructor ID and wire order.
    - Any arg from extra whose name is NOT in base gets appended.
    - Returns a new TLObject with the merged arg list.
    """
    base_names = {a.name for a in base.args}
    merged_args = list(base.args)
    extra_added = 0

    for arg in extra.args:
        if arg.name not in base_names:
            merged_args.append(arg)
            base_names.add(arg.name)
            extra_added += 1

    if extra_added == 0:
        return base

    merged = TLObject(
        fullname    = base.fullname,
        object_id   = f'{base.id:08x}',
        args        = merged_args,
        result      = base.result,
        is_function = base.is_function,
        layer       = base.layer,
    )
    merged._extra_fields_count = extra_added
    return merged


def _merge_experimental(primary: List[TLObject],
                        secondary: List[TLObject]) -> List[TLObject]:
    """
    Experimental smart union.

    Rule 1 — same ID, same name  → pure duplicate → skip
    Rule 2 — same ID, diff name  → constructor collision → keep primary, warn
    Rule 3 — same name, diff ID  → version split → richer base + merge extra fields
    Rule 4 — new name, new ID    → add from secondary
    """
    by_id   = {obj.id:       obj for obj in primary}
    by_name = {obj.fullname: obj for obj in primary}

    result         = list(primary)
    added          = merged = skipped = conflicts = 0

    for obj in secondary:
        id_hit   = by_id.get(obj.id)
        name_hit = by_name.get(obj.fullname)

        # Rule 1 – exact duplicate
        if id_hit is not None and id_hit.fullname == obj.fullname:
            skipped += 1
            continue

        # Rule 2 – constructor ID collision (different names, same ID)
        if id_hit is not None:
            print(f'   ⚠  ID collision 0x{obj.id:08x}: '
                  f'{id_hit.fullname!r} vs {obj.fullname!r} — keeping primary')
            conflicts += 1
            continue

        # Rule 3 – version split (same name, different CRC)
        if name_hit is not None:
            # Pick richer object (more args) as the base
            if len(obj.args) > len(name_hit.args):
                base, other = obj, name_hit
            else:
                base, other = name_hit, obj

            new_obj = _merge_args(base, other)
            extra_n = getattr(new_obj, '_extra_fields_count', 0)

            # Replace in result list
            idx = next(i for i, o in enumerate(result) if o.fullname == name_hit.fullname)
            result[idx] = new_obj
            by_id[new_obj.id]         = new_obj
            by_name[new_obj.fullname] = new_obj

            if extra_n:
                print(f'   🔀  {obj.fullname!r}: merged +{extra_n} field(s) '
                      f'(base CRC=0x{base.id:08x})')
                merged += 1
            else:
                skipped += 1
            continue

        # Rule 4 – brand new type from secondary
        result.append(obj)
        by_id[obj.id]         = obj
        by_name[obj.fullname] = obj
        added += 1

    print(f'   → {added} new types added from beta')
    print(f'   → {merged} types field-merged (version splits resolved)')
    print(f'   → {skipped} pure duplicates skipped')
    if conflicts:
        print(f'   → {conflicts} constructor ID collision(s) (primary kept)')

    return result


# ══════════════════════════════════════════════════════════════════════════════
# Clean-slate wipe
# ══════════════════════════════════════════════════════════════════════════════

def _wipe_generated(out_dir: str):
    """
    Delete every previously-generated TL module so no ghost constructors
    from old runs survive into the new one.

    Removes:
        tglib/tl/types/*.py
        tglib/tl/functions/*.py
        tglib/tl/alltlobjects.py
    """
    tl_dir = os.path.join(out_dir, 'tl')
    total  = 0

    for sub in ('types', 'functions'):
        d = os.path.join(tl_dir, sub)
        if os.path.isdir(d):
            for f in glob.glob(os.path.join(d, '*.py')):
                os.remove(f)
                total += 1

    alltl = os.path.join(tl_dir, 'alltlobjects.py')
    if os.path.exists(alltl):
        os.remove(alltl)
        total += 1

    if total:
        print(f'   🧹  Wiped {total} previously-generated file(s) — clean slate')


# ══════════════════════════════════════════════════════════════════════════════
# Post-gen AST validation
# ══════════════════════════════════════════════════════════════════════════════

def _ast_validate(out_dir: str):
    check_dirs = [
        os.path.join(out_dir, 'tl', 'types'),
        os.path.join(out_dir, 'tl', 'functions'),
    ]
    errors  = []
    checked = 0
    for d in check_dirs:
        for pyfile in glob.glob(os.path.join(d, '*.py')):
            checked += 1
            with open(pyfile, 'r', encoding='utf-8') as fh:
                src = fh.read()
            try:
                ast.parse(src)
            except SyntaxError as e:
                errors.append((pyfile, e))

    if errors:
        print(f'\n❌  {len(errors)} generated file(s) have syntax errors:')
        for path, err in errors:
            print(f'   {path}: {err}')
        sys.exit(1)
    else:
        print(f'✅  {checked} files checked — all valid')


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(
        description='Generate tglib TL Python modules',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        '--mode', default=None, choices=list(MODES),
        help='Generation mode: stable | beta | experimental  (default: stable)',
    )
    # Legacy --source kept for upgrade_layer.py backwards compat
    p.add_argument(
        '--source', default=None,
        choices=list(LEGACY_SOURCE_MAP.keys()) + list(MODES),
        help='[Legacy alias] main→beta  api→stable  both→experimental',
    )
    p.add_argument(
        '--tl', dest='tl_files', action='append', metavar='FILE',
        help='Explicit TL file path (can repeat; overrides --mode)',
    )
    p.add_argument(
        '--out', default=DEFAULT_OUT, metavar='DIR',
        help=f'tglib package root directory  (default: {DEFAULT_OUT})',
    )
    p.add_argument(
        '--layer', type=int, default=None,
        help='Force a TL layer number (auto-detected from file by default)',
    )

    args = p.parse_args()

    # ── Resolve mode ───────────────────────────────────────────────────────
    if args.tl_files:
        mode = 'custom'
    elif args.mode:
        mode = args.mode
    elif args.source:
        mode = LEGACY_SOURCE_MAP.get(args.source, args.source)
    else:
        mode = DEFAULT_MODE

    # ── Validate output dir ────────────────────────────────────────────────
    if not os.path.isdir(args.out):
        print(f'❌  Output directory not found: {args.out}', file=sys.stderr)
        sys.exit(1)

    print(f'\nMode  : {MODE_LABELS.get(mode, mode)}')

    # ── Load objects ───────────────────────────────────────────────────────
    all_objects: List[TLObject] = []

    if mode == 'custom':
        missing = [f for f in args.tl_files if not os.path.exists(f)]
        if missing:
            print(f'❌  File(s) not found: {", ".join(missing)}', file=sys.stderr)
            sys.exit(1)
        print(f'Parsing TL  : {", ".join(args.tl_files)}')
        all_objects = _dedup([_load_file(f, args.layer) for f in args.tl_files])

    elif mode == 'stable':
        print(f'Parsing TL  : {TL_STABLE}')
        all_objects = _dedup([_load_file(TL_STABLE, args.layer)])

    elif mode == 'beta':
        print(f'Parsing TL  : {TL_BETA}')
        all_objects = _dedup([_load_file(TL_BETA, args.layer)])

    elif mode == 'experimental':
        print(f'Parsing TL  : {TL_STABLE}  +  {TL_BETA}')
        raw_stable = _load_file(TL_STABLE, args.layer)
        raw_beta   = _load_file(TL_BETA,   args.layer)

        # Dedup primary (stable) first
        seen_ids, seen_names = set(), set()
        primary = []
        for obj in raw_stable:
            if obj.id not in seen_ids and obj.fullname not in seen_names:
                primary.append(obj)
                seen_ids.add(obj.id)
                seen_names.add(obj.fullname)

        print(f'   Primary (stable): {len(primary)} unique objects')
        print(f'   Merging secondary (beta)...')
        all_objects = _merge_experimental(primary, raw_beta)

    print(f'\nTotal: {len(all_objects)} unique TL objects')
    print(f'Output      : {args.out}/tl/')

    # ── Wipe old generated files ───────────────────────────────────────────
    _wipe_generated(args.out)

    # ── Generate ───────────────────────────────────────────────────────────
    from tglib_generator.generator import generate_tl_modules
    generate_tl_modules(all_objects, out_dir=args.out, depth=2)

    # ── Validate ───────────────────────────────────────────────────────────
    _ast_validate(args.out)

    print('\nDone! You can now use tglib with the generated types.')
    print('   Example:')
    print('       from tglib.tl.functions import SomeFunctionRequest')
    print('       from tglib.tl.types import SomeType')


if __name__ == '__main__':
    main()
