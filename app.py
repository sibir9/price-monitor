import re
import asyncio
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from bs4 import BeautifulSoup
import aiohttp
from fake_useragent import UserAgent
import logging
from urllib.parse import urlparse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Price Monitor")
ua = UserAgent()

async def parse_price(session, url: str, target_product: str):
    headers = {'User-Agent': ua.random}
    try:
        async with session.get(url, timeout=15, headers=headers) as response:
            if response.status != 200:
                return {'url': url, 'price': f'HTTP {response.status}', 'found': False}
            
            html = await response.text()
            soup = BeautifulSoup(html, 'html.parser')
            
            logger.info(f"=== {urlparse(url).netloc} | Ищем: '{target_product}' ===")
            
            # Для iskra-rus - прямая выдерка цены
            if 'iskra-rus' in url:
                # Прямой поиск по ID
                price_elem = soup.select_one('#bx_117848907_13271_price')
                if price_elem:
                    price_text = price_elem.get_text(strip=True)
                    logger.info(f"Найдена цена: {price_text}")
                    # Просто берем число из строки
                    match = re.search(r'(\d+)', price_text)
                    if match:
                        price = match.group(1)
                        logger.info(f"✅ Цена {price} руб.")
                        return {
                            'url': url,
                            'price': price,
                            'found': True,
                            'product_found': target_product
                        }
                
                logger.info(f"❌ Цена не найдена")
                return {'url': url, 'price': 'Товар не найден', 'found': False}
            
            # Для остальных сайтов
            search_query = target_product.lower()
            all_prices = []
            
            # Ищем все элементы с ценой
            for elem in soup.find_all(string=True):
                if not elem:
                    continue
                if not isinstance(elem, str):
                    continue
                if search_query in elem.lower():
                    # Нашли название, ищем родительский контейнер с ценой
                    parent = elem.find_parent()
                    if parent:
                        # Ищем цену в родителе
                        for text in parent.find_all(string=True):
                            if text and isinstance(text, str):
                                match = re.search(r'(\d+)\s*[Рр]уб', text)
                                if match:
                                    price = match.group(1)
                                    all_prices.append(int(price))
                                    logger.info(f"Найдена цена {price} для товара: {elem[:50]}")
                                    break
            
            if all_prices:
                best = min(all_prices)
                logger.info(f"🏆 Лучшая цена: {best} руб.")
                return {
                    'url': url,
                    'price': str(best),
                    'found': True,
                    'product_found': target_product
                }
            
            logger.info(f"❌ Товар '{target_product}' не найден")
            return {'url': url, 'price': 'Товар не найден', 'found': False}
            
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return {'url': url, 'price': 'Ошибка', 'found': False}

@app.get("/", response_class=HTMLResponse)
async def home():
    return HTMLResponse(content="""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Мониторинг цен конкурентов</title>
        <style>
            body { font-family: Arial; max-width: 900px; margin: 50px auto; padding: 20px; }
            input, textarea { width: 100%; padding: 10px; margin: 10px 0; border: 1px solid #ddd; border-radius: 5px; font-family: inherit; }
            button { background: #007bff; color: white; padding: 12px 24px; border: none; border-radius: 5px; cursor: pointer; font-size: 16px; }
            button:hover { background: #0056b3; }
            .loading { display: none; text-align: center; padding: 20px; }
            .spinner { border: 4px solid #f3f3f3; border-top: 4px solid #007bff; border-radius: 50%; width: 40px; height: 40px; animation: spin 1s linear infinite; margin: 0 auto; }
            @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
            .results { margin-top: 30px; display: none; }
            .cheapest { background: #28a745; color: white; padding: 15px; border-radius: 5px; margin-bottom: 20px; text-align: center; }
            table { width: 100%; border-collapse: collapse; }
            th, td { padding: 10px; text-align: left; border-bottom: 1px solid #ddd; }
            th { background: #f5f5f5; }
            .price-found { color: #28a745; font-weight: bold; }
            .price-error { color: #dc3545; }
            .product-name { font-size: 11px; color: #666; }
        </style>
    </head>
    <body>
        <h1>🔍 Мониторинг цен конкурентов</h1>
        <form id="scrapeForm">
            <label>📦 Название товара (обязательно)</label>
            <input type="text" id="productName" placeholder="Пример: 61806" required>
            <label>🔗 Ссылки на товары конкурентов (по одной на строку)</label>
            <textarea id="urls" rows="6" placeholder="https://iskra-rus.ru/product/podshipnik_61806_2rs_iskra/" required></textarea>
            <button type="submit">🚀 Найти цены</button>
        </form>
        <div class="loading" id="loading"><div class="spinner"></div><p>Парсинг...</p></div>
        <div class="results" id="results"></div>
        <script>
            document.getElementById('scrapeForm').onsubmit = async (e) => {
                e.preventDefault();
                const productName = document.getElementById('productName').value;
                const urls = document.getElementById('urls').value;
                if (!productName || !urls) return alert('Заполните поля');
                document.getElementById('loading').style.display = 'block';
                document.getElementById('results').style.display = 'none';
                const formData = new URLSearchParams();
                formData.append('product_name', productName);
                formData.append('urls', urls);
                try {
                    const response = await fetch('/scrape', { method: 'POST', body: formData });
                    const data = await response.json();
                    let html = '';
                    if (data.cheapest) {
                        let hostname = new URL(data.cheapest.url).hostname;
                        html += `<div class="cheapest">🏆 ЛУЧШАЯ ЦЕНА: <strong>${data.cheapest.price} ₽</strong><br><small>${hostname}</small></div>`;
                    }
                    html += '<h3>Результаты</h3>58<table2 <thead><tr><th>Магазин</th><th>Цена</th></thead><tbody>';
                    data.results.forEach(r => {
                        let hostname = new URL(r.url).hostname;
                        let priceClass = r.found ? 'price-found' : 'price-error';
                        let priceText = r.found ? `${r.price} ₽` : r.price;
                        html += `<tr><td>${hostname}</td><td class="${priceClass}">${priceText}</td></tr>`;
                    });
                    html += '</tbody></table>';
                    document.getElementById('results').innerHTML = html;
                    document.getElementById('results').style.display = 'block';
                } catch(e) {
                    document.getElementById('results').innerHTML = `<div style="color:red">Ошибка: ${e.message}</div>`;
                    document.getElementById('results').style.display = 'block';
                } finally {
                    document.getElementById('loading').style.display = 'none';
                }
            };
        </script>
    </body>
    </html>
    """)

@app.post("/scrape")
async def scrape_prices(product_name: str = Form(...), urls: str = Form(...)):
    url_list = [u.strip() for u in urls.strip().split('\n') if u.strip()][:10]
    async with aiohttp.ClientSession() as session:
        tasks = [parse_price(session, url, product_name) for url in url_list]
        results = await asyncio.gather(*tasks)
    
    found = [r for r in results if r['found']]
    found.sort(key=lambda x: int(x['price']))
    
    return {
        'product': product_name,
        'results': found + [r for r in results if not r['found']],
        'total_found': len(found),
        'cheapest': found[0] if found else None
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
