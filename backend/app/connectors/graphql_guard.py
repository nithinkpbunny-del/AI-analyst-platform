"""Read-only GraphQL (BUILD_SPEC 6.3, layer 2; 8.5): parse the document, refuse anything that is not a query.

A connector may send a GraphQL document only after ``validate_request`` accepted it, and the HTTP client checks the
request body again before it leaves (``app.connectors.http``). The rules:

* The document must parse, and hold exactly one operation.
* A **query** is allowed (introspection too). Fragments are allowed in queries.
* A **subscription** is refused.
* A **mutation** is refused, with one exception (DECISIONS D93): Shopify starts a bulk export with
  ``bulkOperationRunQuery``. It only reads (it creates a read job on Shopify's side and works with read-only
  scopes), and it is allowed only in this exact shape: one root field of that name, no alias or directives, the
  single argument ``query`` (a string literal or a variable), a selection of ``bulkOperation`` and ``userErrors``
  with their plain fields, and the inner query itself must pass the rules for queries. Any other mutation
  (``orderUpdate``, ``customerDelete``, ``bulkOperationCancel`` ...) raises ``ReadOnlyViolation``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from graphql import (
    DocumentNode,
    FieldNode,
    GraphQLSyntaxError,
    OperationDefinitionNode,
    OperationType,
    StringValueNode,
    VariableNode,
    parse,
)

from app.connectors.base import ReadOnlyViolation

BULK_MUTATION = "bulkOperationRunQuery"
MAX_DOCUMENT_CHARS = 50_000
# What the bulk mutation may select, and nothing else.
_BULK_SELECTION: dict[str, frozenset[str]] = {
    "bulkOperation": frozenset({"id", "status", "createdAt", "errorCode", "type"}),
    "userErrors": frozenset({"field", "message", "code"}),
}


def _parse(document: str) -> DocumentNode:
    if not isinstance(document, str) or len(document) > MAX_DOCUMENT_CHARS:
        raise ReadOnlyViolation("The GraphQL document is missing or too large.")
    try:
        return parse(document, no_location=True)
    except GraphQLSyntaxError as error:
        raise ReadOnlyViolation("The GraphQL document could not be parsed.") from error


def _operations(tree: DocumentNode) -> list[OperationDefinitionNode]:
    return [d for d in tree.definitions if isinstance(d, OperationDefinitionNode)]


def validate_query(document: str) -> None:
    """Accept a document with exactly one query; raise ``ReadOnlyViolation`` for anything else."""
    tree = _parse(document)
    operations = _operations(tree)
    if len(operations) != 1:
        raise ReadOnlyViolation("A GraphQL request must hold exactly one operation.")
    if operations[0].operation is not OperationType.QUERY:
        raise ReadOnlyViolation(f"A GraphQL {operations[0].operation.value} is not a read.")


def _bulk_query_text(operation: OperationDefinitionNode, variables: Mapping[str, Any]) -> str:
    """The inner query of an allowed bulk mutation. Raises ``ReadOnlyViolation`` unless the shape is exact."""
    if operation.directives:
        raise ReadOnlyViolation("The bulk mutation may not use directives.")
    selections = operation.selection_set.selections
    if len(selections) != 1:
        raise ReadOnlyViolation("Only one bulk operation may be started per request.")
    field = selections[0]
    if not isinstance(field, FieldNode) or field.name.value != BULK_MUTATION:
        raise ReadOnlyViolation("A GraphQL mutation is not a read.")
    if field.alias is not None or field.directives:
        raise ReadOnlyViolation("The bulk mutation may not use an alias or directives.")
    arguments = list(field.arguments or ())
    if len(arguments) != 1 or arguments[0].name.value != "query":
        raise ReadOnlyViolation("The bulk mutation takes only the argument 'query'.")
    value = arguments[0].value
    if isinstance(value, StringValueNode):
        inner = value.value
    elif isinstance(value, VariableNode):
        inner = variables.get(value.name.value)  # type: ignore[assignment]
    else:
        raise ReadOnlyViolation("The bulk query must be a string.")
    if not isinstance(inner, str):
        raise ReadOnlyViolation("The bulk query must be a string.")
    validate_query(inner)  # the export itself must be a plain read
    if field.selection_set is None:
        raise ReadOnlyViolation("The bulk mutation must select its result fields.")
    for top in field.selection_set.selections:
        if not isinstance(top, FieldNode) or top.name.value not in _BULK_SELECTION:
            raise ReadOnlyViolation("The bulk mutation selects something other than its result.")
        if top.alias is not None or top.directives or top.selection_set is None:
            raise ReadOnlyViolation("The bulk mutation selects something other than its result.")
        for leaf in top.selection_set.selections:
            if (
                not isinstance(leaf, FieldNode)
                or leaf.name.value not in _BULK_SELECTION[top.name.value]
                or leaf.selection_set is not None
                or leaf.alias is not None
                or leaf.directives
            ):
                raise ReadOnlyViolation(
                    "The bulk mutation selects something other than its result."
                )
    return inner


def validate_request(document: str, variables: Mapping[str, Any] | None = None) -> None:
    """Accept a query, or the one allowed bulk mutation in its exact shape. Anything else is refused."""
    tree = _parse(document)
    operations = _operations(tree)
    if len(operations) != 1:
        raise ReadOnlyViolation("A GraphQL request must hold exactly one operation.")
    operation = operations[0]
    if operation.operation is OperationType.QUERY:
        return
    if operation.operation is OperationType.MUTATION:
        if any(not isinstance(d, OperationDefinitionNode) for d in tree.definitions):
            raise ReadOnlyViolation("The bulk mutation may not use fragments.")
        _bulk_query_text(operation, variables or {})
        return
    raise ReadOnlyViolation(f"A GraphQL {operation.operation.value} is not a read.")


def validate_body(body: bytes) -> None:
    """Check the JSON body of a request to a GraphQL endpoint (the second check, in the HTTP client)."""
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError) as error:
        raise ReadOnlyViolation("The GraphQL request body is not JSON.") from error
    if not isinstance(payload, dict):
        raise ReadOnlyViolation("The GraphQL request body is not an object.")
    variables = payload.get("variables")
    if variables is not None and not isinstance(variables, dict):
        raise ReadOnlyViolation("The GraphQL variables are not an object.")
    validate_request(payload.get("query", ""), variables)
