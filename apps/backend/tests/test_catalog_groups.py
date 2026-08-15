from app.domains.catalog_groups import (
    canonical_catalog_slug,
    classify_catalog_slug,
    coerce_workspace_domain,
    resolve_catalog_domain,
)


def test_canonical_slug_strips_copy_suffixes() -> None:
    assert canonical_catalog_slug("learning-guide") == "learning-guide"
    assert canonical_catalog_slug("learning-guide-copy") == "learning-guide"
    assert canonical_catalog_slug("learning-guide-copy-2") == "learning-guide"
    assert canonical_catalog_slug("front-desk-team") == "front-desk-team"


def test_classify_stock_broker_and_dental_slugs() -> None:
    assert classify_catalog_slug("learning-guide") == ("stock_broker", "learning")
    assert classify_catalog_slug("paper-trading-copy") == ("stock_broker", "paper")
    assert classify_catalog_slug("live-approval") == ("stock_broker", "live")
    assert classify_catalog_slug("research") == ("stock_broker", "research")
    assert classify_catalog_slug("researcher-copy") == ("stock_broker", "research")
    assert classify_catalog_slug("front-desk") == ("dental_clinic", None)
    assert classify_catalog_slug("research-bot") == ("generic", None)


def test_resolve_prefers_pack_slug_over_destination_tenant() -> None:
    assert (
        resolve_catalog_domain(
            slug="learning",
            stored_domain="generic",
            tenant_domain="dental_clinic",
        )
        == "stock_broker"
    )


def test_resolve_uses_stored_then_tenant_for_custom_slugs() -> None:
    assert (
        resolve_catalog_domain(
            slug="research-bot",
            stored_domain="stock_broker",
            tenant_domain="generic",
        )
        == "stock_broker"
    )
    assert (
        resolve_catalog_domain(
            slug="research-bot",
            stored_domain="generic",
            tenant_domain="dental_clinic",
        )
        == "dental_clinic"
    )
    assert coerce_workspace_domain("general") == "generic"
