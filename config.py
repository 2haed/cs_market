# config.py
API_KEY = "S3WQv4N1Sm3StXwdQV7465V2mJ0za2P"
DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "database": "testdb",
    "user": "admin",
    "password": "adminpass"
}

API_URL = "https://market.csgo.com/api/v2/get-list-items-info"
BASE_URL = 'https://market.csgo.com/api/v2'
BASE_EXPORT_URL = "https://market.csgo.com/api/full-export/"
BEST_EXPORT_URL = "https://market.csgo.com/api/v2/prices/"
EXPORT_FILE = "USD.json"

CS_ITEMS = [
    "Butterfly Knife", "Falchion Knife", "Flip Knife", "Gut Knife", "Huntsman Knife",
    "Karambit", "M9 Bayonet", "Shadow Daggers", "Bowie Knife", "Stiletto Knife",
    "Talon Knife", "Ursus Knife", "Skeleton Knife", "Paracord Knife", "Survival Knife", "Bloodhound Gloves", 
    "Broken Fang Gloves", "Driver Gloves", "Hand Wraps", "Hydra Gloves", "Moto Gloves", 
    "Specialist Gloves",  "Sport Gloves"
]

CS_KNIVES_LC = [item.lower() for item in CS_ITEMS if ("gloves" not in item.lower()  and 'hand wraps' not in  item.lower())]
CS_GLOVES_LC = [item.lower() for item in CS_ITEMS if ("gloves" in item.lower() or 'hand wraps' in item.lower())]
LIS_SKINS_URL = "https://lis-skins.com/market_export_json/csgo.json"
TOKEN = "7973889481:AAHsVViO6NaNqwJ6YEw3d1Pcg6hDztOFvzg"