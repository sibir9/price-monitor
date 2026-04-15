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
    """Извлекает цену из текста"""
    if not text or not isinstance(text, str):
        return None, None
    match = re.search(r'(\d[\d\s\.]*[\d\.]+)\s*[Рр]уб|₽', text)
    if match:
        price_str = re.sub(r'[\s,]', '', match.group(1))
        try:
            return price_str, float(price_str)
        except:
            pass
    return None, None

def is_valid_text(text):
    """Проверяет, что текст не из скрипта и не из стилей"""
    if not text or not isinstance(text, str):
        return False
    if len(text) > 300:
        return False
    text_lower = text.lower()
    if any(x in text_lower for x in ['function(', 'var ', 'window.', 'addEventListener', 'document.', '.style', 'bx_', 'ob', 'javascript:', 'json', 'dataLayer']):
        return False
    return True

async def parse_price(session, url: str, target_product: str):
    headers = {'User-Agent': ua.random}
    try:
        async with session.get(url, timeout=15, headers=headers) as response:
            if response.status != 200:
                return {'url': url, 'price': f'HTTP {response.status}', 'found': False}
            
            html = await response.text()
            soup = BeautifulSoup(html, 'html.parser')
            
            # Удаляем скрипты и стили
            for script in soup(["script", "style", "noscript", "iframe", "svg"]):
                script.decompose()
            
            logger.info(f"=== {urlparse(url).netloc} | Ищем: '{target_product}' ===")
            
            # ----- СПЕЦИАЛЬНАЯ ЛОГИКА ДЛЯ snabservis -----
            if 'snabservis' in url:
                price_elem = soup.select_one('span.price')
                if price_elem:
                    price_text = price_elem.get_text(strip=True)
                    price_str, price_val = extract_price(price_text)
                    if price_str:
                        return {'url': url, 'price': price_str, 'found': True, 'product_found': target_product}
            
            # ----- СПЕЦИАЛЬНАЯ ЛОГИКА ДЛЯ iskra-rus (каталог) -----
            if 'iskra-rus' in url and '/catalog/' in url:
                # Ищем все блоки с товарами
                product_blocks = soup.find_all('div', class_=re.compile(r'product-item', re.I))
                for block in product_blocks:
                    # Название товара
                    name_elem = block.find('a', class_=re.compile(r'name|title', re.I))
                    if not name_elem:
                        name_elem = block.find('span', class_=re.compile(r'name|title', re.I))
                    if name_elem:
                        product_name = name_elem.get_text(strip=True)
                        if target_product.lower() in product_name.lower():
                            # Цена в этом же блоке
                            price_elem = block.find('p', class_=re.compile(r'price', re.I))
                            if not price_elem:
                                price_elem = block.find('span', class_=re.compile(r'price', re.I))
                            if price_elem:
                                price_text = price_elem.get_text(strip=True)
                                price_str, price_val = extract_price(price_text)
                                if price_str:
                                    logger.info(f"✅ iskra: {product_name[:40]} → {price_str} руб.")
                                    return {'url': url, 'price': price_str, 'found': True, 'product_found': product_name[:50]}
                
                # Если не нашли в блоках, ищем по тексту на странице
                for elem in soup.find_all(string=True):
                    if not is_valid_text(elem):
                        continue
                    if target_product.lower() in elem.lower():
                        parent = elem.find_parent()
                        if parent:
                            product_block = parent.find_parent('div', class_=re.compile(r'product|item|card', re.I))
                            if product_block:
                                price_elem = product_block.find(class_=re.compile(r'price', re.I))
                                if price_elem:
                                    price_text = price_elem.get_text(strip=True)
                                    price_str, price_val = extract_price(price_text)
                                    if price_str:
                                        return {'url': url, 'price': price_str, 'found': True, 'product_found': elem.strip()[:50]}
            
            # ----- ОСНОВНАЯ ЛОГИКА -----
            all_prices = []
            for elem in soup.find_all(string=True):
                if not is_valid_text(elem):
                    continue
                if target_product.lower() in elem.lower():
                    product_name = elem.strip()[:60]
                    parent = elem.find_parent()
                    if parent:
                        product_block = parent.find_parent('div', class_=re.compile(r'product|item|card|catalog', re.I))
                        if not product_block:
                            product_block = parent.find_parent('tr')
                        if not product_block:
                            product_block = parent
                        
                        price_selectors = [
                            '.price', '.product-price', '.current-price', '.price_value',
                            '.card__item-price', '.product-item-price-current', '.catalog-item__price',
                            'span.price', 'div.price', '[itemprop="price"]'
                        ]
                        
                        price_found = None
                        for selector in price_selectors:
                            try:
                                price_elem = product_block.select_one(selector)
                                if price_elem:
                                    price_text = price_elem.get_text(strip=True)
                                    price_str, price_val = extract_price(price_text)
                                    if price_str:
                                        price_found = (price_str, price_val)
                                        break
                            except:
                                continue
                        
                        if not price_found:
                            for text in product_block.find_all(string=True):
                                if text and ('руб' in text.lower() or '₽' in text):
                                    if is_valid_text(text):
                                        price_str, price_val = extract_price(text)
                                        if price_str:
                                            price_found = (price_str, price_val)
                                            break
                        
                        if price_found:
                            all_prices.append({'price': price_found[0], 'value': price_found[1], 'product': product_name})
            
            if all_prices:
                best = min(all_prices, key=lambda x: x['value'])
                return {'url': url, 'price': best['price'], 'found': True, 'product_found': best['product']}
            
            # ----- КАРТОЧКА ТОВАРА -----
            title = soup.find('title').get_text() if soup.find('title') else ""
            if target_product.lower() in title.lower():
                for selector in ['.price', '.product-price', '.current-price', '.price_value', '.card__item-price']:
                    price_elem = soup.select_one(selector)
                    if price_elem:
                        price_text = price_elem.get_text(strip=True)
                        price_str, price_val = extract_price(price_text)
                        if price_str:
                            return {'url': url, 'price': price_str, 'found': True, 'product_found': target_product}
            
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
            input, textarea { width: 100%; padding: 10px; margin: 10px 0; border: 1px solid #ddd; border-radius: 5px; }
            button { background: #007bff; color: white; padding: 12px 24px; border: none; border-radius: 5px; cursor: pointer; font-size: 16px; }
            .loading { display: none; text-align: center; padding: 20px; }
            .spinner { border: 4px solid #f3f3f3; border-top: 4px solid #007bff; border-radius: 50%; width: 40px; height: 40px; animation: spin 1s linear infinite; margin: 0 auto; }
            @keyframes spin { to { transform: rotate(360deg); } }
            .results { margin-top: 30px; display: none; }
            .cheapest { background: #28a745; color: white; padding: 15px; border-radius: 5px; margin-bottom: 20px; text-align: center; }
            table { width: 100%; border-collapse: collapse; }
            th, td { padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }
            th { background: #f5f5f5; }
            .price-found { color: #28a745; font-weight: bold; }
            .price-error { color: #dc3545; }
        </style>
    </head>
    <body>
        <h1>🔍 Мониторинг цен конкурентов</h1>
        <form id="scrapeForm">
            <label>📦 Название товара</label>
            <input type="text" id="productName" placeholder="Пример: 606 2RS" required>
            <label>🔗 Ссылки на товары конкурентов</label>
            <textarea id="urls" rows="6" placeholder="https://btk-russia.ru/catalog/radialnye_i_radialno_upornye/6004-2rs-zkl/&#10;https://sf2v.ru/catalog/podshipnik/&#10;https://iskra-rus.ru/catalog/sharikovye_podshipniki/&#10;https://spb.snabservis.ru/product/podshipnik-sharikovyj-radialnyj-606-2rs-180016-6h17h6-mm-gost-8338-75/" required></textarea>
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
                    html += '<h3>📊 Результаты сравнения</h3><table><thead><tr><th>Магазин</th><th>Цена</th><th>Найденный товар</th></tr></thead><tbody>';
                    data.results.forEach(r => {
                        let hostname = new URL(r.url).hostname;
                        let priceClass = r.found ? 'price-found' : 'price-error';
                        let priceText = r.found ? `${r.price} ₽` : r.price;
                        html += `<tr><td>${hostname}</td><td class="${priceClass}">${priceText}</td><td style="font-size:11px">${r.product_found || '-'}</td></tr>`;
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
