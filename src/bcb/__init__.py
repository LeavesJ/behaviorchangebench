"""BehaviorChangeBench pipeline: spec -> conversation -> replies -> grades -> tables.

Run with `PYTHONPATH=src python -m bcb --help`. Each step reads files and writes files; nothing
is kept in memory between steps, so any step can be rerun on saved inputs.
"""

__version__ = "0.1.0"
