"""Pydantic v2 data contracts for the xyz_modules package.

These are the schemas the scoring + cost + export modules consume. The
receiving project can either use them as-is (they're plain Pydantic) or
swap to their own types — every module that takes a schema also accepts
a model_dump'd dict where convenient.
"""
