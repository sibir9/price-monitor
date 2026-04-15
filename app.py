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

def extract_price(text):
    """Извлекает цену из текста (поддерживает целые и дробные числа)"""
    if not text or not isinstance(text, str):
        return None, None
    # Ищем число с рублями
    match = re.search(r'(\d[\d\s\.]*[\d\.]+)\s*[Рр]уб', text)
    if not match:
        match = re.search(r'(\d[\d\s\.]*[\d\.]+)', text)
    if match:
        price_str = re.sub(r'[\s,]', '', match.group(1))
        try:
            return price_str, float(price_str)
        except:
            pass
    return None, None

async def parse_price(session, url: str, target_product: str):
    headers = {'User-Agent': ua.random}
    try:
        async with session.get(url, timeout=15, headers=headers) as response:
            if response.status != 200:
                return {'url': url, 'price': f'HTTP {response.status}', 'found': False}
            
            html = await response.text()
            soup = BeautifulSoup(html, 'html.parser')
            
            logger.info(f"=== {urlparse(url).netloc} | Ищем: '{target_product}' ===")
            
            # ----- СПЕЦИАЛЬНАЯ ЛОГИКА ДЛЯ iskra-rus -----
            if 'iskra-rus' in url:
                price_elem = soup.select_one('#bx_117848907_13271_price')
                if not price_elem:
                    price_elem = soup.select_one('.product-item-detail-price-current.current')
                if not price_elem:
                    price_elem = soup.select_one('.current')
                if price_elem:
                    price_text = price_elem.get_text(strip=True)
                    price_str, price_val = extract_price(price_text)
                    if price_str:
                        logger.info(f"✅ iskra-rus: цена {price_str} руб.")
                        return {
                            'url': url,
                            'price': price_str,
                            'found': True,
                            'product_found': target_product
                        }
            
            # ----- УНИВЕРСАЛЬНЫЙ ПОИСК ПО ПРИОРИТЕТНЫМ СЕЛЕКТОРАМ -----
            price_selectors = [
                '.card__item-price',
                '.product-price',
                '.current-price',
                '.product-item-price-current',
                '.catalog-item__price',
                '.price',
                '.price_value',
                '.price_current',
                '.product__price',
                '.special-price',
                '.sale-price',
                '[itemprop="price"]',
                '.cost-price',
                '.final-price'
            ]
            
            for selector in price_selectors:
                price_elem = soup.select_one(selector)
                if price_elem:
                    price_text = price_elem.get_text(strip=True)
                    price_str, price_val = extract_price(price_text)
                    if price_str:
                        logger.info(f"✅ Цена по {selector}: {price_str} руб.")
                        return {
                            'url': url,
                            'price': price_str,
                            'found': True,
                            'product_found': target_product
                        }
            
            # ----- ПОИСК ЛЮБОЙ ЦЕНЫ НА СТРАНИЦЕ (с фильтром) -----
            price_candidates = []
            for elem in soup.find_all(string=re.compile(r'\d+[\s\d\.]*\d*\s*[Рр]уб')):
                match = re.search(r'(\d[\d\s\.]*[\d\.]+)\s*[Рр]уб', elem)
                if match:
                    price_str_raw = match.group(1)
                    price_str = re.sub(r'[\s,]', '', price_str_raw)
                    try:
                        price_val = float(price_str)
                        parent = elem.find_parent()
                        parent_text = parent.get_text().lower() if parent else ""
                        if 'экономия' not in parent_text and 'скидка' not in parent_text:
                            price_candidates.append((price_val, price_str))
                    except:
                        pass
            
            if price_candidates:
                price_candidates.sort(key=lambda x: x[0])
                best_price = price_candidates[0][1]
                logger.info(f"✅ Найдена минимальная цена: {best_price} руб.")
                return {
                    'url': url,
                    'price': best_price,
                    'found': True,
                    'product_found': target_product
                }
            
            logger.info(f"❌ Товар '{target_product}' не найден")
            return {'url': url, 'price': 'Товар не найден', 'found': False}
            
    except Exception as e:
        logger.error(f"Ошибка: {e}")
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
            body { font-family: Arial; max-width: 1000px; margin: 50px auto; padding: 20px; }
            input, textarea { width: 100%; padding: 10px; margin: 10px 0; border: 1px solid #ddd; border-radius: 5px; font-family: inherit; }
            button { background: #007bff; color: white; padding: 12px 24px; border: none; border-radius: 5px; cursor: pointer; font-size: 16px; }
            button:hover { background: #0056b3; }
            .loading { display: none; text-align: center; padding: 20px; }
            .spinner { border: 4px solid #f3f3f3; border-top: 4px solid #007bff; border-radius: 50%; width: 40px; height: 40px; animation: spin 1s linear infinite; margin: 0 auto; }
            @keyframes spin { to { transform: rotate(360deg); } }
            .results { margin-top: 30px; display: none; }
            .cheapest { background: #28a745; color: white; padding: 15px; border-radius: 5px; margin-bottom: 20px; text-align: center; font-size: 18px; }
            table { width: 100%; border-collapse: collapse; }
            th, td { padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }
            th { background: #f5f5f5; font-weight: bold; }
            tr:hover { background: #f9f9f9; }
            .price-found { color: #28a745; font-weight: bold; }
            .price-error { color: #dc3545; }
            .hostname { font-family: monospace; font-size: 12px; color: #666; }
        </style>
    </head>
    <body>
        <h1>🔍 Мониторинг цен конкурентов</h1>
        <form id="scrapeForm">
            <label>📦 Название товара (обязательно)</label>
            <input type="text" id="productName" placeholder="Пример: 6004-2RS" required>
            <label>🔗 Ссылки на товары конкурентов (по одной на строку)</label>
            <textarea id="urls" rows="6" placeholder="https://btk-russia.ru/catalog/radialnye_i_radialno_upornye/6004-2rs-zkl/&#10;https://sf2v.ru/catalog/podshipnik/&#10;https://iskra-rus.ru/product/podshipnik_61806_2rs_iskra/&#10;https://spb.snabservis.ru/product/podshipnik-sharikovyj-radialnyj-606-2rs-180016-6h17h6-mm-gost-8338-75/" required></textarea>
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
                    html += '<h3>📊 Результаты сравнения</h3>';
                    html += '<table><thead><tr><th>Магазин</th><th>Цена</th></tr></thead><tbody>';
                    data.results.forEach(r => {
                        let hostname = new URL(r.url).hostname;
                        let priceClass = r.found ? 'price-found' : 'price-error';
                        let priceText = r.found ? `${r.price} ₽` : r.price;
                        html += `<tr>
                            <td class="hostname">${hostname}</td>
                            <td class="${priceClass}">${priceText}</td>
                        </tr>`;
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
    found.sort(key=lambda x: float(x['price']))
    
    return {
        'product': product_name,
        'results': found + [r for r in results if not r['found']],
        'total_found': len(found),
        'cheapest': found[0] if found else None
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
