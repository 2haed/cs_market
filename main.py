import requests
import time
from urllib.parse import quote
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
import psycopg2
from typing import Optional, List, Dict
from config import API_KEY, DB_CONFIG, BASE_URL, API_URL, BASE_EXPORT_URL, BEST_EXPORT_URL, EXPORT_FILE, CS_KNIVES_LC, CS_GLOVES_LC, LIS_SKINS_URL
import psycopg2
from psycopg2.extras import execute_values

import asyncpg


async def save_user_watch_items(user_id: int, query_text: str, conn: dict) -> tuple[list[str], bool]:
    """
    Возвращает кортеж:
    - список добавленных предметов
    - bool: было ли что-то найдено в snapshot
    """
    if not query_text:
        return [], False

    conn = await asyncpg.connect(**conn)
    try:
        patterns = [f"%{name.strip()}%" for name in query_text.split(",") if name.strip()]
        if not patterns:
            return [], False

        conditions = " OR ".join([f"full_name ILIKE ${i+1}" for i in range(len(patterns))])
        query = f"SELECT DISTINCT full_name FROM snapshot WHERE {conditions}"

        rows = await conn.fetch(query, *patterns)
        found_items = [row['full_name'] for row in rows]

        if not found_items:
            return [], False  # Ничего не найдено в snapshot

        saved_items = []
        for full_name in found_items:
            exists = await conn.fetchval(
                "SELECT 1 FROM watched_items WHERE user_id = $1 AND full_name = $2",
                user_id, full_name
            )
            if not exists:
                await conn.execute(
                    "INSERT INTO watched_items (user_id, full_name) VALUES ($1, $2)",
                    user_id, full_name
                )
                saved_items.append(full_name)

        return saved_items, True
    finally:
        await conn.close()



def get_usd_to_rub_rate():
    url = "https://www.cbr-xml-daily.ru/daily_json.js"
    try:
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        data = r.json()
        usd_rate = data["Valute"]["USD"]["Value"]
        return usd_rate + 1
    except Exception as e:
        print("Ошибка получения курса ЦБ:", e)
        return 95.0  # fallback

def fetch_json(url):
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None

def get_export_index():
    index_url = BASE_EXPORT_URL + EXPORT_FILE
    index_data = fetch_json(index_url)
    return index_data["format"], index_data["items"]

def get_current_prices(currency) -> dict:
    """
    Получаем актуальные цены и объемы продаж по всем предметам.
    Возвращаем словарь: {market_hash_name: {price: float, volume: int}}
    """
    resp = requests.get(f'{BEST_EXPORT_URL + currency}.json')
    data = resp.json()
    if not data.get("success", False):
        return {}

    items = data.get("items", [])
    prices = {}
    for item in items:
        name = item.get("market_hash_name")
        price = float(item.get("price", 0))
        volume = int(item.get("volume", 0))
        prices[name] = {
            "current_price": price,
            "volume": volume
        }
    return prices

def parse_item_file(file_name: str, format_keys: list) -> list:
    url = BASE_EXPORT_URL + file_name
    data = fetch_json(url)
    if not data:
        return []
    filtered = []
    for entry in data:
        item = dict(zip(format_keys, entry))
        filtered.append(item)
    return filtered

def get_item_info_bulk(hash_names: list) -> dict:
    query = "&".join([f"list_hash_name[]={quote(name)}" for name in hash_names])
    url = f"{API_URL}?key={API_KEY}&{query}"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return r.json().get("data", {})
    except Exception:
        return {}

def parse_item_info(data: dict, current_prices: dict) -> dict:
    """
    Обогащаем parse_item_info, добавляя текущую цену из current_prices по имени.
    """
    result = {}
    now = time.time()
    week_ago = now - 7 * 24 * 3600
    for name, stats in data.items():
        history = stats.get("history", [])
        average_price = stats.get("average")
        if not history:
            avg_price_7d = None
            sales_7d = 0
        else:
            recent_sales = [price for ts, price in history if ts >= week_ago]
            sales_7d = len(recent_sales)
            if sales_7d > 0:
                avg_price_7d = round(sum(recent_sales) / sales_7d, 2)
            else:
                avg_price_7d = round(average_price, 2) if average_price else None

        current_price = None
        if name in current_prices:
            current_price = current_prices[name]["current_price"]

        result[name] = {
            "last_price": avg_price_7d,
            "sales_7d": sales_7d,
            "url": f"https://market.csgo.com/ru/item/{quote(name)}",
            "current_price": current_price
        }
    return result

