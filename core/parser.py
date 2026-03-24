from bs4 import BeautifulSoup
from typing import Dict, List, Optional
from urllib.parse import urljoin

def parse_product_html(html_content: str, url: str) -> Optional[Dict]:
    if not html_content:
        return None
    
    # Use 'html.parser' or 'lxml'
    soup = BeautifulSoup(html_content, "html.parser")
    
    try:
        # On Product to Scrape product pages, the title is inside an <h1>
        title_tag = soup.find("h1") or soup.find("h2") or soup.find("h3") or soup.find("h4") or soup.find("h5") or soup.find("h6") or soup.find("span") or soup.find("p") 
        title = title_tag.get_text(strip=True) if title_tag else "N/A"

        # The price is in a <p> with class "price_color"
        price_tag = soup.find("p", class_="price_color")
        if price_tag:
            # We strip non-numeric characters like £ or Â
            price_text = "".join(c for c in price_tag.get_text() if c.isdigit() or c == '.')
            price = float(price_text)
        else:
            price = 0.0
        
        return {
            "title": title,
            "price": price,
            "url": url
        }
    except Exception as e:
        print(f"Error parsing {url}: {e}")
        return None

def parse_multiple_pages(html_list: List[str], urls: List[str]) -> List[Dict]:
    results = []
    for html, url in zip(html_list, urls):
        data = parse_product_html(html, url)
        # Only add if we actually found a title and a price
        if data and data['title'] != "N/A" and data['price'] > 0:
            results.append(data)
    return results


def extract_product_links(homepage_html: str, base_url: str) -> List[str]:
    """Finds all product links on a main category/home page."""
    soup = BeautifulSoup(homepage_html, "html.parser")
    links = []
    
    # On Products to Scrape, each book is inside an <article class="product_pod">
    for article in soup.find_all("article", class_="product_pod"):
        a_tag = article.find("h3").find("a")
        if a_tag and 'href' in a_tag.attrs:
            # urljoin turns 'catalogue/book_1.html' into a full 'https://...' URL
            full_url = urljoin(base_url, a_tag['href'])
            links.append(full_url)
            
    return list(set(links)) # Remove duplicates
