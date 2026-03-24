import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))


import asyncio
from data.models.database import init_db, AsyncSessionLocal, Product
from core.fetcher import fetch_all
from core.parser import parse_multiple_pages, extract_product_links, parse_product_html
from utils.processor import clean_scraped_data
from sqlalchemy.dialects.sqlite import insert as sqlite_upsert
from concurrent.futures import ProcessPoolExecutor

executor = ProcessPoolExecutor()


BASE_URL = "https://www.amazon.com/s?k=headphones&_encoding=UTF8&content-id=amzn1.sym.f0670b1b-e1fd-4c67-a2b1-b8a347243628&pd_rd_r=58b78489-5fc1-4711-b59d-670648d404ee&pd_rd_w=KnD17&pd_rd_wg=U2U38&pf_rd_p=f0670b1b-e1fd-4c67-a2b1-b8a347243628&pf_rd_r=Q2RERNYT27AVNZA6XD5G&ref=pd_hp_d_btf_unk"

async def run_pipeline():
    
    await init_db()
    
    # 1. Get the Homepage to find links
    print(f"Step 1: Finding links on {BASE_URL}...")
    print("__" * 30)
    homepage_html = await fetch_all([BASE_URL])
    
    # 2. Extract the actual product URLs
    print("__" * 30)
    product_links = extract_product_links(homepage_html[0], BASE_URL)
    print(f"Step 2: Found {len(product_links)}  to scrape.")
    if not product_links:
        print("Stopping: No links were found. Check your selectors or headers.")
        return
    print("__" * 30)

    # 3. Fetch all those  pages concurrently!
    print(f"Step 3: Fetching {len(product_links)} product pages...")
    html_contents = await fetch_all(product_links)
    print("__" * 30)
    
    # 4. Parse each individual page
    print("Step 4: Extracting titles and prices...")
    loop = asyncio.get_event_loop()
    parsed_task = [
        loop.run_in_executor(executor, parse_product_html, html, url)
        for html, url in zip(html_contents, urls) # type: ignore
    ]
    raw_results = await asyncio.gather(*parsed_task), parse_multiple_pages(html_contents, product_links)
    #raw_results = parse_multiple_pages(html_contents, product_links)
    print("__" * 30)
    
    # 5. Clean with Pandas
    clean_results = clean_scraped_data(raw_results)
    
    if not clean_results:
        print("No new data to save.")
        return
    
    # 6. Save with "Upsert" (Ignore if URL already exists)
    print(f"Step 5: Saving {len(clean_results)} products to Database...")
    print("__" * 30)
    async with AsyncSessionLocal() as session:
        # We use a special SQLite 'insert' to handle the Unique Constraint error
        stmt = sqlite_upsert(Product).values(clean_results)
        stmt = stmt.on_conflict_do_nothing(index_elements=['url'])
        
        await session.execute(stmt)
        await session.commit()
    
    print("--- Pipeline Complete! Check your database.db ---")

if __name__ == "__main__":
    asyncio.run(run_pipeline())
