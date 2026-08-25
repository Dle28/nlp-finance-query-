"""LLM research sidecars: diagnostic, calibration and review assistance only.

The package deliberately has no eager imports.  Proof-policy validation uses a
small JSON parser from this folder, while the optional diagnostic lane imports
proof-policy contracts.  Keeping these imports explicit prevents a model
runtime helper from creating a circular operational dependency.
"""

__all__: list[str] = []