def convert_price_to_usd(all_stats: dict, rub_usd_rate: float) -> dict:
    converted = {}
    for name, stat in all_stats.items():
        last_price = stat.get("last_price")
        usd_price = round(last_price / rub_usd_rate, 2) if last_price else None
        converted[name] = {
            "url": stat.get("url"),
            "last_price_usd": usd_price,
            "current_price": stat.get("current_price"),
            "sales_7d": stat.get("sales_7d", 0)
        }
    return converted

def fetch_lis_skins_items(filter_list_lc):
    resp = requests.get(LIS_SKINS_URL)
    all_items = resp.json()
    items = [
        {
            "name": item["name"],
            "price": item["price"],
            "count": item["count"],
            "url": item["url"].strip()
        }
        for item in all_items
        if any(knife in item["name"].lower() for knife in filter_list_lc)
        and "StatTrak" not in item["name"]
    ]
    return items

def get_top_10_rating_items(
    conn_params: dict,
    price_min: Optional[float] = None,
    price_max: Optional[float] = None,
    item_type: Optional[str] = None,
    subtype_list: Optional[List[str]] = None
) -> List[Dict]:
    """
    Получает топ-10 ножей или перчаток по рейтингу из snapshot-таблицы.
    
    :param conn_params: параметры подключения к БД (host, dbname, user, password, port)
    :param price_min: минимальная цена (по lisskins_price_without_tax)
    :param price_max: максимальная цена
    :param item_type: тип предмета ('knife' или 'glove')
    :return: список словарей с топ-10 предметами по рейтингу
    """
    query = """
    SELECT
        full_name as "Предмет",
        market_price_without_tax as "Маркет Цена",
        market_current_price_without_tax as "Текущая Маркет Цена",
        lisskins_price_without_tax as "Лисскинс Цена",
        net_profit as "Чистая прибыль",
        current_profit as "Текущая прибыль",
        round(ROS * 100, 2) as ros,
        sales_7d as "Продаж за последние 7 дней",
        market_url, lisskins_url
    FROM snapshot
    WHERE 1 = 1
        and rating > 0
    """

    filters = []
    values = []

    if price_min is not None:
        filters.append("lisskins_price_without_tax >= %s")
        values.append(price_min)
    if price_max is not None:
        filters.append("lisskins_price_without_tax <= %s")
        values.append(price_max)
    if item_type is not None and item_type.lower() != 'both':
        filters.append("LOWER(full_name) LIKE %s")
        values.append(f"%{item_type.lower()}%")
    if subtype_list:
        # Оборачиваем OR-группу в скобки
        subtype_filter = "(" + " OR ".join(["item_name ILIKE %s" for _ in subtype_list]) + ")"
        filters.append(subtype_filter)
        values.extend([f"%{sub}%" for sub in subtype_list])

    if filters:
        query += " AND " + " AND ".join(filters)

    query += " ORDER BY rating DESC LIMIT 10"

    with psycopg2.connect(**conn_params) as conn:
        with conn.cursor() as cur:
            cur.execute(query, values)
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
        
def get_my_inventory() -> List[Dict]:
    """
    Получаем только те предметы из инвентаря, которые еще не выставлены на продажу.
    """
    url = f"{BASE_URL}/my-inventory?key={API_KEY}"
    try:
        resp = requests.get(url)
        data = resp.json()
        inventory = data.get("items", [])
        current_prices = get_current_prices(currency='RUB')
        for item in inventory:
            name = item.get("market_hash_name")
            price_info = current_prices.get(name)
            if price_info:
                item["current_price"] = price_info["current_price"]
                item["volume"] = price_info["volume"]
            else:
                item["current_price"] = None
                item["volume"] = None
        return inventory
    except Exception:
        return []

def add_to_sale(item_id: str, price: float) -> bool:
    """
    Выставляет предмет на продажу. Цена в копейках (1 RUB = 100).
    """
    price_int = int(price * 100)-1  # RUB → копейки
    url = f"{BASE_URL}/add-to-sale"
    params = {
        "key": API_KEY,
        "id": item_id,
        "price": price_int,
        "cur": 'RUB'
    }
    try:
        resp = requests.get(url, params=params)
        return resp.json().get("success", False)
    except Exception:
        return False
    
