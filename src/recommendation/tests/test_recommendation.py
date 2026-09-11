# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0
import logging
from unittest.mock import Mock

import pytest
from opentelemetry import trace

import demo_pb2
import recommendation_server as server


@pytest.fixture
def catalog(monkeypatch):
    """Use real protobufs and service logic, with no gRPC/flagd/OTLP servers."""
    stub = Mock()
    stub.ListProducts.return_value = demo_pb2.ListProductsResponse(
        products=[demo_pb2.Product(id=f"p{i}") for i in range(8)]
    )
    monkeypatch.setattr(server, "product_catalog_stub", stub, raising=False)
    monkeypatch.setattr(server, "tracer", trace.get_tracer(__name__), raising=False)
    monkeypatch.setattr(server, "logger", logging.getLogger(__name__), raising=False)
    monkeypatch.setattr(server, "check_feature_flag", lambda name: False)
    monkeypatch.setattr(server, "cached_ids", [])
    monkeypatch.setattr(server, "first_run", True)
    return stub


@pytest.mark.parametrize("requested", [["p0", "p1"], ["p0,p1"], ["p0,p1", "p2"]])
def test_excludes_requested_products(catalog, requested):
    # Fewer than five candidates makes exclusions deterministic even though
    # the service samples randomly: every eligible product must be returned.
    available = {f"p{i}" for i in range(4)}
    catalog.ListProducts.return_value = demo_pb2.ListProductsResponse(
        products=[demo_pb2.Product(id=product) for product in available]
    )
    result = server.get_product_list(requested)
    excluded = {product for group in requested for product in group.split(",")}
    assert set(result) == available - excluded
    assert len(result) == len(available - excluded)
    catalog.ListProducts.assert_called_once_with(demo_pb2.Empty())


def test_caps_recommendations_at_five_unique_catalog_products(catalog):
    result = server.get_product_list([])
    assert len(result) == 5
    assert len(set(result)) == 5
    assert set(result) <= {f"p{i}" for i in range(8)}


@pytest.mark.parametrize("products,requested,expected", [
    ([], [], set()),
    (["p0", "p1"], ["p0", "p1"], set()),
    (["p0", "p1", "p1", "p2"], ["p0"], {"p1", "p2"}),
])
def test_empty_small_and_duplicate_catalogs(catalog, products, requested, expected):
    catalog.ListProducts.return_value = demo_pb2.ListProductsResponse(
        products=[demo_pb2.Product(id=product) for product in products]
    )
    result = server.get_product_list(requested)
    assert set(result) == expected
    assert len(result) == len(expected)


def test_catalog_errors_propagate(catalog):
    catalog.ListProducts.side_effect = RuntimeError("catalog unavailable")
    with pytest.raises(RuntimeError, match="catalog unavailable"):
        server.get_product_list([])


def test_cache_failure_flag_reuses_cached_ids_on_a_hit(catalog, monkeypatch):
    monkeypatch.setattr(server, "check_feature_flag", lambda name: True)
    monkeypatch.setattr(server.random, "random", lambda: 0.9)
    assert len(server.get_product_list([])) == 5  # first_run forces a miss
    catalog.ListProducts.assert_called_once()
    catalog.ListProducts.side_effect = AssertionError("cache hit must not query catalog")
    assert len(server.get_product_list([])) == 5
    assert server.first_run is False


def test_handler_returns_protobuf_and_counts_recommendations(catalog, monkeypatch):
    counter = Mock()
    monkeypatch.setattr(server, "rec_svc_metrics", {"demo.recommendation.requests": counter}, raising=False)
    request = demo_pb2.ListRecommendationsRequest(product_ids=["p0", "p1"])
    result = server.RecommendationService().ListRecommendations(request, None)
    assert isinstance(result, demo_pb2.ListRecommendationsResponse)
    assert len(result.product_ids) == 5
    assert not {"p0", "p1"} & set(result.product_ids)
    counter.add.assert_called_once_with(5, {"recommendation.type": "catalog"})


def test_required_environment_value(monkeypatch):
    monkeypatch.setenv("RECOMMENDATION_PORT", "9001")
    assert server.must_map_env("RECOMMENDATION_PORT") == "9001"


def test_missing_environment_value(monkeypatch):
    monkeypatch.delenv("RECOMMENDATION_PORT", raising=False)
    with pytest.raises(Exception, match="RECOMMENDATION_PORT environment variable must be set"):
        server.must_map_env("RECOMMENDATION_PORT")


def test_feature_flag_defaults_to_false(monkeypatch):
    client = Mock()
    client.get_boolean_value.return_value = False
    monkeypatch.setattr(server.api, "get_client", lambda: client)
    assert server.check_feature_flag("recommendationCacheFailure") is False
    client.get_boolean_value.assert_called_once_with("recommendationCacheFailure", False)
