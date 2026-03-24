import asyncio
import aiohttp
from typing import List
from dotenv import load_dotenv
import os

load_dotenv()


# Standard browser hearders so websites don't block our requests
HEADERS = {
    "User-Agent": os.getenv("USER_AGENT"),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.google.com"
}

sem = asyncio.Semaphore(10)


async def fetch_page(url: str, session: aiohttp.ClientSession) -> str:
    """ Fetchesthe HTML content of single URL."""

    try:
        async with sem:
            timeout = aiohttp.ClientTimeout(total=15)
            async with session.get(url, headers=HEADERS, timeout=timeout) as response:
                # Check if the response is successful 200 ok code
                response.raise_for_status()
                return await response.text()
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return ""
    
async def fetch_all(urls: List[str]) -> List[str]:
    """ Orchestrates multiple requests concurrently."""
    # Create a single session for all requests to reuse connections
    async with aiohttp.ClientSession() as session:
        tasks = []
        for url in urls:
            tasks.append(fetch_page(url, session))
            
            #asyncio.gather runs all the tasks concurrently and waits for them to finish
        pages_html = await asyncio.gather(*tasks)
        return pages_html
        