def set_price(item_id: str, price: float, currency: str = 'RUB') -> bool:
    current_prices = get_current_prices('RUB')
    int_price = int(price * 100)-1  # в копейках
    params = {
        'key': API_KEY,
        'item_id': item_id,
        'price': int_price,
        'cur': currency
    }
    response = requests.get(BASE_URL + 'set-price', params=params)
    data = response.json()
    if data.get("success"):
        print(f"[OK] Установлена цена {price} RUB для item_id={item_id}")
        return True
    else:
        print(f"[ERR] Ошибка для item_id={item_id}: {data.get('error')}")
        return False

def get_items():
    params = {
        'key': API_KEY,
    }
    response = requests.get(BASE_URL + '/items', params=params)
    data = response.json()
    if not data.get("success"):
        raise Exception("Failed to get items")
    return data["items"]
    
def set_price(item_id, new_price):
    url = f"https://market.csgo.com/api/v2/set-price?key={API_KEY}&item_id={item_id}&price={int(new_price * 100)}&cur=RUB"
    response = requests.get(url)
    data = response.json()
    return data

def adjust_prices():
    items = get_items()
    current_prices = get_current_prices(currency='RUB')
    result_messages = []
    if not items:
        return None
    else: 

        for item in items:
            if item.get("status") != "1":
                continue

            name = item["market_hash_name"]
            item_id = item["item_id"]
            market_price_data = current_prices.get(name)

            if not market_price_data:
                continue

            market_price = market_price_data.get('current_price')
            if not market_price:
                continue

            my_price = float(item["price"])
            target_price = round(market_price - 0.01, 2)

            if my_price > target_price:
                result = set_price(item_id, target_price)
                msg = f"<code>{name}</code>: {my_price} ₽ → {target_price} ₽ ({result})"
            else:
                msg = f"<code>{name}</code>: {my_price} ₽ (оптимально)"
            result_messages.append(msg)
            time.sleep(1.5)

    return result_messages

def parse_items(item_type: str = "both"):
    # Выбираем список для фильтрации по типу предметов
    if item_type == "knife":
        filter_list_lc = CS_KNIVES_LC
        valid_types = ["Knife"]
    elif item_type == "glove":
        filter_list_lc = CS_GLOVES_LC
        valid_types = ["Gloves"]
    elif item_type == "both":
        filter_list_lc = CS_KNIVES_LC + CS_GLOVES_LC
        valid_types = ["Knife", "Gloves"]
    else:
        return ValueError("Неверный item_type. Ожидается 'knife', 'glove' или 'both'.")

    format_keys, item_files = get_export_index()
    all_items = []

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(parse_item_file, file_name, format_keys) for file_name in item_files]
        for future in futures:
            result = future.result()
            if result:
                all_items.extend(result)
            time.sleep(0.005)

    df = pd.DataFrame(all_items)
    df = df[df['type'].isin(valid_types)]
    hash_names = df["market_hash_name"].unique().tolist()
    hash_names = [name for name in hash_names if "StatTrak" not in name]

    all_stats = {}
    batch_size = 30
    current_prices = get_current_prices('USD')
    for i in range(0, len(hash_names), batch_size):
        batch = hash_names[i:i+batch_size]
        raw_data = get_item_info_bulk(batch)
        
        batch_stats = parse_item_info(raw_data, current_prices)
        all_stats.update(batch_stats)
        print(f"Processed {i + len(batch)} out of {len(hash_names)}")
        time.sleep(0.7)

    rub_usd_rate = get_usd_to_rub_rate()
    converted_stats = convert_price_to_usd(all_stats, rub_usd_rate)

    lis_skins_items = fetch_lis_skins_items(filter_list_lc)

    # Запись в БД
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()

    for item in lis_skins_items:
        cursor.execute("""
            INSERT INTO lisskins_items (name, price, count, url)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (name) DO UPDATE
            SET price = EXCLUDED.price,
                count = EXCLUDED.count,
                url = EXCLUDED.url;
        """, (item['name'], item['price'], item['count'], item['url']))

    insert_query = """
        INSERT INTO market_items (item_name, last_price, current_price, sales_7d, url)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (item_name) DO UPDATE
        SET last_price = EXCLUDED.last_price,
            current_price = EXCLUDED.current_price,
            sales_7d = EXCLUDED.sales_7d,
            url = EXCLUDED.url;
    """
    records = [
        (name, info['last_price_usd'], info['current_price'], info['sales_7d'], info['url'])
        for name, info in converted_stats.items()
    ]
    cursor.executemany(insert_query, records)

    conn.commit()
    cursor.close()
    conn.close()
    return f"Данные успешно добавлены в базу для типа: {item_type}"

