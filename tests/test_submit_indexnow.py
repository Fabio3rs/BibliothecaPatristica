from tools.submit_indexnow import (
    Download,
    batches,
    collect_sitemap_urls,
    normalize_page_url,
    parse_sitemap,
    validate_urls,
)


def test_parse_sitemap_urlset_with_namespace():
    document = Download(
        b'''<?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://example.org/one</loc></url>
          <url><loc>https://example.org/two</loc></url>
        </urlset>'''
    )

    kind, locations = parse_sitemap(document, "https://example.org/sitemap.xml")

    assert kind == "urlset"
    assert locations == ["https://example.org/one", "https://example.org/two"]


def test_collect_sitemap_urls_recurses_and_removes_duplicates():
    documents = {
        "https://example.org/index.xml": b'''
            <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <sitemap><loc>https://example.org/first.xml</loc></sitemap>
              <sitemap><loc>https://example.org/second.xml</loc></sitemap>
            </sitemapindex>''',
        "https://example.org/first.xml": b'''
            <urlset><url><loc>https://example.org/one</loc></url></urlset>''',
        "https://example.org/second.xml": b'''
            <urlset>
              <url><loc>https://example.org/one</loc></url>
              <url><loc>https://example.org/two</loc></url>
            </urlset>''',
    }

    def fake_download(url, _timeout):
        return Download(documents[url])

    urls = collect_sitemap_urls(
        "https://example.org/index.xml", timeout=1, downloader=fake_download
    )

    assert urls == ["https://example.org/one", "https://example.org/two"]


def test_validate_urls_rejects_another_host():
    try:
        validate_urls(["https://other.example/page"], "example.org")
    except ValueError as error:
        assert "fora do host" in str(error)
    else:
        raise AssertionError("validate_urls deveria rejeitar outro host")


def test_batches_splits_urls_at_requested_size():
    assert list(batches(["a", "b", "c"], 2)) == [["a", "b"], ["c"]]


def test_normalize_page_url_adds_slash_only_to_html_routes():
    assert (
        normalize_page_url("https://example.org/seo/volumes/PG001")
        == "https://example.org/seo/volumes/PG001/"
    )
    assert normalize_page_url("https://example.org/search?q=deus") == (
        "https://example.org/search/?q=deus"
    )
    assert normalize_page_url("https://example.org/sitemap.xml") == (
        "https://example.org/sitemap.xml"
    )
    assert normalize_page_url("https://example.org/already/") == (
        "https://example.org/already/"
    )
