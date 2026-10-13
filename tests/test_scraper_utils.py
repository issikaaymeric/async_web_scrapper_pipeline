import unittest
from unittest.mock import AsyncMock, patch
from urllib.robotparser import RobotFileParser

from core.crawler import crawl_site
from core.parser import extract_internal_links, extract_product_links, parse_product_html
from utils.processor import calculate_price_change, clean_scraped_data


class ScraperUtilitiesTests(unittest.TestCase):
    def test_parse_product_html_reads_meta_title_and_price(self):
        html = '''
        <html>
          <head>
            <meta property="og:title" content="Noise Cancelling Headphones" />
            <meta property="og:price:amount" content="199.99" />
          </head>
          <body></body>
        </html>
        '''

        result = parse_product_html(html, "https://example.com/product/1")

        self.assertIsNotNone(result)
        self.assertEqual(result["title"], "Noise Cancelling Headphones")
        self.assertAlmostEqual(result["price"], 199.99)

    def test_parse_product_html_reads_nested_product_json_ld(self):
        html = """
        <html><head>
          <script type="application/ld+json">
          {
            "@context": "https://schema.org",
            "@graph": [{
              "@type": "Product",
              "name": "Camera",
              "image": ["/images/camera.jpg"],
              "offers": {"@type": "Offer", "price": "1.299,95"},
              "aggregateRating": {"ratingValue": "4.7"}
            }]
          }
          </script>
        </head></html>
        """

        result = parse_product_html(html, "https://shop.example/products/camera")

        self.assertEqual(result["title"], "Camera")
        self.assertAlmostEqual(result["price"], 1299.95)
        self.assertAlmostEqual(result["rating"], 4.7)
        self.assertEqual(result["image_url"], "https://shop.example/images/camera.jpg")

    def test_extract_product_links_handles_relative_urls(self):
        html = '''
        <html>
          <body>
            <article class="product_pod">
              <h3><a href="/dp/B0ABC123">Sample Product</a></h3>
            </article>
            <a href="/gp/product/XYZ">Another product</a>
          </body>
        </html>
        '''

        links = extract_product_links(html, "https://example.com")

        self.assertIn("https://example.com/dp/B0ABC123", links)
        self.assertIn("https://example.com/gp/product/XYZ", links)

    def test_extract_product_links_uses_product_cards_and_ignores_navigation(self):
        html = """
        <html><body>
          <nav><a href="/about">About</a><a href="/cart">Cart</a></nav>
          <div class="product-card">
            <a href="/catalog/camera-x">Camera X</a>
            <span class="price">$249.99</span>
          </div>
          <a href="https://other.example/products/not-ours">External product</a>
        </body></html>
        """

        links = extract_product_links(html, "https://shop.example/catalog")

        self.assertEqual(links, ["https://shop.example/catalog/camera-x"])

    def test_extract_internal_links_keeps_only_crawlable_same_host_links(self):
        html = """
        <html><body>
          <a href="/category/cameras">Category</a>
          <a href="/manual.pdf">Manual</a>
          <a href="https://outside.example/products/camera">External</a>
          <a href="/private" rel="nofollow">No follow</a>
          <a href="#top">Fragment</a>
        </body></html>
        """

        links = extract_internal_links(html, "https://shop.example/start")

        self.assertEqual(links, ["https://shop.example/category/cameras"])

    def test_clean_scraped_data_filters_duplicates_and_invalid_prices(self):
        rows = [
            {"title": "Headphones", "price": "199.99", "url": "https://example.com/a"},
            {"title": "N/A", "price": "0", "url": "https://example.com/b"},
            {"title": " Headphones ", "price": "199.99", "url": "https://example.com/a"},
            {"title": "Wireless Earbuds", "price": "-5", "url": "https://example.com/c"},
        ]

        cleaned = clean_scraped_data(rows)

        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0]["title"], "Headphones")
        self.assertAlmostEqual(cleaned[0]["price"], 199.99)

    def test_calculate_price_change_uses_percentage_delta(self):
        delta = calculate_price_change(80, 100)
        self.assertAlmostEqual(delta, -20.0)


class SiteCrawlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_crawl_respects_page_limit_and_stays_on_starting_host(self):
        policy = RobotFileParser()
        policy.parse(["User-agent: *", "Allow: /"])
        pages = {
            "https://shop.example/": """
                <a href="/products/a">Product A</a>
                <a href="https://outside.example/products/x">External</a>
            """,
            "https://shop.example/products/a": """
                <script type="application/ld+json">
                {"@type":"Product","name":"Product A","offers":{"price":"25.00"}}
                </script>
            """,
        }
        requested_urls = []

        async def fake_fetch_all(urls, **kwargs):
            requested_urls.extend(urls)
            return [pages.get(url, "") for url in urls]

        with (
            patch("core.crawler._load_robots_policy", new=AsyncMock(return_value=(policy, 0.0))),
            patch("core.crawler.fetch_all", new=AsyncMock(side_effect=fake_fetch_all)),
        ):
            result = await crawl_site(
                "https://shop.example/",
                max_products=10,
                max_pages=2,
                max_depth=2,
                max_concurrency=2,
            )

        self.assertEqual(result.pages_visited, 2)
        self.assertEqual(requested_urls, ["https://shop.example/", "https://shop.example/products/a"])
        self.assertEqual([row["title"] for row in result.products], ["Product A"])


if __name__ == "__main__":
    unittest.main()
