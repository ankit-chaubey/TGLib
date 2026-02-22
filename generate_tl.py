#!/usr/bin/env python3
"""
tglib TL Code Generator
=======================
Generates Python source files from Telegram TL schema files.

Usage:
    python generate_tl.py [options]

Options:
    --tl FILE           TL schema file(s) to use (can specify multiple times)
    --out DIR           Output directory (tglib package root), default: ./tglib
    --layer N           TL layer number (auto-detected from file if omitted)
    --help              Show this help

Examples:
    # Use default TL files shipped with tglib
    python generate_tl.py

    # Use your own TL file
    python generate_tl.py --tl path/to/schema.tl

    # Use two TL files (both will be merged)
    python generate_tl.py --tl tl_files/api.tl --tl tl_files/main_api.tl

    # Specify a custom output directory
    python generate_tl.py --out /path/to/your/tglib
"""
import argparse
import sys
import os

# Ensure we can import tglib_generator from this directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tglib_generator.parser import parse_tl, find_layer
from tglib_generator.generator import generate_tl_modules

DEFAULT_TL_FILES = [
    'tl_files/api.tl',
    'tl_files/main_api.tl',
]
DEFAULT_OUT = 'tglib'


def main():
    parser = argparse.ArgumentParser(
        description='Generate tglib TL Python modules from .tl schema files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        '--tl', dest='tl_files', action='append', metavar='FILE',
        help='TL schema file (can repeat for multiple files)'
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

    tl_files = args.tl_files or DEFAULT_TL_FILES

    # Validate TL files exist
    missing = [f for f in tl_files if not os.path.exists(f)]
    if missing:
        print(f'❌ TL file(s) not found: {", ".join(missing)}', file=sys.stderr)
        print('   Use --tl path/to/file.tl to specify a TL file.', file=sys.stderr)
        sys.exit(1)

    # Validate output dir
    if not os.path.isdir(args.out):
        print(f'❌ Output directory not found: {args.out}', file=sys.stderr)
        print('   Make sure tglib package directory exists.', file=sys.stderr)
        sys.exit(1)

    print(f'🔍 Parsing TL files: {", ".join(tl_files)}')

    all_objects = []
    seen_ids = set()

    for tl_file in tl_files:
        layer = args.layer or find_layer(tl_file)
        print(f'   {tl_file}  (layer {layer or "unknown"})')
        objects = parse_tl(tl_file, layer=layer)

        added = 0
        skipped = 0
        for obj in objects:
            if obj.id not in seen_ids:
                all_objects.append(obj)
                seen_ids.add(obj.id)
                added += 1
            else:
                skipped += 1

        print(f'   → {added} objects parsed, {skipped} duplicates skipped')

    print(f'\n📦 Total: {len(all_objects)} unique TL objects')
    print(f'🛠  Generating Python code into: {args.out}/tl/\n')

    generate_tl_modules(all_objects, out_dir=args.out, depth=2)

    print('\n✅ Done! You can now use tglib with the generated types.')
    print('   Example:')
    print('       from tglib.tl.functions import SomeFunctionRequest')
    print('       from tglib.tl.types import SomeType')


if __name__ == '__main__':
    main()
