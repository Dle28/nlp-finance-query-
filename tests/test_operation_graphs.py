from __future__ import annotations

import pytest

from finance_query.operation_graphs import GRAPH_PROTOCOL, validate_completed_graph


def _route() -> dict:
    return {
        "question_id": 1,
        "required_operations": ["reported_value", "subtract_or_difference", "stage_output_dependency"],
        "stages": [{"stage_id": "left"}, {"stage_id": "right"}],
    }


def test_completed_difference_graph_is_typed_but_not_authorized() -> None:
    from finance_query.operation_graphs import canonical_sha256

    route = _route()
    graph = {
        "schema_version": 1,
        "protocol": GRAPH_PROTOCOL,
        "source_route_sha256": canonical_sha256(route),
        "nodes": [
            {"node_id": "a", "op": "stage_ref", "stage_id": "left", "inputs": []},
            {"node_id": "b", "op": "stage_ref", "stage_id": "right", "inputs": []},
            {"node_id": "answer", "op": "subtract", "inputs": ["a", "b"]},
        ],
        "final_node_id": "answer",
    }
    result = validate_completed_graph(graph, route)
    assert result["status"] == "validated_not_authorized"
    assert result["source_contract"]["may_execute_formula"] is False


def test_graph_rejects_unknown_or_forward_references() -> None:
    from finance_query.operation_graphs import canonical_sha256

    route = _route()
    graph = {
        "schema_version": 1,
        "protocol": GRAPH_PROTOCOL,
        "source_route_sha256": canonical_sha256(route),
        "nodes": [{"node_id": "answer", "op": "subtract", "inputs": ["a", "b"]}],
        "final_node_id": "answer",
    }
    with pytest.raises(ValueError, match="topologically valid"):
        validate_completed_graph(graph, route)
