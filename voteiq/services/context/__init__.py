"""voteiq.services.context — modular context-building sub-package.

Extracted from the database_context monolith in order of safety:
  _parsing.py  — pure query-parsing helpers (no DB, no I/O)
  _budget.py   — PriorityBlockList budget allocator (no DB, no I/O)

The main entry points (build_database_context, build_admin_database_context)
remain in voteiq.services.database_context for backward compatibility.
"""
