import unittest

from core.parser import extract_product_links, parse_product_html
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


if __name__ == "__main__":
    unittest.main()
