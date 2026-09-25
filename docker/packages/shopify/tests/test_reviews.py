"""Tests for product review tools."""

import json

import pytest
from pydantic import ValidationError

from shopify import state as shopify_state
from shopify.models import (
    CreateReviewArgs,
    DeleteReviewArgs,
    GetProductReviewsArgs,
    UpdateReviewArgs,
)
from shopify.tools.reviews_returns import (
    handle_create_review,
    handle_delete_review,
    handle_get_product_reviews,
    handle_update_review,
)


@pytest.fixture
def shopify_data(tmp_path):
    data_file = tmp_path / "shopify_data.json"
    data_file.write_text(
        json.dumps(
            {
                "products": {
                    "product-1": {
                        "id": "product-1",
                        "title": "Widget",
                        "variants": [],
                    },
                },
                "carts": {},
                "orders": {},
                "customers": {},
                "collections": {},
                "reviews": {},
                "policies": [],
                "counters": {
                    "cart_id": 1000,
                    "line_id": 1000,
                    "order_id": 2000,
                    "line_item_id": 3000,
                    "customer_id": 4000,
                    "collection_id": 5000,
                    "review_id": 6000,
                },
            }
        )
    )
    return data_file


@pytest.fixture(autouse=True)
def _patch_state(shopify_data, monkeypatch):
    monkeypatch.setattr(shopify_state, "_STATE_FILE", shopify_data)
    shopify_state._current_state = None
    shopify_state._stores.clear()
    shopify_state._active_store_id = "default"
    shopify_state.load_state()


class TestCreateReview:
    def test_create_review(self):
        result = handle_create_review(
            CreateReviewArgs(product_id="product-1", rating=5, author="Alice", title="Great!", body="Love it")
        )
        assert result["userErrors"] == []
        r = result["review"]
        assert r["id"].startswith("gid://shopify/Review/")
        assert r["rating"] == 5
        assert r["author"] == "Alice"
        assert r["status"] == "PUBLISHED"

    def test_create_review_nonexistent_product(self):
        result = handle_create_review(CreateReviewArgs(product_id="nonexistent", rating=3, author="Bob"))
        assert result["review"] is None
        assert len(result["userErrors"]) == 1

    def test_create_multiple_reviews(self):
        handle_create_review(CreateReviewArgs(product_id="product-1", rating=5, author="Alice"))
        handle_create_review(CreateReviewArgs(product_id="product-1", rating=3, author="Bob"))
        reviews = handle_get_product_reviews(GetProductReviewsArgs(product_id="product-1"))
        assert reviews["totalCount"] == 2


class TestGetProductReviews:
    def test_get_reviews_empty(self):
        result = handle_get_product_reviews(GetProductReviewsArgs(product_id="product-1"))
        assert result["totalCount"] == 0
        assert result["averageRating"] is None

    def test_get_reviews_with_average(self):
        handle_create_review(CreateReviewArgs(product_id="product-1", rating=5, author="A"))
        handle_create_review(CreateReviewArgs(product_id="product-1", rating=3, author="B"))
        result = handle_get_product_reviews(GetProductReviewsArgs(product_id="product-1"))
        assert result["totalCount"] == 2
        assert result["averageRating"] == 4.0

    def test_get_reviews_filter_by_status(self):
        r1 = handle_create_review(CreateReviewArgs(product_id="product-1", rating=5, author="A"))
        handle_update_review(UpdateReviewArgs(review_id=r1["review"]["id"], status="HIDDEN"))

        handle_create_review(CreateReviewArgs(product_id="product-1", rating=3, author="B"))

        result = handle_get_product_reviews(GetProductReviewsArgs(product_id="product-1", status="PUBLISHED"))
        assert result["totalCount"] == 1

    def test_get_reviews_nonexistent_product(self):
        result = handle_get_product_reviews(GetProductReviewsArgs(product_id="nonexistent"))
        assert len(result["userErrors"]) == 1

    def test_get_reviews_pagination(self):
        handle_create_review(CreateReviewArgs(product_id="product-1", rating=5, author="A"))
        handle_create_review(CreateReviewArgs(product_id="product-1", rating=4, author="B"))
        result = handle_get_product_reviews(GetProductReviewsArgs(product_id="product-1", limit=1))
        assert len(result["reviews"]) == 1
        assert result["pageInfo"]["hasNextPage"] is True


class TestUpdateReview:
    def test_update_status(self):
        create = handle_create_review(CreateReviewArgs(product_id="product-1", rating=5, author="A"))
        rid = create["review"]["id"]

        result = handle_update_review(UpdateReviewArgs(review_id=rid, status="HIDDEN"))
        assert result["review"]["status"] == "HIDDEN"

    def test_update_rating(self):
        create = handle_create_review(CreateReviewArgs(product_id="product-1", rating=3, author="A"))
        rid = create["review"]["id"]

        result = handle_update_review(UpdateReviewArgs(review_id=rid, rating=5))
        assert result["review"]["rating"] == 5

    def test_update_invalid_status(self):
        create = handle_create_review(CreateReviewArgs(product_id="product-1", rating=5, author="A"))
        rid = create["review"]["id"]

        with pytest.raises(ValidationError):
            UpdateReviewArgs.model_validate({"review_id": rid, "status": "INVALID"})

    def test_update_nonexistent(self):
        result = handle_update_review(UpdateReviewArgs(review_id="nonexistent"))
        assert result["review"] is None


class TestDeleteReview:
    def test_delete_review(self):
        create = handle_create_review(CreateReviewArgs(product_id="product-1", rating=5, author="A"))
        rid = create["review"]["id"]

        result = handle_delete_review(DeleteReviewArgs(review_id=rid))
        assert result["deletedReviewId"] == rid

        reviews = handle_get_product_reviews(GetProductReviewsArgs(product_id="product-1"))
        assert reviews["totalCount"] == 0

    def test_delete_nonexistent(self):
        result = handle_delete_review(DeleteReviewArgs(review_id="nonexistent"))
        assert result["deletedReviewId"] is None
        assert len(result["userErrors"]) == 1
