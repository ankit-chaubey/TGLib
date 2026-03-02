#!/usr/bin/env python3
"""
upgrade_layer.py  —  TGLib Layer Upgrade Tool
==============================================
Upgrades TGLib to a new Telegram TL layer in ONE command.

What it does (in order):
  1. Downloads the new TL schema from Telegram's GitHub (or uses --tl file)
  2. Runs the fixed generator  → regenerates all tl/types/ and tl/functions/
  3. Runs patch_types.py       → rebuilds Type* Union aliases
  4. Bumps TL_LAYER constant   in tglib/client.py
  5. Bumps __version__         in tglib/__init__.py  (optional)
  6. AST-validates every generated file
  7. Prints a diff summary of what changed

Usage:
  python upgrade_layer.py                     # auto-download latest TL from TG
  python upgrade_layer.py --layer 224         # force specific layer number
  python upgrade_layer.py --tl my_api.tl      # use local TL file (no download)
  python upgrade_layer.py --dry-run           # show what would change, don't write
  python upgrade_layer.py --no-download       # skip download, use existing tl_files/

Requirements:
  Run from inside TGLib-main/ (same dir as generate_tl.py)
"""

import argparse
import ast
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

# ── Paths (all relative to script location = TGLib-main/) ────────────────────
ROOT      = Path(__file__).parent.resolve()
TL_DIR    = ROOT / 'tl_files'
TGLIB_DIR = ROOT / 'tglib'
CLIENT_PY = TGLIB_DIR / 'client.py'
INIT_PY   = TGLIB_DIR / '__init__.py'
GEN_SCRIPT   = ROOT / 'generate_tl.py'
PATCH_SCRIPT = ROOT / 'patch_types.py'

# Telegram's official TL schema URLs (raw GitHub)
TL_URLS = {
    'api':  'https://raw.githubusercontent.com/telegramdesktop/tdesktop/refs/heads/dev/Telegram/SourceFiles/mtproto/scheme/api.tl',
    'main': 'https://raw.githubusercontent.com/TGScheme/Schema/refs/heads/main/main_api.tl',
}


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _banner(msg: str):
    print(f'\n{"─" * 60}')
    print(f'  {msg}')
    print(f'{"─" * 60}')


def _run(cmd: list, cwd=None, check=True) -> subprocess.CompletedProcess:
    print(f'  $ {" ".join(str(c) for c in cmd)}')
    r = subprocess.run(cmd, cwd=cwd or ROOT, capture_output=False, text=True)
    if check and r.returncode != 0:
        print(f'\n❌  Command failed (exit {r.returncode})')
        sys.exit(r.returncode)
    return r


def _read(path: Path) -> str:
    return path.read_text(encoding='utf-8')


def _write(path: Path, content: str):
    path.write_text(content, encoding='utf-8')


def _detect_current_layer() -> int:
    """Read TL_LAYER from client.py."""
    src = _read(CLIENT_PY)
    m = re.search(r'^TL_LAYER\s*=\s*(\d+)', src, re.MULTILINE)
    return int(m.group(1)) if m else 0


def _detect_tl_layer(tl_file: Path) -> int:
    """Read // LAYER N from a .tl file."""
    m = re.search(r'^//\s*LAYER\s*(\d+)', _read(tl_file), re.MULTILINE)
    return int(m.group(1)) if m else 0


def _detect_current_version() -> str:
    src = _read(INIT_PY)
    m = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", src)
    return m.group(1) if m else '0.0.0'


def _bump_version(old: str, layer: int) -> str:
    """Bump the patch component and embed the layer: e.g. 0.1.4 → 0.2.224"""
    parts = old.split('.')
    major = parts[0] if parts else '0'
    minor = str(int(parts[1]) + 1) if len(parts) > 1 else '1'
    return f'{major}.{minor}.{layer}'


def _ast_validate_dir(directory: Path) -> list:
    """Return list of (filepath, SyntaxError) for any broken .py files."""
    errors = []
    for pyfile in sorted(directory.glob('*.py')):
        try:
            ast.parse(pyfile.read_text(encoding='utf-8'))
        except SyntaxError as e:
            errors.append((pyfile, e))
    return errors


def _count_classes(directory: Path) -> int:
    total = 0
    for pyfile in directory.glob('*.py'):
        src = pyfile.read_text(encoding='utf-8')
        total += src.count('\nclass ')
    return total


# ══════════════════════════════════════════════════════════════════════════════
# Step 1 — Download
# ══════════════════════════════════════════════════════════════════════════════

def download_tl(dry_run: bool) -> dict:
    """Download both TL files from Telegram's repo. Returns {name: path}."""
    _banner('Step 1 — Downloading TL schema files')
    TL_DIR.mkdir(exist_ok=True)
    downloaded = {}

    for name, url in TL_URLS.items():
        dest = TL_DIR / f'{name}.tl' if name != 'api' else TL_DIR / 'api.tl'
        dest = TL_DIR / ('main_api.tl' if name == 'main' else 'api.tl')
        print(f'  ↓  {url}')
        print(f'     → {dest}')
        if not dry_run:
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'TGLib-Upgrader/1.0'})
                with urllib.request.urlopen(req, timeout=30) as r:
                    content = r.read()
                dest.write_bytes(content)
                layer = _detect_tl_layer(dest)
                print(f'     ✅ {len(content):,} bytes  (LAYER {layer})')
                downloaded[name] = dest
            except Exception as e:
                print(f'     ❌ Download failed: {e}')
                print('        Use --no-download to skip and use existing tl_files/')
                sys.exit(1)
        else:
            print('     (dry-run — skipped)')

    return downloaded


