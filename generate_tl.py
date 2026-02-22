#!/usr/bin/env python3
"""
tglib TL Code Generator
=======================
Generates Python source files from Telegram TL schema files.

Usage:
    python generate_tl.py [options]

Options:
    --source SOURCE     Which TL file(s) to use:
                          main  -> tl_files/main_api.tl only  [DEFAULT]
                          api   -> tl_files/api.tl only
                          both  -> api.tl first, then main_api.tl merged in
    --tl FILE           Override: explicit TL schema file path (can repeat)
    --out DIR           Output directory (tglib package root), default: ./tglib
    --layer N           TL layer number (auto-detected from file if omitted)
    --help              Show this help

Source differences:
    main  - 2317 types. Has all legacy types. Recommended default.
    api   - 2291 types. Has payments.craftStarGift / payments.getCraftStarGifts
            (vs messages.* equivalents in main). Missing 25 legacy types.
    both  - Union of both files. api.tl wins on conflicts (its CRCs are
            authoritative). Dedup by both ID and name prevents duplicate classes.

Examples:
    python generate_tl.py                        # uses main_api.tl (default)
    python generate_tl.py --source api           # uses api.tl only
    python generate_tl.py --source both          # merges both files
    python generate_tl.py --tl path/to/custom.tl
    python generate_tl.py --out /path/to/your/tglib
"""
import argparse
import sys
import os

# Ensure we can import tglib_generator from this directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tglib_generator.parser import parse_tl, find_layer
from tglib_generator.generator import generate_tl_modules

# Predefined source sets.
# In "both" mode, api.tl is processed FIRST so its CRCs win over main_api.tl
# for the 5 types where main_api has stale (wrong) constructor IDs.
SOURCE_MAP = {
    'main': ['tl_files/main_api.tl'],
    'api':  ['tl_files/api.tl'],
    'both': ['tl_files/api.tl', 'tl_files/main_api.tl'],
}
DEFAULT_SOURCE = 'main'
DEFAULT_OUT = 'tglib'


def main():
    parser = argparse.ArgumentParser(
        description='Generate tglib TL Python modules from .tl schema files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        '--source', default=DEFAULT_SOURCE, choices=list(SOURCE_MAP.keys()),
        help=(
            'Which built-in TL file(s) to use: '
            'main (default, main_api.tl), api (api.tl only), both (merged)'
        )
    )
    parser.add_argument(
        '--tl', dest='tl_files', action='append', metavar='FILE',
        help='Override: explicit TL schema file path (can repeat for multiple files)'
    )
    parser.add_argument(
        '--out', default=DEFAULT_OUT, metavar='DIR',
        help=f'Output directory (default: {DEFAULT_OUT})'
    )
    parser.add_argument(
        '--layer', type=int, default=None,
        help='TL layer number (auto-detected from file if omitted)'
    )

    args = parser.parse_args()

    # --tl overrides --source entirely
    if args.tl_files:
        tl_files = args.tl_files
        source_label = 'custom'
    else:
        tl_files = SOURCE_MAP[args.source]
        source_label = args.source

    # Validate TL files exist
    missing = [f for f in tl_files if not os.path.exists(f)]
    if missing:
        print(f'[ERR] TL file(s) not found: {", ".join(missing)}', file=sys.stderr)
        print('      Use --tl path/to/file.tl or --source [main|api|both].', file=sys.stderr)
        sys.exit(1)

    # Validate output dir
    if not os.path.isdir(args.out):
        print(f'[ERR] Output directory not found: {args.out}', file=sys.stderr)
        print('      Make sure tglib package directory exists.', file=sys.stderr)
        sys.exit(1)

    print(f'Source mode : {source_label}')
    print(f'Parsing TL  : {", ".join(tl_files)}')

    all_objects = []

    # Dedup by BOTH id AND fullname to prevent duplicate class names.
    #
    # Bug in original: dedup only checked obj.id.
    # Problem: inputKeyboardButtonRequestPeer has DIFFERENT IDs in api.tl vs
    # main_api.tl (genuine version split — api.tl added a 'style' field and
    # recomputed the CRC). Because both IDs are unique, both objects passed
    # the original id-only check and BOTH got added to all_objects.
    # Result: duplicate class "InputKeyboardButtonRequestPeer" in _root.py
    # and alltlobjects.py pointing to two different constructor IDs.
    #
    # Fix: also track fullname. First file processed wins (api.tl in 'both'
    # mode, since its args+CRC are authoritative for conflicting types).
    seen_ids   = set()
    seen_names = set()

    for tl_file in tl_files:
        layer = args.layer or find_layer(tl_file)
        print(f'   {tl_file}  (layer {layer or "unknown"})')
        objects = parse_tl(tl_file, layer=layer)

        added = skipped = 0
        for obj in objects:
            if obj.id not in seen_ids and obj.fullname not in seen_names:
                all_objects.append(obj)
                seen_ids.add(obj.id)
                seen_names.add(obj.fullname)
                added += 1
            else:
                skipped += 1

        print(f'   -> {added} objects added, {skipped} duplicates skipped')

    print(f'\nTotal: {len(all_objects)} unique TL objects')
    print(f'Generating Python code into: {args.out}/tl/\n')

    generate_tl_modules(all_objects, out_dir=args.out, depth=2)

    print('\nDone! You can now use tglib with the generated types.')
    print('   Example:')
    print('       from tglib.tl.functions import SomeFunctionRequest')
    print('       from tglib.tl.types import SomeType')


if __name__ == '__main__':
    main()
