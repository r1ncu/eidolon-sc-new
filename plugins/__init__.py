"""Plugins: everything that could be switched off without the character going quiet.

Each one owns its tables, writes its own prompt parts, and knows nothing about the others
— including `quota`, which is the smallest of them and the one to read first.
"""
