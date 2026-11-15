"""Stable source descriptions for the Python 3.9, 3.12 and 3.14 guards."""

import ast


def describe_source_ast(value):
    """Describe source semantics without interpreter-specific empty AST fields.

    The format keeps 3.9's empty lists. Optional None fields are omitted; a
    Constant's value is required even when the literal is None. Empty generic
    parameter fields added by later interpreters do not change source semantics.
    """
    if isinstance(value, ast.AST):
        fields = []
        for name, member in ast.iter_fields(value):
            if name == 'type_params' and not member:
                continue
            if member is None and not (isinstance(value, ast.Constant) and name == 'value'):
                continue
            fields.append(name + '=' + describe_source_ast(member))
        return type(value).__name__ + '(' + ', '.join(fields) + ')'
    if isinstance(value, list):
        return '[' + ', '.join(describe_source_ast(member) for member in value) + ']'
    return repr(value)