# ══════════════════════════════════════════════════════════════════════════════
# Step 2 — Generate
# ══════════════════════════════════════════════════════════════════════════════

def regenerate(tl_file, dry_run: bool, mode: str = 'experimental'):
    """Run generate_tl.py then patch_types.py."""
    _banner('Step 2 — Regenerating TL Python modules')

    # Snapshot class counts before
    types_dir = TGLIB_DIR / 'tl' / 'types'
    funcs_dir = TGLIB_DIR / 'tl' / 'functions'
    before_types = _count_classes(types_dir)
    before_funcs = _count_classes(funcs_dir)

    if dry_run:
        print('  (dry-run — skipping generation)')
        return

    if not GEN_SCRIPT.exists():
        print(f'❌  generate_tl.py not found at {GEN_SCRIPT}')
        sys.exit(1)

    # --tl overrides mode entirely (single custom file)
    if tl_file:
        cmd = [sys.executable, str(GEN_SCRIPT), '--tl', str(tl_file)]
    else:
        cmd = [sys.executable, str(GEN_SCRIPT), '--mode', mode]

    _run(cmd)

    # Run patch_types.py
    if PATCH_SCRIPT.exists():
        _banner('Step 3 — Rebuilding Type* Union aliases')
        _run([sys.executable, str(PATCH_SCRIPT)])
    else:
        print('  ⚠️  patch_types.py not found — skipping alias rebuild')

    # Report delta
    after_types = _count_classes(types_dir)
    after_funcs = _count_classes(funcs_dir)
    dt = after_types - before_types
    df = after_funcs - before_funcs
    sign = lambda n: f'+{n}' if n >= 0 else str(n)
    print(f'\n  Types:     {after_types} classes  ({sign(dt)} vs before)')
    print(f'  Functions: {after_funcs} classes  ({sign(df)} vs before)')


# ══════════════════════════════════════════════════════════════════════════════
# Step 3 — Validate
# ══════════════════════════════════════════════════════════════════════════════

def validate(dry_run: bool):
    """AST-parse every generated file and abort on syntax errors."""
    _banner('Step 4 — Validating generated files')

    if dry_run:
        print('  (dry-run — skipping validation)')
        return

    dirs_to_check = [
        TGLIB_DIR / 'tl' / 'types',
        TGLIB_DIR / 'tl' / 'functions',
    ]
    total   = 0
    all_err = []
    for d in dirs_to_check:
        errs = _ast_validate_dir(d)
        total += len(list(d.glob('*.py')))
        all_err.extend(errs)

    if all_err:
        print(f'\n❌  {len(all_err)} file(s) have syntax errors:\n')
        for path, err in all_err:
            print(f'   {path.name}: {err}')
        print('\n   Fix: update snake_to_camel() / _sanitize_identifier()')
        print('        in tglib_generator/parser.py, then re-run.')
        sys.exit(1)

    print(f'  ✅  {total} files checked — all valid')


# ══════════════════════════════════════════════════════════════════════════════
# Step 4 — Bump constants
# ══════════════════════════════════════════════════════════════════════════════

def bump_layer(new_layer: int, bump_ver: bool, dry_run: bool):
    """Update TL_LAYER in client.py and optionally __version__ in __init__.py."""
    _banner('Step 5 — Updating version constants')

    old_layer = _detect_current_layer()
    old_ver   = _detect_current_version()
    new_ver   = _bump_version(old_ver, new_layer) if bump_ver else old_ver

    print(f'  TL_LAYER  : {old_layer}  →  {new_layer}  (in client.py)')
    if bump_ver:
        print(f'  __version__: {old_ver}  →  {new_ver}  (in __init__.py)')
    else:
        print(f'  __version__: {old_ver}  (unchanged — use --bump-version to update)')

    if dry_run:
        print('  (dry-run — no files written)')
        return

    # Patch client.py
    src = _read(CLIENT_PY)
    new_src = re.sub(
        r'^(TL_LAYER\s*=\s*)\d+',
        lambda m: f'{m.group(1)}{new_layer}',
        src,
        flags=re.MULTILINE,
    )
    if new_src == src:
        print('  ⚠️  TL_LAYER not found in client.py — manual update required')
    else:
        _write(CLIENT_PY, new_src)
        print(f'  ✅  client.py updated')

    # Patch __init__.py
    if bump_ver:
        src = _read(INIT_PY)
        new_src = re.sub(
            r"(__version__\s*=\s*)['\"][^'\"]+['\"]",
            lambda m: f"{m.group(1)}'{new_ver}'",
            src,
        )
        if new_src == src:
            print('  ⚠️  __version__ not found in __init__.py')
        else:
            _write(INIT_PY, new_src)
            print(f'  ✅  __init__.py updated')


