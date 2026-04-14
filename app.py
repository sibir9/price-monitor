import os
import re
import asyncio
from fastapi import FastAPI, Form, Request
from fastapi.templating import Jinja2Templates
from bs4 import BeautifulSoup
import aiohttp
from fake_useragent import UserAgent
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Price Monitor")
templates = Jinja2Templates(directory="templates")
ua = UserAgent()

async def get_price(session, url: str, product_name: str):
    headers = {'User-Agent': ua.random}
    try:
        async with session.get(url, timeout=10) as response:
            html = await response.text()
            soup = BeautifulSoup(html, 'html.parser')
            price_selectors = ['.price', '.product-price', '[data-price]', '.current-price']
            for selector in price_selectors:
                elem = soup.select_one(selector)
                if elem:
                    text = elem.text.strip()
                    numbers = re.findall(r'[\d\s]+[,.]?\d*', text)
                    if numbers:
                        price = re.sub(r'[\s,]', '', numbers[0])
                        return {'url': url, 'price': price, 'found': True}
            return {'url': url, 'price': 'Не найдена', 'found': False}
    except Exception as e:
        return {'url': url, 'price': 'Ошибка', 'found': False}

@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/scrape")
async def scrape_prices(product_name: str = Form(...), urls: str = Form(...)):
    url_list = [u.strip() for u in urls.strip().split('\n') if u.strip()][:10]
    async with aiohttp.ClientSession() as session:
        tasks = [get_price(session, url, product_name) for url in url_list]
        results = await asyncio.gather(*tasks)
    found = [r for r in results if r['found']]
    return {'product': product_name, 'results': results, 'total_found': len(found)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
