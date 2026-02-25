#!/usr/bin/env python3
"""
patch_types.py  —  Run this AFTER generate_tl.py to add Type* aliases
                   to tglib/tl/types/__init__.py

Usage:
    python generate_tl.py
    python patch_types.py

What it does:
    Scans all generated type classes, groups them by SUBCLASS_OF_ID,
    and appends  TypeFoo = Union[Bar, Baz, ...]  to the types __init__.
    This mirrors how Telethon's own generator works.
"""
import sys
import os
import ast
import importlib
import inspect
from collections import defaultdict
from typing import Union

# ── Paths ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
TYPES_DIR    = os.path.join(SCRIPT_DIR, 'tglib', 'tl', 'types')
INIT_FILE    = os.path.join(TYPES_DIR, '__init__.py')
ROOT_FILE    = os.path.join(TYPES_DIR, '_root.py')

# ── Step 1: Parse _root.py with AST to extract (ClassName, SUBCLASS_OF_ID) ───
print(f"Scanning  : {ROOT_FILE}")

with open(ROOT_FILE, 'r', encoding='utf-8') as f:
    source = f.read()

tree = ast.parse(source)

# class_name → subclass_of_id (hex int)
class_subclass: dict[str, int] = {}

for node in ast.walk(tree):
    if not isinstance(node, ast.ClassDef):
        continue
    for item in node.body:
        if (isinstance(item, ast.Assign)
                and len(item.targets) == 1
                and isinstance(item.targets[0], ast.Name)
                and item.targets[0].id == 'SUBCLASS_OF_ID'):
            val = item.value
            if isinstance(val, ast.Constant):
                class_subclass[node.name] = val.value
            elif isinstance(val, ast.UnaryOp) and isinstance(val.op, ast.USub):
                # negative hex — unlikely but handle
                class_subclass[node.name] = -val.operand.value

# Also scan namespace files for additional classes
for fname in os.listdir(TYPES_DIR):
    if fname.startswith('_') or not fname.endswith('.py'):
        continue
    fpath = os.path.join(TYPES_DIR, fname)
    with open(fpath, 'r', encoding='utf-8') as f:
        ns_source = f.read()
    try:
        ns_tree = ast.parse(ns_source)
    except SyntaxError:
        continue
    for node in ast.walk(ns_tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if (isinstance(item, ast.Assign)
                    and len(item.targets) == 1
                    and isinstance(item.targets[0], ast.Name)
                    and item.targets[0].id == 'SUBCLASS_OF_ID'):
                val = item.value
                if isinstance(val, ast.Constant):
                    class_subclass[node.name] = val.value

print(f"Found     : {len(class_subclass)} classes with SUBCLASS_OF_ID")

# ── Step 2: Group classes by SUBCLASS_OF_ID ────────────────────────────────────
subclass_groups: dict[int, list[str]] = defaultdict(list)
for cls_name, sub_id in class_subclass.items():
    subclass_groups[sub_id].append(cls_name)

# ── Step 3: Determine the abstract type name for each SUBCLASS_OF_ID ──────────
# Strategy: The SUBCLASS_OF_ID of class X is the CRC32 of the abstract type
# name that X belongs to.  We can derive the type name from the class names
# themselves: if multiple classes share a SUBCLASS_OF_ID, strip common prefix.
# But actually the most reliable way is to look at which class in the group has
# CONSTRUCTOR_ID == SUBCLASS_OF_ID (i.e. it IS the abstract type).  That is
# rare.  Better: use the class name prefix pattern.
#
# Simplest reliable approach: for each group, find the longest common prefix
# among class names — that IS the type name.

def longest_common_prefix(names: list[str]) -> str:
    if not names:
        return ''
    if len(names) == 1:
        # single-constructor type — type name = class name itself
        return names[0]
    prefix = []
    for chars in zip(*names):
        if len(set(chars)) == 1:
            prefix.append(chars[0])
        else:
            break
    return ''.join(prefix)

# Build type_name → [class_names]
type_aliases: dict[str, list[str]] = {}
for sub_id, classes in subclass_groups.items():
    classes.sort()
    prefix = longest_common_prefix(classes)
    # Trim to last whole CamelCase word boundary
    # e.g. "MessageEntity" from ["MessageEntityBold","MessageEntityItalic"]
    if not prefix:
        prefix = classes[0]  # fallback
    # ensure prefix ends on a capital-letter boundary
    while prefix and not prefix[-1].isupper() and len(prefix) > 1:
        prefix = prefix[:-1]
    # strip trailing capitals that are part of the variant suffix
    # e.g. avoid "MessageEntityBol" — keep "MessageEntity"
    import re
    m = re.match(r'^((?:[A-Z][a-z0-9]*)+)', prefix)
    type_name = m.group(1) if m else prefix
    if type_name not in type_aliases:
        type_aliases[type_name] = classes
    else:
        type_aliases[type_name].extend(classes)

# ── Step 4: Read existing __init__.py ─────────────────────────────────────────
with open(INIT_FILE, 'r', encoding='utf-8') as f:
    init_content = f.read()

# Remove any existing Type* alias block (so we don't double-add)
marker = '\n# --- AUTO-GENERATED Type aliases (patch_types.py) ---\n'
if marker in init_content:
    init_content = init_content[:init_content.index(marker)]

# ── Step 5: Collect all class names already imported in __init__.py ───────────
imported: set[str] = set()
for line in init_content.splitlines():
    # grab names from "from .xxx import A, B, C"
    if line.startswith('from .'):
        parts = line.split('import', 1)
        if len(parts) == 2:
            names = [n.strip() for n in parts[1].split(',')]
            imported.update(names)

# ── Step 6: Build alias lines ─────────────────────────────────────────────────
alias_lines = [marker, 'from typing import Union\n']

skipped = 0
added   = 0
for type_name, classes in sorted(type_aliases.items()):
    # only include classes that are actually imported in __init__
    available = [c for c in classes if c in imported]
    if not available:
        skipped += 1
        continue
    type_alias_name = f'Type{type_name}'
    # skip if already defined
    if f'{type_alias_name} =' in init_content:
        continue
    if len(available) == 1:
        alias_lines.append(f'{type_alias_name} = {available[0]}\n')
    else:
        members = ', '.join(available)
        alias_lines.append(f'{type_alias_name} = Union[{members}]\n')
    added += 1

# ── Step 7: Write back ────────────────────────────────────────────────────────
with open(INIT_FILE, 'w', encoding='utf-8') as f:
    f.write(init_content)
    f.write(''.join(alias_lines))

print(f"Added     : {added} Type* aliases  (skipped {skipped} with no imported classes)")
print(f"Updated   : {INIT_FILE}")
print("Done! You can now run your bot/script.")
