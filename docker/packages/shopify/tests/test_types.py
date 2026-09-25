"""Tests for Shopify type definitions and utility functions."""

from pathlib import Path

from shopify.models import Image, MoneyV2, PageInfo, SelectedOption
from shopify.utils import get_shopify_state_path


class TestGetShopifyStatePath:
    def test_computes_external_services_path(self):
        result = get_shopify_state_path("/workspace/dumps/workspace")
        assert result == Path("/workspace/dumps/external_services/shopify_data.json")

    def test_path_is_sibling_to_workspace(self):
        result = get_shopify_state_path("/a/b/workspace")
        assert result.parent == Path("/a/b/external_services")


class TestMoneyV2:
    def test_creates_money(self):
        m = MoneyV2(amount="19.99", currencyCode="USD")
        assert m.amount == "19.99"
        assert m.currencyCode == "USD"


class TestImage:
    def test_creates_image_minimal(self):
        img = Image(url="https://example.com/img.png")
        assert img.url == "https://example.com/img.png"
        assert img.altText is None


class TestSelectedOption:
    def test_creates_option(self):
        opt = SelectedOption(name="Size", value="Large")
        assert opt.name == "Size"
        assert opt.value == "Large"


class TestPageInfo:
    def test_defaults(self):
        pi = PageInfo()
        assert pi.hasNextPage is False
        assert pi.hasPreviousPage is False
        assert pi.startCursor is None
