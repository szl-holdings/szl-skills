"""Repository test package.

The modules here share fixtures through `tests.*` imports; this marker keeps the
repo package ahead of any unrelated site-packages `tests` distribution on
sys.path during unittest discovery.
"""
