"""Tests for word-AND + quoted-phrase search semantics across Shopify search tools."""

import json

import pytest
from pydantic import ValidationError

from shopify import state as shopify_state
from shopify.models import (
    CategoryFilter,
    PriceFilter,
    SearchFilter,
    SearchShopCatalogArgs,
    SearchShopPoliciesAndFaqsArgs,
    VariantOptionFilter,
)
from shopify.state import search_faqs, search_policies, search_products
from shopify.tools.catalog import handle_search_shop_catalog, handle_search_shop_policies_and_faqs


@pytest.fixture
def shopify_data(tmp_path):
    data_file = tmp_path / "shopify_data.json"
    data_file.write_text(
        json.dumps(
            {
                "products": {
                    "p1": {
                        "id": "p1",
                        "title": "Wireless Headphones",
                        "description": "Premium noise-cancelling over-ear model.",
                        "handle": "wireless-headphones",
                        "productType": "Electronics",
                        "vendor": "AudioTech",
                        "tags": ["wireless", "audio"],
                        "category": {"id": "cat-audio", "name": "Audio"},
                        "availableForSale": True,
                        "options": [{"name": "Color", "values": ["Black"]}],
                        "priceRange": {
                            "minVariantPrice": {"amount": "89.99", "currencyCode": "USD"},
                            "maxVariantPrice": {"amount": "89.99", "currencyCode": "USD"},
                        },
                        "variants": [
                            {
                                "id": "v1",
                                "title": "Black",
                                "price": {"amount": "89.99", "currencyCode": "USD"},
                                "sku": "AT-101-BLK",
                                "availableForSale": True,
                                "selectedOptions": [{"name": "Color", "value": "Black"}],
                            }
                        ],
                    },
                    "p2": {
                        "id": "p2",
                        "title": "Bluetooth Speaker",
                        "description": "Portable wireless bluetooth speaker.",
                        "handle": "bluetooth-speaker",
                        "productType": "Electronics",
                        "vendor": "AudioTech",
                        "tags": ["wireless", "audio", "bluetooth"],
                        "availableForSale": True,
                        "options": [{"name": "Color", "values": ["Blue"]}],
                        "priceRange": {
                            "minVariantPrice": {"amount": "49.99", "currencyCode": "USD"},
                            "maxVariantPrice": {"amount": "49.99", "currencyCode": "USD"},
                        },
                        "variants": [
                            {
                                "id": "v2",
                                "title": "Default",
                                "price": {"amount": "49.99", "currencyCode": "USD"},
                                "sku": "AT-202",
                                "availableForSale": True,
                                "selectedOptions": [{"name": "Color", "value": "Blue"}],
                            }
                        ],
                    },
                    "p3": {
                        "id": "p3",
                        "title": "Studio Microphone",
                        "description": "Cardioid vocal microphone.",
                        "handle": "studio-microphone",
                        "productType": "Electronics",
                        "vendor": "SoundWorks",
                        "tags": ["audio", "recording"],
                        "availableForSale": False,
                        "priceRange": {
                            "minVariantPrice": {"amount": "129.99", "currencyCode": "USD"},
                            "maxVariantPrice": {"amount": "129.99", "currencyCode": "USD"},
                        },
                        "variants": [
                            {
                                "id": "v3",
                                "title": "Default",
                                "price": {"amount": "129.99", "currencyCode": "USD"},
                                "sku": "SW-MIC-1",
                                "availableForSale": False,
                            }
                        ],
                    },
                    "p4": {
                        "id": "p4",
                        "title": "Travel Earbuds",
                        "description": "Compact wireless earbuds for commuting.",
                        "handle": "travel-earbuds",
                        "productType": "Electronics",
                        "vendor": "AudioTech",
                        "tags": ["wireless", "audio", "travel"],
                        "priceRange": {
                            "minVariantPrice": {"amount": "99.99", "currencyCode": "USD"},
                            "maxVariantPrice": {"amount": "99.99", "currencyCode": "USD"},
                        },
                        "variants": [
                            {
                                "id": "v4",
                                "title": "Green",
                                "price": {"amount": "99.99", "currencyCode": "USD"},
                                "sku": "AT-EARBUD-GRN",
                                "availableForSale": True,
                                "selectedOptions": [{"name": "Color", "value": "Green"}],
                            }
                        ],
                    },
                    "p5": {
                        "id": "p5",
                        "title": "Clearance Speaker Dock",
                        "description": "Legacy speaker dock for office desks.",
                        "handle": "clearance-speaker-dock",
                        "productType": "Electronics",
                        "vendor": "SoundWorks",
                        "tags": ["audio", "clearance"],
                        "priceRange": {
                            "minVariantPrice": {"amount": "39.99", "currencyCode": "USD"},
                            "maxVariantPrice": {"amount": "39.99", "currencyCode": "USD"},
                        },
                        "variants": [
                            {
                                "id": "v5",
                                "title": "Default",
                                "price": {"amount": "39.99", "currencyCode": "USD"},
                                "sku": "SW-DOCK-1",
                                "availableForSale": False,
                            }
                        ],
                    },
                },
                "carts": {},
                "orders": {},
                "customers": {},
                "collections": {
                    "coll-audio": {
                        "id": "coll-audio",
                        "title": "Audio Gear",
                        "handle": "audio-gear",
                        "productIds": ["P1", "p2"],
                    }
                },
                "reviews": {},
                "returns": {},
                "discount_codes": {},
                "shipping_methods": {},
                "policies": [
                    {
                        "id": "pol1",
                        "type": "fixture-policy-type",
                        "title": "Return Policy",
                        "body": "<p>30-day returns on all items including headphones and speakers.</p>",
                        "url": "https://shop.example.com/policies/pol1",
                    },
                    {
                        "id": "pol2",
                        "title": "Shipping Policy",
                        "body": "<p>Free shipping over $50.</p>",
                        "url": "https://shop.example.com/policies/pol2",
                    },
                ],
                "faqs": [
                    {
                        "type": "fixture-faq-type",
                        "question": "What are your business hours?",
                        "answer": "Our support team is available Monday-Friday 9am-5pm EST.",
                    },
                    {
                        "question": "Do headphones include a warranty?",
                        "answer": "Audio products include a one-year warranty.",
                    },
                ],
                "counters": {"cart_id": 1000},
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


class TestSearchProductsWordAnd:
    def test_all_words_must_appear(self):
        # "wireless headphones" hits only p1 — p2 has "wireless" in tags but not "headphones"
        products, *_ = search_products("wireless headphones")
        ids = [p["id"] for p in products]
        assert ids == ["p1"]

    def test_order_does_not_matter(self):
        products, *_ = search_products("headphones wireless")
        assert [p["id"] for p in products] == ["p1"]

    def test_misses_when_one_word_absent(self):
        products, *_ = search_products("wireless espresso")
        assert products == []

    def test_quoted_phrase_requires_adjacency(self):
        # "Wireless Headphones" is adjacent in the title.
        adjacent, *_ = search_products('"wireless headphones"')
        assert [p["id"] for p in adjacent] == ["p1"]

        # "headphones wireless" is not adjacent anywhere.
        non_adjacent, *_ = search_products('"headphones wireless"')
        assert non_adjacent == []

    def test_sku_still_matches_as_single_token(self):
        # Structured SKU with hyphens is one token (no whitespace).
        products, *_ = search_products("AT-101-BLK")
        assert [p["id"] for p in products] == ["p1"]

    def test_mixed_sku_and_word(self):
        # SKU + prose word both must appear in the concatenated haystack.
        products, *_ = search_products("AT-202 speaker")
        assert [p["id"] for p in products] == ["p2"]


class TestSearchCatalogTool:
    def test_empty_query_returns_all_products_without_default_availability_filter(self):
        result = handle_search_shop_catalog(SearchShopCatalogArgs(query="", context="browsing"))
        assert result["totalCount"] == 5
        assert {p["id"] for p in result["nodes"]} == {"p1", "p2", "p3", "p4", "p5"}

    def test_multi_word_query_hits(self):
        result = handle_search_shop_catalog(SearchShopCatalogArgs(query="wireless headphones", context="buying"))
        assert result["totalCount"] == 1

    def test_multi_word_query_miss(self):
        result = handle_search_shop_catalog(SearchShopCatalogArgs(query="wireless grandfatherclock", context="buying"))
        assert result["totalCount"] == 0

    def test_filter_only_search_returns_matching_products(self):
        result = handle_search_shop_catalog(
            SearchShopCatalogArgs(query="", context="browsing", filters=[SearchFilter(productVendor="AudioTech")])
        )
        assert result["totalCount"] == 3
        assert {p["id"] for p in result["nodes"]} == {"p1", "p2", "p4"}

    def test_query_and_filter_narrows_results(self):
        result = handle_search_shop_catalog(
            SearchShopCatalogArgs(query="wireless", context="browsing", filters=[SearchFilter(tag="bluetooth")])
        )
        assert result["totalCount"] == 1
        assert result["nodes"][0]["id"] == "p2"

    def test_variant_option_filter(self):
        result = handle_search_shop_catalog(
            SearchShopCatalogArgs(
                query="",
                context="browsing",
                filters=[SearchFilter(variantOption=VariantOptionFilter(name="Color", value="Black"))],
            )
        )
        assert result["totalCount"] == 1
        assert result["nodes"][0]["id"] == "p1"

    def test_variant_option_filter_matches_selected_options_without_top_level_options(self):
        result = handle_search_shop_catalog(
            SearchShopCatalogArgs(
                query="",
                context="browsing",
                filters=[SearchFilter(variantOption=VariantOptionFilter(name="Color", value="Green"))],
            )
        )
        assert result["totalCount"] == 1
        assert result["nodes"][0]["id"] == "p4"

    def test_available_filter(self):
        result = handle_search_shop_catalog(
            SearchShopCatalogArgs(query="", context="browsing", filters=[SearchFilter(available=False)])
        )
        assert result["totalCount"] == 2
        assert {p["id"] for p in result["nodes"]} == {"p3", "p5"}

    def test_multi_filter_combination_and_price_boundary(self):
        result = handle_search_shop_catalog(
            SearchShopCatalogArgs(
                query="",
                context="browsing",
                filters=[
                    SearchFilter(tag="travel"),
                    SearchFilter(available=True),
                    SearchFilter(price=PriceFilter(min=99.99, max=99.99)),
                    SearchFilter(variantOption=VariantOptionFilter(name="Color", value="Green")),
                ],
            )
        )
        assert result["totalCount"] == 1
        assert result["nodes"][0]["id"] == "p4"

    def test_exact_price_boundary_matches(self):
        result = handle_search_shop_catalog(
            SearchShopCatalogArgs(
                query="",
                context="browsing",
                filters=[SearchFilter(price=PriceFilter(min=49.99, max=49.99))],
            )
        )
        assert result["totalCount"] == 1
        assert result["nodes"][0]["id"] == "p2"

    def test_category_filter_matches_collection_membership(self):
        result = handle_search_shop_catalog(
            SearchShopCatalogArgs(
                query="",
                context="browsing",
                filters=[SearchFilter(category=CategoryFilter(id="coll-audio"))],
            )
        )
        assert result["totalCount"] == 2
        assert {p["id"] for p in result["nodes"]} == {"p1", "p2"}

    def test_filter_only_no_match_is_empty(self):
        result = handle_search_shop_catalog(
            SearchShopCatalogArgs(query="", context="browsing", filters=[SearchFilter(productType="Furniture")])
        )
        assert result["totalCount"] == 0
        assert result["nodes"] == []

    def test_country_and_language_are_reported_as_noop_hints(self):
        result = handle_search_shop_catalog(
            SearchShopCatalogArgs(query="bluetooth", context="browsing", country="US", language="EN")
        )
        assert result["totalCount"] == 1
        assert result["localization"] == {"country": "US", "language": "EN", "applied": False}

    def test_unsupported_filter_field_reports_warning(self):
        search_filter = SearchFilter.model_validate({"giftCard": True})
        result = handle_search_shop_catalog(
            SearchShopCatalogArgs(query="", context="browsing", filters=[search_filter])
        )
        assert result["totalCount"] == 5
        assert result["warnings"] == ["Unsupported catalog filter field 'giftCard' was ignored."]

    def test_malformed_price_filter_reports_warning(self):
        result = handle_search_shop_catalog(
            SearchShopCatalogArgs(
                query="",
                context="browsing",
                filters=[SearchFilter(price=PriceFilter(min=100.0, max=50.0))],
            )
        )
        assert result["totalCount"] == 0
        assert result["warnings"] == ["Price filter min is greater than max; no products can match that filter."]

    def test_unsafe_pagination_inputs_report_warnings(self):
        with pytest.raises(ValidationError):
            SearchShopCatalogArgs(query="audio", context="browsing", limit=-1)

        capped = handle_search_shop_catalog(SearchShopCatalogArgs(query="audio", context="browsing", limit=999))
        assert len(capped["nodes"]) == 5
        assert capped["warnings"] == ["limit exceeds the maximum of 250; using 250."]

        malformed_cursor = handle_search_shop_catalog(
            SearchShopCatalogArgs(query="audio", context="browsing", after="not-a-cursor")
        )
        assert malformed_cursor["pageInfo"]["hasPreviousPage"] is False
        assert malformed_cursor["warnings"] == ["Invalid after cursor 'not-a-cursor'; using the first page."]


class TestSearchPoliciesWordAnd:
    def test_all_words_must_appear(self):
        # "return headphones" both appear in Return Policy's title + body.
        results = search_policies("return headphones")
        assert len(results) == 1
        assert results[0]["title"] == "Return Policy"

    def test_misses_when_one_word_absent(self):
        results = search_policies("return xyzunknown")
        assert results == []

    def test_quoted_phrase_requires_adjacency(self):
        # "Free shipping" is literal text in pol2.
        hit = search_policies('"free shipping"')
        assert [p["id"] for p in hit] == ["pol2"]

        miss = search_policies('"shipping free"')
        assert miss == []


class TestSearchFaqs:
    def test_faq_question_answer_search(self):
        results = search_faqs("business hours")
        assert len(results) == 1
        assert results[0]["question"] == "What are your business hours?"

    def test_policy_and_faq_tool_returns_typed_results(self):
        policy = handle_search_shop_policies_and_faqs(SearchShopPoliciesAndFaqsArgs(query="return headphones"))
        assert policy["results"][0]["type"] == "policy"
        assert "30-day returns" in policy["answer"]

        faq = handle_search_shop_policies_and_faqs(SearchShopPoliciesAndFaqsArgs(query="business hours"))
        assert faq["results"][0]["type"] == "faq"
        assert "Monday-Friday" in faq["answer"]

    def test_state_round_trip_preserves_faqs(self):
        exported = shopify_state.state_to_json()
        assert exported["faqs"][0]["question"] == "What are your business hours?"

        shopify_state.state_from_json(exported)
        round_tripped = shopify_state.state_to_json()
        assert round_tripped["faqs"] == exported["faqs"]