# ══════════════════════════════════════════════════════════════════════════════
# Final summary
# ══════════════════════════════════════════════════════════════════════════════

def print_summary(old_layer: int, new_layer: int, dry_run: bool):
    _banner('Done! ✅')
    if dry_run:
        print(f'  DRY RUN — no files were modified.')
        print(f'  Would upgrade: Layer {old_layer} → Layer {new_layer}')
    else:
        print(f'  Upgraded: Layer {old_layer} → Layer {new_layer}')
        print()
        print('  Next steps:')
        print('    1. Test your bot/userbot:')
        print('         python userbot.py')
        print('    2. If any new TL types are needed, import from:')
        print('         from tglib.tl.types import NewTypeName')
        print('         from tglib.tl.functions.namespace import NewRequest')
        print('    3. Commit:')
        print('         git add tglib/tl/ tglib/client.py tglib/__init__.py tl_files/')
        print(f'         git commit -m "chore: upgrade to TL Layer {new_layer}"')


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(
        description='Upgrade TGLib to a new Telegram TL layer',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument('--layer', type=int, default=None,
                   help='Force specific layer number (default: auto-detected from TL file)')
    p.add_argument('--tl', metavar='FILE', default=None,
                   help='Use a local TL file instead of downloading')
    p.add_argument('--no-download', action='store_true',
                   help='Skip download, use existing tl_files/ directory')
    p.add_argument('--dry-run', action='store_true',
                   help='Show what would happen without writing any files')
    p.add_argument('--bump-version', action='store_true',
                   help='Also bump __version__ in tglib/__init__.py')
    p.add_argument('--mode', choices=['stable', 'beta', 'experimental'], default='experimental',
                   help='Generation mode: stable (api.tl), beta (main_api.tl), experimental (smart union, default)')
    p.add_argument('--source', choices=['api', 'main', 'both'], default=None,
                   help='[Legacy alias] api→stable  main→beta  both→experimental')
    args = p.parse_args()

    # ── Validate we are in the right directory ─────────────────────────────
    if not GEN_SCRIPT.exists():
        print(f'❌  generate_tl.py not found.')
        print(f'    Run this script from inside TGLib-main/')
        sys.exit(1)

    old_layer = _detect_current_layer()
    print(f'\n🔧  TGLib Layer Upgrade Tool')
    print(f'    Current layer: {old_layer}')
    print(f'    Root: {ROOT}')
    if args.dry_run:
        print(f'    Mode: DRY RUN (nothing will be written)')

    # ── Step 1: Get TL file ────────────────────────────────────────────────
    tl_file_override = None

    if args.tl:
        tl_file_override = Path(args.tl)
        if not tl_file_override.exists():
            print(f'❌  TL file not found: {tl_file_override}')
            sys.exit(1)
        new_layer = args.layer or _detect_tl_layer(tl_file_override)
        print(f'    Using local TL file: {tl_file_override}  (LAYER {new_layer})')

    elif args.no_download:
        # Use what's already in tl_files/
        existing = list(TL_DIR.glob('*.tl'))
        if not existing:
            print(f'❌  No .tl files found in {TL_DIR}')
            print(f'    Remove --no-download or provide --tl <file>')
            sys.exit(1)
        # Detect layer from whichever file has it
        new_layer = args.layer or 0
        for f in existing:
            detected = _detect_tl_layer(f)
            if detected > new_layer:
                new_layer = detected
        print(f'    Using existing tl_files/  (LAYER {new_layer})')

    else:
        # Download
        download_tl(dry_run=args.dry_run)
        new_layer = args.layer or _detect_tl_layer(TL_DIR / 'api.tl')

    if not new_layer:
        new_layer = args.layer or old_layer
        print(f'  ⚠️  Could not detect layer from TL file — using {new_layer}')

    if new_layer == old_layer and not args.dry_run:
        print(f'\n  ℹ️  Layer {new_layer} is already current. Re-generating anyway.')
    elif new_layer < old_layer:
        print(f'\n  ⚠️  New layer ({new_layer}) < current ({old_layer}) — downgrade!')
        ans = input('    Continue? [y/N] ').strip().lower()
        if ans != 'y':
            sys.exit(0)

    # ── Steps 2+3: Generate + validate ────────────────────────────────────
    # Resolve --source legacy alias
    _legacy = {'api': 'stable', 'main': 'beta', 'both': 'experimental'}
    gen_mode = args.mode
    if args.source and not args.mode:
        gen_mode = _legacy.get(args.source, args.source)
    regenerate(tl_file_override, dry_run=args.dry_run, mode=gen_mode)
    validate(dry_run=args.dry_run)

    # ── Step 4: Bump constants ─────────────────────────────────────────────
    bump_layer(new_layer, bump_ver=args.bump_version, dry_run=args.dry_run)

    # ── Summary ────────────────────────────────────────────────────────────
    print_summary(old_layer, new_layer, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
